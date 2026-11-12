"""Adapter für das Dashboard-Frontend: POST /api/interviews/reply.

Zustandslos – der gesamte Gesprächsverlauf kommt mit jedem Request (camelCase-Vertrag des Frontends).
Pro Turn: 1 LLM-Aufruf (nächste Frage + Kernaussage) und 1 RAG-Prüfung (/check) der Kernaussage.
Kein Auth-Dependency: das Dashboard sendet keine Auth-Header. Antwortet nie mit 500.
"""
from __future__ import annotations

import logging
import time
from datetime import date

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..config import settings
from ..llm.provider import get_llm

log = logging.getLogger("ekep.dashboard")

router = APIRouter(prefix="/api", tags=["dashboard"])

RAG_CHECK_TIMEOUT_S = 30.0
FALLBACK_REPLY = ("Können Sie das bitte noch etwas genauer beschreiben – gibt es Ausnahmen, "
                  "und seit wann gilt diese Regelung?")


# --------------------------------------------------------------------------
# Request / Response (Vertrag des Frontends, extra Felder werden ignoriert)
# --------------------------------------------------------------------------
class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ExpertIn(_Base):
    name: str | None = None
    role: str | None = None
    department: str | None = None


class MessageIn(_Base):
    role: str = "expert"  # 'ai' | 'expert'
    text: str = ""
    timestamp: str | int | float | None = None


class MissingInfoIn(_Base):
    id: str
    label: str = ""
    resolved: bool = False


class TopicIn(_Base):
    id: str
    label: str = ""
    done: bool = False


class ReplyRequest(_Base):
    interviewId: str | None = None
    employeeId: str | None = None
    projectName: str | None = None
    step: int | str | None = None
    scriptId: str | None = None
    expert: ExpertIn | None = None
    linkedDocumentName: str | None = None
    messages: list[MessageIn] = Field(default_factory=list)
    missingInfo: list[MissingInfoIn] = Field(default_factory=list)
    topics: list[TopicIn] = Field(default_factory=list)
    expertText: str


class Plausibility(BaseModel):
    score: int
    reasons: list[str]


class Extracted(BaseModel):
    statement: str
    topic: str
    confidence: float
    plausibility: Plausibility | None = None


class ContradictionOut(BaseModel):
    text: str
    severity: str  # 'hoch' | 'mittel'


class ReplyResponse(BaseModel):
    reply: str
    extracted: Extracted | None = None
    resolveMissing: list[str] = Field(default_factory=list)
    topicDone: str | None = None
    contradiction: ContradictionOut | None = None
    resolveContradictions: bool | None = None
    followUp: str | None = None
    finished: bool | None = None


# --------------------------------------------------------------------------
# LLM
# --------------------------------------------------------------------------
class TurnDecision(BaseModel):
    reply: str
    statement: str
    topic: str
    topicId: str | None
    topicDone: bool
    resolveMissing: list[str]
    finished: bool
    confidence: float


LLM_SYSTEM = """Du führst ein Experteninterview in einer Bausparkasse (LBS), um implizites Wissen zu sichern.
Du antwortest ausschließlich mit JSON (Deutsch).
Regeln:
- reply: genau EINE nächste Nachfrage, konkret und bohrend (Lücken, Widersprüche, Ausnahmen, Stichtage/Daten),
  max. 2 Sätze, keine Wiederholung bereits gestellter Fragen, keine Fachbehauptungen unterstellen.
- statement: die zentrale Sachaussage aus der neuen Expertenantwort als EIN deutscher Satz (nur Gesagtes, nichts erfinden).
- topic/topicId: bestes passendes Thema aus der Liste (topicId = dessen id, sonst null); topic = dessen Label.
- topicDone: true nur, wenn dieses Thema durch die Antwort ausreichend behandelt ist.
- resolveMissing: ids der offenen Punkte, die die Antwort jetzt tatsächlich beantwortet (nur ids aus der Liste).
- finished: true nur, wenn alle offenen Punkte beantwortet sind oder der Experte klar sagt, dass er fertig ist.
- confidence 0..1: wie klar, konkret und vorbehaltlos der Experte es gesagt hat (nicht, ob es fachlich stimmt)."""


def _llm_user(req: ReplyRequest) -> str:
    ex = req.expert or ExpertIn()
    lines = [
        f"EXPERTE: {ex.name or '-'} | Rolle: {ex.role or '-'} | Abteilung: {ex.department or '-'}",
        f"PROJEKT: {req.projectName or '-'}" + (f" | Dokument: {req.linkedDocumentName}" if req.linkedDocumentName else ""),
        "THEMEN:",
        *([f"- id={t.id} | {t.label} | {'erledigt' if t.done else 'offen'}" for t in req.topics] or ["- (keine)"]),
        "OFFENE PUNKTE (missingInfo, noch unbeantwortet):",
        *([f"- id={m.id} | {m.label}" for m in req.missingInfo if not m.resolved] or ["- (keine)"]),
        "BISHERIGES GESPRÄCH:",
        *([f"{'KI' if m.role == 'ai' else 'EXPERTE'}: {m.text}" for m in req.messages[-16:] if m.text]
          or ["- (Beginn)"]),
        f"NEUE EXPERTENANTWORT: {req.expertText}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# RAG /check
# --------------------------------------------------------------------------
_VERDICT_SCORE = {"correct": 90, "plausible": 72, "unsupported": 62, "possibly_outdated": 48, "contradicted": 30}


async def _rag_check(statement: str) -> dict | None:
    if not settings.rag_service_url or not statement.strip():
        return None
    body = {"statement": statement, "as_of": date.today().isoformat(), "k": 6, "lang": settings.rag_service_lang or "de"}
    try:
        async with httpx.AsyncClient(timeout=RAG_CHECK_TIMEOUT_S) as client:
            r = await client.post(settings.rag_service_url.rstrip("/") + "/check", json=body)
            r.raise_for_status()
            data = r.json()
        return data if isinstance(data, dict) and data.get("verdict") else None
    except Exception as e:
        log.warning("RAG /check fehlgeschlagen: %s", str(e)[:300])
        return None


def _plausibility(check: dict) -> tuple[Plausibility | None, ContradictionOut | None]:
    verdict = str(check.get("verdict") or "")
    if verdict not in _VERDICT_SCORE:
        return None, None
    explanation = " ".join(str(check.get("explanation") or "").split())
    today = " ".join(str(check.get("what_applies_today") or "").split())
    reasons = []
    if explanation:
        reasons.append(explanation[:200])
    # Der RAG-Service liefert evidence_ids (Vertrag) oder evidence: [{id, citation, ...}] (aktuelle Implementierung)
    ev_ids = list(check.get("evidence_ids") or []) or [
        e.get("citation") or e.get("id") for e in (check.get("evidence") or []) if isinstance(e, dict)]
    for ev in [e for e in ev_ids if e][:2]:
        reasons.append(f"Quelle: {ev}")
    if verdict in ("possibly_outdated", "contradicted") and today:
        reasons.append(f"Heute gilt: {today}")
    contradiction = None
    if verdict == "contradicted":
        contradiction = ContradictionOut(text=explanation or "Aussage widerspricht der Wissensbasis.", severity="hoch")
    elif verdict == "possibly_outdated":
        contradiction = ContradictionOut(text="Aussage entspricht einer älteren Regelung: " + (today or explanation),
                                         severity="mittel")
    return Plausibility(score=_VERDICT_SCORE[verdict], reasons=reasons), contradiction


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------
@router.post("/interviews/reply", response_model=ReplyResponse, response_model_exclude_none=True)
async def interview_reply(req: ReplyRequest) -> ReplyResponse:
    t0 = time.perf_counter()
    try:
        decision = await get_llm().structured(system=LLM_SYSTEM, user=_llm_user(req), schema=TurnDecision,
                                              model=settings.interviewer_model, temperature=0.4)
    except Exception as e:
        log.warning("dashboard reply: LLM fehlgeschlagen: %s", str(e)[:300])
        return ReplyResponse(reply=FALLBACK_REPLY, resolveMissing=[])

    valid_missing = {m.id for m in req.missingInfo}
    valid_topics = {t.id for t in req.topics}
    resolve = [i for i in decision.resolveMissing if i in valid_missing]
    topic_done = decision.topicId if (decision.topicDone and decision.topicId in valid_topics) else None

    check = await _rag_check(decision.statement)
    plaus, contradiction = _plausibility(check) if check else (None, None)
    extracted = Extracted(statement=decision.statement.strip(), topic=decision.topic.strip(),
                          confidence=min(1.0, max(0.0, decision.confidence)), plausibility=plaus)
    log.info("dashboard reply %.2fs verdict=%s", time.perf_counter() - t0, (check or {}).get("verdict"))
    return ReplyResponse(reply=decision.reply.strip() or FALLBACK_REPLY, extracted=extracted, resolveMissing=resolve,
                         topicDone=topic_done, contradiction=contradiction, finished=decision.finished or None)

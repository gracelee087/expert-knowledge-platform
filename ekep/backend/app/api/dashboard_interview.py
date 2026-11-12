"""Adapter für das Dashboard-Frontend: POST /api/interviews/reply.

Der Dashboard-Vertrag ist zustandslos (gesamter Verlauf kommt mit jedem Request, camelCase). Intern läuft aber
dieselbe Interview-Engine wie in der eigenen App (services/orchestrator.py):
  Planner-Themen → Interviewer-Agent (mit RAG-Fragevorschlag) → Analyst-Extraktion (Background) → RAG-Fakt.

Dafür wird pro Dashboard-Interview ein ekep-Interview angelegt (Titel "[dash:<interviewId>] …"), der Verlauf
als Turns gespiegelt und jede Expertenantwort über `orchestrator.answer()` verarbeitet. Die Kernaussage kommt
aus der vom Analysten extrahierten Wissenseinheit und wird wie bisher über RAG /check auf Plausibilität geprüft.

DASHBOARD_ENGINE=simple schaltet auf den alten Ein-LLM-Pfad zurück.
Kein Auth-Dependency: das Dashboard sendet keine Auth-Header. Antwortet nie mit 500.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..llm.provider import get_llm
from ..models import Employee, Interview, Turn
from ..services import coverage as cov
from ..services import orchestrator as orch
from ..services.guards import redact_pii

log = logging.getLogger("ekep.dashboard")

router = APIRouter(prefix="/api", tags=["dashboard"])

RAG_CHECK_TIMEOUT_S = 30.0
ANALYSIS_WAIT_S = 25.0
FALLBACK_REPLY = ("Können Sie das bitte noch etwas genauer beschreiben – gibt es Ausnahmen, "
                  "und seit wann gilt diese Regelung?")
_CONFIDENCE = {"hoch": 0.9, "mittel": 0.7, "niedrig": 0.5}
_SCRIPT_GOAL = {
    "historisch": "Historische Bausparverträge und Tarifgenerationen dokumentieren",
    "generisch": "Allgemeines Erfahrungswissen sichern",
}
_SCRIPT_LABEL = {"historisch": "Historische Bausparverträge", "projekt": "Projektwissen", "generisch": "Erfahrungswissen"}

# Dashboard-interviewId → ekep-Interview-ID (Prozessspeicher; Fallback: Titelpräfix in der DB)
_ekep_ids: dict[str, str] = {}


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
    topicId: str | None = None      # aktuelles Thema der Engine (Dashboard-ID)
    topicDone: str | None = None
    contradiction: ContradictionOut | None = None
    resolveContradictions: bool | None = None
    followUp: str | None = None
    finished: bool | None = None


# --------------------------------------------------------------------------
# LLM (alter Ein-Aufruf-Pfad, DASHBOARD_ENGINE=simple)
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
# Dashboard-Interview ↔ ekep-Interview
# --------------------------------------------------------------------------
def _dash_key(req: ReplyRequest) -> str:
    return (req.interviewId or req.employeeId or "default").strip()


def _goal(req: ReplyRequest) -> str:
    sid = (req.scriptId or "").strip().lower()
    if sid == "projekt":
        return f"Projektwissen zu {req.projectName or 'dem aktuellen Projekt'} vor dem Austritt sichern"
    return _SCRIPT_GOAL.get(sid, _SCRIPT_GOAL["generisch"])


def _plan_from_dashboard(req: ReplyRequest) -> dict:
    """Interviewplan im Format des Planners, aber aus den Dashboard-Themen – UI und Engine teilen die IDs."""
    missing = [m.label for m in req.missingInfo if m.label and not m.resolved]
    topics_in = req.topics or [TopicIn(id="T1", label=_goal(req))]
    topics = []
    for i, t in enumerate(topics_in):
        topics.append({
            "id": t.id, "title": t.label or t.id,
            "description": "Offene Punkte laut Dashboard: " + "; ".join(missing) if missing and i == 0 else
                           f"Themenblock „{t.label or t.id}“ aus dem Dashboard-Skript",
            "rationale": "Aus dem Dashboard-Skript vorgegeben",
            "priority": "hoch" if i == 0 else "mittel",
            "focus_dimensions": [],
            "opening_question": "",
            "tacit_knowledge_probes": missing if i == 0 else [],
            "status": "saturated" if t.done else "open",
            "origin": "plan",
        })
    warmup = next((m.text for m in req.messages if m.role == "ai" and m.text), "")
    strategy = ("Die Themenblöcke des Dashboard-Skripts nacheinander vertiefen und die offenen Punkte gezielt schließen; "
                "Aussagen zu Tarifen und Fristen durch Nachfragen konkretisieren.")
    return {"strategy": strategy, "warmup_question": warmup, "topics": topics}


def _history(req: ReplyRequest) -> list[tuple[str, str]]:
    """(speaker, text) des Dashboard-Verlaufs ohne die neue Expertenantwort (die fügt answer() hinzu)."""
    msgs = [(("ai" if m.role == "ai" else "expert"), m.text.strip()) for m in req.messages if m.text and m.text.strip()]
    new_text = req.expertText.strip()
    if msgs and msgs[-1][0] == "expert" and msgs[-1][1] == new_text:
        msgs = msgs[:-1]
    if settings.pii_redaction:
        msgs = [(s, redact_pii(t)[0] if s == "expert" else t) for s, t in msgs]
    return msgs


def _append_turns(iv: Interview, msgs: list[tuple[str, str]], *, first_is_opening: bool) -> int:
    """Noch nicht gespiegelte Dashboard-Nachrichten als Turns anhängen (Dedupe über speaker+text)."""
    seen = {(t.speaker, t.text.strip()) for t in iv.turns}
    added = 0
    for speaker, text in msgs:
        if (speaker, text) in seen:
            continue
        seen.add((speaker, text))
        technique = "opening" if (speaker == "ai" and first_is_opening and added == 0 and not iv.turns) else None
        iv.turns.append(Turn(
            interview_id=iv.id, seq=orch._next_seq(iv), speaker=speaker, text=text, technique=technique,
            topic_id=iv.current_topic_id, input_mode="text" if speaker == "expert" else None, t_start=0.0, t_end=0.0,
        ))
        added += 1
    if added:
        last_ai = next((t.text for t in reversed(iv.turns) if t.speaker == "ai"), None)
        if last_ai:
            iv.current_question = last_ai
    return added


def _find_existing(db, key: str) -> Interview | None:
    iid = _ekep_ids.get(key)
    iv = db.get(Interview, iid) if iid else None
    if iv is None:
        iv = db.scalars(select(Interview).where(Interview.title.startswith(f"[dash:{key}]"))
                        .order_by(Interview.created_at.desc())).first()
    if iv is not None and iv.status in ("planned", "in_progress", "paused"):
        return iv
    return None  # abgeschlossen → neues ekep-Interview für dieselbe Dashboard-ID


def _create_interview(db, req: ReplyRequest, key: str) -> Interview:
    ex = req.expert or ExpertIn()
    name = (ex.name or "").strip() or f"Experte {key}"
    emp = db.scalars(select(Employee).where(Employee.name == name)).first()
    if emp is None:
        emp = Employee(name=name, department=ex.department or "", role=ex.role or "", expertise_area=ex.role or "")
        db.add(emp)
        db.flush()
    plan = _plan_from_dashboard(req)
    topic_ids = [t["id"] for t in plan["topics"]]
    open_ids = [t["id"] for t in plan["topics"] if t["status"] == "open"]
    now = orch._now()
    label = (req.projectName or "").strip() or _SCRIPT_LABEL.get((req.scriptId or "").lower(), "Experteninterview")
    iv = Interview(
        employee_id=emp.id, title=f"[dash:{key}] {label}"[:300], goal=_goal(req), planned_minutes=30,
        plan=plan, coverage=cov.ensure_topics({}, topic_ids),
        consent_given_at=now, consent_version=settings.consent_text_version, audio_retention=settings.audio_retention,
        status="in_progress", phase="opening", started_at=now, last_resumed_at=now, last_activity_at=now,
        current_topic_id=(open_ids or topic_ids)[0],
    )
    db.add(iv)
    db.flush()
    return iv


def _ensure_interview(req: ReplyRequest) -> tuple[str, str | None, dict]:
    """ekep-Interview für die Dashboard-ID holen/anlegen und den Verlauf spiegeln.

    Rückgabe: (ekep_id, current_topic_id, {topic_id: status}) – Zustand VOR der neuen Antwort.
    """
    key = _dash_key(req)
    with session_scope() as db:
        iv = _find_existing(db, key)
        created = iv is None
        if created:
            iv = _create_interview(db, req, key)
        elif iv.status == "paused":
            iv.status, iv.last_resumed_at = "in_progress", orch._now()
        elif iv.status == "planned":
            iv.status, iv.started_at, iv.last_resumed_at = "in_progress", orch._now(), orch._now()
        _append_turns(iv, _history(req), first_is_opening=created)
        if created:
            asked = sum(1 for t in iv.turns if t.speaker == "ai")
            iv.phase = "opening" if asked <= 1 else "exploration"
        db.flush()
        _ekep_ids[key] = iv.id
        status_before = {t["id"]: t.get("status") for t in (iv.plan or {}).get("topics", [])}
        return iv.id, iv.current_topic_id, status_before


def _unit_for_turn(db, ekep_id: str, turn_id: str):
    """Wissenseinheit(en) des Analysten, die die neue Expertenantwort belegen – bevorzugt verifizierte Zitate."""
    best, best_rank = None, -1
    for u in orch.get_units(db, ekep_id):
        refs = [r for r in (u.source_refs or []) if r.get("turn_id") == turn_id]
        in_orig = turn_id in ((u.original_extraction or {}).get("source_turn_ids") or [])
        if not refs and not in_orig:
            continue
        rank = (2 if any(r.get("verified") for r in refs) else 1 if refs else 0) * 10 + (1 if u.version == 1 else 0)
        if rank > best_rank:
            best, best_rank = u, rank
    return best


_WORD_RX = re.compile(r"[a-zäöüß0-9]{4,}")
_STOP = {"relevante", "relevant", "welche", "diese", "dieser", "einer", "eines", "beim", "nach", "über", "unter",
         "oder", "sowie", "zwischen", "wird", "werden", "sind", "sein", "nicht", "auch", "noch", "dann"}


def _words(s: str) -> set[str]:
    return {w for w in _WORD_RX.findall(s.lower()) if w not in _STOP}


def _resolve_missing(req: ReplyRequest, statement: str) -> list[str]:
    sw = _words(statement)
    out = []
    for m in req.missingInfo:
        if m.resolved or not m.label:
            continue
        lw = _words(m.label)
        if lw and (len(lw & sw) >= 2 or (len(lw) == 1 and lw <= sw)):
            out.append(m.id)
    return out


# --------------------------------------------------------------------------
# Engine-Pfad
# --------------------------------------------------------------------------
async def _reply_via_orchestrator(req: ReplyRequest) -> ReplyResponse:
    t0 = time.perf_counter()
    ekep_id, topic_before, status_before = _ensure_interview(req)
    result = await orch.answer(ekep_id, req.expertText, mode="text", actor="dashboard", role="interviewer")
    reply = (result["ai_turn"].get("text") or "").strip() or FALLBACK_REPLY
    expert_turn_id = result["expert_turn"]["id"]
    t_answer = time.perf_counter() - t0

    # Analyst (Background) abwarten → extrahierte Wissenseinheit als Kernaussage
    try:
        await orch.wait_idle(ekep_id, timeout=ANALYSIS_WAIT_S)
    except Exception as e:  # pragma: no cover
        log.warning("dashboard reply: wait_idle: %s", str(e)[:200])
    valid_topics = {t.id: t.label for t in req.topics}
    with session_scope() as db:
        iv = db.get(Interview, ekep_id)
        unit = _unit_for_turn(db, ekep_id, expert_turn_id)
        topic_after = iv.current_topic_id
        status_after = {t["id"]: t.get("status") for t in (iv.plan or {}).get("topics", [])}
        if unit is not None:
            statement = (unit.content or unit.title or "").strip() or req.expertText.strip()[:300]
            topic = unit.topic or valid_topics.get(unit.topic_id or "", "") or ""
            confidence = _CONFIDENCE.get(unit.confidence or "", 0.7)
        else:
            statement = result["expert_turn"]["text"].strip()[:300]
            topic = valid_topics.get(topic_after or "", "") or valid_topics.get(topic_before or "", "") or ""
            confidence = 0.6

    check = await _rag_check(statement)
    plaus, contradiction = _plausibility(check) if check else (None, None)
    extracted = Extracted(statement=statement, topic=topic, confidence=confidence, plausibility=plaus)

    topic_done = None
    if topic_before in valid_topics and (
        status_after.get(topic_before) in ("saturated", "limit_reached") or (topic_after and topic_after != topic_before)
    ):
        topic_done = topic_before
    topic_id = topic_after if topic_after in valid_topics else None
    log.info("dashboard reply via orchestrator %.1fs (answer %.1fs) ekep=%s unit=%s verdict=%s",
             time.perf_counter() - t0, t_answer, ekep_id, getattr(unit, "id", None), (check or {}).get("verdict"))
    return ReplyResponse(reply=reply, extracted=extracted, resolveMissing=_resolve_missing(req, statement),
                         topicId=topic_id, topicDone=topic_done, contradiction=contradiction,
                         finished=bool(result.get("finished")) or None)


async def _reply_simple(req: ReplyRequest) -> ReplyResponse:
    t0 = time.perf_counter()
    decision = await get_llm().structured(system=LLM_SYSTEM, user=_llm_user(req), schema=TurnDecision,
                                          model=settings.interviewer_model, temperature=0.4)
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


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------
@router.post("/interviews/reply", response_model=ReplyResponse, response_model_exclude_none=True)
async def interview_reply(req: ReplyRequest) -> ReplyResponse:
    simple = os.getenv("DASHBOARD_ENGINE", "orchestrator").strip().lower() == "simple"
    try:
        if simple:
            return await _reply_simple(req)
        return await _reply_via_orchestrator(req)
    except Exception as e:
        log.warning("dashboard reply (%s) fehlgeschlagen: %s", "simple" if simple else "orchestrator", str(e)[:300])
        return ReplyResponse(reply=FALLBACK_REPLY, resolveMissing=[])

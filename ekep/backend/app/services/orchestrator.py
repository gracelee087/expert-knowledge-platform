"""Interview-Orchestrator: explizite State Machine.

    planned ──consent+start──▶ in_progress ◀──resume── paused
                                   │  └──pause──────────▲
                                   ▼
                               completed ──summary──▶ in_review ──alle geprüft──▶ approved

Phasen innerhalb von in_progress: opening → exploration → validation → closing → done

Pro Expertenantwort:
  1. Fast Path   – Interviewer entscheidet die nächste Frage (blockiert die Antwort)
  2. Background  – Analyst extrahiert Wissen/Coverage/Lücken/Widersprüche (asynchron, serialisiert)
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy import select

from ..agents import intelligence as ai
from ..config import settings
from ..db import session_scope
from ..models import (
    CONFIRMED_STATUSES, Contradiction, Employee, Gap, Interview, KnowledgeRevision, KnowledgeUnit, Turn,
)
from . import coverage as cov
from . import knowledge_search
from . import rag_client
from .audit import audit
from .events import hub
from .guards import redact_pii, verify_sources
from .serialize import elapsed_seconds, interview_out, turn_out, unit_snapshot

log = logging.getLogger("ekep.orchestrator")


class InterviewError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


_turn_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_analysis_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_pending: dict[str, set[asyncio.Task]] = defaultdict(set)
# RAG-Service (nur im Speicher, keine Schemaänderung): Prüfurteile je Expertenturn, letzte Belege je Interview
_verdicts: dict[str, dict[str, str]] = defaultdict(dict)
_last_rag: dict[str, dict] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load(db, interview_id: str) -> Interview:
    iv = db.get(Interview, interview_id)
    if iv is None:
        raise InterviewError("Interview nicht gefunden", 404)
    return iv


def _next_seq(iv: Interview) -> int:
    return (max((t.seq for t in iv.turns), default=0)) + 1


def _topic_ids(iv: Interview) -> list[str]:
    return [t["id"] for t in (iv.plan or {}).get("topics", [])]


def _set_topic_status(iv: Interview, topic_id: str | None, status: str) -> None:
    if not topic_id:
        return
    plan = dict(iv.plan or {})
    topics = [dict(t) for t in plan.get("topics", [])]
    for t in topics:
        if t["id"] == topic_id and t.get("status") not in ("saturated", "limit_reached"):
            t["status"] = status
    plan["topics"] = topics
    iv.plan = plan


async def _state(interview_id: str) -> dict:
    with session_scope() as db:
        return interview_out(_load(db, interview_id), full=True)


# --------------------------------------------------------------------------
# Planung
# --------------------------------------------------------------------------
async def create_plan(interview_id: str, actor: str, role: str) -> dict:
    with session_scope() as db:
        iv = _load(db, interview_id)
        emp = db.get(Employee, iv.employee_id)
        goal, minutes = iv.goal, iv.planned_minutes
    plan = await ai.plan_interview(emp, goal, minutes)
    with session_scope() as db:
        iv = _load(db, interview_id)
        topics = []
        for t in plan.topics:
            td = t.model_dump()
            td["status"] = "open"
            td["origin"] = "plan"
            topics.append(td)
        iv.plan = {"strategy": plan.strategy, "warmup_question": plan.warmup_question, "topics": topics}
        iv.coverage = cov.ensure_topics({}, [t["id"] for t in topics])
        audit(db, actor, role, "interview.plan_generated", "interview", iv.id, topics=len(topics))
    return await _state(interview_id)


# --------------------------------------------------------------------------
# Einwilligung & Start
# --------------------------------------------------------------------------
def give_consent(interview_id: str, actor: str, role: str) -> None:
    with session_scope() as db:
        iv = _load(db, interview_id)
        iv.consent_given_at = _now()
        iv.consent_version = settings.consent_text_version
        iv.audio_retention = settings.audio_retention
        audit(db, actor, role, "interview.consent_given", "interview", iv.id,
              version=settings.consent_text_version, audio_retention=settings.audio_retention)


def _rag_topic(iv: Interview, emp: Employee | None) -> str:
    return settings.rag_service_topic or iv.title or (iv.plan or {}).get("strategy") or (emp.role if emp else "")


def _rag_turns(iv: Interview) -> list[dict]:
    """KI-Frage mit der jeweils folgenden Expertenantwort paaren (+ kb_verdict aus dem Speicher)."""
    verdicts = _verdicts.get(iv.id, {})
    turns, question = [], None
    for t in iv.turns:
        if t.speaker == "ai":
            question = t.text
        elif t.speaker == "expert" and question is not None:
            turns.append({"question": question, "answer": t.text, "kb_verdict": verdicts.get(t.id)})
            question = None
    return turns


def _rag_augment(context: str, instruction: str, result: dict) -> tuple[str, str]:
    lines = []
    for ev in (result.get("evidence") or [])[:6]:
        text = " ".join(str(ev.get("text") or "").split())[:400]
        lines.append(f"- [{ev.get('citation') or ev.get('title') or ev.get('id')}] ({ev.get('validity_status')}) {text}")
    block = (
        "\n\nWISSENSBASIS (Dokumente, zeitlich gültig geprüft) - verwende diese Belege und bevorzuge die "
        "vorgeschlagene Frage:\n"
        f"VORGESCHLAGENE FRAGE [{result.get('reason')}]: {result.get('question')}\n"
        f"BEGRÜNDUNG: {result.get('rationale') or ''}\n"
        "BELEGE:\n" + ("\n".join(lines) if lines else "- (keine)")
    )
    hint = (" Stelle die VORGESCHLAGENE FRAGE aus der Wissensbasis (ggf. leicht angepasst an den Gesprächsfluss); "
            "bei Belegen mit Status possibly_outdated weise auf das aktuelle Dokument hin.")
    return context + block, instruction + hint


async def _ask(interview_id: str, instruction: str, *, phase_after: str | None = None) -> dict:
    """Interviewer aufrufen, KI-Turn persistieren und veröffentlichen."""
    with session_scope() as db:
        iv = _load(db, interview_id)
        emp = db.get(Employee, iv.employee_id)
        open_gaps = [g for g in iv.gaps if g.status == "open"]
        open_cons = [c for c in iv.contradictions if c.status == "open"]
        context = ai.interviewer_context(iv, emp, open_gaps, open_cons, elapsed_seconds(iv) / 60)
        rag_args = (emp.name, _rag_topic(iv, emp), _rag_turns(iv)) if rag_client.enabled() else None
    if rag_args:
        rag = await rag_client.next_question(*rag_args, lang=settings.rag_service_lang,
                                             as_of=_now().date().isoformat())
        if rag:
            _last_rag[interview_id] = rag
            context, instruction = _rag_augment(context, instruction, rag)
    decision = await ai.next_question(context, instruction)

    with session_scope() as db:
        iv = _load(db, interview_id)
        valid = set(_topic_ids(iv))
        topic_id = decision.topic_id if decision.topic_id in valid else iv.current_topic_id
        # Fortschritt/Sättigung festhalten (im Plan-JSON, keine Schemaänderung nötig)
        prog = dict(ai.progress(iv))
        gains = list(prog.get("gains", []))
        if iv.turns and iv.turns[-1].speaker == "expert":
            gains.append({"topic_id": iv.current_topic_id, "gain": decision.last_answer_information_gain})
        topic_gains = [g["gain"] for g in gains if g["topic_id"] == iv.current_topic_id][-2:]
        if topic_gains == ["niedrig", "niedrig"]:
            decision.current_topic_saturated = True  # deterministisch: 2× wenig Neues → gesättigt
        prog.update({"gains": gains[-40:], "remaining_estimate": max(0, decision.estimated_remaining_questions)})
        plan = dict(iv.plan or {})
        plan["progress"] = prog
        iv.plan = plan
        if decision.current_topic_saturated:
            _set_topic_status(iv, iv.current_topic_id, "saturated")
        if decision.expert_reached_knowledge_limit:
            _set_topic_status(iv, iv.current_topic_id, "limit_reached")
        if topic_id != iv.current_topic_id:
            iv.followup_depth = 0
            iv.current_topic_id = topic_id
        _set_topic_status(iv, topic_id, "in_progress")

        if phase_after:
            iv.phase = phase_after
        elif decision.action == "validate" and iv.phase == "exploration":
            iv.phase = "validation"
        elif decision.action == "close":
            iv.phase = "closing"
        elif decision.action in ("follow_up", "switch_topic") and iv.phase in ("opening", "validation"):
            iv.phase = "exploration"

        spoken = f"{decision.acknowledgement.strip()} {decision.question.strip()}".strip()
        now_s = elapsed_seconds(iv)
        turn = Turn(
            interview_id=iv.id, seq=_next_seq(iv), speaker="ai", text=spoken, technique=decision.technique,
            topic_id=topic_id, rationale=decision.rationale, t_start=now_s, t_end=now_s,
        )
        db.add(turn)
        iv.current_question = spoken
        iv.current_technique = decision.technique
        iv.last_activity_at = _now()
        db.flush()
        out = turn_out(turn)
        status = {"status": iv.status, "phase": iv.phase, "current_topic_id": iv.current_topic_id}
    await hub.publish(interview_id, "turn_added", out)
    await hub.publish(interview_id, "status_changed", status)
    return out


async def start(interview_id: str, actor: str, role: str) -> dict:
    async with _turn_locks[interview_id]:
        with session_scope() as db:
            iv = _load(db, interview_id)
            if not iv.plan or not iv.plan.get("topics"):
                raise InterviewError("Bitte zuerst einen Interviewplan erstellen.")
            if not iv.consent_given_at:
                raise InterviewError("Die Einwilligung des Experten fehlt.", 409)
            if iv.status == "in_progress" and iv.turns:
                return await _state(interview_id)
            if iv.status not in ("planned",):
                raise InterviewError(f"Start im Status '{iv.status}' nicht möglich.", 409)
            iv.status = "in_progress"
            iv.phase = "opening"
            iv.started_at = iv.last_resumed_at = _now()
            iv.current_topic_id = iv.plan["topics"][0]["id"]
            audit(db, actor, role, "interview.started", "interview", iv.id)
        await _ask(
            interview_id,
            "Eröffne das Interview: Begrüße den Experten mit Namen, erkläre in einem Satz das Ziel und dass alle "
            "Ergebnisse vor jeder Weitergabe von ihm geprüft werden. Stelle dann die Warm-up-Frage aus dem Plan "
            "(technique=opening, action=follow_up, topic_id=erstes Thema).",
            phase_after="opening",
        )
    return await _state(interview_id)


# --------------------------------------------------------------------------
# Antwort verarbeiten
# --------------------------------------------------------------------------
def _instruction(iv: Interview) -> str:
    elapsed_min = elapsed_seconds(iv) / 60
    open_topics = [t for t in (iv.plan or {}).get("topics", []) if t.get("status") in ("open", "in_progress")]
    asked = sum(1 for t in iv.turns if t.speaker == "ai")
    budget = ai.question_budget(iv)
    gains = ai.progress(iv).get("gains", [])
    topic_gains = [g["gain"] for g in gains if g["topic_id"] == iv.current_topic_id][-2:]
    if iv.phase == "closing":
        return "Schließe ab."
    if asked >= budget:
        return ("Das Fragen-Budget ist ausgeschöpft. Schließe jetzt ab: action=close, technique=closing, mit einer "
                "letzten Frage, was unbedingt noch festgehalten werden sollte.")
    if iv.phase == "validation" and asked >= budget - 1:
        return "Schließe ab: action=close, technique=closing."
    if asked >= budget * 0.8 and iv.phase in ("exploration", "opening"):
        return ("Das Fragen-Budget ist fast erreicht. Fasse die wichtigste Kernaussage kurz zusammen und lass sie "
                "bestätigen (action=validate, technique=validation).")
    if topic_gains == ["niedrig", "niedrig"]:
        return ("Die letzten Antworten zum aktuellen Thema brachten kaum Neues – das Thema ist gesättigt. "
                "Wechsle zum wichtigsten offenen Thema (action=switch_topic) oder schließe ab, wenn keines offen ist.")
    if iv.phase == "opening":
        return ("Das Warm-up ist beantwortet. Leite mit einer kurzen Brücke zum ersten Thema über und stelle eine "
                "offene, konkrete Einstiegsfrage (action=switch_topic oder follow_up).")
    if elapsed_min >= iv.planned_minutes * 0.95 or (not open_topics and iv.phase == "validation"):
        return ("Die geplante Zeit ist erreicht bzw. alle Themen sind behandelt. Schließe ab: action=close, "
                "technique=closing, mit einer letzten Frage, was unbedingt noch festgehalten werden sollte.")
    if elapsed_min >= iv.planned_minutes * 0.8 and iv.phase == "exploration":
        return ("Die Zeit wird knapp. Validiere die wichtigste noch unbestätigte Kernaussage durch kurzes "
                "Zurückspiegeln (action=validate, technique=validation).")
    if iv.followup_depth >= settings.max_followups_per_topic:
        return ("Das Nachfragelimit für das aktuelle Thema ist erreicht. Spiegele kurz zurück oder wechsle zum "
                "wichtigsten offenen Thema (action=switch_topic).")
    if not open_topics and iv.phase == "exploration":
        return "Alle geplanten Themen sind gesättigt. Validiere die zentralen Aussagen (action=validate)."
    return "Wähle die nächste Frage mit dem größten Erkenntnisgewinn."


async def answer(interview_id: str, text: str, *, mode: str = "text", duration_s: float | None = None,
                 actor: str = "expert", role: str = "expert") -> dict:
    text = (text or "").strip()
    if not text:
        raise InterviewError("Leere Antwort.")
    async with _turn_locks[interview_id]:
        with session_scope() as db:
            iv = _load(db, interview_id)
            if iv.status != "in_progress":
                raise InterviewError("Das Interview ist nicht aktiv.", 409)
            redactions = 0
            if settings.pii_redaction:
                text, redactions = redact_pii(text)
            t_end = elapsed_seconds(iv)
            question_before = iv.current_question
            dur = duration_s if duration_s else max(2.0, len(text.split()) / 2.4)
            turn = Turn(
                interview_id=iv.id, seq=_next_seq(iv), speaker="expert", text=text, topic_id=iv.current_topic_id,
                input_mode=mode, redactions=redactions, t_start=max(0.0, t_end - dur), t_end=t_end,
            )
            db.add(turn)
            iv.followup_depth = (iv.followup_depth or 0) + 1
            iv.last_activity_at = _now()
            closing = iv.phase == "closing"
            db.flush()
            expert_out = turn_out(turn)
            instruction = _instruction(iv)
            rag_fact = None
            if rag_client.enabled():
                emp = db.get(Employee, iv.employee_id)
                rag_fact = (emp.name, _rag_topic(iv, emp), emp.role or "")
        await hub.publish(interview_id, "turn_added", expert_out)
        schedule_analysis(interview_id, [expert_out["id"]])
        knowledge_search.schedule(interview_id, expert_out["id"], expert_out["text"], question_before)  # parallel
        if rag_fact:
            asyncio.create_task(_submit_fact(interview_id, expert_out["id"], rag_fact[0], rag_fact[1],
                                             question_before or "", expert_out["text"], rag_fact[2]))

        if closing:
            ai_out = await _closing_words(interview_id)
            asyncio.create_task(_finish_safely(interview_id, actor, role))
            return {"expert_turn": expert_out, "ai_turn": ai_out, "finished": True}

        ai_out = await _ask(interview_id, instruction)
        return {"expert_turn": expert_out, "ai_turn": ai_out, "finished": False}


async def _submit_fact(interview_id: str, turn_id: str, expert: str, topic: str, question: str, answer: str,
                       expert_role: str = "") -> None:
    """Expertenaussage an den RAG-Service melden; kb_verdict nur im Speicher merken (fire-and-forget)."""
    try:
        res = await rag_client.add_fact(expert, topic, question, answer, settings.rag_service_lang,
                                        _now().date().isoformat(), expert_role=expert_role)
    except Exception:  # pragma: no cover – rag_client fängt selbst, doppelte Absicherung
        log.exception("RAG add_fact failed")
        return
    if res and res.get("kb_verdict"):
        _verdicts[interview_id][turn_id] = str(res["kb_verdict"])


async def regenerate_question(interview_id: str) -> dict:
    """Falls der Interviewer-Aufruf fehlschlug: neue Frage für die letzte Expertenantwort."""
    async with _turn_locks[interview_id]:
        with session_scope() as db:
            iv = _load(db, interview_id)
            if iv.status != "in_progress":
                raise InterviewError("Das Interview ist nicht aktiv.", 409)
            instruction = _instruction(iv)
        return await _ask(interview_id, instruction)


async def _closing_words(interview_id: str) -> dict:
    with session_scope() as db:
        iv = _load(db, interview_id)
        emp = db.get(Employee, iv.employee_id)
        text = (f"Vielen Dank, {emp.name.split()[0] if emp.name else ''}. Ich bereite jetzt die Zusammenfassung vor. "
                "Sie können alle extrahierten Wissenseinheiten anschließend prüfen, korrigieren und freigeben.").replace("  ", " ")
        now_s = elapsed_seconds(iv)
        turn = Turn(interview_id=iv.id, seq=_next_seq(iv), speaker="ai", text=text, technique="closing",
                    topic_id=iv.current_topic_id, t_start=now_s, t_end=now_s)
        db.add(turn)
        iv.current_question = None
        db.flush()
        out = turn_out(turn)
    await hub.publish(interview_id, "turn_added", out)
    return out


# --------------------------------------------------------------------------
# Pause / Fortsetzen / Abschluss
# --------------------------------------------------------------------------
async def pause(interview_id: str, actor: str, role: str) -> dict:
    with session_scope() as db:
        iv = _load(db, interview_id)
        if iv.status != "in_progress":
            raise InterviewError("Nur laufende Interviews können pausiert werden.", 409)
        iv.active_seconds = elapsed_seconds(iv)
        iv.last_resumed_at = None
        iv.status = "paused"
        iv.last_activity_at = _now()
        audit(db, actor, role, "interview.paused", "interview", iv.id)
    await hub.publish(interview_id, "status_changed", {"status": "paused"})
    return await _state(interview_id)


async def resume(interview_id: str, actor: str, role: str) -> dict:
    async with _turn_locks[interview_id]:
        with session_scope() as db:
            iv = _load(db, interview_id)
            if iv.status != "paused":
                raise InterviewError("Nur pausierte Interviews können fortgesetzt werden.", 409)
            iv.status = "in_progress"
            iv.last_resumed_at = _now()
            last_is_ai = bool(iv.turns) and iv.turns[-1].speaker == "ai"
            audit(db, actor, role, "interview.resumed", "interview", iv.id)
        await hub.publish(interview_id, "status_changed", {"status": "in_progress"})
        await wait_idle(interview_id)
        await _ask(
            interview_id,
            "Das Interview wird nach einer Pause fortgesetzt. Fasse in einem Satz zusammen, wo Sie stehen geblieben "
            "sind (acknowledgement). "
            + ("Die letzte Frage wurde noch nicht beantwortet: stelle sie sinngemäß, kürzer erneut."
               if last_is_ai else "Stelle die nächste sinnvolle Frage, ohne Bekanntes zu wiederholen."),
        )
    return await _state(interview_id)


async def _finish_safely(interview_id: str, actor: str, role: str) -> None:
    try:
        await finish(interview_id, actor, role)
    except Exception:  # pragma: no cover
        log.exception("finish failed")


async def finish(interview_id: str, actor: str, role: str) -> dict:
    with session_scope() as db:
        iv = _load(db, interview_id)
        if iv.status not in ("in_progress", "paused", "completed"):
            raise InterviewError(f"Abschluss im Status '{iv.status}' nicht möglich.", 409)
        if iv.status != "completed":
            iv.active_seconds = elapsed_seconds(iv)
            iv.last_resumed_at = None
            iv.status = "completed"
            iv.phase = "done"
            iv.completed_at = _now()
            iv.current_question = None
            audit(db, actor, role, "interview.completed", "interview", iv.id)
    await hub.publish(interview_id, "status_changed", {"status": "completed", "phase": "done"})
    await wait_idle(interview_id)
    await hub.publish(interview_id, "summary_started", {})
    try:
        with session_scope() as db:
            iv = _load(db, interview_id)
            units = [u for u in iv.units if u.review_status != "rejected"]
            summary = await ai.summarize(iv, units, list(iv.gaps), list(iv.contradictions))
        with session_scope() as db:
            iv = _load(db, interview_id)
            iv.summary = summary.model_dump()
            iv.status = "in_review"
            audit(db, "system", "system", "interview.summary_generated", "interview", iv.id)
    except Exception as e:
        log.exception("summary failed")
        with session_scope() as db:
            iv = _load(db, interview_id)
            iv.status = "in_review"
        await hub.publish(interview_id, "error", {"message": f"Zusammenfassung fehlgeschlagen: {e}"})
    try:  # Abgleich mit der bestehenden Wissensbasis: neu / Ergänzung / bekannt / Widerspruch
        await knowledge_search.reconcile(interview_id)
    except Exception:
        log.exception("reconciliation failed")
    state = await _state(interview_id)
    await hub.publish(interview_id, "state", state)
    return state


# --------------------------------------------------------------------------
# Background-Analyse
# --------------------------------------------------------------------------
def schedule_analysis(interview_id: str, turn_ids: list[str]) -> asyncio.Task:
    task = asyncio.create_task(_run_analysis(interview_id, turn_ids))
    _pending[interview_id].add(task)
    task.add_done_callback(lambda t: _pending[interview_id].discard(t))
    return task


async def wait_idle(interview_id: str, timeout: float = 90) -> None:
    tasks = list(_pending.get(interview_id, ()))
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


def _new_unit_id() -> str:
    return f"KU-{secrets.token_hex(3).upper()}"


def _merge_refs(old: list, new: list) -> list:
    seen = {(r["turn_id"], r["quote"]) for r in old}
    return old + [r for r in new if (r["turn_id"], r["quote"]) not in seen]


async def _run_analysis(interview_id: str, turn_ids: list[str]) -> None:
    async with _analysis_locks[interview_id]:
        await hub.publish(interview_id, "analysis_started", {"turn_ids": turn_ids})
        try:
            with session_scope() as db:
                iv = _load(db, interview_id)
                units = list(iv.units)
                open_gaps = [g for g in iv.gaps if g.status == "open"]
                context = ai.analyst_context(iv, units, open_gaps, turn_ids)
            result = await ai.analyze(context)
            with session_scope() as db:
                changes = _apply_analysis(db, interview_id, result, turn_ids)
        except Exception as e:
            log.exception("analysis failed")
            await hub.publish(interview_id, "analysis_error", {"message": str(e)})
            return
        await hub.publish(interview_id, "analysis_updated", {"changes": changes, "state": await _state(interview_id)})


def _apply_analysis(db, interview_id: str, result, turn_ids: list[str]) -> dict:
    iv = _load(db, interview_id)
    expert_turns = {t.id: t for t in iv.turns if t.speaker == "expert"}
    topic_titles = {t["id"]: t["title"] for t in (iv.plan or {}).get("topics", [])}
    units_by_id = {u.id: u for u in iv.units}
    changes = {"new_units": [], "updated_units": [], "dropped_units": 0, "new_gaps": 0, "resolved_gaps": 0,
               "new_contradictions": 0, "new_topics": []}

    # 1) neue Themen (vom Experten eingebracht)
    plan = dict(iv.plan or {})
    topics = [dict(t) for t in plan.get("topics", [])]
    for nt in result.new_topics:
        if any(SequenceMatcher(None, nt.title.lower(), t["title"].lower()).ratio() > 0.75 for t in topics):
            continue
        tid = f"T{len(topics) + 1}"
        topics.append({"id": tid, "title": nt.title, "description": nt.description, "rationale": "Vom Experten eingebracht",
                       "priority": "mittel", "focus_dimensions": [], "opening_question": "", "tacit_knowledge_probes": [],
                       "status": "open", "origin": "emerged"})
        topic_titles[tid] = nt.title
        changes["new_topics"].append(tid)
    plan["topics"] = topics
    iv.plan = plan
    coverage = cov.ensure_topics(iv.coverage or {}, list(topic_titles))

    # 2) Wissenseinheiten mit Grounding-Check
    for eu in result.units:
        refs = verify_sources(eu.source_quotes, expert_turns)
        if not refs:
            changes["dropped_units"] += 1
            continue
        uncertainties = list(eu.uncertainties)
        confidence = eu.confidence
        if not any(r["verified"] for r in refs):
            confidence = "niedrig"
            uncertainties.append("Quellenbezug konnte nicht wörtlich im Transkript verifiziert werden.")
        topic_id = eu.topic_id if eu.topic_id in topic_titles else iv.current_topic_id
        fields = dict(
            topic_id=topic_id, topic=topic_titles.get(topic_id, topic_id or "Allgemein"), category=eu.category,
            title=eu.title, content=eu.content, explanation=eu.explanation, prerequisites=eu.prerequisites,
            process_steps=eu.process_steps, decision_logic=eu.decision_logic, exceptions=eu.exceptions,
            examples=eu.examples, risks=eu.risks, dependencies=eu.dependencies,
            escalation_paths=eu.escalation_paths, common_mistakes=eu.common_mistakes,
            historical_changes=eu.historical_changes, validity_note=eu.validity_note, confidence=confidence,
            confidence_reason=eu.confidence_reason, uncertainties=uncertainties,
        )
        existing = units_by_id.get(eu.existing_unit_id or "")
        if existing and existing.review_status not in CONFIRMED_STATUSES and existing.review_status != "rejected":
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.source_refs = _merge_refs(list(existing.source_refs or []), refs)
            existing.version += 1
            db.add(KnowledgeRevision(unit_id=existing.id, version=existing.version, action="ai_update",
                                     actor="ai-analyst", snapshot=unit_snapshot(existing)))
            changes["updated_units"].append(existing.id)
        else:
            if existing:  # bereits bestätigt → nicht überschreiben, sondern Ergänzung anlegen
                fields["dependencies"] = list(fields["dependencies"]) + [f"Ergänzt {existing.id}"]
            u = KnowledgeUnit(id=_new_unit_id(), interview_id=iv.id, source_refs=refs, review_status="extracted",
                              version=1, **fields)
            u.original_extraction = {**unit_snapshot(u), "source_turn_ids": turn_ids}
            db.add(u)
            db.flush()
            db.add(KnowledgeRevision(unit_id=u.id, version=1, action="extracted", actor="ai-analyst",
                                     snapshot=unit_snapshot(u)))
            units_by_id[u.id] = u
            changes["new_units"].append(u.id)

    # 3) Coverage (Guardrail: kein expert_confirmed durch KI)
    for cu in result.coverage_updates:
        if cu.topic_id in coverage:
            coverage = cov.apply_ai_update(coverage, cu.topic_id, cu.dimension, cu.status, cu.evidence)
    iv.coverage = cov.derive_with_review(coverage, list(units_by_id.values()))

    # 4) Lücken
    open_gaps = {g.id: g for g in iv.gaps if g.status == "open"}
    for gid in result.resolved_gap_ids:
        g = open_gaps.get(gid)
        if g:
            g.status = "resolved"
            g.resolved_turn_id = turn_ids[-1] if turn_ids else None
            changes["resolved_gaps"] += 1
    for ng in result.new_gaps:
        if any(SequenceMatcher(None, ng.description.lower(), g.description.lower()).ratio() > 0.8
               for g in open_gaps.values()):
            continue
        g = Gap(interview_id=iv.id, topic_id=ng.topic_id if ng.topic_id in topic_titles else iv.current_topic_id,
                dimension=ng.dimension, description=ng.description, created_turn_id=turn_ids[-1] if turn_ids else None)
        db.add(g)
        open_gaps[g.id or ng.description] = g
        changes["new_gaps"] += 1

    # 5) Widersprüche
    known = [c.description.lower() for c in iv.contradictions]
    for c in result.contradictions:
        if any(SequenceMatcher(None, c.description.lower(), k).ratio() > 0.8 for k in known):
            continue
        db.add(Contradiction(interview_id=iv.id, description=c.description, statement_a=c.statement_a,
                             statement_b=c.statement_b, turn_ids=c.turn_ids))
        known.append(c.description.lower())
        changes["new_contradictions"] += 1
    iv.last_activity_at = _now()
    return changes


# --------------------------------------------------------------------------
# Review-Hilfen
# --------------------------------------------------------------------------
def refresh_review_state(db, iv: Interview) -> None:
    iv.coverage = cov.derive_with_review(iv.coverage or {}, list(iv.units))
    active = [u for u in iv.units if u.review_status != "rejected"]
    if iv.status == "in_review" and active and all(
        u.review_status in CONFIRMED_STATUSES or u.review_status == "uncertain" for u in active
    ):
        iv.status = "approved"
    elif iv.status == "approved" and any(u.review_status == "extracted" for u in active):
        iv.status = "in_review"


def get_units(db, interview_id: str) -> list[KnowledgeUnit]:
    return list(db.scalars(select(KnowledgeUnit).where(KnowledgeUnit.interview_id == interview_id)))

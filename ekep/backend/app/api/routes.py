from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents import intelligence as ai
from ..config import settings
from ..db import get_db
from ..llm.provider import LLMError, get_stt, get_tts
from ..models import (
    CONFIRMED_STATUSES, AuditLog, Contradiction, Employee, Gap, Interview, KnowledgeRevision, KnowledgeUnit,
    TrainingPackage,
)
from ..services import orchestrator as orch
from ..services.audit import audit
from ..services.events import hub
from ..services.export import build_export, export_json_schema
from ..services.serialize import EDITABLE_FIELDS, employee_out, interview_out, iso, unit_out, unit_snapshot
from .deps import Principal, principal, require

log = logging.getLogger("ekep.api")
router = APIRouter(prefix="/api")

ADMIN = ("admin",)
STAFF = ("admin", "interviewer")
PARTICIPANT = ("admin", "interviewer", "expert")
REVIEWER = ("admin", "expert")

CONSENT_TEXT = [
    "Das Interview wird durch eine KI geführt. Ihre Aussagen werden transkribiert und zur Wissenssicherung strukturiert.",
    "Audioaufnahmen werden nur zur Transkription verarbeitet und nicht dauerhaft gespeichert, sofern nicht anders vereinbart.",
    "Bitte nennen Sie keine personenbezogenen Kundendaten (Namen, Vertragsnummern, IBAN). Erkannte Daten werden automatisch maskiert.",
    "Alle extrahierten Inhalte werden erst nach Ihrer Prüfung und Freigabe an nachgelagerte Systeme weitergegeben.",
    "Sie können das Interview jederzeit pausieren oder abbrechen und die Löschung Ihrer Daten verlangen.",
]


def _err(e: Exception):
    if isinstance(e, orch.InterviewError):
        raise HTTPException(e.status_code, str(e))
    if isinstance(e, LLMError):
        raise HTTPException(502, str(e))
    raise e


# ------------------------------------------------------------------ meta
@router.get("/health")
def health():
    return {"status": "ok", "llm_provider": settings.llm_provider,
            "llm_configured": bool(settings.openai_api_key or settings.azure_api_key)}


@router.get("/config/public")
def public_config():
    return {"consent_text": CONSENT_TEXT, "consent_version": settings.consent_text_version,
            "audio_retention": settings.audio_retention, "pii_redaction": settings.pii_redaction,
            "auth_mode": settings.auth_mode, "language": settings.interview_language,
            "voice_engine": settings.voice_engine, "stt_engine": settings.stt_engine,
            "tts_engine": settings.tts_engine}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), p: Principal = Depends(require(*PARTICIPANT))):
    interviews = list(db.scalars(select(Interview)))
    units = [u for iv in interviews for u in iv.units if u.review_status != "rejected"]
    scores = [interview_out(iv)["coverage_score"] for iv in interviews if iv.coverage]
    cat_counts: dict[str, int] = {}
    for u in units:
        cat_counts[u.category] = cat_counts.get(u.category, 0) + 1
    activity = list(db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(12)))
    return {
        "employees": db.scalar(select(func.count(Employee.id))) or 0,
        "interviews_total": len(interviews),
        "interviews_completed": sum(1 for iv in interviews if iv.status in ("completed", "in_review", "approved")),
        "interviews_in_progress": sum(1 for iv in interviews if iv.status in ("in_progress", "paused")),
        "knowledge_units": len(units),
        "confirmed_units": sum(1 for u in units if u.review_status in CONFIRMED_STATUSES),
        "open_validations": sum(1 for u in units if u.review_status == "extracted"),
        "avg_coverage": round(sum(scores) / len(scores), 3) if scores else 0.0,
        "categories": cat_counts,
        "recent_interviews": [interview_out(iv) for iv in sorted(interviews, key=lambda x: x.created_at, reverse=True)[:6]],
        "activity": [{"at": iso(a.at), "actor": a.actor, "role": a.role, "action": a.action,
                      "entity_type": a.entity_type, "entity_id": a.entity_id} for a in activity],
    }


@router.get("/audit")
def audit_log(limit: int = 100, db: Session = Depends(get_db), p: Principal = Depends(require(*ADMIN))):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.at.desc()).limit(min(limit, 500)))
    return [{"at": iso(a.at), "actor": a.actor, "role": a.role, "action": a.action, "entity_type": a.entity_type,
             "entity_id": a.entity_id, "details": a.details} for a in rows]


# ------------------------------------------------------------------ employees
class EmployeeIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    department: str
    role: str
    expertise_area: str
    years_experience: int | None = None
    responsibilities: list[str] = []
    specializations: list[str] = []


@router.get("/employees")
def list_employees(db: Session = Depends(get_db), p: Principal = Depends(require(*PARTICIPANT))):
    return [employee_out(e, with_stats=True) for e in db.scalars(select(Employee).order_by(Employee.name))]


@router.post("/employees", status_code=201)
def create_employee(body: EmployeeIn, db: Session = Depends(get_db), p: Principal = Depends(require(*ADMIN))):
    e = Employee(**body.model_dump())
    db.add(e)
    db.flush()
    audit(db, p.user, p.role, "employee.created", "employee", e.id)
    db.commit()
    return employee_out(e, with_stats=True)


@router.get("/employees/{emp_id}")
def get_employee(emp_id: str, db: Session = Depends(get_db), p: Principal = Depends(require(*PARTICIPANT))):
    e = db.get(Employee, emp_id) or _404("Mitarbeiter")
    d = employee_out(e, with_stats=True)
    d["interviews"] = [interview_out(iv) for iv in sorted(e.interviews, key=lambda x: x.created_at, reverse=True)]
    return d


@router.put("/employees/{emp_id}")
def update_employee(emp_id: str, body: EmployeeIn, db: Session = Depends(get_db), p: Principal = Depends(require(*ADMIN))):
    e = db.get(Employee, emp_id) or _404("Mitarbeiter")
    for k, v in body.model_dump().items():
        setattr(e, k, v)
    audit(db, p.user, p.role, "employee.updated", "employee", e.id)
    db.commit()
    return employee_out(e, with_stats=True)


@router.delete("/employees/{emp_id}", status_code=204)
def delete_employee(emp_id: str, db: Session = Depends(get_db), p: Principal = Depends(require(*ADMIN))):
    e = db.get(Employee, emp_id) or _404("Mitarbeiter")
    audit(db, p.user, p.role, "employee.deleted", "employee", e.id, interviews=len(e.interviews))
    db.delete(e)
    db.commit()


def _404(what: str):
    raise HTTPException(404, f"{what} nicht gefunden")


# ------------------------------------------------------------------ interviews
class InterviewIn(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    goal: str = Field(min_length=5)
    planned_minutes: int = Field(default=45, ge=5, le=180)


@router.post("/employees/{emp_id}/interviews", status_code=201)
def create_interview(emp_id: str, body: InterviewIn, db: Session = Depends(get_db),
                     p: Principal = Depends(require(*STAFF))):
    e = db.get(Employee, emp_id) or _404("Mitarbeiter")
    iv = Interview(employee_id=e.id, **body.model_dump(), audio_retention=settings.audio_retention)
    db.add(iv)
    db.flush()
    audit(db, p.user, p.role, "interview.created", "interview", iv.id)
    db.commit()
    return interview_out(iv, full=True)


@router.get("/interviews")
def list_interviews(status: str | None = None, db: Session = Depends(get_db),
                    p: Principal = Depends(require(*PARTICIPANT))):
    q = select(Interview).order_by(Interview.created_at.desc())
    if status:
        q = q.where(Interview.status == status)
    return [interview_out(iv) for iv in db.scalars(q)]


@router.get("/interviews/{iv_id}")
def get_interview(iv_id: str, db: Session = Depends(get_db), p: Principal = Depends(require(*PARTICIPANT))):
    iv = db.get(Interview, iv_id) or _404("Interview")
    return interview_out(iv, full=True)


@router.delete("/interviews/{iv_id}", status_code=204)
def delete_interview(iv_id: str, db: Session = Depends(get_db), p: Principal = Depends(require(*ADMIN))):
    iv = db.get(Interview, iv_id) or _404("Interview")
    audit(db, p.user, p.role, "interview.deleted", "interview", iv.id, units=len(iv.units), turns=len(iv.turns))
    db.delete(iv)
    db.commit()


@router.post("/interviews/{iv_id}/plan")
async def plan(iv_id: str, p: Principal = Depends(require(*STAFF))):
    try:
        return await orch.create_plan(iv_id, p.user, p.role)
    except Exception as e:
        _err(e)


@router.post("/interviews/{iv_id}/consent")
async def consent(iv_id: str, p: Principal = Depends(require(*PARTICIPANT))):
    try:
        orch.give_consent(iv_id, p.user, p.role)
        return await orch._state(iv_id)
    except Exception as e:
        _err(e)


@router.post("/interviews/{iv_id}/start")
async def start(iv_id: str, p: Principal = Depends(require(*PARTICIPANT))):
    try:
        return await orch.start(iv_id, p.user, p.role)
    except Exception as e:
        _err(e)


class AnswerIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    mode: Literal["text", "voice"] = "text"  # voice = im Browser transkribiert (VOICE_ENGINE=browser)
    duration_s: float | None = Field(default=None, ge=0, le=900)


@router.post("/interviews/{iv_id}/answer")
async def answer_text(iv_id: str, body: AnswerIn, p: Principal = Depends(require(*PARTICIPANT))):
    try:
        return await orch.answer(iv_id, body.text, mode=body.mode, duration_s=body.duration_s, actor=p.user, role=p.role)
    except Exception as e:
        _err(e)


MAX_AUDIO_BYTES = 25 * 1024 * 1024


@router.post("/interviews/{iv_id}/answer/audio")
async def answer_audio(iv_id: str, audio: UploadFile = File(...), duration_s: float | None = Form(default=None),
                       p: Principal = Depends(require(*PARTICIPANT))):
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Leere Audiodatei")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audiodatei zu groß")
    # Datenschutz: Audio wird standardmäßig nur im Speicher verarbeitet
    if settings.audio_retention == "store":
        os.makedirs(settings.audio_dir, exist_ok=True)
        with open(os.path.join(settings.audio_dir, f"{iv_id}_{secrets.token_hex(4)}.webm"), "wb") as f:
            f.write(data)
    try:
        from ..db import session_scope

        with session_scope() as db:
            iv = db.get(Interview, iv_id) or _404("Interview")
            hint = iv.current_question or ""
            vocab = "Bausparvertrag, Tarifgeneration, Bausparsumme, Zuteilung, Bewertungszahl, Regelsparbeitrag, Wohnungsbauprämie, Arbeitnehmersparzulage, Vor- und Zwischenfinanzierung"
        text = await get_stt().transcribe(data, audio.filename or "answer.webm", audio.content_type or "audio/webm",
                                             prompt=f"{vocab}. {hint}")
    except LLMError as e:
        raise HTTPException(502, str(e))
    finally:
        del data
    if not text or len(text.strip()) < 2:
        raise HTTPException(422, "Es wurde keine Sprache erkannt. Bitte erneut sprechen.")
    try:
        return await orch.answer(iv_id, text, mode="voice", duration_s=duration_s, actor=p.user, role=p.role)
    except Exception as e:
        _err(e)


@router.post("/interviews/{iv_id}/regenerate")
async def regenerate(iv_id: str, p: Principal = Depends(require(*PARTICIPANT))):
    try:
        return await orch.regenerate_question(iv_id)
    except Exception as e:
        _err(e)


@router.post("/interviews/{iv_id}/pause")
async def pause(iv_id: str, p: Principal = Depends(require(*PARTICIPANT))):
    try:
        return await orch.pause(iv_id, p.user, p.role)
    except Exception as e:
        _err(e)


@router.post("/interviews/{iv_id}/resume")
async def resume(iv_id: str, p: Principal = Depends(require(*PARTICIPANT))):
    try:
        return await orch.resume(iv_id, p.user, p.role)
    except Exception as e:
        _err(e)


@router.post("/interviews/{iv_id}/finish")
async def finish(iv_id: str, p: Principal = Depends(require(*PARTICIPANT))):
    try:
        return await orch.finish(iv_id, p.user, p.role)
    except Exception as e:
        _err(e)


class StatusIn(BaseModel):
    status: str
    resolution: str | None = None


@router.post("/interviews/{iv_id}/gaps/{gap_id}")
def update_gap(iv_id: str, gap_id: str, body: StatusIn, db: Session = Depends(get_db),
               p: Principal = Depends(require(*PARTICIPANT))):
    g = db.get(Gap, gap_id)
    if not g or g.interview_id != iv_id or body.status not in ("open", "resolved", "dismissed"):
        raise HTTPException(400, "Ungültig")
    g.status = body.status
    audit(db, p.user, p.role, "gap.updated", "gap", g.id, status=body.status)
    db.commit()
    return {"ok": True}


@router.post("/interviews/{iv_id}/contradictions/{cid}")
def update_contradiction(iv_id: str, cid: str, body: StatusIn, db: Session = Depends(get_db),
                         p: Principal = Depends(require(*REVIEWER))):
    c = db.get(Contradiction, cid)
    if not c or c.interview_id != iv_id or body.status not in ("open", "clarified", "dismissed"):
        raise HTTPException(400, "Ungültig")
    c.status, c.resolution = body.status, body.resolution
    audit(db, p.user, p.role, "contradiction.updated", "contradiction", c.id, status=body.status)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ review
class ReviewIn(BaseModel):
    action: Literal["confirm", "correct", "mark_uncertain", "reject", "add_example", "add_note", "reopen"]
    fields: dict | None = None
    text: str | None = None
    comment: str | None = None


@router.get("/units/{unit_id}")
def get_unit(unit_id: str, db: Session = Depends(get_db), p: Principal = Depends(require(*PARTICIPANT))):
    u = db.get(KnowledgeUnit, unit_id) or _404("Wissenseinheit")
    return unit_out(u, with_history=True)


@router.post("/units/{unit_id}/review")
def review_unit(unit_id: str, body: ReviewIn, db: Session = Depends(get_db),
                p: Principal = Depends(require(*REVIEWER))):
    u = db.get(KnowledgeUnit, unit_id) or _404("Wissenseinheit")
    iv = db.get(Interview, u.interview_id)
    if iv.status in ("in_progress", "paused", "planned"):
        raise HTTPException(409, "Review ist erst nach Abschluss des Interviews möglich.")
    now = datetime.now(timezone.utc)
    a = body.action
    if a == "confirm":
        u.review_status = "confirmed"
    elif a == "correct":
        changed = {k: v for k, v in (body.fields or {}).items() if k in EDITABLE_FIELDS}
        if not changed:
            raise HTTPException(400, "Keine änderbaren Felder übergeben.")
        for k, v in changed.items():
            setattr(u, k, v)
        u.review_status = "corrected"
    elif a == "mark_uncertain":
        u.review_status = "uncertain"
        if body.text:
            u.uncertainties = list(u.uncertainties or []) + [f"Experte: {body.text}"]
    elif a == "reject":
        u.review_status = "rejected"
    elif a == "add_example":
        if not body.text:
            raise HTTPException(400, "Beispieltext fehlt.")
        u.examples = list(u.examples or []) + [body.text]
    elif a == "add_note":
        if not body.text:
            raise HTTPException(400, "Text fehlt.")
        u.expert_notes = list(u.expert_notes or []) + [body.text]
    elif a == "reopen":
        u.review_status = "extracted"
    u.reviewed_by, u.reviewed_at = p.user, now
    u.version += 1
    db.add(KnowledgeRevision(unit_id=u.id, version=u.version, action=a, actor=p.user, comment=body.comment,
                             snapshot=unit_snapshot(u)))
    db.flush()
    orch.refresh_review_state(db, iv)
    audit(db, p.user, p.role, f"unit.{a}", "knowledge_unit", u.id, version=u.version)
    db.commit()
    db.refresh(u)
    return {"unit": unit_out(u, with_history=True), "interview_status": iv.status}


# ------------------------------------------------------------------ training (Phase-3-Vorschau)
class TrainingIn(BaseModel):
    audience: Literal["einsteiger", "fortgeschritten", "experten"] = "einsteiger"


@router.post("/interviews/{iv_id}/training")
async def create_training(iv_id: str, body: TrainingIn, db: Session = Depends(get_db),
                          p: Principal = Depends(require(*STAFF))):
    iv = db.get(Interview, iv_id) or _404("Interview")
    units = [u for u in iv.units if u.review_status in CONFIRMED_STATUSES]
    if not units:
        raise HTTPException(409, "Training nur aus expertenbestätigten Wissenseinheiten. Bitte zuerst im Review bestätigen.")
    try:
        content = await ai.generate_training(units, body.audience, iv.title)
    except LLMError as e:
        raise HTTPException(502, str(e))
    valid = {u.id for u in units}
    data = content.model_dump()
    for s in data["slides"]:
        s["source_unit_ids"] = [x for x in s["source_unit_ids"] if x in valid]
    for q in data["quiz"]:
        q["source_unit_ids"] = [x for x in q["source_unit_ids"] if x in valid]
        q["correct_index"] = max(0, min(q["correct_index"], len(q["options"]) - 1))
    tp = TrainingPackage(interview_id=iv.id, audience=body.audience, content=data, source_unit_ids=sorted(valid))
    db.add(tp)
    db.flush()
    audit(db, p.user, p.role, "training.generated", "training", tp.id, units=len(valid))
    db.commit()
    return {"id": tp.id, "audience": tp.audience, "content": data, "created_at": iso(tp.created_at)}


@router.get("/interviews/{iv_id}/training")
def list_training(iv_id: str, db: Session = Depends(get_db), p: Principal = Depends(require(*PARTICIPANT))):
    iv = db.get(Interview, iv_id) or _404("Interview")
    return [{"id": t.id, "audience": t.audience, "content": t.content, "created_at": iso(t.created_at)}
            for t in sorted(iv.trainings, key=lambda x: x.created_at, reverse=True)]


# ------------------------------------------------------------------ voice
class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/tts")
async def tts(body: TTSIn, p: Principal = Depends(require(*PARTICIPANT))):
    try:
        audio = await get_tts().synthesize(body.text)
    except LLMError as e:
        raise HTTPException(502, str(e))
    return Response(content=audio, media_type=getattr(get_tts(), "audio_mime", "audio/mpeg"), headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ export (Vertrag mit RAG-Team)
export_router = APIRouter(prefix="/api/v1/export", tags=["export"])
EXPORTERS = ("admin", "rag_consumer")


@export_router.get("/schema")
def schema():
    return export_json_schema()


@export_router.get("/interviews")
def exportable(db: Session = Depends(get_db), p: Principal = Depends(require(*EXPORTERS))):
    out = []
    for iv in db.scalars(select(Interview).where(Interview.status.in_(["in_review", "approved"]))):
        n = sum(1 for u in iv.units if u.review_status in CONFIRMED_STATUSES)
        out.append({"interview_id": iv.id, "title": iv.title, "status": iv.status, "confirmed_units": n,
                    "completed_at": iso(iv.completed_at)})
    return out


@export_router.get("/interviews/{iv_id}")
def export_interview(iv_id: str, include_unconfirmed: bool = False, include_transcript: bool = False,
                     db: Session = Depends(get_db), p: Principal = Depends(require(*EXPORTERS))):
    iv = db.get(Interview, iv_id) or _404("Interview")
    if include_unconfirmed and p.role != "admin":
        raise HTTPException(403, "Ungeprüfte Inhalte dürfen nicht an nachgelagerte Systeme gegeben werden.")
    data = build_export(iv, include_unconfirmed=include_unconfirmed, include_transcript=include_transcript)
    audit(db, p.user, p.role, "export.generated", "interview", iv.id, export_id=data["export_id"],
          units=len(data["knowledge_units"]), include_unconfirmed=include_unconfirmed)
    db.commit()
    return data


# ------------------------------------------------------------------ websocket
ws_router = APIRouter()


@ws_router.websocket("/ws/interviews/{iv_id}")
async def ws_interview(ws: WebSocket, iv_id: str):
    await ws.accept()
    await hub.join(iv_id, ws)
    try:
        await ws.send_json({"event": "state", "data": await orch._state(iv_id)})
        while True:
            msg = await ws.receive_text()
            if msg == "ping":
                await ws.send_text('{"event":"pong","data":{}}')
            elif msg == "sync":
                await ws.send_json({"event": "state", "data": await orch._state(iv_id)})
    except WebSocketDisconnect:
        pass
    except orch.InterviewError:
        await ws.close(code=4404)
    finally:
        hub.leave(iv_id, ws)

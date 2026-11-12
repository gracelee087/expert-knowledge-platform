"""Adapter für das Dashboard-Frontend: GET /api/learning/modules.

Liefert die nach KI-Interviews erzeugten Schulungspakete (Slides + Quiz) und die daraus
generierten Lernvideos in der „LearningModule“-Struktur des Dashboards
(src/types/index.ts: LearningModule, Chapter, Slide, Quiz, QuizQuestion).

Rein lesend, keine LLM-Aufrufe, kein Auth-Dependency (das Frontend sendet keine Header).
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..models import AuditLog, Interview, TrainingPackage
from ..services.serialize import iso

router = APIRouter(prefix="/api", tags=["dashboard"])

VIDEO_ACTION = "training.video_generated"
PASSING_SCORE = 70
MIN_DURATION_MIN = 5

# audience (TrainingPackage) → (Audience, Difficulty) im Dashboard
LEVEL_MAP = {
    "einsteiger": ("Anfänger", "Leicht"),
    "fortgeschritten": ("Fortgeschrittene", "Mittel"),
    "experten": ("Experten", "Schwer"),
}
# Reihenfolge = Priorität; erster Treffer gewinnt
CATEGORY_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("kredit", "finanzierung"), "Finanzierung"),
    (("tarif",), "Tarife"),
    (("förder", "foerder", "prämie", "praemie"), "Förderung"),
    (("compliance", "regulator", "wikr"), "Regulatorik"),
    (("prozess", "zuteilung", "auszahlung"), "Prozesse"),
]
ROLE_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("kredit", "finanzierung"), "Kreditanalyse"),
    (("förder", "foerder", "wibank"), "Förderberatung"),
    (("compliance", "regulator"), "Compliance"),
    (("beratung", "vertrieb", "berater"), "Bausparberatung"),
    (("teamleit", "leitung"), "Teamleitung"),
    (("personal", "hr"), "Personal (HR)"),
    (("knowledge", "wissensmanag"), "Knowledge Management"),
]
DEFAULT_CATEGORY = "Bausparen"
DEFAULT_ROLE = "Sachbearbeitung Bestandsverwaltung"


def _match(text: str, table: list[tuple[tuple[str, ...], str]], default: str) -> str:
    t = text.lower()
    for keywords, value in table:
        if any(k in t for k in keywords):
            return value
    return default


def _video_files(db) -> dict[str, list[str]]:
    """interview_id → Dateinamen aus audit_log (neueste zuerst)."""
    rows = db.execute(
        select(AuditLog).where(AuditLog.action == VIDEO_ACTION).order_by(AuditLog.at.desc(), AuditLog.id.desc())
    ).scalars().all()
    out: dict[str, list[str]] = {}
    for r in rows:
        f = (r.details or {}).get("file")
        if r.entity_id and f:
            out.setdefault(r.entity_id, []).append(str(f))
    return out


def _video_url(files: list[str]) -> str | None:
    if not files:
        return None
    chosen = next((f for f in files if "motion" in f.lower()), files[0])
    return f"{settings.rag_service_url.rstrip('/')}/videos/{chosen}"


def _module_out(tp: TrainingPackage, iv: Interview | None, video_url: str | None) -> dict:
    content = tp.content or {}
    title = content.get("title") or (iv.title if iv else "Schulung")
    objectives = [str(o) for o in (content.get("learning_objectives") or []) if o]
    slides_in = content.get("slides") or []
    quiz_in = content.get("quiz") or []
    emp = iv.employee if iv else None

    level, difficulty = LEVEL_MAP.get((tp.audience or "").lower(), LEVEL_MAP["einsteiger"])
    category = _match(f"{iv.title if iv else ''} {emp.department if emp else ''}", CATEGORY_KEYWORDS, DEFAULT_CATEGORY)
    role = _match(f"{emp.department if emp else ''} {emp.role if emp else ''}", ROLE_KEYWORDS, DEFAULT_ROLE)
    duration = max(MIN_DURATION_MIN, 3 * len(slides_in) + len(quiz_in))

    slides = [
        {
            "title": str(s.get("title") or ""),
            "bullets": [str(b) for b in (s.get("bullets") or [])],
            "notes": str(s.get("speaker_notes") or ""),
        }
        for s in slides_in
    ]
    questions = [
        {
            "id": f"q-{tp.id}-{n}",
            "question": str(q.get("question") or ""),
            "options": [str(o) for o in (q.get("options") or [])],
            "correctIndex": int(q.get("correct_index") or 0),
            "explanation": str(q.get("explanation") or ""),
        }
        for n, q in enumerate(quiz_in, start=1)
    ]
    chapter = {
        "id": f"ch-{tp.id}-1",
        "title": f"Lernvideo: {title}",
        "type": "video",
        "durationMin": duration,
        "summary": " · ".join(objectives),
        "slides": slides,
        "quiz": {"id": f"qz-{tp.id}", "title": "Wissenscheck", "passingScore": PASSING_SCORE, "questions": questions},
    }
    description = objectives[0] if objectives else f"Aus dem Experteninterview „{iv.title if iv else ''}“ generiert."
    return {
        "id": f"trn-{tp.id}",
        "title": title,
        "description": description,
        "category": category,
        "level": level,
        "difficulty": difficulty,
        "durationMin": duration,
        "chapters": [chapter],
        "sourceArticleIds": [],
        "createdAt": iso(tp.created_at),
        "generated": True,
        "format": "video",
        "targetRoles": [role],
        "sourceExperts": [emp.name] if emp else [],
        "seed": False,
        "tags": ["neu"],
        "videoUrl": video_url,
        "interviewId": tp.interview_id,
        "audience": tp.audience,
    }


@router.get("/learning/modules")
def list_learning_modules(interviewId: str | None = Query(default=None)):
    with session_scope() as db:
        stmt = select(TrainingPackage).order_by(TrainingPackage.created_at.desc())
        if interviewId:
            stmt = stmt.where(TrainingPackage.interview_id == interviewId)
        packages = db.execute(stmt).scalars().all()
        videos = _video_files(db)
        out = []
        for tp in packages:
            iv = db.get(Interview, tp.interview_id)
            out.append(_module_out(tp, iv, _video_url(videos.get(tp.interview_id, []))))
        return out

"""Adapter für das Dashboard-Frontend: GET /api/learning/modules, GET /api/learning/articles.

  /learning/modules   Schulungspakete (Slides + Quiz) und generierte Lernvideos je Interview
                      in der „LearningModule“-Struktur des Dashboards.
  /learning/articles  Expertenbestätigte Wissenseinheiten je Interview als „KnowledgeArticle“
                      (src/types/index.ts: KnowledgeArticle, ArticleVersion, QuizFact).

Rein lesend, keine LLM-Aufrufe, kein Auth-Dependency (das Frontend sendet keine Header).
"""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..models import AuditLog, Interview, KnowledgeUnit, TrainingPackage
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
# Category → Department (Dashboard-Enum)
DEPARTMENT_BY_CATEGORY = {
    "Finanzierung": "Immobilienfinanzierung",
    "Förderung": "WIBank Förderung",
    "Regulatorik": "Compliance & Regulatorik",
    "Prozesse": "LBS Bestandsverwaltung",
}
DEFAULT_DEPARTMENT = "LBS Bausparen"
ARTICLE_UNIT_STATUSES = ("confirmed", "approved")
PARAGRAPH_MAX_CHARS = 1200


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


def _ymd(dt) -> str | None:
    s = iso(dt)
    return s[:10] if s else None


def _facts_out(tp: TrainingPackage | None) -> list[dict]:
    if tp is None:
        return []
    facts = []
    for q in (tp.content or {}).get("quiz") or []:
        options = [str(o) for o in (q.get("options") or [])]
        ci = int(q.get("correct_index") or 0)
        if not options or not (0 <= ci < len(options)):
            continue
        facts.append({
            "question": str(q.get("question") or ""),
            "answer": options[ci],
            "distractors": [o for i, o in enumerate(options) if i != ci],
            "explanation": str(q.get("explanation") or ""),
        })
    return facts


def _article_out(iv: Interview, units: list[KnowledgeUnit], tp: TrainingPackage | None) -> dict:
    emp = iv.employee
    name = emp.name if emp else ""
    content = []
    for u in units:
        p = f"{u.title}: {u.content}"
        if (u.explanation or "").strip():
            p += " " + u.explanation.strip()
        content.append(p[:PARAGRAPH_MAX_CHARS])
    category = _match(" ".join([iv.title, *[u.title for u in units], *[u.topic for u in units]]),
                      CATEGORY_KEYWORDS, DEFAULT_CATEGORY)
    role = _match(f"{emp.department if emp else ''} {emp.role if emp else ''}", ROLE_KEYWORDS, DEFAULT_ROLE)
    reviewed = [u.reviewed_at for u in units if u.reviewed_at]
    updated = _ymd(max(reviewed)) if reviewed else _ymd(iv.created_at)
    created = _ymd(iv.created_at)
    return {
        "id": f"iv-{iv.id}",
        "title": iv.title,
        "description": f"Expertenwissen aus dem KI-Interview mit {name} ({len(units)} expertenbestätigte Wissenseinheiten).",
        "content": content,
        "category": category,
        "department": DEPARTMENT_BY_CATEGORY.get(category, DEFAULT_DEPARTMENT),
        "sourceDocuments": [],
        "expert": name,
        "createdAt": created,
        "updatedAt": updated,
        "validFrom": created,
        "validUntil": None,
        "status": "aktuell",
        "verification": "verifiziert",
        "versions": [{"version": "1.0", "date": updated, "author": name,
                      "change": "Aus KI-Interview übernommen (expertenbestätigt)"}],
        "tags": ["neu", "Interview", *[u.title for u in units[:4]]],
        "origin": "interview",
        "facts": _facts_out(tp),
        "targetRoles": [role],
        "interviewId": iv.id,
    }


@router.get("/learning/articles")
def list_learning_articles(interviewId: str | None = Query(default=None)):
    with session_scope() as db:
        stmt = select(Interview).order_by(Interview.created_at.desc())
        if interviewId:
            stmt = stmt.where(Interview.id == interviewId)
        out = []
        for iv in db.execute(stmt).scalars().all():
            units = sorted((u for u in iv.units if u.review_status in ARTICLE_UNIT_STATUSES), key=lambda u: u.id)
            if not units:
                continue
            tp = db.execute(
                select(TrainingPackage).where(TrainingPackage.interview_id == iv.id)
                .order_by(TrainingPackage.created_at.desc()).limit(1)
            ).scalar_one_or_none()
            out.append(_article_out(iv, units, tp))
        return out


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

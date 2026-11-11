from __future__ import annotations

from datetime import datetime, timezone

from ..models import CONFIRMED_STATUSES, DIMENSION_LABELS, Employee, Interview, KnowledgeUnit, Turn
from . import coverage as cov

UNIT_FIELDS = [
    "topic_id", "topic", "category", "title", "content", "explanation", "prerequisites", "process_steps",
    "decision_logic", "exceptions", "examples", "risks", "dependencies", "escalation_paths", "common_mistakes",
    "historical_changes", "validity_note", "source_refs", "confidence", "confidence_reason", "uncertainties",
    "expert_notes", "review_status",
]
EDITABLE_FIELDS = [
    "title", "content", "explanation", "prerequisites", "process_steps", "decision_logic", "exceptions",
    "examples", "risks", "dependencies", "escalation_paths", "common_mistakes", "historical_changes",
    "validity_note", "category",
]


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def unit_snapshot(u: KnowledgeUnit) -> dict:
    return {f: getattr(u, f) for f in UNIT_FIELDS}


def elapsed_seconds(iv: Interview) -> float:
    total = iv.active_seconds or 0.0
    if iv.status == "in_progress" and iv.last_resumed_at:
        lr = iv.last_resumed_at if iv.last_resumed_at.tzinfo else iv.last_resumed_at.replace(tzinfo=timezone.utc)
        total += (datetime.now(timezone.utc) - lr).total_seconds()
    return total


def _progress(iv: Interview) -> dict:
    from ..agents.intelligence import progress, question_budget

    topics = (iv.plan or {}).get("topics", [])
    p = progress(iv)
    asked = sum(1 for t in iv.turns if t.speaker == "ai")
    budget = question_budget(iv)
    remaining = p.get("remaining_estimate")
    remaining = max(0, budget - asked) if remaining is None else min(remaining, max(0, budget - asked))
    done = sum(1 for t in topics if t.get("status") in ("saturated", "limit_reached"))
    return {
        "questions_asked": asked,
        "question_budget": budget,
        "remaining_estimate": remaining,
        "topics_done": done,
        "topics_total": len(topics),
        "recent_gains": [g["gain"] for g in p.get("gains", [])[-6:]],
    }


def employee_out(e: Employee, with_stats: bool = False) -> dict:
    d = {
        "id": e.id, "name": e.name, "department": e.department, "role": e.role,
        "expertise_area": e.expertise_area, "years_experience": e.years_experience,
        "responsibilities": e.responsibilities or [], "specializations": e.specializations or [],
        "created_at": iso(e.created_at),
    }
    if with_stats:
        ivs = e.interviews
        units = [u for iv in ivs for u in iv.units if u.review_status != "rejected"]
        d["interview_count"] = len(ivs)
        d["unit_count"] = len(units)
        d["confirmed_count"] = sum(1 for u in units if u.review_status in CONFIRMED_STATUSES)
        scores = [cov.overall_score(iv.coverage or {}, (iv.plan or {}).get("topics", [])) for iv in ivs if iv.coverage]
        d["coverage_score"] = round(sum(scores) / len(scores), 3) if scores else 0.0
    return d


def turn_out(t: Turn) -> dict:
    return {
        "id": t.id, "seq": t.seq, "speaker": t.speaker, "text": t.text, "technique": t.technique,
        "topic_id": t.topic_id, "rationale": t.rationale, "input_mode": t.input_mode, "redactions": t.redactions,
        "t_start": round(t.t_start, 1), "t_end": round(t.t_end, 1), "created_at": iso(t.created_at),
    }


def unit_out(u: KnowledgeUnit, with_history: bool = False) -> dict:
    d = {"id": u.id, "interview_id": u.interview_id, "version": u.version, **unit_snapshot(u),
         "reviewed_by": u.reviewed_by, "reviewed_at": iso(u.reviewed_at),
         "created_at": iso(u.created_at), "updated_at": iso(u.updated_at)}
    if with_history:
        d["original_extraction"] = u.original_extraction
        d["revisions"] = [
            {"version": r.version, "action": r.action, "actor": r.actor, "comment": r.comment,
             "at": iso(r.created_at), "snapshot": r.snapshot}
            for r in u.revisions
        ]
    return d


def interview_out(iv: Interview, full: bool = False) -> dict:
    topics = (iv.plan or {}).get("topics", [])
    coverage = iv.coverage or {}
    units = [u for u in iv.units if u.review_status != "rejected"]
    d = {
        "id": iv.id, "employee_id": iv.employee_id, "employee_name": iv.employee.name if iv.employee else None,
        "title": iv.title, "goal": iv.goal, "planned_minutes": iv.planned_minutes, "status": iv.status,
        "phase": iv.phase, "current_topic_id": iv.current_topic_id, "current_question": iv.current_question,
        "current_technique": iv.current_technique, "consent_given_at": iso(iv.consent_given_at),
        "consent_version": iv.consent_version, "audio_retention": iv.audio_retention,
        "started_at": iso(iv.started_at), "completed_at": iso(iv.completed_at),
        "last_activity_at": iso(iv.last_activity_at), "elapsed_seconds": round(elapsed_seconds(iv), 1),
        "created_at": iso(iv.created_at),
        "coverage_score": cov.overall_score(coverage, topics),
        "unit_count": len(units),
        "confirmed_count": sum(1 for u in units if u.review_status in CONFIRMED_STATUSES),
        "open_gap_count": sum(1 for g in iv.gaps if g.status == "open"),
        "open_contradiction_count": sum(1 for c in iv.contradictions if c.status == "open"),
        "progress": _progress(iv),
    }
    if full:
        d.update(
            {
                "plan": iv.plan or {},
                "coverage": coverage,
                "dimension_labels": DIMENSION_LABELS,
                "dimension_scores": cov.dimension_summary(coverage),
                "topic_scores": {tid: round(cov.topic_score(dims), 3) for tid, dims in coverage.items()},
                "turns": [turn_out(t) for t in iv.turns],
                "units": [unit_out(u) for u in sorted(iv.units, key=lambda x: x.created_at)],
                "gaps": [
                    {"id": g.id, "topic_id": g.topic_id, "dimension": g.dimension, "description": g.description,
                     "status": g.status} for g in sorted(iv.gaps, key=lambda x: x.created_at)
                ],
                "contradictions": [
                    {"id": c.id, "description": c.description, "statement_a": c.statement_a,
                     "statement_b": c.statement_b, "turn_ids": c.turn_ids, "status": c.status,
                     "resolution": c.resolution} for c in iv.contradictions
                ],
                "summary": iv.summary,
            }
        )
    return d

"""Knowledge Coverage Engine.

Coverage misst, wie vollständig ein Thema *besprochen* wurde – nicht, ob die
Inhalte fachlich wahr sind. `expert_confirmed` wird ausschließlich aus dem
menschlichen Review abgeleitet und kann vom LLM nicht gesetzt werden.
"""
from __future__ import annotations

from ..models import CONFIRMED_STATUSES, DIMENSIONS

SCORE = {"not_started": 0.0, "needs_clarification": 0.25, "partially_covered": 0.5, "covered": 0.85, "expert_confirmed": 1.0}
PRIORITY_WEIGHT = {"hoch": 3, "mittel": 2, "niedrig": 1}
AI_ALLOWED = {"not_started", "partially_covered", "needs_clarification", "covered"}
# Reihenfolge, damit ein späterer schwächerer KI-Status einen stärkeren nicht stillschweigend überschreibt –
# außer "needs_clarification", das ist ein bewusstes Signal (z.B. Widerspruch).
_RANK = {"not_started": 0, "partially_covered": 1, "covered": 2}


def empty_topic() -> dict:
    return {d: {"status": "not_started", "evidence": ""} for d in DIMENSIONS}


def ensure_topics(coverage: dict, topic_ids: list[str]) -> dict:
    cov = {k: dict(v) for k, v in (coverage or {}).items()}
    for tid in topic_ids:
        cov.setdefault(tid, empty_topic())
    return cov


def apply_ai_update(coverage: dict, topic_id: str, dimension: str, status: str, evidence: str) -> dict:
    if status not in AI_ALLOWED or dimension == "validierung" or dimension not in DIMENSIONS:
        return coverage  # Guardrail: LLM darf keine Validierung setzen
    cov = {k: {d: dict(v) for d, v in dims.items()} for k, dims in coverage.items()}
    topic = cov.setdefault(topic_id, empty_topic())
    current = topic.get(dimension, {"status": "not_started"})["status"]
    if current == "expert_confirmed":
        return cov
    if status == "needs_clarification" or current == "needs_clarification" or _RANK.get(status, 0) >= _RANK.get(current, 0):
        topic[dimension] = {"status": status, "evidence": evidence[:500]}
    return cov


def derive_with_review(coverage: dict, units) -> dict:
    """Leitet Validierungsstatus und expert_confirmed aus Review-Ergebnissen ab."""
    cov = {k: {d: dict(v) for d, v in dims.items()} for k, dims in (coverage or {}).items()}
    by_topic: dict[str, list] = {}
    for u in units:
        if u.review_status == "rejected":
            continue
        by_topic.setdefault(u.topic_id or "_", []).append(u)
    for tid, dims in cov.items():
        tus = by_topic.get(tid, [])
        confirmed = [u for u in tus if u.review_status in CONFIRMED_STATUSES]
        if not tus or not confirmed:
            val = "not_started"
        elif len(confirmed) == len(tus):
            val = "expert_confirmed"
        else:
            val = "partially_covered"
        dims["validierung"] = {"status": val, "evidence": f"{len(confirmed)}/{len(tus)} Einheiten bestätigt"}
        if val == "expert_confirmed":
            for d in DIMENSIONS:
                if d != "validierung" and dims.get(d, {}).get("status") == "covered":
                    dims[d] = {**dims[d], "status": "expert_confirmed"}
    return cov


def topic_score(dims: dict) -> float:
    vals = [SCORE.get(dims.get(d, {}).get("status", "not_started"), 0.0) for d in DIMENSIONS]
    return sum(vals) / len(vals)


def overall_score(coverage: dict, topics: list[dict]) -> float:
    if not coverage:
        return 0.0
    prio = {t["id"]: PRIORITY_WEIGHT.get(t.get("priority", "mittel"), 2) for t in topics}
    total = weight = 0.0
    for tid, dims in coverage.items():
        w = prio.get(tid, 1)
        total += topic_score(dims) * w
        weight += w
    return round(total / weight, 3) if weight else 0.0


def dimension_summary(coverage: dict) -> dict:
    out = {}
    for d in DIMENSIONS:
        vals = [SCORE.get(dims.get(d, {}).get("status", "not_started"), 0.0) for dims in coverage.values()]
        out[d] = round(sum(vals) / len(vals), 3) if vals else 0.0
    return out

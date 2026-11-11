"""Übergabevertrag an das RAG-Team (Phase 2).

Vertrag: `ekep.knowledge-export`, semantische Versionierung.
  * MAJOR – inkompatible Änderung (Feld entfernt/umbenannt, Semantik geändert)
  * MINOR – rückwärtskompatible Ergänzung (neues optionales Feld)
  * PATCH – Dokumentation/Fehlerkorrektur ohne Strukturänderung
Das JSON-Schema wird direkt aus diesen Pydantic-Modellen generiert
(GET /api/v1/export/schema) – Code und Vertrag können nicht auseinanderlaufen.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from ..config import settings
from ..models import CONFIRMED_STATUSES, Interview
from .serialize import iso

SCHEMA_NAME = "ekep.knowledge-export"
SCHEMA_VERSION = "1.1.0"  # 1.1.0: + vector_sync (Abgleich mit Wissensbasis, additiv)


class SourceRef(BaseModel):
    turn_id: str
    quote: str = Field(description="Wörtliches Zitat aus dem Transkript")
    transcript_offset_start_s: float | None = Field(description="Sekunden seit Interviewbeginn (aktive Zeit)")
    transcript_offset_end_s: float | None
    verified: bool = Field(description="Zitat wurde deterministisch im Transkript wiedergefunden")


class Revision(BaseModel):
    version: int
    action: str
    actor: str
    at: str | None
    comment: str | None


class Provenance(BaseModel):
    source_refs: list[SourceRef]
    original_extraction: dict = Field(description="Unveränderte KI-Erstextraktion")
    revisions: list[Revision]


class Confidence(BaseModel):
    level: Literal["niedrig", "mittel", "hoch"]
    reason: str
    semantics: Literal["statement_clarity_not_truth"] = Field(
        default="statement_clarity_not_truth",
        description="Beschreibt Klarheit der Expertenaussage, NICHT fachliche Richtigkeit",
    )


class Review(BaseModel):
    status: Literal["extracted", "confirmed", "corrected", "uncertain", "rejected"]
    expert_confirmed: bool
    reviewed_by: str | None
    reviewed_at: str | None
    expert_notes: list[str]


class TemporalValidity(BaseModel):
    note: str | None
    historical_changes: list[str]


class VectorSync(BaseModel):
    """Empfehlung für die Vektordatenbank aus dem Abgleich mit der bestehenden Wissensbasis."""

    relation: Literal["neu", "ergaenzung", "bestaetigung", "widerspruch"]
    action: Literal["create", "update", "none", "review"] = Field(
        description="create = neuen Eintrag anlegen; update = target_id ergänzen/anpassen; none = bereits vorhanden; "
                    "review = Widerspruch, fachliche Klärung vor Übernahme"
    )
    target_id: str | None = Field(description="ID des bestehenden Eintrags in der Wissensbasis")
    target_title: str | None
    similarity: float | None
    explanation: str
    method: str = Field(description="llm | heuristic | no_match")


class KnowledgeUnitExport(BaseModel):
    knowledge_id: str
    version: int
    topic: str
    topic_id: str | None
    category: Literal["fachwissen", "prozesswissen", "entscheidungswissen", "erfahrungswissen", "kontextwissen"]
    title: str
    content: str
    explanation: str
    prerequisites: list[str]
    process_steps: list[str]
    decision_logic: str | None
    exceptions: list[str]
    examples: list[str]
    risks: list[str]
    dependencies: list[str]
    escalation_paths: list[str]
    common_mistakes: list[str]
    temporal_validity: TemporalValidity
    confidence: Confidence
    uncertainties: list[str]
    review: Review
    provenance: Provenance
    vector_sync: VectorSync | None = Field(default=None, description="Seit 1.1.0; null = noch kein Abgleich erfolgt")


class ExpertRef(BaseModel):
    employee_id: str
    name: str | None = Field(description="Nur bei EXPORT_INCLUDE_EXPERT_NAME=true (Datenminimierung)")
    department: str
    role: str
    expertise_area: str


class SourceInfo(BaseModel):
    system: str
    interview_id: str
    interview_title: str
    interview_goal: str
    started_at: str | None
    completed_at: str | None
    consent_version: str | None


class OpenItem(BaseModel):
    id: str
    topic_id: str | None
    description: str


class ContradictionExport(BaseModel):
    id: str
    description: str
    statement_a: str
    statement_b: str
    turn_ids: list[str]
    status: str


class TranscriptTurn(BaseModel):
    turn_id: str
    seq: int
    speaker: Literal["ai", "expert"]
    text: str
    t_start_s: float
    t_end_s: float


class KnowledgeExport(BaseModel):
    schema_name: Literal["ekep.knowledge-export"] = Field(alias="schema", default=SCHEMA_NAME)
    schema_version: str
    export_id: str
    generated_at: str
    source: SourceInfo
    expert: ExpertRef
    filter: dict = Field(description="Angewandte Exportfilter")
    knowledge_units: list[KnowledgeUnitExport]
    open_questions: list[OpenItem]
    contradictions: list[ContradictionExport]
    transcript: list[TranscriptTurn] | None

    model_config = {"populate_by_name": True}


def build_export(iv: Interview, *, include_unconfirmed: bool = False, include_transcript: bool = False) -> dict:
    allowed = set(CONFIRMED_STATUSES) | ({"extracted", "uncertain"} if include_unconfirmed else set())
    from sqlalchemy.orm import object_session

    from .knowledge_search import reconciliations_for

    sess = object_session(iv)
    recs = reconciliations_for(sess, iv.id) if sess is not None else {}
    units = []
    for u in sorted(iv.units, key=lambda x: x.created_at):
        if u.review_status not in allowed:
            continue
        units.append(
            KnowledgeUnitExport(
                knowledge_id=u.id, version=u.version, topic=u.topic, topic_id=u.topic_id, category=u.category,
                title=u.title, content=u.content, explanation=u.explanation or "", prerequisites=u.prerequisites or [],
                process_steps=u.process_steps or [], decision_logic=u.decision_logic, exceptions=u.exceptions or [],
                examples=u.examples or [], risks=u.risks or [], dependencies=u.dependencies or [],
                escalation_paths=u.escalation_paths or [], common_mistakes=u.common_mistakes or [],
                temporal_validity=TemporalValidity(note=u.validity_note, historical_changes=u.historical_changes or []),
                confidence=Confidence(level=u.confidence, reason=u.confidence_reason or ""),
                uncertainties=u.uncertainties or [],
                review=Review(status=u.review_status, expert_confirmed=u.review_status in CONFIRMED_STATUSES,
                              reviewed_by=u.reviewed_by, reviewed_at=iso(u.reviewed_at), expert_notes=u.expert_notes or []),
                provenance=Provenance(
                    source_refs=[SourceRef(turn_id=r["turn_id"], quote=r["quote"],
                                           transcript_offset_start_s=r.get("t_start"),
                                           transcript_offset_end_s=r.get("t_end"), verified=bool(r.get("verified")))
                                 for r in (u.source_refs or [])],
                    original_extraction=u.original_extraction or {},
                    revisions=[Revision(version=r.version, action=r.action, actor=r.actor, at=iso(r.created_at),
                                        comment=r.comment) for r in u.revisions],
                ),
                vector_sync=VectorSync(relation=recs[u.id]["relation"], action=recs[u.id]["action"],
                                       target_id=recs[u.id]["target_id"], target_title=recs[u.id]["target_title"],
                                       similarity=recs[u.id]["score"], explanation=recs[u.id]["explanation"],
                                       method=recs[u.id]["method"]) if u.id in recs else None,
            )
        )
    emp = iv.employee
    export = KnowledgeExport(
        schema_version=SCHEMA_VERSION,
        export_id=f"exp_{secrets.token_hex(6)}",
        generated_at=datetime.now(timezone.utc).isoformat(),
        source=SourceInfo(system="Expert Knowledge Extraction Platform", interview_id=iv.id, interview_title=iv.title,
                          interview_goal=iv.goal, started_at=iso(iv.started_at), completed_at=iso(iv.completed_at),
                          consent_version=iv.consent_version),
        expert=ExpertRef(employee_id=emp.id, name=emp.name if settings.export_include_expert_name else None,
                         department=emp.department, role=emp.role, expertise_area=emp.expertise_area),
        filter={"review_statuses": sorted(allowed), "include_transcript": include_transcript},
        knowledge_units=units,
        open_questions=[OpenItem(id=g.id, topic_id=g.topic_id, description=g.description)
                        for g in iv.gaps if g.status == "open"],
        contradictions=[ContradictionExport(id=c.id, description=c.description, statement_a=c.statement_a,
                                            statement_b=c.statement_b, turn_ids=c.turn_ids or [], status=c.status)
                        for c in iv.contradictions],
        transcript=[TranscriptTurn(turn_id=t.id, seq=t.seq, speaker=t.speaker, text=t.text, t_start_s=round(t.t_start, 1),
                                   t_end_s=round(t.t_end, 1)) for t in iv.turns] if include_transcript else None,
    )
    return export.model_dump(by_alias=True)


def export_json_schema() -> dict:
    schema = KnowledgeExport.model_json_schema(by_alias=True)
    schema["$id"] = f"urn:helaba:lbs:{SCHEMA_NAME}:{SCHEMA_VERSION}"
    schema["title"] = f"{SCHEMA_NAME} v{SCHEMA_VERSION}"
    return schema

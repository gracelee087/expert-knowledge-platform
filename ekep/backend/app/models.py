"""Persistentes Datenmodell.

JSON-Spalten halten listenartige Felder (Prozessschritte, Ausnahmen …). Sie
funktionieren identisch auf PostgreSQL (JSONB-kompatibel) und SQLite.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, new_id, utcnow

# --- Fachliche Konstanten -------------------------------------------------

DIMENSIONS = [
    "fachlich",           # Fachliche Vollständigkeit
    "prozess",            # Prozessverständnis
    "entscheidung",       # Entscheidungslogik
    "ausnahmen",          # Ausnahmebehandlung
    "praxisbeispiele",    # Praxisbeispiele
    "risiken",            # Risikoverständnis
    "kontext",            # Kontextverständnis
    "verstaendlichkeit",  # Verständlichkeit
    "validierung",        # Validierungsstatus (nur durch Review)
]
DIMENSION_LABELS = {
    "fachlich": "Fachliche Vollständigkeit",
    "prozess": "Prozessverständnis",
    "entscheidung": "Entscheidungslogik",
    "ausnahmen": "Ausnahmebehandlung",
    "praxisbeispiele": "Praxisbeispiele",
    "risiken": "Risikoverständnis",
    "kontext": "Kontextverständnis",
    "verstaendlichkeit": "Verständlichkeit",
    "validierung": "Validierungsstatus",
}
COVERAGE_STATUSES = ["not_started", "partially_covered", "needs_clarification", "covered", "expert_confirmed"]
CATEGORIES = ["fachwissen", "prozesswissen", "entscheidungswissen", "erfahrungswissen", "kontextwissen"]
REVIEW_STATUSES = ["extracted", "confirmed", "corrected", "uncertain", "rejected"]
CONFIRMED_STATUSES = {"confirmed", "corrected"}

INTERVIEW_STATUSES = ["planned", "in_progress", "paused", "completed", "in_review", "approved"]
INTERVIEW_PHASES = ["preparation", "opening", "exploration", "validation", "closing", "done"]


class Employee(Base):
    __tablename__ = "employees"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("emp"))
    name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(200))
    expertise_area: Mapped[str] = mapped_column(String(300))
    years_experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    responsibilities: Mapped[list] = mapped_column(JSON, default=list)
    specializations: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    interviews: Mapped[list["Interview"]] = relationship(back_populates="employee", cascade="all, delete-orphan")


class Interview(Base):
    __tablename__ = "interviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("int"))
    employee_id: Mapped[str] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    goal: Mapped[str] = mapped_column(Text)
    planned_minutes: Mapped[int] = mapped_column(Integer, default=45)
    status: Mapped[str] = mapped_column(String(20), default="planned")
    phase: Mapped[str] = mapped_column(String(20), default="preparation")

    plan: Mapped[dict] = mapped_column(JSON, default=dict)        # {strategy, topics:[...]}
    coverage: Mapped[dict] = mapped_column(JSON, default=dict)    # {topic_id: {dimension: {status, evidence}}}
    current_topic_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    current_question: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_technique: Mapped[str | None] = mapped_column(String(40), nullable=True)
    followup_depth: Mapped[int] = mapped_column(Integer, default=0)

    consent_given_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consent_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    audio_retention: Mapped[str] = mapped_column(String(10), default="none")

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    employee: Mapped[Employee] = relationship(back_populates="interviews")
    turns: Mapped[list["Turn"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan", order_by="Turn.seq"
    )
    units: Mapped[list["KnowledgeUnit"]] = relationship(back_populates="interview", cascade="all, delete-orphan")
    gaps: Mapped[list["Gap"]] = relationship(cascade="all, delete-orphan")
    contradictions: Mapped[list["Contradiction"]] = relationship(cascade="all, delete-orphan")
    trainings: Mapped[list["TrainingPackage"]] = relationship(cascade="all, delete-orphan")


class Turn(Base):
    """Eine Gesprächsäußerung. t_start/t_end = Sekunden seit Interviewbeginn (aktive Zeit)."""

    __tablename__ = "turns"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("turn"))
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    speaker: Mapped[str] = mapped_column(String(10))  # ai | expert
    text: Mapped[str] = mapped_column(Text)
    technique: Mapped[str | None] = mapped_column(String(40), nullable=True)
    topic_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)  # voice | text
    redactions: Mapped[int] = mapped_column(Integer, default=0)
    t_start: Mapped[float] = mapped_column(Float, default=0.0)
    t_end: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    interview: Mapped[Interview] = relationship(back_populates="turns")


class KnowledgeUnit(Base):
    __tablename__ = "knowledge_units"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # KU-xxxxxx
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    topic: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text, default="")
    prerequisites: Mapped[list] = mapped_column(JSON, default=list)
    process_steps: Mapped[list] = mapped_column(JSON, default=list)
    decision_logic: Mapped[str | None] = mapped_column(Text, nullable=True)
    exceptions: Mapped[list] = mapped_column(JSON, default=list)
    examples: Mapped[list] = mapped_column(JSON, default=list)
    risks: Mapped[list] = mapped_column(JSON, default=list)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    escalation_paths: Mapped[list] = mapped_column(JSON, default=list)
    common_mistakes: Mapped[list] = mapped_column(JSON, default=list)
    historical_changes: Mapped[list] = mapped_column(JSON, default=list)
    validity_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_refs: Mapped[list] = mapped_column(JSON, default=list)  # [{turn_id, quote, t_start, t_end, verified}]
    confidence: Mapped[str] = mapped_column(String(10), default="mittel")
    confidence_reason: Mapped[str] = mapped_column(Text, default="")
    uncertainties: Mapped[list] = mapped_column(JSON, default=list)
    expert_notes: Mapped[list] = mapped_column(JSON, default=list)
    review_status: Mapped[str] = mapped_column(String(20), default="extracted")
    reviewed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    original_extraction: Mapped[dict] = mapped_column(JSON, default=dict)  # unveränderlich
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    interview: Mapped[Interview] = relationship(back_populates="units")
    revisions: Mapped[list["KnowledgeRevision"]] = relationship(
        cascade="all, delete-orphan", order_by="KnowledgeRevision.version"
    )


class KnowledgeRevision(Base):
    """Append-only Historie jeder Änderung an einer Wissenseinheit."""

    __tablename__ = "knowledge_revisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("rev"))
    unit_id: Mapped[str] = mapped_column(ForeignKey("knowledge_units.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(30))  # extracted | ai_update | confirm | correct | ...
    actor: Mapped[str] = mapped_column(String(200))
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Gap(Base):
    __tablename__ = "gaps"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("gap"))
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    topic_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dimension: Mapped[str | None] = mapped_column(String(30), nullable=True)
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | resolved | dismissed
    created_turn_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolved_turn_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Contradiction(Base):
    __tablename__ = "contradictions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("con"))
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    statement_a: Mapped[str] = mapped_column(Text)
    statement_b: Mapped[str] = mapped_column(Text)
    turn_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | clarified | dismissed
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrainingPackage(Base):
    __tablename__ = "training_packages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("trn"))
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    audience: Mapped[str] = mapped_column(String(20))
    content: Mapped[dict] = mapped_column(JSON)
    source_unit_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------
# Wissensabgleich mit bestehender Wissensbasis (Vektorsuche, Phase-2-Anbindung)
# --------------------------------------------------------------------------
class KnowledgeSearch(Base):
    """Ergebnis einer semantischen Suche zu einer Expertenantwort (läuft parallel zum Interview)."""

    __tablename__ = "knowledge_searches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("ks"))
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[str] = mapped_column(String(32), index=True)
    query: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(20))       # local | http
    mode: Mapped[str] = mapped_column(String(20))           # semantic | lexical
    status: Mapped[str] = mapped_column(String(10))         # ok | error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    hits: Mapped[list] = mapped_column(JSON, default=list)  # [{id,title,snippet,score,source,metadata}]
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UnitReconciliation(Base):
    """Abgleich einer Wissenseinheit mit der Wissensbasis: neu / Ergänzung / bekannt / Widerspruch."""

    __tablename__ = "unit_reconciliations"
    unit_id: Mapped[str] = mapped_column(ForeignKey("knowledge_units.id", ondelete="CASCADE"), primary_key=True)
    interview_id: Mapped[str] = mapped_column(ForeignKey("interviews.id", ondelete="CASCADE"), index=True)
    relation: Mapped[str] = mapped_column(String(20))       # neu | ergaenzung | bestaetigung | widerspruch
    action: Mapped[str] = mapped_column(String(10))         # create | update | none | review
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    target_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation: Mapped[str] = mapped_column(Text, default="")
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    method: Mapped[str] = mapped_column(String(20), default="llm")  # llm | heuristic
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

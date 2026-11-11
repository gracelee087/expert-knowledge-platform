"""Strukturierte LLM-Ausgaben.

Alle Felder sind Pflichtfelder ohne Defaults – Voraussetzung für OpenAI
Structured Outputs im Strict-Modus. Optionalität wird über `| None` abgebildet.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Dimension = Literal[
    "fachlich", "prozess", "entscheidung", "ausnahmen", "praxisbeispiele",
    "risiken", "kontext", "verstaendlichkeit",
]
AICoverageStatus = Literal["not_started", "partially_covered", "needs_clarification", "covered"]
Category = Literal["fachwissen", "prozesswissen", "entscheidungswissen", "erfahrungswissen", "kontextwissen"]
Technique = Literal[
    "clarification", "deep_dive", "decision_logic", "exception_handling", "practical_experience",
    "risk_failure", "tacit_knowledge", "validation", "transition", "opening", "closing",
]


# ---------- Planung ----------
class PlannedTopic(BaseModel):
    id: str = Field(description="Kurze ID, z.B. T1, T2 …")
    title: str
    description: str = Field(description="Was in diesem Themenblock geklärt werden soll")
    rationale: str = Field(description="Warum dieses Thema für den Wissenserhalt relevant ist")
    priority: Literal["hoch", "mittel", "niedrig"]
    focus_dimensions: list[Dimension]
    opening_question: str
    tacit_knowledge_probes: list[str] = Field(
        description="Fragen, mit denen implizites Wissen gehoben werden kann – als Fragen formuliert, keine Fakten"
    )


class InterviewPlan(BaseModel):
    strategy: str
    warmup_question: str
    topics: list[PlannedTopic]


# ---------- Interviewer (Fast Path) ----------
class InterviewerDecision(BaseModel):
    action: Literal["follow_up", "switch_topic", "validate", "close"]
    technique: Technique
    topic_id: str = Field(description="ID des Themas, auf das sich die Frage bezieht")
    acknowledgement: str = Field(description="Kurze, aktive Spiegelung der letzten Antwort (max. 1 Satz) oder leer")
    question: str = Field(description="Genau eine gesprochene Frage, max. 2 Sätze")
    rationale: str = Field(description="Warum diese Frage jetzt den größten Erkenntnisgewinn bringt")
    current_topic_saturated: bool
    expert_reached_knowledge_limit: bool
    last_answer_information_gain: Literal["hoch", "mittel", "niedrig"] = Field(
        description="Wie viel NEUES, konkretes Wissen die letzte Expertenantwort gebracht hat (Wiederholung/Allgemeines = niedrig)"
    )
    estimated_remaining_questions: int = Field(
        description="Realistische Schätzung, wie viele Fragen insgesamt noch sinnvoll sind (0 = abschließen)"
    )


# ---------- Analyst (Background) ----------
class SourceQuote(BaseModel):
    turn_id: str
    quote: str = Field(description="Wörtliches Zitat aus der Expertenaussage (exakt kopiert)")


class ExtractedUnit(BaseModel):
    existing_unit_id: str | None = Field(description="ID einer bestehenden Einheit, die ergänzt wird, sonst null")
    topic_id: str
    category: Category
    title: str
    content: str = Field(description="Kernaussage in 1-3 Sätzen, nur was der Experte gesagt hat")
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
    historical_changes: list[str]
    validity_note: str | None = Field(description="Zeitliche Gültigkeit, nur wenn vom Experten genannt")
    source_quotes: list[SourceQuote]
    confidence: Literal["niedrig", "mittel", "hoch"] = Field(
        description="Wie eindeutig und konkret der Experte es ausgesagt hat – NICHT fachliche Richtigkeit"
    )
    confidence_reason: str
    uncertainties: list[str]


class CoverageUpdate(BaseModel):
    topic_id: str
    dimension: Dimension
    status: AICoverageStatus
    evidence: str


class GapItem(BaseModel):
    topic_id: str
    dimension: Dimension
    description: str


class ContradictionItem(BaseModel):
    description: str
    statement_a: str
    statement_b: str
    turn_ids: list[str]


class NewTopic(BaseModel):
    title: str
    description: str


class AnalysisResult(BaseModel):
    units: list[ExtractedUnit]
    coverage_updates: list[CoverageUpdate]
    new_gaps: list[GapItem]
    resolved_gap_ids: list[str]
    contradictions: list[ContradictionItem]
    new_topics: list[NewTopic]


# ---------- Abschluss ----------
class TopicSummary(BaseModel):
    topic_id: str
    title: str
    summary: str


class InterviewSummary(BaseModel):
    executive_summary: str
    key_findings: list[str]
    topics: list[TopicSummary]
    open_questions: list[str]
    recommended_followups: list[str]
    quality_notes: list[str]


# ---------- Training (Phase-3-Vorschau, nur Demo) ----------
class Slide(BaseModel):
    title: str
    bullets: list[str]
    speaker_notes: str
    source_unit_ids: list[str]


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    correct_index: int
    explanation: str
    source_unit_ids: list[str]


class TrainingContent(BaseModel):
    title: str
    audience: Literal["einsteiger", "fortgeschritten", "experten"]
    learning_objectives: list[str]
    slides: list[Slide]
    quiz: list[QuizQuestion]


# ---------- Abgleich mit bestehender Wissensbasis ----------
class UnitRelation(BaseModel):
    unit_id: str
    relation: Literal["neu", "ergaenzung", "bestaetigung", "widerspruch"] = Field(
        description="neu = nicht vorhanden; ergaenzung = erweitert/präzisiert einen Treffer; "
                    "bestaetigung = inhaltlich bereits vorhanden; widerspruch = weicht inhaltlich von einem Treffer ab"
    )
    matched_id: str | None = Field(description="ID des relevantesten Treffers, null bei 'neu'")
    explanation: str = Field(description="1-2 Sätze: was ist neu bzw. worin liegt die Ergänzung/Abweichung")


class ReconciliationResult(BaseModel):
    items: list[UnitRelation]

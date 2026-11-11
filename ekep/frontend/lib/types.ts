export type CoverageStatus = "not_started" | "partially_covered" | "needs_clarification" | "covered" | "expert_confirmed";
export type ReviewStatus = "extracted" | "confirmed" | "corrected" | "uncertain" | "rejected";

export interface Employee {
  id: string;
  name: string;
  department: string;
  role: string;
  expertise_area: string;
  years_experience: number | null;
  responsibilities: string[];
  specializations: string[];
  interview_count?: number;
  unit_count?: number;
  confirmed_count?: number;
  coverage_score?: number;
  interviews?: InterviewSummaryRow[];
}

export interface Topic {
  id: string;
  title: string;
  description: string;
  rationale?: string;
  priority: "hoch" | "mittel" | "niedrig";
  focus_dimensions: string[];
  opening_question: string;
  tacit_knowledge_probes: string[];
  status: "open" | "in_progress" | "saturated" | "limit_reached";
  origin?: "plan" | "emerged";
}

export interface Turn {
  id: string;
  seq: number;
  speaker: "ai" | "expert";
  text: string;
  technique: string | null;
  topic_id: string | null;
  rationale: string | null;
  input_mode: string | null;
  redactions: number;
  t_start: number;
  t_end: number;
}

export interface SourceRef {
  turn_id: string;
  quote: string;
  t_start: number | null;
  t_end: number | null;
  verified: boolean;
  match_score?: number;
}

export interface Revision {
  version: number;
  action: string;
  actor: string;
  comment: string | null;
  at: string;
  snapshot: Record<string, unknown>;
}

export interface KnowledgeUnit {
  id: string;
  version: number;
  topic_id: string | null;
  topic: string;
  category: string;
  title: string;
  content: string;
  explanation: string;
  prerequisites: string[];
  process_steps: string[];
  decision_logic: string | null;
  exceptions: string[];
  examples: string[];
  risks: string[];
  dependencies: string[];
  escalation_paths: string[];
  common_mistakes: string[];
  historical_changes: string[];
  validity_note: string | null;
  source_refs: SourceRef[];
  confidence: "niedrig" | "mittel" | "hoch";
  confidence_reason: string;
  uncertainties: string[];
  expert_notes: string[];
  review_status: ReviewStatus;
  reviewed_by: string | null;
  reviewed_at: string | null;
  original_extraction?: Record<string, unknown>;
  revisions?: Revision[];
}

export interface InterviewSummaryRow {
  id: string;
  employee_id: string;
  employee_name: string | null;
  title: string;
  goal: string;
  planned_minutes: number;
  status: "planned" | "in_progress" | "paused" | "completed" | "in_review" | "approved";
  phase: string;
  current_topic_id: string | null;
  current_question: string | null;
  current_technique: string | null;
  consent_given_at: string | null;
  elapsed_seconds: number;
  coverage_score: number;
  unit_count: number;
  confirmed_count: number;
  open_gap_count: number;
  open_contradiction_count: number;
  progress?: {
    questions_asked: number;
    question_budget: number;
    remaining_estimate: number;
    topics_done: number;
    topics_total: number;
    recent_gains: ("hoch" | "mittel" | "niedrig")[];
  };
  created_at: string;
  last_activity_at: string | null;
}

export interface InterviewState extends InterviewSummaryRow {
  plan: { strategy?: string; warmup_question?: string; topics?: Topic[] };
  coverage: Record<string, Record<string, { status: CoverageStatus; evidence: string }>>;
  dimension_labels: Record<string, string>;
  dimension_scores: Record<string, number>;
  topic_scores: Record<string, number>;
  turns: Turn[];
  units: KnowledgeUnit[];
  gaps: { id: string; topic_id: string | null; dimension: string | null; description: string; status: string }[];
  contradictions: { id: string; description: string; statement_a: string; statement_b: string; status: string; resolution: string | null }[];
  summary: null | {
    executive_summary: string;
    key_findings: string[];
    topics: { topic_id: string; title: string; summary: string }[];
    open_questions: string[];
    recommended_followups: string[];
    quality_notes: string[];
  };
}

export interface TrainingPackage {
  id: string;
  audience: string;
  created_at: string;
  content: {
    title: string;
    audience: string;
    learning_objectives: string[];
    slides: { title: string; bullets: string[]; speaker_notes: string; source_unit_ids: string[] }[];
    quiz: { question: string; options: string[]; correct_index: number; explanation: string; source_unit_ids: string[] }[];
  };
}

export interface SearchHit {
  id: string;
  title: string;
  snippet: string;
  score: number;
  source: string;
  metadata: Record<string, unknown>;
}

export interface KnowledgeSearchResult {
  id: string;
  turn_id: string;
  provider: "local" | "http";
  mode: "semantic" | "lexical";
  status: "ok" | "error";
  error: string | null;
  hits: SearchHit[];
  latency_ms: number;
}

export type Relation = "neu" | "ergaenzung" | "bestaetigung" | "widerspruch";

export interface Reconciliation {
  unit_id: string;
  relation: Relation;
  action: "create" | "update" | "none" | "review";
  target_id: string | null;
  target_title: string | null;
  score: number | null;
  explanation: string;
  candidates: SearchHit[];
  method: string;
}

export interface KnowledgeSearchState {
  provider: string;
  searches: KnowledgeSearchResult[];
  reconciliation: {
    items: Reconciliation[];
    by_unit: Record<string, Reconciliation>;
    summary: { neu: number; ergaenzung: number; bestaetigung: number; widerspruch: number; total: number };
  };
}

export interface KnowledgeBasePublishResult {
  department: string;
  rag_service_url: string;
  published: { unit_id: string; title: string; fact_id: string; delta_type?: Relation | string; kb_verdict?: string; status: "übernommen" | "bereits übernommen" | "fehlgeschlagen" }[];
  skipped: { unit_id: string; title: string; reason: string }[];
}

export interface TrainingVideoResult {
  title: string;
  file: string;
  url: string;
  language: string;
  voice: string;
  audience: string;
  absolute_url: string;
}

"use client";
import type { CoverageStatus, InterviewState } from "@/lib/types";
import type { ReactNode } from "react";

export function PageHeader({ eyebrow, title, subtitle, actions }: { eyebrow?: string; title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
        <h1 className="text-3xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-ink-2">{subtitle}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, accent }: { label: string; value: ReactNode; hint?: string; accent?: boolean }) {
  return (
    <div className="card p-5">
      <div className="text-xs font-medium text-ink-2">{label}</div>
      <div className={`mt-2 text-3xl font-semibold tracking-tight ${accent ? "text-brand" : ""}`}>{value}</div>
      {hint && <div className="mt-1 text-xs text-ink-3">{hint}</div>}
    </div>
  );
}

const STATUS_LABEL: Record<string, string> = {
  planned: "Geplant", in_progress: "Läuft", paused: "Pausiert", completed: "Abgeschlossen",
  in_review: "Im Review", approved: "Freigegeben",
  extracted: "KI-extrahiert", confirmed: "Bestätigt", corrected: "Korrigiert & bestätigt", uncertain: "Unsicher", rejected: "Zurückgewiesen",
  open: "Offen", resolved: "Geklärt", dismissed: "Verworfen", clarified: "Geklärt",
};
const STATUS_TONE: Record<string, string> = {
  planned: "bg-zinc-100 text-zinc-600", in_progress: "bg-blue-50 text-blue-700", paused: "bg-amber-50 text-amber-700",
  completed: "bg-zinc-100 text-zinc-700", in_review: "bg-violet-50 text-violet-700", approved: "bg-emerald-50 text-emerald-700",
  extracted: "bg-zinc-100 text-zinc-600", confirmed: "bg-emerald-50 text-emerald-700", corrected: "bg-emerald-50 text-emerald-700",
  uncertain: "bg-amber-50 text-amber-700", rejected: "bg-red-50 text-red-700", open: "bg-amber-50 text-amber-700",
  resolved: "bg-emerald-50 text-emerald-700", clarified: "bg-emerald-50 text-emerald-700", dismissed: "bg-zinc-100 text-zinc-500",
};

export function Badge({ children, tone = "bg-zinc-100 text-zinc-600", title }: { children: ReactNode; tone?: string; title?: string }) {
  return <span title={title} className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}`}>{children}</span>;
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status]}>{STATUS_LABEL[status] || status}</Badge>;
}

export const CATEGORY_LABEL: Record<string, string> = {
  fachwissen: "Fachwissen", prozesswissen: "Prozesswissen", entscheidungswissen: "Entscheidungswissen",
  erfahrungswissen: "Erfahrungswissen", kontextwissen: "Kontextwissen",
};
export const TECHNIQUE_LABEL: Record<string, string> = {
  clarification: "Klärung", deep_dive: "Deep Dive", decision_logic: "Entscheidungslogik", exception_handling: "Ausnahmen",
  practical_experience: "Praxisfall", risk_failure: "Risiken & Fehler", tacit_knowledge: "Implizites Wissen",
  validation: "Validierung", transition: "Themenwechsel", opening: "Einstieg", closing: "Abschluss",
};

export function ConfidenceBadge({ level, reason }: { level: string; reason?: string }) {
  const tone = level === "hoch" ? "bg-sky-50 text-sky-700" : level === "mittel" ? "bg-zinc-100 text-zinc-600" : "bg-amber-50 text-amber-700";
  return (
    <Badge tone={tone} title={`Aussageklarheit – NICHT fachliche Richtigkeit. ${reason || ""}`}>
      Klarheit: {level}
    </Badge>
  );
}

export function Ring({ value, size = 88, label }: { value: number; size?: number; label?: string }) {
  const r = (size - 10) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} stroke="#ececf0" strokeWidth="8" fill="none" />
        <circle cx={size / 2} cy={size / 2} r={r} stroke="#1d1d1f" strokeWidth="8" fill="none" strokeLinecap="round"
          strokeDasharray={c} strokeDashoffset={c * (1 - Math.min(1, value))} style={{ transition: "stroke-dashoffset .6s ease" }} />
      </svg>
      <div className="absolute text-center">
        <div className="text-lg font-semibold">{Math.round(value * 100)}%</div>
        {label && <div className="text-[10px] text-ink-3">{label}</div>}
      </div>
    </div>
  );
}

export function Bar({ value, tone = "bg-ink" }: { value: number; tone?: string }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-zinc-100">
      <div className={`h-1.5 rounded-full ${tone}`} style={{ width: `${Math.round(Math.min(1, value) * 100)}%`, transition: "width .5s" }} />
    </div>
  );
}

const COV_TONE: Record<CoverageStatus, string> = {
  not_started: "bg-zinc-200", partially_covered: "bg-sky-300", needs_clarification: "bg-amber-400",
  covered: "bg-sky-600", expert_confirmed: "bg-emerald-500",
};
const COV_LABEL: Record<CoverageStatus, string> = {
  not_started: "Nicht begonnen", partially_covered: "Teilweise", needs_clarification: "Klärung nötig",
  covered: "Abgedeckt", expert_confirmed: "Expert confirmed",
};

const SHORT: Record<string, string> = {
  // Kurzlabels für die Matrix
  fachlich: "Fach", prozess: "Prozess", entscheidung: "Entsch.", ausnahmen: "Ausn.", praxisbeispiele: "Praxis",
  risiken: "Risiko", kontext: "Kontext", verstaendlichkeit: "Verst.", validierung: "Valid.",
};

export function CoverageGrid({ iv, compact }: { iv: InterviewState; compact?: boolean }) {
  const topics = iv.plan?.topics || [];
  const dims = Object.keys(iv.dimension_labels || {});
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr>
              <th className="pb-2 text-left font-medium text-ink-3">Thema</th>
              {dims.map((d) => (
                <th key={d} className={`pb-2 ${compact ? "px-0.5 text-[9px]" : "px-1"} text-center font-medium text-ink-3`} title={iv.dimension_labels[d]}>{SHORT[d] || d}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {topics.map((t) => (
              <tr key={t.id} className="border-t border-line/60">
                <td className={`py-2 pr-2 ${compact ? "max-w-[110px]" : "max-w-[260px]"} truncate`} title={t.title}>
                  <span className="text-ink-3 mr-1">{t.id}</span>{t.title}
                </td>
                {dims.map((d) => {
                  const c = iv.coverage?.[t.id]?.[d];
                  const st = (c?.status || "not_started") as CoverageStatus;
                  return (
                    <td key={d} className={`${compact ? "px-0.5" : "px-1"} text-center`}>
                      <span title={`${iv.dimension_labels[d]}: ${COV_LABEL[st]}${c?.evidence ? " – " + c.evidence : ""}`}
                        className={`inline-block h-3 w-3 rounded-full ${COV_TONE[st]}`} />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex flex-wrap gap-3 text-[11px] text-ink-2">
        {(Object.keys(COV_TONE) as CoverageStatus[]).map((s) => (
          <span key={s} className="inline-flex items-center gap-1"><span className={`h-2 w-2 rounded-full ${COV_TONE[s]}`} />{COV_LABEL[s]}</span>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-ink-3">Abdeckung misst, wie vollständig besprochen wurde – nicht, ob Inhalte fachlich richtig sind.</p>
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-2xl border border-dashed border-line p-8 text-center text-sm text-ink-3">{children}</div>;
}

export function ErrorNote({ message, onClose }: { message: string | null; onClose?: () => void }) {
  if (!message) return null;
  return (
    <div className="fade-in mb-4 flex items-start justify-between gap-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
      <span>{message}</span>
      {onClose && <button onClick={onClose} className="text-red-500 hover:text-red-800">✕</button>}
    </div>
  );
}

export function Spinner({ className = "" }: { className?: string }) {
  return <span className={`inline-block h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent ${className}`} />;
}

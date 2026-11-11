"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import type { InterviewSummaryRow } from "@/lib/types";
import { Bar, CATEGORY_LABEL, Empty, ErrorNote, PageHeader, Ring, Stat, StatusBadge } from "@/components/ui";

interface Dash {
  employees: number;
  interviews_total: number;
  interviews_completed: number;
  interviews_in_progress: number;
  knowledge_units: number;
  confirmed_units: number;
  open_validations: number;
  avg_coverage: number;
  categories: Record<string, number>;
  recent_interviews: InterviewSummaryRow[];
  activity: { at: string; actor: string; role: string; action: string; entity_type: string; entity_id: string }[];
}

const ACTION_LABEL: Record<string, string> = {
  "employee.created": "Mitarbeiter angelegt", "interview.created": "Interview angelegt", "interview.plan_generated": "Interviewplan erstellt",
  "interview.consent_given": "Einwilligung erteilt", "interview.started": "Interview gestartet", "interview.paused": "Interview pausiert",
  "interview.resumed": "Interview fortgesetzt", "interview.completed": "Interview abgeschlossen", "interview.summary_generated": "Zusammenfassung erstellt",
  "unit.confirm": "Wissenseinheit bestätigt", "unit.correct": "Wissenseinheit korrigiert", "unit.reject": "Wissenseinheit zurückgewiesen",
  "unit.mark_uncertain": "Als unsicher markiert", "unit.add_example": "Beispiel ergänzt", "unit.add_note": "Ergänzung hinzugefügt",
  "export.generated": "JSON-Export erzeugt", "training.generated": "Training generiert",
};

export default function Dashboard() {
  const [d, setD] = useState<Dash | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.get<Dash>("/api/dashboard").then(setD).catch((e) => setErr(e.message));
  }, []);
  const maxCat = Math.max(1, ...Object.values(d?.categories || {}));

  return (
    <>
      <PageHeader eyebrow="Phase 1 · Knowledge Acquisition" title="Wissenssicherung"
        subtitle="Erfahrungswissen erfassen, bevor es das Unternehmen verlässt."
        actions={<Link href="/employees" className="btn-primary">Interview vorbereiten</Link>} />
      <ErrorNote message={err} />
      {d && (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-6">
            <Stat label="Mitarbeiter" value={d.employees} />
            <Stat label="Interviews durchgeführt" value={d.interviews_completed} />
            <Stat label="In Bearbeitung" value={d.interviews_in_progress} />
            <Stat label="Wissenseinheiten" value={d.knowledge_units} hint={`${d.confirmed_units} expertenbestätigt`} />
            <Stat label="Offene Validierungen" value={d.open_validations} accent={d.open_validations > 0} />
            <div className="card flex items-center justify-between p-5">
              <div>
                <div className="text-xs font-medium text-ink-2">Ø Wissensabdeckung</div>
                <div className="mt-1 text-[11px] text-ink-3">≠ fachliche Richtigkeit</div>
              </div>
              <Ring value={d.avg_coverage} size={68} />
            </div>
          </div>

          <div className="mt-6 grid gap-6 lg:grid-cols-3">
            <div className="card p-6 lg:col-span-2">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="font-semibold">Letzte Interviews</h2>
                <Link href="/interviews" className="text-sm text-ink-2 hover:text-ink">Alle</Link>
              </div>
              {d.recent_interviews.length === 0 ? (
                <Empty>Noch keine Interviews. Legen Sie zuerst einen Mitarbeiter an.</Empty>
              ) : (
                <div className="divide-y divide-line/60">
                  {d.recent_interviews.map((iv) => (
                    <Link key={iv.id} href={iv.status === "in_review" || iv.status === "approved" ? `/interviews/${iv.id}/review` : `/interviews/${iv.id}`}
                      className="flex items-center gap-4 py-3 hover:opacity-80">
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium">{iv.title}</div>
                        <div className="text-xs text-ink-3">{iv.employee_name} · {iv.unit_count} Einheiten · {fmtDate(iv.created_at)}</div>
                      </div>
                      <div className="w-28"><Bar value={iv.coverage_score} /></div>
                      <StatusBadge status={iv.status} />
                    </Link>
                  ))}
                </div>
              )}
            </div>

            <div className="space-y-6">
              <div className="card p-6">
                <h2 className="mb-4 font-semibold">Wissensdimensionen</h2>
                {Object.keys(CATEGORY_LABEL).map((c) => (
                  <div key={c} className="mb-3">
                    <div className="mb-1 flex justify-between text-xs"><span className="text-ink-2">{CATEGORY_LABEL[c]}</span><span>{d.categories[c] || 0}</span></div>
                    <Bar value={(d.categories[c] || 0) / maxCat} tone="bg-zinc-800" />
                  </div>
                ))}
              </div>
              <div className="card p-6">
                <h2 className="mb-4 font-semibold">Letzte Aktivitäten</h2>
                {d.activity.length === 0 && <div className="text-sm text-ink-3">Keine Aktivitäten.</div>}
                <ul className="space-y-3">
                  {d.activity.slice(0, 8).map((a, i) => (
                    <li key={i} className="flex gap-3 text-sm">
                      <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-ink-3" />
                      <div>
                        <div>{ACTION_LABEL[a.action] || a.action}</div>
                        <div className="text-xs text-ink-3">{a.actor} · {fmtDate(a.at)}</div>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </div>
        </>
      )}
    </>
  );
}

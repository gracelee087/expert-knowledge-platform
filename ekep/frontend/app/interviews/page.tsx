"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, fmtDate, fmtTime } from "@/lib/api";
import type { InterviewSummaryRow } from "@/lib/types";
import { Bar, Empty, ErrorNote, PageHeader, StatusBadge } from "@/components/ui";

export default function Interviews() {
  const [list, setList] = useState<InterviewSummaryRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.get<InterviewSummaryRow[]>("/api/interviews").then(setList).catch((e) => setErr(e.message)); }, []);
  return (
    <>
      <PageHeader eyebrow="Übersicht" title="Interviews" />
      <ErrorNote message={err} />
      {list.length === 0 ? <Empty>Noch keine Interviews.</Empty> : (
        <div className="card overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-canvas/60 text-left text-xs text-ink-3">
              <tr><th className="px-5 py-3 font-medium">Interview</th><th className="font-medium">Experte</th><th className="font-medium">Dauer</th>
                <th className="font-medium">Einheiten</th><th className="w-40 font-medium">Abdeckung</th><th className="font-medium">Status</th><th /></tr>
            </thead>
            <tbody className="divide-y divide-line/60">
              {list.map((iv) => (
                <tr key={iv.id}>
                  <td className="px-5 py-3"><div className="font-medium">{iv.title}</div><div className="text-xs text-ink-3">{fmtDate(iv.created_at)}</div></td>
                  <td>{iv.employee_name}</td>
                  <td>{fmtTime(iv.elapsed_seconds)} / {iv.planned_minutes}′</td>
                  <td>{iv.unit_count} <span className="text-ink-3">({iv.confirmed_count} ✓)</span></td>
                  <td className="pr-6"><Bar value={iv.coverage_score} /></td>
                  <td><StatusBadge status={iv.status} /></td>
                  <td className="pr-5 text-right space-x-2">
                    <Link className="btn-secondary !py-1" href={`/interviews/${iv.id}`}>Workspace</Link>
                    {["completed", "in_review", "approved"].includes(iv.status) && <Link className="btn-primary !py-1" href={`/interviews/${iv.id}/review`}>Review</Link>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

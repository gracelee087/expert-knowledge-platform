"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, fmtDate } from "@/lib/api";
import type { Employee } from "@/lib/types";
import { Bar, Empty, ErrorNote, PageHeader, Ring, Spinner, StatusBadge } from "@/components/ui";

export default function EmployeeProfile() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [e, setE] = useState<Employee | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [form, setForm] = useState({ title: "", goal: "", planned_minutes: 30 });

  useEffect(() => {
    api.get<Employee>(`/api/employees/${id}`).then((x) => {
      setE(x);
      setForm((f) => ({
        ...f,
        title: f.title || `Wissenssicherung: ${x.expertise_area.split(",")[0]}`,
        goal: f.goal || `Implizites Erfahrungswissen von ${x.name} zu ${x.expertise_area} sichern – insbesondere Sonderfälle, Entscheidungslogik und typische Fehler.`,
      }));
    }).catch((e) => setErr(e.message));
  }, [id]);

  const create = async (ev: React.FormEvent) => {
    ev.preventDefault();
    try {
      setBusy("Interview wird angelegt …");
      const iv = await api.post<{ id: string }>(`/api/employees/${id}/interviews`, form);
      setBusy("KI erstellt individuellen Interviewplan …");
      await api.post(`/api/interviews/${iv.id}/plan`);
      router.push(`/interviews/${iv.id}`);
    } catch (e: any) {
      setErr(e.message);
      setBusy(null);
    }
  };

  if (!e) return <ErrorNote message={err} />;
  return (
    <>
      <PageHeader eyebrow={e.department} title={e.name} subtitle={`${e.role} · ${e.years_experience ?? "?"} Jahre Erfahrung`} />
      <ErrorNote message={err} onClose={() => setErr(null)} />
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6">
          <div className="card p-6">
            <div className="eyebrow mb-2">Fachgebiet</div>
            <p className="text-sm">{e.expertise_area}</p>
            {e.responsibilities.length > 0 && (<><div className="eyebrow mb-2 mt-5">Verantwortung</div>
              <ul className="list-disc space-y-1 pl-4 text-sm text-ink-2">{e.responsibilities.map((r) => <li key={r}>{r}</li>)}</ul></>)}
            {e.specializations.length > 0 && (<><div className="eyebrow mb-2 mt-5">Spezialisierungen</div>
              <div className="flex flex-wrap gap-1.5">{e.specializations.map((s) => <span key={s} className="rounded-full bg-canvas px-2.5 py-1 text-xs">{s}</span>)}</div></>)}
          </div>
          <div className="card flex items-center justify-around p-6">
            <Ring value={e.coverage_score || 0} label="Abdeckung" />
            <div className="space-y-2 text-sm">
              <div><span className="font-semibold">{e.unit_count}</span> <span className="text-ink-3">Wissenseinheiten</span></div>
              <div><span className="font-semibold">{e.confirmed_count}</span> <span className="text-ink-3">expertenbestätigt</span></div>
              <div><span className="font-semibold">{(e.unit_count || 0) - (e.confirmed_count || 0)}</span> <span className="text-ink-3">offen/ungeprüft</span></div>
            </div>
          </div>
        </div>

        <div className="space-y-6 lg:col-span-2">
          <form onSubmit={create} className="card p-6">
            <h2 className="mb-4 font-semibold">Neues Interview vorbereiten</h2>
            <div className="grid gap-4 md:grid-cols-4">
              <div className="md:col-span-3"><label className="label">Titel</label>
                <input required className="input" value={form.title} onChange={(x) => setForm({ ...form, title: x.target.value })} /></div>
              <div><label className="label">Dauer (Min.)</label>
                <input type="number" min={5} max={180} className="input" value={form.planned_minutes}
                  onChange={(x) => setForm({ ...form, planned_minutes: Number(x.target.value) })} /></div>
              <div className="md:col-span-4"><label className="label">Interviewziel</label>
                <textarea required rows={3} className="input" value={form.goal} onChange={(x) => setForm({ ...form, goal: x.target.value })} /></div>
            </div>
            <div className="mt-4 flex items-center justify-end gap-3">
              {busy && <span className="flex items-center gap-2 text-sm text-ink-2"><Spinner />{busy}</span>}
              <button disabled={!!busy} className="btn-primary">Interviewplan erstellen</button>
            </div>
          </form>

          <div className="card p-6">
            <h2 className="mb-4 font-semibold">Interviewhistorie</h2>
            {!e.interviews?.length ? <Empty>Noch keine Interviews.</Empty> : (
              <div className="divide-y divide-line/60">
                {e.interviews.map((iv) => (
                  <Link key={iv.id} href={["in_review", "approved"].includes(iv.status) ? `/interviews/${iv.id}/review` : `/interviews/${iv.id}`}
                    className="flex items-center gap-4 py-3 hover:opacity-80">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{iv.title}</div>
                      <div className="text-xs text-ink-3">{fmtDate(iv.created_at)} · {iv.unit_count} Einheiten · {iv.confirmed_count} bestätigt</div>
                    </div>
                    <div className="w-28"><Bar value={iv.coverage_score} /></div>
                    <StatusBadge status={iv.status} />
                  </Link>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

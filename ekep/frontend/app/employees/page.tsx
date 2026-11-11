"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Employee } from "@/lib/types";
import { Bar, Empty, ErrorNote, PageHeader } from "@/components/ui";

const blank = { name: "", department: "", role: "", expertise_area: "", years_experience: "", responsibilities: "", specializations: "" };

const SAMPLE = {
  name: "Helga Weber", department: "Bestandsverwaltung Bausparen", role: "Senior Sachbearbeiterin",
  expertise_area: "Altverträge, historische Tarifgenerationen und Sonderfälle", years_experience: "31",
  responsibilities: "Klärung von Sonderfällen bei Altverträgen\nFreigabe von Tarifwechseln\nEinarbeitung neuer Kolleginnen",
  specializations: "Tarife vor 2005\nZuteilungsfragen\nWohnungsbauprämie bei Altverträgen",
};

export default function Employees() {
  const [list, setList] = useState<Employee[]>([]);
  const [form, setForm] = useState(blank);
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () => api.get<Employee[]>("/api/employees").then(setList).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);

  const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/api/employees", {
        ...form, years_experience: form.years_experience ? Number(form.years_experience) : null,
        responsibilities: lines(form.responsibilities), specializations: lines(form.specializations),
      });
      setForm(blank);
      setOpen(false);
      load();
    } catch (e: any) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };

  const f = (k: keyof typeof blank) => ({ value: form[k], onChange: (e: any) => setForm({ ...form, [k]: e.target.value }) });

  return (
    <>
      <PageHeader eyebrow="Wissensträger" title="Mitarbeiter" subtitle="Expertinnen und Experten, deren Wissen gesichert werden soll."
        actions={<button className="btn-primary" onClick={() => setOpen(!open)}>{open ? "Schließen" : "Mitarbeiter anlegen"}</button>} />
      <ErrorNote message={err} onClose={() => setErr(null)} />

      {open && (
        <form onSubmit={submit} className="card fade-in mb-8 grid gap-4 p-6 md:grid-cols-2">
          <div className="md:col-span-2 flex items-center justify-between">
            <h2 className="font-semibold">Neuer Mitarbeiter</h2>
            <button type="button" className="btn-ghost text-xs" onClick={() => setForm(SAMPLE)}>Fiktives Beispiel einfügen</button>
          </div>
          <div><label className="label">Name</label><input required className="input" {...f("name")} /></div>
          <div><label className="label">Abteilung</label><input required className="input" {...f("department")} /></div>
          <div><label className="label">Funktion</label><input required className="input" {...f("role")} /></div>
          <div><label className="label">Berufserfahrung (Jahre)</label><input type="number" min={0} className="input" {...f("years_experience")} /></div>
          <div className="md:col-span-2"><label className="label">Fachgebiet</label><input required className="input" {...f("expertise_area")} /></div>
          <div><label className="label">Verantwortungsbereiche (eine pro Zeile)</label><textarea rows={4} className="input" {...f("responsibilities")} /></div>
          <div><label className="label">Spezialisierungen (eine pro Zeile)</label><textarea rows={4} className="input" {...f("specializations")} /></div>
          <div className="md:col-span-2 flex justify-end"><button disabled={busy} className="btn-primary">Speichern</button></div>
        </form>
      )}

      {list.length === 0 ? (
        <Empty>Noch keine Mitarbeiter angelegt.</Empty>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {list.map((e) => (
            <Link key={e.id} href={`/employees/${e.id}`} className="card p-5 transition hover:shadow-md">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-canvas text-sm font-semibold">
                  {e.name.split(" ").map((x) => x[0]).slice(0, 2).join("")}
                </div>
                <div className="min-w-0">
                  <div className="truncate font-medium">{e.name}</div>
                  <div className="truncate text-xs text-ink-3">{e.role} · {e.department}</div>
                </div>
              </div>
              <div className="mt-4 line-clamp-2 text-sm text-ink-2">{e.expertise_area}</div>
              <div className="mt-4 grid grid-cols-3 gap-2 text-center text-xs">
                <div><div className="text-base font-semibold">{e.interview_count}</div><div className="text-ink-3">Interviews</div></div>
                <div><div className="text-base font-semibold">{e.unit_count}</div><div className="text-ink-3">Einheiten</div></div>
                <div><div className="text-base font-semibold">{e.confirmed_count}</div><div className="text-ink-3">bestätigt</div></div>
              </div>
              <div className="mt-4"><Bar value={e.coverage_score || 0} /></div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

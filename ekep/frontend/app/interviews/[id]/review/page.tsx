"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { API_URL, api, fmtDate, fmtTime, getIdentity } from "@/lib/api";
import type { InterviewState, KnowledgeBasePublishResult, KnowledgeSearchState, KnowledgeUnit, Reconciliation, TrainingPackage, TrainingVideoResult } from "@/lib/types";
import { ReconciliationNote, ReconciliationSummary, RelationBadge } from "@/components/KnowledgeHits";
import {
  Badge, CATEGORY_LABEL, ConfidenceBadge, CoverageGrid, Empty, ErrorNote, PageHeader, Ring, Spinner, StatusBadge,
} from "@/components/ui";

const FILTERS = [
  { key: "all", label: "Alle" }, { key: "extracted", label: "Zu prüfen" }, { key: "confirmed", label: "Bestätigt" },
  { key: "uncertain", label: "Unsicher" }, { key: "rejected", label: "Zurückgewiesen" },
];

export default function Review() {
  const { id } = useParams<{ id: string }>();
  const [iv, setIv] = useState<InterviewState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [trainings, setTrainings] = useState<TrainingPackage[]>([]);
  const [audience, setAudience] = useState("einsteiger");
  const [busy, setBusy] = useState<string | null>(null);
  const [ks, setKs] = useState<KnowledgeSearchState | null>(null);
  const role = typeof window !== "undefined" ? getIdentity().role : "admin";

  const load = useCallback(async () => {
    try {
      setIv(await api.get<InterviewState>(`/api/interviews/${id}`));
      setTrainings(await api.get<TrainingPackage[]>(`/api/interviews/${id}/training`));
      setKs(await api.get<KnowledgeSearchState>(`/api/interviews/${id}/knowledge-search`));
    } catch (e: any) { setErr(e.message); }
  }, [id]);
  useEffect(() => { load(); }, [load]);

  const units = useMemo(() => {
    const all = iv?.units || [];
    if (filter === "all") return all;
    if (filter === "confirmed") return all.filter((u) => u.review_status === "confirmed" || u.review_status === "corrected");
    return all.filter((u) => u.review_status === filter);
  }, [iv, filter]);

  const grouped = useMemo(() => {
    const g: Record<string, KnowledgeUnit[]> = {};
    units.forEach((u) => (g[u.topic] ||= []).push(u));
    return g;
  }, [units]);

  const exportJson = async (includeUnconfirmed: boolean) => {
    try {
      const data = await api.get<any>(`/api/v1/export/interviews/${id}?include_transcript=true${includeUnconfirmed ? "&include_unconfirmed=true" : ""}`);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `ekep-export-${id}-v${data.schema_version}${includeUnconfirmed ? "-ENTWURF" : ""}.json`;
      a.click();
    } catch (e: any) { setErr(e.message); }
  };

  const runReconcile = async () => {
    setBusy("Abgleich mit der Wissensbasis läuft …");
    try {
      await api.post(`/api/interviews/${id}/reconcile`);
      setKs(await api.get<KnowledgeSearchState>(`/api/interviews/${id}/knowledge-search`));
    } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  };

  const genTraining = async () => {
    setBusy("Training wird aus bestätigtem Wissen generiert …");
    try {
      const t = await api.post<TrainingPackage>(`/api/interviews/${id}/training`, { audience });
      setTrainings((x) => [t, ...x]);
    } catch (e: any) { setErr(e.message); } finally { setBusy(null); }
  };

  if (!iv) return <ErrorNote message={err} />;
  const total = iv.units.filter((u) => u.review_status !== "rejected").length;
  const open = iv.units.filter((u) => u.review_status === "extracted").length;

  return (
    <>
      <PageHeader eyebrow={`Expert Review · ${iv.employee_name}`} title={iv.title}
        subtitle={<span className="inline-flex items-center gap-2"><StatusBadge status={iv.status} /> {iv.confirmed_count} von {total} Einheiten bestätigt · {open} offen</span>}
        actions={<>
          <Link href={`/interviews/${id}`} className="btn-ghost">Transkript</Link>
          {role === "admin" && <button className="btn-secondary" onClick={() => exportJson(true)} title="Nur intern – enthält ungeprüfte Inhalte">Entwurf exportieren</button>}
          {role === "admin" && <button className="btn-primary" onClick={() => exportJson(false)}>JSON-Export (bestätigt)</button>}
        </>} />
      <ErrorNote message={err} onClose={() => setErr(null)} />

      {["in_progress", "paused", "planned"].includes(iv.status) && (
        <div className="mb-6 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">Review ist erst nach Abschluss des Interviews möglich.</div>
      )}
      {role === "interviewer" && <div className="mb-6 rounded-xl bg-canvas px-4 py-3 text-sm text-ink-2">Als Wissensmanager sehen Sie das Review nur lesend. Bestätigen kann der Experte.</div>}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {iv.summary && (
            <div className="card p-6">
              <div className="eyebrow mb-2">Zusammenfassung (KI, ungeprüft)</div>
              <p className="text-[15px] leading-relaxed">{iv.summary.executive_summary}</p>
              {iv.summary.key_findings.length > 0 && <ul className="mt-4 list-disc space-y-1 pl-5 text-sm text-ink-2">{iv.summary.key_findings.map((k) => <li key={k}>{k}</li>)}</ul>}
              {iv.summary.quality_notes.length > 0 && <div className="mt-4 rounded-xl bg-canvas p-3 text-xs text-ink-2"><b>Belastbarkeit:</b> {iv.summary.quality_notes.join(" · ")}</div>}
            </div>
          )}

          <div className="flex flex-wrap gap-1">
            {FILTERS.map((f) => (
              <button key={f.key} onClick={() => setFilter(f.key)} className={`rounded-full px-3 py-1.5 text-sm ${filter === f.key ? "bg-ink text-white" : "text-ink-2 hover:bg-white"}`}>{f.label}</button>
            ))}
          </div>

          {units.length === 0 ? <Empty>Keine Wissenseinheiten in diesem Filter.</Empty> :
            Object.entries(grouped).map(([topic, us]) => (
              <section key={topic}>
                <h2 className="mb-3 text-sm font-semibold text-ink-2">{topic}</h2>
                <div className="space-y-4">{us.map((u) => <UnitCard key={u.id} unit={u} iv={iv} rec={ks?.reconciliation.by_unit[u.id]} canReview={role !== "interviewer"} onChange={load} onError={setErr} />)}</div>
              </section>
            ))}
        </div>

        <div className="space-y-6">
          <div className="card flex items-center gap-5 p-6">
            <Ring value={iv.coverage_score} label="Abdeckung" />
            <div className="text-sm text-ink-2">Validierungsstatus wird ausschließlich durch Ihre Bestätigung gesetzt.</div>
          </div>
          <div className="card p-6">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">Abgleich mit Wissensbasis</h3>
              <button className="btn-ghost !px-2 !py-0.5 text-xs" disabled={!!busy || ["planned", "in_progress", "paused"].includes(iv.status)} onClick={runReconcile}>
                {ks?.reconciliation.summary.total ? "Neu abgleichen" : "Abgleich ausführen"}
              </button>
            </div>
            {ks?.reconciliation.summary.total ? <ReconciliationSummary s={ks.reconciliation.summary} /> :
              <p className="text-xs text-ink-3">Wird beim Interviewabschluss automatisch erstellt.</p>}
            <p className="mt-3 text-[11px] text-ink-3">
              Empfehlung für die Vektordatenbank: neu → anlegen · Ergänzung → bestehenden Eintrag anpassen · bekannt → keine Änderung · Widerspruch → fachlich klären.
              Quelle: {ks?.provider === "rag_service" ? "Vektordatenbank (RAG-Service)" : ks?.provider === "http" ? "Vektordatenbank" : "lokaler Referenzbestand (bis zur Anbindung)"}.
            </p>
          </div>
          <KnowledgeBaseActions id={id} confirmedCount={iv.confirmed_count} onError={setErr} />
          <div className="card p-6"><h3 className="mb-3 text-sm font-semibold">Coverage-Matrix</h3><CoverageGrid iv={iv} compact /></div>

          {iv.contradictions.length > 0 && (
            <div className="card p-6">
              <h3 className="mb-3 text-sm font-semibold">Widersprüche</h3>
              <ul className="space-y-3">{iv.contradictions.map((c) => (
                <li key={c.id} className="text-sm">
                  <div className="flex items-center justify-between gap-2"><span>{c.description}</span><StatusBadge status={c.status} /></div>
                  <div className="mt-1 text-xs text-ink-3">A: {c.statement_a}<br />B: {c.statement_b}</div>
                  {c.status === "open" && role !== "interviewer" && (
                    <div className="mt-2 flex gap-2">
                      <button className="btn-secondary !px-2 !py-0.5 text-xs" onClick={async () => {
                        const res = prompt("Wie ist der Widerspruch aufzulösen?"); if (res === null) return;
                        await api.post(`/api/interviews/${id}/contradictions/${c.id}`, { status: "clarified", resolution: res }); load();
                      }}>Klären</button>
                      <button className="btn-ghost !px-2 !py-0.5 text-xs" onClick={async () => { await api.post(`/api/interviews/${id}/contradictions/${c.id}`, { status: "dismissed" }); load(); }}>Kein Widerspruch</button>
                    </div>
                  )}
                </li>))}</ul>
            </div>
          )}

          <div className="card p-6">
            <h3 className="mb-3 text-sm font-semibold">Offene Fragen</h3>
            {iv.gaps.filter((g) => g.status === "open").length === 0 && !iv.summary?.open_questions.length ? <p className="text-xs text-ink-3">Keine.</p> : (
              <ul className="space-y-2 text-xs text-ink-2">
                {iv.gaps.filter((g) => g.status === "open").map((g) => <li key={g.id}>• {g.description}</li>)}
                {iv.summary?.open_questions.map((q) => <li key={q}>• {q}</li>)}
              </ul>
            )}
            {iv.summary?.recommended_followups.length ? <><div className="eyebrow mb-1 mt-4">Empfohlene Folgeinterviews</div>
              <ul className="space-y-1 text-xs text-ink-2">{iv.summary.recommended_followups.map((q) => <li key={q}>• {q}</li>)}</ul></> : null}
          </div>

          <div className="card p-6">
            <h3 className="text-sm font-semibold">Übergabe an RAG-Team</h3>
            <p className="mt-1 text-xs text-ink-3">Vertrag <code>ekep.knowledge-export</code> v1.1.0 · nur expertenbestätigte Einheiten · mit Quellenzitaten und Revisionshistorie.</p>
            <a href={`${API_URL}/api/v1/export/schema`} target="_blank" className="mt-3 inline-block text-xs text-accent hover:underline">JSON-Schema ansehen ↗</a>
          </div>

          <div className="card p-6">
            <div className="eyebrow mb-1">Vorschau Phase 3 · Demo</div>
            <h3 className="text-sm font-semibold">Training generieren</h3>
            <p className="mt-1 text-xs text-ink-3">Folien, Sprechertext und Quiz ausschließlich aus bestätigten Einheiten – jede Folie mit Quellenverweis.</p>
            <div className="mt-3 flex gap-2">
              <select value={audience} onChange={(e) => setAudience(e.target.value)} className="input !w-auto">
                <option value="einsteiger">Einsteiger</option><option value="fortgeschritten">Fortgeschrittene</option><option value="experten">Erfahrene</option>
              </select>
              <button className="btn-primary flex-1" disabled={!!busy || iv.confirmed_count === 0 || role === "expert"} onClick={genTraining}>
                {busy ? <Spinner /> : "Generieren"}
              </button>
            </div>
            {iv.confirmed_count === 0 && <p className="mt-2 text-[11px] text-amber-700">Erst Einheiten bestätigen.</p>}
          </div>
        </div>
      </div>

      {trainings.length > 0 && <TrainingView t={trainings[0]} />}
    </>
  );
}

const DELTA_TONE: Record<string, string> = {
  neu: "bg-emerald-50 text-emerald-700", ergaenzung: "bg-sky-50 text-sky-700", bestaetigung: "bg-zinc-100 text-zinc-600", widerspruch: "bg-red-50 text-red-700",
};
const DELTA_LABEL: Record<string, string> = { neu: "Neu", ergaenzung: "Ergänzung", bestaetigung: "Bereits bekannt", widerspruch: "Widerspruch" };

function KnowledgeBaseActions({ id, confirmedCount, onError }: { id: string; confirmedCount: number; onError: (m: string) => void }) {
  const [publishing, setPublishing] = useState(false);
  const [publish, setPublish] = useState<KnowledgeBasePublishResult | null>(null);
  const [rendering, setRendering] = useState(false);
  const [video, setVideo] = useState<TrainingVideoResult | null>(null);

  const runPublish = async () => {
    setPublishing(true);
    try { setPublish(await api.post<KnowledgeBasePublishResult>(`/api/interviews/${id}/knowledge-base/publish`)); }
    catch (e: any) { onError(e.message); } finally { setPublishing(false); }
  };
  const runVideo = async () => {
    setRendering(true);
    try { setVideo(await api.post<TrainingVideoResult>(`/api/interviews/${id}/knowledge-base/video`)); }
    catch (e: any) { onError(e.message); } finally { setRendering(false); }
  };

  const taken = publish ? publish.published.filter((p) => p.status === "übernommen").length : 0;
  return (
    <div className="card p-6">
      <h3 className="text-sm font-semibold">Wissensbasis aktualisieren</h3>
      <p className="mt-1 text-xs text-ink-3">Übernimmt bestätigte Einheiten (Neu, Ergänzung) in die Vektordatenbank der Abteilung. Bereits bekannte Einheiten werden übersprungen, Widersprüche bleiben zur Klärung.</p>
      <div className="mt-3 flex flex-col gap-2">
        <button className="btn-primary" disabled={publishing || rendering || confirmedCount === 0} onClick={runPublish}
          title={confirmedCount === 0 ? "Erst Einheiten bestätigen." : undefined}>
          {publishing ? <><Spinner /> Übernehme …</> : "Wissensbasis aktualisieren"}
        </button>
        <button className="btn-secondary" disabled={rendering || publishing} onClick={runVideo}>
          {rendering ? <><Spinner /> Video wird erstellt (1–4 Min.) …</> : "Schulungsvideo erstellen"}
        </button>
      </div>

      {publish && (
        <div className="mt-4 space-y-2 text-xs">
          <div className="font-medium text-ink">{taken} Einträge in die Wissensbasis ({publish.department}) übernommen</div>
          {publish.published.length > 0 && (
            <ul className="space-y-1">
              {publish.published.map((p) => (
                <li key={p.unit_id} className="flex flex-wrap items-center gap-1.5">
                  <span className="text-ink-2">{p.title}</span>
                  {p.delta_type && <Badge tone={DELTA_TONE[p.delta_type] || "bg-zinc-100 text-zinc-600"}>{DELTA_LABEL[p.delta_type] || p.delta_type}</Badge>}
                  <span className={p.status === "fehlgeschlagen" ? "text-red-600" : "text-ink-3"}>{p.status}</span>
                </li>
              ))}
            </ul>
          )}
          {publish.skipped.length > 0 && (
            <ul className="space-y-1 text-ink-3">
              {publish.skipped.map((s) => <li key={s.unit_id}>{s.title} · <span className="italic">{s.reason}</span></li>)}
            </ul>
          )}
        </div>
      )}

      {video && (
        <div className="mt-4 space-y-2 text-xs">
          <div className="font-medium text-ink">{video.title}</div>
          <a href={video.absolute_url} target="_blank" className="text-accent hover:underline">Video öffnen ↗</a>
          <video controls src={video.absolute_url} className="mt-1 w-full max-w-full rounded-xl" />
        </div>
      )}
    </div>
  );
}

const LIST_FIELDS: [keyof KnowledgeUnit, string][] = [
  ["prerequisites", "Voraussetzungen"], ["process_steps", "Prozessschritte"], ["exceptions", "Ausnahmen"], ["examples", "Praxisbeispiele"],
  ["risks", "Risiken"], ["common_mistakes", "Typische Fehler"], ["escalation_paths", "Eskalationswege"], ["dependencies", "Abhängigkeiten"],
  ["historical_changes", "Historische Veränderungen"],
];

function UnitCard({ unit: u, iv, rec, canReview, onChange, onError }: { unit: KnowledgeUnit; iv: InterviewState; rec?: Reconciliation; canReview: boolean; onChange: () => void; onError: (m: string) => void }) {
  const [edit, setEdit] = useState(false);
  const [history, setHistory] = useState<KnowledgeUnit | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const reviewable = canReview && !["in_progress", "paused", "planned"].includes(iv.status);
  const turnsById = useMemo(() => Object.fromEntries(iv.turns.map((t) => [t.id, t])), [iv.turns]);

  const review = async (action: string, extra: Record<string, unknown> = {}) => {
    try { await api.post(`/api/units/${u.id}/review`, { action, ...extra }); onChange(); setEdit(false); } catch (e: any) { onError(e.message); }
  };
  const startEdit = () => {
    setDraft({
      title: u.title, content: u.content, explanation: u.explanation, decision_logic: u.decision_logic || "", validity_note: u.validity_note || "",
      ...Object.fromEntries(LIST_FIELDS.map(([k]) => [k, ((u[k] as string[]) || []).join("\n")])),
    });
    setEdit(true);
  };
  const saveEdit = () => {
    const fields: Record<string, unknown> = {
      title: draft.title, content: draft.content, explanation: draft.explanation,
      decision_logic: draft.decision_logic || null, validity_note: draft.validity_note || null,
    };
    LIST_FIELDS.forEach(([k]) => (fields[k] = (draft[k as string] || "").split("\n").map((x) => x.trim()).filter(Boolean)));
    const comment = prompt("Kurzer Kommentar zur Korrektur (optional):") || undefined;
    review("correct", { fields, comment });
  };
  const toggleHistory = async () => {
    if (history) return setHistory(null);
    try { setHistory(await api.get<KnowledgeUnit>(`/api/units/${u.id}`)); } catch (e: any) { onError(e.message); }
  };

  const dim = u.review_status === "rejected" ? "opacity-50" : "";
  return (
    <div className={`card p-5 ${dim}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-ink-3">{u.id} · v{u.version}</span>
        <Badge>{CATEGORY_LABEL[u.category]}</Badge>
        <ConfidenceBadge level={u.confidence} reason={u.confidence_reason} />
        <RelationBadge r={rec} />
        <span className="ml-auto"><StatusBadge status={u.review_status} /></span>
      </div>

      {!edit ? (
        <>
          <h3 className="mt-2 font-semibold">{u.title}</h3>
          <p className="mt-1 text-sm">{u.content}</p>
          {u.explanation && <p className="mt-2 text-sm text-ink-2">{u.explanation}</p>}
          {u.decision_logic && <div className="mt-3 text-sm"><span className="eyebrow">Entscheidungslogik</span><p className="text-ink-2">{u.decision_logic}</p></div>}
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {LIST_FIELDS.filter(([k]) => ((u[k] as string[]) || []).length).map(([k, label]) => (
              <div key={k}><div className="eyebrow mb-1">{label}</div>
                <ul className={`${k === "process_steps" ? "list-decimal" : "list-disc"} space-y-0.5 pl-4 text-sm text-ink-2`}>
                  {(u[k] as string[]).map((x, i) => <li key={i}>{x}</li>)}</ul></div>
            ))}
          </div>
          {u.validity_note && <div className="mt-3 text-xs"><span className="eyebrow">Zeitliche Gültigkeit </span>{u.validity_note}</div>}
          {u.uncertainties.length > 0 && <div className="mt-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-900">Unsicherheiten: {u.uncertainties.join(" · ")}</div>}
          {u.expert_notes.length > 0 && <div className="mt-3 rounded-lg bg-canvas px-3 py-2 text-xs">Ergänzungen: {u.expert_notes.join(" · ")}</div>}
          <ReconciliationNote r={rec} />
        </>
      ) : (
        <div className="mt-3 grid gap-3">
          {["title", "content", "explanation", "decision_logic", "validity_note"].map((k) => (
            <div key={k}><label className="label">{({ title: "Titel", content: "Inhalt", explanation: "Erklärung", decision_logic: "Entscheidungslogik", validity_note: "Zeitliche Gültigkeit" } as any)[k]}</label>
              <textarea rows={k === "title" ? 1 : 2} className="input" value={draft[k]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} /></div>
          ))}
          <div className="grid gap-3 sm:grid-cols-2">
            {LIST_FIELDS.map(([k, label]) => (
              <div key={k}><label className="label">{label} (eine pro Zeile)</label>
                <textarea rows={3} className="input" value={draft[k as string]} onChange={(e) => setDraft({ ...draft, [k]: e.target.value })} /></div>
            ))}
          </div>
          <div className="flex justify-end gap-2"><button className="btn-ghost" onClick={() => setEdit(false)}>Abbrechen</button><button className="btn-primary" onClick={saveEdit}>Korrektur speichern & bestätigen</button></div>
        </div>
      )}

      {/* Quellen */}
      <div className="mt-4 space-y-1.5 border-t border-line/60 pt-3">
        {u.source_refs.map((r, i) => (
          <div key={i} className="flex gap-2 text-xs">
            <span className={r.verified ? "text-emerald-600" : "text-red-600"} title={r.verified ? "Zitat im Transkript verifiziert" : "Zitat nicht wörtlich im Transkript gefunden"}>{r.verified ? "✓" : "!"}</span>
            <span className="text-ink-3">{fmtTime(r.t_start)}</span>
            <span className="italic text-ink-2" title={turnsById[r.turn_id]?.text}>„{r.quote}“</span>
          </div>
        ))}
      </div>

      {reviewable && !edit && (
        <div className="mt-4 flex flex-wrap gap-2">
          {u.review_status !== "confirmed" && u.review_status !== "corrected" && <button className="btn-primary !py-1.5" onClick={() => review("confirm")}>Bestätigen</button>}
          <button className="btn-secondary !py-1.5" onClick={startEdit}>Korrigieren</button>
          <button className="btn-secondary !py-1.5" onClick={() => { const t = prompt("Weiteres Praxisbeispiel:"); if (t) review("add_example", { text: t }); }}>Beispiel ergänzen</button>
          <button className="btn-secondary !py-1.5" onClick={() => { const t = prompt("Ergänzung:"); if (t) review("add_note", { text: t }); }}>Ergänzen</button>
          <button className="btn-ghost !py-1.5" onClick={() => { const t = prompt("Was ist unsicher?") ?? undefined; review("mark_uncertain", { text: t }); }}>Unsicher</button>
          {u.review_status !== "rejected" ? <button className="btn-ghost !py-1.5 text-red-600" onClick={() => review("reject", { comment: prompt("Grund (optional):") || undefined })}>Zurückweisen</button>
            : <button className="btn-ghost !py-1.5" onClick={() => review("reopen")}>Wiederherstellen</button>}
          <button className="btn-ghost ml-auto !py-1.5 text-xs" onClick={toggleHistory}>{history ? "Historie ausblenden" : "Historie"}</button>
        </div>
      )}
      {!reviewable && <button className="btn-ghost mt-3 !py-1 text-xs" onClick={toggleHistory}>{history ? "Historie ausblenden" : "Historie"}</button>}
      {history?.revisions && (
        <ol className="mt-3 space-y-1 rounded-xl bg-canvas p-3 text-xs">
          {history.revisions.map((r) => (
            <li key={r.version}><b>v{r.version}</b> · {r.action} · {r.actor} · {fmtDate(r.at)}{r.comment ? ` – „${r.comment}“` : ""}</li>
          ))}
          <li className="pt-1 text-ink-3">Original-Extraktion: „{String(history.original_extraction?.content || "")}“</li>
        </ol>
      )}
    </div>
  );
}

function TrainingView({ t }: { t: TrainingPackage }) {
  const [slide, setSlide] = useState(0);
  const [answers, setAnswers] = useState<Record<number, number>>({});
  const c = t.content;
  const s = c.slides[slide];
  return (
    <div className="mt-10">
      <PageHeader eyebrow={`Trainingspaket · ${c.audience} · ${fmtDate(t.created_at)}`} title={c.title}
        subtitle={c.learning_objectives.join(" · ")} />
      <div className="grid gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <div className="card flex aspect-[16/9] flex-col justify-center bg-gradient-to-br from-white to-canvas p-10">
            <div className="eyebrow mb-3">Folie {slide + 1} / {c.slides.length}</div>
            <h3 className="text-3xl font-semibold tracking-tight">{s.title}</h3>
            <ul className="mt-6 space-y-2 text-lg text-ink-2">{s.bullets.map((b, i) => <li key={i}>— {b}</li>)}</ul>
            <div className="mt-auto pt-6 text-[11px] text-ink-3">Quellen: {s.source_unit_ids.join(", ") || "–"}</div>
          </div>
          <div className="mt-3 flex items-center justify-between">
            <button className="btn-secondary" disabled={slide === 0} onClick={() => setSlide(slide - 1)}>Zurück</button>
            <div className="flex gap-1">{c.slides.map((_, i) => <span key={i} className={`h-1.5 w-1.5 rounded-full ${i === slide ? "bg-ink" : "bg-zinc-300"}`} />)}</div>
            <button className="btn-secondary" disabled={slide === c.slides.length - 1} onClick={() => setSlide(slide + 1)}>Weiter</button>
          </div>
          <div className="card mt-4 p-5"><div className="eyebrow mb-1">Sprechertext</div><p className="text-sm leading-relaxed text-ink-2">{s.speaker_notes}</p></div>
        </div>
        <div className="card p-6 lg:col-span-2">
          <h3 className="mb-4 font-semibold">Wissenscheck</h3>
          <ol className="space-y-5">
            {c.quiz.map((q, qi) => (
              <li key={qi}>
                <div className="text-sm font-medium">{qi + 1}. {q.question}</div>
                <div className="mt-2 space-y-1.5">
                  {q.options.map((o, oi) => {
                    const chosen = answers[qi];
                    const show = chosen !== undefined;
                    const tone = !show ? "border-line hover:bg-canvas" : oi === q.correct_index ? "border-emerald-400 bg-emerald-50" : oi === chosen ? "border-red-300 bg-red-50" : "border-line opacity-60";
                    return <button key={oi} disabled={show} onClick={() => setAnswers({ ...answers, [qi]: oi })} className={`block w-full rounded-xl border px-3 py-2 text-left text-sm ${tone}`}>{o}</button>;
                  })}
                </div>
                {answers[qi] !== undefined && <p className="mt-2 text-xs text-ink-2">{q.explanation} <span className="text-ink-3">({q.source_unit_ids.join(", ")})</span></p>}
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}

"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, fmtTime } from "@/lib/api";
import type { InterviewState, KnowledgeSearchResult, KnowledgeSearchState, Turn } from "@/lib/types";
import { KnowledgeChips, KnowledgeHits, type SearchState } from "@/components/KnowledgeHits";
import { useInterviewSocket } from "@/lib/useInterviewSocket";
import { useVoice, type MicState, type VoiceEngine } from "@/lib/useVoice";
import {
  Badge, Bar, CATEGORY_LABEL, ConfidenceBadge, CoverageGrid, ErrorNote, Ring, Spinner, StatusBadge, TECHNIQUE_LABEL,
} from "@/components/ui";

const MIC_LABEL: Record<MicState, string> = {
  off: "Sprachmodus aus", idle: "Bereit", listening: "Ich höre zu …", recording: "Aufnahme läuft",
  processing: "Verarbeite Antwort …", ai_speaking: "KI spricht …", error: "Mikrofonfehler",
};
const PHASE_LABEL: Record<string, string> = {
  preparation: "Vorbereitung", opening: "Einstieg", exploration: "Exploration", validation: "Validierung", closing: "Abschluss", done: "Beendet",
};
const TOPIC_STATUS: Record<string, string> = { open: "offen", in_progress: "aktiv", saturated: "gesättigt", limit_reached: "Wissensgrenze" };

export default function Workspace() {
  const { id } = useParams<{ id: string }>();
  const [iv, setIv] = useState<InterviewState | null>(null);
  const [fetchedAt, setFetchedAt] = useState(Date.now());
  const [now, setNow] = useState(Date.now());
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [summarizing, setSummarizing] = useState(false);
  const [text, setText] = useState("");
  const [consent, setConsent] = useState(false);
  const [consentText, setConsentText] = useState<string[]>([]);
  const [engine, setEngine] = useState<VoiceEngine>("browser");
  const [engineName, setEngineName] = useState("browser");
  const [ttsMode, setTtsMode] = useState<"browser" | "server">("browser");
  const [silenceMs, setSilenceMs] = useState(1800);
  // Ausgabe: false = Voice/Text in → Text out (Standard), true = KI liest Fragen zusätzlich vor
  const [readAloud, setReadAloud] = useState(false);
  const [bargeIn, setBargeIn] = useState(false);
  useEffect(() => { setBargeIn(localStorage.getItem("ekep.bargeIn") === "1"); }, []);
  useEffect(() => { setReadAloud(localStorage.getItem("ekep.readAloud") !== "0"); }, []);
  const [showWhy, setShowWhy] = useState(false);
  const [needsRegen, setNeedsRegen] = useState(false);
  const spokenRef = useRef<string | null>(null);
  const transcriptBox = useRef<HTMLDivElement>(null);
  // Wissensabgleich: Vektorsuche je Expertenantwort (läuft parallel im Backend)
  const [searches, setSearches] = useState<Record<string, SearchState>>({});

  const applyState = useCallback((s: InterviewState) => {
    setIv(s);
    setFetchedAt(Date.now());
    setNeedsRegen(s.status === "in_progress" && s.turns.length > 0 && s.turns[s.turns.length - 1].speaker === "expert");
  }, []);

  const refresh = useCallback(() => api.get<InterviewState>(`/api/interviews/${id}`).then(applyState).catch((e) => setErr(e.message)), [id, applyState]);

  useEffect(() => {
    api.get<KnowledgeSearchState>(`/api/interviews/${id}/knowledge-search`)
      .then((k) => setSearches(Object.fromEntries(k.searches.map((x) => [x.turn_id, x]))))
      .catch(() => {});
  }, [id]);

  useEffect(() => {
    refresh();
    api.get<{ consent_text: string[]; stt_engine: string; tts_engine: string }>("/api/config/public").then((c) => {
      setConsentText(c.consent_text);
      setEngine(c.stt_engine === "browser" ? "browser" : "openai"); // openai | gemini = serverseitige Erkennung
      setTtsMode(c.tts_engine === "browser" ? "browser" : "server");
      setEngineName(`${c.stt_engine}/${c.tts_engine}`);
    }).catch(() => {});
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [refresh]);

  const addTurn = useCallback((t: Turn) => {
    setIv((prev) => (prev && !prev.turns.some((x) => x.id === t.id) ? { ...prev, turns: [...prev.turns, t], current_question: t.speaker === "ai" ? t.text : prev.current_question, current_technique: t.speaker === "ai" ? t.technique : prev.current_technique } : prev));
  }, []);

  const conn = useInterviewSocket(id, (event, data) => {
    if (event === "state") applyState(data);
    else if (event === "turn_added") addTurn(data);
    else if (event === "status_changed") setIv((p) => (p ? { ...p, ...data } : p));
    else if (event === "analysis_started") setAnalyzing(true);
    else if (event === "analysis_updated") { setAnalyzing(false); setIv((p) => (p ? { ...data.state, turns: p.turns.length > data.state.turns.length ? p.turns : data.state.turns } : data.state)); }
    else if (event === "analysis_error") { setAnalyzing(false); setErr(`Analyse fehlgeschlagen: ${data.message}`); }
    else if (event === "summary_started") setSummarizing(true);
    else if (event === "knowledge_search_started") setSearches((m) => ({ ...m, [data.turn_id]: m[data.turn_id] ?? "pending" }));
    else if (event === "knowledge_search_results") setSearches((m) => ({ ...m, [data.turn_id]: data as KnowledgeSearchResult }));
    else if (event === "error") setErr(data.message);
  });

  // ---------------- Voice
  const sendAudio = async (blob: Blob, durationS: number, ext: string) => {
    const fd = new FormData();
    fd.append("audio", blob, `answer.${ext}`);
    fd.append("duration_s", String(durationS.toFixed(1)));
    try {
      const r = await api.upload<{ expert_turn: Turn; ai_turn: Turn; finished: boolean }>(`/api/interviews/${id}/answer/audio`, fd);
      addTurn(r.expert_turn);
      addTurn(r.ai_turn);
      setNeedsRegen(false);
    } catch (e: any) {
      if (e instanceof ApiError && e.status === 422) {
        voice.setError("Ich habe nichts verstanden – bitte noch einmal sprechen.");
      } else {
        setErr(e.message);
        await refresh();
      }
      voice.listen();
    }
  };
  const sendSpokenText = async (spoken: string, durationS: number) => {
    try {
      const r = await api.post<{ expert_turn: Turn; ai_turn: Turn; finished: boolean }>(`/api/interviews/${id}/answer`,
        { text: spoken, mode: "voice", duration_s: Number(durationS.toFixed(1)) });
      addTurn(r.expert_turn);
      addTurn(r.ai_turn);
      setNeedsRegen(false);
    } catch (e: any) {
      setErr(e.message);
      await refresh();
      voice.listen();
    }
  };
  const voice = useVoice({ engine, tts: ttsMode, bargeIn, onUtterance: sendAudio, onText: sendSpokenText, silenceMs });

  const last = iv?.turns[iv.turns.length - 1];
  useEffect(() => {
    if (!iv || !last || last.speaker !== "ai") return;
    if (voice.state === "off" || voice.state === "error") return;
    if (spokenRef.current === last.id) return;
    if (iv.status !== "in_progress" && last.technique !== "closing") return;
    spokenRef.current = last.id;
    if (readAloud) voice.speak(last.text);
    else voice.listen(); // Text out: Frage nur anzeigen, sofort zuhören
  }, [last?.id, voice.state, iv?.status]); // eslint-disable-line

  useEffect(() => {
    if (iv?.status === "paused" || iv?.status === "completed" || iv?.status === "in_review") {
      if (voice.state === "listening" || voice.state === "recording") voice.hold();
    }
  }, [iv?.status]); // eslint-disable-line

  // Transkript scrollt nur innerhalb seines Kastens; bei neuer KI-Frage springt die Seite nach oben zur Frage
  useEffect(() => {
    const box = transcriptBox.current;
    if (box) box.scrollTo({ top: box.scrollHeight, behavior: "smooth" });
    if (last?.speaker === "ai") window.scrollTo({ top: 0, behavior: "smooth" });
  }, [iv?.turns.length]); // eslint-disable-line

  // ---------------- Actions
  const act = async (label: string, fn: () => Promise<any>) => {
    setBusy(label);
    setErr(null);
    try { const r = await fn(); if (r && r.id && r.turns) applyState(r); } catch (e: any) { setErr(e.message); await refresh(); } finally { setBusy(null); }
  };
  const start = () => act("Interview wird eröffnet …", async () => {
    await voice.enable();
    await api.post(`/api/interviews/${id}/consent`);
    return api.post<InterviewState>(`/api/interviews/${id}/start`);
  });
  const sendText = () => {
    const t = text.trim();
    if (!t) return;
    setText("");
    voice.hold();
    act("KI denkt nach …", async () => {
      const r = await api.post<{ expert_turn: Turn; ai_turn: Turn }>(`/api/interviews/${id}/answer`, { text: t });
      addTurn(r.expert_turn); addTurn(r.ai_turn); setNeedsRegen(false);
    });
  };
  const pause = () => { voice.hold(); act("Pausiere …", () => api.post(`/api/interviews/${id}/pause`)); };
  const resume = () => act("Setze fort …", () => api.post(`/api/interviews/${id}/resume`));
  const finish = () => {
    if (!confirm("Interview jetzt beenden und Zusammenfassung erstellen?")) return;
    voice.hold();
    setSummarizing(true);
    act("Zusammenfassung wird erstellt …", () => api.post(`/api/interviews/${id}/finish`)).finally(() => setSummarizing(false));
  };
  const regenerate = () => act("Neue Frage wird generiert …", async () => { await api.post(`/api/interviews/${id}/regenerate`); setNeedsRegen(false); });
  const toggleVoice = async () => {
    if (voice.state === "off" || voice.state === "error") {
      spokenRef.current = null;
      await voice.enable();
    } else voice.disable();
  };

  if (!iv) return <ErrorNote message={err} />;
  const topics = iv.plan?.topics || [];
  const elapsed = iv.elapsed_seconds + (iv.status === "in_progress" ? (now - fetchedAt) / 1000 : 0);
  const openGaps = iv.gaps.filter((g) => g.status === "open");
  const openCons = iv.contradictions.filter((c) => c.status === "open");
  const currentTopic = topics.find((t) => t.id === iv.current_topic_id);
  const lastAi = [...iv.turns].reverse().find((t) => t.speaker === "ai");
  const lastExpert = [...iv.turns].reverse().find((t) => t.speaker === "expert");

  // ================= Vorbereitung
  if (iv.status === "planned") {
    return (
      <div className="mx-auto max-w-4xl">
        <Header iv={iv} conn={conn} elapsed={elapsed} />
        <ErrorNote message={err} onClose={() => setErr(null)} />
        <div className="grid gap-6 md:grid-cols-5">
          <div className="card p-6 md:col-span-3">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="font-semibold">Individueller Interviewplan</h2>
              <button className="btn-ghost text-xs" disabled={!!busy} onClick={() => act("Plan wird neu erstellt …", () => api.post(`/api/interviews/${id}/plan`))}>
                {topics.length ? "Neu planen" : "Plan erstellen"}
              </button>
            </div>
            {iv.plan?.strategy && <p className="mb-4 text-sm text-ink-2">{iv.plan.strategy}</p>}
            <ol className="space-y-3">
              {topics.map((t) => (
                <li key={t.id} className="rounded-xl bg-canvas/60 p-4">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-ink-3">{t.id}</span>
                    <span className="font-medium">{t.title}</span>
                    <Badge tone={t.priority === "hoch" ? "bg-brand-soft text-brand" : undefined}>{t.priority}</Badge>
                  </div>
                  <p className="mt-1 text-sm text-ink-2">{t.description}</p>
                  <p className="mt-2 text-xs italic text-ink-3">„{t.opening_question}“</p>
                </li>
              ))}
            </ol>
          </div>
          <div className="card h-fit p-6 md:col-span-2">
            <h2 className="mb-3 font-semibold">Information & Einwilligung</h2>
            <ul className="mb-4 space-y-2 text-sm text-ink-2">
              {consentText.map((c) => <li key={c} className="flex gap-2"><span className="text-ink-3">•</span>{c}</li>)}
            </ul>
            <label className="mb-4 flex items-start gap-2 text-sm">
              <input type="checkbox" className="mt-1" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
              Ich habe die Informationen gelesen und willige in das KI-gestützte Interview ein.
            </label>
            <button className="btn-brand w-full" disabled={!consent || !topics.length || !!busy} onClick={start}>
              {busy ? <><Spinner /> {busy}</> : "Einwilligen & Interview starten"}
            </button>
            <p className="mt-3 text-[11px] text-ink-3">Mikrofonzugriff wird beim Start angefragt. Texteingabe ist jederzeit möglich.</p>
          </div>
        </div>
      </div>
    );
  }

  // ================= Abgeschlossen
  if (["completed", "in_review", "approved"].includes(iv.status)) {
    return (
      <div className="mx-auto max-w-3xl">
        <Header iv={iv} conn={conn} elapsed={elapsed} />
        <ErrorNote message={err} onClose={() => setErr(null)} />
        <div className="card p-8 text-center">
          {summarizing || (iv.status === "completed" && !iv.summary) ? (
            <div className="flex flex-col items-center gap-3 text-ink-2"><Spinner className="h-6 w-6" />Zusammenfassung und letzte Analyse werden erstellt …</div>
          ) : (
            <>
              <h2 className="text-2xl font-semibold">Interview abgeschlossen</h2>
              <p className="mx-auto mt-2 max-w-xl text-ink-2">{iv.summary?.executive_summary}</p>
              <div className="mt-6 flex justify-center gap-8 text-sm">
                <div><div className="text-2xl font-semibold">{iv.unit_count}</div><div className="text-ink-3">Wissenseinheiten</div></div>
                <div><div className="text-2xl font-semibold">{iv.open_gap_count}</div><div className="text-ink-3">offene Lücken</div></div>
                <div><div className="text-2xl font-semibold">{fmtTime(iv.elapsed_seconds)}</div><div className="text-ink-3">Dauer</div></div>
              </div>
              <Link href={`/interviews/${id}/review`} className="btn-primary mt-8">Zum Expert Review</Link>
            </>
          )}
        </div>
      </div>
    );
  }

  // ================= Laufendes Interview
  const micOn = voice.state !== "off" && voice.state !== "error";
  const paused = iv.status === "paused";
  return (
    <>
      <Header iv={iv} conn={conn} elapsed={elapsed}
        actions={
          <>
            {paused ? <button className="btn-primary" disabled={!!busy} onClick={resume}>Fortsetzen</button>
              : <button className="btn-secondary" disabled={!!busy} onClick={pause}>Pausieren</button>}
            <button className="btn-secondary" disabled={!!busy} onClick={finish}>Beenden</button>
          </>
        } />
      <ErrorNote message={err} onClose={() => setErr(null)} />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {/* Aktuelle Frage */}
          <div className="card p-8">
            {iv.progress && <InterviewProgress p={iv.progress} phase={iv.phase} />}
            <div className="mb-4 flex flex-wrap items-center gap-2">
              {iv.current_technique && <Badge tone="bg-ink text-white">{TECHNIQUE_LABEL[iv.current_technique] || iv.current_technique}</Badge>}
              {currentTopic && <Badge>{currentTopic.id} · {currentTopic.title}</Badge>}
              <Badge>{PHASE_LABEL[iv.phase] || iv.phase}</Badge>
              {lastAi?.rationale && <button onClick={() => setShowWhy(!showWhy)} className="ml-auto text-xs text-ink-3 hover:text-ink">Warum diese Frage?</button>}
            </div>
            <p className={`text-2xl font-medium leading-snug tracking-tight ${paused ? "text-ink-3" : ""}`}>
              {paused ? "Interview pausiert. Fortsetzen, wenn Sie bereit sind." : iv.current_question || "…"}
            </p>
            {showWhy && lastAi?.rationale && <p className="fade-in mt-4 rounded-xl bg-canvas p-3 text-sm text-ink-2">{lastAi.rationale}</p>}

            {/* Voice-Steuerung */}
            <div className="mt-8 flex flex-wrap items-center gap-6">
              <MicButton state={voice.state} level={voice.level} disabled={paused}
                onClick={() => {
                  if (!micOn) return toggleVoice();
                  if (voice.state === "recording") return voice.finishNow();
                  if (voice.state === "ai_speaking") return voice.listen();
                  if (voice.state === "idle" || voice.state === "listening") return voice.startNow(); // manuell aufnehmen
                }} />
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{busy || MIC_LABEL[voice.state]}</div>
                <div className="mt-1 text-xs text-ink-3">
                  {voice.state === "recording" ? "Sprechpause beendet die Antwort automatisch – oder Mikro antippen." :
                    voice.state === "ai_speaking" ? (bargeIn ? "Sie können die KI jederzeit unterbrechen." : "Bitte ausreden lassen – danach hört das Mikrofon automatisch zu.") :
                    micOn ? "Sprechen Sie frei. Nach einer kurzen Pause wird Ihre Antwort automatisch gesendet." : "Mikrofon aktivieren oder unten tippen."}
                </div>
                {micOn && (
                  <div className="mt-2 flex items-center gap-2" title="Mikrofonpegel">
                    <div className="h-1.5 w-32 rounded-full bg-zinc-100">
                      <div className={`h-1.5 rounded-full ${voice.state === "recording" ? "bg-brand" : "bg-ink-3"}`}
                        style={{ width: `${Math.min(100, Math.round(voice.level * 900))}%` }} />
                    </div>
                    {voice.state === "listening" && <span className="text-[11px] text-ink-3">Startet nicht? Mikrofon antippen.</span>}
                  </div>
                )}
                {voice.interim && <div className="fade-in mt-2 rounded-lg bg-canvas px-3 py-2 text-sm italic text-ink-2">„{voice.interim}“</div>}
                {voice.error && <div className="mt-2 text-xs text-amber-700">{voice.error}</div>}
              </div>
              <div className="flex flex-col items-end gap-2">
                <button className="btn-ghost text-xs" onClick={toggleVoice}>{micOn ? "Mikrofon aus" : "Mikrofon an"}</button>
                <label className="flex items-center gap-2 text-[11px] text-ink-2">
                  <input type="checkbox" checked={readAloud} onChange={(e) => {
                    setReadAloud(e.target.checked);
                    localStorage.setItem("ekep.readAloud", e.target.checked ? "1" : "0");
                  }} />
                  Fragen vorlesen
                </label>
                <label className="flex items-center gap-2 text-[11px] text-ink-2" title="Nur mit Headset – über Lautsprecher würde die KI-Stimme als Antwort aufgenommen">
                  <input type="checkbox" checked={bargeIn} onChange={(e) => {
                    setBargeIn(e.target.checked);
                    localStorage.setItem("ekep.bargeIn", e.target.checked ? "1" : "0");
                  }} />
                  Unterbrechen erlauben (Headset)
                </label>
                <label className="flex items-center gap-2 text-[11px] text-ink-3" title="Wie lange eine Sprechpause dauern darf, bevor die Antwort abgeschickt wird">
                  Pause {(silenceMs / 1000).toFixed(1)}s
                  <input type="range" min={1000} max={4000} step={200} value={silenceMs} onChange={(e) => setSilenceMs(Number(e.target.value))} />
                </label>
              </div>
            </div>

            {needsRegen && !busy && (
              <div className="mt-4 flex items-center justify-between rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-800">
                Ihre Antwort ist gespeichert, die nächste Frage konnte nicht erzeugt werden.
                <button className="btn-secondary !py-1" onClick={regenerate}>Erneut versuchen</button>
              </div>
            )}

            <div className="mt-6 flex gap-2">
              <textarea rows={2} value={text} disabled={paused || !!busy} onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) sendText(); }}
                placeholder="Oder Antwort eintippen … (⌘/Strg + Enter)" className="input resize-none" />
              <button className="btn-primary self-end" disabled={!text.trim() || paused || !!busy} onClick={sendText}>Senden</button>
            </div>

            {lastExpert && searches[lastExpert.id] && (
              <div className="mt-5">
                <div className="eyebrow mb-1">Wissensabgleich zu Ihrer letzten Antwort</div>
                <p className="mb-1 line-clamp-1 text-xs italic text-ink-3">„{lastExpert.text}“</p>
                <KnowledgeHits result={searches[lastExpert.id]} />
              </div>
            )}
          </div>

          {/* Transkript */}
          <div className="card p-6">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="font-semibold">Live-Transkript</h2>
              <span className="text-xs text-ink-3">{iv.turns.length} Beiträge · Audio wird nicht gespeichert · Erkennung/Stimme: {engineName}</span>
            </div>
            <div ref={transcriptBox} className="max-h-[420px] space-y-4 overflow-y-auto pr-2">
              {iv.turns.map((t) => (
                <div key={t.id} className={`fade-in flex ${t.speaker === "expert" ? "flex-col items-end text-right" : "gap-3"}`}>
                  <div className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm ${t.speaker === "ai" ? "bg-canvas" : "bg-ink text-white"}`}>
                    <div className={`mb-1 text-[10px] ${t.speaker === "ai" ? "text-ink-3" : "text-white/60"}`}>
                      {t.speaker === "ai" ? "KI-Interviewer" : iv.employee_name} · {fmtTime(t.t_start)}
                      {t.technique && ` · ${TECHNIQUE_LABEL[t.technique] || t.technique}`}
                      {t.input_mode === "voice" && " · 🎙"}
                      {t.redactions > 0 && ` · ${t.redactions}× maskiert`}
                    </div>
                    {t.text}
                  </div>
                  {t.speaker === "expert" && (
                    <div className="w-full max-w-[85%]">
                      <KnowledgeChips result={searches[t.id]} topic={topics.find((x) => x.id === t.topic_id)?.title} />
                    </div>
                  )}
                </div>
              ))}
              {busy === "KI denkt nach …" || voice.state === "processing" ? (
                <div className="flex items-center gap-2 text-xs text-ink-3"><Spinner /> KI wählt die nächste Frage …</div>
              ) : null}
            </div>
          </div>
        </div>

        {/* Seitenleiste */}
        <div className="space-y-6">
          <div className="card p-6">
            <div className="flex items-center gap-5">
              <Ring value={iv.coverage_score} label="Abdeckung" />
              <div className="flex-1 space-y-2 text-sm">
                <div className="flex justify-between"><span className="text-ink-3">Zeit</span><span>{fmtTime(elapsed)} / {iv.planned_minutes}:00</span></div>
                <Bar value={elapsed / (iv.planned_minutes * 60)} tone={elapsed > iv.planned_minutes * 60 ? "bg-brand" : "bg-ink"} />
                <div className="flex justify-between"><span className="text-ink-3">Wissenseinheiten</span><span>{iv.unit_count}</span></div>
                <div className="flex justify-between"><span className="text-ink-3">Offene Lücken</span><span>{openGaps.length}</span></div>
              </div>
            </div>
          </div>

          <div className="card p-6">
            <h3 className="mb-3 text-sm font-semibold">Themen</h3>
            <ul className="space-y-2.5">
              {topics.map((t) => (
                <li key={t.id} className="text-sm">
                  <div className="flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${t.id === iv.current_topic_id ? "bg-brand" : t.status === "saturated" ? "bg-emerald-500" : t.status === "limit_reached" ? "bg-amber-400" : "bg-zinc-300"}`} />
                    <span className={`flex-1 truncate ${t.id === iv.current_topic_id ? "font-medium" : ""}`}>{t.title}</span>
                    <span className="text-[11px] text-ink-3">{t.origin === "emerged" ? "neu · " : ""}{TOPIC_STATUS[t.status] || t.status}</span>
                  </div>
                  <div className="ml-4 mt-1"><Bar value={iv.topic_scores?.[t.id] || 0} tone="bg-zinc-400" /></div>
                </li>
              ))}
            </ul>
          </div>

          <div className="card p-6">
            <h3 className="mb-3 text-sm font-semibold">Offene Wissenslücken</h3>
            {openGaps.length === 0 ? <p className="text-xs text-ink-3">Noch keine erkannt.</p> : (
              <ul className="space-y-2">{openGaps.slice(-6).reverse().map((g) => (
                <li key={g.id} className="fade-in rounded-lg bg-amber-50/60 px-3 py-2 text-xs text-amber-900">
                  <span className="text-amber-600">{g.topic_id} · {g.dimension}</span><br />{g.description}
                </li>))}</ul>
            )}
            {openCons.length > 0 && (
              <>
                <h3 className="mb-2 mt-5 text-sm font-semibold">Mögliche Widersprüche</h3>
                <ul className="space-y-2">{openCons.map((c) => <li key={c.id} className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-800">{c.description}</li>)}</ul>
              </>
            )}
          </div>

          <div className="card p-6">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">Erkanntes Wissen</h3>
              {analyzing && <span className="flex items-center gap-1.5 text-[11px] text-ink-3"><Spinner className="h-3 w-3" />Analyse läuft</span>}
            </div>
            {iv.units.length === 0 ? <p className="text-xs text-ink-3">Wird während des Gesprächs extrahiert.</p> : (
              <ul className="space-y-3">
                {[...iv.units].reverse().slice(0, 6).map((u) => (
                  <li key={u.id} className="fade-in border-l-2 border-line pl-3">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-[10px] text-ink-3">{u.id}</span>
                      <Badge>{CATEGORY_LABEL[u.category]}</Badge>
                      <ConfidenceBadge level={u.confidence} reason={u.confidence_reason} />
                      {!u.source_refs.some((r) => r.verified) && <Badge tone="bg-red-50 text-red-700">Quelle unbestätigt</Badge>}
                    </div>
                    <div className="mt-1 text-sm font-medium">{u.title}</div>
                    <div className="text-xs text-ink-2 line-clamp-2">{u.content}</div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <details className="card p-6">
            <summary className="cursor-pointer text-sm font-semibold">Coverage-Matrix</summary>
            <div className="mt-4"><CoverageGrid iv={iv} compact /></div>
          </details>
        </div>
      </div>
    </>
  );
}

const GAIN_TONE: Record<string, string> = { hoch: "bg-emerald-500", mittel: "bg-sky-400", niedrig: "bg-zinc-300" };

function InterviewProgress({ p, phase }: { p: NonNullable<InterviewState["progress"]>; phase: string }) {
  const nearEnd = phase === "validation" || phase === "closing" || p.remaining_estimate <= 2;
  return (
    <div className="mb-6 rounded-2xl bg-canvas/70 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-ink-2">
        <span><b className="text-ink">Frage {p.questions_asked}</b> von max. {p.question_budget}</span>
        <span>noch ca. <b className="text-ink">{p.remaining_estimate}</b> {p.remaining_estimate === 1 ? "Frage" : "Fragen"}</span>
        <span>Themen abgeschlossen: <b className="text-ink">{p.topics_done}/{p.topics_total}</b></span>
        <span className="ml-auto flex items-center gap-1.5" title="Erkenntnisgewinn der letzten Antworten (grün = viel Neues, grau = kaum Neues → Thema gesättigt)">
          Erkenntnisgewinn
          {p.recent_gains.length === 0 ? <span className="text-ink-3">–</span> : p.recent_gains.map((g, i) => (
            <span key={i} className={`h-2.5 w-2.5 rounded-full ${GAIN_TONE[g]}`} />
          ))}
        </span>
        {nearEnd && <Badge tone="bg-brand-soft text-brand">Interview endet bald</Badge>}
      </div>
      <div className="mt-2"><Bar value={p.questions_asked / Math.max(1, p.question_budget)} tone={nearEnd ? "bg-brand" : "bg-ink"} /></div>
    </div>
  );
}

function Header({ iv, conn, elapsed, actions }: { iv: InterviewState; conn: string; elapsed: number; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <div className="eyebrow mb-1 flex items-center gap-2">
          <Link href={`/employees/${iv.employee_id}`} className="hover:text-ink">{iv.employee_name}</Link>
          <span>·</span>
          <span className={`inline-flex items-center gap-1 normal-case tracking-normal ${conn === "open" ? "text-emerald-600" : "text-amber-600"}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${conn === "open" ? "bg-emerald-500" : "bg-amber-500"}`} />
            {conn === "open" ? "verbunden" : conn === "reconnecting" ? "verbinde neu …" : "verbinde …"}
          </span>
        </div>
        <h1 className="text-2xl font-semibold tracking-tight">{iv.title}</h1>
        <div className="mt-1 flex items-center gap-2 text-sm text-ink-2"><StatusBadge status={iv.status} /> {fmtTime(elapsed)}</div>
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </div>
  );
}

function MicButton({ state, level, onClick, disabled }: { state: MicState; level: number; onClick: () => void; disabled?: boolean }) {
  const active = state === "recording";
  const scale = 1 + Math.min(0.35, level * 6);
  const tone = state === "recording" ? "bg-brand text-white" : state === "ai_speaking" ? "bg-ink text-white"
    : state === "listening" ? "bg-white text-ink border-2 border-ink" : state === "processing" ? "bg-zinc-200 text-ink-2" : "bg-canvas text-ink-2 border border-line";
  return (
    <button onClick={onClick} disabled={disabled} aria-label="Mikrofon" className="relative flex h-20 w-20 shrink-0 items-center justify-center disabled:opacity-40">
      {(state === "listening" || active) && <span className={`pulse-ring absolute inset-0 rounded-full ${active ? "bg-brand/30" : "bg-ink/10"}`} />}
      <span className={`relative flex h-16 w-16 items-center justify-center rounded-full transition ${tone}`} style={{ transform: active ? `scale(${scale})` : undefined }}>
        {state === "processing" ? <Spinner /> : state === "ai_speaking" ? (
          <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M3 10v4h4l5 4V6L7 10H3zm13.5 2a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4z" /></svg>
        ) : (
          <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2z" /></svg>
        )}
      </span>
    </button>
  );
}

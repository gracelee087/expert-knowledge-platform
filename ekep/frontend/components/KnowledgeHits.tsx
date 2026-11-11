"use client";
/** Kompakte Darstellung des Wissensabgleichs (Vektorsuche) zu einer Expertenantwort. */
import { useState } from "react";
import type { KnowledgeSearchResult, Reconciliation, Relation } from "@/lib/types";
import { Badge, Spinner } from "./ui";

export type SearchState = KnowledgeSearchResult | "pending" | undefined;

const SOURCE_LABEL = (r: KnowledgeSearchResult) =>
  r.provider === "http" ? "Vektordatenbank" : r.mode === "lexical" ? "Referenzbestand · Stichwortsuche" : "Referenzbestand · semantisch";

export function KnowledgeHits({ result, compact = false, align = "left" }: { result: SearchState; compact?: boolean; align?: "left" | "right" }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!result) return null;
  const wrap = `mt-2 rounded-xl border border-line/80 bg-white px-3 py-2 text-left text-xs ${align === "right" ? "ml-auto" : ""} ${compact ? "max-w-[85%]" : ""}`;

  if (result === "pending") {
    return <div className={wrap}><span className="flex items-center gap-2 text-ink-3"><Spinner className="h-3 w-3" />Suche in der Wissensbasis …</span></div>;
  }
  if (result.status === "error") {
    return <div className={wrap}><span className="text-amber-700">Wissensbasis nicht erreichbar – Abgleich wird am Ende nachgeholt.</span></div>;
  }
  if (result.hits.length === 0) {
    return (
      <div className={wrap}>
        <div className="flex items-center gap-2">
          <Badge tone="bg-emerald-50 text-emerald-700">Keine Treffer</Badge>
          <span className="text-ink-2">Vermutlich neues Wissen</span>
          <span className="ml-auto text-ink-3">{SOURCE_LABEL(result)} · {result.latency_ms} ms</span>
        </div>
      </div>
    );
  }
  return (
    <div className={wrap}>
      <div className="mb-1.5 flex items-center gap-2">
        <span className="font-medium text-ink">{result.hits.length} {result.hits.length === 1 ? "Treffer" : "Treffer"} in der Wissensbasis</span>
        <span className="ml-auto text-ink-3">{SOURCE_LABEL(result)} · {result.latency_ms} ms</span>
      </div>
      <ul className="space-y-1">
        {result.hits.map((h) => (
          <li key={h.id}>
            <button onClick={() => setOpen(open === h.id ? null : h.id)} className="flex w-full items-center gap-2 text-left hover:opacity-80">
              <span className="w-10 shrink-0 text-right tabular-nums text-ink-3">{Math.round(h.score * 100)}%</span>
              <span className="h-1 w-12 shrink-0 rounded-full bg-zinc-100"><span className="block h-1 rounded-full bg-sky-500" style={{ width: `${Math.min(100, h.score * 100)}%` }} /></span>
              <span className="truncate text-ink">{h.title}</span>
              <span className="shrink-0 text-ink-3">{h.id}</span>
            </button>
            {open === h.id && (
              <div className="fade-in mt-1 ml-[5.5rem] rounded-lg bg-canvas px-2 py-1.5 text-ink-2">
                {h.snippet}
                {h.source && <div className="mt-1 text-[10px] text-ink-3">Quelle: {h.source}</div>}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

const REL: Record<Relation, { label: string; tone: string; action: string }> = {
  neu: { label: "Neu", tone: "bg-emerald-50 text-emerald-700", action: "Neuer Eintrag" },
  ergaenzung: { label: "Ergänzung", tone: "bg-sky-50 text-sky-700", action: "Bestehenden Eintrag anpassen" },
  bestaetigung: { label: "Bereits bekannt", tone: "bg-zinc-100 text-zinc-600", action: "Keine Änderung" },
  widerspruch: { label: "Widerspruch", tone: "bg-red-50 text-red-700", action: "Fachlich klären" },
};

export function RelationBadge({ r }: { r?: Reconciliation }) {
  if (!r) return null;
  const m = REL[r.relation];
  return (
    <Badge tone={m.tone} title={`${m.action}${r.target_id ? ` · ${r.target_id} ${r.target_title || ""}` : ""} – ${r.explanation}`}>
      {m.label}{r.target_id ? ` · ${r.target_id}` : ""}
    </Badge>
  );
}

export function ReconciliationNote({ r }: { r?: Reconciliation }) {
  if (!r) return null;
  const m = REL[r.relation];
  return (
    <div className={`mt-3 rounded-lg px-3 py-2 text-xs ${r.relation === "widerspruch" ? "bg-red-50 text-red-800" : "bg-canvas text-ink-2"}`}>
      <b>Abgleich Wissensbasis: {m.label}</b> → {m.action}
      {r.target_id && <> ({r.target_id}{r.target_title ? ` · ${r.target_title}` : ""}{r.score != null ? ` · ${Math.round(r.score * 100)}%` : ""})</>}
      <div className="mt-0.5">{r.explanation}</div>
    </div>
  );
}

export function ReconciliationSummary({ s }: { s: { neu: number; ergaenzung: number; bestaetigung: number; widerspruch: number; total: number } }) {
  const cells: [Relation, number][] = [["neu", s.neu], ["ergaenzung", s.ergaenzung], ["bestaetigung", s.bestaetigung], ["widerspruch", s.widerspruch]];
  return (
    <div className="grid grid-cols-4 gap-2 text-center">
      {cells.map(([k, v]) => (
        <div key={k} className="rounded-xl bg-canvas px-2 py-3">
          <div className="text-xl font-semibold">{v}</div>
          <div className="text-[11px] text-ink-3">{REL[k].label}</div>
        </div>
      ))}
    </div>
  );
}

/** Kompakte Labels unter einer Chat-Antwort: Thema + Treffer-Status (Details stehen oben im Live-Kasten). */
export function KnowledgeChips({ result, topic }: { result: SearchState; topic?: string }) {
  const chip = "inline-flex max-w-[220px] items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px]";
  return (
    <div className="mt-1.5 flex flex-wrap justify-end gap-1.5">
      {topic && <span className={`${chip} border-line bg-white text-ink-2`} title="Thema"><span className="truncate">{topic}</span></span>}
      {result === "pending" && (
        <span className={`${chip} border-line bg-white text-ink-3`}><Spinner className="h-2.5 w-2.5" />sucht …</span>
      )}
      {result && result !== "pending" && result.status === "error" && (
        <span className={`${chip} border-amber-200 bg-amber-50 text-amber-700`} title={result.error || ""}>
          <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />Suche nicht erreichbar
        </span>
      )}
      {result && result !== "pending" && result.status === "ok" && result.hits.length === 0 && (
        <span className={`${chip} border-emerald-200 bg-emerald-50 text-emerald-700`} title="Kein vergleichbarer Eintrag in der Wissensbasis">
          <span className="h-1.5 w-1.5 rounded-full border border-emerald-500" />keine Treffer · neu
        </span>
      )}
      {result && result !== "pending" && result.status === "ok" && result.hits.map((h) => (
        <span key={h.id} className={`${chip} border-sky-200 bg-sky-50 text-sky-800`} title={`${h.id} · ${h.title}\n${h.snippet}`}>
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-sky-500" />
          <span className="truncate">{h.title}</span>
          <span className="shrink-0 tabular-nums text-sky-600">{Math.round(h.score * 100)}%</span>
        </span>
      ))}
    </div>
  );
}

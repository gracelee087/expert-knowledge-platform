"""Wissensabgleich: semantische Suche in der bestehenden Wissensbasis.

Ziel: Nach jeder Expertenantwort wird PARALLEL zum Interview gesucht, ob es dazu
bereits Wissen gibt. Am Interviewende wird jede Wissenseinheit klassifiziert:
neu / Ergänzung / bereits bekannt / Widerspruch → Empfehlung für die Vektordatenbank
(create / update / none / review). Die Vektordatenbank selbst gehört zu Phase 2
und wird hier NICHT gebaut – nur angesprochen.

Provider (KNOWLEDGE_SEARCH_PROVIDER):
  * local – In-Memory-Vektorsuche (Embeddings + Kosinus) über einen Referenzbestand
            (seed/reference_knowledge.json) plus bereits bestätigte Einheiten anderer
            Interviews. Fällt das Embedding-Modell aus → lexikalische Suche. Platzhalter,
            bis die Vektordatenbank angebunden ist.
  * http  – Vektordatenbank/Search-Service des RAG-Teams (Vertrag: docs/05-knowledge-search.md)
  * rag_service – GET {RAG_SERVICE_URL}/search des externen RAG-Service (services/rag_client.py)
  * off   – deaktiviert
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable, Protocol

import httpx
from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..models import CONFIRMED_STATUSES, Interview, KnowledgeSearch, KnowledgeUnit, UnitReconciliation
from .events import hub

log = logging.getLogger("ekep.search")

SEED_PATH = Path(__file__).resolve().parent.parent.parent / "seed" / "reference_knowledge.json"


@dataclass
class Hit:
    id: str
    title: str
    snippet: str
    score: float
    source: str = ""
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "snippet": self.snippet, "score": round(self.score, 3),
                "source": self.source, "metadata": self.metadata}


@dataclass
class SearchResult:
    hits: list[Hit]
    provider: str
    mode: str


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, top_k: int, exclude_interview_id: str | None = None) -> SearchResult: ...


# --------------------------------------------------------------------------
# Embeddings (OpenAI-kompatibel: OpenAI, Azure, hIP/embeddinggemma …)
# --------------------------------------------------------------------------
Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]
_embedder_override: Embedder | None = None


def set_embedder(fn: Embedder | None) -> None:
    """Dependency Injection (Tests / alternative Embedding-Dienste)."""
    global _embedder_override
    _embedder_override = fn
    _LOCAL.reset()


async def embed(texts: list[str]) -> list[list[float]]:
    if _embedder_override:
        return await _embedder_override(texts)
    from openai import AsyncOpenAI

    key = settings.embedding_api_key or settings.openai_api_key
    base = settings.embedding_base_url or settings.openai_base_url or "https://api.openai.com/v1"
    client = AsyncOpenAI(api_key=key, base_url=base, timeout=20, max_retries=2)
    resp = await client.embeddings.create(model=settings.embedding_model, input=texts)
    return [d.embedding for d in resp.data]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# --------------------------------------------------------------------------
# Lexikalischer Fallback (immer verfügbar, offline)
# --------------------------------------------------------------------------
_STOP = set("""der die das und oder aber ein eine einer eines einem einen ist sind war waren wird werden wurde
wurden bei beim mit von vom zu zum zur im in am an auf aus für fuer über ueber unter nach vor nicht kein keine
es er sie wir ich ihr man dann dass daß wenn wie was wer wo so auch noch nur schon sehr mehr hat haben hatte
dem den des sich als bis durch um ja nein also mal immer muss müssen kann können soll sollen""".split())


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-zäöüß0-9]{3,}", text.lower())
    return [w[:7] for w in words if w not in _STOP]  # einfaches Stemming über Präfix


def _lexical_score(q: Counter, d: Counter) -> float:
    common = set(q) & set(d)
    if not common:
        return 0.0
    dot = sum(q[t] * d[t] for t in common)
    return dot / (math.sqrt(sum(v * v for v in q.values())) * math.sqrt(sum(v * v for v in d.values())))


# --------------------------------------------------------------------------
# Lokaler Provider (Platzhalter bis zur Anbindung der Vektordatenbank)
# --------------------------------------------------------------------------
@dataclass
class _Doc:
    id: str
    title: str
    content: str
    source: str
    metadata: dict
    vec: list[float] | None = None
    toks: Counter | None = None

    @property
    def text(self) -> str:
        return f"{self.title}. {self.content}"


class LocalSearch:
    name = "local"

    def __init__(self) -> None:
        self._seed: list[_Doc] | None = None
        self._vec_cache: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()
        self._down_until = 0.0  # nach Embedding-Ausfall 60 s lexikalisch, dann erneut versuchen

    def reset(self) -> None:
        self._seed = None
        self._vec_cache = {}
        self._down_until = 0.0

    def _load_seed(self) -> list[_Doc]:
        if self._seed is None:
            path = Path(settings.reference_corpus_path) if settings.reference_corpus_path else SEED_PATH
            docs: list[_Doc] = []
            if path.is_file():
                raw = json.loads(path.read_text(encoding="utf-8"))
                for d in raw.get("documents", raw if isinstance(raw, list) else []):
                    meta = {k: v for k, v in d.items() if k not in ("id", "title", "content", "source")}
                    meta["origin"] = "referenzbestand"
                    docs.append(_Doc(str(d["id"]), d.get("title", ""), d.get("content", ""), d.get("source", ""), meta))
            self._seed = docs
        return self._seed

    def _confirmed_units(self, exclude_interview_id: str | None) -> list[_Doc]:
        """Bereits expertenbestätigtes Wissen aus früheren Interviews zählt als 'bestehend'."""
        with session_scope() as db:
            q = select(KnowledgeUnit).where(KnowledgeUnit.review_status.in_(list(CONFIRMED_STATUSES)))
            if exclude_interview_id:
                q = q.where(KnowledgeUnit.interview_id != exclude_interview_id)
            return [
                _Doc(u.id, u.title, u.content, f"Interview {u.interview_id}",
                     {"origin": "interview", "interview_id": u.interview_id, "version": u.version})
                for u in db.scalars(q)
            ]

    async def _vectors(self, docs: list[_Doc]) -> None:
        missing = [d for d in docs if d.vec is None]
        keys = {d.id: hashlib.sha1(f"{settings.embedding_model}|{d.text}".encode()).hexdigest() for d in missing}
        todo = [d for d in missing if keys[d.id] not in self._vec_cache]
        if todo:
            vecs = await embed([d.text for d in todo])
            for d, v in zip(todo, vecs):
                self._vec_cache[keys[d.id]] = v
        for d in missing:
            d.vec = self._vec_cache[keys[d.id]]

    async def search(self, query: str, top_k: int, exclude_interview_id: str | None = None) -> SearchResult:
        docs = list(self._load_seed()) + self._confirmed_units(exclude_interview_id)
        if not docs:
            return SearchResult([], self.name, "semantic")
        if time.monotonic() >= self._down_until:
            try:
                async with self._lock:
                    await self._vectors(docs)
                qv = (await embed([query]))[0]
                scored = sorted(((d, _cos(qv, d.vec)) for d in docs), key=lambda x: -x[1])
                hits = [Hit(d.id, d.title, d.content[:400], s, d.source, d.metadata)
                        for d, s in scored if s >= settings.search_min_score][:top_k]
                return SearchResult(hits, self.name, "semantic")
            except Exception as e:
                log.warning("Embeddings nicht verfügbar (%s) – lexikalische Suche", str(e)[:160])
                self._down_until = time.monotonic() + 60
        qt = Counter(_tokens(query))
        for d in docs:
            if d.toks is None:
                d.toks = Counter(_tokens(d.text))
        scored = sorted(((d, _lexical_score(qt, d.toks)) for d in docs), key=lambda x: -x[1])
        hits = [Hit(d.id, d.title, d.content[:400], s, d.source, d.metadata)
                for d, s in scored if s >= settings.search_min_score_lexical][:top_k]
        return SearchResult(hits, self.name, "lexical")


_LOCAL = LocalSearch()


# --------------------------------------------------------------------------
# HTTP-Provider: Vektordatenbank / Search-Service des RAG-Teams
# --------------------------------------------------------------------------
class HttpSearch:
    """Spricht den Search-Endpunkt der Vektordatenbank an.

    Erwarteter Vertrag (docs/05-knowledge-search.md), tolerant gegenüber Varianten:
      POST {VECTOR_SEARCH_URL}  {"query": str, "top_k": int, "filters": {...}}
      → {"results": [{"id", "title", "content", "score", "source", "metadata"}]}
    Akzeptiert auch "hits"/"matches"/"documents", "text"/"chunk"/"page_content",
    "similarity" oder "distance" (wird zu 1 - distance).
    """

    name = "http"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=settings.search_timeout_s)
        return self._client

    async def search(self, query: str, top_k: int, exclude_interview_id: str | None = None) -> SearchResult:
        if not settings.vector_search_url:
            raise RuntimeError("VECTOR_SEARCH_URL ist nicht gesetzt.")
        headers = {"Content-Type": "application/json"}
        if settings.vector_search_api_key:
            headers["Authorization"] = f"Bearer {settings.vector_search_api_key}"
        body = {"query": query, "top_k": top_k, "filters": json.loads(settings.vector_search_filters or "{}")}
        r = await self.client.post(settings.vector_search_url, json=body, headers=headers)
        r.raise_for_status()
        data = r.json()
        items = data if isinstance(data, list) else next(
            (data[k] for k in ("results", "hits", "matches", "documents", "data") if isinstance(data.get(k), list)), [])
        hits = []
        for i, it in enumerate(items[:top_k]):
            meta = it.get("metadata") or {}
            text = it.get("content") or it.get("text") or it.get("chunk") or it.get("page_content") or ""
            if "score" in it or "similarity" in it:
                score = float(it.get("score", it.get("similarity", 0)))
            elif "distance" in it:
                score = 1.0 - float(it["distance"])
            else:
                score = 0.0
            hits.append(Hit(str(it.get("id") or meta.get("id") or f"hit-{i}"),
                            it.get("title") or meta.get("title") or text[:60], text[:400], score,
                            it.get("source") or meta.get("source", ""), meta))
        return SearchResult(hits, self.name, "semantic")


# --------------------------------------------------------------------------
# RAG-Service-Provider: GET {RAG_SERVICE_URL}/search (Treffer mit Gültigkeitsstatus + Zitat)
# --------------------------------------------------------------------------
class RagServiceSearch:
    """Suche über den externen RAG-Service (services/rag_client.py); Stichtag = heute.

    Treffer: {"id","doc_id","title","text","score","validity_status","citation","valid_from","valid_to","language"}
    → Hit(title="<title> [<citation>] (<validity_status>)", source=citation, metadata=Rest).
    """

    name = "rag_service"

    async def search(self, query: str, top_k: int, exclude_interview_id: str | None = None) -> SearchResult:
        from datetime import date

        from . import rag_client

        if not rag_client.enabled():
            raise RuntimeError("RAG_SERVICE_URL ist nicht gesetzt.")
        items = await rag_client.search(query, top_k, date.today().isoformat(), None)
        hits = []
        for i, it in enumerate(items[:top_k]):
            text = str(it.get("text") or it.get("content") or "")
            title = it.get("title") or text[:60]
            citation, validity = it.get("citation") or "", it.get("validity_status") or ""
            label = f"{title}" + (f" [{citation}]" if citation else "") + (f" ({validity})" if validity else "")
            meta = {k: v for k, v in it.items() if k not in ("id", "title", "text", "content", "score")}
            meta["origin"] = "rag_service"
            hits.append(Hit(str(it.get("id") or it.get("doc_id") or f"hit-{i}"), label, text[:400],
                            float(it.get("score") or 0.0), citation or str(it.get("doc_id") or ""), meta))
        return SearchResult(hits, self.name, "semantic")


_provider_override: SearchProvider | None = None


def set_provider(p: SearchProvider | None) -> None:
    global _provider_override
    _provider_override = p


def get_provider() -> SearchProvider | None:
    if _provider_override:
        return _provider_override
    if settings.knowledge_search_provider == "off":
        return None
    if settings.knowledge_search_provider == "http":
        return HttpSearch()
    if settings.knowledge_search_provider == "rag_service":
        return RagServiceSearch()
    return _LOCAL


# --------------------------------------------------------------------------
# Live-Suche pro Antwort (parallel, blockiert das Interview nicht)
# --------------------------------------------------------------------------
_pending: dict[str, set[asyncio.Task]] = defaultdict(set)


def build_query(answer: str, question: str | None) -> str:
    """Kurze Antworten ('Ja, genau so') brauchen die Frage als Kontext."""
    if len(answer.split()) < 12 and question:
        return f"{question} {answer}"
    return answer


def schedule(interview_id: str, turn_id: str, answer: str, question: str | None) -> asyncio.Task | None:
    if get_provider() is None:
        return None
    task = asyncio.create_task(run_search(interview_id, turn_id, build_query(answer, question)))
    _pending[interview_id].add(task)
    task.add_done_callback(lambda t: _pending[interview_id].discard(t))
    return task


async def wait_idle(interview_id: str, timeout: float = 30) -> None:
    tasks = list(_pending.get(interview_id, ()))
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


async def run_search(interview_id: str, turn_id: str, query: str) -> dict:
    provider = get_provider()
    await hub.publish(interview_id, "knowledge_search_started", {"turn_id": turn_id})
    t0 = time.perf_counter()
    status, error, hits, mode = "ok", None, [], "semantic"
    try:
        res = await asyncio.wait_for(provider.search(query, settings.search_top_k, exclude_interview_id=interview_id),
                                     timeout=settings.search_timeout_s + 5)
        hits, mode = [h.as_dict() for h in res.hits], res.mode
    except Exception as e:
        status, error = "error", str(e)[:300]
        log.warning("Wissenssuche fehlgeschlagen: %s", error)
    latency = int((time.perf_counter() - t0) * 1000)
    with session_scope() as db:
        rec = KnowledgeSearch(interview_id=interview_id, turn_id=turn_id, query=query[:4000], provider=provider.name,
                              mode=mode, status=status, error=error, hits=hits, latency_ms=latency)
        db.add(rec)
        db.flush()
        out = search_out(rec)
    await hub.publish(interview_id, "knowledge_search_results", out)
    return out


def search_out(r: KnowledgeSearch) -> dict:
    return {"id": r.id, "turn_id": r.turn_id, "provider": r.provider, "mode": r.mode, "status": r.status,
            "error": r.error, "hits": r.hits or [], "latency_ms": r.latency_ms,
            "created_at": r.created_at.isoformat() if r.created_at else None}


# --------------------------------------------------------------------------
# Abgleich am Interviewende: neu / Ergänzung / bekannt / Widerspruch
# --------------------------------------------------------------------------
ACTION = {"neu": "create", "ergaenzung": "update", "bestaetigung": "none", "widerspruch": "review"}

RECONCILE_SYSTEM = """Du gleichst neu extrahierte Wissenseinheiten aus einem Experteninterview mit Treffern aus der
bestehenden Wissensbasis einer Bausparkasse ab. Für jede Einheit entscheidest du die Beziehung zum relevantesten Treffer:
- neu: Kein Treffer behandelt diesen Sachverhalt.
- ergaenzung: Ein Treffer behandelt denselben Sachverhalt, die Einheit fügt aber neue Details, Ausnahmen, Kriterien,
  Beispiele oder Praxiswissen hinzu (Anpassung des bestehenden Eintrags).
- bestaetigung: Die Einheit sagt inhaltlich dasselbe wie ein Treffer, ohne relevanten Mehrwert.
- widerspruch: Die Einheit widerspricht einem Treffer inhaltlich (z. B. andere Regel, andere Zuständigkeit, andere Frist).
Beurteile NICHT, welche Seite fachlich richtig ist – nur die Beziehung. matched_id nur aus den gelieferten Treffer-IDs.
Wenn die Treffer nur entfernt verwandt sind: neu. Sprache: Deutsch."""


async def reconcile(interview_id: str) -> list[dict]:
    from ..llm.provider import get_llm

    provider = get_provider()
    await wait_idle(interview_id)
    with session_scope() as db:
        units = [u for u in db.scalars(select(KnowledgeUnit).where(KnowledgeUnit.interview_id == interview_id))
                 if u.review_status != "rejected"]
        payload_units = [{"id": u.id, "title": u.title, "content": u.content, "explanation": u.explanation,
                          "exceptions": u.exceptions, "examples": u.examples} for u in units]
    if not units:
        return []
    await hub.publish(interview_id, "reconciliation_started", {"units": len(units)})

    candidates: dict[str, list[dict]] = {}
    if provider is not None:
        async def one(u):
            try:
                res = await provider.search(f"{u['title']}. {u['content']}", settings.search_top_k,
                                            exclude_interview_id=interview_id)
                return u["id"], [h.as_dict() for h in res.hits[:3]]
            except Exception as e:
                log.warning("Kandidatensuche für %s fehlgeschlagen: %s", u["id"], e)
                return u["id"], []
        candidates = dict(await asyncio.gather(*(one(u) for u in payload_units)))

    method = "llm"
    relations: dict[str, dict] = {}
    with_cands = [u for u in payload_units if candidates.get(u["id"])]
    if with_cands:
        try:
            user = json.dumps([{"einheit": u, "treffer": [{"id": h["id"], "title": h["title"], "inhalt": h["snippet"]}
                                                          for h in candidates[u["id"]]]} for u in with_cands],
                              ensure_ascii=False)
            from ..agents.schemas import ReconciliationResult

            result = await get_llm().structured(system=RECONCILE_SYSTEM, user=user, schema=ReconciliationResult,
                                                model=settings.analyst_model, temperature=0.0)
            for it in result.items:
                ids = {h["id"] for h in candidates.get(it.unit_id, [])}
                matched = it.matched_id if it.matched_id in ids else None
                rel = it.relation if (matched or it.relation == "neu") else "neu"
                relations[it.unit_id] = {"relation": rel, "target_id": matched, "explanation": it.explanation}
        except Exception as e:
            log.warning("LLM-Abgleich fehlgeschlagen (%s) – heuristischer Abgleich", e)
            method = "heuristic"

    out = []
    with session_scope() as db:
        for u in payload_units:
            cands = candidates.get(u["id"], [])
            rel = relations.get(u["id"])
            if rel is None:  # keine Treffer oder Heuristik
                top = cands[0] if cands else None
                strong = top and top["score"] >= settings.search_reconcile_threshold
                rel = {"relation": "ergaenzung" if strong else "neu", "target_id": top["id"] if strong else None,
                       "explanation": ("Ähnlicher Eintrag vorhanden – bitte prüfen, ob die Aussage ihn ergänzt."
                                       if strong else "Kein vergleichbarer Eintrag in der Wissensbasis gefunden.")}
                m = method if cands else "no_match"
            else:
                m = method
            target = next((c for c in cands if c["id"] == rel["target_id"]), None)
            rec = db.get(UnitReconciliation, u["id"]) or UnitReconciliation(unit_id=u["id"], interview_id=interview_id)
            rec.relation = rel["relation"]
            rec.action = ACTION[rel["relation"]]
            rec.target_id = rel["target_id"]
            rec.target_title = target["title"] if target else None
            rec.score = target["score"] if target else (cands[0]["score"] if cands else None)
            rec.explanation = rel["explanation"]
            rec.candidates = cands
            rec.method = m
            db.merge(rec)
            out.append(reconciliation_out(rec))
    await hub.publish(interview_id, "reconciliation_updated", {"items": out, "summary": summarize(out)})
    return out


def reconciliation_out(r: UnitReconciliation) -> dict:
    return {"unit_id": r.unit_id, "relation": r.relation, "action": r.action, "target_id": r.target_id,
            "target_title": r.target_title, "score": r.score, "explanation": r.explanation,
            "candidates": r.candidates or [], "method": r.method}


def summarize(items: list[dict]) -> dict:
    c = Counter(i["relation"] for i in items)
    return {"neu": c.get("neu", 0), "ergaenzung": c.get("ergaenzung", 0),
            "bestaetigung": c.get("bestaetigung", 0), "widerspruch": c.get("widerspruch", 0), "total": len(items)}


def reconciliations_for(db, interview_id: str) -> dict[str, dict]:
    rows = db.scalars(select(UnitReconciliation).where(UnitReconciliation.interview_id == interview_id))
    return {r.unit_id: reconciliation_out(r) for r in rows}


def searches_for(db, interview_id: str) -> list[dict]:
    rows = db.scalars(select(KnowledgeSearch).where(KnowledgeSearch.interview_id == interview_id)
                      .order_by(KnowledgeSearch.created_at))
    return [search_out(r) for r in rows]


def interview_exists(db, interview_id: str) -> bool:
    return db.get(Interview, interview_id) is not None

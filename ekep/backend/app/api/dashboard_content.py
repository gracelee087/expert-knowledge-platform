"""Adapter für das Dashboard-Frontend: Assistent (Frage an die Wissensbasis) und Content-Generator.

  POST /api/assistant/ask      → RAG /search + ein LLM-Aufruf → Zusammenfassung, Kernpunkte, Warnungen, Quellen
  POST /api/content/generate   → RAG /export?outline=true (+ Fallback aus den gelieferten Artikeln)
                                 + ein LLM-Aufruf (Quiz + Podcast) → GeneratedContent

Kein Auth-Dependency (das Frontend sendet keine Header). Beide Endpunkte liefern nie 500:
RAG- oder LLM-Fehler führen zu leeren Listen bzw. deterministischen Fallbacks.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, timezone

import httpx
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..config import settings
from ..llm.provider import get_llm

log = logging.getLogger("ekep.dashboard")

router = APIRouter(prefix="/api", tags=["dashboard"])

RAG_DEFAULT_URL = "http://localhost:8001"
TIMEOUT_SEARCH_S = 20.0
TIMEOUT_EXPORT_S = 120.0

STATUS_MAP = {"current": "aktuell", "possibly_outdated": "historisch", "not_yet_valid": "pruefung"}
AUDIENCE_MAP = {"Anfänger": "beginner", "Fortgeschrittene": "intermediate", "Experten": "expert"}


# --------------------------------------------------------------------------
# RAG-Zugriff
# --------------------------------------------------------------------------
def _rag_base() -> str:
    return (settings.rag_service_url or RAG_DEFAULT_URL).rstrip("/")


async def _rag_get(path: str, params: dict, timeout: float) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(_rag_base() + path, params=params)
            r.raise_for_status()
            data = r.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.warning("RAG %s fehlgeschlagen: %s", path, str(e)[:300])
        return None


def _today() -> str:
    return date.today().isoformat()


def _fmt_date(value: str | None) -> str:
    return value if value else "offen"


# --------------------------------------------------------------------------
# Gemeinsame Eingabe-Modelle
# --------------------------------------------------------------------------
class ArticleIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = ""
    title: str = ""
    content: list[str] = Field(default_factory=list)
    validFrom: str | None = None
    validUntil: str | None = None
    status: str | None = None


# ==========================================================================
# Contract A: POST /api/assistant/ask
# ==========================================================================
class AskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    question: str
    articles: list[ArticleIn] = Field(default_factory=list)


class AskPoint(BaseModel):
    title: str
    text: str


class AskSource(BaseModel):
    articleId: str
    title: str
    status: str
    validFrom: str
    validUntil: str | None
    relevance: int


class AskResponse(BaseModel):
    summary: str
    points: list[AskPoint]
    warnings: list[str]
    sources: list[AskSource]
    documents: list[str]
    confidence: int


class _AskLLM(BaseModel):
    summary: str = Field(description="Genau zwei Sätze auf Deutsch")
    points: list[AskPoint] = Field(description="Bis zu 4 Kernpunkte")


ASK_SYSTEM = """Du bist der Wissensassistent einer Bausparkasse. Du beantwortest Fragen von Mitarbeitenden
ausschließlich auf Grundlage der mitgelieferten Belege aus der Wissensbasis. Antworte auf Deutsch, sachlich und knapp.
Regeln:
- summary: genau zwei Sätze, die die Frage direkt beantworten.
- points: bis zu 4 Kernpunkte (title = kurze Überschrift, text = 1-2 Sätze), jeweils aus den Belegen abgeleitet.
- Belege mit Status possibly_outdated sind historische Regelungen: als solche kennzeichnen, nicht als aktuell darstellen.
- Belege mit Status not_yet_valid gelten noch nicht: als zukünftige Regelung kennzeichnen.
- Nichts erfinden. Wenn die Belege die Frage nur teilweise beantworten, sage das in der Zusammenfassung."""


def _chunk_block(hits: list[dict]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        text = " ".join(str(h.get("text") or "").split())[:900]
        lines.append(
            f"[{i}] {h.get('title') or h.get('doc_id')} | Status: {h.get('validity_status')} | "
            f"gültig {_fmt_date(h.get('valid_from'))} bis {_fmt_date(h.get('valid_to'))} | Quelle: {h.get('source')}\n{text}"
        )
    return "\n\n".join(lines)


def _warning_for(h: dict) -> str | None:
    status = h.get("validity_status")
    title = h.get("title") or h.get("doc_id") or "Dokument"
    if status == "possibly_outdated":
        return (f"„{title}“ ist eine historische Regelung (gültig von {_fmt_date(h.get('valid_from'))} "
                f"bis {_fmt_date(h.get('valid_to'))}) – nur für Altfälle anwenden.")
    if status == "not_yet_valid":
        return (f"„{title}“ gilt erst ab {_fmt_date(h.get('valid_from'))} – aktuell noch nicht anwenden.")
    if status and status != "current":
        return f"„{title}“ hat den Gültigkeitsstatus „{status}“ – vor Anwendung prüfen."
    return None


def _match_article_id(title: str, articles: list[ArticleIn]) -> str | None:
    t = (title or "").strip().lower()
    if not t:
        return None
    for a in articles:
        at = (a.title or "").strip().lower()
        if at and (at == t or at in t or t in at):
            return a.id or None
    return None


def _build_sources(hits: list[dict], articles: list[ArticleIn]) -> list[AskSource]:
    sources: list[AskSource] = []
    seen: set[str] = set()
    for h in hits:
        key = h.get("doc_id") or h.get("title") or h.get("id") or ""
        if not key or key in seen:
            continue
        seen.add(key)
        article_id = h.get("doc_id") or _match_article_id(h.get("title") or "", articles) or key
        rank = len(sources)
        sources.append(AskSource(
            articleId=str(article_id),
            title=str(h.get("title") or h.get("doc_id") or ""),
            status=STATUS_MAP.get(str(h.get("validity_status")), "pruefung"),
            validFrom=str(h.get("valid_from") or ""),
            validUntil=h.get("valid_to") or None,
            relevance=max(40, 100 - 12 * rank),
        ))
    return sources


@router.post("/assistant/ask", response_model=AskResponse)
async def assistant_ask(req: AskRequest) -> AskResponse:
    question = (req.question or "").strip()
    data = await _rag_get("/search", {"q": question, "k": 6, "as_of": _today(), "language": "de"}, TIMEOUT_SEARCH_S) \
        if question else None
    hits = [h for h in ((data or {}).get("hits") or []) if isinstance(h, dict)]
    # Dashboard-Wissensbasis bevorzugen: Treffer aus den Dashboard-Artikeln (doc_id = Artikel-id, Präfix "a-")
    # verdrängen Treffer aus anderen Korpora, sobald mindestens zwei Dashboard-Treffer vorliegen.
    known = {a.id for a in req.articles if a.id}
    dashboard_hits = [h for h in hits if str(h.get("doc_id") or "") in known or str(h.get("doc_id") or "").startswith("a-")]
    if len(dashboard_hits) >= 2:
        hits = dashboard_hits

    if not hits:
        return AskResponse(summary="Zu dieser Frage wurde in der Wissensbasis nichts Passendes gefunden.",
                           points=[], warnings=[], sources=[], documents=[], confidence=25)

    warnings: list[str] = []
    warned: set[str] = set()
    for h in hits:
        w = _warning_for(h)
        key = h.get("doc_id") or h.get("title") or ""
        if w and key not in warned:
            warned.add(key)
            warnings.append(w)

    try:
        llm = await get_llm().structured(
            system=ASK_SYSTEM,
            user=f"FRAGE: {question}\n\nBELEGE:\n{_chunk_block(hits)}",
            schema=_AskLLM,
            model=settings.analyst_model,
            temperature=0.2,
        )
        summary, points = llm.summary.strip(), llm.points[:4]
    except Exception as e:
        log.warning("assistant/ask LLM fehlgeschlagen: %s", str(e)[:300])
        top = hits[0]
        summary = (f"Zur Frage wurden Belege gefunden, die automatische Zusammenfassung ist jedoch fehlgeschlagen. "
                   f"Relevantester Beleg: „{top.get('title')}“.")
        points = [AskPoint(title=str(h.get("title") or ""), text=" ".join(str(h.get("text") or "").split())[:300])
                  for h in hits[:4]]

    documents: list[str] = []
    for h in hits:
        s = str(h.get("source") or "")
        if s and s not in documents:
            documents.append(s)

    return AskResponse(
        summary=summary,
        points=points,
        warnings=warnings,
        sources=_build_sources(hits, req.articles),
        documents=documents,
        confidence=85 if hits[0].get("validity_status") == "current" else 65,
    )


# ==========================================================================
# Contract B: POST /api/content/generate
# ==========================================================================
class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    format: str = "praesentation"
    audience: str = "Anfänger"
    difficulty: str = "Mittel"
    targetRoles: list[str] = Field(default_factory=list)
    articles: list[ArticleIn] = Field(default_factory=list)


class Slide(BaseModel):
    title: str
    bullets: list[str]
    notes: str


class Scene(BaseModel):
    title: str
    narration: str
    subtitle: str
    durationSec: int


class PodcastLine(BaseModel):
    speaker: str
    text: str


class QuizItem(BaseModel):
    id: str
    question: str
    options: list[str]
    correctIndex: int
    explanation: str


class GeneratedContent(BaseModel):
    id: str
    format: str
    title: str
    sourceArticleIds: list[str]
    audience: str
    difficulty: str
    createdAt: str
    learningGoals: list[str]
    slides: list[Slide]
    scenes: list[Scene]
    podcast: list[PodcastLine]
    quiz: list[QuizItem]
    targetRoles: list[str]


class _QuizLLM(BaseModel):
    question: str
    options: list[str] = Field(description="Genau 4 Antwortmöglichkeiten")
    correctIndex: int = Field(description="Index 0-3 der richtigen Antwort")
    explanation: str


class _PodcastLLM(BaseModel):
    speaker: str = Field(description="'Anna' (Moderatorin) oder 'Klaus' (Experte)")
    text: str


class _GenerateLLM(BaseModel):
    learning_goals: list[str] = Field(description="3-5 Lernziele auf Deutsch")
    quiz: list[_QuizLLM] = Field(description="Multiple-Choice-Fragen; Anzahl siehe ANZAHL QUIZFRAGEN (Standard 4-6)")
    podcast: list[_PodcastLLM] = Field(description="6-10 Dialogzeilen, abwechselnd Anna und Klaus, Anna beginnt")


class _GenerateQuizLLM(_GenerateLLM):
    """Quiz Generator: das Modell ignoriert eine Anzahl im Fließtext – daher explizit im Schema."""
    quiz: list[_QuizLLM] = Field(description="GENAU 20 Multiple-Choice-Fragen (nicht weniger): Mischung aus "
                                             "Verständnis-, Anwendungs- und Fallfragen, jede mit genau 4 Optionen")


GENERATE_SYSTEM = """Du erstellst Schulungsmaterial für Mitarbeitende einer Bausparkasse. Sprache: Deutsch.
Du erhältst einen Schulungsentwurf (Abschnitte mit Kernpunkten und Sprechertext), Zielgruppe und Schwierigkeitsgrad.
Erzeuge in EINEM Durchgang:
1. learning_goals: 3-5 Lernziele (nur falls im Entwurf keine stehen; sonst die vorhandenen leicht sprachlich glätten).
2. quiz: Multiple-Choice-Fragen (Anzahl: siehe ANZAHL QUIZFRAGEN in der Anfrage, sonst 4-6) mit genau 4 Optionen,
   correctIndex 0-3 (richtige Antwort nicht immer an Position 0),
   explanation = 1-2 Sätze mit Bezug auf den Abschnitt. Nutze die Quiz-Ideen, wenn vorhanden. Nur Inhalte aus dem Entwurf.
3. podcast: kurzer Dialog mit 6-10 Zeilen. Anna moderiert (stellt Fragen, fasst zusammen), Klaus ist der Experte
   (erklärt die Inhalte). Anna beginnt, Anna schließt. Jede Zeile 1-3 Sätze, gesprochene Sprache.
Passe Tiefe und Wortwahl an Zielgruppe und Schwierigkeitsgrad an. Keine Fakten erfinden, die nicht im Entwurf stehen."""


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ„\"(])")


def _first_sentence(paragraph: str) -> str:
    p = " ".join((paragraph or "").split())
    if not p:
        return ""
    return _SENT_SPLIT.split(p, maxsplit=1)[0].strip()


def _sentences(paragraph: str, n: int = 3) -> list[str]:
    p = " ".join((paragraph or "").split())
    return [s.strip() for s in _SENT_SPLIT.split(p) if s.strip()][:n]


def _duration(text: str) -> int:
    return max(8, round(len((text or "").split()) / 2.5))


def _sections_from_articles(articles: list[ArticleIn]) -> list[dict]:
    """Fallback: ein Abschnitt pro Absatz, Kernpunkte = erste Sätze."""
    sections: list[dict] = []
    for a in articles:
        paras = [p for p in (a.content or []) if p and p.strip()]
        for i, p in enumerate(paras, 1):
            title = a.title or "Abschnitt"
            if len(paras) > 1:
                title = f"{title} – Teil {i}"
            sections.append({"title": title, "key_points": _sentences(p, 3), "evidence_ids": [a.id] if a.id else [],
                             "speaker_script": " ".join(p.split())})
    return sections


def _usable_outline(export: dict | None) -> dict | None:
    outline = (export or {}).get("outline")
    if not isinstance(outline, dict):
        return None
    sections = [s for s in (outline.get("sections") or []) if isinstance(s, dict) and s.get("title")]
    if not sections:
        return None
    return {**outline, "sections": sections}


def _build_slides(title: str, sections: list[dict], goals: list[str], validity_notes: list[str],
                  audience: str) -> list[Slide]:
    slides = [Slide(title=title, bullets=(goals[:4] or [f"Zielgruppe: {audience}"]),
                    notes=f"Willkommen zur Schulung „{title}“. Zielgruppe: {audience}.")]
    for s in sections:
        points = [str(k) for k in (s.get("key_points") or []) if str(k).strip()]
        slides.append(Slide(title=str(s.get("title") or ""), bullets=points, notes=str(s.get("speaker_script") or "")))
    summary_bullets = [_first_sentence(str((s.get("key_points") or [""])[0])) for s in sections]
    summary_bullets = [b for b in summary_bullets if b][:6]
    slides.append(Slide(title="Zusammenfassung", bullets=summary_bullets,
                        notes=" ".join(validity_notes) if validity_notes else "Die wichtigsten Punkte im Überblick."))
    return slides


def _build_scenes(sections: list[dict]) -> list[Scene]:
    scenes = []
    for s in sections:
        narration = str(s.get("speaker_script") or "")
        points = [str(k) for k in (s.get("key_points") or []) if str(k).strip()]
        if not narration:
            narration = " ".join(points)
        scenes.append(Scene(title=str(s.get("title") or ""), narration=narration,
                            subtitle=points[0] if points else str(s.get("title") or ""),
                            durationSec=_duration(narration)))
    return scenes


def _normalize_quiz(items: list[_QuizLLM]) -> list[QuizItem]:
    out: list[QuizItem] = []
    for q in items:
        opts = [str(o).strip() for o in (q.options or []) if str(o).strip()][:4]
        if len(opts) < 2:
            continue
        while len(opts) < 4:
            opts.append("Keine der genannten Antworten")
        idx = q.correctIndex if 0 <= q.correctIndex < 4 else 0
        # Deterministisch rotieren: das Modell setzt die richtige Antwort sonst fast immer auf Position 0
        shift = len(out) % 4
        opts = opts[-shift:] + opts[:-shift] if shift else opts
        idx = (idx + shift) % 4
        out.append(QuizItem(id=f"q{len(out) + 1}", question=q.question.strip(), options=opts, correctIndex=idx,
                            explanation=q.explanation.strip()))
    return out


def _normalize_podcast(lines: list[_PodcastLLM]) -> list[PodcastLine]:
    out: list[PodcastLine] = []
    for i, line in enumerate(lines):
        sp = (line.speaker or "").strip().lower()
        speaker = "Anna" if sp.startswith("anna") else "Klaus" if sp.startswith("klaus") else ("Anna" if i % 2 == 0 else "Klaus")
        if line.text and line.text.strip():
            out.append(PodcastLine(speaker=speaker, text=line.text.strip()))
    return out


def _outline_block(title: str, sections: list[dict], goals: list[str], quiz_seeds: list[str]) -> str:
    parts = [f"TITEL: {title}", "LERNZIELE: " + ("; ".join(goals) if goals else "(keine – bitte erzeugen)")]
    for i, s in enumerate(sections, 1):
        kp = "; ".join(str(k) for k in (s.get("key_points") or []))
        parts.append(f"ABSCHNITT {i}: {s.get('title')}\nKERNPUNKTE: {kp}\nSPRECHERTEXT: "
                     + " ".join(str(s.get("speaker_script") or "").split())[:1500])
    if quiz_seeds:
        parts.append("QUIZ-IDEEN: " + "; ".join(str(q) for q in quiz_seeds))
    return "\n\n".join(parts)


@router.post("/content/generate", response_model=GeneratedContent)
async def content_generate(req: GenerateRequest) -> GeneratedContent:
    titles = [a.title for a in req.articles if a.title]
    topic = " – ".join(titles) if titles else "Schulung"
    audience_en = AUDIENCE_MAP.get(req.audience, "beginner")

    export = await _rag_get("/export", {"topic": topic, "outline": "true", "audience": audience_en, "lang": "de",
                                        "as_of": _today(), "k": 8}, TIMEOUT_EXPORT_S)
    outline = _usable_outline(export)

    if outline:
        title = str(outline.get("title") or topic)
        sections = outline["sections"]
        goals = [str(g) for g in (outline.get("learning_objectives") or []) if str(g).strip()]
        validity_notes = [str(v) for v in (outline.get("validity_notes") or []) if str(v).strip()]
        quiz_seeds = [str(q) for q in (outline.get("quiz_seeds") or []) if str(q).strip()]
    else:
        title = topic
        sections = _sections_from_articles(req.articles)
        goals, validity_notes, quiz_seeds = [], [], []
        for a in req.articles:
            if a.status and a.status != "aktuell":
                validity_notes.append(f"„{a.title}“ hat den Status „{a.status}“ (gültig {_fmt_date(a.validFrom)} "
                                      f"bis {_fmt_date(a.validUntil)}).")

    quiz: list[QuizItem] = []
    podcast: list[PodcastLine] = []
    if sections:
        user = (f"ZIELGRUPPE: {req.audience} | SCHWIERIGKEIT: {req.difficulty} | FORMAT: {req.format}\n"
                f"ROLLEN: {', '.join(req.targetRoles) or '-'}\n\n{_outline_block(title, sections, goals, quiz_seeds)}")
        is_quiz = req.format == "quiz"
        system = GENERATE_SYSTEM
        schema: type[_GenerateLLM] = _GenerateLLM
        if is_quiz:
            user = "ANZAHL QUIZFRAGEN: GENAU 20 (Mischung aus Verständnis-, Anwendungs- und Fallfragen)\n" + user
            system = GENERATE_SYSTEM.replace(
                "2. quiz: Multiple-Choice-Fragen (Anzahl: siehe ANZAHL QUIZFRAGEN in der Anfrage, sonst 4-6) mit genau 4 Optionen,",
                "2. quiz: GENAU 20 Multiple-Choice-Fragen (nicht weniger – zähle sie) mit genau 4 Optionen,")
            schema = _GenerateQuizLLM
        min_quiz = 10 if is_quiz else 4
        for attempt in range(2):
            try:
                llm = await get_llm().structured(system=system, user=user, schema=schema,
                                                 model=settings.analyst_model, temperature=0.4)
            except Exception as e:
                log.warning("content/generate LLM fehlgeschlagen: %s", str(e)[:300])
                break
            quiz = _normalize_quiz(llm.quiz)[:20 if is_quiz else 6]
            podcast = _normalize_podcast(llm.podcast)[:10]
            if not goals:
                goals = [g.strip() for g in llm.learning_goals if g.strip()][:5]
            if len(quiz) >= min_quiz:
                break
            user += (f"\n\nHINWEIS: Es wurden nur {len(quiz)} Quizfragen geliefert – es werden "
                     f"{'GENAU 20' if is_quiz else 'mindestens 4'} Quizfragen benötigt.")

    return GeneratedContent(
        id=f"gc-{uuid.uuid4().hex[:8]}",
        format=req.format,
        title=title,
        sourceArticleIds=[a.id for a in req.articles if a.id],
        audience=req.audience,
        difficulty=req.difficulty,
        createdAt=datetime.now(timezone.utc).isoformat(),
        learningGoals=goals,
        slides=_build_slides(title, sections, goals, validity_notes, req.audience),
        scenes=_build_scenes(sections),
        podcast=podcast,
        quiz=quiz,
        targetRoles=req.targetRoles,
    )

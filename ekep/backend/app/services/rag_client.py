"""Client für den externen RAG-Service (Wissensbasis mit zeitlicher Gültigkeit).

Nur aktiv, wenn RAG_SERVICE_URL gesetzt ist. Jeder Aufruf fängt Fehler selbst ab
(Log-Warnung, Rückgabe None bzw. []) – der Orchestrator darf dadurch nie scheitern.

Endpunkte des RAG-Service (JSON):
  POST /interview/next  → Vorschlag für die nächste Frage + Belege
  POST /facts           → Expertenaussage speichern, Prüfurteil (kb_verdict) zurück
  POST /facts/{id}/approve → Aussage freigeben (reviewer, resolution)
  POST /export/video    → Lernvideo zu Thema/Abteilung erzeugen (LLM + TTS + ffmpeg, 1–4 min)
  GET  /search          → Treffer in der Wissensbasis (Stichtag as_of)
  GET  /health          → Erreichbarkeit
"""
from __future__ import annotations

import logging

import httpx

from ..config import settings

log = logging.getLogger("ekep.rag")

TIMEOUT_NEXT_S = 90.0
TIMEOUT_FACT_S = 120.0
TIMEOUT_SEARCH_S = 20.0
TIMEOUT_VIDEO_S = 600.0


def enabled() -> bool:
    return bool(settings.rag_service_url)


def _url(path: str) -> str:
    return settings.rag_service_url.rstrip("/") + path


async def next_question(expert: str, topic: str, turns: list[dict], lang: str | None = None,
                        as_of: str | None = None, k: int = 10) -> dict | None:
    body = {"expert": expert, "topic": topic, "as_of": as_of, "lang": lang or settings.rag_service_lang,
            "k": k, "turns": turns}
    if not enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_NEXT_S) as client:
            r = await client.post(_url("/interview/next"), json=body)
            r.raise_for_status()
            data = r.json()
        return data if isinstance(data, dict) and data.get("question") else None
    except Exception as e:
        log.warning("RAG /interview/next fehlgeschlagen: %s", str(e)[:300])
        return None


async def add_fact(expert: str, topic: str, question: str, answer: str, lang: str | None = None,
                   interviewed_at: str | None = None, expert_role: str = "", department: str = "") -> dict | None:
    body = {"expert": expert, "topic": topic, "question": question, "answer": answer,
            "interviewed_at": interviewed_at, "lang": lang or settings.rag_service_lang}
    if expert_role:
        body["expert_role"] = expert_role
    if department:
        body["department"] = department
    if not enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_FACT_S) as client:
            r = await client.post(_url("/facts"), json=body)
            r.raise_for_status()
            data = r.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.warning("RAG /facts fehlgeschlagen: %s", str(e)[:300])
        return None


async def approve_fact(fact_id: str, reviewer: str, resolution: str = "coexist") -> dict | None:
    if not enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_FACT_S) as client:
            r = await client.post(_url(f"/facts/{fact_id}/approve"),
                                  params={"reviewer": reviewer, "resolution": resolution})
            r.raise_for_status()
            data = r.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.warning("RAG /facts/%s/approve fehlgeschlagen: %s", fact_id, str(e)[:300])
        return None


async def export_video(topic: str, department: str | None, lang: str | None = None,
                       audience: str = "beginner") -> dict | None:
    params: dict = {"topic": topic, "lang": lang or settings.rag_service_lang, "audience": audience}
    if department:
        params["department"] = department
    if not enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_VIDEO_S) as client:
            r = await client.post(_url("/export/video"), params=params)
            r.raise_for_status()
            data = r.json()
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.warning("RAG /export/video fehlgeschlagen: %s", str(e)[:300])
        return None


async def search(q: str, k: int = 5, as_of: str | None = None, language: str | None = None) -> list[dict]:
    params: dict = {"q": q, "k": k}
    if as_of:
        params["as_of"] = as_of
    if language:
        params["language"] = language
    if not enabled():
        return []
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SEARCH_S) as client:
            r = await client.get(_url("/search"), params=params)
            r.raise_for_status()
            data = r.json()
        hits = data.get("hits") if isinstance(data, dict) else data
        return [h for h in (hits or []) if isinstance(h, dict)]
    except Exception as e:
        log.warning("RAG /search fehlgeschlagen: %s", str(e)[:300])
        return []

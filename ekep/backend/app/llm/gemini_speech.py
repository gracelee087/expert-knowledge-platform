"""Gemini als Speech-Provider (STT + TTS) – nur serverseitig, der Key verlässt nie das Backend.

Bewusst über die REST-API (httpx, bereits Abhängigkeit des OpenAI-SDK) statt über das
`google-genai`-SDK: keine zusätzliche Installation nötig.

* STT: generateContent mit Audio (WAV) + Transkriptionsanweisung
* TTS: generateContent mit responseModalities=AUDIO → PCM 16 bit → WAV

Warum nicht die Gemini Live API (Speech-to-Speech)? Dann würde das Modell das Gespräch führen.
Für die Wissensextraktion entscheidet aber der Orchestrator jede Frage (Technik, Begründung,
Coverage) – Gemini ist hier reine, austauschbare Ein-/Ausgabeschicht.
"""
from __future__ import annotations

import base64
import io
import logging
import re
import time
import wave

import httpx

from ..config import settings
from .provider import LLMError

log = logging.getLogger("ekep.gemini")
BASE = "https://generativelanguage.googleapis.com/v1beta"
SILENCE_TOKEN = "[KEINE_SPRACHE]"

STT_PROMPT = (
    "Transkribiere die Sprachaufnahme wörtlich auf Deutsch. Gib ausschließlich das Transkript aus – "
    "ohne Einleitung, ohne Zeitstempel, ohne Sprecherlabels, ohne Anführungszeichen. "
    "Erfinde nichts und ergänze nichts. Ist keine verständliche Sprache enthalten, gib exakt "
    f"{SILENCE_TOKEN} aus. Fachbegriffe, die vorkommen können: "
)


def pcm_to_wav(pcm: bytes, rate: int = 24000, channels: int = 1, width: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def _rate_from_mime(mime: str) -> int:
    m = re.search(r"rate=(\d+)", mime or "")
    return int(m.group(1)) if m else 24000


class GeminiSpeech:
    audio_mime = "audio/wav"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60)
        return self._client

    async def _generate(self, model: str, body: dict) -> dict:
        if not settings.gemini_api_key:
            raise LLMError("GEMINI_API_KEY fehlt. Bitte in .env eintragen.")
        try:
            r = await self.client.post(
                f"{BASE}/models/{model}:generateContent",
                headers={"x-goog-api-key": settings.gemini_api_key, "Content-Type": "application/json"},
                json=body,
            )
        except httpx.HTTPError as e:
            raise LLMError(f"Gemini nicht erreichbar: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"Gemini-Fehler {r.status_code}: {r.text[:300]}")
        return r.json()

    @staticmethod
    def _parts(data: dict) -> list[dict]:
        try:
            return data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError):
            reason = (data.get("promptFeedback") or {}).get("blockReason") or data.get("candidates", [{}])[0].get("finishReason")
            raise LLMError(f"Gemini lieferte keine Antwort ({reason}).")

    async def transcribe(self, audio: bytes, filename: str, mime: str, prompt: str | None = None) -> str:
        t0 = time.perf_counter()
        mime = (mime or "audio/wav").split(";")[0]
        body = {
            "contents": [{"role": "user", "parts": [
                {"text": STT_PROMPT + (prompt or "")},
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(audio).decode()}},
            ]}],
            "generationConfig": {"temperature": 0},
        }
        data = await self._generate(settings.gemini_stt_model, body)
        text = " ".join(p.get("text", "") for p in self._parts(data)).strip()
        log.info("gemini stt %.2fs", time.perf_counter() - t0)
        if SILENCE_TOKEN in text:
            return ""
        return text.strip().strip('"„“').strip()

    async def synthesize(self, text: str) -> bytes:
        t0 = time.perf_counter()
        body = {
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": settings.gemini_tts_voice}}},
            },
        }
        data = await self._generate(settings.gemini_tts_model, body)
        for p in self._parts(data):
            inline = p.get("inlineData") or p.get("inline_data")
            if inline and inline.get("data"):
                raw = base64.b64decode(inline["data"])
                m = inline.get("mimeType") or inline.get("mime_type") or ""
                log.info("gemini tts %.2fs", time.perf_counter() - t0)
                if "wav" in m or raw[:4] == b"RIFF":
                    return raw
                return pcm_to_wav(raw, rate=_rate_from_mime(m))
        raise LLMError("Gemini-TTS lieferte kein Audio.")

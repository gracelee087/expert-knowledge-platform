"""Gemini-Speech-Provider (STT/TTS) gegen gemockte REST-API."""
import asyncio
import base64
import json

import httpx
import pytest

from app.config import settings
from app.llm.gemini_speech import GeminiSpeech, pcm_to_wav
from app.llm.provider import LLMError


@pytest.fixture(autouse=True)
def key(monkeypatch):
    object.__setattr__(settings, "gemini_api_key", "test-gemini-key")
    yield
    object.__setattr__(settings, "gemini_api_key", "")


def _speech(handler):
    return GeminiSpeech(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_transcribe_sends_audio_server_side_and_returns_text():
    seen = {}

    def handler(req):
        seen["key"] = req.headers["x-goog-api-key"]
        seen["body"] = json.loads(req.content)
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "Bei Classic 2003 prüfe ich das Tarifkennzeichen."}]}}]})

    text = asyncio.run(_speech(handler).transcribe(b"RIFFxxxx", "a.wav", "audio/wav", prompt="Bausparvertrag"))
    assert text.startswith("Bei Classic 2003")
    assert seen["key"] == "test-gemini-key" and ":generateContent" in seen["url"]
    parts = seen["body"]["contents"][0]["parts"]
    assert "Bausparvertrag" in parts[0]["text"] and parts[1]["inline_data"]["mime_type"] == "audio/wav"


def test_transcribe_silence_returns_empty():
    h = lambda req: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "[KEINE_SPRACHE]"}]}}]})
    assert asyncio.run(_speech(h).transcribe(b"x", "a.wav", "audio/wav")) == ""


def test_synthesize_wraps_pcm_as_wav():
    pcm = b"\x00\x01" * 2400

    def handler(req):
        body = json.loads(req.content)
        assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"inlineData": {
            "mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}}]}}]})

    wav = asyncio.run(_speech(handler).synthesize("Guten Tag"))
    assert wav[:4] == b"RIFF" and wav == pcm_to_wav(pcm, 24000)


def test_errors_become_llm_errors():
    h = lambda req: httpx.Response(403, text="API key not valid")
    with pytest.raises(LLMError, match="403"):
        asyncio.run(_speech(h).synthesize("x"))
    object.__setattr__(settings, "gemini_api_key", "")
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        asyncio.run(_speech(h).transcribe(b"x", "a.wav", "audio/wav"))

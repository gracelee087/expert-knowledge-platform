"""Adapter für das Dashboard-Frontend: POST /api/interviews/transcribe.

Transkribiert eine Audioaufnahme mit exakt derselben STT-Pipeline wie answer_audio (routes.py),
liefert aber nur den Text zurück – ohne Interview-Zustand, ohne Auth-Dependency.
"""
from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..config import settings
from ..llm.provider import LLMError, get_stt

router = APIRouter(prefix="/api", tags=["dashboard"])

MAX_AUDIO_BYTES = 25 * 1024 * 1024
VOCAB = ("Bausparvertrag, Tarifgeneration, Bausparsumme, Zuteilung, Bewertungszahl, Regelsparbeitrag, "
         "Wohnungsbauprämie, Arbeitnehmersparzulage, Vor- und Zwischenfinanzierung")


@router.post("/interviews/transcribe")
async def transcribe(audio: UploadFile = File(...), hint: str | None = Form(default=None),
                     duration_s: float | None = Form(default=None)):
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Leere Audiodatei")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audiodatei zu groß")
    # Datenschutz: Audio wird standardmäßig nur im Speicher verarbeitet
    if settings.audio_retention == "store":
        os.makedirs(settings.audio_dir, exist_ok=True)
        with open(os.path.join(settings.audio_dir, f"dashboard_{secrets.token_hex(4)}.webm"), "wb") as f:
            f.write(data)
    try:
        text = await get_stt().transcribe(data, audio.filename or "answer.webm", audio.content_type or "audio/webm",
                                             prompt=f"{VOCAB}. {hint or ''}")
    except LLMError as e:
        raise HTTPException(502, str(e))
    finally:
        del data
    # Stille: Whisper gibt den Prompt (Vokabular + Hint) zurück → als „keine Sprache“ werten
    if text and (VOCAB[:40].lower() in text.lower() or (hint and text.strip().lower() == hint.strip().lower())):
        text = ""
    if not text or len(text.strip()) < 2:
        raise HTTPException(422, "Es wurde keine Sprache erkannt. Bitte erneut sprechen.")
    return {"text": text, "duration_s": duration_s}

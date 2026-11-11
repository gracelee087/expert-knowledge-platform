"""Voice-Check für STT_ENGINE / TTS_ENGINE.

Aufruf im backend-Ordner:  python scripts/check_voice.py
* STT: schickt einen kurzen, erzeugten Ton an die Spracherkennung (prüft Zugang + Modell)
* TTS: erzeugt check_voice.wav/mp3 (nur wenn TTS_ENGINE nicht "browser")
"""
import asyncio
import math
import os
import struct
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.config import settings  # noqa: E402
from app.llm.gemini_speech import pcm_to_wav  # noqa: E402
from app.llm.provider import get_stt, get_tts  # noqa: E402

SENTENCE = "Woran erkennen Sie im Arbeitsalltag, welcher Tarifgeneration ein Bausparvertrag angehört?"


def tone_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    pcm = b"".join(struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(int(seconds * rate)))
    return pcm_to_wav(pcm, rate)


async def main() -> None:
    print(f"STT_ENGINE={settings.stt_engine} · TTS_ENGINE={settings.tts_engine}")
    if settings.stt_engine == "browser":
        print("  STT läuft im Browser – hier nichts zu prüfen.")
    else:
        model = settings.gemini_stt_model if settings.stt_engine == "gemini" else settings.stt_model
        t0 = time.perf_counter()
        try:
            text = await get_stt().transcribe(tone_wav(), "tone.wav", "audio/wav")
            print(f"  ✓ STT ({model}) erreichbar, {time.perf_counter() - t0:.1f}s – Ergebnis für Testton: {text!r}")
        except Exception as e:
            print(f"  ✗ STT ({model}): {e}")
    if settings.tts_engine == "browser":
        print("  TTS läuft lokal im Browser (Systemstimme) – hier nichts zu prüfen.")
    else:
        t0 = time.perf_counter()
        try:
            audio = await get_tts().synthesize(SENTENCE)
            ext = "wav" if audio[:4] == b"RIFF" else "mp3"
            with open(f"check_voice.{ext}", "wb") as f:
                f.write(audio)
            print(f"  ✓ TTS {time.perf_counter() - t0:.1f}s → check_voice.{ext}")
        except Exception as e:
            print(f"  ✗ TTS: {e}")


if __name__ == "__main__":
    asyncio.run(main())

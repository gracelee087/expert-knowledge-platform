"""Modellabstraktion.

Alle Agenten sprechen nur mit `LLMProvider` bzw. `SpeechProvider`. Damit sind
OpenAI, Azure OpenAI (Helaba-KI-Plattform) oder künftig andere Anbieter
austauschbar, ohne die Interview-Logik anzufassen.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Protocol, TypeVar

from pydantic import BaseModel

from ..config import settings

log = logging.getLogger("ekep.llm")
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    async def structured(self, *, system: str, user: str, schema: type[T], model: str, temperature: float = 0.3) -> T: ...


class SpeechProvider(Protocol):
    async def transcribe(self, audio: bytes, filename: str, mime: str, prompt: str | None = None) -> str: ...
    async def synthesize(self, text: str) -> bytes: ...


def _is_reasoning_model(model: str) -> bool:
    m = model.lower()
    return m.startswith(("o1", "o3", "o4", "gpt-5", "gpt-6"))


def _client():
    if settings.llm_provider == "azure":
        from openai import AsyncAzureOpenAI

        if not settings.azure_endpoint or not settings.azure_api_key:
            raise LLMError("Azure OpenAI ist nicht konfiguriert (AZURE_OPENAI_ENDPOINT / AZURE_OPENAI_API_KEY).")
        return AsyncAzureOpenAI(
            azure_endpoint=settings.azure_endpoint,
            api_key=settings.azure_api_key,
            api_version=settings.azure_api_version,
            timeout=60,
            max_retries=2,
        )
    from openai import AsyncOpenAI

    if not settings.openai_api_key:
        raise LLMError("OPENAI_API_KEY fehlt. Bitte in .env eintragen.")
    return AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url or "https://api.openai.com/v1",
                       timeout=60, max_retries=2)


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def extract_json(text: str) -> str:
    """JSON aus Modellantworten lösen (Thinking-Blöcke, ```json-Fences, Begleittext)."""
    t = _THINK.sub("", text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.M).strip()
    if t.startswith("{"):
        return t
    m = _JSON_BLOCK.search(t)
    if not m:
        raise ValueError("Keine JSON-Struktur in der Modellantwort gefunden.")
    return m.group(0)


def _thinking_controllable(model: str) -> bool:
    m = model.lower()
    return "gemma" in m or "qwen" in m


class OpenAIProvider:
    """Structured Outputs für OpenAI, Azure OpenAI und OpenAI-kompatible Gateways (z. B. hIP / hessian.AI).

    Strategie:
      1. `json_schema` (strict) – wenn das Modell/Gateway es unterstützt
      2. Fallback: Schema im Prompt, JSON aus Text extrahieren, mit Pydantic validieren,
         bei Fehlern eine Reparaturrunde mit der Fehlermeldung.
    Die Ausgabe wird IMMER gegen das Pydantic-Schema validiert.
    """

    def __init__(self) -> None:
        self._c = None
        self._schema_unsupported: set[str] = set()

    @property
    def client(self):
        if self._c is None:
            self._c = _client()
        return self._c

    def _common(self, model: str, temperature: float, max_tokens: int) -> dict:
        kw: dict = {"model": model}
        if not _is_reasoning_model(model):
            kw["temperature"] = temperature
            kw["max_tokens"] = max_tokens
        if settings.llm_disable_thinking and _thinking_controllable(model):
            kw["extra_headers"] = {"x-bf-passthrough-extra-params": "true"}
            kw["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        return kw

    async def structured(self, *, system: str, user: str, schema: type[T], model: str, temperature: float = 0.3) -> T:
        t0 = time.perf_counter()
        max_tokens = settings.llm_max_tokens
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        result: T | None = None

        if settings.llm_json_mode == "schema" and model not in self._schema_unsupported:
            try:
                completions = self.client.chat.completions
                parse = getattr(completions, "parse", None) or self.client.beta.chat.completions.parse
                resp = await parse(messages=messages, response_format=schema,
                                   **self._common(model, temperature, max_tokens))
                msg = resp.choices[0].message
                if getattr(msg, "refusal", None):
                    raise LLMError(f"Modell hat die Antwort verweigert: {msg.refusal}")
                result = msg.parsed
                if result is None and msg.content:
                    result = schema.model_validate_json(extract_json(msg.content))
            except LLMError:
                raise
            except Exception as e:  # Schema-Modus nicht unterstützt / ungültige Ausgabe → Fallback
                log.warning("json_schema-Modus für %s fehlgeschlagen (%s) – nutze Prompt-JSON", model, str(e)[:200])
                if _looks_unsupported(e):
                    self._schema_unsupported.add(model)
                if _is_auth_or_network(e):
                    raise LLMError(f"LLM-Aufruf fehlgeschlagen: {e}") from e

        if result is None:
            result = await self._prompt_json(messages, schema, model, temperature, max_tokens)
        log.info("llm %s %s %.2fs", model, schema.__name__, time.perf_counter() - t0)
        return result

    async def _prompt_json(self, messages, schema: type[T], model, temperature, max_tokens) -> T:
        schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
        msgs = [
            {"role": "system", "content": messages[0]["content"] + "\n\nAUSGABEFORMAT: Antworte ausschließlich mit "
             "einem einzigen JSON-Objekt (kein Markdown, kein Begleittext), das exakt diesem JSON-Schema entspricht. "
             "Alle Felder sind Pflichtfelder; nutze null bzw. [] wenn nichts bekannt ist.\n" + schema_json},
            messages[1],
        ]
        last_err = ""
        for attempt in range(2):
            try:
                resp = await self.client.chat.completions.create(messages=msgs, **self._common(model, temperature, max_tokens))
            except Exception as e:
                raise LLMError(f"LLM-Aufruf fehlgeschlagen: {e}") from e
            content = resp.choices[0].message.content or ""
            try:
                return schema.model_validate_json(extract_json(content))
            except Exception as e:
                last_err = str(e)[:1500]
                msgs = msgs + [
                    {"role": "assistant", "content": content[:6000]},
                    {"role": "user", "content": f"Die Antwort war kein gültiges JSON gemäß Schema. Fehler: {last_err}\n"
                     "Gib das vollständige, korrigierte JSON-Objekt zurück – nur JSON."},
                ]
        raise LLMError(f"Modell {model} lieferte keine schema-konforme Antwort: {last_err[:300]}")


def _looks_unsupported(e: Exception) -> bool:
    s = str(e).lower()
    return any(k in s for k in ("response_format", "json_schema", "not supported", "unsupported", "400"))


def _is_auth_or_network(e: Exception) -> bool:
    name = type(e).__name__
    return name in ("AuthenticationError", "PermissionDeniedError", "APIConnectionError", "APITimeoutError", "NotFoundError")


class OpenAISpeech:
    def __init__(self) -> None:
        self._c = None

    @property
    def client(self):
        if self._c is None:
            self._c = _client()
        return self._c

    async def transcribe(self, audio: bytes, filename: str, mime: str, prompt: str | None = None) -> str:
        try:
            kwargs = {
                "model": settings.stt_model,
                "file": (filename, audio, mime),
                "language": settings.interview_language,
            }
            if prompt:
                kwargs["prompt"] = prompt[:800]
            try:
                resp = await self.client.audio.transcriptions.create(**kwargs)
            except Exception as first:
                # Fallback: Modell im Account nicht freigeschaltet → whisper-1
                if kwargs["model"] == "whisper-1" or _is_auth_or_network(first) and "model" not in str(first).lower():
                    raise
                log.warning("STT mit %s fehlgeschlagen (%s) – versuche whisper-1", kwargs["model"], str(first)[:160])
                kwargs["model"] = "whisper-1"
                resp = await self.client.audio.transcriptions.create(**kwargs)
        except Exception as e:
            raise LLMError(f"Transkription fehlgeschlagen: {e}") from e
        return (getattr(resp, "text", None) or "").strip()

    async def synthesize(self, text: str) -> bytes:
        kwargs = {"model": settings.tts_model, "voice": settings.tts_voice, "input": text, "response_format": "mp3"}
        if "gpt-4o" in settings.tts_model:
            kwargs["instructions"] = (
                "Sprich Deutsch, ruhig, freundlich und professionell wie ein erfahrener Interviewer "
                "in einer Bank. Natürliches Tempo, kurze Pausen an Satzgrenzen."
            )
        try:
            resp = await self.client.audio.speech.create(**kwargs)
        except Exception as e:
            raise LLMError(f"Sprachausgabe fehlgeschlagen: {e}") from e
        return resp.content if hasattr(resp, "content") else await resp.aread()


_llm: LLMProvider | None = None
_speech: SpeechProvider | None = None


def get_llm() -> LLMProvider:
    global _llm
    if _llm is None:
        _llm = OpenAIProvider()
    return _llm


_by_engine: dict[str, SpeechProvider] = {}


def _engine_provider(engine: str) -> SpeechProvider:
    if engine not in _by_engine:
        if engine == "gemini":
            from .gemini_speech import GeminiSpeech

            _by_engine[engine] = GeminiSpeech()
        else:
            _by_engine[engine] = OpenAISpeech()
    return _by_engine[engine]


def get_stt() -> SpeechProvider:
    """Speech-to-Text gemäß STT_ENGINE (openai | gemini)."""
    return _speech or _engine_provider(settings.stt_engine)


def get_tts() -> SpeechProvider:
    """Text-to-Speech gemäß TTS_ENGINE (openai | gemini)."""
    return _speech or _engine_provider(settings.tts_engine)


def get_speech() -> SpeechProvider:  # Rückwärtskompatibilität
    return get_stt()


def set_providers(llm: LLMProvider | None = None, speech: SpeechProvider | None = None) -> None:
    """Dependency Injection (Tests, alternative Anbieter)."""
    global _llm, _speech
    if llm is not None:
        _llm = llm
    if speech is not None:
        _speech = speech

"""Zentrale Konfiguration – ausschließlich über Umgebungsvariablen (12-Factor)."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field


def _load_dotenv() -> None:
    """.env aus Projekt- oder backend-Ordner laden und Prozessvariablen ÜBERSCHREIBEN.

    So wirken Änderungen an .env immer – auch wenn im Terminal noch alte Werte gesetzt sind.
    In Tests (pytest) wird nichts geladen.
    """
    if "pytest" in sys.modules or os.getenv("EKEP_SKIP_DOTENV"):
        return
    here = Path(__file__).resolve().parent.parent  # backend/
    for path in (here.parent / ".env", here / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            m = re.match(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*)$", line)
            if not m:
                continue
            value = m.group(2)
            if not value.startswith(("'", '"')):
                value = value.split(" #")[0].split("\t#")[0]
            value = value.strip().strip("'\"")
            if value:
                os.environ[m.group(1)] = value
            else:
                os.environ.pop(m.group(1), None)  # leere Werte = nicht gesetzt (z. B. OPENAI_BASE_URL=)
        break


_load_dotenv()


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Datenbank: PostgreSQL in Docker, SQLite als lokaler Fallback
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./data/ekep.db"))

    # LLM-Provider: "openai" oder "azure"
    llm_provider: str = field(default_factory=lambda: os.getenv("LLM_PROVIDER", "openai"))
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str | None = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL") or None)
    azure_endpoint: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_ENDPOINT", ""))
    azure_api_key: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_KEY", ""))
    azure_api_version: str = field(default_factory=lambda: os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"))

    # Structured Output: "schema" = json_schema strict mit automatischem Fallback, "prompt" = immer Prompt-JSON
    llm_json_mode: str = field(default_factory=lambda: os.getenv("LLM_JSON_MODE", "schema"))
    llm_max_tokens: int = field(default_factory=lambda: int(os.getenv("LLM_MAX_TOKENS", "4096")))
    # Gemma/Qwen auf dem hIP-Gateway: Thinking abschalten (Latenz im Gespräch)
    llm_disable_thinking: bool = field(default_factory=lambda: _bool("LLM_DISABLE_THINKING", True))

    # Modelle (bei Azure: Deployment-Namen; bei hIP: Gateway-Modell-IDs)
    interviewer_model: str = field(default_factory=lambda: os.getenv("INTERVIEWER_MODEL", "gpt-4o"))
    analyst_model: str = field(default_factory=lambda: os.getenv("ANALYST_MODEL", "gpt-4o"))
    planner_model: str = field(default_factory=lambda: os.getenv("PLANNER_MODEL", "gpt-4o"))
    stt_model: str = field(default_factory=lambda: os.getenv("STT_MODEL", "gpt-4o-transcribe"))
    tts_model: str = field(default_factory=lambda: os.getenv("TTS_MODEL", "gpt-4o-mini-tts"))
    tts_voice: str = field(default_factory=lambda: os.getenv("TTS_VOICE", "alloy"))
    # Voice-Engine: "openai" = STT/TTS über API (benötigt Transkriptions-/TTS-Modelle)
    #               "browser" = Web Speech API im Browser (nur Chat-Modell nötig, z. B. gpt-4o)
    #               "gemini"  = STT + TTS über Google Gemini (serverseitig)
    # Getrennt einstellbar (überschreiben VOICE_ENGINE): openai | gemini | browser
    stt_engine: str = field(default_factory=lambda: os.getenv("STT_ENGINE") or os.getenv("VOICE_ENGINE", "browser"))
    tts_engine: str = field(default_factory=lambda: os.getenv("TTS_ENGINE") or os.getenv("VOICE_ENGINE", "browser"))
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_stt_model: str = field(default_factory=lambda: os.getenv("GEMINI_STT_MODEL", "gemini-2.5-flash"))
    gemini_tts_model: str = field(default_factory=lambda: os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts"))
    gemini_tts_voice: str = field(default_factory=lambda: os.getenv("GEMINI_TTS_VOICE", "Kore"))
    voice_engine: str = field(default_factory=lambda: os.getenv("VOICE_ENGINE", "browser"))
    interview_language: str = field(default_factory=lambda: os.getenv("INTERVIEW_LANGUAGE", "de"))

    # Datenschutz
    # none = Audio wird nur im Speicher transkribiert und sofort verworfen (Default)
    # store = Audio wird lokal abgelegt (nur nach genehmigtem Datenschutzkonzept)
    audio_retention: str = field(default_factory=lambda: os.getenv("AUDIO_RETENTION", "none"))
    audio_dir: str = field(default_factory=lambda: os.getenv("AUDIO_DIR", "./data/audio"))
    pii_redaction: bool = field(default_factory=lambda: _bool("PII_REDACTION", True))
    export_include_expert_name: bool = field(default_factory=lambda: _bool("EXPORT_INCLUDE_EXPERT_NAME", False))
    consent_text_version: str = field(default_factory=lambda: os.getenv("CONSENT_TEXT_VERSION", "2026-11-v1"))

    # Zugriff
    auth_mode: str = field(default_factory=lambda: os.getenv("AUTH_MODE", "demo"))  # demo | off
    export_api_key: str = field(default_factory=lambda: os.getenv("EXPORT_API_KEY", "change-me-rag-team-key"))
    cors_origins: list[str] = field(
        default_factory=lambda: [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
    )

    # Wissensabgleich mit der bestehenden Wissensbasis (Vektorsuche; Datenbank = Phase 2)
    #   local = semantische In-Memory-Suche über seed/reference_knowledge.json + bestätigte Einheiten
    #   http  = Search-Endpunkt der Vektordatenbank (VECTOR_SEARCH_URL)   |   off = aus
    knowledge_search_provider: str = field(default_factory=lambda: os.getenv("KNOWLEDGE_SEARCH_PROVIDER", "local"))
    vector_search_url: str = field(default_factory=lambda: os.getenv("VECTOR_SEARCH_URL", ""))
    vector_search_api_key: str = field(default_factory=lambda: os.getenv("VECTOR_SEARCH_API_KEY", ""))
    vector_search_filters: str = field(default_factory=lambda: os.getenv("VECTOR_SEARCH_FILTERS", "{}"))
    reference_corpus_path: str = field(default_factory=lambda: os.getenv("REFERENCE_CORPUS_PATH", ""))
    embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"))
    embedding_base_url: str = field(default_factory=lambda: os.getenv("EMBEDDING_BASE_URL", ""))
    embedding_api_key: str = field(default_factory=lambda: os.getenv("EMBEDDING_API_KEY", ""))
    search_top_k: int = field(default_factory=lambda: int(os.getenv("SEARCH_TOP_K", "3")))
    search_min_score: float = field(default_factory=lambda: float(os.getenv("SEARCH_MIN_SCORE", "0.35")))
    search_min_score_lexical: float = field(default_factory=lambda: float(os.getenv("SEARCH_MIN_SCORE_LEXICAL", "0.12")))
    search_reconcile_threshold: float = field(default_factory=lambda: float(os.getenv("SEARCH_RECONCILE_THRESHOLD", "0.55")))
    search_timeout_s: float = field(default_factory=lambda: float(os.getenv("SEARCH_TIMEOUT_S", "8")))

    # Externer RAG-Service (Wissensbasis mit Gültigkeitsprüfung): leer = aus, Verhalten unverändert
    #   RAG_SERVICE_URL   z. B. http://localhost:8001 → Fragevorschläge + Belege im Interviewer-Kontext,
    #                     Expertenaussagen werden nach jeder Antwort als Fakt eingereicht (kb_verdict)
    #   RAG_SERVICE_TOPIC leer = Interviewtitel   |   RAG_SERVICE_LANG = de | en
    #   Suche über den RAG-Service: KNOWLEDGE_SEARCH_PROVIDER=rag_service
    rag_service_url: str = field(default_factory=lambda: os.getenv("RAG_SERVICE_URL", "").strip())
    rag_service_lang: str = field(default_factory=lambda: os.getenv("RAG_SERVICE_LANG", "de"))
    rag_service_topic: str = field(default_factory=lambda: os.getenv("RAG_SERVICE_TOPIC", ""))

    # Interviewsteuerung
    # Fragen-Budget = geplante Minuten / MINUTES_PER_QUESTION (z. B. 30 Min. → 20 Fragen)
    minutes_per_question: float = field(default_factory=lambda: float(os.getenv("MINUTES_PER_QUESTION", "1.5")))
    max_followups_per_topic: int = field(default_factory=lambda: int(os.getenv("MAX_FOLLOWUPS_PER_TOPIC", "4")))


settings = Settings()

"""Unit Tests: Structured-Output-Fallback für OpenAI-kompatible Gateways (hIP)."""
import asyncio
from types import SimpleNamespace

import pytest

from app.agents.schemas import InterviewerDecision
from app.llm.provider import LLMError, OpenAIProvider, extract_json

VALID = ('{"action":"follow_up","technique":"clarification","topic_id":"T1","acknowledgement":"",'
         '"question":"Welche Tarifgeneration genau?","rationale":"vage","current_topic_saturated":false,'
         '"expert_reached_knowledge_limit":false,"last_answer_information_gain":"hoch","estimated_remaining_questions":3}')


def _resp(content=None, parsed=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, parsed=parsed, refusal=None))])


class FakeCompletions:
    def __init__(self, contents, schema_error=True):
        self.contents = list(contents)
        self.schema_error = schema_error
        self.create_kwargs = []

    async def parse(self, **kw):
        if self.schema_error:
            raise RuntimeError("Error code: 400 - response_format json_schema not supported")
        return _resp(parsed=InterviewerDecision.model_validate_json(VALID))

    async def create(self, **kw):
        self.create_kwargs.append(kw)
        return _resp(content=self.contents.pop(0))


def _provider(comp):
    p = OpenAIProvider()
    p._c = SimpleNamespace(chat=SimpleNamespace(completions=comp))
    return p


def run(coro):
    return asyncio.run(coro)


def test_extract_json_handles_think_and_fences():
    assert extract_json(f"<think>hmm</think>\n```json\n{VALID}\n```").startswith("{")
    assert extract_json(f"Hier ist das Ergebnis: {VALID} Danke.").endswith("}")


def test_schema_mode_used_when_supported():
    comp = FakeCompletions([], schema_error=False)
    r = run(_provider(comp).structured(system="s", user="u", schema=InterviewerDecision, model="Mistral-Medium"))
    assert r.question.startswith("Welche") and comp.create_kwargs == []


def test_fallback_with_repair_round_and_thinking_disabled():
    comp = FakeCompletions(["Leider kein JSON", f"<think>x</think>{VALID}"])
    p = _provider(comp)
    r = run(p.structured(system="s", user="u", schema=InterviewerDecision, model="gemma-4-31B-it"))
    assert r.topic_id == "T1"
    assert len(comp.create_kwargs) == 2  # Reparaturrunde
    kw = comp.create_kwargs[0]
    assert kw["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert kw["extra_headers"]["x-bf-passthrough-extra-params"] == "true"
    assert "gemma-4-31B-it" in p._schema_unsupported  # nächster Aufruf direkt im Prompt-Modus


def test_fallback_gives_up_with_llm_error():
    comp = FakeCompletions(["nope", "immer noch nicht"])
    with pytest.raises(LLMError):
        run(_provider(comp).structured(system="s", user="u", schema=InterviewerDecision, model="GLM-5.3-Flash"))

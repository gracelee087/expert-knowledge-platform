"""Testinfrastruktur.

Die Fake-Provider ersetzen NUR in Tests das externe LLM (deterministisch,
offline). Die Anwendung selbst nutzt immer die echten Provider.
Szenarien: fiktive Daten aus dem Bauspar- und Immobilienfinanzierungsumfeld.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ["EXPORT_API_KEY"] = "rag-test-key"

from app import db as dbmod  # noqa: E402
from app.agents import schemas as S  # noqa: E402
from app.llm import provider  # noqa: E402

ADMIN = {"X-Demo-Role": "admin", "X-Demo-User": "admin.test"}
EXPERT = {"X-Demo-Role": "expert", "X-Demo-User": "h.weber"}
INTERVIEWER = {"X-Demo-Role": "interviewer", "X-Demo-User": "km.team"}
RAG = {"Authorization": "Bearer rag-test-key"}

EXPERT_ANSWERS = [
    "Ich bin seit 1994 bei der LBS, zuerst in der Sachbearbeitung, heute betreue ich vor allem Altverträge.",
    "Bei älteren Bausparverträgen der Tarifgeneration Classic 2003 gilt eine andere Mindestbewertungszahl, "
    "das sieht man am Tarifkennzeichen C03 im Bestandssystem.",
    "Wenn das Kennzeichen fehlt, prüfe ich zuerst das Abschlussdatum und dann die Vertragsbedingungen im Archiv. "
    "Im Zweifel frage ich die Fachabteilung Tarifwesen.",
]


class FakeLLM:
    """Liefert schema-konforme, aus dem Prompt abgeleitete Antworten."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.analysis_override = None
        self.fail_interviewer = False
        self.gain = "hoch"
        self.instructions: list[str] = []

    async def structured(self, *, system, user, schema, model, temperature=0.3):
        self.calls.append(schema.__name__)
        if schema is S.InterviewPlan:
            return S.InterviewPlan(
                strategy="Vom Bekannten zu Sonderfällen",
                warmup_question="Wie sind Sie zur LBS gekommen?",
                topics=[
                    S.PlannedTopic(id="T1", title="Altverträge und Tarifgenerationen", description="Erkennung alter Tarife",
                                   rationale="Hohes Verlustrisiko", priority="hoch",
                                   focus_dimensions=["fachlich", "ausnahmen"], opening_question="Welche Altverträge …?",
                                   tacit_knowledge_probes=["Woran erkennen Sie Sonderfälle sofort?"]),
                    S.PlannedTopic(id="T2", title="Zuteilungsprozess", description="Ablauf der Zuteilung",
                                   rationale="Prozesswissen", priority="mittel", focus_dimensions=["prozess"],
                                   opening_question="Wie läuft die Zuteilung ab?", tacit_knowledge_probes=[]),
                ],
            )
        if schema is S.InterviewerDecision:
            self.instructions.append(user.split("AUFGABE:")[-1].strip())
            if self.fail_interviewer:
                raise provider.LLMError("simulierter Ausfall")
            n = sum(1 for c in self.calls if c == "InterviewerDecision")
            return S.InterviewerDecision(
                action="follow_up", technique="clarification" if n % 2 else "exception_handling", topic_id="T1",
                acknowledgement="Verstanden.", question=f"Frage Nummer {n}: Welche Merkmale prüfen Sie zuerst?",
                rationale="Vage Aussage konkretisieren", current_topic_saturated=False,
                expert_reached_knowledge_limit=False, last_answer_information_gain=self.gain,
                estimated_remaining_questions=5,
            )
        if schema is S.AnalysisResult:
            if self.analysis_override:
                return self.analysis_override(user)
            return self._analysis(user)
        if schema is S.InterviewSummary:
            return S.InterviewSummary(executive_summary="Altverträge erkennt man am Tarifkennzeichen.",
                                      key_findings=["C03 = Classic 2003"], topics=[], open_questions=["Ausnahmen C01?"],
                                      recommended_followups=[], quality_notes=[])
        if schema is S.ReconciliationResult:
            import json as _json

            items = []
            for entry in _json.loads(user):
                hits = entry["treffer"]
                items.append(S.UnitRelation(unit_id=entry["einheit"]["id"], relation="ergaenzung" if hits else "neu",
                                            matched_id=hits[0]["id"] if hits else None,
                                            explanation="Ergänzt den bestehenden Eintrag um Praxisdetails."))
            return S.ReconciliationResult(items=items)
        if schema is S.TrainingContent:
            ids = re.findall(r'"id": "(KU-[0-9A-F]+)"', user)
            return S.TrainingContent(
                title="Altverträge erkennen", audience="einsteiger", learning_objectives=["Tarifkennzeichen lesen"],
                slides=[S.Slide(title="Tarifkennzeichen", bullets=["C03"], speaker_notes="…", source_unit_ids=ids[:1] + ["KU-FAKE"])],
                quiz=[S.QuizQuestion(question="Was bedeutet C03?", options=["Classic 2003", "Komfort"], correct_index=7,
                                     explanation="Laut Experte", source_unit_ids=ids[:1])],
            )
        raise AssertionError(schema)

    @staticmethod
    def expert_texts(user: str) -> dict[str, str]:
        return dict(re.findall(r"\[(turn_[0-9a-f]+)\] Experte: (.+)", user))

    def _analysis(self, user: str) -> S.AnalysisResult:
        new_ids = re.findall(r"turn_[0-9a-f]+", user.split("NEU ZU ANALYSIEREN")[-1])
        texts = self.expert_texts(user)
        units = []
        for tid in new_ids:
            words = texts.get(tid, "").split()
            if len(words) < 12:
                continue
            units.append(S.ExtractedUnit(
                existing_unit_id=None, topic_id="T1", category="fachwissen", title="Tarifgeneration erkennen",
                content=" ".join(words[:20]), explanation="", prerequisites=[], process_steps=[], decision_logic=None,
                exceptions=[], examples=[], risks=[], dependencies=[], escalation_paths=[], common_mistakes=[],
                historical_changes=[], validity_note=None,
                source_quotes=[S.SourceQuote(turn_id=tid, quote=" ".join(words[2:12]))],
                confidence="hoch", confidence_reason="konkret benannt", uncertainties=[],
            ))
        return S.AnalysisResult(
            units=units,
            coverage_updates=[
                S.CoverageUpdate(topic_id="T1", dimension="fachlich", status="partially_covered", evidence="Kennzeichen"),
                # Guardrail-Test: KI versucht einen unzulässigen Status über eine nicht erlaubte Dimension
            ],
            new_gaps=[S.GapItem(topic_id="T1", dimension="ausnahmen", description="Vorgehen bei fehlendem Tarifkennzeichen")],
            resolved_gap_ids=[], contradictions=[], new_topics=[],
        )


class FakeSpeech:
    def __init__(self) -> None:
        self.next_text = EXPERT_ANSWERS[1]
        self.fail = False

    async def transcribe(self, audio, filename, mime, prompt=None):
        if self.fail:
            raise provider.LLMError("STT nicht erreichbar")
        return self.next_text

    async def synthesize(self, text):
        return b"ID3fake-mp3"


async def fake_embed(texts):
    """Deterministische Bag-of-Words-Embeddings (offline) für die lokale Vektorsuche."""
    import hashlib

    from app.services.knowledge_search import _tokens

    out = []
    for t in texts:
        v = [0.0] * 256
        for tok in _tokens(t):
            v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 256] += 1.0
        out.append(v)
    return out


@pytest.fixture()
def fakes():
    from app.services import knowledge_search as ks

    llm, speech = FakeLLM(), FakeSpeech()
    provider.set_providers(llm, speech)
    ks.set_provider(None)
    ks.set_embedder(fake_embed)
    return llm, speech


@pytest.fixture()
def client(fakes):
    from fastapi.testclient import TestClient

    tmp = tempfile.mkdtemp()
    dbmod.configure(os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp}/test.db")
    dbmod.init_db()
    from app.main import app

    with TestClient(app) as c:
        yield c


def wait_idle(client, interview_id: str) -> None:
    from app.services import orchestrator

    client.portal.call(orchestrator.wait_idle, interview_id)


@pytest.fixture()
def started(client):
    """Mitarbeiter + Interview mit Plan, Einwilligung und erster Frage."""
    emp = client.post("/api/employees", headers=ADMIN, json={
        "name": "Helga Weber", "department": "Bestandsverwaltung", "role": "Senior Sachbearbeiterin",
        "expertise_area": "Altverträge und Tarifgenerationen", "years_experience": 31,
        "responsibilities": ["Sonderfälle Altverträge"], "specializations": ["Tarife vor 2005"],
    }).json()
    iv = client.post(f"/api/employees/{emp['id']}/interviews", headers=INTERVIEWER, json={
        "title": "Wissenssicherung Altverträge", "goal": "Erkennung und Behandlung historischer Tarifgenerationen sichern",
        "planned_minutes": 30,
    }).json()
    assert client.post(f"/api/interviews/{iv['id']}/plan", headers=INTERVIEWER).status_code == 200
    assert client.post(f"/api/interviews/{iv['id']}/consent", headers=EXPERT).status_code == 200
    r = client.post(f"/api/interviews/{iv['id']}/start", headers=EXPERT)
    assert r.status_code == 200, r.text
    return emp, r.json()

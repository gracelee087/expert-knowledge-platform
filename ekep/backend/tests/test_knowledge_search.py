"""Wissensabgleich: Live-Vektorsuche pro Antwort, Abgleich am Ende, Export für die Vektordatenbank."""
import asyncio
import json

import httpx
from conftest import ADMIN, EXPERT, RAG

from app.config import settings
from app.services import knowledge_search as ks


def _answer(client, iid, text):
    r = client.post(f"/api/interviews/{iid}/answer", headers=EXPERT, json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def _idle(client, iid):
    from app.services import orchestrator

    client.portal.call(ks.wait_idle, iid)
    client.portal.call(orchestrator.wait_idle, iid)


def test_answer_triggers_parallel_search_with_relevant_hit(client, started):
    _, iv = started
    r = _answer(client, iv["id"], "Wenn das Tarifkennzeichen im Bestandssystem fehlt, prüfe ich das Abschlussdatum "
                                  "und frage im Zweifel die Fachabteilung Tarifwesen.")
    _idle(client, iv["id"])
    data = client.get(f"/api/interviews/{iv['id']}/knowledge-search", headers=EXPERT).json()
    s = data["searches"][0]
    assert s["turn_id"] == r["expert_turn"]["id"] and s["status"] == "ok" and s["mode"] == "semantic"
    assert s["hits"][0]["id"] == "KB-0001"  # Arbeitsanweisung Tarifgenerationen
    assert len(s["hits"]) <= settings.search_top_k


def test_search_runs_even_without_hits(client, started):
    _, iv = started
    _answer(client, iv["id"], "Quantenphysik und Raumfahrt interessieren mich privat sehr, besonders Teleskope.")
    _idle(client, iv["id"])
    s = client.get(f"/api/interviews/{iv['id']}/knowledge-search", headers=EXPERT).json()["searches"][0]
    assert s["status"] == "ok" and s["hits"] == []


def test_search_failure_does_not_block_interview(client, started):
    class Broken:
        name = "http"

        async def search(self, *a, **k):
            raise RuntimeError("Vektordatenbank nicht erreichbar")

    ks.set_provider(Broken())
    _, iv = started
    r = _answer(client, iv["id"], "Bei Classic 2003 gilt eine andere Mindestbewertungszahl.")
    assert r["ai_turn"]["speaker"] == "ai"  # Interview läuft weiter
    _idle(client, iv["id"])
    s = client.get(f"/api/interviews/{iv['id']}/knowledge-search", headers=EXPERT).json()["searches"][0]
    assert s["status"] == "error" and "nicht erreichbar" in s["error"]


def test_lexical_fallback_when_embeddings_unavailable(client, started):
    async def broken(texts):
        raise RuntimeError("embedding model not available")

    ks.set_embedder(broken)
    _, iv = started
    _answer(client, iv["id"], "Für die Wohnungsbauprämie prüfen wir Einkommensgrenzen und fordern fehlende Nachweise an.")
    _idle(client, iv["id"])
    s = client.get(f"/api/interviews/{iv['id']}/knowledge-search", headers=EXPERT).json()["searches"][0]
    assert s["mode"] == "lexical" and s["hits"][0]["id"] == "KB-0004"


def test_short_answers_use_question_as_context():
    assert ks.build_query("Ja, genau.", "Prüfen Sie zuerst das Tarifkennzeichen?").startswith("Prüfen Sie")
    long = "Wir prüfen zuerst das Kennzeichen im System und danach das Datum des Abschlusses im Archiv sorgfältig."
    assert ks.build_query(long, "Frage?") == long


def test_http_provider_parses_vector_db_variants(monkeypatch):
    object.__setattr__(settings, "vector_search_url", "https://vector.example/search")
    object.__setattr__(settings, "vector_search_api_key", "vk")
    seen = {}

    def handler(req):
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"matches": [
            {"id": "doc-7", "text": "Zwischenfinanzierung erfordert Abtretung.", "distance": 0.2,
             "metadata": {"title": "Kreditrichtlinie", "source": "SharePoint"}}]})

    p = ks.HttpSearch(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    res = asyncio.run(p.search("Zwischenfinanzierung Voraussetzungen", 3))
    object.__setattr__(settings, "vector_search_url", "")
    object.__setattr__(settings, "vector_search_api_key", "")
    assert seen["auth"] == "Bearer vk" and seen["body"]["query"].startswith("Zwischen") and seen["body"]["top_k"] == 3
    h = res.hits[0]
    assert h.id == "doc-7" and h.title == "Kreditrichtlinie" and abs(h.score - 0.8) < 1e-9


def test_reconciliation_at_finish_and_export(client, started):
    _, iv = started
    iid = iv["id"]
    _answer(client, iid, "Bei älteren Bausparverträgen der Tarifgeneration Classic 2003 gilt eine andere "
                         "Mindestbewertungszahl, das sieht man am Tarifkennzeichen C03 im Bestandssystem.")
    _answer(client, iid, "Unsere HR-Abteilung hat einen neuen Mentoring-Prozess entwickelt: Patenschaft, "
                         "monatliche Feedbackgespräche und eine Abschlussreflexion nach drei Monaten.")
    client.post(f"/api/interviews/{iid}/finish", headers=EXPERT)
    data = client.get(f"/api/interviews/{iid}/knowledge-search", headers=EXPERT).json()
    rec = data["reconciliation"]
    assert rec["summary"]["total"] >= 2
    for item in rec["items"]:
        assert item["relation"] in ("neu", "ergaenzung", "bestaetigung", "widerspruch")
        assert item["action"] == {"neu": "create", "ergaenzung": "update", "bestaetigung": "none", "widerspruch": "review"}[item["relation"]]
        if item["relation"] == "ergaenzung":
            assert item["target_id"] in {c["id"] for c in item["candidates"]}

    units = client.get(f"/api/interviews/{iid}", headers=EXPERT).json()["units"]
    for u in units:
        client.post(f"/api/units/{u['id']}/review", headers=EXPERT, json={"action": "confirm"})
    exp = client.get(f"/api/v1/export/interviews/{iid}", headers=RAG).json()
    assert exp["schema_version"] == "1.1.0"
    assert all(k["vector_sync"] and k["vector_sync"]["action"] in ("create", "update", "none", "review")
               for k in exp["knowledge_units"])


def test_reconciliation_heuristic_when_llm_fails(client, started, fakes):
    llm, _ = fakes
    _, iv = started
    _answer(client, iv["id"], "Bei älteren Bausparverträgen der Tarifgeneration Classic 2003 gilt eine andere "
                              "Mindestbewertungszahl, das sieht man am Tarifkennzeichen C03 im Bestandssystem.")
    _idle(client, iv["id"])
    orig = llm.structured

    async def fail_reconcile(**kw):
        if kw["schema"].__name__ == "ReconciliationResult":
            raise RuntimeError("LLM down")
        return await orig(**kw)

    llm.structured = fail_reconcile
    r = client.post(f"/api/interviews/{iv['id']}/reconcile", headers=EXPERT).json()
    assert r["items"] and all(i["method"] in ("heuristic", "no_match") for i in r["items"])


def test_adhoc_search_and_status(client):
    assert client.get("/api/knowledge-search/status", headers=ADMIN).json()["provider"] == "local"
    r = client.post("/api/knowledge-search", headers=ADMIN, json={"query": "Onboarding neuer Mitarbeitender mit Paten"}).json()
    assert r["hits"][0]["id"] == "KB-0010"

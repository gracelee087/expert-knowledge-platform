"""Integration-, API- und Interview-Flow-Tests (Szenario: Altverträge / Tarifgenerationen)."""
from conftest import ADMIN, EXPERT, EXPERT_ANSWERS, INTERVIEWER, RAG, wait_idle

from app.agents import schemas as S


def _answer(client, iid, text):
    r = client.post(f"/api/interviews/{iid}/answer", headers=EXPERT, json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_start_requires_consent(client):
    emp = client.post("/api/employees", headers=ADMIN, json={"name": "Peter Klein", "department": "Kredit",
                      "role": "Spezialist", "expertise_area": "Zwischenfinanzierung"}).json()
    iv = client.post(f"/api/employees/{emp['id']}/interviews", headers=ADMIN,
                     json={"title": "Zwischenfinanzierung", "goal": "Sonderfälle sichern", "planned_minutes": 20}).json()
    assert client.post(f"/api/interviews/{iv['id']}/start", headers=EXPERT).status_code == 400  # kein Plan
    client.post(f"/api/interviews/{iv['id']}/plan", headers=ADMIN)
    assert client.post(f"/api/interviews/{iv['id']}/start", headers=EXPERT).status_code == 409  # keine Einwilligung


def test_full_interview_flow(client, started, fakes):
    llm, _ = fakes
    emp, iv = started
    iid = iv["id"]
    assert iv["status"] == "in_progress" and iv["phase"] == "opening"
    assert iv["turns"][0]["speaker"] == "ai" and iv["turns"][0]["technique"] == "clarification"

    r1 = _answer(client, iid, EXPERT_ANSWERS[0])
    assert r1["ai_turn"]["text"].startswith("Verstanden.")
    for a in EXPERT_ANSWERS[1:]:
        _answer(client, iid, a)
    wait_idle(client, iid)

    state = client.get(f"/api/interviews/{iid}", headers=EXPERT).json()
    assert state["phase"] == "exploration"
    assert len([t for t in state["turns"] if t["speaker"] == "expert"]) == 3
    # Keine Frage doppelt gestellt
    ai_q = [t["text"] for t in state["turns"] if t["speaker"] == "ai"]
    assert len(ai_q) == len(set(ai_q))
    # Wissen extrahiert, mit verifizierter Quelle + Zeitstempel
    assert state["unit_count"] >= 2
    for u in state["units"]:
        ref = u["source_refs"][0]
        assert ref["verified"] is True and ref["t_end"] >= ref["t_start"]
        assert u["review_status"] == "extracted"
    # Coverage gesetzt, Validierung nicht durch KI
    assert state["coverage"]["T1"]["fachlich"]["status"] == "partially_covered"
    assert state["coverage"]["T1"]["validierung"]["status"] == "not_started"
    # Lücke dedupliziert (Analyst meldet sie mehrfach)
    assert state["open_gap_count"] == 1

    # Pause/Fortsetzen ohne Zeitverlust und mit Wiedereinstiegsfrage
    p = client.post(f"/api/interviews/{iid}/pause", headers=EXPERT).json()
    assert p["status"] == "paused"
    assert client.post(f"/api/interviews/{iid}/answer", headers=EXPERT, json={"text": "x"}).status_code == 409
    r = client.post(f"/api/interviews/{iid}/resume", headers=EXPERT).json()
    assert r["status"] == "in_progress" and r["turns"][-1]["speaker"] == "ai"

    # Abschluss → Zusammenfassung → Review
    done = client.post(f"/api/interviews/{iid}/finish", headers=EXPERT).json()
    assert done["status"] == "in_review" and done["summary"]["executive_summary"]


def test_review_export_and_training(client, started):
    emp, iv = started
    iid = iv["id"]
    for a in EXPERT_ANSWERS:
        _answer(client, iid, a)
    client.post(f"/api/interviews/{iid}/finish", headers=EXPERT)
    units = client.get(f"/api/interviews/{iid}", headers=EXPERT).json()["units"]
    u1, u2 = units[0], units[1]

    # Export vor Review: nichts Ungeprüftes
    exp = client.get(f"/api/v1/export/interviews/{iid}", headers=RAG).json()
    assert exp["schema"] == "ekep.knowledge-export" and exp["schema_version"] == "1.1.0"
    assert exp["knowledge_units"] == []
    # RAG-Team darf ungeprüfte Inhalte nicht anfordern
    assert client.get(f"/api/v1/export/interviews/{iid}?include_unconfirmed=true", headers=RAG).status_code == 403
    # Training nur aus bestätigtem Wissen
    assert client.post(f"/api/interviews/{iid}/training", headers=ADMIN, json={"audience": "einsteiger"}).status_code == 409

    # Interviewer darf nicht reviewen
    assert client.post(f"/api/units/{u1['id']}/review", headers=INTERVIEWER, json={"action": "confirm"}).status_code == 403
    r = client.post(f"/api/units/{u1['id']}/review", headers=EXPERT, json={"action": "confirm"}).json()
    assert r["unit"]["review_status"] == "confirmed"
    r = client.post(f"/api/units/{u2['id']}/review", headers=EXPERT, json={
        "action": "correct", "fields": {"content": "Classic 2003 hat eine abweichende Mindestbewertungszahl.", "id": "HACK"},
        "comment": "präzisiert"}).json()
    unit = r["unit"]
    assert unit["review_status"] == "corrected" and unit["id"] == u2["id"]
    assert unit["original_extraction"]["content"] != unit["content"]  # Original bleibt nachvollziehbar
    assert [rev["action"] for rev in unit["revisions"]][-1] == "correct"
    client.post(f"/api/units/{u2['id']}/review", headers=EXPERT, json={"action": "add_example", "text": "Vertrag aus 2004 mit C03"})
    for u in units[2:]:
        client.post(f"/api/units/{u['id']}/review", headers=EXPERT, json={"action": "reject"})

    state = client.get(f"/api/interviews/{iid}", headers=EXPERT).json()
    assert state["status"] == "approved"
    assert state["coverage"]["T1"]["validierung"]["status"] == "expert_confirmed"

    exp = client.get(f"/api/v1/export/interviews/{iid}?include_transcript=true", headers=RAG).json()
    ids = {k["knowledge_id"] for k in exp["knowledge_units"]}
    assert ids == {u1["id"], u2["id"]}
    k = next(k for k in exp["knowledge_units"] if k["knowledge_id"] == u2["id"])
    assert k["review"]["expert_confirmed"] and k["confidence"]["semantics"] == "statement_clarity_not_truth"
    assert k["provenance"]["source_refs"][0]["verified"] and k["provenance"]["revisions"]
    assert "Vertrag aus 2004 mit C03" in k["examples"]
    assert exp["expert"]["name"] is None  # Datenminimierung
    assert len(exp["transcript"]) >= 7

    t = client.post(f"/api/interviews/{iid}/training", headers=ADMIN, json={"audience": "einsteiger"}).json()
    assert "KU-FAKE" not in t["content"]["slides"][0]["source_unit_ids"]  # erfundene Referenzen entfernt
    assert t["content"]["quiz"][0]["correct_index"] == 1  # auf gültigen Bereich begrenzt

    audit = client.get("/api/audit", headers=ADMIN).json()
    actions = {a["action"] for a in audit}
    assert {"interview.consent_given", "unit.confirm", "unit.correct", "export.generated"} <= actions


def test_rbac(client):
    assert client.get("/api/employees").status_code == 401
    assert client.post("/api/employees", headers=EXPERT, json={"name": "X Y", "department": "a", "role": "b",
                       "expertise_area": "c"}).status_code == 403
    assert client.get("/api/employees", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/employees", headers=RAG).status_code == 403
    assert client.get("/api/v1/export/schema").status_code == 200


def test_extraction_quality_guards(client, started, fakes):
    """Halluzinierte Zitate werden herabgestuft, Einheiten ohne Quelle verworfen,
    bestätigtes Wissen wird von der KI nicht überschrieben."""
    llm, _ = fakes
    _, iv = started
    iid = iv["id"]

    def bad_analysis(user):
        tid = list(llm.expert_texts(user))[-1]
        base = dict(existing_unit_id=None, topic_id="T9-unknown", category="erfahrungswissen", explanation="",
                    prerequisites=[], process_steps=[], decision_logic=None, exceptions=[], examples=[], risks=[],
                    dependencies=[], escalation_paths=[], common_mistakes=[], historical_changes=[], validity_note=None,
                    confidence="hoch", confidence_reason="", uncertainties=[])
        return S.AnalysisResult(units=[
            S.ExtractedUnit(**base, title="Erfunden", content="Zuteilung immer nach 7 Jahren",
                            source_quotes=[S.SourceQuote(turn_id=tid, quote="die Zuteilung erfolgt grundsätzlich nach sieben Jahren")]),
            S.ExtractedUnit(**base, title="Ohne Quelle", content="…", source_quotes=[]),
        ], coverage_updates=[], new_gaps=[], resolved_gap_ids=[], contradictions=[
            S.ContradictionItem(description="Mindestbewertungszahl widersprüchlich", statement_a="a", statement_b="b", turn_ids=[tid])],
            new_topics=[S.NewTopic(title="Wohnungsbauprämie bei Altverträgen", description="vom Experten erwähnt")])

    llm.analysis_override = bad_analysis
    _answer(client, iid, EXPERT_ANSWERS[1])
    wait_idle(client, iid)
    st = client.get(f"/api/interviews/{iid}", headers=EXPERT).json()
    assert [u["title"] for u in st["units"]] == ["Erfunden"]
    u = st["units"][0]
    assert u["confidence"] == "niedrig" and u["source_refs"][0]["verified"] is False
    assert any("nicht wörtlich" in x for x in u["uncertainties"])
    assert u["topic_id"] == "T1"  # unbekanntes Thema → aktuelles Thema
    assert st["open_contradiction_count"] == 1
    assert any(t["title"] == "Wohnungsbauprämie bei Altverträgen" and t["origin"] == "emerged" for t in st["plan"]["topics"])


def test_interviewer_failure_is_recoverable(client, started, fakes):
    llm, _ = fakes
    _, iv = started
    llm.fail_interviewer = True
    r = client.post(f"/api/interviews/{iv['id']}/answer", headers=EXPERT, json={"text": EXPERT_ANSWERS[1]})
    assert r.status_code == 502
    llm.fail_interviewer = False
    r = client.post(f"/api/interviews/{iv['id']}/regenerate", headers=EXPERT)
    assert r.status_code == 200 and r.json()["speaker"] == "ai"
    turns = client.get(f"/api/interviews/{iv['id']}", headers=EXPERT).json()["turns"]
    assert [t["speaker"] for t in turns][-2:] == ["expert", "ai"]  # Expertenantwort ging nicht verloren


def test_closing_phase_finishes_interview(client, started, fakes):
    llm, _ = fakes
    _, iv = started
    iid = iv["id"]
    orig = llm.structured

    async def closing(**kw):
        if kw["schema"] is S.InterviewerDecision:
            llm.calls.append("InterviewerDecision")
            return S.InterviewerDecision(action="close", technique="closing", topic_id="T1", acknowledgement="",
                                         question="Was sollte unbedingt noch festgehalten werden?", rationale="Zeit",
                                         current_topic_saturated=True, expert_reached_knowledge_limit=False,
                                         last_answer_information_gain="mittel", estimated_remaining_questions=0)
        return await orig(**kw)

    llm.structured = closing
    _answer(client, iid, EXPERT_ANSWERS[0])
    assert client.get(f"/api/interviews/{iid}", headers=EXPERT).json()["phase"] == "closing"
    r = _answer(client, iid, "Dass man bei C03 immer das Archiv prüfen sollte.")
    assert r["finished"] is True
    client.post(f"/api/interviews/{iid}/finish", headers=EXPERT)
    st = client.get(f"/api/interviews/{iid}", headers=EXPERT).json()
    assert st["status"] in ("in_review", "approved") and st["phase"] == "done"


def test_websocket_receives_events(client, started):
    _, iv = started
    with client.websocket_connect(f"/ws/interviews/{iv['id']}") as ws:
        first = ws.receive_json()
        assert first["event"] == "state" and first["data"]["id"] == iv["id"]
        _answer(client, iv["id"], EXPERT_ANSWERS[0])
        events = [ws.receive_json()["event"] for _ in range(3)]
        assert "turn_added" in events


def test_low_information_gain_saturates_topic(client, started, fakes):
    """2× 'niedrig' in Folge → Thema deterministisch gesättigt, nächste Anweisung = Themenwechsel."""
    llm, _ = fakes
    _, iv = started
    llm.gain = "niedrig"
    _answer(client, iv["id"], "Das hatte ich ja schon gesagt.")
    _answer(client, iv["id"], "Wie gesagt, das ist eigentlich alles.")
    st = client.get(f"/api/interviews/{iv['id']}", headers=EXPERT).json()
    t1 = next(t for t in st["plan"]["topics"] if t["id"] == "T1")
    assert t1["status"] == "saturated"
    assert st["progress"]["recent_gains"][-2:] == ["niedrig", "niedrig"]
    _answer(client, iv["id"], "Noch etwas?")
    assert "gesättigt" in llm.instructions[-1]


def test_question_budget_forces_closing(client, fakes):
    """Fragen-Budget (geplante Minuten / 1,5, min. 6) begrenzt das Interview sichtbar."""
    llm, _ = fakes
    emp = client.post("/api/employees", headers=ADMIN, json={"name": "Jürgen Brandt", "department": "Baufinanzierung",
                      "role": "Kreditspezialist", "expertise_area": "Vor- und Zwischenfinanzierung"}).json()
    iv = client.post(f"/api/employees/{emp['id']}/interviews", headers=ADMIN,
                     json={"title": "Zwischenfinanzierung", "goal": "Sonderfälle sichern", "planned_minutes": 5}).json()
    client.post(f"/api/interviews/{iv['id']}/plan", headers=ADMIN)
    client.post(f"/api/interviews/{iv['id']}/consent", headers=EXPERT)
    st = client.post(f"/api/interviews/{iv['id']}/start", headers=EXPERT).json()
    assert st["progress"]["question_budget"] == 6 and st["progress"]["questions_asked"] == 1
    for i in range(5):  # 1 Eröffnungsfrage + 5 Antworten → 5 von 6 Fragen gestellt
        _answer(client, iv["id"], f"Antwort {i}: Bei Zwischenfinanzierungen prüfe ich zuerst die Zuteilungsreife.")
    assert "fast erreicht" in llm.instructions[-1]  # 80 % → Validierung
    _answer(client, iv["id"], "Ja, das ist korrekt so.")
    assert "ausgeschöpft" in llm.instructions[-1]  # 100 % → Abschluss
    st = client.get(f"/api/interviews/{iv['id']}", headers=EXPERT).json()
    assert st["progress"]["remaining_estimate"] == 0

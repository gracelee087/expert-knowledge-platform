"""Unit Tests: deterministische Schutzmechanismen und Coverage-Logik."""
from types import SimpleNamespace

from app.services import coverage as cov
from app.services.guards import quote_match_score, redact_pii, verify_sources


def test_quote_exact_and_punctuation_tolerant():
    text = "Bei älteren Bausparverträgen der Tarifgeneration Classic 2003 gilt eine andere Mindestbewertungszahl."
    assert quote_match_score("Tarifgeneration Classic 2003 gilt eine andere", text) == 1.0
    assert quote_match_score("„Tarifgeneration Classic 2003, gilt eine andere“", text) >= 0.95


def test_hallucinated_quote_is_not_verified():
    turns = {"turn_a": SimpleNamespace(id="turn_a", text="Das Tarifkennzeichen steht im Bestandssystem.", t_start=1, t_end=4)}
    refs = verify_sources([{"turn_id": "turn_a", "quote": "Die Zuteilung erfolgt immer nach 7 Jahren"}], turns)
    assert refs[0]["verified"] is False


def test_wrong_turn_id_is_recovered_by_search():
    turns = {"turn_a": SimpleNamespace(id="turn_a", text="Wir prüfen zuerst das Abschlussdatum im Archiv.", t_start=1, t_end=4)}
    refs = verify_sources([{"turn_id": "turn_ai_question", "quote": "prüfen zuerst das Abschlussdatum"}], turns)
    assert refs[0]["verified"] and refs[0]["turn_id"] == "turn_a" and refs[0]["t_start"] == 1


def test_pii_redaction():
    text, n = redact_pii("Kunde Müller, IBAN DE89 3704 0044 0532 0130 00, Mail max@beispiel.de, Tel 069 1234567")
    assert n == 3
    assert "DE89" not in text and "max@beispiel.de" not in text and "1234567" not in text


def test_pii_redaction_keeps_domain_numbers():
    text, n = redact_pii("Die Mindestbewertungszahl lag bei 300 und die Bausparsumme bei 50.000 Euro, Tarif von 2003.")
    assert n == 0


def test_ai_cannot_set_expert_confirmed_or_validation():
    c = cov.ensure_topics({}, ["T1"])
    c = cov.apply_ai_update(c, "T1", "fachlich", "expert_confirmed", "x")
    assert c["T1"]["fachlich"]["status"] == "not_started"
    c = cov.apply_ai_update(c, "T1", "validierung", "covered", "x")
    assert c["T1"]["validierung"]["status"] == "not_started"


def test_coverage_does_not_regress_silently_but_accepts_clarification():
    c = cov.ensure_topics({}, ["T1"])
    c = cov.apply_ai_update(c, "T1", "prozess", "covered", "a")
    c = cov.apply_ai_update(c, "T1", "prozess", "partially_covered", "b")
    assert c["T1"]["prozess"]["status"] == "covered"
    c = cov.apply_ai_update(c, "T1", "prozess", "needs_clarification", "Widerspruch")
    assert c["T1"]["prozess"]["status"] == "needs_clarification"


def test_validation_only_from_review():
    c = cov.ensure_topics({}, ["T1"])
    c = cov.apply_ai_update(c, "T1", "fachlich", "covered", "a")
    units = [SimpleNamespace(topic_id="T1", review_status="confirmed"), SimpleNamespace(topic_id="T1", review_status="extracted")]
    d = cov.derive_with_review(c, units)
    assert d["T1"]["validierung"]["status"] == "partially_covered"
    assert d["T1"]["fachlich"]["status"] == "covered"
    units[1].review_status = "corrected"
    d = cov.derive_with_review(c, units)
    assert d["T1"]["validierung"]["status"] == "expert_confirmed"
    assert d["T1"]["fachlich"]["status"] == "expert_confirmed"


def test_overall_score_weights_priority():
    c = cov.ensure_topics({}, ["T1", "T2"])
    for d in ["fachlich", "prozess", "entscheidung"]:
        c = cov.apply_ai_update(c, "T1", d, "covered", "")
    hi = cov.overall_score(c, [{"id": "T1", "priority": "hoch"}, {"id": "T2", "priority": "niedrig"}])
    lo = cov.overall_score(c, [{"id": "T1", "priority": "niedrig"}, {"id": "T2", "priority": "hoch"}])
    assert hi > lo > 0

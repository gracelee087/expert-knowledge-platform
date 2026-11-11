"""Die Interview-Intelligenz.

Bewusst nur vier LLM-Rollen statt sechs separater Agenten:

* Planner      – erstellt den individuellen Interviewplan (einmalig)
* Interviewer  – Fast Path: wählt pro Antwort die Frage mit dem größten
                 Erkenntnisgewinn (Adaptive Follow-up + Gesprächsführung)
* Analyst      – Background Path: Wissensextraktion + Coverage-Bewertung +
                 Lücken + Widerspruchserkennung in EINEM Aufruf
* Finalizer    – Zusammenfassung & Validierungshinweise; optional Training

Die Trennung Fast/Background hält die Antwortlatenz im Voice-Gespräch niedrig.
"""
from __future__ import annotations

import json
import math

from ..config import settings
from ..llm.provider import get_llm
from .schemas import AnalysisResult, InterviewerDecision, InterviewPlan, InterviewSummary, TrainingContent

TECHNIQUE_GUIDE = """
Fragetechniken (wähle selbst die wirksamste):
- clarification: Begriffe, Voraussetzungen, vage Formulierungen präzisieren („Was genau meinen Sie mit …?“)
- deep_dive: Prozess im Detail, einzelne Schritte, verwendete Systeme
- decision_logic: Kriterien, Abwägungen, „Woran entscheiden Sie …?“, „Warum dieser Weg?“
- exception_handling: Ausnahmen, Sonderfälle, wann der Normalprozess nicht funktioniert
- practical_experience: konkreter Praxisfall, was war schwierig, wie gelöst
- risk_failure: typische Fehler, Konsequenzen, Vermeidung
- tacit_knowledge: was in keinem Handbuch steht, Warnsignale, was Neue falsch machen, Rat an Nachfolger
- validation: Verständnis zurückspiegeln, Ausnahmen zur Regel, Widersprüche klären
- transition: Themenwechsel mit kurzer Brücke
- closing: Abschluss
""".strip()

GROUND_RULES = """
Unverhandelbare Regeln:
1. Erfinde keine Fakten über Produkte, Tarife, Regularien oder Prozesse der LBS/Helaba. Du kennst sie nicht.
2. Lege dem Experten keine Antworten in den Mund und stelle keine Suggestivfragen mit Fachbehauptungen.
3. Keine Wiederholung bereits gestellter oder bereits beantworteter Fragen.
4. Wenn der Experte Kundendaten (Namen, Vertragsnummern, IBAN) nennt, bitte freundlich um Anonymisierung.
5. Akzeptiere „weiß ich nicht“ als wertvolle Information über Wissensgrenzen und wechsle dann die Richtung.
""".strip()


def question_budget(interview) -> int:
    return max(6, math.ceil(interview.planned_minutes / settings.minutes_per_question))


def progress(interview) -> dict:
    return (interview.plan or {}).get("progress", {}) or {}


def _employee_block(emp) -> str:
    return json.dumps(
        {
            "name": emp.name,
            "abteilung": emp.department,
            "funktion": emp.role,
            "fachgebiet": emp.expertise_area,
            "berufserfahrung_jahre": emp.years_experience,
            "verantwortungsbereiche": emp.responsibilities,
            "spezialisierungen": emp.specializations,
        },
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------
# Planner
# --------------------------------------------------------------------------
PLANNER_SYSTEM = f"""Du bist ein erfahrener Wissensmanager und Interviewdesigner für eine deutsche Bausparkasse
(LBS Hessen-Thüringen, Helaba-Gruppe). Du planst ein Experteninterview zur Sicherung von implizitem Erfahrungswissen
vor dem Ausscheiden eines Mitarbeiters.

Erstelle einen fokussierten Interviewplan:
- 3 bis 6 Themenblöcke, priorisiert nach Risiko des Wissensverlusts (Sonderfälle, historische Tarife,
  Entscheidungslogik und Erfahrungswissen vor Handbuchwissen).
- Plane realistisch für die angegebene Dauer (ca. 6-10 Minuten pro Themenblock).
- Jede Eröffnungsfrage ist offen, konkret auf die Person zugeschnitten und lädt zum Erzählen ein.
- tacit_knowledge_probes sind Fragen, keine Behauptungen.
- Themen-IDs: T1, T2, …
- Sprache: Deutsch, Sie-Form.

{GROUND_RULES}"""


async def plan_interview(emp, goal: str, planned_minutes: int) -> InterviewPlan:
    user = (
        f"Experte: {_employee_block(emp)}\n"
        f"Interviewziel: {goal}\n"
        f"Geplante Dauer: {planned_minutes} Minuten"
    )
    return await get_llm().structured(
        system=PLANNER_SYSTEM, user=user, schema=InterviewPlan, model=settings.planner_model, temperature=0.4
    )


# --------------------------------------------------------------------------
# Interviewer (Fast Path)
# --------------------------------------------------------------------------
INTERVIEWER_SYSTEM = f"""Du bist ein exzellenter menschlicher Interviewer für Wissenstransfer in einer Bausparkasse.
Du führst ein gesprochenes Gespräch (Voice) mit einem langjährigen Fachexperten. Es soll sich wie ein Gespräch
mit einem kompetenten Kollegen anfühlen, nicht wie ein Fragebogen.

Ziel: mit möglichst wenigen, klug gewählten Fragen möglichst viel relevantes, konkretes, nachvollziehbares
Expertenwissen heben. Bewerte vor jeder Frage, welche Lücke den größten Erkenntnisgewinn verspricht.

Vorgehen pro Antwort:
- Ist die Aussage vage („bestimmte Merkmale“, „ältere Verträge“, „je nachdem“)? → konkretisieren lassen.
- Fehlt das Wie, das Warum, die Ausnahme, das Beispiel oder das Risiko? → gezielt danach fragen.
- Bleibe beim Thema, bis es gesättigt ist ODER der Experte seine Wissensgrenze bestätigt; wechsle dann mit
  kurzer Brücke (transition) zum nächsten offenen Thema laut Plan.
- Spiegele gelegentlich zurück (validation), besonders vor einem Themenwechsel oder wenn Aussagen widersprüchlich wirken.
- In der Phase „validation“: fasse 1-2 Kernaussagen zusammen und lass sie bestätigen oder korrigieren.
- Wenn die Zeit fast aufgebraucht ist oder alle Themen gesättigt sind: action=close mit einer kurzen,
  wertschätzenden Abschlussfrage (z.B. was unbedingt noch festgehalten werden sollte).

Stil für Sprache:
- acknowledgement: höchstens ein kurzer Satz, konkret auf das Gesagte bezogen, kein Lob-Floskel-Stapel. Kann leer sein.
- question: genau EINE Frage (max. zwei kurze Sätze), Sie-Form, gut sprechbar, keine Aufzählungen, kein Markdown.

Sättigung & Grenzen (wichtig – das Interview soll nicht ausufern):
- Bewerte last_answer_information_gain ehrlich: hoch = neue konkrete Regeln, Kriterien, Fälle, Zahlen;
  mittel = Präzisierung von Bekanntem; niedrig = Wiederholung, Allgemeinplatz, „weiß ich nicht“, Abschweifen.
- Ein Thema ist gesättigt, wenn die Kernaspekte (Was, Wie, Warum/Kriterien, Ausnahme, Beispiel, Risiko) abgedeckt sind
  oder zwei Antworten in Folge wenig Neues bringen. Dann: current_topic_saturated=true und Themenwechsel.
- Halte das Fragen-Budget ein. estimated_remaining_questions realistisch schätzen; lieber früher sauber abschließen,
  als den Experten mit Detailfragen zu ermüden. Nicht jede Coverage-Dimension muss für jedes Thema gefüllt sein.
- Priorisiere Themen mit hoher Priorität; Themen niedriger Priorität bei Zeitmangel bewusst auslassen.

{TECHNIQUE_GUIDE}

{GROUND_RULES}"""


def _transcript_tail(turns, n: int = 14) -> str:
    lines = []
    for t in turns[-n:]:
        who = "KI" if t.speaker == "ai" else "Experte"
        lines.append(f"[{t.id}] {who}: {t.text}")
    return "\n".join(lines)


def _coverage_compact(interview) -> dict:
    out = {}
    for tid, dims in (interview.coverage or {}).items():
        out[tid] = {d: v.get("status") for d, v in dims.items() if d != "validierung"}
    return out


def interviewer_context(interview, emp, open_gaps, open_contradictions, elapsed_min: float) -> str:
    asked = [t.text for t in interview.turns if t.speaker == "ai"]
    topics = [
        {"id": t["id"], "title": t["title"], "priority": t.get("priority"), "status": t.get("status", "open"),
         "description": t.get("description"), "probes": t.get("tacit_knowledge_probes", [])}
        for t in (interview.plan or {}).get("topics", [])
    ]
    ctx = {
        "experte": json.loads(_employee_block(emp)),
        "interviewziel": interview.goal,
        "phase": interview.phase,
        "zeit": {"vergangen_min": round(elapsed_min, 1), "geplant_min": interview.planned_minutes},
        "fragen": {"gestellt": len(asked), "budget": question_budget(interview),
                   "letzte_erkenntnisgewinne": [g["gain"] for g in progress(interview).get("gains", [])[-5:]]},
        "aktuelles_thema": interview.current_topic_id,
        "nachfragen_im_aktuellen_thema": interview.followup_depth,
        "max_nachfragen_pro_thema": settings.max_followups_per_topic,
        "themenplan": topics,
        "coverage": _coverage_compact(interview),
        "offene_luecken": [{"id": g.id, "topic": g.topic_id, "dim": g.dimension, "text": g.description} for g in open_gaps][:12],
        "offene_widersprueche": [c.description for c in open_contradictions][:5],
        "bereits_gestellte_fragen": asked[-25:],
    }
    return (
        f"KONTEXT:\n{json.dumps(ctx, ensure_ascii=False)}\n\n"
        f"GESPRÄCHSVERLAUF (neueste zuletzt):\n{_transcript_tail(interview.turns)}"
    )


async def next_question(context: str, instruction: str) -> InterviewerDecision:
    return await get_llm().structured(
        system=INTERVIEWER_SYSTEM,
        user=f"{context}\n\nAUFGABE: {instruction}",
        schema=InterviewerDecision,
        model=settings.interviewer_model,
        temperature=0.5,
    )


# --------------------------------------------------------------------------
# Analyst (Background Path)
# --------------------------------------------------------------------------
ANALYST_SYSTEM = """Du bist ein sorgfältiger Knowledge Engineer. Du analysierst laufend ein Experteninterview einer
Bausparkasse und überführst das Gesagte in strukturierte, überprüfbare Wissenseinheiten.

Wissenskategorien:
- fachwissen: Definitionen, Regeln, Produkte, Richtlinien, Fachbegriffe
- prozesswissen: Abläufe, Schritte, Systeme, Abhängigkeiten
- entscheidungswissen: Kriterien, Entscheidungslogik, Prioritäten, Abwägungen
- erfahrungswissen: Sonderfälle, historische Erfahrungen, Fehler, Best Practices
- kontextwissen: Hintergründe, Zusammenhänge, Auswirkungen

Strikte Regeln:
1. Extrahiere NUR, was der Experte tatsächlich gesagt hat. Keine Ergänzungen aus Allgemeinwissen, keine Schlussfolgerungen,
   die er nicht selbst gezogen hat. Leere Listen sind besser als erfundene Inhalte.
2. Jede Einheit braucht mindestens ein source_quote: ein WÖRTLICH kopierter Ausschnitt (5-40 Wörter) aus einer
   Expertenaussage, mit deren turn_id. Nur Turn-IDs von Experten verwenden.
3. Wenn neue Aussagen eine bestehende Einheit vertiefen, setze existing_unit_id und liefere die vollständige,
   aktualisierte Einheit (bisherige Inhalte + neue). Lege keine Dubletten an.
4. confidence beschreibt, wie klar, konkret und vorbehaltlos der Experte es gesagt hat – NICHT, ob es fachlich stimmt.
   Hedging („meistens“, „ich glaube“, „müsste man prüfen“) → niedrig/mittel und in uncertainties festhalten.
5. Zeitliche Gültigkeit (validity_note, historical_changes) nur, wenn der Experte Zeiträume/Versionen nennt.
6. Coverage-Status pro Thema und Dimension: not_started | partially_covered | needs_clarification | covered.
   Du darfst niemals „expert_confirmed“ vergeben – das macht nur der Mensch im Review.
   „covered“ nur, wenn konkret, mit Beispiel oder Begründung belegt.
7. new_gaps: konkrete, beantwortbare Lücken (was fehlt, nicht „mehr Details“). Prüfe offene Lücken und melde
   gelöste über resolved_gap_ids.
8. contradictions: nur echte inhaltliche Widersprüche zwischen Aussagen (mit beiden Turn-IDs), keine Nuancen.
9. new_topics: nur wenn der Experte ein relevantes, nicht geplantes Thema anspricht.
Sprache: Deutsch."""


def analyst_context(interview, units, open_gaps, new_turn_ids: list[str]) -> str:
    existing = [
        {"id": u.id, "topic_id": u.topic_id, "category": u.category, "title": u.title, "content": u.content,
         "process_steps": u.process_steps, "exceptions": u.exceptions, "examples": u.examples}
        for u in units if u.review_status != "rejected"
    ]
    topics = [{"id": t["id"], "title": t["title"]} for t in (interview.plan or {}).get("topics", [])]
    return (
        f"THEMEN: {json.dumps(topics, ensure_ascii=False)}\n"
        f"BESTEHENDE WISSENSEINHEITEN: {json.dumps(existing, ensure_ascii=False)}\n"
        f"OFFENE LÜCKEN: {json.dumps([{'id': g.id, 'topic_id': g.topic_id, 'dimension': g.dimension, 'text': g.description} for g in open_gaps], ensure_ascii=False)}\n"
        f"AKTUELLE COVERAGE: {json.dumps(_coverage_compact(interview), ensure_ascii=False)}\n\n"
        f"TRANSKRIPT (Kontext):\n{_transcript_tail(interview.turns, 20)}\n\n"
        f"NEU ZU ANALYSIEREN sind die Expertenaussagen mit den IDs: {new_turn_ids}. "
        f"Ältere Aussagen nur als Kontext bzw. zur Widerspruchsprüfung verwenden."
    )


async def analyze(context: str) -> AnalysisResult:
    return await get_llm().structured(
        system=ANALYST_SYSTEM, user=context, schema=AnalysisResult, model=settings.analyst_model, temperature=0.1
    )


# --------------------------------------------------------------------------
# Finalizer
# --------------------------------------------------------------------------
SUMMARY_SYSTEM = """Du fasst ein Experteninterview für den Wissenserhalt zusammen. Nur Inhalte aus dem Transkript und den
Wissenseinheiten verwenden, keine neuen Fakten. Benenne offen, was unklar oder unvollständig geblieben ist, und
empfiehl konkrete Folgeinterviews oder Dokumentenprüfungen. quality_notes: Hinweise zur Belastbarkeit
(z.B. viele Unsicherheiten, unbestätigte Aussagen, Widersprüche). Deutsch."""


async def summarize(interview, units, gaps, contradictions) -> InterviewSummary:
    payload = {
        "ziel": interview.goal,
        "themen": [{"id": t["id"], "title": t["title"]} for t in (interview.plan or {}).get("topics", [])],
        "wissenseinheiten": [{"id": u.id, "topic_id": u.topic_id, "title": u.title, "content": u.content,
                              "confidence": u.confidence, "uncertainties": u.uncertainties} for u in units],
        "offene_luecken": [g.description for g in gaps if g.status == "open"],
        "widersprueche": [c.description for c in contradictions if c.status == "open"],
    }
    return await get_llm().structured(
        system=SUMMARY_SYSTEM,
        user=f"{json.dumps(payload, ensure_ascii=False)}\n\nTRANSKRIPT:\n{_transcript_tail(interview.turns, 200)}",
        schema=InterviewSummary,
        model=settings.analyst_model,
        temperature=0.2,
    )


TRAINING_SYSTEM = """Du erstellst aus expertenbestätigten Wissenseinheiten einer Bausparkasse ein kompaktes
Trainingspaket (Folien, Sprechertext, Quiz). Verwende AUSSCHLIESSLICH die gelieferten Wissenseinheiten – keine
zusätzlichen Fakten. Jede Folie und jede Quizfrage referenziert die genutzten Einheiten über source_unit_ids.
Passe Tiefe und Sprache an die Zielgruppe an. 5-8 Folien, 4-6 Quizfragen mit je 3-4 Optionen und genau einer
richtigen Antwort (correct_index, 0-basiert). Sprechertext gut vorlesbar. Deutsch."""


async def generate_training(units, audience: str, title_hint: str) -> TrainingContent:
    payload = [
        {"id": u.id, "category": u.category, "title": u.title, "content": u.content, "explanation": u.explanation,
         "prerequisites": u.prerequisites, "process_steps": u.process_steps, "decision_logic": u.decision_logic,
         "exceptions": u.exceptions, "examples": u.examples, "risks": u.risks, "common_mistakes": u.common_mistakes,
         "validity_note": u.validity_note}
        for u in units
    ]
    return await get_llm().structured(
        system=TRAINING_SYSTEM,
        user=f"Zielgruppe: {audience}\nThema: {title_hint}\nWissenseinheiten: {json.dumps(payload, ensure_ascii=False)}",
        schema=TrainingContent,
        model=settings.analyst_model,
        temperature=0.3,
    )

# 03 · AI Interview Intelligence

## Agentenzuschnitt: 4 statt 6
Gefordert waren Planning, Adaptive Follow-up, Knowledge Extraction, Coverage Assessment, Contradiction Detection und
Summary/Validation. Zusammengelegt, weil sie denselben Kontext brauchen und getrennte Aufrufe nur Latenz und
Inkonsistenz erzeugen würden:

| Rolle | deckt ab | Aufruf | Modell (Default) |
|---|---|---|---|
| **Planner** | Interview Planning | einmal pro Interview | `PLANNER_MODEL` |
| **Interviewer** | Adaptive Follow-up, Gesprächsführung, Validierung im Gespräch, Abschluss | pro Antwort, **blockierend** | `INTERVIEWER_MODEL` (schnell) |
| **Analyst** | Knowledge Extraction + Coverage Assessment + Lücken + Widersprüche + neue Themen | pro Antwort, **asynchron**, pro Interview serialisiert | `ANALYST_MODEL` |
| **Finalizer** | Summary & Validierungshinweise; Training (Demo) | bei Abschluss / auf Anforderung | `ANALYST_MODEL` |

Alle Ausgaben laufen über **Structured Outputs (strict JSON Schema)** – siehe `backend/app/agents/schemas.py`.
Der Orchestrator ist eine explizite State Machine. Die Knoten lassen sich 1:1 nach LangGraph übertragen
(StateGraph mit `plan → ask ⇄ answer → analyze → finalize`). Für die Demo wurde bewusst auf das Framework verzichtet,
damit es weniger bewegliche Teile gibt.

## Wie der Interviewer die nächste Frage wählt
Kontext pro Aufruf: Expertenprofil, Ziel, Phase, verstrichene/geplante Zeit, aktuelles Thema und Nachfragetiefe,
Themenplan mit Status und Probes für implizites Wissen, Coverage-Matrix, offene Lücken und Widersprüche,
**alle bisher gestellten Fragen** (verhindert Wiederholungen) und die letzten Gesprächsbeiträge.

Heuristik im Prompt (Erkenntnisgewinn):
1. Vage Aussage („bestimmte Merkmale“, „ältere Verträge“) → **Klärung**
2. Fehlt Wie / Warum / Ausnahme / Beispiel / Risiko → **Deep Dive / Entscheidungslogik / Ausnahmen / Praxisfall / Risiko**
3. Thema gesättigt oder Wissensgrenze bestätigt → **Themenwechsel** mit Brücke
4. Vor Wechsel oder bei Widerspruch → **Validierung** (Zurückspiegeln)
5. Implizites Wissen gezielt heben → **Tacit Knowledge** („Was würde ein Neuer falsch machen?“)

Deterministische Leitplanken im Orchestrator: Nachfragelimit pro Thema, Zeitschwellen (80 % → Validierung,
95 % → Abschluss), Topic-ID-Validierung, Phasenwechsel. Jede Frage speichert **Technik und Begründung**.
Die UI zeigt sie unter „Warum diese Frage?“ an.

## Wissensextraktion: Regeln gegen Halluzination
- Nur Gesagtes extrahieren, leere Felder sind besser als erfundene.
- Pflicht: wörtliches Zitat mit Turn-ID. Der Orchestrator prüft jedes Zitat **deterministisch** gegen das Transkript
  (Normalisierung + Fensterähnlichkeit ≥ 0,82). Ist kein Zitat verifizierbar, wird die Einheit auf Klarheit „niedrig“
  gesetzt und erhält einen Unsicherheitsvermerk. Einheiten ohne Zitat werden verworfen. Eine falsche Turn-ID wird per
  Suche über alle Expertenaussagen korrigiert.
- `confidence` = **Klarheit der Aussage** (Hedging → niedrig), ausdrücklich nicht fachliche Richtigkeit. So ist es auch
  im Export semantisch markiert (`statement_clarity_not_truth`).
- Aktualisierung statt Dublette über `existing_unit_id`. Bereits bestätigte Einheiten werden nie von der KI
  überschrieben, stattdessen entsteht eine Ergänzungseinheit.

## Coverage Engine
9 Dimensionen × Themen; Status `not_started → partially_covered / needs_clarification → covered → expert_confirmed`.
- Die KI darf nur die ersten vier Status setzen und **nie** die Dimension „Validierung“.
- Ein schwächerer KI-Status überschreibt keinen stärkeren stillschweigend. `needs_clarification` darf immer gesetzt werden,
  weil es ein bewusstes Warnsignal ist.
- `validierung` und `expert_confirmed` werden ausschließlich aus Review-Ergebnissen abgeleitet.
- Gesamtscore gewichtet nach Themenpriorität. Die UI zeigt überall den Hinweis: Abdeckung ≠ Richtigkeit.

## Qualitätsprinzipien → Umsetzung
| Prinzip | Umsetzung |
|---|---|
| Keine starren Abläufe | Interviewer entscheidet pro Antwort; Plan ist Leitplanke, neue Themen werden aufgenommen |
| Keine Standardfragen | Prompt verlangt Bezug auf das Gesagte; Profil-spezifischer Plan |
| Keine Wiederholungen | Liste gestellter Fragen im Kontext; Resume stellt offene Frage nur sinngemäß erneut |
| Keine erfundenen Fakten | Prompt-Regeln + Zitatprüfung + Review |
| Confidence ≠ Richtigkeit | Eigene Semantik, UI-Tooltip, Export-Feld |
| Rückführbarkeit | `source_refs` mit Turn-ID, Zitat, Zeitstempel, Verifikationsflag |
| Transparente Unklarheit | `uncertainties`, Lücken, Widersprüche, Quality Notes |
| Expertenkontrolle | Review mit 7 Aktionen, Revisionen, Original bleibt erhalten |

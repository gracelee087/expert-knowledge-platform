# Expert Knowledge Extraction Platform

**Phase 1 · Knowledge Acquisition** für LBS Hessen-Thüringen / Helaba (FUTURY Build Days #4).
Ein KI-Interviewer führt Voice-Interviews mit langjährigen Fachexperten. Er stellt gezielte Folgefragen, erkennt Lücken
und Widersprüche, extrahiert strukturierte Wissenseinheiten mit Quellenzitat und lässt sie vom Experten prüfen.
Das Ergebnis geht über einen versionierten JSON-Vertrag an das RAG-Team.

## Schnellstart (Docker)
```bash
cp .env.example .env        # hIP-Key bei OPENAI_API_KEY eintragen (Gateway ist vorkonfiguriert)
docker compose up --build
docker compose exec backend python scripts/check_llm.py --all   # Modelle + JSON-Fähigkeit prüfen
```

### LLM-Gateway (hIP / hessian.AI)
Die App spricht jedes OpenAI-kompatible Gateway an. Voreingestellt ist `https://hip-gw.hessian.ai/v1` mit `gemma-4-31B-it`.
Für strukturierte Ausgaben wird zuerst `json_schema` versucht. Unterstützt ein Modell das nicht, wechselt die App automatisch
auf Prompt-JSON mit Pydantic-Validierung und einer Reparaturrunde. Bei Gemma/Qwen wird Thinking per
`x-bf-passthrough-extra-params` abgeschaltet, damit die Antwortzeit im Gespräch niedrig bleibt.
`scripts/check_llm.py` listet die Modell-IDs und misst pro Modell Latenz und Schema-Treue. Danach tragt ihr
z. B. Mistral Medium 3.5 (EU-Anbieter) als `ANALYST_MODEL` ein.
Das Gateway bietet keine Sprachmodelle, deshalb läuft die Sprache über den Browser (`VOICE_ENGINE=browser`).
Frontend: http://localhost:3000 · API-Doku: http://localhost:8000/docs

## Schnellstart ohne Docker
```bash
# Backend (SQLite als Fallback)
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
set -a; source ../.env; set +a
uvicorn app.main:app --reload --port 8000

# Frontend (zweites Terminal)
cd frontend && npm install && npm run dev
```
Chrome oder Edge verwenden. `localhost` gilt als sicherer Kontext, deshalb klappt der Mikrofonzugriff ohne HTTPS.

### Voice-Engine
| `VOICE_ENGINE` | Benötigte Modelle | Spracherkennung / -ausgabe |
|---|---|---|
| `browser` (Default) | nur ein Chat-Modell, z. B. `gpt-4o` | Web Speech API (Chrome/Edge) + `speechSynthesis`, mit Live-Zwischentranskript |
| `openai` | zusätzlich STT (`gpt-4o-transcribe`/`whisper-1`) und TTS (`gpt-4o-mini-tts`/`tts-1`) | serverseitig, Audio wird nach der Transkription verworfen |

Hinweis Datenschutz: Im Browser-Modus übergibt Chrome das Audio an den Spracherkennungsdienst von Google. Für die Demo mit
fiktiven Inhalten ist das in Ordnung. Produktiv gehört STT auf die Helaba-KI-Plattform (Azure OpenAI / Azure Speech, EU), also `VOICE_ENGINE=openai`.
Fällt serverseitiges TTS aus, schaltet die App automatisch auf die Browser-Stimme um.

## Tests
```bash
cd backend && pytest -q                                   # SQLite
TEST_DATABASE_URL=postgresql+psycopg://… pytest -q        # gegen PostgreSQL
```
23 Tests decken ab: Unit (Zitatprüfung, PII, Coverage-Regeln), API/RBAC, kompletter Interview-Flow
(Plan → Einwilligung → Antworten → Pause/Resume → Abschluss → Review → Export → Training), Voice-Fehlerpfade
(STT-Ausfall, Stille, leere Datei, PII im gesprochenen Text) und Extraktionsqualität (halluzinierte Zitate, Einheiten ohne
Quelle, unbekannte Themen, Widersprüche, neue Themen, Wiederherstellung nach Interviewer-Ausfall).
Die Tests ersetzen das externe LLM durch deterministische Fakes. Die Anwendung selbst ruft immer das echte Modell auf.

## Demo-Drehbuch (≈ 5 Min.)
1. **Mitarbeiter** → „Mitarbeiter anlegen“ → „Fiktives Beispiel einfügen“ (Helga Weber, Altverträge).
2. Profil → „Interviewplan erstellen“: Die KI plant Themen, priorisiert nach Verlustrisiko.
3. Einwilligung lesen, bestätigen, Interview starten. Frei sprechen, z. B. *„Bei älteren Bausparverträgen gibt es einige Besonderheiten.“*
   Die KI fragt nach („Welche Tarifgenerationen konkret?“). Rechts erscheinen live Wissenseinheiten, Lücken und Coverage.
   „Warum diese Frage?“ zeigt die Begründung.
4. Pausieren und fortsetzen: Die KI nimmt den Faden wieder auf. Danach beenden: Die Zusammenfassung wird erstellt.
5. Rolle **Experte** wählen → Review: Zitat mit Zeitstempel ✓, Bestätigen, Korrigieren, Historie zeigen.
6. Rolle **Administrator** → „JSON-Export (bestätigt)“ und „Training generieren“ (Folien, Sprechertext, Quiz mit Quellen-IDs).

## Dokumentation
- [01 Product Discovery](docs/01-product-discovery.md): Vision, Personas, Stories, FR/NFR, MVP, Abgrenzung
- [02 Architektur](docs/02-architecture.md): Mermaid-Diagramme, State Machine, Voice-Bewertung, Sicherheit, Risiken
- [03 AI Interview Intelligence](docs/03-ai-interview-intelligence.md)
- [04 Export-Vertrag](docs/04-export-contract.md) + [JSON-Schema](docs/ekep.knowledge-export.v1.schema.json)
- [05 Wissensabgleich / Vektorsuche](docs/05-knowledge-search.md): Live-Suche je Antwort, Abgleich neu/Ergänzung/bekannt/Widerspruch, Vertrag für die Vektordatenbank, RAG-Service-Integration (`RAG_SERVICE_URL`, `RAG_SERVICE_LANG`, `RAG_SERVICE_TOPIC`; Health-Check `curl -s http://localhost:8001/health`)

## Rollout-Skizze LBS
1. **Pilot (4–6 Wochen):** 3–5 Experten mit Ruhestand in < 24 Monaten, Azure OpenAI (EU) auf der Helaba-KI-Plattform, Entra-ID-SSO, DSB/BR-Freigabe der Einwilligung.
2. **Messen:** bestätigte Einheiten pro Interviewstunde, Korrekturquote, Anteil verifizierter Zitate, Experten-NPS.
3. **Skalieren:** Wissensträger-Radar (Austrittsdatum × Kritikalität), Folgeinterviews aus offenen Lücken, Anbindung RAG-Team über Vertrag v1.
4. **Ausbau:** Dokumenten-Ingest zur Vorbefüllung des Plans, Gültigkeitsabgleich mit Rundschreiben, Mehrsprachigkeit, SCORM (Phase 3).

## Transparenz: Was das MVP (noch) nicht ist
- **Auth:** Rollen kommen im Demo-Modus aus der UI. Produktiv ersetzt Entra ID (OIDC) diesen Baustein, die Rollenprüfung im Backend bleibt.
- **Voice:** STT/TTS pro Äußerung, nicht Realtime. Die Begründung steht in docs/02. Die Barge-in-Qualität hängt von Echo-Cancellation und Headset ab.
- **PII-Erkennung:** regelbasiert (IBAN, E-Mail, Telefon, Vertragsnummern), keine NER.
- **Training:** nur Vorschau auf Phase 3, generiert ausschließlich aus bestätigten Einheiten. Keine Lernplattform, kein RAG.
- **Verschlüsselung at rest, Aufbewahrungsfristen, Purge-Jobs:** Teil des Deployments bzw. Rollouts, nicht im Code.

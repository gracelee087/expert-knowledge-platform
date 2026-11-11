# 01 · Product Discovery – Expert Knowledge Extraction Platform

## Produktvision
Ein digitaler Experteninterviewer, der das wertvollste Erfahrungswissen langjähriger LBS-Mitarbeiter
systematisch, nachvollziehbar und expertengeprüft erfasst, bevor es das Unternehmen verlässt.
Der Maßstab ist nicht die Gesprächsqualität, sondern **wie viel relevantes, konkretes und auf
Originalaussagen rückführbares Wissen** pro Interviewminute gesichert wird.

## Kontext (aus der LBS-Challenge, FUTURY Build Days #4)
- LBS Hessen-Thüringen: ca. 180 Mitarbeitende, > 560.000 Verträge, > 21 Mrd. EUR Bausparsumme.
- Wissen steckt in Konzepten, Handbüchern, Rundschreiben, PPT/PDF, Mails, SharePoint und vor allem in Köpfen.
- Jury-Kriterien: Business Value, technische Machbarkeit, regulatorische Konformität, Innovationsgrad, Fit zur Helaba-KI-Plattform.
- Leitprinzipien: Transparenz, Sicherheit, Erklärbarkeit, Human in the Loop.

## Personas
| Persona | Ziel | Schmerz heute |
|---|---|---|
| **Helga W., Senior-Sachbearbeiterin (Experte)**, 31 J. LBS, geht in 18 Monaten in Rente | Ihr Wissen soll „richtig“ ankommen, ohne Dokumentationsaufwand | Kein Raum/keine Zeit zum Aufschreiben; Fragebögen treffen das Wesentliche nicht |
| **Markus K., Wissensmanager / Interviewer** | Wissensträger priorisieren, Interviews planen, Lücken sehen | Interviews sind teuer, Ergebnisse unstrukturiert, nicht vergleichbar |
| **Admin / Datenschutz / ISB** | DSGVO-Konformität, Rollen, Audit | Schatten-Tools, fehlende Nachweise |
| **RAG-Team (Phase 2, Konsument)** | Stabile, versionierte, geprüfte Wissenseinheiten mit Provenienz | Unstrukturierte Transkripte ohne Qualitätsstatus |

## User Stories (MVP, priorisiert)
1. Als **Admin** lege ich einen Mitarbeiter mit Fachgebiet, Verantwortungen und Spezialisierungen an.
2. Als **Wissensmanager** erzeuge ich aus Profil + Interviewziel einen individuellen, priorisierten Interviewplan.
3. Als **Experte** werde ich vor Start informiert und willige ein (versioniert, auditiert).
4. Als **Experte** spreche ich frei; die KI hört zu, unterbricht nicht, lässt sich aber von mir unterbrechen.
5. Als **Experte** erhalte ich konkrete Folgefragen statt Standardfragen; bereits Gesagtes wird nicht erneut gefragt.
6. Als **Wissensmanager** sehe ich live Themenfortschritt, Coverage, Lücken und mögliche Widersprüche.
7. Als **Experte** kann ich pausieren und später nahtlos fortsetzen.
8. Als **Experte** prüfe ich jede Wissenseinheit: bestätigen, korrigieren, ergänzen, als unsicher markieren, zurückweisen – mit Originalzitat und Zeitstempel.
9. Als **RAG-Team** rufe ich per API ausschließlich expertenbestätigtes Wissen in einem versionierten JSON-Vertrag ab.
10. *(Demo-Erweiterung)* Als **Wissensmanager** erzeuge ich aus bestätigtem Wissen ein Trainingspaket (Folien, Sprechertext, Quiz) für eine Zielgruppe.

## Functional Requirements
- Mitarbeiterverwaltung (CRUD), Interviewanlage, KI-Interviewplanung.
- Voice-Interview mit VAD, Barge-in, Pausentoleranz, Texteingabe als Fallback.
- Adaptiver Interviewer mit Techniken A–H (Klärung … Validierung) plus Einstieg, Themenwechsel und Abschluss.
- Wissensextraktion in 5 Dimensionen (Fach-, Prozess-, Entscheidungs-, Erfahrungs-, Kontextwissen) inkl. Voraussetzungen,
  Ausnahmen, Abhängigkeiten, Risiken, Eskalationswege, typische Fehler, historische Veränderungen, zeitliche Gültigkeit.
- Coverage-Modell: 9 Dimensionen × Themen, 5 Status; `expert_confirmed` nur über Review.
- Lücken- und Widerspruchserkennung, vom Experten eingebrachte neue Themen.
- Persistenter Interviewzustand, Pause/Fortsetzen, Zusammenfassung.
- Review mit unveränderlicher Originalextraktion und append-only Revisionshistorie.
- Versionierter JSON-Export + JSON-Schema, Audit-Log.

## Non-functional Requirements
| Bereich | Anforderung (MVP) |
|---|---|
| Latenz | Nächste Frage < 3–4 s nach Sprechende (Fast Path; Analyse asynchron) |
| Nachvollziehbarkeit | 100 % der Einheiten mit Quellenzitat; deterministische Zitatprüfung |
| Datenschutz | Audio standardmäßig nicht persistiert; PII-Maskierung; Einwilligung versioniert |
| Sicherheit | Rollenprüfung pro Endpoint; Export-Token; Audit-Trail |
| Betreibbarkeit | `docker compose up`; Konfiguration nur über ENV; Provider austauschbar (OpenAI ↔ Azure OpenAI) |
| Robustheit | WS-Reconnect mit Resync; LLM-Ausfall ohne Datenverlust (Antwort bleibt gespeichert, Frage neu erzeugbar) |

## MVP-Scope
**Drin:** alles oben. **Bewusst draußen:** SSO (Entra ID), Mandantenfähigkeit, Dokumenten-Ingest,
Mehrsprachigkeit, SCORM, Diarisierung bei mehreren Sprechern, Realtime-Voice.

## Abgrenzung Phase 2/3
| | Phase 1 (dieses Produkt) | Phase 2 (RAG-Team) | Phase 3 (Utilization) |
|---|---|---|---|
| Aufgabe | Wissen erheben, strukturieren, prüfen lassen, exportieren | Chunking, Embeddings, Vector DB, Retrieval | Chatbot, Trainings, Podcasts, Quiz, Onboarding |
| Schnittstelle | **liefert** `ekep.knowledge-export` v1 | **konsumiert** den Export | nutzt Phase 2 |
| Hier gebaut? | ja | nein | nur eine kleine Demo-Vorschau (Training), die ausschließlich bestätigte Einheiten nutzt und klar als Vorschau gekennzeichnet ist |

# 04 · Übergabevertrag an das RAG-Team

**Vertrag:** `ekep.knowledge-export` · **Version:** `1.0.0` (SemVer) ·
**Schema:** [`ekep.knowledge-export.v1.schema.json`](./ekep.knowledge-export.v1.schema.json), live unter `GET /api/v1/export/schema`.
Das Schema wird aus denselben Pydantic-Modellen erzeugt, die den Export bauen. Code und Vertrag können dadurch nicht auseinanderlaufen.

## Endpunkte
| Methode | Pfad | Auth | Zweck |
|---|---|---|---|
| GET | `/api/v1/export/schema` | – | JSON-Schema |
| GET | `/api/v1/export/interviews` | Bearer `EXPORT_API_KEY` | exportierbare Interviews (in_review/approved) mit Anzahl bestätigter Einheiten |
| GET | `/api/v1/export/interviews/{id}` | Bearer `EXPORT_API_KEY` | Export; `include_transcript=true` optional |

`include_unconfirmed=true` ist nur für Admins (interner Entwurf) erlaubt und für das RAG-Team gesperrt (403):
**keine ungeprüfte Weitergabe**.

```bash
curl -H "Authorization: Bearer $EXPORT_API_KEY" \
  http://localhost:8000/api/v1/export/interviews/int_1234abcd?include_transcript=true
```

## Struktur (gekürzt)
```json
{
  "schema": "ekep.knowledge-export",
  "schema_version": "1.0.0",
  "export_id": "exp_…",
  "generated_at": "2026-11-11T14:02:11Z",
  "source": { "system": "Expert Knowledge Extraction Platform", "interview_id": "int_…", "interview_goal": "…", "consent_version": "2026-11-v1" },
  "expert": { "employee_id": "emp_…", "name": null, "department": "…", "role": "…", "expertise_area": "…" },
  "filter": { "review_statuses": ["confirmed", "corrected"], "include_transcript": false },
  "knowledge_units": [{
    "knowledge_id": "KU-3F9A21", "version": 3, "topic": "Altverträge und Tarifgenerationen", "category": "entscheidungswissen",
    "title": "…", "content": "…", "explanation": "…",
    "prerequisites": [], "process_steps": [], "decision_logic": "…", "exceptions": [], "examples": [], "risks": [],
    "dependencies": [], "escalation_paths": [], "common_mistakes": [],
    "temporal_validity": { "note": "gilt für Verträge vor 2005", "historical_changes": [] },
    "confidence": { "level": "hoch", "reason": "…", "semantics": "statement_clarity_not_truth" },
    "uncertainties": [],
    "review": { "status": "corrected", "expert_confirmed": true, "reviewed_by": "h.weber", "reviewed_at": "…", "expert_notes": [] },
    "provenance": {
      "source_refs": [{ "turn_id": "turn_…", "quote": "…", "transcript_offset_start_s": 312.4, "transcript_offset_end_s": 331.0, "verified": true }],
      "original_extraction": { "…": "unveränderte KI-Erstextraktion" },
      "revisions": [{ "version": 1, "action": "extracted", "actor": "ai-analyst", "at": "…", "comment": null }]
    }
  }],
  "open_questions": [{ "id": "gap_…", "topic_id": "T2", "description": "…" }],
  "contradictions": [],
  "transcript": null
}
```

## Garantien für Konsumenten
1. `knowledge_id` ist stabil, `version` steigt bei jeder Änderung. Das ist der Schlüssel für Upserts im Vektorindex.
2. Standardmäßig enthält der Export nur `review.expert_confirmed = true`.
3. Jede Einheit hat ≥ 1 `source_ref`. `verified=false` ist ein Qualitätssignal und kein Fehler.
4. `confidence` beschreibt die Klarheit der Aussage, nicht die Wahrheit.
5. Zeitangaben in `transcript_offset_*` sind Sekunden aktiver Interviewzeit (ohne Pausen).
6. Personenbezug ist minimiert (`expert.name` = null, solange `EXPORT_INCLUDE_EXPERT_NAME=false`).

## Versionierung
- **PATCH:** Doku/Bugfix ohne Strukturänderung · **MINOR:** neue optionale Felder · **MAJOR:** Entfernen/Umbenennen/Semantikänderung.
- Bei MAJOR: alter Pfad bleibt parallel bestehen (`/api/v1/…` → `/api/v2/…`), Ankündigung an das RAG-Team.

"""Verbindungs- und Modellcheck für das LLM-Gateway (z. B. hIP / hessian.AI).

Aufruf (im backend-Ordner, mit gesetzter .env):
    python scripts/check_llm.py            # Modelle listen + konfigurierte Rollen testen
    python scripts/check_llm.py --all      # zusätzlich alle Chat-Modelle mit Interviewer-Schema testen

Gibt pro Modell Latenz und Erfolg der strukturierten Ausgabe aus – damit wählt ihr
INTERVIEWER_MODEL / ANALYST_MODEL / PLANNER_MODEL in der .env.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.agents.schemas import InterviewerDecision  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm.provider import get_llm, _client  # noqa: E402

SYSTEM = "Du bist Interviewer in einer Bausparkasse. Stelle genau eine präzise Folgefrage auf Deutsch."
USER = ("Themen: T1 Altverträge. Experte: 'Bei älteren Bausparverträgen gibt es einige Besonderheiten.' "
        "Wähle die nächste Frage mit dem größten Erkenntnisgewinn.")
SKIP = ("embed", "rerank", "bge")


async def probe(model: str) -> None:
    t0 = time.perf_counter()
    try:
        r = await get_llm().structured(system=SYSTEM, user=USER, schema=InterviewerDecision, model=model, temperature=0.4)
        print(f"  ✓ {model:<45} {time.perf_counter() - t0:5.1f}s  [{r.technique}] {r.question}")
    except Exception as e:
        print(f"  ✗ {model:<45} {time.perf_counter() - t0:5.1f}s  {str(e)[:160]}")


async def main() -> None:
    print(f"Gateway: {settings.openai_base_url or 'api.openai.com'}")
    try:
        models = [m.id for m in (await _client().models.list()).data]
        print("Verfügbare Modelle:\n  " + "\n  ".join(models))
    except Exception as e:
        models = []
        print(f"Modellliste nicht abrufbar: {e}")
    print("\nKonfigurierte Rollen:")
    for role, m in [("INTERVIEWER", settings.interviewer_model), ("ANALYST", settings.analyst_model),
                    ("PLANNER", settings.planner_model)]:
        print(f" {role}:")
        await probe(m)
    if "--all" in sys.argv:
        print("\nAlle Chat-Modelle:")
        for m in models:
            if not any(s in m.lower() for s in SKIP):
                await probe(m)


if __name__ == "__main__":
    asyncio.run(main())

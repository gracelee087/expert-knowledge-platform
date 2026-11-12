"""Expert Knowledge Extraction Platform – API."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.dashboard_interview import router as dashboard_interview_router
from .api.dashboard_content import router as dashboard_content_router
from .api.dashboard_learning import router as dashboard_learning_router
from .api.dashboard_voice import router as dashboard_voice_router
from .api.knowledge_routes import router as knowledge_router
from .api.routes import export_router, router, ws_router
from .config import settings
from .db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(
    title="Expert Knowledge Extraction Platform",
    version="0.1.0",
    description="Phase 1 – Knowledge Acquisition. Export-Vertrag für das RAG-Team unter /api/v1/export.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


app.include_router(dashboard_interview_router)  # teammate dashboard contract; before /api/interviews/{iv_id}
app.include_router(dashboard_content_router)
app.include_router(dashboard_voice_router)
app.include_router(dashboard_learning_router)
app.include_router(router)
app.include_router(export_router)
app.include_router(ws_router)
app.include_router(knowledge_router)

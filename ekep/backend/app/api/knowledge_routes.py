"""API für den Wissensabgleich (Vektorsuche gegen die bestehende Wissensbasis)."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import CONFIRMED_STATUSES, Interview
from ..services import knowledge_search as ks
from ..services import rag_client
from ..services.audit import audit
from .deps import Principal, require

router = APIRouter(prefix="/api", tags=["knowledge-search"])
STAFF = ("admin", "interviewer")
PARTICIPANT = ("admin", "interviewer", "expert")


@router.get("/knowledge-search/status")
def status(p: Principal = Depends(require(*PARTICIPANT))):
    return {
        "provider": settings.knowledge_search_provider,
        "connected_to_vector_db": settings.knowledge_search_provider == "http" and bool(settings.vector_search_url),
        "embedding_model": settings.embedding_model if settings.knowledge_search_provider == "local" else None,
        "top_k": settings.search_top_k,
    }


class QueryIn(BaseModel):
    query: str = Field(min_length=2, max_length=4000)


@router.post("/knowledge-search")
async def adhoc_search(body: QueryIn, p: Principal = Depends(require(*PARTICIPANT))):
    provider = ks.get_provider()
    if provider is None:
        raise HTTPException(409, "Wissenssuche ist deaktiviert (KNOWLEDGE_SEARCH_PROVIDER=off).")
    try:
        res = await provider.search(body.query, settings.search_top_k)
    except Exception as e:
        raise HTTPException(502, f"Wissenssuche fehlgeschlagen: {e}")
    return {"provider": res.provider, "mode": res.mode, "hits": [h.as_dict() for h in res.hits]}


@router.get("/interviews/{iv_id}/knowledge-search")
def interview_search(iv_id: str, db: Session = Depends(get_db), p: Principal = Depends(require(*PARTICIPANT))):
    if not ks.interview_exists(db, iv_id):
        raise HTTPException(404, "Interview nicht gefunden")
    rec = ks.reconciliations_for(db, iv_id)
    items = list(rec.values())
    return {"provider": settings.knowledge_search_provider, "searches": ks.searches_for(db, iv_id),
            "reconciliation": {"items": items, "by_unit": rec, "summary": ks.summarize(items)}}


@router.post("/interviews/{iv_id}/reconcile")
async def reconcile(iv_id: str, db: Session = Depends(get_db), p: Principal = Depends(require(*PARTICIPANT))):
    if not ks.interview_exists(db, iv_id):
        raise HTTPException(404, "Interview nicht gefunden")
    items = await ks.reconcile(iv_id)
    audit(db, p.user, p.role, "interview.reconciled", "interview", iv_id, units=len(items))
    db.commit()
    return {"items": items, "summary": ks.summarize(items)}


# ------------------------------------------------------------------ Übernahme in den RAG-Service
SKIP_REASON = {"bestaetigung": "bereits bekannt – keine Änderung", "widerspruch": "Widerspruch – fachlich klären"}


@router.post("/interviews/{iv_id}/knowledge-base/publish")
async def publish_to_knowledge_base(iv_id: str, db: Session = Depends(get_db), p: Principal = Depends(require(*STAFF))):
    iv = db.get(Interview, iv_id)
    if iv is None:
        raise HTTPException(404, "Interview nicht gefunden")
    if not rag_client.enabled():
        raise HTTPException(409, "RAG-Service nicht konfiguriert")
    emp = iv.employee
    units = [u for u in iv.units if u.review_status in CONFIRMED_STATUSES]
    if not units:
        raise HTTPException(409, "Bitte zuerst Wissenseinheiten im Review bestätigen.")
    rec = ks.reconciliations_for(db, iv_id)
    today = date.today().isoformat()
    published, skipped = [], []
    for u in units:
        relation = (rec.get(u.id) or {}).get("relation") or "neu"
        if relation in SKIP_REASON:
            skipped.append({"unit_id": u.id, "title": u.title, "relation": relation, "reason": SKIP_REASON[relation]})
            continue
        res = await rag_client.add_fact(expert=emp.name, topic=u.title, question=u.topic or iv.title, answer=u.content,
                                        lang=settings.rag_service_lang, interviewed_at=today,
                                        expert_role=emp.role or "", department=emp.department or "")
        if res is None:
            published.append({"unit_id": u.id, "title": u.title, "fact_id": None, "status": "fehlgeschlagen"})
            continue
        if res.get("duplicate_of"):
            published.append({"unit_id": u.id, "title": u.title, "fact_id": res["duplicate_of"], "status": "bereits übernommen"})
            continue
        fact_id = res.get("id")
        if fact_id:
            await rag_client.approve_fact(fact_id, reviewer=p.user)
        published.append({"unit_id": u.id, "title": u.title, "fact_id": fact_id, "delta_type": res.get("delta_type"),
                          "kb_verdict": res.get("kb_verdict"), "status": "übernommen"})
    audit(db, p.user, p.role, "knowledge_base.published", "interview", iv_id, units=len(units))
    db.commit()
    return {"department": emp.department, "rag_service_url": settings.rag_service_url,
            "published": published, "skipped": skipped}


@router.post("/interviews/{iv_id}/knowledge-base/video")
async def generate_video(iv_id: str, style: str = "motion", db: Session = Depends(get_db), p: Principal = Depends(require(*STAFF))):
    iv = db.get(Interview, iv_id)
    if iv is None:
        raise HTTPException(404, "Interview nicht gefunden")
    if not rag_client.enabled():
        raise HTTPException(409, "RAG-Service nicht konfiguriert")
    res = await rag_client.export_video(topic=iv.title, department=iv.employee.department, lang=settings.rag_service_lang, style=style)
    if res is None:
        raise HTTPException(502, "Videoerstellung fehlgeschlagen")
    audit(db, p.user, p.role, "training.video_generated", "interview", iv_id, file=res.get("file"))
    db.commit()
    return {**res, "absolute_url": settings.rag_service_url.rstrip("/") + str(res.get("url") or "")}

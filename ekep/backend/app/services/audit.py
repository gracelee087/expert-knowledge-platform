from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuditLog


def audit(db: Session, actor: str, role: str, action: str, entity_type: str, entity_id: str | None, **details) -> None:
    """Append-only Audit-Trail (im selben Commit wie die fachliche Änderung)."""
    db.add(AuditLog(actor=actor, role=role, action=action, entity_type=entity_type, entity_id=entity_id, details=details))

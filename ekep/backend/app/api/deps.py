"""Rollen & Berechtigungen.

AUTH_MODE=demo: Benutzer und Rolle kommen aus Headern (Rollenwechsel in der UI),
damit die Rechteprüfung in der Demo sichtbar ist. In Produktion wird dieser
Baustein durch OIDC (Microsoft Entra ID) ersetzt; die Rollenprüfung bleibt.
Das RAG-Team authentifiziert sich per Bearer-Token (EXPORT_API_KEY).
"""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from ..config import settings

ROLES = {"admin", "interviewer", "expert", "rag_consumer"}


@dataclass
class Principal:
    user: str
    role: str


def principal(
    x_demo_user: str | None = Header(default=None),
    x_demo_role: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> Principal:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if hmac.compare_digest(token, settings.export_api_key):
            return Principal("rag-team-service", "rag_consumer")
        raise HTTPException(401, "Ungültiges Token")
    if settings.auth_mode == "off":
        return Principal("local-admin", "admin")
    role = (x_demo_role or "").strip()
    if role not in ROLES - {"rag_consumer"}:
        raise HTTPException(401, "Nicht angemeldet")
    return Principal((x_demo_user or f"demo-{role}").strip()[:100], role)


def require(*roles: str):
    def dep(p: Principal = Depends(principal)) -> Principal:
        if p.role not in roles:
            raise HTTPException(403, f"Rolle '{p.role}' ist für diese Aktion nicht berechtigt.")
        return p

    return dep

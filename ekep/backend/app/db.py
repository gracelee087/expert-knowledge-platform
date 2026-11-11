from __future__ import annotations

import os
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def _make_engine(url: str):
    if url.startswith("sqlite"):
        path = url.replace("sqlite:///", "")
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def configure(url: str) -> None:
    """Für Tests: Engine austauschen."""
    global engine
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)


@contextmanager
def session_scope():
    s: Session = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db():
    s: Session = SessionLocal()
    try:
        yield s
    finally:
        s.close()

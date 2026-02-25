import ssl
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import DATABASE_URL
from app.core.logging import logger


def _sanitize_database_url(url: str) -> str:
    """Strip query params like sslmode=require and enforce SSL via connect_args."""
    if not url:
        return url
    if "?" in url:
        url = url.split("?", 1)[0]
    return url


def _make_engine():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    url = _sanitize_database_url(DATABASE_URL)

    connect_args = {}
    if url.startswith("postgresql") or url.startswith("postgres://"):
        ssl_ctx = ssl.create_default_context()
        connect_args["ssl_context"] = ssl_ctx

    if "sqlite" in url:
        connect_args = {"check_same_thread": False}

    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

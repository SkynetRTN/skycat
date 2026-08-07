"""Engine + session construction for the catalog database.

Owned entirely by this package. A process can hold another application's DB
session and a catalog DB session simultaneously without shared state.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Connection, Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import CatalogDatabaseConfig

__all__ = [
    "create_catalog_engine",
    "create_session_factory",
    "driver_connection",
    "session_scope",
]


class DriverConnectionError(RuntimeError):
    """The raw DBAPI connection behind a SQLAlchemy ``Connection`` was gone."""


def driver_connection(conn: Connection) -> Any:
    """The raw psycopg3 connection behind ``conn``.

    ``COPY`` is a driver-level protocol with no SQLAlchemy Core expression, so
    the two bulk paths (staging load, crossmatch input upload) reach through to
    psycopg. ``driver_connection`` is typed Optional because it is ``None`` once
    the pooled connection has been invalidated or returned — which never happens
    inside an open ``begin()`` block, but the type checker cannot know that.

    Returns ``Any``: the point is to leave SQLAlchemy's typing behind and speak
    psycopg (``.cursor()``, ``.copy()``) deliberately, in one named place.
    """
    raw = conn.connection.driver_connection
    if raw is None:
        raise DriverConnectionError(
            "no live DBAPI connection behind this SQLAlchemy Connection — it was "
            "closed or invalidated before the COPY could start"
        )
    return raw


def create_catalog_engine(config: CatalogDatabaseConfig, **overrides) -> Engine:
    """Build a SQLAlchemy ``Engine`` for the given catalog connection identity.

    A per-connection ``statement_timeout`` is installed via a connect listener
    (driver-agnostic), when configured.
    """
    engine = create_engine(
        config.url(),
        echo=overrides.pop("echo", config.echo),
        pool_size=overrides.pop("pool_size", config.pool_size),
        max_overflow=overrides.pop("max_overflow", config.max_overflow),
        pool_recycle=overrides.pop("pool_recycle", config.pool_recycle),
        pool_timeout=overrides.pop("pool_timeout", config.pool_timeout),
        pool_pre_ping=overrides.pop("pool_pre_ping", config.pool_pre_ping),
        **overrides,
    )

    timeout_ms = config.statement_timeout_ms
    if timeout_ms:
        @event.listens_for(engine, "connect")
        def _set_statement_timeout(dbapi_conn, _record):  # pragma: no cover - thin
            with dbapi_conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {int(timeout_ms)}")

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Transactional session scope: commit on success, rollback on error."""
    factory = create_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def fetch_current_database(engine: Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT current_database()")).scalar_one()

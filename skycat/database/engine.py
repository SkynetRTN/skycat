"""Engine + session construction for the catalog database.

Owned entirely by this package — it does not reuse ``skynet_db``'s engine
helpers or session factory, so a process can hold a ``sky`` session and a
``catalogs`` session simultaneously without any shared state.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from ..config import CatalogDatabaseConfig

__all__ = [
    "create_catalog_engine",
    "create_session_factory",
    "session_scope",
]


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

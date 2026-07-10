"""PostGIS enablement and verification.

The catalog store *requires* PostGIS — it never falls back to non-spatial Python
filtering. ``enable_postgis`` needs a superuser/DBA (bootstrap) connection;
``verify_postgis`` only needs read access and is safe for health checks.
"""

from __future__ import annotations

from sqlalchemy import Connection, text


class PostgisUnavailableError(RuntimeError):
    pass


def enable_postgis(conn: Connection) -> str:
    """``CREATE EXTENSION IF NOT EXISTS postgis`` and return its version."""
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    version = postgis_version(conn)
    if not version:
        raise PostgisUnavailableError(
            "PostGIS could not be enabled. Ensure the catalog PostgreSQL image "
            "ships PostGIS (the dev image is built FROM postgis/postgis)."
        )
    return version


def postgis_version(conn: Connection) -> str | None:
    present = conn.execute(
        text("SELECT extversion FROM pg_extension WHERE extname = 'postgis'")
    ).scalar()
    return str(present) if present is not None else None


def verify_postgis(conn: Connection) -> str:
    version = postgis_version(conn)
    if not version:
        raise PostgisUnavailableError(
            "PostGIS is not installed in this database. Run `skycat init` "
            "(as the bootstrap/DBA role) before migrating or querying."
        )
    return version

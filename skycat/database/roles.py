"""Catalog database roles and grants.

Three operational roles with least-privilege responsibilities:

* ``catalog_owner``  — owns schemas/objects, applies migrations.
* ``catalog_ingest`` — loads staging, inserts release data, creates partitions,
  writes registry metadata, builds indexes.
* ``catalog_reader`` — read-only query/crossmatch role.

All statements are idempotent so ``init`` can be re-run safely. Role creation and
grants require a superuser/DBA (bootstrap) connection. On externally-provisioned
production hosts the roles may already exist (created by Ansible); the DO blocks
simply skip creation and (re)apply grants.
"""

from __future__ import annotations

from sqlalchemy import Connection, text

from ..constants import (
    ROLE_INGEST,
    ROLE_OWNER,
    ROLE_READER,
    SCHEMA_DATA,
    SCHEMA_REGISTRY,
    SCHEMA_STAGING,
)


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ensure_role(conn: Connection, name: str, password: str | None) -> None:
    conn.execute(
        text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{name}') THEN
                    CREATE ROLE "{name}" LOGIN;
                END IF;
            END
            $$;
            """
        )
    )
    if password:
        conn.execute(text(f'ALTER ROLE "{name}" WITH LOGIN PASSWORD {_quote_literal(password)}'))


def ensure_roles(
    conn: Connection,
    *,
    owner_password: str | None = None,
    ingest_password: str | None = None,
    reader_password: str | None = None,
) -> None:
    _ensure_role(conn, ROLE_OWNER, owner_password)
    _ensure_role(conn, ROLE_INGEST, ingest_password)
    _ensure_role(conn, ROLE_READER, reader_password)


def ensure_schemas_owned(conn: Connection) -> None:
    """Create the three schemas owned by the owner role."""
    for schema in (SCHEMA_REGISTRY, SCHEMA_DATA, SCHEMA_STAGING):
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}" AUTHORIZATION "{ROLE_OWNER}"'))
        conn.execute(text(f'ALTER SCHEMA "{schema}" OWNER TO "{ROLE_OWNER}"'))


def apply_grants(conn: Connection, *, database: str) -> None:
    """Apply schema/object grants and default privileges (idempotent).

    Called *before* migrations so default privileges apply to objects the owner
    creates, then again (the explicit ALL-IN-SCHEMA grants) afterwards.
    """
    # The owner needs CREATE on the database so `CREATE SCHEMA IF NOT EXISTS`
    # from the Alembic env (run as owner) succeeds.
    conn.execute(text(f'GRANT CREATE, CONNECT, TEMPORARY ON DATABASE "{database}" TO "{ROLE_OWNER}"'))
    conn.execute(text(f'GRANT CONNECT, TEMPORARY ON DATABASE "{database}" TO "{ROLE_INGEST}"'))
    # TEMPORARY lets the read-only role build a temp table of input coordinates
    # for batch crossmatch; it still cannot write any catalog table.
    conn.execute(text(f'GRANT CONNECT, TEMPORARY ON DATABASE "{database}" TO "{ROLE_READER}"'))

    # Schema usage.
    for schema in (SCHEMA_REGISTRY, SCHEMA_DATA, SCHEMA_STAGING):
        conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{ROLE_INGEST}"'))
    for schema in (SCHEMA_REGISTRY, SCHEMA_DATA):
        conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{ROLE_READER}"'))
    # Ingest creates partitions + staging tables.
    conn.execute(text(f'GRANT CREATE ON SCHEMA "{SCHEMA_DATA}" TO "{ROLE_INGEST}"'))
    conn.execute(text(f'GRANT CREATE ON SCHEMA "{SCHEMA_STAGING}" TO "{ROLE_INGEST}"'))

    # Default privileges for objects the OWNER creates (registry + parent tables).
    conn.execute(text(
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{ROLE_OWNER}" IN SCHEMA "{SCHEMA_REGISTRY}" '
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{ROLE_INGEST}"'
    ))
    conn.execute(text(
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{ROLE_OWNER}" IN SCHEMA "{SCHEMA_REGISTRY}" '
        f'GRANT SELECT ON TABLES TO "{ROLE_READER}"'
    ))
    conn.execute(text(
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{ROLE_OWNER}" IN SCHEMA "{SCHEMA_DATA}" '
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{ROLE_INGEST}"'
    ))
    conn.execute(text(
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{ROLE_OWNER}" IN SCHEMA "{SCHEMA_DATA}" '
        f'GRANT SELECT ON TABLES TO "{ROLE_READER}"'
    ))
    for schema in (SCHEMA_REGISTRY, SCHEMA_DATA):
        conn.execute(text(
            f'ALTER DEFAULT PRIVILEGES FOR ROLE "{ROLE_OWNER}" IN SCHEMA "{schema}" '
            f'GRANT USAGE, SELECT ON SEQUENCES TO "{ROLE_INGEST}"'
        ))
    # Default privileges for partitions the INGEST role creates in catalog_data.
    conn.execute(text(
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{ROLE_INGEST}" IN SCHEMA "{SCHEMA_DATA}" '
        f'GRANT SELECT ON TABLES TO "{ROLE_READER}"'
    ))
    conn.execute(text(
        f'ALTER DEFAULT PRIVILEGES FOR ROLE "{ROLE_INGEST}" IN SCHEMA "{SCHEMA_DATA}" '
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "{ROLE_INGEST}"'
    ))


def apply_existing_object_grants(conn: Connection) -> None:
    """Catch-all grants over already-created objects (run after migrations)."""
    conn.execute(text(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{SCHEMA_REGISTRY}" TO "{ROLE_INGEST}"'
    ))
    conn.execute(text(
        f'GRANT SELECT ON ALL TABLES IN SCHEMA "{SCHEMA_REGISTRY}" TO "{ROLE_READER}"'
    ))
    conn.execute(text(
        f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{SCHEMA_DATA}" TO "{ROLE_INGEST}"'
    ))
    conn.execute(text(
        f'GRANT SELECT ON ALL TABLES IN SCHEMA "{SCHEMA_DATA}" TO "{ROLE_READER}"'
    ))
    for schema in (SCHEMA_REGISTRY, SCHEMA_DATA):
        conn.execute(text(
            f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO "{ROLE_INGEST}"'
        ))


def grant_ingest_to_owner(conn: Connection) -> None:
    """Make the owner/migrator a member of the ingest role.

    Release partitions + staging objects are owned by ``catalog_ingest`` (so the
    ingestion role can create/attach/index them — PostgreSQL ties partition DDL
    to ownership). Future schema migrations run as ``catalog_owner``; this
    membership lets the owner ALTER those ingest-owned data objects. It does NOT
    grant the ingest role any owner privileges.
    """
    conn.execute(text(f'GRANT "{ROLE_INGEST}" TO "{ROLE_OWNER}"'))


def reassign_data_objects_to_ingest(conn: Connection) -> None:
    """Transfer ownership of all ``catalog_data`` objects to the ingest role.

    Run (by the bootstrap/DBA) after migrations create the parent partitioned
    tables. Makes ``catalog_ingest`` the owner so ingestion can manage release
    partitions and indexes. The owner role (a member of ingest) retains the
    ability to migrate them.
    """
    conn.execute(text(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = '{SCHEMA_DATA}' LOOP
                EXECUTE format('ALTER TABLE {SCHEMA_DATA}.%I OWNER TO "{ROLE_INGEST}"', r.tablename);
            END LOOP;
            FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = '{SCHEMA_DATA}' LOOP
                EXECUTE format('ALTER SEQUENCE {SCHEMA_DATA}.%I OWNER TO "{ROLE_INGEST}"', r.sequencename);
            END LOOP;
        END
        $$;
        """
    ))
    # The ingest role also owns the staging schema so it can freely create /
    # drop staging + rejected-row tables there.
    conn.execute(text(f'ALTER SCHEMA "{SCHEMA_STAGING}" OWNER TO "{ROLE_INGEST}"'))


def roles_present(conn: Connection) -> dict[str, bool]:
    rows = conn.execute(
        text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"),
        {"names": [ROLE_OWNER, ROLE_INGEST, ROLE_READER]},
    ).scalars()
    found = set(rows)
    return {r: (r in found) for r in (ROLE_OWNER, ROLE_INGEST, ROLE_READER)}

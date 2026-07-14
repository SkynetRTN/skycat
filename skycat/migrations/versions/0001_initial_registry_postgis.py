"""initial: schemas, PostGIS verification, registry tables

Revision ID: 0001
Revises:
Create Date: 2026-06-17

Creates the three catalog schemas (idempotently, in env.py too), verifies
PostGIS is installed (fails clearly otherwise — never silently degrades), and
builds the ``catalog_registry`` management tables.

PostGIS itself is *enabled* by ``init`` running as the bootstrap/DBA role, not
here — the migrator role typically cannot ``CREATE EXTENSION``. This migration
only asserts that it is present.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REGISTRY = "catalog_registry"
DATA = "catalog_data"
STAGING = "catalog_staging"


def _verify_postgis() -> None:
    bind = op.get_bind()
    present = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'")
    ).scalar()
    if not present:
        raise RuntimeError(
            "PostGIS extension is not installed in this database. Run catalog "
            "`init` (as the bootstrap/DBA role) to `CREATE EXTENSION postgis` "
            "before migrating. The catalog store never falls back to non-spatial "
            "filtering."
        )


def upgrade() -> None:
    for schema in (REGISTRY, DATA, STAGING):
        op.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')

    _verify_postgis()

    op.create_table(
        "catalog_family",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("provider", sa.String(256)),
        sa.Column("description", sa.Text()),
        sa.Column("reference_url", sa.String(512)),
        sa.Column("catalog_type", sa.String(64)),
        sa.Column("epoch_jyear", sa.Float()),
        sa.Column("coordinate_frame", sa.String(32)),
        sa.Column("data_table", sa.String(128)),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_release_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_catalog_family_slug"),
        schema=REGISTRY,
    )

    op.create_table(
        "catalog_release",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("family_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("version", sa.String(64)),
        sa.Column("internal_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_location", sa.Text()),
        sa.Column("source_format", sa.String(64)),
        sa.Column("source_checksum", sa.String(128)),
        sa.Column("source_size_bytes", sa.BigInteger()),
        sa.Column("source_modified_at", sa.DateTime(timezone=True)),
        sa.Column("expected_row_count", sa.BigInteger()),
        sa.Column("parsed_row_count", sa.BigInteger()),
        sa.Column("imported_row_count", sa.BigInteger()),
        sa.Column("rejected_row_count", sa.BigInteger()),
        sa.Column("state", sa.String(32), nullable=False, server_default="registered"),
        sa.Column("validation_status", sa.String(32), nullable=False, server_default="not_run"),
        sa.Column("production_table", sa.String(256)),
        sa.Column("importer_version", sa.String(32)),
        sa.Column("notes", sa.Text()),
        sa.Column("failure_detail", postgresql.JSONB()),
        sa.Column("import_started_at", sa.DateTime(timezone=True)),
        sa.Column("import_completed_at", sa.DateTime(timezone=True)),
        sa.Column("validated_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["family_id"], [f"{REGISTRY}.catalog_family.id"],
            name="fk_catalog_release_family_id_catalog_family", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("family_id", "name", name="uq_catalog_release_family_name"),
        schema=REGISTRY,
    )
    op.create_index("ix_catalog_release_family_id", "catalog_release", ["family_id"], schema=REGISTRY)
    op.create_index("ix_catalog_release_state", "catalog_release", ["state"], schema=REGISTRY)

    # Deferred FK family.default_release_id -> release.id (circular).
    op.create_foreign_key(
        "fk_catalog_family_default_release_id_catalog_release",
        "catalog_family", "catalog_release",
        ["default_release_id"], ["id"],
        source_schema=REGISTRY, referent_schema=REGISTRY, ondelete="SET NULL",
    )

    # At most ONE active release per family — enforced by the database.
    op.create_index(
        "uq_active_release_per_family", "catalog_release", ["family_id"],
        unique=True, schema=REGISTRY, postgresql_where=sa.text("state = 'active'"),
    )

    op.create_table(
        "ingestion_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("release_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("stage", sa.String(64)),
        sa.Column("importer_version", sa.String(32)),
        sa.Column("host", sa.String(256)),
        sa.Column("source_path", sa.Text()),
        sa.Column("parsed_row_count", sa.BigInteger()),
        sa.Column("loaded_row_count", sa.BigInteger()),
        sa.Column("rejected_row_count", sa.BigInteger()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("message", sa.Text()),
        sa.Column("detail", postgresql.JSONB()),
        sa.ForeignKeyConstraint(
            ["release_id"], [f"{REGISTRY}.catalog_release.id"],
            name="fk_ingestion_run_release_id_catalog_release", ondelete="CASCADE",
        ),
        schema=REGISTRY,
    )
    op.create_index("ix_ingestion_run_release_id", "ingestion_run", ["release_id"], schema=REGISTRY)

    op.create_table(
        "validation_summary",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("release_id", sa.Integer(), nullable=False),
        sa.Column("ingestion_run_id", sa.Integer()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("checks", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["release_id"], [f"{REGISTRY}.catalog_release.id"],
            name="fk_validation_summary_release_id_catalog_release", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"], [f"{REGISTRY}.ingestion_run.id"],
            name="fk_validation_summary_ingestion_run_id_ingestion_run", ondelete="SET NULL",
        ),
        schema=REGISTRY,
    )
    op.create_index("ix_validation_summary_release_id", "validation_summary", ["release_id"], schema=REGISTRY)


def downgrade() -> None:
    op.drop_table("validation_summary", schema=REGISTRY)
    op.drop_table("ingestion_run", schema=REGISTRY)
    op.drop_constraint(
        "fk_catalog_family_default_release_id_catalog_release",
        "catalog_family", schema=REGISTRY, type_="foreignkey",
    )
    op.drop_table("catalog_release", schema=REGISTRY)
    op.drop_table("catalog_family", schema=REGISTRY)

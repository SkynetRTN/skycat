"""drop catalog_family.default_release_id (and its circular FK)

The active release is resolved from ``catalog_release.state == 'active'``, which
a partial unique index already constrains to at most one per family.
``default_release_id`` mirrored that fact: it was maintained by activate/
deactivate but never read, so it was a second source of truth for something the
state column already answers — and it forced a deferred circular FK plus an
explicit join onclause everywhere family and release are joined.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REGISTRY = "catalog_registry"
FK_NAME = "fk_catalog_family_default_release_id_catalog_release"


def upgrade() -> None:
    op.drop_constraint(FK_NAME, "catalog_family", schema=REGISTRY, type_="foreignkey")
    op.drop_column("catalog_family", "default_release_id", schema=REGISTRY)


def downgrade() -> None:
    op.add_column(
        "catalog_family",
        sa.Column("default_release_id", sa.Integer(), nullable=True),
        schema=REGISTRY,
    )
    op.create_foreign_key(
        FK_NAME,
        "catalog_family",
        "catalog_release",
        ["default_release_id"],
        ["id"],
        source_schema=REGISTRY,
        referent_schema=REGISTRY,
        ondelete="SET NULL",
    )
    # Backfill from the authoritative state column so a downgrade is not lossy.
    op.execute(
        f"""
        UPDATE {REGISTRY}.catalog_family f
           SET default_release_id = r.id
          FROM {REGISTRY}.catalog_release r
         WHERE r.family_id = f.id AND r.state = 'active'
        """
    )

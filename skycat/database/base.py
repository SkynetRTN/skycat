"""The catalog package's own SQLAlchemy declarative base and metadata.

``CatalogBase`` owns the package's ``MetaData``. Catalog tables are registered
**only** on this metadata and live in the ``catalog_registry`` /
``catalog_data`` / ``catalog_staging`` schemas.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint / index names — makes Alembic autogenerate stable and
# migrations readable.
#
# `ix` is `%(table_name)s`, not the usual `%(column_0_label)s`: every catalog
# table is schema-qualified, and SQLAlchemy's column label carries the schema, so
# `column_0_label` would name the release index
# `ix_catalog_registry_catalog_release_family_id` while the migration that
# created it (and the database) call it `ix_catalog_release_family_id`. Nothing
# reads an index by name, but autogenerate compares by name: the mismatch made
# every autogenerate pass propose dropping and re-creating all four registry
# indexes. Schemas are a deployment detail here — three fixed names, no
# cross-schema table-name collisions — so leaving it out costs nothing.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

catalog_metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CatalogBase(DeclarativeBase):
    """Declarative base for every catalog table.

    Declarative base for the standalone catalog schema.
    """

    metadata = catalog_metadata

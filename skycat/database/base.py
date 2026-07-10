"""The catalog package's own SQLAlchemy declarative base and metadata.

This is the architectural isolation boundary. ``CatalogBase`` is a *separate*
declarative base from ``skynet_db.models.Base`` and owns its own ``MetaData``.
Catalog tables are registered **only** on this metadata and live in the
``catalog_registry`` / ``catalog_data`` / ``catalog_staging`` schemas — never on
``skynet_db``'s metadata and never in the ``sky`` database's default schema.

Nothing in this package imports ``skynet_db``.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Deterministic constraint / index names — makes Alembic autogenerate stable and
# migrations readable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

catalog_metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CatalogBase(DeclarativeBase):
    """Declarative base for every catalog table.

    Deliberately independent of ``skynet_db.models.Base``.
    """

    metadata = catalog_metadata

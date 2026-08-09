"""What Alembic autogenerate is allowed to look at.

``include_schemas=True`` is not optional here: every catalog table lives in a
named schema, so without it autogenerate sees none of them and proposes
creating all of them. Unfiltered, though, it reflects *every* schema in the
database and proposes dropping everything it does not find in
``CatalogBase.metadata``. On a PostGIS install that is ``tiger``, ``tiger_data``,
``topology`` and ``public``; in a provisioned catalog it is also every live
release partition, every detached Phase-B1 build table, and every retained
``catalog_staging.*_rejects`` table. None of those can ever be in the metadata:
partitions are created per release by the ingestion runner, and a migration that
knew their names would have to know release ids that do not exist yet.

The filter lives here rather than inline in ``migrations/env.py`` because
``env.py`` is an Alembic entry script, not an importable module — running it
runs migrations. ``tests/test_schema_drift.py`` imports these, so the drift test
compares what the migration environment compares instead of a copy of it that
can quietly diverge.
"""

from __future__ import annotations

import re
from typing import Any

from ..constants import ALL_SCHEMAS, SCHEMA_DATA, SCHEMA_REGISTRY

_MANAGED_SCHEMAS = frozenset(ALL_SCHEMAS)

# Tables in ``catalog_data`` that the ingestion runner creates, not the
# migrations: a release partition (``apass_source_r2``) and the detached table
# Phase B1 builds before the swap (``apass_source_r2_incoming``). Matching on the
# suffix rather than on metadata membership is deliberate — an unmodelled
# *parent* table is real drift and must still be reported.
_RUNTIME_DATA_TABLE = re.compile(r"(_r\d+|_incoming)$")


def include_name(
    name: str | None, type_: str, parent_names: dict[str, str | None]
) -> bool:
    """Alembic ``include_name`` hook: decide what gets *reflected*.

    This is ``include_name`` rather than ``include_object`` on purpose. The
    object hook runs after reflection, which is too late for either problem it
    has to solve: reflecting ``tiger`` fails outright as ``catalog_owner``
    (``permission denied for schema tiger``), and reflecting every partition of
    a 128-million-row family is not free even when it succeeds.
    """
    if type_ == "schema":
        # ``None`` is the default schema (``public``), which holds PostGIS's
        # ``spatial_ref_sys`` and nothing of ours.
        return name in _MANAGED_SCHEMAS
    if type_ == "table":
        schema = parent_names.get("schema_name")
        if schema == SCHEMA_DATA:
            return not _RUNTIME_DATA_TABLE.search(name or "")
        if schema == SCHEMA_REGISTRY:
            # Every registry table is modelled, so an unexpected one here is
            # drift worth reporting rather than something to hide.
            return True
        # catalog_staging holds only what the runner creates and drops per
        # import: ``<family>_<release>_stg`` and the retained ``*_rejects`` the
        # runbook tells operators to go read. Nothing there is modelled.
        return False
    return True


def autogenerate_options() -> dict[str, Any]:
    """The autogenerate half of ``context.configure``, in one place.

    Both ``env.py`` calls and the drift test build their context from this, so
    "what autogenerate compares" has a single definition.
    """
    return {"include_schemas": True, "include_name": include_name}

"""``CatalogBase.metadata`` must describe the migrated schema, and autogenerate
must only look at the schema this package owns.

``tests/test_migration_graph.py`` proves the revision graph is well formed; it
says nothing about what the revisions *produce*. This module closes the other
half: after ``upgrade head``, an autogenerate pass must find nothing to do.

Two failure modes, both real, and they compound:

* **Drift.** An index that exists only in a migration is invisible to the
  metadata, so the next autogenerate proposes dropping it — including
  ``uq_active_release_per_family``, the partial unique index that is the whole
  enforcement of "one active release per family".
* **Over-reach.** Unfiltered, ``include_schemas=True`` reflects ``tiger``,
  ``topology`` and ``public`` as well as every live release partition and every
  retained ``catalog_staging.*_rejects`` table, and proposes dropping all of
  them. The generated revision is valid Python that passes
  ``test_migration_graph.py``; running it destroys the catalog.

The database tests use ``imported`` rather than a bare migrated database
precisely because of the second one — the partitions and rejects tables only
exist once something has been ingested, so a drift test against an empty
database would not see the objects that matter.

The predicate tests need no database and run in the unit suite; only the two
autogenerate passes are marked ``postgis``.
"""

from __future__ import annotations

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from skycat.config import CatalogRole
from skycat.constants import SCHEMA_DATA, SCHEMA_REGISTRY, SCHEMA_STAGING
from skycat.database.autogen import autogenerate_options, include_name
from skycat.database.base import CatalogBase
from skycat.database.engine import create_catalog_engine
from skycat.database.migrate import make_alembic_config

# Importing the models is what attaches the tables to CatalogBase.metadata.
import skycat.models  # noqa: F401


def _table(schema: str | None, name: str) -> bool:
    return include_name(
        name,
        "table",
        {"schema_name": schema, "schema_qualified_table_name": f"{schema}.{name}"},
    )


@pytest.mark.parametrize("schema", [SCHEMA_REGISTRY, SCHEMA_DATA, SCHEMA_STAGING])
def test_catalog_schemas_are_reflected(schema):
    assert include_name(schema, "schema", {}) is True


@pytest.mark.parametrize("schema", [None, "public", "tiger", "tiger_data", "topology"])
def test_foreign_schemas_are_not_reflected(schema):
    # As `catalog_owner`, reflecting `tiger` is not merely noisy: it raises
    # `permission denied for schema tiger` and autogenerate cannot run at all.
    assert include_name(schema, "schema", {}) is False


def test_partition_parents_are_reflected():
    for parent in ("apass_source", "vsx_source", "landolt_source", "stetson_source"):
        assert _table(SCHEMA_DATA, parent) is True, parent


@pytest.mark.parametrize(
    "name",
    [
        "apass_source_r2",  # a live release partition
        "apass_source_r2_incoming",  # a Phase B1 build, mid-import
        "stetson_source_r6",
        "landolt_source_r10",  # two-digit release id
    ],
)
def test_runtime_data_tables_are_not_reflected(name):
    assert _table(SCHEMA_DATA, name) is False


@pytest.mark.parametrize("name", ["apass_dr10_stg", "apass_dr10_rejects"])
def test_staging_tables_are_not_reflected(name):
    assert _table(SCHEMA_STAGING, name) is False


def test_registry_tables_are_reflected():
    assert _table(SCHEMA_REGISTRY, "catalog_release") is True


def test_non_table_names_are_left_to_the_object_comparison():
    # Columns, indexes and constraints are reached only through a table that
    # already passed the filter, so there is nothing left to decide here.
    assert include_name("ix_apass_source_geom", "index", {}) is True


@pytest.mark.postgis
def test_metadata_matches_the_migrated_database(imported):
    """The load-bearing test: `compare_metadata` must find nothing.

    Runs as the ADMIN role — the identity that actually runs migrations, and the
    one an unfiltered autogenerate fails outright for.
    """
    engine = create_catalog_engine(
        imported.config_for(CatalogRole.ADMIN), pool_size=1, max_overflow=0
    )
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(
                connection=conn,
                opts={"version_table_schema": SCHEMA_REGISTRY, **autogenerate_options()},
            )
            diffs = compare_metadata(context, CatalogBase.metadata)
    finally:
        engine.dispose()

    assert diffs == [], (
        "CatalogBase.metadata no longer describes the migrated schema. Each "
        "entry below is an operation `alembic revision --autogenerate` would "
        "write into the next migration — declare it on the model (or filter it "
        "in skycat/database/autogen.py) rather than letting a contributor "
        f"commit the generated revision:\n{diffs}"
    )


@pytest.mark.postgis
def test_autogenerate_through_env_py_finds_nothing(imported):
    """The same assertion through ``env.py`` itself.

    `test_metadata_matches_the_migrated_database` builds its own context from
    the shared options; this one exercises the wiring — that `env.py` passes
    them to the online `context.configure`, and that the URL it resolves for the
    admin role is one autogenerate can actually reflect. `alembic check` raises
    `AutogenerateDiffsDetected` if there is anything to write.
    """
    command.check(make_alembic_config(imported.config_for(CatalogRole.ADMIN)))

"""Package-wide constants for skycat.

Kept dependency-free so every other module (config, models, migrations, CLI)
can import these without pulling in SQLAlchemy.
"""

from __future__ import annotations

import enum

# --- PostgreSQL schema layout -------------------------------------------------
# Management metadata, production catalog rows, and bulk-load staging are kept in
# three separate schemas so they can be granted, vacuumed, and reasoned about
# independently. See README "PostgreSQL schemas".
SCHEMA_REGISTRY = "catalog_registry"
SCHEMA_DATA = "catalog_data"
SCHEMA_STAGING = "catalog_staging"
ALL_SCHEMAS = (SCHEMA_REGISTRY, SCHEMA_DATA, SCHEMA_STAGING)

# Database the package is allowed to operate on. Refusing anything else is the
# primary guard against accidentally migrating/ingesting into the operational
# `sky` database (see config.CatalogDatabaseConfig.assert_not_primary_database).
EXPECTED_DATABASE_NAME = "catalogs"
FORBIDDEN_DATABASE_NAMES = ("sky", "postgres", "template0", "template1")

# Database name substrings that mark a target as production-like; destructive
# operations refuse to run against these without an explicit dangerous override.
PRODUCTION_DB_NAME_MARKERS = ("prod", "production", "staging", "live")

# --- Spatial representation ---------------------------------------------------
# Every positional table stores the original ``ra_deg``/``dec_deg`` columns plus
# a derived ``geom geography(Point,4326)`` GENERATED column. RA (0..360) is
# mapped to PostGIS longitude (-180..180) as: lon = ra_deg - 360 if ra_deg > 180
# else ra_deg.  Latitude == dec_deg.  Cone searches use ST_DWithin / ST_Distance
# with ``use_spheroid => false`` so distances are evaluated on a *sphere* (exact
# angular separation), not the WGS84 ellipsoid.
#
# POSTGIS_SPHERE_RADIUS_M is the sphere radius PostGIS uses for spherical
# geography math on SRID 4326 with use_spheroid=false — the WGS84 mean radius
# (2a + b) / 3 = 6371008.7714 m. We reuse the same constant to convert between
# angular degrees and the metres ST_DWithin/ST_Distance expect, so the
# degrees<->metres round-trip (and the reported angular separation) matches
# PostGIS to ~1e-9 deg.
POSTGIS_SPHERE_RADIUS_M = 6371008.7714
SRID = 4326

# Coordinate validity ranges.
RA_MIN_DEG = 0.0
RA_MAX_DEG = 360.0
DEC_MIN_DEG = -90.0
DEC_MAX_DEG = 90.0


class CatalogReleaseState(str, enum.Enum):
    """Lifecycle of a single catalog release.

    The runner walks REGISTERED → STAGING → READY → ACTIVE, with FAILED and
    SUPERSEDED as the terminal branches; these six are the whole machine. A
    release only ever becomes ACTIVE through an explicit, atomic activation that
    requires it to be READY (imported, transformed, indexed, validated). A
    FAILED/incomplete release can never auto-activate.
    """

    REGISTERED = "registered"
    STAGING = "staging"
    READY = "ready"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"


class IngestionRunStatus(str, enum.Enum):
    """Outcome of one ingestion attempt. The runner sets exactly these three."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ValidationStatus(str, enum.Enum):
    PASSED = "passed"
    PASSED_WITH_WARNINGS = "passed_with_warnings"
    FAILED = "failed"
    NOT_RUN = "not_run"


class CatalogRole(str, enum.Enum):
    """Logical connection identities. Mapped to concrete DB users by config."""

    BOOTSTRAP = "bootstrap"  # container superuser / DBA — init only
    ADMIN = "admin"  # owner / migrator
    INGEST = "ingest"  # bulk loader + release metadata writer
    READER = "reader"  # query / crossmatch consumer
    DEFAULT = "default"  # generic app identity (resolves to reader)


# Concrete role names created inside the catalogs database.
ROLE_OWNER = "catalog_owner"  # owns schemas + objects, applies migrations
ROLE_INGEST = "catalog_ingest"  # NOLOGIN group + login member loads data
ROLE_READER = "catalog_reader"  # read-only query consumer

# Internal schema version stamped onto releases; bump when the typed production
# table shape for a family changes in a way that affects already-imported rows.
INTERNAL_SCHEMA_VERSION = 1

# Importer version — recorded on each ingestion run / release for idempotency &
# provenance. Bump when parser/transform semantics change.
IMPORTER_VERSION = "1.0.0"

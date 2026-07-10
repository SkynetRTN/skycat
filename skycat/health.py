"""Comprehensive, read-only catalog health checks.

Distinguishes the many ways the catalog store can be unhealthy (unreachable,
wrong database, PostGIS missing, migrations behind, schema/role/registry gaps,
active-release-without-table, stuck imports, …). Never modifies the database.
The lightweight Compose/k8s probe is just ``pg_isready``; this is the deep check.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from .config import CatalogRole, CatalogSettings
from .constants import (
    ALL_SCHEMAS,
    FORBIDDEN_DATABASE_NAMES,
    ROLE_INGEST,
    ROLE_OWNER,
    ROLE_READER,
    SCHEMA_DATA,
    SCHEMA_REGISTRY,
    CatalogReleaseState,
)
from .database.engine import create_catalog_engine
from .database.migrate import current_revision, script_heads
from .registry.catalog_defs import CATALOG_FAMILIES

_TRANSIENT_STATES = (
    CatalogReleaseState.STAGING.value,
    CatalogReleaseState.LOADING.value,
    CatalogReleaseState.VALIDATING.value,
)


@dataclass
class HealthCheck:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class HealthReport:
    healthy: bool = True
    target: str = ""
    checks: list[HealthCheck] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append(HealthCheck(name, ok, detail))
        if not ok:
            self.healthy = False

    def to_dict(self) -> dict:
        return {"healthy": self.healthy, "target": self.target,
                "checks": [asdict(c) for c in self.checks]}


def health_report(
    settings: CatalogSettings,
    *,
    family: str | None = None,
    role: CatalogRole = CatalogRole.READER,
) -> HealthReport:
    cfg = settings.config_for(role)
    report = HealthReport(target=cfg.target_summary())

    # Guard first (no connection needed).
    report.add("not_primary_database", not cfg.is_forbidden_database(),
               f"target database is {cfg.name!r}")
    if cfg.is_forbidden_database():
        return report

    try:
        engine = create_catalog_engine(cfg, pool_size=1, max_overflow=0)
    except Exception as exc:  # pragma: no cover
        report.add("postgres_reachable", False, str(exc))
        return report

    try:
        with engine.connect() as conn:
            report.add("postgres_reachable", True, "SELECT 1 ok")
            report.add("credentials_accepted", True, f"connected as {cfg.user}")

            current_db = conn.execute(text("SELECT current_database()")).scalar_one()
            report.add(
                "catalog_database",
                current_db.lower() not in FORBIDDEN_DATABASE_NAMES,
                f"current_database={current_db}",
            )

            pg_ver = conn.execute(text(
                "SELECT extversion FROM pg_extension WHERE extname='postgis'"
            )).scalar()
            report.add("postgis_installed", bool(pg_ver), f"postgis {pg_ver}" if pg_ver else "missing")

            # pg_namespace (not information_schema.schemata) so the read-only
            # role can confirm a schema exists even without USAGE on it.
            present_schemas = set(conn.execute(text(
                "SELECT nspname FROM pg_namespace"
            )).scalars())
            for schema in ALL_SCHEMAS:
                report.add(f"schema_{schema}", schema in present_schemas,
                           "present" if schema in present_schemas else "missing")

            present_roles = set(conn.execute(text(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY(:n)"
            ), {"n": [ROLE_OWNER, ROLE_INGEST, ROLE_READER]}).scalars())
            for r in (ROLE_OWNER, ROLE_INGEST, ROLE_READER):
                report.add(f"role_{r}", r in present_roles,
                           "present" if r in present_roles else "missing")

            # Registry contents (only if registry schema exists).
            if SCHEMA_REGISTRY in present_schemas:
                fam_count = conn.execute(text(
                    f"SELECT count(*) FROM {SCHEMA_REGISTRY}.catalog_family"
                )).scalar() or 0
                report.add("catalogs_registered", fam_count > 0, f"{fam_count} families")

                # active release / table consistency
                rows = conn.execute(text(
                    f"SELECT f.slug, r.name, r.state, r.production_table "
                    f"FROM {SCHEMA_REGISTRY}.catalog_release r "
                    f"JOIN {SCHEMA_REGISTRY}.catalog_family f ON f.id=r.family_id "
                    f"WHERE r.state='active'"
                )).all()
                for slug, name, _state, prod in rows:
                    exists = False
                    if prod:
                        schema, _, tbl = prod.partition(".")
                        exists = bool(conn.execute(text(
                            "SELECT to_regclass(:q)"
                        ), {"q": f"{schema}.{tbl}"}).scalar())
                    report.add(f"active_table_{slug}", bool(prod) and exists,
                               f"{slug} active={name} table={prod} exists={exists}")

                # stuck imports
                cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
                stuck = conn.execute(text(
                    f"SELECT count(*) FROM {SCHEMA_REGISTRY}.catalog_release "
                    f"WHERE state = ANY(:s) AND import_started_at < :c"
                ), {"s": list(_TRANSIENT_STATES), "c": cutoff}).scalar() or 0
                report.add("no_stuck_imports", stuck == 0, f"{stuck} releases stuck >6h")

                if family:
                    active = conn.execute(text(
                        f"SELECT r.name FROM {SCHEMA_REGISTRY}.catalog_release r "
                        f"JOIN {SCHEMA_REGISTRY}.catalog_family f ON f.id=r.family_id "
                        f"WHERE f.slug=:s AND r.state='active'"
                    ), {"s": family}).scalar()
                    report.add(f"active_release_{family}", active is not None,
                               f"active={active}" if active else "no active release")

            # spatial indexes on each family parent
            if SCHEMA_DATA in present_schemas:
                for fam in CATALOG_FAMILIES:
                    if not fam.importer_available:
                        continue
                    has_parent = conn.execute(text("SELECT to_regclass(:q)"),
                                              {"q": f"{SCHEMA_DATA}.{fam.data_table}"}).scalar()
                    if not has_parent:
                        continue
                    has_gist = conn.execute(text(
                        "SELECT count(*) FROM pg_index i "
                        "JOIN pg_class c ON c.oid=i.indrelid "
                        "JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "JOIN pg_am am ON am.oid=(SELECT relam FROM pg_class WHERE oid=i.indexrelid) "
                        "WHERE n.nspname=:s AND c.relname=:t AND am.amname='gist'"
                    ), {"s": SCHEMA_DATA, "t": fam.data_table}).scalar() or 0
                    report.add(f"spatial_index_{fam.slug}", has_gist > 0,
                               "gist present" if has_gist else "missing gist index")
    except Exception as exc:  # pragma: no cover
        report.add("query_error", False, str(exc))
        return report
    finally:
        engine.dispose()

    # migrations current (own engine inside)
    try:
        admin = settings.config_for(CatalogRole.ADMIN if settings.admin_user else role)
        rev = current_revision(admin)
        heads = set(script_heads(admin))
        report.add("migrations_current", rev in heads if rev else False,
                   f"current={rev} heads={sorted(heads)}")
    except Exception as exc:
        report.add("migrations_current", False, str(exc))

    return report

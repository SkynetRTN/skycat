"""Skycat command-line interface (``skycat``).

One entry point for every catalog operation. Every destructive command is
explicit (``--force`` and, for production-like targets, ``--allow-production``).
Add ``--json`` for machine-readable output.

Errors are messages, not tracebacks — see :class:`_FriendlyGroup`. Set
``SKYCAT_DEBUG=1`` to get the original exception and its traceback back when the
cause is a bug rather than an operator's input.
"""

from __future__ import annotations

import csv
import json as jsonlib
import os
from contextlib import contextmanager

import click
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ..config import CatalogConfigError, CatalogRole, load_settings
from ..database.engine import DriverConnectionError, create_catalog_engine
from ..database.init import initialize_catalog_database, reset_catalog_database
from ..database.migrate import current_revision, script_heads, upgrade
from ..database.orm import MissingRowError
from ..database.postgis import PostgisUnavailableError
from ..health import health_report
from ..ingestion import IngestionError, discover_all, discover_one, import_release
from ..ingestion.maintenance import (
    clean_staging,
    remove_release,
    revalidate_release,
    table_sizes,
)
from ..registry import (
    ReleaseStateError,
    activate_release,
    deactivate_release,
    list_families,
    list_releases,
    resolve_release,
)
from ..query import (
    CatalogQueryError,
    batch_crossmatch,
    cone_search,
    cone_search_plan,
    lookup_native_id,
    radius_to_deg,
)


def _emit(ctx, data, human: str) -> None:
    if ctx.obj["json"]:
        click.echo(jsonlib.dumps(data, default=str, indent=2))
    else:
        click.echo(human)


@contextmanager
def _session(settings, role: CatalogRole):
    cfg = settings.config_for(role)
    cfg.assert_not_reserved_database()
    engine = create_catalog_engine(cfg, pool_size=1, max_overflow=0)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


class _ConfigException(click.ClickException):
    exit_code = 2


#: Environment variable that turns the friendly wrapper off. Named on the error
#: path in the module docstring, because the day you need it is the day a
#: message turned out to be hiding a bug rather than describing an input.
DEBUG_ENV_VAR = "SKYCAT_DEBUG"

#: Operational outcomes an operator needs to read. ``ValueError`` is in here on
#: purpose: it is what ``validate_radec`` raises for ``--ra 400``,
#: ``discover_one`` for an unknown family, and ``configparser`` for a password
#: with a ``%`` in it — three different layers, one meaning at this boundary.
_OPERATIONAL_ERRORS = (
    CatalogQueryError,
    IngestionError,
    ReleaseStateError,
    MissingRowError,
    PostgisUnavailableError,
    DriverConnectionError,
    ValueError,
)


def _debug_enabled() -> bool:
    return os.environ.get(DEBUG_ENV_VAR, "").strip().lower() in ("1", "true", "yes", "on")


def _driver_message(exc: DBAPIError) -> str:
    """The driver's own words, without the statement or the bound parameters.

    ``str(DBAPIError)`` appends ``[SQL: …]`` and ``[parameters: …]``. For a
    refused connection that is pure noise around the one line that matters, and
    for a failed bulk statement it can push a multi-kilobyte ``COPY`` — or a
    bound value the caller would rather not see logged — into a Job's output.
    """
    orig = exc.orig
    return str(orig).strip() if orig is not None else str(exc)


class _FriendlyGroup(click.Group):
    """Render operator-input errors as messages rather than tracebacks.

    Lives on the group (not the console-script wrapper) so it applies to every
    entry point — ``skycat``, ``python -m skycat``, and CliRunner in the tests.

    The catch is deliberately wide, because the set that escaped it was exactly
    the set of ordinary mistakes: a stale ``SKYCAT_DB_PORT``, a rotated
    password, a mistyped ``--ra``, an unknown family, a malformed crossmatch
    CSV. Each of those printed thirty lines of Python at whoever was reading a
    Kubernetes Job log. ``SKYCAT_DEBUG=1`` puts the traceback back.

    Database-driver failures are configuration exits: a wrong host, stale port,
    rotated password, or unavailable server is not an import/query outcome. The
    remaining translated exceptions are operational exits.
    """

    def invoke(self, ctx: click.Context):
        if _debug_enabled():
            # Diagnosis mode: no translation, so the original exception reaches
            # the interpreter with its traceback intact.
            return super().invoke(ctx)
        try:
            return super().invoke(ctx)
        except CatalogConfigError as exc:
            raise _ConfigException(str(exc)) from exc
        except DBAPIError as exc:
            # Wrong port, wrong password, server down: the driver is the first
            # layer that can prove the resolved database configuration is bad.
            raise _ConfigException(f"database error: {_driver_message(exc)}") from exc
        except _OPERATIONAL_ERRORS as exc:
            # Unknown family/column, no active release, a missing source file, a
            # failed import, an illegal state transition, a row that vanished
            # under us, PostGIS missing, a dropped driver connection.
            raise click.ClickException(str(exc)) from exc


@click.group(cls=_FriendlyGroup)
@click.option("--json", "json_out", is_flag=True, help="Machine-readable JSON output.")
@click.pass_context
def main(ctx: click.Context, json_out: bool) -> None:
    """Manage the standalone Skycat database (APASS, VSX, …)."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_out
    ctx.obj["settings"] = load_settings()


# --------------------------------------------------------------------- config
@main.command()
@click.pass_context
def config(ctx):
    """Show the resolved (redacted) configuration."""
    s = ctx.obj["settings"]
    data = {
        "url": s.base.safe_url(),
        "host": s.base.host,
        "port": s.base.port,
        "database": s.base.name,
        "default_user": s.default_user,
        "roles": {
            "bootstrap": s.bootstrap_user or "(falls back)",
            "admin": s.admin_user or "(falls back)",
            "ingest": s.ingest_user or "(falls back)",
            "reader": s.reader_user or "(falls back)",
        },
        "data_root": s.data_root,
        "work_root": s.work_root,
    }
    _emit(ctx, data, jsonlib.dumps(data, indent=2))


# ----------------------------------------------------------------- init/migrate
@main.command()
@click.pass_context
def init(ctx):
    """Enable PostGIS, create roles/grants/schemas, and apply migrations."""
    res = initialize_catalog_database(ctx.obj["settings"])
    data = {
        "database": res.database,
        "postgis": res.postgis_version,
        "roles": list(res.roles_ensured),
        "schemas": list(res.schemas),
        "migrated_to": res.migrated_to,
    }
    _emit(
        ctx,
        data,
        f"initialized {res.database}: postgis {res.postgis_version}, "
        f"migrated to {res.migrated_to}",
    )


@main.command()
@click.option("--revision", default="head")
@click.pass_context
def migrate(ctx, revision):
    """Apply Alembic migrations (owner/migrator role)."""
    cfg = ctx.obj["settings"].config_for(CatalogRole.ADMIN)
    upgrade(cfg, revision)
    rev = current_revision(cfg)
    _emit(ctx, {"current": rev}, f"migrated to {rev}")


@main.command("migrate-status")
@click.pass_context
def migrate_status(ctx):
    """Show current vs head migration revisions."""
    cfg = ctx.obj["settings"].config_for(CatalogRole.ADMIN)
    cur = current_revision(cfg)
    heads = script_heads(cfg)
    data = {
        "current": cur,
        "heads": heads,
        "up_to_date": cur in set(heads) if cur else False,
    }
    _emit(ctx, data, f"current={cur} heads={heads} up_to_date={data['up_to_date']}")


@main.command()
@click.option(
    "--family", default=None, help="Also check a specific family's active release."
)
@click.pass_context
def health(ctx, family):
    """Comprehensive (read-only) health report."""
    report = health_report(ctx.obj["settings"], family=family)
    lines = [f"target: {report.target}", f"healthy: {report.healthy}"]
    lines += [
        f"  [{'ok' if c.ok else 'FAIL'}] {c.name}: {c.detail}" for c in report.checks
    ]
    _emit(ctx, report.to_dict(), "\n".join(lines))
    if not report.healthy:
        ctx.exit(1)


# ------------------------------------------------------------------- families
@main.command()
@click.pass_context
def families(ctx):
    """List registered catalog families."""
    with _session(ctx.obj["settings"], CatalogRole.READER) as session:
        fams = list_families(session)
        data = [
            {
                "slug": f.slug,
                "display_name": f.display_name,
                "type": f.catalog_type,
                "enabled": f.enabled,
                "data_table": f.data_table,
            }
            for f in fams
        ]
    human = (
        "\n".join(f"{d['slug']:12} {d['display_name']} ({d['type']})" for d in data)
        or "(none)"
    )
    _emit(ctx, data, human)


# ------------------------------------------------------------------- discover
@main.command()
@click.argument("family", required=False)
@click.argument("release", required=False)
@click.option("--source-dir", default=None, help="Explicit source directory override.")
@click.pass_context
def discover(ctx, family, release, source_dir):
    """Discover local source releases under the data root."""
    s = ctx.obj["settings"]
    if family and release:
        found = [discover_one(s.data_root, family, release, explicit_dir=source_dir)]
    else:
        found = discover_all(s.data_root)
    data = [
        {
            "family": d.family.slug,
            "release": d.release.slug,
            "present": d.present,
            "source_dir": str(d.source_dir),
            "files": d.file_count,
            "aux_files": d.aux_file_count,
            "bytes": d.total_bytes,
            "issues": d.issues,
        }
        for d in found
    ]
    human = "\n".join(
        f"{d['family']}/{d['release']:8} present={d['present']} files={d['files']} "
        f"aux={d['aux_files']} bytes={d['bytes']} {d['source_dir']} {d['issues'] or ''}"
        for d in data
    )
    _emit(ctx, data, human)


# --------------------------------------------------------------------- import
@main.command("import")
@click.argument("family")
@click.argument("release")
@click.option("--activate", is_flag=True, help="Activate after a clean import.")
@click.option(
    "--allow-warnings", is_flag=True, help="Activate even with non-critical warnings."
)
@click.option("--replace", is_flag=True, help="Re-import an existing release.")
@click.option(
    "--force", is_flag=True, help="Required with --replace for an ACTIVE release."
)
@click.option("--source-dir", default=None)
@click.option(
    "--content-checksum",
    is_flag=True,
    help="Full content hash (slow) vs manifest hash.",
)
@click.option(
    "--keep-staging", is_flag=True, help="Retain staging tables after success."
)
@click.pass_context
def import_cmd(
    ctx,
    family,
    release,
    activate,
    allow_warnings,
    replace,
    force,
    source_dir,
    content_checksum,
    keep_staging,
):
    """Import (and optionally activate) a catalog release."""
    s = ctx.obj["settings"]
    ingest = s.config_for(CatalogRole.INGEST)
    # Safety: report the target + source before loading anything.
    click.echo(
        f"importing {family}/{release} -> {ingest.target_summary()} "
        f"(source: {s.data_root})",
        err=True,
    )

    def progress(n):
        click.echo(f"  loaded {n:,} rows…", err=True)

    report = import_release(
        s,
        family,
        release,
        activate=activate,
        allow_warnings=allow_warnings,
        explicit_dir=source_dir,
        replace=replace,
        force=force,
        content_checksum=content_checksum,
        keep_staging=keep_staging,
        on_progress=progress,
    )
    data = report.__dict__
    human = (
        f"{report.family}/{report.release}: parsed={report.parsed} loaded={report.loaded} "
        f"rejected={report.rejected} imported={report.imported}\n"
        f"  state={report.state} validation={report.validation_status} "
        f"activated={report.activated} table={report.production_table}"
        + (f"\n  skipped: {report.skipped_reason}" if report.skipped_reason else "")
    )
    _emit(ctx, data, human)

    # Asking for --activate and not getting it is a failure of intent, and it must
    # not look like success: the K8s ingest Job would otherwise be marked Complete
    # while the release sits in READY and default queries keep using old data.
    # The import itself is intact — re-run with --allow-warnings, or `activate`
    # explicitly, once the warnings have been read.
    if activate and not report.activated:
        raise click.ClickException(
            f"{family}/{release} imported but NOT activated: "
            f"{report.skipped_reason or 'activation did not occur'}"
        )


@main.command()
@click.argument("family")
@click.argument("release")
@click.pass_context
def validate(ctx, family, release):
    """Re-validate an imported release's production partition."""
    res = revalidate_release(ctx.obj["settings"], family, release)
    human = f"status={res['status']}\n" + "\n".join(
        f"  [{'ok' if c['passed'] else 'FAIL'}] {c['name']} ({c['level']}): {c['detail']}"
        for c in res["checks"]
    )
    _emit(ctx, res, human)


@main.command()
@click.argument("family")
@click.argument("release")
@click.pass_context
def activate(ctx, family, release):
    """Atomically activate a READY release."""
    with _session(ctx.obj["settings"], CatalogRole.INGEST) as session:
        rel = resolve_release(session, family, release)
        if rel is None:
            raise click.ClickException(f"No release {family}/{release}")
        activate_release(session, rel)
        session.commit()
        data = {"family": family, "release": rel.name, "state": str(rel.state)}
    _emit(ctx, data, f"activated {family}/{data['release']}")


@main.command()
@click.argument("family")
@click.argument("release")
@click.pass_context
def deactivate(ctx, family, release):
    """Supersede (deactivate) the active release."""
    with _session(ctx.obj["settings"], CatalogRole.INGEST) as session:
        rel = resolve_release(session, family, release)
        if rel is None:
            raise click.ClickException(f"No release {family}/{release}")
        deactivate_release(session, rel)
        session.commit()
        data = {"family": family, "release": rel.name, "state": str(rel.state)}
    _emit(ctx, data, f"deactivated {family}/{data['release']} -> {data['state']}")


# ------------------------------------------------------------------- releases
@main.command()
@click.argument("family", required=False)
@click.pass_context
def releases(ctx, family):
    """List releases (optionally for one family)."""
    with _session(ctx.obj["settings"], CatalogRole.READER) as session:
        rels = list_releases(session, family)
        data = [
            {
                "family": r.family.slug,
                "release": r.name,
                "state": str(r.state),
                "validation": str(r.validation_status),
                "rows": r.imported_row_count,
                "table": r.production_table,
            }
            for r in rels
        ]
    human = (
        "\n".join(
            f"{d['family']:8} {d['release']:10} {d['state']:11} val={d['validation']:20} "
            f"rows={d['rows']} {d['table'] or ''}"
            for d in data
        )
        or "(none)"
    )
    _emit(ctx, data, human)


@main.command()
@click.argument("family", required=False)
@click.pass_context
def history(ctx, family):
    """Show ingestion-run history."""
    from ..models.registry import CatalogFamily, CatalogRelease, IngestionRun
    from sqlalchemy import select

    with _session(ctx.obj["settings"], CatalogRole.READER) as session:
        stmt = (
            select(IngestionRun, CatalogRelease, CatalogFamily)
            .join(CatalogRelease, IngestionRun.release_id == CatalogRelease.id)
            .join(CatalogFamily, CatalogRelease.family_id == CatalogFamily.id)
            .order_by(IngestionRun.started_at.desc())
        )
        if family:
            stmt = stmt.where(CatalogFamily.slug == family.lower())
        data = []
        for run, rel, fam in session.execute(stmt).all():
            data.append(
                {
                    "family": fam.slug,
                    "release": rel.name,
                    "status": str(run.status),
                    "stage": run.stage,
                    "loaded": run.loaded_row_count,
                    "rejected": run.rejected_row_count,
                    "started": run.started_at,
                    "finished": run.finished_at,
                }
            )
    human = (
        "\n".join(
            f"{d['family']:8} {d['release']:10} {d['status']:10} loaded={d['loaded']} "
            f"rejected={d['rejected']} {d['started']}"
            for d in data
        )
        or "(none)"
    )
    _emit(ctx, data, human)


# ---------------------------------------------------------------------- query
@main.command()
@click.argument("family")
@click.option("--ra", type=float, required=True, help="Right ascension (deg).")
@click.option("--dec", type=float, required=True, help="Declination (deg).")
@click.option("--radius-deg", type=float, default=None)
@click.option("--radius-arcmin", type=float, default=None)
@click.option("--radius-arcsec", type=float, default=None)
@click.option("--release", default=None, help="Explicit release (default: active).")
@click.option("--limit", type=int, default=50)
@click.option(
    "--mag-band", default=None, help="Magnitude column to filter, e.g. johnson_v_mag."
)
@click.option("--mag-min", type=float, default=None)
@click.option("--mag-max", type=float, default=None)
@click.option(
    "--order-by",
    default=None,
    help="Numeric column to sort by, ascending (default: angular separation). "
    "A magnitude column gives brightest-first, e.g. johnson_v_mag.",
)
@click.option(
    "--explain", is_flag=True, help="Show the query plan (proves index usage)."
)
@click.pass_context
def cone(
    ctx,
    family,
    ra,
    dec,
    radius_deg,
    radius_arcmin,
    radius_arcsec,
    release,
    limit,
    mag_band,
    mag_min,
    mag_max,
    order_by,
    explain,
):
    """Cone search a catalog family."""
    s = ctx.obj["settings"]
    rdeg = radius_to_deg(
        radius_deg=radius_deg, radius_arcmin=radius_arcmin, radius_arcsec=radius_arcsec
    )
    if explain:
        plan = cone_search_plan(
            s, family, ra, dec, radius_deg=rdeg, release=release,
            order_by=order_by, limit=limit,
        )
        _emit(ctx, {"plan": plan}, plan)
        return
    rows = cone_search(
        s,
        family,
        ra,
        dec,
        radius_deg=rdeg,
        release=release,
        limit=limit,
        mag_band=mag_band,
        mag_min=mag_min,
        mag_max=mag_max,
        order_by=order_by,
    )
    human = f"{len(rows)} matches\n" + "\n".join(
        f"  {r.get('native_id'):20} ra={r['ra_deg']:.6f} dec={r['dec_deg']:.6f} "
        f'sep={r["separation_deg"] * 3600:.2f}"'
        for r in rows
    )
    _emit(ctx, rows, human)


@main.command()
@click.argument("family")
@click.argument("native_id")
@click.option("--release", default=None)
@click.pass_context
def lookup(ctx, family, native_id, release):
    """Look up rows by native source identifier."""
    rows = lookup_native_id(ctx.obj["settings"], family, native_id, release=release)
    human = f"{len(rows)} rows\n" + "\n".join(
        f"  {r.get('native_id')} ra={r['ra_deg']:.6f} dec={r['dec_deg']:.6f}"
        for r in rows
    )
    _emit(ctx, rows, human)


@main.command()
@click.argument("family")
@click.argument("input_csv", type=click.Path(exists=True))
@click.option("--radius-arcsec", type=float, default=5.0)
@click.option("--release", default=None)
@click.option(
    "--all-candidates", is_flag=True, help="Return all candidates (not just nearest)."
)
@click.option("--max-candidates", type=int, default=5)
@click.pass_context
def crossmatch(
    ctx, family, input_csv, radius_arcsec, release, all_candidates, max_candidates
):
    """Batch crossmatch a CSV of `id,ra,dec` against a release."""
    inputs = []
    with open(input_csv, newline="") as fh:
        for lineno, row in enumerate(csv.reader(fh), start=1):
            if not row or row[0].lower() in ("id", "input_id"):
                continue
            try:
                inputs.append((row[0], float(row[1]), float(row[2])))
            except (IndexError, ValueError) as exc:
                # Name the line. A crossmatch input is often thousands of rows
                # generated by something else, and "could not convert string to
                # float" alone leaves the operator diffing files by hand.
                raise click.ClickException(
                    f"{input_csv}:{lineno}: expected `id,ra,dec` with numeric "
                    f"coordinates, got {row!r}"
                ) from exc
    rows = batch_crossmatch(
        ctx.obj["settings"],
        family,
        inputs,
        radius_deg=radius_arcsec / 3600.0,
        release=release,
        nearest_only=not all_candidates,
        max_candidates=max_candidates,
    )
    human = "\n".join(
        f"  {r['input_id']:12} -> "
        + (
            f'{r.get("native_id")} sep={r["separation_deg"] * 3600:.2f}"'
            if r["matched"]
            else "(no match)"
        )
        for r in rows
    )
    _emit(ctx, rows, human)


# --------------------------------------------------------------- maintenance
@main.command()
@click.pass_context
def sizes(ctx):
    """Report table + index sizes."""
    rows = table_sizes(ctx.obj["settings"])
    human = "\n".join(
        f"  {r['schema']}.{r['table']:24} total={r['total_bytes'] / 1e6:.1f}MB "
        f"idx={r['index_bytes'] / 1e6:.1f}MB rows~{r['approx_rows']}"
        for r in rows
    )
    _emit(ctx, rows, human)


@main.command("clean-staging")
@click.pass_context
def clean_staging_cmd(ctx):
    """Drop staging tables (never touches production or source files)."""
    dropped = clean_staging(ctx.obj["settings"])
    _emit(ctx, {"dropped": dropped}, "dropped: " + (", ".join(dropped) or "(none)"))


@main.command("remove-release")
@click.argument("family")
@click.argument("release")
@click.option("--force", is_flag=True, help="Allow removing an ACTIVE release.")
@click.pass_context
def remove_release_cmd(ctx, family, release, force):
    """Remove an INACTIVE release (drops its partition + registry rows)."""
    res = remove_release(ctx.obj["settings"], family, release, force=force)
    _emit(ctx, res, f"removed {res['removed']} (table {res['production_table']})")


@main.command()
@click.option(
    "--force", is_flag=True, help="Required — destructive (explicit confirmation)."
)
@click.option(
    "--allow-production", is_flag=True, help="Override production-like guard."
)
@click.pass_context
def reset(ctx, force, allow_production):
    """DESTRUCTIVE (dev only): drop the catalog schemas. Never drops the DB/volume/source."""
    reset_catalog_database(
        ctx.obj["settings"], force=force, allow_production=allow_production
    )
    _emit(ctx, {"reset": True}, "catalog schemas dropped; run `init` to rebuild")


def main_entry() -> None:  # console-script entry point
    main(obj={})


if __name__ == "__main__":
    main_entry()

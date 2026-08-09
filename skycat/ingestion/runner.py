"""Ingestion lifecycle orchestrator.

Runs the full ingestion flow for one (family, release):

    discover -> register family -> register release -> ingestion-run ->
    verify checksum -> stage (COPY) -> validate staging -> transform into a
    new detached production partition -> build indexes -> ANALYZE ->
    validate production -> ATTACH partition -> mark READY -> [activate].

Bulk load is streaming COPY into an UNLOGGED staging table (bounded memory, no
per-row ORM inserts). The release is built as a *detached* table, indexed, then
``ATTACH PARTITION`` — so activation is atomic and never rewrites existing rows,
and the previously active release keeps serving queries until the new one is
fully READY and explicitly activated.

Staging is committed before the transform so it survives a transform failure for
diagnosis; the failure recorder writes registry/ingestion-run rows on its own
short-lived engine (see :func:`_record_failure`) so a failure is recorded even
when the identity that was loading has stopped working.

**The release row describes the partition on disk, not the attempt.** Source
provenance, row counts and ``production_table`` are written only in the finalize
block, after the Phase B2 swap has succeeded; the attempt's own provenance lives
on the per-attempt ``IngestionRun`` row until then. So a ``--replace`` that fails
before the swap leaves the release exactly as it was — same checksum, same
counts, same state — which is what makes idempotency honest (the recorded
checksum is by construction one that was successfully imported) and what keeps a
SUPERSEDED release rollback-able after a failed attempt to replace it.

Phase boundaries are logged as structured events on the ``skycat.ingestion``
logger — see :func:`_event` and docs/operations/runbook.md. An import of APASS DR10 runs
for hours; without them the only signals are the registry row (written at the
start and the end, nothing in between) and the row-count callback.
"""

from __future__ import annotations

import logging
import re
import socket
import traceback
from dataclasses import dataclass, field, replace as dc_replace
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import CatalogDatabaseConfig, CatalogRole, CatalogSettings
from ..constants import (
    IMPORTER_VERSION,
    INTERNAL_SCHEMA_VERSION,
    SCHEMA_DATA,
    SCHEMA_STAGING,
    CatalogReleaseState,
    IngestionRunStatus,
    ValidationStatus,
)
from ..database.base import CatalogBase
from ..database.engine import create_catalog_engine
from ..database.orm import require_row
from ..models.registry import CatalogRelease, IngestionRun, ValidationSummary
from ..registry import (
    activate_release,
    get_or_create_release,
    sync_family,
)
from ..registry.catalog_defs import get_family_def
from ..validation import (
    summarize,
    validate_family_staging,
    validate_production,
    validate_staging_common,
)
from . import copy_loader
from .discovery import compute_content_checksum, discover_one
from .parsers import ParseStats, get_parser


logger = logging.getLogger("skycat.ingestion")

#: The states that mean "this release has a production partition that was built,
#: indexed and validated". Idempotency skips a re-import of one of these, and the
#: failure recorder refuses to mark one FAILED — in both cases because there is
#: real data behind the row.
_BUILT_STATES = (
    CatalogReleaseState.ACTIVE.value,
    CatalogReleaseState.READY.value,
    CatalogReleaseState.SUPERSEDED.value,
)


class IngestionError(RuntimeError):
    pass


def _event(event: str, *, level: int = logging.INFO, **fields) -> None:
    """Emit one structured ingestion event.

    The fields go under a single ``skycat`` key rather than being spread across
    ``extra``: ``LogRecord`` already owns ``name``, ``module``, ``message`` and a
    dozen others, and a field collision raises inside the logging call — which
    would turn an observability nicety into an import failure.

    The rendered message repeats the fields, so a plain
    ``logging.basicConfig()`` is still readable; a JSON formatter reads
    ``record.skycat`` and gets them typed.
    """
    payload = {"event": event, **fields}
    logger.log(
        level,
        "%s %s",
        event,
        " ".join(f"{k}={v}" for k, v in fields.items()),
        extra={"skycat": payload},
    )


@dataclass
class ImportReport:
    family: str
    release: str
    target: str
    source_dir: str
    parsed: int = 0
    loaded: int = 0
    rejected: int = 0
    imported: int = 0
    production_table: str | None = None
    state: str = ""
    validation_status: str = ""
    checks: list[dict] = field(default_factory=list)
    activated: bool = False
    skipped_reason: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sanitize(name: str) -> str:
    return re.sub(r"[^a-z0-9_]", "_", name.lower())


def _table_for(data_table: str):
    return CatalogBase.metadata.tables[f"{SCHEMA_DATA}.{data_table}"]


def _partition_name(data_table: str, release_id: int) -> str:
    return f"{data_table}_r{release_id}"


def _drop_existing_partition(conn, data_table: str, release_id: int) -> None:
    child = _partition_name(data_table, release_id)
    fqn = f'{SCHEMA_DATA}."{child}"'
    # Detach if currently attached, then drop.
    attached = conn.execute(
        text(
            "SELECT 1 FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :s AND c.relname = :c"
        ),
        {"s": SCHEMA_DATA, "c": child},
    ).scalar()
    if attached:
        conn.execute(
            text(f'ALTER TABLE {SCHEMA_DATA}."{data_table}" DETACH PARTITION {fqn}')
        )
    conn.execute(text(f"DROP TABLE IF EXISTS {fqn}"))


def _replicate_parent_indexes(conn, data_table: str, child: str) -> None:
    """Recreate every non-PK parent index on the detached child (auto-named) so
    ATTACH can adopt them without a rebuild scan.

    Uses pg_index/pg_get_indexdef (not pg_indexes) because indexes on a
    *partitioned* parent have relkind 'I' and are not listed by pg_indexes.
    """
    rows = conn.execute(
        text(
            "SELECT c.relname AS indexname, pg_get_indexdef(i.indexrelid) AS indexdef "
            "FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_class t ON t.oid = i.indrelid "
            "JOIN pg_namespace n ON n.oid = t.relnamespace "
            "WHERE n.nspname = :s AND t.relname = :t AND NOT i.indisprimary"
        ),
        {"s": SCHEMA_DATA, "t": data_table},
    ).all()
    for indexname, indexdef in rows:
        # Drop the explicit (parent) index name first so the child index
        # auto-names and we don't collide with the parent index name.
        # pg_get_indexdef emits "CREATE [UNIQUE] INDEX <name> ON ..." — UNIQUE
        # comes before INDEX, so only the name is between INDEX and ON.
        ddl = re.sub(
            r'\bINDEX\s+"?' + re.escape(indexname) + r'"?\s+ON',
            "INDEX ON",
            indexdef,
        )
        # Retarget from the (partitioned) parent — pg_get_indexdef emits
        # "ON ONLY <schema>.<parent>" — to the detached child.
        ddl = re.sub(
            r"\bON\s+(?:ONLY\s+)?"
            + re.escape(SCHEMA_DATA)
            + r"\."
            + re.escape(data_table)
            + r"\b",
            f'ON {SCHEMA_DATA}."{child}"',
            ddl,
        )
        conn.execute(text(ddl))


def _record_failure(
    ingest_cfg: CatalogDatabaseConfig,
    *,
    family_slug: str,
    release_name: str,
    run_id: int | None,
    error: str,
    detail: dict,
) -> str | None:
    """Record a failed import. Returns the release state left behind, or None.

    Built on its **own** engine rather than the loader's. Same host and role, but
    a fresh short-lived pool with ``pool_pre_ping`` on and no
    ``statement_timeout``: every cause that can break the loader — a revoked
    grant, a password rotated mid-import, a server that went away, a timeout
    sized for readers — would otherwise break the recorder with it, which is
    precisely the "separate connection" claim not holding.

    The broad catch stays: recording a failure must never mask the original
    error, which the caller re-raises. What it must not do is stay quiet about
    it, so its own exception is logged as ``import.record_failed`` at ERROR.
    """
    engine = None
    try:
        recorder_cfg = dc_replace(
            ingest_cfg, statement_timeout_ms=None, pool_pre_ping=True
        )
        engine = create_catalog_engine(recorder_cfg, pool_size=1, max_overflow=0)
        with Session(engine) as meta:
            release_id = meta.scalar(
                text(
                    "SELECT r.id FROM catalog_registry.catalog_release r "
                    "JOIN catalog_registry.catalog_family f ON f.id = r.family_id "
                    "WHERE f.slug = :fs AND r.name = :rn"
                ).bindparams(fs=family_slug, rn=release_name)
            )
            # No require_row here: this is the recorder, and a raise would cost
            # the failure marking *and* the run row.
            release = (
                meta.get(CatalogRelease, release_id) if release_id is not None else None
            )
            state: str | None = None
            if release is not None:
                # A release with a built partition is not FAILED because a later
                # attempt to replace it failed. Phase B1 builds detached, so that
                # partition was never touched: an ACTIVE release is still serving
                # it and a SUPERSEDED one can still be activated for rollback.
                # Only a release with nothing behind it (REGISTERED, or a
                # previous FAILED) actually failed.
                built = bool(release.production_table) and release.state in _BUILT_STATES
                if not built:
                    release.state = CatalogReleaseState.FAILED.value
                release.failure_detail = detail
                state = release.state
            if run_id is not None:
                run = meta.get(IngestionRun, run_id)
                if run is not None and run.status == IngestionRunStatus.RUNNING.value:
                    run.status = IngestionRunStatus.FAILED.value
                    run.finished_at = _now()
                    run.message = error
                    # Merge, not replace: the attempted source provenance was
                    # parked here at the start of the run and is the only record
                    # of which tree this attempt was reading.
                    run.detail = {**(run.detail or {}), **detail}
            meta.commit()
            return state
    except Exception as rec_exc:  # noqa: BLE001 -- see the docstring: the original error is re-raised by the caller and must not be masked
        _event(
            "import.record_failed",
            level=logging.ERROR,
            family=family_slug,
            release=release_name,
            run_id=run_id,
            error=str(rec_exc),
        )
        return None
    finally:
        if engine is not None:
            engine.dispose()


def import_release(
    settings: CatalogSettings,
    family_slug: str,
    release_slug: str,
    *,
    activate: bool = False,
    allow_warnings: bool = False,
    explicit_dir: str | Path | None = None,
    replace: bool = False,
    force: bool = False,
    content_checksum: bool = False,
    keep_staging: bool = False,
    on_progress=None,
) -> ImportReport:
    fam_def = get_family_def(family_slug)
    if fam_def is None or not fam_def.importer_available:
        raise IngestionError(f"No importer available for family {family_slug!r}")
    rel_def = fam_def.release(release_slug)
    if rel_def is None:
        raise IngestionError(
            f"Unknown release {release_slug!r} for {family_slug!r} "
            f"(known: {[r.slug for r in fam_def.releases]})"
        )

    discovered = discover_one(
        settings.data_root, family_slug, release_slug, explicit_dir=explicit_dir
    )
    if not discovered.present:
        raise IngestionError(
            f"Source for {family_slug}/{release_slug} not found: {discovered.issues}"
        )

    checksum = (
        compute_content_checksum(discovered.data_files)
        if content_checksum
        else discovered.manifest_checksum()
    )

    ingest_cfg = settings.config_for(CatalogRole.INGEST)
    ingest_cfg.assert_not_reserved_database()
    engine = create_catalog_engine(ingest_cfg, pool_size=2, max_overflow=2)

    data_table = fam_def.data_table
    table = _table_for(data_table)
    report = ImportReport(
        family=family_slug,
        release=rel_def.name,
        target=ingest_cfg.target_summary(),
        source_dir=str(discovered.source_dir),
    )
    run_id: int | None = None

    _event(
        "import.started",
        family=family_slug,
        release=rel_def.name,
        target=ingest_cfg.target_summary(),
        source_dir=str(discovered.source_dir),
        source_files=len(discovered.data_files),
        source_bytes=discovered.total_bytes,
        checksum=checksum,
        checksum_mode="content" if content_checksum else "manifest",
        replace=replace,
        force=force,
    )

    try:
        # --- registry bookkeeping (own connection, committed per step) --------
        with Session(engine) as meta:
            family = sync_family(meta, fam_def)
            meta.commit()
            release = get_or_create_release(
                meta,
                family,
                name=rel_def.name,
                version=rel_def.version,
                internal_schema_version=INTERNAL_SCHEMA_VERSION,
                importer_version=IMPORTER_VERSION,
            )
            # Idempotency: skip a completed, unchanged, non-replace re-import.
            # "Completed" = already imported into a production partition, whether
            # it is currently active, merely ready, or superseded by a newer one.
            # source_checksum is only written after the swap succeeds, so a match
            # here means *this* tree was successfully imported — not merely that
            # someone once attempted it.
            if (
                not replace
                and release.source_checksum == checksum
                and release.state in _BUILT_STATES
                and release.production_table
            ):
                report.state = release.state
                report.validation_status = release.validation_status
                report.production_table = release.production_table
                report.skipped_reason = (
                    "already imported (matching checksum); use --replace to force"
                )
                report.imported = int(release.imported_row_count or 0)
                _event(
                    "import.skipped",
                    family=family_slug,
                    release=rel_def.name,
                    release_id=release.id,
                    state=report.state,
                    reason="unchanged_checksum",
                )
                return report

            was_active = release.state == CatalogReleaseState.ACTIVE.value
            if was_active and not (replace and force):
                raise IngestionError(
                    f"Release {rel_def.name} is ACTIVE; re-importing requires --replace --force."
                )

            release_id = release.id
            # Nothing is written to the release row here — not the provenance, not
            # the state, not even a timestamp. Every column on it describes the
            # partition currently on disk, and until Phase B2 swaps, that is still
            # the old one. Writing the new source's checksum up front made the
            # registry describe a tree that was never imported, and made the next
            # plain import skip as "already imported"; moving a READY/SUPERSEDED
            # release to STAGING deleted the documented SUPERSEDED -> ACTIVE
            # rollback path with no CLI way back. Both are properties of the
            # *attempt*, so both live on the per-attempt IngestionRun row below,
            # which is also what `skycat health`'s no_stuck_imports check and the
            # runbook's in-flight query read.
            import_started_at = _now()
            attempted = {
                "source_location": str(discovered.source_dir),
                "source_format": rel_def.source_format,
                "source_checksum": checksum,
                "source_size_bytes": discovered.total_bytes,
                "checksum_mode": "content" if content_checksum else "manifest",
                "expected_row_count": rel_def.approx_row_count,
            }

            run = IngestionRun(
                release_id=release_id,
                status=IngestionRunStatus.RUNNING.value,
                stage="staging",
                importer_version=IMPORTER_VERSION,
                host=socket.gethostname(),
                source_path=str(discovered.source_dir),
                started_at=import_started_at,
                detail=attempted,
            )
            meta.add(run)
            meta.commit()
            run_id = run.id

        staging_table = _sanitize(f"{family_slug}_{rel_def.name}_stg")
        staging_fqn = f'{SCHEMA_STAGING}."{staging_table}"'
        rejects_fqn = f'{SCHEMA_STAGING}."{_sanitize(family_slug + "_" + rel_def.name + "_rejects")}"'

        parser = get_parser(rel_def.source_format)
        stats = ParseStats()

        # --- Phase A: stage + validate (committed; survives a transform error) -
        with engine.begin() as conn:
            columns = copy_loader.create_staging_table(conn, staging_fqn, table)
            data_paths = [f.path for f in discovered.data_files]
            loaded = copy_loader.copy_rows(
                conn,
                staging_fqn,
                columns,
                parser.iter_rows(data_paths, stats),
                on_progress=on_progress,
            )
            checks, valid, rejected = validate_staging_common(conn, staging_fqn)
            checks += validate_family_staging(family_slug, conn, staging_fqn)
            # Retain rejected rows for diagnostics.
            conn.execute(text(f"DROP TABLE IF EXISTS {rejects_fqn}"))
            conn.execute(
                text(
                    f"CREATE UNLOGGED TABLE {rejects_fqn} AS "
                    f"SELECT * FROM {staging_fqn} WHERE reject_reason IS NOT NULL"
                )
            )

        report.parsed = stats.parsed
        report.loaded = loaded
        report.rejected = rejected
        status = summarize(checks)
        report.validation_status = status.value
        report.checks = [c.to_dict() for c in checks]

        _event(
            "phase_a.completed",
            family=family_slug,
            release=rel_def.name,
            release_id=release_id,
            run_id=run_id,
            staging_table=staging_table,
            parsed=stats.parsed,
            loaded=loaded,
            rejected=rejected,
            malformed=stats.malformed,
            validation_status=status.value,
        )

        if status == ValidationStatus.FAILED:
            raise IngestionError(
                "Staging validation failed (critical): "
                + "; ".join(
                    c.detail for c in checks if not c.passed and c.level == "critical"
                )
            )

        # --- Phase B: build a detached replacement, then atomically swap it in -
        # The replacement is built as a standalone (unattached) table so the
        # partition parent is NOT locked during the multi-minute rebuild and the
        # existing partition keeps serving reads. Only the final swap (Phase B2)
        # takes a brief ACCESS EXCLUSIVE on the parent — metadata-only DETACH/
        # ATTACH; the redundant CHECK lets ATTACH skip its validation scan.
        child = _partition_name(data_table, release_id)
        child_fqn = f'{SCHEMA_DATA}."{child}"'
        incoming = f"{child}_incoming"
        incoming_fqn = f'{SCHEMA_DATA}."{incoming}"'
        incoming_chk = f"{incoming}_relchk"
        data_cols = copy_loader.data_column_names(table)
        col_list = ", ".join(f'"{c}"' for c in data_cols)

        _event(
            "phase_b1.started",
            family=family_slug,
            release=rel_def.name,
            release_id=release_id,
            run_id=run_id,
            building=incoming,
            # The long pole. Nothing else is logged until the transform, primary
            # key, replicated indexes, and ANALYZE are all done, which on the
            # largest families is most of the import's wall clock.
            note="detached build; the current partition keeps serving reads",
        )

        # Phase B1 — transform + index + validate the detached replacement. Takes
        # no lock on the partition parent (only the new standalone table).
        with engine.begin() as conn:
            # Clear any leftover build from a crashed prior run before rebuilding.
            conn.execute(text(f"DROP TABLE IF EXISTS {incoming_fqn}"))
            conn.execute(
                text(
                    f"CREATE TABLE {incoming_fqn} "
                    f'(LIKE {SCHEMA_DATA}."{data_table}" INCLUDING DEFAULTS INCLUDING GENERATED)'
                )
            )
            conn.execute(
                text(
                    f"ALTER TABLE {incoming_fqn} ALTER COLUMN release_id SET DEFAULT {release_id}"
                )
            )
            # Transform: typed copy from valid staging rows (geom auto-generated).
            conn.execute(
                text(
                    f"INSERT INTO {incoming_fqn} ({col_list}) "
                    f"SELECT {col_list} FROM {staging_fqn} WHERE reject_reason IS NULL"
                )
            )
            imported = conn.execute(
                text(f"SELECT count(*) FROM {incoming_fqn}")
            ).scalar_one()
            # Constrain + index (after load), then ANALYZE. The CHECK stays on the
            # table through the swap so ATTACH can skip its partition scan.
            conn.execute(
                text(
                    f'ALTER TABLE {incoming_fqn} ADD CONSTRAINT "{incoming_chk}" '
                    f"CHECK (release_id = {release_id})"
                )
            )
            conn.execute(
                text(f"ALTER TABLE {incoming_fqn} ADD PRIMARY KEY (release_id, id)")
            )
            _replicate_parent_indexes(conn, data_table, incoming)
            conn.execute(text(f"ANALYZE {incoming_fqn}"))
            prod_checks = validate_production(
                conn,
                SCHEMA_DATA,
                incoming,
                expected_row_count=rel_def.approx_row_count,
            )
            if summarize(prod_checks) == ValidationStatus.FAILED:
                # Drop the failed build; the old partition is untouched and, for a
                # --replace of an active release, the release is still ACTIVE.
                conn.execute(text(f"DROP TABLE IF EXISTS {incoming_fqn}"))
                raise IngestionError(
                    "Production validation failed: "
                    + "; ".join(
                        c.detail
                        for c in prod_checks
                        if not c.passed and c.level == "critical"
                    )
                )

        # Phase B2 — atomic swap. Short transaction: DETACH+DROP the old partition
        # (if any), rename the validated replacement into the canonical partition
        # name, and ATTACH it. The parent's ACCESS EXCLUSIVE lock is held only for
        # these metadata operations, not the rebuild. DDL is transactional, so a
        # failure here rolls the swap back and leaves the old partition serving.
        _event(
            "phase_b1.completed",
            family=family_slug,
            release=rel_def.name,
            release_id=release_id,
            run_id=run_id,
            rows=int(imported),
            validation_status=summarize(prod_checks).value,
        )

        with engine.begin() as conn:
            _drop_existing_partition(conn, data_table, release_id)
            conn.execute(text(f'ALTER TABLE {incoming_fqn} RENAME TO "{child}"'))
            conn.execute(
                text(
                    f'ALTER TABLE {SCHEMA_DATA}."{data_table}" ATTACH PARTITION {child_fqn} '
                    f"FOR VALUES IN ({release_id})"
                )
            )
            conn.execute(
                text(f'ALTER TABLE {child_fqn} DROP CONSTRAINT "{incoming_chk}"')
            )

        _event(
            "phase_b2.swapped",
            family=family_slug,
            release=rel_def.name,
            release_id=release_id,
            run_id=run_id,
            partition=f"{SCHEMA_DATA}.{child}",
        )

        checks += prod_checks
        report.imported = int(imported)
        report.production_table = f"{SCHEMA_DATA}.{child}"
        report.checks = [c.to_dict() for c in checks]
        final_status = summarize(checks)
        report.validation_status = final_status.value

        # --- finalize registry -------------------------------------------------
        with Session(engine) as meta:
            release = require_row(
                meta.get(CatalogRelease, release_id),
                f"catalog_release id={release_id}",
            )
            # Provenance lands here, with the row counts and the partition name,
            # because only now has it been earned: these columns are what
            # guides/provenance.md proves a release against, and every one of
            # them now describes the rows that Phase B2 just made live.
            release.source_location = str(discovered.source_dir)
            release.source_format = rel_def.source_format
            release.source_checksum = checksum
            release.source_size_bytes = discovered.total_bytes
            release.source_modified_at = discovered.latest_modified()
            release.expected_row_count = rel_def.approx_row_count
            # Captured before Phase A, written now: the pair of timestamps can no
            # longer claim a start later than the completion it belongs to.
            release.import_started_at = import_started_at
            release.failure_detail = None
            release.parsed_row_count = stats.parsed
            release.imported_row_count = int(imported)
            release.rejected_row_count = rejected
            release.production_table = f"{SCHEMA_DATA}.{child}"
            # An in-place --replace of the ACTIVE release stays ACTIVE — the Phase
            # B2 swap already put the new data live under the same release_id. A
            # fresh/non-active release lands READY and is activated only on request.
            release.state = (
                CatalogReleaseState.ACTIVE.value
                if was_active
                else CatalogReleaseState.READY.value
            )
            release.validation_status = final_status.value
            release.validated_at = _now()
            release.import_completed_at = _now()
            meta.add(
                ValidationSummary(
                    release_id=release_id,
                    ingestion_run_id=run_id,
                    status=final_status.value,
                    checks=[c.to_dict() for c in checks],
                )
            )
            run = require_row(meta.get(IngestionRun, run_id), f"ingestion_run id={run_id}")
            run.status = IngestionRunStatus.SUCCEEDED.value
            run.stage = "ready"
            run.parsed_row_count = stats.parsed
            run.loaded_row_count = int(imported)
            run.rejected_row_count = rejected
            run.finished_at = _now()
            run.detail = {
                **attempted,
                "malformed": stats.malformed,
                "malformed_examples": stats.malformed_examples or [],
            }
            meta.commit()
            report.state = release.state

            if was_active:
                # Already live via the Phase B2 swap; no supersede/activate step.
                report.activated = True
            elif activate:
                if final_status == ValidationStatus.FAILED:
                    raise IngestionError("cannot activate: validation failed")
                if (
                    final_status == ValidationStatus.PASSED_WITH_WARNINGS
                    and not allow_warnings
                ):
                    report.skipped_reason = (
                        "not activated: validation warnings (use --allow-warnings)"
                    )
                else:
                    activate_release(meta, release)
                    meta.commit()
                    report.activated = True
                    report.state = CatalogReleaseState.ACTIVE.value

        # --- cleanup staging on success ---------------------------------------
        if not keep_staging:
            with engine.begin() as conn:
                conn.execute(text(f"DROP TABLE IF EXISTS {staging_fqn}"))

        _event(
            "import.completed",
            family=family_slug,
            release=rel_def.name,
            release_id=release_id,
            run_id=run_id,
            state=report.state,
            activated=report.activated,
            imported=report.imported,
            rejected=rejected,
            validation_status=report.validation_status,
            partition=report.production_table,
        )
        return report

    except Exception as exc:  # noqa: BLE001 - record failure then re-raise
        detail = {"error": str(exc), "traceback": traceback.format_exc()[-4000:]}
        _event(
            "import.failed",
            family=family_slug,
            release=rel_def.name,
            run_id=run_id,
            error=str(exc),
        )
        recorded_state = _record_failure(
            ingest_cfg,
            family_slug=family_slug,
            release_name=rel_def.name,
            run_id=run_id,
            error=str(exc),
            detail=detail,
        )
        # The report is not returned on this path, but it must not claim FAILED
        # for a release the recorder deliberately left ACTIVE or SUPERSEDED.
        if recorded_state is not None:
            report.state = recorded_state
        raise IngestionError(str(exc)) from exc
    finally:
        engine.dispose()

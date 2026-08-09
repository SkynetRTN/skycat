"""Imports that fail *after* the database has been touched.

`test_missing_source_fails_cleanly` was the suite's only failure test, and it
fails inside `discover_one` before a connection is opened. Everything below
drives a real Phase B1 failure instead: staging is created, loaded and
committed, the detached build is created, and `production_rows` (CRITICAL) then
fails on zero rows. Four defects lived in that untested region:

* **F1** — a failed `--replace` wrote the *new* source's provenance onto the
  release row before any data work, so the registry described a tree that was
  never imported and the next plain import was skipped as "already imported".
* **F6** — a failed `--replace` of a READY/SUPERSEDED release stranded it in
  STAGING/FAILED even though its partition was never touched, deleting the
  documented rollback path with no CLI way back.
* **F9** — the failure recorder reused the identity that had just failed and
  swallowed its own exception, so "a failure is always recorded" was
  unfalsifiable.
* **F13** — `failure_detail = None` wrote JSONB `'null'`, not SQL NULL, so
  "which releases have a recorded failure?" matched every release ever imported.

The zero-row source is the cheapest honest failure: nothing parses, so Phase A's
range checks all pass on an empty table (the row-count check there is INFO), and
the first CRITICAL that can fire is `production_rows` in Phase B1 — after the
commit boundary that makes this class of bug possible at all.

These tests mutate the session-scoped `imported` fixture's APASS releases, so
the module restores its canonical shape (DR10 ACTIVE, DR6 SUPERSEDED, no
leftover staging tables) on teardown.
"""

from __future__ import annotations

import logging
import queue
import threading
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from skycat.config import CatalogDatabaseConfig, CatalogRole
from skycat.database.engine import create_catalog_engine
from skycat.ingestion import IngestionError, import_release
from skycat.registry import activate_release, resolve_release

# --------------------------------------------------------------------- helpers


def _reader_engine(settings):
    return create_catalog_engine(
        settings.config_for(CatalogRole.READER), pool_size=1, max_overflow=0
    )


def _release_row(settings, family: str, name: str) -> dict:
    eng = _reader_engine(settings)
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT r.id, r.state, r.source_location, r.source_checksum, "
                    "       r.source_size_bytes, r.production_table, r.imported_row_count, "
                    "       r.expected_row_count, r.import_started_at, r.import_completed_at, "
                    "       r.failure_detail IS NOT NULL AS has_failure, "
                    "       jsonb_typeof(r.failure_detail) AS failure_type, "
                    "       r.failure_detail ->> 'error' AS failure_detail_error "
                    "FROM catalog_registry.catalog_release r "
                    "JOIN catalog_registry.catalog_family f ON f.id = r.family_id "
                    "WHERE f.slug = :fs AND r.name = :rn"
                ),
                {"fs": family, "rn": name},
            ).mappings().one()
    finally:
        eng.dispose()
    return dict(row)


def _latest_run(settings, release_id: int) -> dict:
    eng = _reader_engine(settings)
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, status, stage, message, detail, finished_at "
                    "FROM catalog_registry.ingestion_run "
                    "WHERE release_id = :rid ORDER BY id DESC LIMIT 1"
                ),
                {"rid": release_id},
            ).mappings().one()
    finally:
        eng.dispose()
    return dict(row)


def _drop_staging_tables(settings) -> None:
    """Drop the `_stg` tables a failed import deliberately leaves behind.

    Retention is the documented behaviour (the runbook tells operators to read
    them), but `test_staging_table_cleaned` asserts the *success* path leaves
    none, so this module must not hand it a leftover.
    """
    eng = create_catalog_engine(
        settings.config_for(CatalogRole.INGEST), pool_size=1, max_overflow=0
    )
    try:
        with eng.begin() as conn:
            names = list(
                conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'catalog_staging' AND tablename LIKE '%\\_stg'"
                    )
                ).scalars()
            )
            for name in names:
                conn.execute(text(f'DROP TABLE IF EXISTS catalog_staging."{name}"'))
    finally:
        eng.dispose()


def _activate(settings, family: str, name: str) -> None:
    eng = create_catalog_engine(
        settings.config_for(CatalogRole.INGEST), pool_size=1, max_overflow=0
    )
    try:
        with Session(eng) as session:
            release = resolve_release(session, family, name)
            assert release is not None
            activate_release(session, release)
            session.commit()
    finally:
        eng.dispose()


# -------------------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def bad_source(tmp_path_factory) -> Path:
    """A data root whose APASS files match the globs and parse to zero rows."""
    root = tmp_path_factory.mktemp("bad-catalog-data")
    dr6 = root / "APASS-DR6"
    dr6.mkdir()
    (dr6 / "zp00_6.sum").write_text("#  Name    RA(J2000)   raerr  DEC(J2000)\n")
    dr10 = root / "APASS-DR10"
    dr10.mkdir()
    (dr10 / "zp00.txt").write_text("APASS ID, RA (deg), Dec (deg)\n")
    return root


@pytest.fixture(scope="module")
def catalog(imported):
    """The session catalog, put back the way `imported` left it afterwards.

    `imported` is session-scoped and `test_integration` asserts DR10 ACTIVE /
    DR6 SUPERSEDED with both partitions attached. Module order puts this file
    first, so the restore is not optional. Requested explicitly rather than
    autouse, so the two database-free tests at the bottom of this module stay
    database-free.
    """
    yield imported
    import_release(imported, "apass", "dr6", replace=True, force=True, allow_warnings=True)
    import_release(imported, "apass", "dr10", replace=True, force=True, allow_warnings=True)
    # DR6 lands READY; activating it and then DR10 walks it back to SUPERSEDED
    # through the supported transition rather than an UPDATE.
    _activate(imported, "apass", "DR6")
    _activate(imported, "apass", "DR10")
    _drop_staging_tables(imported)


# ------------------------------------------------------------- F1 / F6 / F13


@pytest.mark.postgis
def test_failed_replace_of_the_active_release_keeps_its_own_provenance(
    catalog, bad_source
):
    """F1: a failed rebuild must not claim the source it failed to import.

    `--replace --force` of the ACTIVE release deliberately stays ACTIVE and
    keeps serving its old partition. The registry row must describe *that*
    partition — the old checksum, the old location, the old counts — because
    `guides/provenance.md` proves a release against exactly those columns.
    """
    before = _release_row(catalog, "apass", "DR10")
    assert before["state"] == "active"

    with pytest.raises(IngestionError, match="Production validation failed"):
        import_release(
            catalog,
            "apass",
            "dr10",
            replace=True,
            force=True,
            explicit_dir=str(bad_source / "APASS-DR10"),
        )

    after = _release_row(catalog, "apass", "DR10")
    assert after["state"] == "active"
    assert after["source_checksum"] == before["source_checksum"]
    assert after["source_location"] == before["source_location"]
    assert after["source_size_bytes"] == before["source_size_bytes"]
    assert after["production_table"] == before["production_table"]
    assert after["imported_row_count"] == before["imported_row_count"]
    # The old incoherence: import_started_at advanced past the completion it
    # claimed. Both timestamps now move together, in the finalize block.
    assert after["import_started_at"] == before["import_started_at"]
    assert after["import_completed_at"] == before["import_completed_at"]
    assert after["import_started_at"] <= after["import_completed_at"]

    # ...and the release is still serving.
    from skycat.query import cone_search

    assert cone_search(catalog, "apass", 100.0039, 4.861469, radius_deg=0.5)


@pytest.mark.postgis
def test_failed_replace_of_a_superseded_release_leaves_it_activatable(
    catalog, bad_source
):
    """F6: the rollback path survives an import that never touched the partition.

    A SUPERSEDED release is retained precisely so it can be activated again.
    The old runner demoted it to STAGING before any data work and the recorder
    then marked it FAILED, and neither `activate` nor `deactivate` could undo
    that — recovery was a hand-written UPDATE.
    """
    before = _release_row(catalog, "apass", "DR6")
    assert before["state"] == "superseded"

    with pytest.raises(IngestionError, match="Production validation failed"):
        import_release(
            catalog,
            "apass",
            "dr6",
            replace=True,
            force=True,
            explicit_dir=str(bad_source / "APASS-DR6"),
        )

    after = _release_row(catalog, "apass", "DR6")
    assert after["state"] == "superseded"
    assert after["source_checksum"] == before["source_checksum"]
    assert after["production_table"] == before["production_table"]

    # The partition is intact and the release still activates — the property the
    # state demotion destroyed.
    _activate(catalog, "apass", "DR6")
    assert _release_row(catalog, "apass", "DR6")["state"] == "active"
    assert _release_row(catalog, "apass", "DR10")["state"] == "superseded"
    _activate(catalog, "apass", "DR10")
    assert _release_row(catalog, "apass", "DR6")["state"] == "superseded"


@pytest.mark.postgis
def test_a_changed_source_is_not_skipped_after_a_failed_import(catalog, bad_source):
    """F1's second half: idempotency must key on a checksum that was catalog.

    The failed `--replace` above wrote the bad tree's checksum onto the release,
    so this plain re-import matched it, returned exit 0, and printed the word
    "catalog" for rows that had never been loaded. It must now run the import
    and fail the same way.
    """
    with pytest.raises(IngestionError, match="Production validation failed"):
        import_release(
            catalog, "apass", "dr6", explicit_dir=str(bad_source / "APASS-DR6")
        )

    # The good source is still what the release claims, so *it* still skips.
    report = import_release(catalog, "apass", "dr6")
    assert report.skipped_reason is not None
    assert report.state == "superseded"


@pytest.mark.postgis
def test_failure_detail_is_sql_null_until_something_fails(catalog, bad_source):
    """F13: `WHERE failure_detail IS NOT NULL` must mean what it says.

    SQLAlchemy's JSON types default to `none_as_null=False`, so assigning None
    stored the JSON scalar `null` — truthy to `IS NOT NULL`, and indistinguishable
    from a real failure without `jsonb_typeof`. Both spellings are asserted here
    because that is exactly what broke.
    """
    import_release(catalog, "apass", "dr6", replace=True, force=True, allow_warnings=True)
    clean = _release_row(catalog, "apass", "DR6")
    assert clean["has_failure"] is False
    assert clean["failure_type"] is None

    with pytest.raises(IngestionError, match="Production validation failed"):
        import_release(
            catalog,
            "apass",
            "dr6",
            replace=True,
            force=True,
            explicit_dir=str(bad_source / "APASS-DR6"),
        )

    failed = _release_row(catalog, "apass", "DR6")
    assert failed["has_failure"] is True
    assert failed["failure_type"] == "object"
    assert "Production validation failed" in failed["failure_detail_error"]


@pytest.mark.postgis
def test_every_failure_is_recorded_on_the_ingestion_run(catalog, bad_source):
    """F9's contract, asserted for both the preserved and the demoted release.

    The run row is per-attempt, so it is the one place a failure can always be
    recorded — including for an ACTIVE release, whose own state must not move.
    """
    for release_slug, release_name, subdir in (
        ("dr10", "DR10", "APASS-DR10"),
        ("dr6", "DR6", "APASS-DR6"),
    ):
        row = _release_row(catalog, "apass", release_name)
        with pytest.raises(IngestionError, match="Production validation failed"):
            import_release(
                catalog,
                "apass",
                release_slug,
                replace=True,
                force=True,
                explicit_dir=str(bad_source / subdir),
            )
        run = _latest_run(catalog, row["id"])
        assert run["status"] == "failed"
        assert run["finished_at"] is not None
        assert "Production validation failed" in (run["message"] or "")
        assert "traceback" in (run["detail"] or {})


@pytest.mark.postgis
def test_phase_b2_lock_timeout_fails_without_demoting_active_release(catalog, caplog):
    """F7: a queued swap must time out instead of wedging the family parent.

    The reader transaction holds ACCESS SHARE on the partition parent. Phase B2
    asks for ACCESS EXCLUSIVE, waits only for the configured lock timeout, rolls
    back, retries a bounded number of times, then fails with the old partition
    still attached and queryable.
    """
    import dataclasses

    from skycat.query import cone_search

    settings = dataclasses.replace(
        catalog,
        base=dataclasses.replace(catalog.base, lock_timeout_ms=100),
    )
    before = _release_row(settings, "apass", "DR10")
    assert before["state"] == "active"

    result: queue.Queue[BaseException | None] = queue.Queue(maxsize=1)
    reader = _reader_engine(settings)
    try:
        with reader.connect() as conn:
            txn = conn.begin()
            try:
                count = conn.execute(
                    text("SELECT count(*) FROM catalog_data.apass_source")
                ).scalar_one()
                assert count

                def run_import() -> None:
                    try:
                        import_release(settings, "apass", "dr10", replace=True, force=True)
                    except Exception as exc:
                        result.put(exc)
                    else:
                        result.put(None)

                with caplog.at_level(logging.WARNING, logger="skycat.ingestion"):
                    thread = threading.Thread(target=run_import, daemon=True)
                    thread.start()
                    exc = result.get(timeout=10)
                    thread.join(timeout=1)
                assert not thread.is_alive()
            finally:
                txn.rollback()
    finally:
        reader.dispose()

    assert isinstance(exc, IngestionError)
    assert "lock timeout" in str(exc).lower()
    lock_events = [
        rec for rec in caplog.records
        if getattr(rec, "skycat", {}).get("event") == "phase_b2.lock_wait"
    ]
    assert len(lock_events) == 3
    assert [rec.skycat["retry"] for rec in lock_events] == [True, True, False]
    assert {rec.skycat["lock_timeout_ms"] for rec in lock_events} == {100}

    after = _release_row(settings, "apass", "DR10")
    assert after["state"] == "active"
    assert after["source_checksum"] == before["source_checksum"]
    assert after["production_table"] == before["production_table"]
    assert cone_search(settings, "apass", 100.0039, 4.861469, radius_deg=0.5)


# ------------------------------------------------------- F9, without a database


def test_the_recorder_uses_its_own_independent_connection(monkeypatch, caplog):
    """F9: the recorder must not inherit the identity that just failed.

    Same host, same role, same pool and — critically — the same
    `statement_timeout` as the work that died. The review reproduced exactly
    that: a 1 ms timeout killed the loader *and* the UPDATE meant to record it.
    """
    from skycat.ingestion import runner

    seen: list = []

    def fake_engine(config, **kwargs):
        seen.append((config, kwargs))
        raise RuntimeError("engine construction failed")

    monkeypatch.setattr(runner, "create_catalog_engine", fake_engine)

    ingest_cfg = CatalogDatabaseConfig(
        host="127.0.0.1", port=5432, name="catalogs", user="catalog_ingest",
        password="pw", statement_timeout_ms=1, pool_pre_ping=False,
    )
    with caplog.at_level(logging.ERROR, logger="skycat.ingestion"):
        state = runner._record_failure(
            ingest_cfg,
            family_slug="apass",
            release_name="DR6",
            run_id=None,
            error="boom",
            detail={"error": "boom"},
        )

    assert state is None
    assert len(seen) == 1
    cfg, _ = seen[0]
    assert cfg.statement_timeout_ms is None
    assert cfg.pool_pre_ping is True
    assert cfg.user == "catalog_ingest"  # same role, new engine


def test_a_recorder_failure_is_logged_not_swallowed(caplog):
    """F9: the bare `pass` made the gap unobservable.

    `import.failed` had already been emitted with only `error=`, so a lost
    registry write looked identical to a recorded one. Keep the broad catch —
    recording must never mask the original error — but say so at ERROR.
    """
    from skycat.ingestion import runner

    dead = CatalogDatabaseConfig(
        host="127.0.0.1", port=1, name="catalogs", user="nobody", password="nobody"
    )
    with caplog.at_level(logging.ERROR, logger="skycat.ingestion"):
        state = runner._record_failure(
            dead,
            family_slug="apass",
            release_name="DR6",
            run_id=None,
            error="boom",
            detail={"error": "boom"},
        )

    assert state is None
    events = [
        rec for rec in caplog.records
        if getattr(rec, "skycat", {}).get("event") == "import.record_failed"
    ]
    assert events, "the recorder's own failure must be logged, not swallowed"
    assert events[0].levelno == logging.ERROR
    assert events[0].skycat["error"]

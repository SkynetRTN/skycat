# Changelog

Skycat uses GitHub Releases as the release-notes surface. This file records the
source-controlled summary that release notes should start from.

## Unreleased

Phases 1-3 of the August 2026 code review action plan
(`docs/working/code-review-2026-08.md`).

### Fixed

- A failed `--replace` no longer writes the new source's provenance onto the
  release row. Provenance is written after the partition swap succeeds, so the
  registry always describes the rows actually on disk and the idempotency check
  compares against a checksum that was successfully imported. Previously a
  failed replace left the release describing a source that was never loaded, and
  the next plain `import` skipped silently and reported success.
- A failed import no longer demotes a release whose partition it never touched.
  READY, ACTIVE and SUPERSEDED releases survive a failed `--replace` in the state
  they went in, restoring the documented rollback path for a SUPERSEDED release.
  In-flight imports are now tracked through the `ingestion_run` row rather than
  by moving the release to `staging`.
- The ingestion failure recorder now runs on an independent short-lived
  connection with no statement timeout, and logs an `import.record_failed` event
  at ERROR when it cannot record. Its query was previously ambiguous SQL that
  PostgreSQL rejected on every invocation while a bare `except` hid the error, so
  no import failure had ever been recorded.
- `catalog_release.failure_detail` stores SQL `NULL` instead of the JSON scalar
  `null`, so `WHERE failure_detail IS NOT NULL` no longer matches every release
  that has ever been imported.
- The CLI reports database, coordinate, unknown-family and malformed-input
  errors as messages instead of tracebacks. Exit codes are unchanged; set
  `SKYCAT_DEBUG=1` to get the original exception and its traceback back.
- `init`, `migrate`, `migrate-status`, the `skycat-migrate` Job and the
  `migrations_current` health check work with passwords containing `@`, `!`,
  `#`, `%`, spaces and other characters that URL-encoding escapes.
- `cone_search`, `lookup_native_id`, `cone_search_plan` and `batch_crossmatch`
  raise `CatalogQueryError` for invalid coordinates, negative limits and
  type-mismatched quality filters, as `api-stability.md` specifies, instead of
  leaking `ValueError` and `sqlalchemy.exc.*`.
- The APASS DR10 parser counts unparseable lines in `ParseStats` instead of
  silently skipping any line whose first token is not a digit.
- `alembic revision --autogenerate` no longer proposes dropping every release
  partition, staging table and PostGIS-owned schema. Reflection is limited to the
  three catalog schemas and the partition parents, and `CatalogBase.metadata` now
  declares the data-table indexes and the one-active-release partial unique index
  it was missing.
- `remove_release` reads the partition parent from `pg_inherits` instead of
  reconstructing it by string-splitting the partition name.

### Added

- `SKYCAT_DEBUG` — suppresses the CLI's friendly error messages so the original
  exception and its traceback reach the interpreter. Diagnosis only.
- `tests/test_import_failures.py` — imports that fail after the database has been
  touched, a region the suite did not previously exercise.
- `tests/test_schema_drift.py` — asserts `CatalogBase.metadata` matches a freshly
  migrated database, so model/migration drift fails CI.
- Coverage for `remove_release`'s destructive path, the CLI error contract, the
  Alembic URL builder, and query-layer input validation. The suite moves from 179
  to 247 tests.

## 0.1.5 - 2026-08-08

- Replaced rendered TestPyPI project-page scraping with JSON API, Simple API,
  artifact hash, metadata, and exact TestPyPI wheel install validation.
- Queued real PyPI publishing after the draft GitHub Release and TestPyPI
  validation succeed, while keeping the protected `pypi` environment as the
  manual approval gate.

## 0.1.4 - 2026-08-08

- Automated TestPyPI publishing and validation on release tag pushes.
- Added a TestPyPI install and rendered-page preflight before manual PyPI
  publishing.

## 0.1.3 - 2026-08-08

- Fixed PyPI/TestPyPI README documentation links by using absolute GitHub URLs.
- Updated package-index installation notes to install the latest published
  release without a version pin.

## 0.1.2 - 2026-08-07

- Fixed PyPI/TestPyPI README logo rendering by using an absolute hosted logo URL.
- Corrected PyPI author and maintainer metadata.
- Condensed the README into a shorter package landing page that points to the
  detailed docs.

## 0.1.1 - 2026-08-07

- Added a manual Trusted Publishing lane for TestPyPI/PyPI release uploads.
- Added Twine metadata checks to package build and release validation.

## 0.1.0 - 2026-08-07

- Prepared GitHub-hosted package release documentation and metadata.
- Declared GPLv3 package license metadata.
- Added CI coverage for package version drift.

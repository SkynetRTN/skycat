# Release process

Skycat's near-term package distribution channel is GitHub Releases. PyPI and
conda-forge are later channels after the package boundary, license, and release
process have been exercised with GitHub-hosted artifacts.

## Release contract

A GitHub-hosted Skycat package release ships:

- the Python package and console script;
- SQLAlchemy models and query APIs;
- parsers, ingestion, validation, and registry code;
- Alembic migration resources under `skycat/migrations`;
- source distribution and wheel artifacts.

It does not ship:

- PostgreSQL or PostGIS;
- a populated catalog database;
- APASS, VSX, Landolt, or Stetson source data;
- production roles, credentials, storage, backups, or hosted query endpoints.

## Versioning

Skycat has several related version identifiers:

- Python package version: `pyproject.toml` and `skycat.__version__`.
- Git tag: `v<python-package-version>`, for example `v0.1.0`.
- GitHub Release title: `Skycat v<python-package-version>`.
- Docker/container tag, if published later: `<python-package-version>` and
  `sha-<shortsha>`.
- Alembic revisions: schema migration graph under `skycat/migrations/versions`.
- `INTERNAL_SCHEMA_VERSION`: production row-shape semantics.
- `IMPORTER_VERSION`: parser/transform provenance and ingestion idempotency.

Package and runtime versions must match; `tests/test_version.py` enforces this.
For `0.x` releases, use semver-like version numbers but treat database and
importer compatibility as explicitly documented release notes rather than an
implied promise.

Bump `IMPORTER_VERSION` when parser or transform semantics change in a way that
should affect ingestion provenance or idempotency. Bump
`INTERNAL_SCHEMA_VERSION` when the production row semantics change in a way not
fully described by Alembic revision identity alone.

## GitHub Release workflow

Releases are draft-first. The workflow should:

1. Run from a `v*.*.*` tag.
2. Check that the tag matches the Python package version.
3. Build the wheel and source distribution once.
4. Install the wheel into a clean environment.
5. Install the source distribution into a clean environment.
6. Verify the installed CLI runs.
7. Verify packaged Alembic migrations resolve to one head.
8. Upload the tested files as workflow artifacts.
9. Attach those exact files to a draft GitHub Release.

Do not rebuild artifacts in the publish job.

## Install examples

Direct wheel install from a GitHub Release:

```bash
python -m pip install \
  https://github.com/SkynetRTN/skycat/releases/download/v0.1.0/skycat-0.1.0-py3-none-any.whl
```

Source install from a Git tag:

```bash
python -m pip install "git+https://github.com/SkynetRTN/skycat.git@v0.1.0"
```

Both forms install Skycat software only. They do not create or populate the
catalog database.

## Pre-release checklist

- `git status` is clean.
- `pyproject.toml` version and `skycat.__version__` match.
- Changelog has an entry for the release.
- CI is green on `dev` and on the release tag.
- The wheel installs cleanly in a fresh environment.
- The source distribution installs cleanly in a fresh environment.
- The installed `skycat` CLI runs without a repository checkout.
- Packaged Alembic migrations resolve to exactly one head.
- GitHub Release notes call out any migration, importer, or operational
  compatibility changes.
- The package-scope statement is still accurate.

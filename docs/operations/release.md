# Release process

Skycat's canonical release surface is GitHub Releases. PyPI and TestPyPI are
package-index publishing destinations that reuse the same built and tested
wheel and source distribution.

Skycat release artifacts are licensed as `GPL-3.0-only`; the root `LICENSE`
file and `pyproject.toml` license metadata must stay synchronized.

## Release contract

A Skycat package release ships:

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
4. Verify the package metadata and README with `twine check --strict`.
5. Install the wheel into a clean environment.
6. Install the source distribution into a clean environment.
7. Verify the installed CLI runs.
8. Verify packaged Alembic migrations resolve to one head.
9. Upload the tested files as workflow artifacts.
10. Attach those exact files to a draft GitHub Release.

Do not rebuild artifacts in the publish job.

## PyPI/TestPyPI workflow

The release workflow can also publish the tested `release-dists` artifact to
TestPyPI and PyPI through Trusted Publishing. Pushing a `v*.*.*` tag builds the
artifacts, creates the draft GitHub Release, publishes to TestPyPI, validates
TestPyPI metadata and artifact hashes through machine-readable APIs, installs
from TestPyPI, and then queues the real PyPI upload behind the protected `pypi`
environment.

Before the first package-index upload:

- Confirm the normalized `skycat` project name is still available on PyPI and
  TestPyPI.
- Configure pending Trusted Publishers on both indexes for
  `SkynetRTN/skycat`, workflow `.github/workflows/release.yml`, and GitHub
  environments `testpypi` and `pypi`.
- Create matching GitHub environments named `testpypi` and `pypi`; require
  reviewer approval on `pypi`. Leave `testpypi` without required reviewers if
  tag pushes should publish rehearsal uploads without a manual deployment
  approval.
- Keep `id-token: write` scoped to the package-index publish jobs only.

Normal publish sequence:

1. Merge the release branch through `dev` and protected `main`.
2. Tag the protected `main` commit, for example `v0.1.6`.
3. Approve the `github-release` environment deployment if it is protected.
4. Let the tag-push workflow publish to TestPyPI and validate the TestPyPI
   Simple API, JSON metadata, uploaded artifact hashes, README metadata source,
   exact TestPyPI wheel install, CLI entry point, and packaged migrations.
5. Approve the queued `pypi` environment deployment when ready. The PyPI job
   re-validates TestPyPI before uploading the same tested `release-dists`
   artifact.
6. Install from PyPI in a clean environment.

The automated TestPyPI install check gets the exact wheel URL from TestPyPI JSON
metadata and installs that TestPyPI-hosted file while resolving dependencies
from PyPI. For a manual name-resolution check, use the pinned release:

```bash
python -m venv /tmp/skycat-testpypi
/tmp/skycat-testpypi/bin/python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  skycat==0.1.6
/tmp/skycat-testpypi/bin/skycat --help
```

PyPI install check:

```bash
python -m venv /tmp/skycat-pypi
/tmp/skycat-pypi/bin/python -m pip install skycat==0.1.6
/tmp/skycat-pypi/bin/skycat --help
```

## Install examples

Direct wheel install from a GitHub Release:

```bash
python -m pip install \
  https://github.com/SkynetRTN/skycat/releases/download/v0.1.6/skycat-0.1.6-py3-none-any.whl
```

Source install from a Git tag:

```bash
python -m pip install "git+https://github.com/SkynetRTN/skycat.git@v0.1.6"
```

Both forms install Skycat software only. They do not create or populate the
catalog database.

After PyPI publishing is complete, the package-index install form is:

```bash
python -m pip install skycat
```

## Pre-release checklist

- `git status` is clean.
- `pyproject.toml` version and `skycat.__version__` match.
- Changelog has an entry for the release.
- CI is green on `dev` and on the release tag.
- The wheel installs cleanly in a fresh environment.
- The source distribution installs cleanly in a fresh environment.
- `twine check --strict dist/*` passes for the built distributions.
- The installed `skycat` CLI runs without a repository checkout.
- Packaged Alembic migrations resolve to exactly one head.
- GitHub Release notes call out any migration, importer, or operational
  compatibility changes.
- The package-scope statement is still accurate.
- For PyPI/TestPyPI publishing, Trusted Publishers and GitHub environments
  match the workflow file and environment names exactly.

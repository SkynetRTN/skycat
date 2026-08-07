---
status: open
reviewed: 2026-08-08
branch: feature/pypi-release-readiness
authority: code-inspection + official-docs
implementation: github-releases-complete; pypi-workflow-in-progress
---

# Skycat package publishing report

This report describes Skycat's package publishing framework now that the first
GitHub Release has been completed. The remaining publishing question is
PyPI/TestPyPI readiness: what must be configured so the same tested wheel and
source distribution can be uploaded to a Python package index without weakening
the GitHub Release process.

> **Status: open — this is the one package-publishing note with work still in
> it.** The GitHub Releases side is complete for the first public artifact
> host: `pyproject.toml` declares `GPL-3.0-only` with `license-files`,
> `release.yml` builds and verifies the wheel and sdist and publishes a draft
> through the protected `github-release` environment, the `Protect release tags`
> ruleset guards the tag namespace, `docs/operations/release.md` documents the
> process, and the README documents the supported GitHub Release install paths.
>
> **What is actually left:** the next package-index track is PyPI/TestPyPI:
> Trusted Publishing, GitHub environments, README rendering, first TestPyPI
> upload, TestPyPI install checks, first PyPI upload, and PyPI install checks.
> Live `pip index versions skycat` probes on 2026-08-08 found no matching
> distribution on PyPI or TestPyPI, but pending publishers do not reserve a
> project name before first upload.

The body below is a current framework report plus the implementation notes for
the `feature/pypi-release-readiness` branch.

## Executive summary

Skycat is packageable now under the GitHub Releases model, and the first GitHub
Release has been completed. The repository has a draft-first release workflow, a
protected `github-release` environment, package build and install checks, a
GPLv3 license declaration, release documentation, and a working public artifact
host. The next maturity step is the PyPI/TestPyPI publishing lane that reuses
the exact same tested artifacts.

The repository already has the core package shape:

- `pyproject.toml` uses PEP 621-style `[project]` metadata.
- `hatchling` is configured as the build backend.
- The import package is `skycat/`.
- The distribution name is currently `skycat`.
- A console script is configured: `skycat = "skycat.cli.main:main_entry"`.
- Runtime dependencies are declared.
- The README is a concise package landing page with links into detailed CLI,
  API, database, release, and operations docs.
- CI now runs ruff, pyright, pytest, a real PostgreSQL/PostGIS service,
  package-build smoke tests, Python-version matrix tests, migration graph
  checks, workflow safety, dependency review, and advisory supply-chain scans.

The GitHub-hosted release path is complete for the first public release:

- GPLv3 license metadata is declared and the root `LICENSE` is included.
- The protected `github-release` environment exists with required reviewer
  approval.
- `release.yml` verifies tag/version alignment, builds wheel and sdist,
  checks metadata with Twine, inspects archive contents, smoke-tests both
  install paths, and publishes a draft GitHub Release from the tested artifacts.
- PyPI/TestPyPI Trusted Publishing setup, first index uploads, README rendering,
  and install checks are now the remaining package-index blockers.

Recommended path:

1. Keep GitHub Releases as the canonical release-note, tag, and artifact
   surface.
2. Merge the PyPI/TestPyPI workflow lane through `dev` and protected `main`.
3. Configure the pending PyPI/TestPyPI publishers immediately before first
   upload.
4. Configure PyPI/TestPyPI Trusted Publishing with GitHub Actions OIDC instead
   of long-lived API tokens.
5. Reuse the exact `release-dists` artifact built by `release-build`; do not
   rebuild for PyPI.
6. Use TestPyPI first, then real PyPI after the package name, long description,
   trusted publisher, and install smoke tests pass.
7. Add a GitHub Pages simple package index only if consumers need
   `pip --extra-index-url` behavior before PyPI.
8. Use GitHub Packages only for container images, not Python wheels, because
   GitHub Packages does not provide a native PyPI registry.

## Scope fit assessment

Skycat fits a Python package if the package is framed as installable catalog
infrastructure: a CLI, Python query client, parser set, ingestion runner,
validation layer, and migration bundle for a PostgreSQL/PostGIS catalog
database that operators provision separately.

Skycat does not fit a Python package if the package is expected to be the whole
catalog query service. A wheel cannot reasonably include the database server,
PostGIS extension, roles, partitions, indexes, APASS/VSX/Landolt/Stetson source
data, imported catalog rows, or production operating model. Those are service
and data assets, not Python package assets.

The right public positioning is therefore: installing Skycat from a GitHub
Release wheel installs the Skycat control/query software. It does not install a
ready-made catalog database or a hosted query endpoint. If the same artifact is
later published on PyPI, `pip install skycat` should carry the same boundary.

### Benefits of publishing Skycat as a Python package

- **Natural fit for the code boundary.** The repository already owns Python
  modules, a console script, SQLAlchemy models, parsers, validators, query
  functions, and Alembic migrations. Those are normal Python distribution
  contents.
- **Easier adoption by Python applications.** Downstream services can depend on
  `skycat` in `pyproject.toml` instead of vendoring this repository or invoking
  a checkout-specific CLI.
- **Versioned client and operator tooling.** A package version can identify the
  exact query API, CLI behavior, parser behavior, and migration bundle used by a
  worker or ingestion job.
- **Cleaner Docker and Kubernetes builds.** Images and Jobs can install a
  published wheel instead of copying the whole source tree. That makes image
  provenance and cache behavior easier to reason about.
- **Standard Python install path.** `pip`, `uv`, and build systems already know
  how to install wheels, resolve dependencies, and pin versions.
- **Better reuse of read-only query APIs.** Applications that only need
  `CatalogReader`, cone search, lookup, or crossmatch can consume the package
  without also adopting the repository's Docker/infra layout.
- **Release artifacts are inspectable.** Wheels and sdists can be checked for
  exactly which migrations, parsers, and package files ship in a release.
- **Conda-forge becomes possible later.** A clean source release with complete
  metadata is a good starting point for scientific users who prefer conda
  environments.

### Drawbacks of publishing Skycat as a Python package

- **It can create the wrong expectation.** Users may assume installing `skycat`
  gives them a working catalog service, but Skycat still needs PostgreSQL,
  PostGIS, roles, schemas, source files, imports, validation, and activation.
- **The important runtime state is not in the wheel.** The most valuable output
  is an initialized and populated catalog database. Python packaging does not
  distribute that state.
- **Operational dependencies remain external.** Database provisioning, storage,
  backups, vacuum/analyze behavior, credentials, networking, and Kubernetes Jobs
  are outside Python packaging's scope.
- **Versioning gets more complex.** Package versions, Alembic revisions,
  internal schema versions, importer versions, Docker tags, and catalog data
  releases must be documented as related but distinct.
- **Migration safety becomes a public contract.** Once published, users may run
  older packages against newer databases or newer packages against older
  databases. The package needs compatibility checks and clear failure modes.
- **Support burden increases.** Public package users will install it in
  environments the maintainers do not control: different OSes, Python versions,
  PostgreSQL versions, PostGIS versions, and dependency resolver states.
- **Large-data workflows can look deceptively simple.** A package install is
  small, but APASS-scale imports are operationally heavy and should not be
  presented like a typical pure-Python library setup.
- **Security and release discipline become mandatory.** GitHub-hosted artifacts
  still require account security, release immutability, artifact checks, and
  careful workflow review.

### Scope recommendation

Publishing a Python package fits Skycat's scope if the release contract is
explicit:

- The package ships code, CLI entry points, migrations, parser/validation logic,
  and Python query APIs.
- The package does not ship catalog datasets, imported rows, a database server,
  a web API, or production credentials.
- The README, GitHub Release notes, and any future PyPI description say
  PostgreSQL/PostGIS and catalog source data are required external runtime
  dependencies.
- Docker and Kubernetes assets remain deployment examples or separately
  published container artifacts, not part of the Python package contract.
- A hosted/shared catalog query service, if desired, should be treated as a
  separate product surface that depends on the `skycat` package.

Under that framing, a GitHub-hosted wheel is already a good fit for Skycat's
software component. PyPI can be added as a standard Python index surface, but it
must not change the package boundary: `pip install skycat` still installs the
software, not a populated catalog query service.

## Current repository state

### Existing package metadata

`pyproject.toml` currently declares:

- `name = "skycat"`
- `version = "0.1.4"`
- `requires-python = ">=3.11, <3.14"`
- `readme = "README.md"`
- `authors = [{ name = "James Atkisson", email = "james@atkisson.net" }]`
- `maintainers = [{ name = "SkynetRTN" }]`
- `project.urls.Organization = "https://skynetgo.org"`
- runtime dependencies:
  - `sqlalchemy ~= 2.0.38`
  - `geoalchemy2 ~= 0.20.0`
  - `psycopg[binary] ~= 3.2.4`
  - `alembic ~= 1.14`
  - `click ~= 8.1`
- optional `dev` dependencies:
  - `pytest`
  - `ruff`
  - `pyright`
- console script:
  - `skycat = "skycat.cli.main:main_entry"`
- build backend:
  - `requires = ["hatchling >= 1.26"]`
  - `build-backend = "hatchling.build"`
- wheel target:
  - `packages = ["skycat"]`

`skycat/__init__.py` also declares `__version__ = "0.1.4"`.

### Existing runtime packaging concerns

Skycat is not only a library. An installed package must support:

- `import skycat`
- `python -m skycat`
- the `skycat` CLI entry point
- Alembic migrations from inside an installed wheel
- database initialization, migration, health, query, ingestion, and validation
- operation without bundled catalog data

The catalog source datasets should not ship in the package. The repository
already keeps large source data out of tree and ignores common data artifacts.
That is correct for GitHub-hosted wheels, PyPI, and conda-forge alike: ship the
code, migrations, parsers, and docs; keep large APASS/VSX/Landolt/Stetson data
external under `SKYCAT_DATA_ROOT`.

### Existing CI and release baseline

The pushed workflow update added a much stronger package and supply-chain
baseline. The repository now has eight workflows under `.github/workflows/`:

- `ci.yml`
- `workflow-safety.yml`
- `dependency-review.yml`
- `containers.yml`
- `kubernetes.yml`
- `codeql.yml`
- `secret-scan.yml`
- `release.yml`

`ci.yml` now contains:

- `skycat: ruff, pyright, pytest` — the deep PostgreSQL/PostGIS integration
  gate;
- `unit: python 3.11`, `unit: python 3.12`, and `unit: python 3.13` — unit
  coverage for the declared Python support range;
- `migration graph` — a database-free Alembic revision graph check;
- `package build` — builds the sdist and wheel, installs the wheel into a clean
  environment, runs CLI smoke tests, and verifies packaged migrations resolve;
- `required: ci` — aggregate status for branch protection.

The non-CI workflows add:

- `required: workflow safety` from actionlint and zizmor;
- `dependency review` with high/critical dependency blocking;
- advisory container build, Compose config, and container scan jobs;
- advisory Kubernetes manifest validation;
- advisory CodeQL Python scanning;
- secret scanning with gitleaks over the working tree and history.

This changes the package-readiness assessment materially: the repository now
does prove that an isolated wheel can be built, installed, invoked, and used to
locate packaged migrations. The release workflow now preserves those tested
artifacts and publishes them to a draft GitHub Release.

### Release readiness already demonstrated

The GitHub Release dry run demonstrated the release path end-to-end through a
temporary `v0.1.0` tag and draft release, and the first official GitHub Release
has now been completed.

The current local and CI-equivalent checks have also shown:

- `uv build` can build both `skycat-0.1.0.tar.gz` and
  `skycat-0.1.0-py3-none-any.whl`;
- the wheel and sdist install in clean Python 3.12 environments;
- installed `skycat --help` and `skycat config` run outside a repository
  checkout;
- packaged Alembic migrations resolve to one head;
- ruff, pyright, unit tests, package-build checks, workflow safety, and
  dependency review are green on the merged release work.

The remaining work is package-index publishing, not GitHub Release mechanics.

## Metadata requirements for GitHub Releases and PyPI

### 1. Distribution name

The current distribution name is `skycat`.

The name is syntactically valid for Python packaging. Package-name
normalization means names such as `skycat`, `sky-cat`, and `sky_cat` would
collide after normalization if any equivalent form is already taken.

For GitHub Releases, this name controls wheel/sdist filenames and installed
package metadata. The GitHub release path is already using it successfully.

For PyPI, `skycat` must still be treated as unreserved until a real upload
creates the project. PyPI supports pending Trusted Publishers for new projects,
but a pending publisher does not reserve the project name before first publish.
If another user registers `skycat` first, the pending publisher would no longer
be useful for that name.

Required actions:

- Confirm that `skycat` is the intended public package name.
- Keep using `skycat` consistently in GitHub Release artifact names and install
  examples.
- Check PyPI and TestPyPI immediately before configuring or using pending
  publishers.
- Configure the pending publisher as close to the first TestPyPI/PyPI upload as
  possible; do not treat it as name reservation.
- If the PyPI name is unavailable later, choose a normalized-distinct
  distribution name such as `skycat-db`, `skycat-catalogs`, or an
  organization-prefixed name.
- Keep the import package as `skycat` unless there is a real collision in the
  Python environment.

### 2. License

The repository now has a root `LICENSE` file using the GNU General Public
License version 3, and `pyproject.toml` declares `license = "GPL-3.0-only"` plus
`license-files = ["LICENSE"]`.

That clears the GitHub-hosted release blocker. For PyPI and any other package
index, keep the SPDX expression, classifier, and license file synchronized.

Required actions:

- Keep the root `LICENSE` file.
- Keep license metadata in `pyproject.toml`:

```toml
[project]
license = "GPL-3.0-only"
license-files = ["LICENSE"]
```

- Verify that dependency licenses are acceptable for the intended use.
- Decide whether catalog source formats, documentation snippets, or bundled
  fixtures require notices separate from the code license.

Conda-forge specifically expects accurate license metadata and license files, so
keep this metadata in place before any conda-forge packaging.

### 3. Project metadata

The current metadata is sufficient for the GitHub-hosted release and close to
PyPI-ready:

```toml
[project]
authors = [
    { name = "James Atkisson", email = "james@atkisson.net" },
]
maintainers = [
    { name = "SkynetRTN" },
]
keywords = ["astronomy", "catalogs", "postgis", "postgresql", "photometry"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Science/Research",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS :: MacOS X",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Database",
    "Topic :: Scientific/Engineering :: Astronomy",
]

[project.urls]
Homepage = "https://github.com/SkynetRTN/skycat"
Repository = "https://github.com/SkynetRTN/skycat"
Issues = "https://github.com/SkynetRTN/skycat/issues"
Documentation = "https://github.com/SkynetRTN/skycat#readme"
```

Use `Typing :: Typed` only if the package commits to PEP 561 behavior and ships
a `py.typed` marker. Right now there is no `py.typed`, so the classifier is
correctly omitted.

Remaining PyPI-facing checks:

- Run `twine check --strict dist/*` against the current README and metadata before any
  TestPyPI upload.
- Review how the full-width README logo and repo-relative documentation links
  render outside GitHub.
- Keep `Development Status :: 3 - Alpha` unless the first public release is
  meant to promise a broader stability level.

### 4. Python version support

`pyproject.toml` declares `>=3.11, <3.14`.

CI now covers the declared support range at the unit level and runs the deep
PostgreSQL/PostGIS gate on Python 3.12. That is enough evidence for the first
GitHub-hosted release and a reasonable baseline for PyPI.

Required actions before PyPI publishing:

- Keep unit tests green on Python 3.11, 3.12, and 3.13.
- Keep the full PostGIS integration suite on at least one supported Python
  version.
- Decide what happens when Python 3.14 is released: either test and expand the
  range, or keep `<3.14`.
- Ensure dependencies have compatible releases across the declared range.

### 5. Versioning policy

There are several version concepts in the repository:

- Python package version: `pyproject.toml`, currently `0.1.4`.
- Runtime `skycat.__version__`, currently `0.1.4`.
- Internal database schema version: `INTERNAL_SCHEMA_VERSION = 1`.
- Importer semantic version: `IMPORTER_VERSION = "1.0.0"`.
- Alembic migration revisions under `skycat/migrations/versions/`.
- Docker image tags, currently not formalized here.
- Catalog-family release versions such as APASS DR6/DR10 and Landolt 1992/2009.

Current status and remaining actions:

- `docs/operations/release.md` documents package version, release tag, schema
  version, importer version, Alembic revisions, and Docker tag policy.
- `tests/test_version.py` checks that `skycat.__version__` matches
  `pyproject.toml`.
- Decide whether `IMPORTER_VERSION` should track the Python package version or
  remain an independent data-provenance version.
- Define when `INTERNAL_SCHEMA_VERSION` changes relative to Alembic migrations.
- Define Docker tag rules, for example `ghcr.io/skynetrtn/skycat:<python-package-version>`.
- Use immutable release tags such as `v0.1.4`.

Practical recommendation:

- For `0.x`, keep semver-like package versions but document that migration and
  importer compatibility may still change.
- Bump `IMPORTER_VERSION` only when parser/transform semantics change in a way
  that should invalidate idempotency/provenance.
- Bump `INTERNAL_SCHEMA_VERSION` when production row semantics change in a way
  not fully represented by Alembic alone.
- Tag Docker images with the same Python package version plus `sha-<shortsha>`.

### 6. Build backend and build reproducibility

Hatchling is a good fit for this package. The current build-system declaration
already carries the lower bound needed for modern license metadata:

```toml
[build-system]
requires = ["hatchling >= 1.26"]
build-backend = "hatchling.build"
```

GitHub Releases use `uv build` in CI and in the release workflow:

```bash
uv build
```

For PyPI readiness, also keep the standards-based check visible:

```bash
python -m pip install --upgrade build twine
uv build
uv run --frozen --extra dev twine check --strict dist/*
```

The important rule for both GitHub Releases and PyPI is artifact identity:
build the distributions once in `release-build`, test those files, then publish
those same files to every destination. Do not rebuild separately for PyPI.

### 7. Wheel and sdist contents

The wheel must include:

- all importable modules under `skycat/`;
- `skycat/migrations/env.py`;
- `skycat/migrations/script.py.mako`;
- `skycat/migrations/versions/*.py`;
- package metadata;
- console script entry point metadata.

The sdist should include:

- source code;
- `pyproject.toml`;
- `README.md`;
- `LICENSE`;
- `alembic.ini`;
- tests and sample fixtures if useful for downstream validation;
- docs if useful for downstream packagers.

The wheel should not include:

- `.venv/`;
- `__pycache__/`;
- `.pytest_cache/`, `.ruff_cache/`;
- catalog source datasets;
- local staging/work/checkpoint artifacts;
- Docker/Kubernetes manifests unless intentionally shipped as package data.

The current `[tool.hatch.build.targets.wheel] packages = ["skycat"]` likely
keeps the wheel focused on the package directory. The new `package build` CI job
now verifies that a built wheel installs cleanly, exposes the CLI, and can
resolve packaged Alembic migrations. For release work, keep an explicit archive
inspection check too, because it makes missing non-Python files such as
`script.py.mako` obvious.

Existing CI covers the install smoke test; this is the stronger local/release
artifact check to keep available:

```bash
uv build
uv run --frozen --extra dev twine check --strict dist/*
python -m venv /tmp/skycat-wheel-venv
/tmp/skycat-wheel-venv/bin/python -m pip install dist/skycat-*.whl
/tmp/skycat-wheel-venv/bin/python -c "import skycat; print(skycat.__version__)"
/tmp/skycat-wheel-venv/bin/skycat --help
/tmp/skycat-wheel-venv/bin/skycat config
python - <<'PY'
import zipfile
from pathlib import Path

wheel = next(Path("dist").glob("skycat-*.whl"))
names = set(zipfile.ZipFile(wheel).namelist())
required = {
    "skycat/migrations/env.py",
    "skycat/migrations/script.py.mako",
}
missing = sorted(required - names)
missing += [] if any(n.startswith("skycat/migrations/versions/") for n in names) else [
    "skycat/migrations/versions/*.py"
]
if missing:
    raise SystemExit(f"missing wheel files: {missing}")
PY
```

Also keep an sdist install check in the release workflow:

```bash
python -m venv /tmp/skycat-sdist-venv
/tmp/skycat-sdist-venv/bin/python -m pip install dist/skycat-*.tar.gz
/tmp/skycat-sdist-venv/bin/skycat --help
```

### 8. README / package landing page

`README.md` is now doing the right job for GitHub and PyPI: it opens with the
Skycat brand, explains the package/CLI purpose, states the package boundary,
keeps install paths near the top, and links into detailed docs instead of
duplicating them. For GitHub-hosted packages, the README and GitHub Release
notes are the package landing pages.

For PyPI, the same README becomes the long description. That adds a stricter
rendering requirement: PyPI may not display repo-relative images and links the
same way GitHub does. The README now uses an absolute GitHub-hosted logo URL so
the package index renderer can fetch the image outside the repository context.

Current GitHub-facing status:

- The README includes the primary Skycat logo.
- The opening explains Skycat as a standalone Python package and CLI.
- The package boundary is explicit: Skycat installs software, not a populated
  catalog database or hosted query endpoint.
- GitHub-hosted wheel and source-tag install examples are prominent near the
  top:

```bash
python -m pip install \
  https://github.com/SkynetRTN/skycat/releases/download/v0.1.4/skycat-0.1.4-py3-none-any.whl
```

```bash
python -m pip install "git+https://github.com/SkynetRTN/skycat.git@v0.1.4"
```

Remaining PyPI-facing actions:

- Run `twine check --strict dist/*`; PyPA's guide identifies this as the
  pre-upload check for README rendering problems.
- Upload to TestPyPI first and validate machine-readable metadata, artifact
  hashes, and clean install behavior from GitHub Actions.
- Visually inspect the rendered project page manually when a human page review
  is needed.
- If repo-relative documentation links break on PyPI, replace the critical
  ones with absolute GitHub URLs or keep PyPI's landing page shorter and direct
  users to GitHub for the full docs.
- Keep the non-Python runtime requirements near the top:
  PostgreSQL/PostGIS, external catalog source files under `SKYCAT_DATA_ROOT`,
  and no bundled catalog datasets.

### 9. Dependencies and optional extras

The current runtime dependency set is small and direct. The main packaging
decision is `psycopg[binary]`.

Keeping `psycopg[binary]` as a required dependency improves first-install
success from PyPI. The tradeoff is that some production teams and downstream
packagers prefer a system-linked driver stack.

Options:

- Keep `psycopg[binary]` as the default for now. This is simplest.
- Change runtime dependency to `psycopg ~= 3.2.4` and add an extra:

```toml
[project.optional-dependencies]
binary = ["psycopg[binary] ~= 3.2.4"]
```

- Split database-adapter choices later if real users need it.

Recommendation for the first GitHub-hosted package release: keep the current
dependency unless a deployment target specifically forbids binary wheels.
Revisit for PyPI, conda-forge, or Linux distribution packaging if users need a
non-binary PostgreSQL driver stack.

### 10. Typing support

Pyright is already used internally, but public typing support is not declared.

Required decision:

- If Skycat wants downstream users to rely on inline type hints, add
  `skycat/py.typed`, ensure it is included in wheels, and consider adding
  `Typing :: Typed`.
- If not, omit the typing classifier for now.

### 11. Security and release integrity

Already in place for GitHub Releases:

- Protect GitHub accounts with two-factor authentication.
- Protect release creation through `github-release` environment approval.
- Require Code Owner review for changes to `.github/workflows/release.yml`.
- Use least-privilege job permissions.
- Build artifacts once, then publish those exact artifacts.
- Store release provenance in GitHub Releases.

Remaining for PyPI:

- Create PyPI and TestPyPI maintainer/project ownership.
- Configure Trusted Publishing rather than long-lived API tokens.
- Use job-level `id-token: write` only on PyPI/TestPyPI publishing jobs.
- Use dedicated GitHub environments such as `testpypi` and `pypi` with reviewer
  approval.
- Review PyPI Trusted Publishers during maintainer offboarding, because
  publishers are project-level trust relationships.

## Release framework: GitHub Releases plus PyPI

### Current GitHub Release path

The implemented release workflow has four jobs:

- `release-build`
- `github-release`
- `testpypi-publish`
- `pypi-publish`

`release-build` checks out the exact version tag, installs `uv`, verifies the
tag matches `pyproject.toml`, builds sdist and wheel, checks package metadata
with Twine, inspects archive contents, installs both distributions in clean
environments, verifies the CLI, and checks that packaged Alembic migrations
resolve.

`github-release` depends on `release-build`, runs in the protected
`github-release` environment, downloads the `release-dists` artifact, and
publishes a draft GitHub Release with the tested wheel and sdist.

GitHub Releases remain the canonical release-note and artifact surface. GitHub's
own release model is tag-based: a release is attached to a Git tag that marks a
specific point in repository history. That matches Skycat's versioning rule:
future release tags should be cut from protected `main` after `dev` is merged.

For future GitHub Releases:

1. Confirm `dev` is ready.
2. Merge `dev` into protected `main`.
3. Confirm required `main` checks are green.
4. Tag the protected `main` commit, for example `v0.1.4`.
5. Let `release.yml` create the draft GitHub Release.
6. Approve the `github-release` deployment.
7. Install from the real GitHub Release wheel asset URL:

```bash
python -m venv /tmp/skycat-github-release
/tmp/skycat-github-release/bin/python -m pip install \
  https://github.com/SkynetRTN/skycat/releases/download/v0.1.4/skycat-0.1.4-py3-none-any.whl
/tmp/skycat-github-release/bin/skycat --help
```

### PyPI/TestPyPI additions

PyPI should be added as a publishing job, not as a replacement for GitHub
Releases. The PyPI job should download the same `release-dists` artifact and
upload it using Trusted Publishing through `pypa/gh-action-pypi-publish`.

Implemented structure on `feature/pypi-release-readiness`:

- Keep `release-build` as the single build/test source.
- Keep `github-release` as the draft GitHub Release publisher.
- Add `testpypi-publish`, gated by a `testpypi` environment.
- Add `testpypi-validation` for Simple API, JSON metadata, artifact hashes,
  README metadata source, install, CLI, and packaged-migration checks.
- Add `pypi-publish`, gated by a stricter `pypi` environment after the draft
  GitHub Release and TestPyPI validation have succeeded.
- Give PyPI jobs `id-token: write` at the job level only.
- Do not give the build or GitHub Release job OIDC publishing permission.
- Use TestPyPI before real PyPI for the first upload.

Implemented job shape:

```yaml
jobs:
  testpypi-publish:
    name: upload distributions to TestPyPI
    runs-on: ubuntu-latest
    needs: release-build
    environment: testpypi
    permissions:
      id-token: write
    steps:
      - name: Download distributions
        uses: actions/download-artifact@v8
        with:
          name: release-dists
          path: dist/

      - name: Publish to TestPyPI
        uses: pypa/gh-action-pypi-publish@v1.14.1
        with:
          repository-url: https://test.pypi.org/legacy/
```

The real PyPI job is the same shape without `repository-url`, using the `pypi`
environment and the PyPI trusted publisher configuration for `SkynetRTN/skycat`,
`.github/workflows/release.yml`, and environment `pypi`. On a tag-push release,
that job is queued automatically after the draft GitHub Release and TestPyPI
validation jobs succeed; the protected environment approval remains the manual
gate before the real PyPI upload.

## GitHub hosting options

### Option A: GitHub Releases

GitHub Releases are the implemented primary host for Skycat wheel and sdist
files.

What it provides:

- public download page;
- release notes;
- tag association;
- downloadable artifacts;
- a good audit trail beside the source code.

What it does not provide:

- a normal package index for dependency resolution;
- the default `pip install skycat` experience;
- dependency metadata discovery equivalent to PyPI's simple API.

Users could install from a direct URL, for example:

```bash
python -m pip install \
  https://github.com/SkynetRTN/skycat/releases/download/v0.1.4/skycat-0.1.4-py3-none-any.whl
```

Recommendation: keep GitHub Releases as the canonical release surface even
after PyPI is added. It is simple, keeps artifacts next to source tags and
release notes, and provides the source-of-truth release page that PyPI can link
back to.

### Option A2: PyPI and TestPyPI

PyPI is the next package-index target now that the first GitHub Release path is
stable.

What it provides:

- the default `pip install skycat` experience;
- standard dependency metadata discovery through Python package indexes;
- higher discoverability for Python users;
- compatibility with ordinary Python dependency managers and lockfile tools.

What it requires:

- package name availability on PyPI and TestPyPI;
- PyPI/TestPyPI account or organization ownership;
- pending or existing Trusted Publisher configuration;
- GitHub environments such as `testpypi` and `pypi`;
- a publishing job that downloads the tested `release-dists` artifact;
- job-level `id-token: write` for Trusted Publishing;
- `twine check --strict dist/*`;
- TestPyPI upload and install smoke test before real PyPI.

Recommendation: add PyPI as a second publish destination after GitHub Releases,
not as a replacement. The artifact sequence should be:

1. Build and test once in `release-build`.
2. Publish a draft GitHub Release from the tested artifacts.
3. Upload the same artifacts to TestPyPI.
4. Validate TestPyPI's Simple API, JSON metadata, uploaded artifact hashes,
   README metadata source, exact TestPyPI wheel install, CLI entry point, and
   packaged migrations.
5. Queue the real PyPI upload behind the protected `pypi` environment.
6. Approve the `pypi` deployment when ready.
7. Upload the same artifacts to PyPI.
8. Install from PyPI in a clean environment.

Example TestPyPI install check:

```bash
python -m venv /tmp/skycat-testpypi
/tmp/skycat-testpypi/bin/python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  skycat==0.1.4
/tmp/skycat-testpypi/bin/skycat --help
```

Example PyPI install check:

```bash
python -m venv /tmp/skycat-pypi
/tmp/skycat-pypi/bin/python -m pip install skycat==0.1.4
/tmp/skycat-pypi/bin/skycat --help
```

### Option B: GitHub Packages

GitHub Packages is not a useful primary target for Skycat's Python package.

As of the official GitHub documentation reviewed on 2026-08-06, supported
GitHub Packages registries cover npm, RubyGems, Maven, Gradle, NuGet, Docker,
and containers. Python/PyPI is not listed as a supported registry.

Recommendation:

- Do not plan on publishing Python wheels to GitHub Packages.
- Do consider publishing Docker/OCI images to GitHub Container Registry if the
  project wants a container-first installation path:

```text
ghcr.io/skynetrtn/skycat:<version>
ghcr.io/skynetrtn/skycat:sha-<shortsha>
```

This is especially relevant because Skycat has operational dependencies
PostgreSQL/PostGIS users may prefer to run through Compose or Kubernetes jobs.

### Option C: GitHub Pages simple Python package index

GitHub Pages could host a static Python "simple repository" index.

What it provides:

- a GitHub-hosted package site;
- `pip install --index-url` or `--extra-index-url` support if the static index
  follows the simple repository API;
- full control over what artifacts are visible.

What it requires:

- a generated `simple/` index structure;
- static pages for project and file links;
- HTTPS through GitHub Pages or a custom domain;
- release automation to build distributions, generate/update index HTML or JSON,
  and deploy Pages;
- user documentation that clearly tells people how to configure `pip`.

Example user install:

```bash
python -m pip install \
  --extra-index-url https://skynetrtn.github.io/skycat/simple/ \
  skycat
```

Downsides:

- less discoverable than PyPI;
- more custom infrastructure to maintain;
- dependency-confusion risk if users combine private/public indexes
  carelessly;
- release automation must update the index every time a wheel/sdist changes.

Recommendation: do not add GitHub Pages for the first GitHub-hosted release.
Start with GitHub Release assets and direct install URLs. Add a Pages-hosted
simple index only if consumers need ordinary resolver behavior through
`pip --extra-index-url` before PyPI.

### Option D: conda-forge / Anaconda.org

Conda-forge is a credible package channel for scientific users, but it is
usually downstream of a source release.

What it provides:

- install path for conda users:

```bash
conda install -c conda-forge skycat
```

- community-maintained feedstock on GitHub;
- builds uploaded to Anaconda.org's `conda-forge` channel;
- better alignment with scientific Python environments.

What it requires:

- public source archive, from PyPI or GitHub Releases;
- license file and accurate license metadata;
- conda recipe;
- runtime requirements mapped to conda packages;
- tests that can run during feedstock build;
- maintainers willing to keep the feedstock current.

Potential friction:

- `psycopg[binary]` may need to become a non-binary conda dependency.
- PostGIS is an external service, not a simple Python dependency. Feedstock
  tests should avoid requiring a live PostGIS server unless it can be provided
  cheaply in CI.
- CLI smoke tests are easy; full integration tests are probably inappropriate
  for a first feedstock recipe.

Recommendation: defer conda-forge. A GitHub Release source archive may be enough
to start a feedstock, but conda-forge is easier after the license, metadata, and
source artifacts have been exercised through at least one GitHub-hosted package
release.

### Option E: private package indexes

If Skycat is not meant to be public yet, alternatives include:

- an internal PyPI-compatible repository such as devpi, Artifactory, Nexus, or
  cloud provider artifact registries;
- direct wheel download from GitHub Releases;
- GitHub Pages simple index for limited distribution;
- container-only distribution through GHCR.

The package-hardening work is mostly the same either way: license, metadata,
wheel/sdist contents, artifact tests, versioning, and release automation.

## Required work checklist

### GitHub Releases: implemented in `dev`

- GPLv3 `LICENSE` file and `pyproject.toml` license metadata.
- Protected `github-release` environment with required reviewer approval.
- Complete `[project.urls]`.
- Non-license classifiers and keywords.
- Versioning and GitHub Release naming policy in `docs/operations/release.md`.
- Version drift test for `pyproject.toml` and `skycat.__version__`.
- GitHub-hosted installation and package-scope sections in `README.md`.
- Changelog/release-notes policy through `CHANGELOG.md` and GitHub Releases.
- Draft-first GitHub Release workflow in `.github/workflows/release.yml`.
- Security reporting policy in `SECURITY.md`.
- Contributing guidance in `CONTRIBUTING.md`.
- Package-build/install CI through `package build`.
- Python 3.11/3.12/3.13 unit-test matrix.
- Alembic migration graph validation.
- Stable aggregate `required: ci`.
- Workflow syntax and security checks with `required: workflow safety`.
- Dependency review.
- Advisory container build, Compose config, container scan, Kubernetes manifest
  validation, CodeQL, and secret scanning.
- First official GitHub Release completed.
- Brand-ready README introduction and primary logo placement.

### PyPI/TestPyPI implemented in this feature branch

- `twine >= 6.0` is part of the `dev` extra and lockfile.
- CI package-build runs `twine check --strict dist/*`.
- Release build runs `twine check --strict dist/*`.
- `release.yml` publishes to TestPyPI automatically on release tags, validates
  TestPyPI through machine-readable APIs and install checks, and queues the
  real PyPI upload behind the protected `pypi` environment.
- Only the PyPI/TestPyPI jobs receive job-level `id-token: write`.
- Release and CI docs explain the required Trusted Publisher and GitHub
  environment setup.

### Blocking before PyPI/TestPyPI upload

- Reconfirm package name availability on PyPI and TestPyPI immediately before
  first upload.
- Decide whether to use a personal PyPI account or PyPI organization/project
  ownership for Skycat.
- Configure pending Trusted Publishers for TestPyPI and PyPI as close as
  possible to first upload.
- Add protected GitHub environments for `testpypi` and `pypi`.
- Verify the README renders correctly on TestPyPI/PyPI, especially the logo and
  repo-relative links.
- Use TestPyPI before real PyPI.
- Install from TestPyPI in a clean environment.
- Install from PyPI in a clean environment after the real upload.
- Publish the exact artifacts that passed the GitHub release build, not rebuilt
  copies.

### Strongly recommended but not blocking

- Add `py.typed` only if public typing support is intended.
- Decide whether `psycopg[binary]` remains mandatory or moves to an extra
  before broad PyPI adoption.
- Add `CODE_OF_CONDUCT.md` only if Skycat moves from closed maintenance to a
  broader public community contribution model.

### Optional follow-ups

- Publish Docker/OCI image to GHCR.
- Add SBOM/provenance/attestation workflow if the project needs supply-chain
  controls beyond GitHub Release provenance.
- Add GitHub Pages simple index if direct GitHub Release URLs are not ergonomic
  enough.
- Add conda-forge feedstock after the first GitHub-hosted release or after a
  later PyPI release.
- Add documentation website after API stabilizes.

## Suggested `pyproject.toml` target shape

This is illustrative, not a patch.

```toml
[project]
name = "skycat"
version = "0.1.4"
description = "Build and query versioned local PostgreSQL/PostGIS databases from astronomical reference catalogs."
readme = "README.md"
requires-python = ">=3.11, <3.14"
license = "GPL-3.0-only"
license-files = ["LICENSE"]
authors = [
    { name = "James Atkisson", email = "james@atkisson.net" },
]
maintainers = [
    { name = "SkynetRTN" },
]
keywords = ["astronomy", "catalogs", "photometry", "postgis", "postgresql"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
    "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS :: MacOS X",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Database",
    "Topic :: Scientific/Engineering :: Astronomy",
]
dependencies = [
    "sqlalchemy ~= 2.0.38",
    "geoalchemy2 ~= 0.20.0",
    "psycopg[binary] ~= 3.2.4",
    "alembic ~= 1.14",
    "click ~= 8.1",
]

[project.optional-dependencies]
dev = [
    "pytest >= 8.0",
    "ruff >= 0.7.0",
    "pyright >= 1.1.380",
    "twine >= 6.0",
]

[project.urls]
Homepage = "https://github.com/SkynetRTN/skycat"
Repository = "https://github.com/SkynetRTN/skycat"
Issues = "https://github.com/SkynetRTN/skycat/issues"
Documentation = "https://github.com/SkynetRTN/skycat#readme"

[project.scripts]
skycat = "skycat.cli.main:main_entry"

[tool.hatch.build.targets.wheel]
packages = ["skycat"]

[build-system]
requires = ["hatchling >= 1.26"]
build-backend = "hatchling.build"
```

If `py.typed` is added, keep it under `skycat/py.typed` and validate that it
appears in the built wheel. Do not add extra Hatchling file-selection rules
unless the package-build check proves they are needed; `packages = ["skycat"]`
may already include tracked files under the package directory. The release test
should be the source of truth.

## Source links reviewed

- Python Packaging User Guide, Packaging Python Projects:
  https://packaging.python.org/en/latest/tutorials/packaging-projects/
- Python Packaging User Guide, packaging flow:
  https://packaging.python.org/en/latest/flow/
- Python Packaging User Guide, names and normalization:
  https://packaging.python.org/en/latest/specifications/name-normalization/
- Python Packaging User Guide, simple repository API:
  https://packaging.python.org/en/latest/specifications/simple-repository-api/
- PyPI Trusted Publishers:
  https://docs.pypi.org/trusted-publishers/
- PyPI pending Trusted Publisher project creation:
  https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/
- PyPI Trusted Publisher publishing workflow:
  https://docs.pypi.org/trusted-publishers/using-a-publisher/
- PyPI help and account/security requirements:
  https://pypi.org/help/
- GitHub Actions OIDC for PyPI:
  https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-pypi
- GitHub Packages supported registries:
  https://docs.github.com/en/packages/learn-github-packages/introduction-to-github-packages
- GitHub Releases:
  https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- GitHub Pages publishing source:
  https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
- conda-forge contributing packages:
  https://conda-forge.org/docs/maintainer/adding_pkgs/
- Hatch build configuration:
  https://hatch.pypa.io/dev/config/build/

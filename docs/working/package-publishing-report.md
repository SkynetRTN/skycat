---
status: working
reviewed: 2026-08-07
branch: dev
authority: code-inspection + official-docs
implementation: not-started
---

# Skycat package publishing report

This report describes what would be required to host Skycat as an installable
Python package through GitHub for the near term, and what would still be needed
to publish it later on PyPI or another package index.

It is intentionally a planning document only. No packaging, metadata, workflow,
or release-process changes are made by this report.

## Executive summary

Skycat is close to being packageable, and the new CI workflow additions make
GitHub-hosted package artifacts a reasonable near-term target. PyPI should be
treated as a later public-index step, not the first publishing milestone.

The repository already has the core package shape:

- `pyproject.toml` uses PEP 621-style `[project]` metadata.
- `hatchling` is configured as the build backend.
- The import package is `skycat/`.
- The distribution name is currently `skycat`.
- A console script is configured: `skycat = "skycat.cli.main:main_entry"`.
- Runtime dependencies are declared.
- The README is substantial and explains the CLI, API, database model, and local
  operation.
- CI now runs ruff, pyright, pytest, a real PostgreSQL/PostGIS service,
  package-build smoke tests, Python-version matrix tests, migration graph
  checks, workflow safety, dependency review, and advisory supply-chain scans.

The main remaining work is release-hardening:

- Add a real license file and license metadata.
- Add complete package metadata: project URLs, classifiers, keywords,
  maintainer contact, and supported operating systems.
- Decide and document versioning across the Python package, database migrations,
  importer semantics, Docker image tags, and catalog release metadata.
- Validate that the built wheel and sdist contain all runtime files, especially
  Alembic migration resources.
- Keep the existing package-build/install CI in the required aggregate.
- Add a GitHub Release workflow that builds immutable artifacts once, tests
  those artifacts, and attaches the exact wheel and sdist to a tagged GitHub
  Release.
- Decide whether `skycat` is the final public package name and reserve/check it
  before any later PyPI release.
- Decide whether GitHub Releases are sufficient for the near term or whether a
  GitHub Pages simple package index is also worth maintaining.

Recommended path:

1. Host near-term Python package artifacts on GitHub Releases.
2. Build and smoke-test the wheel/sdist through the existing `package build`
   CI job before any release is cut.
3. Document direct wheel installs and git-tag installs as the supported GitHub
   installation paths.
4. Add a GitHub Pages simple package index only if consumers need
   `pip --extra-index-url` behavior before PyPI.
5. Treat PyPI/TestPyPI and conda-forge as later distribution channels once the
   metadata, license, versioning, and package boundary are stable.
6. Use GitHub Packages only for container images, not Python wheels, because
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

Under that framing, a GitHub-hosted wheel is a good near-term fit for Skycat's
software component. It is not sufficient by itself for distributing Skycat as a
complete catalog query service, and moving the same artifacts to PyPI later
should not change that boundary.

## Current repository state

### Existing package metadata

`pyproject.toml` currently declares:

- `name = "skycat"`
- `version = "0.1.0"`
- `requires-python = ">=3.11, <3.14"`
- `readme = "README.md"`
- `authors = [{ name = "Skycat maintainers" }]`
- `maintainers = [{ name = "James Atkisson", email = "james@atkisson.net" }]`
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
  - `requires = ["hatchling"]`
  - `build-backend = "hatchling.build"`
- wheel target:
  - `packages = ["skycat"]`

`skycat/__init__.py` also declares `__version__ = "0.1.0"`.

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

### Existing CI baseline

The pushed workflow update added a much stronger package and supply-chain
baseline. The repository now has seven workflows under `.github/workflows/`:

- `ci.yml`
- `workflow-safety.yml`
- `dependency-review.yml`
- `containers.yml`
- `kubernetes.yml`
- `codeql.yml`
- `secret-scan.yml`

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
locate packaged migrations. The missing near-term piece is not package-build
validation; it is a release workflow that preserves and publishes those tested
artifacts through GitHub.

### Local validation attempted for this report

I attempted to build with:

```bash
uv build --out-dir /tmp/skycat-dist-check
```

That could not run in this shell because `uv` is not installed here. The local
`.venv` exists, but it does not contain `hatchling` or `build`, so I could not
inspect an actual wheel/sdist without installing additional tooling.

That local result is now less important because `ci.yml` includes a `package
build` job using `uv build`. For release documentation, keep both the `uv`
workflow and the equivalent standards-based `python -m build`/`twine check`
commands visible so a maintainer can reproduce artifacts outside GitHub
Actions if needed.

## Metadata requirements for GitHub now and PyPI later

### 1. Distribution name

The current distribution name is `skycat`.

The name is syntactically valid for Python packaging. Package-name
normalization means names such as `skycat`, `sky-cat`, and `sky_cat` would
collide after normalization if any equivalent form is already taken.

For GitHub Releases, this name only controls wheel/sdist filenames and installed
package metadata. It does not need to be reserved on PyPI before GitHub-hosted
distribution can start.

Current web search on 2026-08-06 found a PyPI user profile named `skycat` with
no projects, but did not find an existing `skycat` project page. This is not a
guarantee. PyPI name availability must be checked again immediately before the
first upload, and PyPI may still reject a name for policy or reservation
reasons.

Required actions:

- Confirm that `skycat` is the intended public package name.
- For GitHub-only hosting, use `skycat` consistently in artifact names and
  install examples.
- Check PyPI and TestPyPI immediately before any future PyPI upload.
- If the PyPI name is unavailable later, choose a normalized-distinct
  distribution name such as `skycat-db`, `skycat-catalogs`, or an
  organization-prefixed name.
- Keep the import package as `skycat` unless there is a real collision in the
  Python environment.

### 2. License

There is currently no `LICENSE*` file in the repository and no `license` or
`license-files` metadata in `pyproject.toml`.

This is still the biggest release-readiness blocker even if artifacts are hosted
only on GitHub. GitHub can host unlicensed release assets, but users,
organizations, downstream redistributors, PyPI, and conda-forge need clear
licensing before they can responsibly consume the package.

Required actions:

- Choose the project license.
- Add a root `LICENSE` file.
- Add license metadata to `pyproject.toml`, preferably an SPDX expression:

```toml
[project]
license = "MIT"  # example only; choose deliberately
license-files = ["LICENSE"]
```

- Verify that dependency licenses are acceptable for the intended use.
- Decide whether catalog source formats, documentation snippets, or bundled
  fixtures require notices separate from the code license.

Conda-forge specifically expects accurate license metadata and license files, so
this work is mandatory before conda-forge packaging.

### 3. Project metadata

The current metadata is installable but sparse.

Recommended additions:

```toml
[project]
maintainers = [
    { name = "James Atkisson", email = "james@atkisson.net" },
]
keywords = ["astronomy", "catalogs", "postgis", "postgresql", "photometry"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Science/Research",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: ...", # match chosen license
    "Operating System :: POSIX :: Linux",
    "Operating System :: MacOS :: MacOS X",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Database",
    "Topic :: Scientific/Engineering :: Astronomy",
    "Typing :: Typed",
]

[project.urls]
Homepage = "https://github.com/SkynetRTN/skycat"
Repository = "https://github.com/SkynetRTN/skycat"
Issues = "https://github.com/SkynetRTN/skycat/issues"
Documentation = "https://github.com/SkynetRTN/skycat#readme"
```

Use `Typing :: Typed` only if the package commits to PEP 561 behavior and ships
a `py.typed` marker. Right now there is no `py.typed`, so either add one and
commit to exported typing, or omit that classifier.

### 4. Python version support

`pyproject.toml` declares `>=3.11, <3.14`.

CI currently installs Python 3.12 only. The local `.venv` here points at Python
3.13. The declared support range is plausible, but it needs evidence.

Required actions before publishing:

- Run unit tests on Python 3.11, 3.12, and 3.13.
- Keep the full PostGIS integration suite on at least one supported Python
  version.
- Decide what happens when Python 3.14 is released: either test and expand the
  range, or keep `<3.14`.
- Ensure dependencies have compatible releases across the declared range.

### 5. Versioning policy

There are several version concepts in the repository:

- Python package version: `pyproject.toml`, currently `0.1.0`.
- Runtime `skycat.__version__`, currently `0.1.0`.
- Internal database schema version: `INTERNAL_SCHEMA_VERSION = 1`.
- Importer semantic version: `IMPORTER_VERSION = "1.0.0"`.
- Alembic migration revisions under `skycat/migrations/versions/`.
- Docker image tags, currently not formalized here.
- Catalog-family release versions such as APASS DR6/DR10 and Landolt 1992/2009.

Required actions:

- Document which version changes for which kind of change.
- Add a test that `skycat.__version__` matches package metadata, or
  single-source it through package metadata.
- Decide whether `IMPORTER_VERSION` should track the Python package version or
  remain an independent data-provenance version.
- Define when `INTERNAL_SCHEMA_VERSION` changes relative to Alembic migrations.
- Define Docker tag rules, for example `ghcr.io/skynetrtn/skycat:<python-package-version>`.
- Use immutable release tags such as `v0.1.0`.

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
is minimal:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Recommended actions:

- Pin a lower bound for Hatchling, especially if using modern license metadata:

```toml
[build-system]
requires = ["hatchling >= 1.26"]
build-backend = "hatchling.build"
```

- Add a documented build command:

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

- If the project standardizes on `uv`, also document:

```bash
uv build
```

- Ensure release CI does a clean isolated build instead of relying on the
  developer's existing virtualenv.

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
python -m build
python -m twine check dist/*
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

`README.md` is detailed and useful, but it was written primarily for GitHub.
For GitHub-hosted packages, the README and GitHub Release notes are the package
landing pages. Before later PyPI publishing, the README also has to validate as
a PyPI long description.

Required actions:

- For the GitHub-hosted release, confirm README links and release notes render
  correctly on GitHub.
- Before later PyPI publishing, run `twine check dist/*` and confirm the
  Markdown renders acceptably on PyPI.
- Replace or supplement repo-relative links that do not make sense outside
  GitHub before publishing elsewhere.
- Make the package boundary explicit: Skycat installs software, not a populated
  catalog database or hosted query endpoint.
- Make GitHub-hosted installation instructions prominent near the top:

```bash
python -m pip install \
  https://github.com/SkynetRTN/skycat/releases/download/v0.1.0/skycat-0.1.0-py3-none-any.whl
```

- Also document the source-tag fallback for users who do not need a prebuilt
  wheel:

```bash
python -m pip install "git+https://github.com/SkynetRTN/skycat.git@v0.1.0"
```

- Call out non-Python runtime requirements near the top:
  - PostgreSQL
  - PostGIS
  - external catalog source files under `SKYCAT_DATA_ROOT`
  - no bundled catalog datasets
- Describe the expected service/deployment path separately from the Python
  install path.
- Add a short "What gets installed" section:
  - Python API
  - CLI
  - migrations
  - no database server, no large catalog data

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

Required actions:

- Protect GitHub accounts with two-factor authentication.
- Protect release creation through GitHub environment approval if the workflow
  publishes artifacts automatically.
- Require Code Owner review for changes to `.github/workflows/release.yml`.
- Use least-privilege job permissions.
- Build artifacts once, then publish those exact artifacts.
- Store release provenance in GitHub Releases.
- For future PyPI publishing, add PyPI/TestPyPI accounts or organization
  membership and use Trusted Publishing rather than long-lived API tokens.

## Recommended GitHub-hosted release workflow

### Manual preparation

One-time project setup:

1. Choose and add the license.
2. Finish project metadata.
3. Decide final distribution name for GitHub artifact filenames.
4. Decide release tag format, for example `v0.1.0`.
5. Add GitHub environment protection for release publishing if releases are
   created by Actions.
6. Decide whether releases are draft-first or published immediately.
7. Document direct wheel URL and git-tag install examples.
8. Decide whether a GitHub Pages simple package index is needed.

Per release:

1. Confirm `git status` is clean.
2. Update version and changelog/release notes.
3. Run CI.
4. Build wheel and sdist.
5. Install from wheel in a clean venv and run smoke tests.
6. Install from sdist in a clean venv and run smoke tests.
7. Tag the release, for example `v0.1.0`.
8. Create a GitHub Release and attach the tested `dist/*.whl` and
   `dist/*.tar.gz` files.
9. Test install from the GitHub Release wheel URL:

```bash
python -m venv /tmp/skycat-github-release
/tmp/skycat-github-release/bin/python -m pip install \
  https://github.com/SkynetRTN/skycat/releases/download/v0.1.0/skycat-0.1.0-py3-none-any.whl
/tmp/skycat-github-release/bin/skycat --help
```

10. Optionally publish Docker/container artifacts if that is part of the
    release.
11. If using GitHub Pages as a simple package index, update the index after the
    GitHub Release artifacts exist.

### GitHub Actions shape

Use two jobs:

- `release-build`
- `github-release`

The build job should:

- check out the exact tag;
- install `uv`;
- build sdist and wheel;
- run clean wheel/sdist install smoke tests;
- verify packaged migrations resolve;
- upload `dist/*` as a GitHub Actions artifact.

The GitHub release job should:

- depend on `release-build`;
- run only for version tags or approved manual dispatches;
- use a protected GitHub environment;
- set `permissions: { contents: write }`;
- download the exact build artifact;
- create or update a GitHub Release;
- upload the tested wheel and sdist as release assets.

Do not rebuild in the release job. Rebuilding creates a risk that the tested
artifact and released artifact differ.

### Draft workflow skeleton

```yaml
name: Release

on:
  push:
    tags:
      - "v*.*.*"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  release-build:
    name: build distributions
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v3

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Build
        run: uv build

      - name: Smoke install wheel
        run: |
          uv venv /tmp/wheel-venv
          uv pip install --python /tmp/wheel-venv/bin/python dist/*.whl
          /tmp/wheel-venv/bin/python -c "import skycat; print(skycat.__version__)"
          /tmp/wheel-venv/bin/skycat --help
          /tmp/wheel-venv/bin/skycat config

      - name: Upload distributions
        uses: actions/upload-artifact@v4
        with:
          name: release-dists
          path: dist/*
          if-no-files-found: error

  github-release:
    name: publish GitHub Release
    runs-on: ubuntu-latest
    needs: release-build
    environment: github-release
    permissions:
      contents: write
    steps:
      - name: Download distributions
        uses: actions/download-artifact@v4
        with:
          name: release-dists
          path: dist/

      - name: Publish release assets
        uses: softprops/action-gh-release@v2
        with:
          files: dist/*
          draft: true
```

For later PyPI publishing, add a separate `pypi-publish` job that downloads the
same `release-dists` artifact and uses `pypa/gh-action-pypi-publish` from a
protected `pypi` environment.

## GitHub hosting options

### Option A: GitHub Releases

GitHub Releases should be the near-term primary host for Skycat wheel and sdist
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
  https://github.com/SkynetRTN/skycat/releases/download/v0.1.0/skycat-0.1.0-py3-none-any.whl
```

Recommendation: use GitHub Releases as the first package-hosting path. It is
simple, keeps artifacts next to source tags and release notes, and matches the
current desire to keep distribution inside GitHub while the package boundary and
release process stabilize.

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

### Blocking before a GitHub-hosted package release

- Choose and add a `LICENSE`.
- Add `license` and `license-files` metadata.
- Add complete `[project.urls]`.
- Add classifiers and keywords.
- Decide and document versioning policy.
- Ensure `pyproject.toml` version and `skycat.__version__` cannot drift.
- Decide release tag format and GitHub Release naming.
- Build wheel and sdist in a clean environment.
- Verify clean install from wheel.
- Verify clean install from sdist.
- Verify installed CLI works.
- Verify installed migration machinery can locate packaged migrations.
- Verify GitHub Release install instructions work from a direct wheel URL.
- Verify the README does not imply that installing the package creates a
  complete catalog query service.
- Add release notes or changelog policy.
- Decide whether releases start as drafts or publish immediately.

### Already implemented in dev

- Package-build/install CI through `package build`.
- Python 3.11/3.12/3.13 unit-test matrix.
- Alembic migration graph validation.
- Stable aggregate `required: ci`.
- Workflow syntax and security checks with `required: workflow safety`.
- Dependency review.
- Advisory container build, Compose config, container scan, Kubernetes manifest
  validation, CodeQL, and secret scanning.

### Strongly recommended before first GitHub-hosted release

- Add a release workflow that attaches the tested wheel and sdist to GitHub
  Releases.
- Add GitHub environment protection for release publishing.
- Add a short installation section at the top of README.
- Add a package-scope section explaining that PostgreSQL/PostGIS, source data,
  imports, and operational deployment are external to the wheel.
- Add direct GitHub Release wheel install examples.
- Add `SECURITY.md` with vulnerability-reporting policy.
- Add `CONTRIBUTING.md` if public contribution is expected.
- Add `CODE_OF_CONDUCT.md` if using a public community workflow.
- Add `py.typed` only if public typing support is intended.
- Decide whether `psycopg[binary]` remains mandatory or moves to an extra.

### Blocking before later PyPI release

- Confirm package name availability on PyPI and TestPyPI.
- Verify `twine check dist/*` passes.
- Verify the README renders correctly on PyPI.
- Use TestPyPI before real PyPI.
- Configure PyPI Trusted Publishing from GitHub Actions.
- Publish the exact artifacts that passed the GitHub release build, not rebuilt
  copies.

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
version = "0.1.0"
description = "Build and query versioned local PostgreSQL/PostGIS databases from astronomical reference catalogs."
readme = "README.md"
requires-python = ">=3.11, <3.14"
license = "..."                    # choose SPDX expression
license-files = ["LICENSE"]
authors = [
    { name = "Skycat maintainers" },
]
maintainers = [
    { name = "Skycat maintainers", email = "..." },
]
keywords = ["astronomy", "catalogs", "photometry", "postgis", "postgresql"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Intended Audience :: Science/Research",
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
    "build >= 1.2",
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

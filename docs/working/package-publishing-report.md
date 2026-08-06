---
status: working
reviewed: 2026-08-06
branch: dev
authority: code-inspection + official-docs
implementation: not-started
---

# Skycat package publishing report

This report describes what would be required to publish Skycat as an installable
Python package on PyPI, and what credible GitHub-driven alternatives exist.

It is intentionally a planning document only. No packaging, metadata, workflow,
or release-process changes are made by this report.

## Executive summary

Skycat is close to being packageable, but it is not yet ready for a responsible
public release.

The repository already has the core package shape:

- `pyproject.toml` uses PEP 621-style `[project]` metadata.
- `hatchling` is configured as the build backend.
- The import package is `skycat/`.
- The distribution name is currently `skycat`.
- A console script is configured: `skycat = "skycat.cli.main:main_entry"`.
- Runtime dependencies are declared.
- The README is substantial and explains the CLI, API, database model, and local
  operation.
- CI already runs ruff, pyright, pytest, and a real PostgreSQL/PostGIS service.

The main remaining work is release-hardening:

- Add a real license file and license metadata.
- Add complete package metadata: project URLs, classifiers, keywords,
  maintainer contact, and supported operating systems.
- Decide and document versioning across the Python package, database migrations,
  importer semantics, Docker image tags, and catalog release metadata.
- Validate that the built wheel and sdist contain all runtime files, especially
  Alembic migration resources.
- Add package-build/install tests to CI.
- Add a release workflow that builds immutable artifacts once, tests those
  artifacts, then publishes through PyPI Trusted Publishing.
- Decide whether `skycat` is the final public package name and reserve/check it
  at release time.
- Decide whether PyPI is enough or whether conda-forge, GitHub Releases, a
  GitHub Pages simple index, or container publishing are also useful.

Recommended path:

1. Publish Python distributions to PyPI as the canonical `pip install skycat`
   channel.
2. Use TestPyPI first.
3. Use GitHub Actions with PyPI Trusted Publishing instead of long-lived PyPI
   tokens.
4. Attach the exact wheel and sdist to GitHub Releases for traceability.
5. Treat conda-forge as a follow-up only after a PyPI release exists and the
   dependency/runtime story is stable.
6. Use GitHub Packages only for container images, not Python wheels, because
   GitHub Packages does not provide a native PyPI registry.

## Current repository state

### Existing package metadata

`pyproject.toml` currently declares:

- `name = "skycat"`
- `version = "0.1.0"`
- `requires-python = ">=3.11, <3.14"`
- `readme = "README.md"`
- `authors = [{ name = "Skycat maintainers" }]`
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
That is correct for PyPI: ship the code, migrations, parsers, and docs; keep
large APASS/VSX/Landolt/Stetson data external under `SKYCAT_DATA_ROOT`.

### Existing CI baseline

`.github/workflows/ci.yml` currently:

- runs on pull requests, pushes to `main`, and manual dispatch;
- starts PostgreSQL/PostGIS as a service;
- installs through `uv sync --frozen --extra dev`;
- runs `ruff check skycat tests`;
- runs `pyright skycat`;
- runs `skycat init`;
- verifies reader connectivity;
- runs `pytest tests -q`.

That is a strong functional gate, but it does not yet prove that an isolated
wheel or sdist is correct.

### Local validation attempted for this report

I attempted to build with:

```bash
uv build --out-dir /tmp/skycat-dist-check
```

That could not run in this shell because `uv` is not installed here. The local
`.venv` exists, but it does not contain `hatchling` or `build`, so I could not
inspect an actual wheel/sdist without installing additional tooling.

This is itself a release-readiness finding: the project documents and CI assume
`uv`, but the package release process should still be reproducible from a clean
environment by running a small documented set of commands.

## PyPI requirements and readiness

### 1. Distribution name

The current distribution name is `skycat`.

The name is syntactically valid for Python packaging. Package-name
normalization means names such as `skycat`, `sky-cat`, and `sky_cat` would
collide after normalization if any equivalent form is already taken.

Current web search on 2026-08-06 found a PyPI user profile named `skycat` with
no projects, but did not find an existing `skycat` project page. This is not a
guarantee. PyPI name availability must be checked again immediately before the
first upload, and PyPI may still reject a name for policy or reservation
reasons.

Required actions:

- Confirm that `skycat` is the intended public package name.
- Check PyPI and TestPyPI immediately before the first upload.
- If unavailable, choose a normalized-distinct name such as `skycat-db`,
  `skycat-catalogs`, or an organization-prefixed name.
- Keep the import package as `skycat` unless there is a real collision in the
  Python environment.

### 2. License

There is currently no `LICENSE*` file in the repository and no `license` or
`license-files` metadata in `pyproject.toml`.

This is the biggest public-release blocker. PyPI may technically accept a
package with minimal metadata, but users, organizations, downstream
redistributors, and conda-forge need clear licensing.

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
    { name = "Skycat maintainers", email = "..." },
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
Homepage = "https://github.com/<owner>/<repo>"
Repository = "https://github.com/<owner>/<repo>"
Issues = "https://github.com/<owner>/<repo>/issues"
Documentation = "https://github.com/<owner>/<repo>#readme"
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
- Define Docker tag rules, for example `ghcr.io/<owner>/skycat:<python-package-version>`.
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
keeps the wheel focused on the package directory. The unverified part is whether
all required non-Python runtime files, especially `script.py.mako`, are present
in the built wheel. That must be tested.

Recommended CI check:

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

Also add an sdist install check:

```bash
python -m venv /tmp/skycat-sdist-venv
/tmp/skycat-sdist-venv/bin/python -m pip install dist/skycat-*.tar.gz
/tmp/skycat-sdist-venv/bin/skycat --help
```

### 8. README / PyPI long description

`README.md` is detailed and useful, but it was written primarily for GitHub.
Before publishing, validate it as a PyPI long description.

Required actions:

- Run `twine check dist/*`.
- Confirm that all Markdown renders acceptably on PyPI.
- Replace or supplement repo-relative links that do not make sense on PyPI.
- Make installation instructions prominent near the top:

```bash
pip install skycat
```

- Call out non-Python runtime requirements near the top:
  - PostgreSQL
  - PostGIS
  - external catalog source files under `SKYCAT_DATA_ROOT`
  - no bundled catalog datasets
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

Recommendation for first PyPI release: keep the current dependency unless a
deployment target specifically forbids binary wheels. Revisit for conda-forge or
Linux distribution packaging.

### 10. Typing support

Pyright is already used internally, but public typing support is not declared.

Required decision:

- If Skycat wants downstream users to rely on inline type hints, add
  `skycat/py.typed`, ensure it is included in wheels, and consider adding
  `Typing :: Typed`.
- If not, omit the typing classifier for now.

### 11. Security and release integrity

Required actions:

- Create PyPI and TestPyPI accounts with verified email.
- Enable two-factor authentication.
- Use a PyPI organization if the package is maintained by a team.
- Prefer Trusted Publishing from GitHub Actions over long-lived API tokens.
- Protect the release workflow through a GitHub environment such as `pypi`.
- Require review for changes to `.github/workflows/release.yml`.
- Use least-privilege job permissions.
- Build artifacts once, then publish those exact artifacts.
- Store release provenance in GitHub Releases and PyPI attestations if enabled
  by the publishing action.

## Recommended PyPI release workflow

### Manual preparation

One-time project setup:

1. Choose and add the license.
2. Finish project metadata.
3. Decide final distribution name.
4. Create PyPI and TestPyPI accounts or organization.
5. Enable 2FA.
6. Configure TestPyPI Trusted Publisher for the repository/workflow/environment.
7. Configure PyPI Trusted Publisher after TestPyPI works.
8. Add GitHub environment protection for `testpypi` and `pypi`.

Per release:

1. Confirm `git status` is clean.
2. Update version and changelog/release notes.
3. Run CI.
4. Build wheel and sdist.
5. Install from wheel in a clean venv and run smoke tests.
6. Install from sdist in a clean venv and run smoke tests.
7. Publish to TestPyPI.
8. Test install from TestPyPI:

```bash
python -m venv /tmp/skycat-testpypi
/tmp/skycat-testpypi/bin/python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  skycat
/tmp/skycat-testpypi/bin/skycat --help
```

9. Tag the release, for example `v0.1.0`.
10. Publish to PyPI from the tag.
11. Attach the same wheel/sdist to a GitHub Release.
12. Publish Docker/container artifacts if that is part of the release.

### GitHub Actions shape

Use two jobs:

- `release-build`
- `pypi-publish`

The build job should:

- check out the exact tag;
- set up Python;
- install build tooling;
- build sdist and wheel;
- run `twine check`;
- run clean wheel/sdist install smoke tests;
- upload `dist/*` as a GitHub Actions artifact.

The publish job should:

- depend on `release-build`;
- run only for version tags or approved manual dispatches;
- use a protected GitHub environment;
- set `permissions: { id-token: write }`;
- download the exact build artifact;
- use `pypa/gh-action-pypi-publish`.

Do not rebuild in the publish job. Rebuilding creates a risk that the tested
artifact and uploaded artifact differ.

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

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install build tooling
        run: python -m pip install --upgrade build twine

      - name: Build
        run: python -m build

      - name: Check metadata
        run: python -m twine check dist/*

      - name: Smoke install wheel
        run: |
          python -m venv /tmp/wheel-venv
          /tmp/wheel-venv/bin/python -m pip install dist/*.whl
          /tmp/wheel-venv/bin/python -c "import skycat; print(skycat.__version__)"
          /tmp/wheel-venv/bin/skycat --help
          /tmp/wheel-venv/bin/skycat config

      - name: Upload distributions
        uses: actions/upload-artifact@v4
        with:
          name: release-dists
          path: dist/*
          if-no-files-found: error

  pypi-publish:
    name: publish to PyPI
    runs-on: ubuntu-latest
    needs: release-build
    environment: pypi
    permissions:
      id-token: write
      contents: read
    steps:
      - name: Download distributions
        uses: actions/download-artifact@v4
        with:
          name: release-dists
          path: dist/

      - name: Publish
        uses: pypa/gh-action-pypi-publish@release/v1
```

Add a parallel TestPyPI workflow or a manual `workflow_dispatch` input that
publishes to `https://test.pypi.org/legacy/` from a separate protected
environment.

## GitHub-driven alternatives

### Option A: GitHub Releases

GitHub Releases can host wheel and sdist files as release assets.

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
  https://github.com/<owner>/<repo>/releases/download/v0.1.0/skycat-0.1.0-py3-none-any.whl
```

Recommendation: use GitHub Releases as a supplement to PyPI, not as the primary
Python package channel.

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
ghcr.io/<owner>/skycat:<version>
ghcr.io/<owner>/skycat:sha-<shortsha>
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
  --extra-index-url https://<owner>.github.io/<repo>/simple/ \
  skycat
```

Downsides:

- less discoverable than PyPI;
- more custom infrastructure to maintain;
- dependency-confusion risk if users combine private/public indexes
  carelessly;
- no real advantage for a public open-source package whose intended install is
  `pip install skycat`.

Recommendation: only use this for a private/internal package index or a
special-purpose pre-release channel. Do not use it instead of PyPI for a public
package.

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

- public source archive, usually from PyPI or GitHub Releases;
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

Recommendation: publish PyPI first, then add conda-forge once the package
metadata, license, and source artifacts are stable.

### Option E: private package indexes

If Skycat is not meant to be public yet, alternatives include:

- an internal PyPI-compatible repository such as devpi, Artifactory, Nexus, or
  cloud provider artifact registries;
- GitHub Pages simple index for limited distribution;
- direct wheel download from GitHub Releases;
- container-only distribution through GHCR.

The package-hardening work is mostly the same either way: license, metadata,
wheel/sdist contents, artifact tests, versioning, and release automation.

## Required work checklist

### Blocking before any public release

- Choose and add a `LICENSE`.
- Add `license` and `license-files` metadata.
- Confirm package name availability.
- Add complete `[project.urls]`.
- Add classifiers and keywords.
- Decide and document versioning policy.
- Ensure `pyproject.toml` version and `skycat.__version__` cannot drift.
- Build wheel and sdist in a clean environment.
- Verify `twine check dist/*` passes.
- Verify clean install from wheel.
- Verify clean install from sdist.
- Verify installed CLI works.
- Verify installed migration machinery can locate packaged migrations.
- Verify the README renders correctly on PyPI.
- Add release notes or changelog policy.
- Use TestPyPI before real PyPI.

### Strongly recommended before first PyPI release

- Add package-build/install CI.
- Add a Python 3.11/3.12/3.13 unit-test matrix.
- Add release workflow using PyPI Trusted Publishing.
- Add GitHub environment protection for publishing.
- Attach release artifacts to GitHub Releases.
- Add a short installation section at the top of README.
- Add `SECURITY.md` with vulnerability-reporting policy.
- Add `CONTRIBUTING.md` if public contribution is expected.
- Add `CODE_OF_CONDUCT.md` if using a public community workflow.
- Add `py.typed` only if public typing support is intended.
- Decide whether `psycopg[binary]` remains mandatory or moves to an extra.

### Optional follow-ups

- Publish Docker/OCI image to GHCR.
- Add SBOM/provenance/attestation workflow if the project needs supply-chain
  controls beyond PyPI's default metadata.
- Add conda-forge feedstock after first PyPI release.
- Add GitHub Pages simple index for private/pre-release channels if useful.
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
Homepage = "https://github.com/<owner>/<repo>"
Repository = "https://github.com/<owner>/<repo>"
Issues = "https://github.com/<owner>/<repo>/issues"
Documentation = "https://github.com/<owner>/<repo>#readme"

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

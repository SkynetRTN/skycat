<p align="center">
  <img src="https://raw.githubusercontent.com/SkynetRTN/skycat/main/brand/skycat_logo.png" alt="Skycat logo" width="100%">
</p>

# Skycat

Skycat is a standalone Python package and command-line tool for building, managing and
querying versioned local PostgreSQL/PostGIS databases from mirrored
astronomical reference catalogs.

It provides calibration, photometry, and observation-processing pipelines
with a fast reproducible catalog layer that does not depend on the 
availability or performance of external catalog services. Skycat can discover 
and ingest known catalog releases, validate imported data, manage multiple
versions of the same catalog, activate the release that should serve as the default,
and retain older releases for reproducibility or comparison.

Its query interface supports spatial cone searches, catalog-native source lookups,
magnitude and quality filtering, and efficient batch crossmatching using PostGIS spatial
indexes. The overall goal is to provide astronomy software with a consistent, locally
controlled source of catalog data for workflows that require reliable access to large
astronomical reference catalogs. 

Skycat installs:

- the `skycat` CLI;
- Python query APIs, including `CatalogReader`;
- SQLAlchemy models and registry logic;
- ingestion parsers, validators, and COPY loaders;
- Alembic migrations under `skycat/migrations`;
- support for APASS, VSX, Landolt, and Stetson catalog families.

Skycat does not install PostgreSQL, PostGIS, catalog source files, imported
catalog rows, production credentials, backups, or a hosted query endpoint.
Operators still provision the database and provide source data separately.

## Supported Catalogs

| Family | Releases | Source | Rows |
|---|---|---|---|
| `apass` | DR6, DR10 | AAVSO APASS | 42.6M / 128.6M |
| `vsx` | current | AAVSO VSX | 10.3M |
| `landolt` | 1992, 2009 | CDS II/183A, J/AJ/137/4186 | 526 / 595 |
| `stetson` | StetsonGlobs | CDS J/MNRAS/485/3042 | 4.89M |

## Installation

From PyPI:

```bash
python -m pip install skycat
```

From the GitHub Release wheel:

```bash
python -m pip install \
  https://github.com/SkynetRTN/skycat/releases/download/v0.1.5/skycat-0.1.5-py3-none-any.whl
```

From a Git tag:

```bash
python -m pip install "git+https://github.com/SkynetRTN/skycat.git@v0.1.5"
```

See
[docs/operations/release.md](https://github.com/SkynetRTN/skycat/blob/main/docs/operations/release.md)
for release, artifact, TestPyPI, and PyPI validation steps.

## Quick Start

For local development:

```bash
uv sync --extra dev
uv run skycat --help
```

For a local PostgreSQL/PostGIS catalog database:

```bash
cp infra/docker/.env.example infra/docker/.env
cp infra/docker/.env.secrets.example infra/docker/.env.secrets
docker compose -f infra/docker/compose.yaml build skycat-postgres
docker compose -f infra/docker/compose.yaml up -d skycat-postgres
docker compose -f infra/docker/compose.yaml --profile skycat-tools run --rm skycat-init
docker compose -f infra/docker/compose.yaml --profile skycat-tools run --rm skycat-migrate
```

Then point `SKYCAT_DATA_ROOT` at mirrored source data, discover releases, and
import deliberately:

```bash
export SKYCAT_DATA_ROOT=/srv/agents/catalogs
uv run skycat discover
uv run skycat import apass dr6 --activate
uv run skycat cone apass --ra 10.0 --dec 1.0 --radius-arcmin 5
```

The operational details are in
[docs/operations/runbook.md](https://github.com/SkynetRTN/skycat/blob/main/docs/operations/runbook.md).

## CLI

Run `skycat --help` for the full command list. The main commands are:

| Command | Purpose |
|---|---|
| `config` | Show resolved configuration. |
| `init` | Create roles, grants, schemas, PostGIS, and migrations. |
| `migrate` / `migrate-status` | Apply or inspect Alembic migrations. |
| `discover` | Discover local source releases. |
| `import` | Ingest, validate, mark ready, and optionally activate a release. |
| `activate` / `deactivate` | Change the default active release for a family. |
| `releases` / `history` | Inspect release and ingestion state. |
| `cone` / `crossmatch` / `lookup` | Query catalog rows. |
| `validate` | Re-validate an imported production partition. |
| `health` | Run read-only health checks. |
| `sizes` | Report table and index sizes. |
| `clean-staging` / `remove-release` / `reset` | Maintenance and guarded destructive operations. |

Examples:

```bash
skycat --json config
skycat releases apass
skycat cone apass --ra 10.0 --dec 1.0 --radius-arcmin 5 --order-by johnson_v_mag --limit 50
skycat lookup apass 090-0000001
```

`--json` is a group option, so it goes before the subcommand.

## Python API

Use `CatalogReader` for application reads. It owns connection pooling, active
release caching, and a default statement timeout.

```python
from skycat import CatalogReader

with CatalogReader.from_env() as reader:
    stars = reader.cone(
        "apass",
        ra=10.0,
        dec=1.0,
        radius_arcmin=5,
        order_by="johnson_v_mag",
        limit=50,
    )
    hits = reader.crossmatch("vsx", [("target-1", 10.0, 1.0)], radius_arcsec=5)
    row = reader.lookup("apass", "090-0000001")
```

Pass `order_by` whenever you also pass `limit`. Without it, cone results are
nearest-first by angular separation, which is not the same as brightest-first in
dense fields.

See
[docs/reference/api-stability.md](https://github.com/SkynetRTN/skycat/blob/main/docs/reference/api-stability.md)
for the stable API surface and compatibility policy.

## Release Model

Catalog releases are a database dimension, not a deployment artifact. Each
family can have multiple imported releases, but only one active release serves
default queries at a time. Use `--release` when you need a specific historical
release for parity checks, rollback, or reproducibility.

Imports stage rows, validate them, build production partitions, mark releases
ready, and activate only when explicitly requested. A failed import should not
degrade the currently active release.

The full architecture is documented in
[docs/reference/architecture.md](https://github.com/SkynetRTN/skycat/blob/main/docs/reference/architecture.md),
and source data provenance is covered in
[docs/guides/provenance.md](https://github.com/SkynetRTN/skycat/blob/main/docs/guides/provenance.md).

## Configuration

Skycat uses `SKYCAT_DB_*` for database settings and `SKYCAT_*` for catalog
runtime paths and diagnostics.

| Variable | Default | Purpose |
|---|---|---|
| `SKYCAT_DB_BACKEND` | `postgresql+psycopg` | SQLAlchemy backend. |
| `SKYCAT_DB_HOST` | `127.0.0.1` | Database host. |
| `SKYCAT_DB_PORT` | `5433` | Host-side Compose port; use `5432` inside Compose. |
| `SKYCAT_DB_NAME` | `catalogs` | Catalog database name. |
| `SKYCAT_DB_USER` / `SKYCAT_DB_PASSWORD` | `catalog_reader` / unset | Default query identity. |
| `SKYCAT_DB_SSLMODE` | unset | libpq SSL mode. |
| `SKYCAT_DB_POOL_SIZE` / `SKYCAT_DB_MAX_OVERFLOW` | `10` / `5` | Reader pool sizing. |
| `SKYCAT_DB_POOL_RECYCLE` / `SKYCAT_DB_POOL_TIMEOUT` | `300` / `30` | Pool recycle and checkout timing. |
| `SKYCAT_DB_POOL_PRE_PING` | `true` | Detect stale pooled connections. |
| `SKYCAT_DB_ECHO` | `false` | SQL echo logging. |
| `SKYCAT_DB_STATEMENT_TIMEOUT` | unset | Per-connection statement timeout in milliseconds. |
| `SKYCAT_DB_BOOTSTRAP_USER` / `SKYCAT_DB_BOOTSTRAP_PASSWORD` | unset | DBA/bootstrap identity for `init`. |
| `SKYCAT_DB_ADMIN_USER` / `SKYCAT_DB_ADMIN_PASSWORD` | unset | Owner/migrator identity. |
| `SKYCAT_DB_INGEST_USER` / `SKYCAT_DB_INGEST_PASSWORD` | unset | Bulk-ingestion identity. |
| `SKYCAT_DB_READER_USER` / `SKYCAT_DB_READER_PASSWORD` | unset | Read-only role managed by `init`. |
| `SKYCAT_DATA_ROOT` | `/srv/agents/catalogs` | Read-only catalog source root. |
| `SKYCAT_WORK_ROOT` | `/tmp/skycat-work` | Writable work area for manifests, rejects, and checkpoints. |
| `SKYCAT_DEBUG` | unset | Diagnosis only: suppress the CLI's friendly error messages entirely, so the original exception and its traceback reach the interpreter. Leave it unset in Jobs and services — with it set, an error is a stack trace instead of a message. |

## Testing

Fast tests do not require a database:

```bash
uv run pytest tests -q -m "not postgis"
```

PostGIS tests require a throwaway catalog database. Do not point them at a
catalog store you care about; fixtures import small samples with replacement.

```bash
uv run skycat health
uv run pytest tests -q --require-postgis
```

See
[docs/operations/ci.md](https://github.com/SkynetRTN/skycat/blob/main/docs/operations/ci.md)
for the CI matrix and
[docs/operations/runbook.md](https://github.com/SkynetRTN/skycat/blob/main/docs/operations/runbook.md)
for operational safety notes.

## Adding a Catalog Family

The implementation guide is
[docs/guides/add-family.md](https://github.com/SkynetRTN/skycat/blob/main/docs/guides/add-family.md).
The short checklist is:

1. Add a `FamilyDef` / `ReleaseDef` in `skycat/registry/catalog_defs.py`.
2. Add a typed model under `skycat/models/`.
3. Add a streaming parser under `skycat/ingestion/parsers/`.
4. Add an Alembic migration under `skycat/migrations/versions/`.
5. Add family-specific validation under `skycat/validation/` when needed.
6. Add fixtures, tests, and a row in the supported-catalog table above.

## Documentation

| Document | Use it for |
|---|---|
| [docs/reference/architecture.md](https://github.com/SkynetRTN/skycat/blob/main/docs/reference/architecture.md) | Database model, release lifecycle, spatial representation, deployment shape. |
| [docs/reference/api-stability.md](https://github.com/SkynetRTN/skycat/blob/main/docs/reference/api-stability.md) | Stable Python, CLI, configuration, and database contracts. |
| [docs/guides/add-family.md](https://github.com/SkynetRTN/skycat/blob/main/docs/guides/add-family.md) | Worked guide for adding a new catalog family. |
| [docs/guides/provenance.md](https://github.com/SkynetRTN/skycat/blob/main/docs/guides/provenance.md) | Source mirroring and release provenance. |
| [docs/operations/runbook.md](https://github.com/SkynetRTN/skycat/blob/main/docs/operations/runbook.md) | Destructive-operation safety, incident checks, credential rotation. |
| [docs/operations/performance.md](https://github.com/SkynetRTN/skycat/blob/main/docs/operations/performance.md) | Query targets and tuning guidance. |
| [docs/operations/ci.md](https://github.com/SkynetRTN/skycat/blob/main/docs/operations/ci.md) | Required checks and workflow behavior. |
| [docs/operations/release.md](https://github.com/SkynetRTN/skycat/blob/main/docs/operations/release.md) | GitHub Releases, TestPyPI, and PyPI publishing. |
| [docs/decisions/](https://github.com/SkynetRTN/skycat/tree/main/docs/decisions) | Architecture decisions. |

Working notes live under `docs/working/`; they are snapshots of open work, not
current API references.

## License

Skycat is licensed under the GNU General Public License v3.0. See
[LICENSE](https://github.com/SkynetRTN/skycat/blob/main/LICENSE).

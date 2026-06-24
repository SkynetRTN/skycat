# Skycat

A **standalone** PostgreSQL/PostGIS store for large local astronomical reference
catalogs — APASS DR6, APASS DR10, VSX, and (by design) future families such as
Pan-STARRS, 2MASS, UCAC5, Tycho-2, Landolt, Stetson, SkyMapper, USNO-B1.0.

It is deliberately **independent** of `skynet-db` and of the primary `sky`
operational database. It has its own SQLAlchemy declarative base, metadata,
engine/session factory, Alembic environment, schemas, database roles, and
configuration namespace (`SKYCAT_DB_*`). It is never wired into
`skynet_db.db.initialization.initialize_db`, never added to the `sky`
`reset-db`, and never imports `skynet_db`.

---

## Why a separate database?

Reference catalogs are large (tens to hundreds of millions of rows), immutable
once published, versioned by release, and queried spatially. They have an
entirely different lifecycle from operational `sky` data. Keeping them in their
own database (`catalogs`) on their own PostGIS server:

- isolates their storage, vacuum, backup, and connection pools from `sky`;
- lets them be (re)built/ingested without touching operational data;
- lets a process hold a `sky` session **and** a `catalogs` session at once,
  with no shared base/metadata/engine.

```
Docker Compose project: skynet
├── postgres          (existing)  db: sky        host 127.0.0.1:5432  vol postgres_data
└── skycat-postgres  (new)       db: catalogs   host 127.0.0.1:5433  vol catalog_postgres_data
```

---

## Architecture at a glance

| Concern | This package |
|---|---|
| Declarative base | `skycat.database.base.CatalogBase` (own `MetaData`) |
| Database | `catalogs` (never `sky`) |
| Schemas | `catalog_registry`, `catalog_data`, `catalog_staging` |
| Roles | `catalog_owner` (migrator), `catalog_ingest`, `catalog_reader` (+ bootstrap) |
| Migrations | catalog-owned Alembic env (`skycat/migrations`) |
| Spatial | `geography(Point,4326)` GENERATED column + GiST, spherical cone search |
| Config | `SKYCAT_DB_*` (never `SHARED_DB_*` / `SKYNET_<APP>_DB_*`) |
| CLI | `skycat …` |

### PostgreSQL schemas

- **`catalog_registry`** — families, releases, ingestion runs, validation
  summaries, source manifests/files. (Management metadata.)
- **`catalog_data`** — production catalog rows. Each family is a single
  `LIST (release_id)` **partitioned** parent table with one partition per
  release. Activation flips a registry flag — it never rewrites rows.
- **`catalog_staging`** — unlogged bulk-load staging + rejected rows. Cleaned
  after success; optionally retained after failure for diagnosis.

### Catalog-family clustering

Each family has its own scientifically-typed table, its own parser, validation,
spatial indexes, and release-aware partitions. Unrelated families are **never**
forced into one universal row table. APASS DR6 and DR10 are two releases of one
APASS family sharing a canonical superset schema (bands absent in a release are
NULL). VSX has its own typed model.

### PostGIS coordinate representation

Every positional table keeps the original numeric `ra_deg` (0..360) and
`dec_deg` (-90..90), plus a derived **GENERATED** column:

```sql
geom geography(Point,4326) GENERATED ALWAYS AS (
  ST_SetSRID(ST_MakePoint(
    CASE WHEN ra_deg > 180 THEN ra_deg - 360 ELSE ra_deg END,  -- RA -> lon[-180,180]
    dec_deg), 4326)
) STORED
```

Cone searches use `ST_DWithin(geom, point, radius_m, false)` and report
separation via `ST_Distance(geom, point, false)` with `use_spheroid => false`,
so distances are evaluated on a **sphere** (exact angular separation). RA
wraparound at 0/360 and both celestial poles are handled correctly; there is no
flat-plane approximation. Angular degrees ↔ metres conversion reuses the PostGIS
sphere radius (`6371008.7714 m`, WGS84 mean) so the round-trip is exact. Each partition gets a
GiST index on `geom` plus btree indexes on `native_id` / `release_id`.

---

## Configuration

All settings live in the `SKYCAT_DB_*` namespace. Nothing hard-codes a
host/port — the same code works inside Compose, host→Compose, and against remote
staging/prod/Kubernetes endpoints.

| Variable | Default | Notes |
|---|---|---|
| `SKYCAT_DB_BACKEND` | `postgresql+psycopg` | SQLAlchemy driver |
| `SKYCAT_DB_HOST` | `127.0.0.1` | `skycat-postgres` inside Compose |
| `SKYCAT_DB_PORT` | `5433` | `5432` inside Compose |
| `SKYCAT_DB_NAME` | `catalogs` | refuses `sky` |
| `SKYCAT_DB_USER` / `_PASSWORD` | `catalog_reader` / — | default identity |
| `SKYCAT_DB_SSLMODE` | — | libpq sslmode |
| `SKYCAT_DB_POOL_SIZE` / `_MAX_OVERFLOW` / `_POOL_RECYCLE` / `_POOL_TIMEOUT` / `_POOL_PRE_PING` | 10 / 5 / 300 / 30 / true | pool tuning |
| `SKYCAT_DB_ECHO` | false | SQL echo |
| `SKYCAT_DB_STATEMENT_TIMEOUT` | — | per-connection ms |
| `SKYCAT_DB_BOOTSTRAP_USER` / `_PASSWORD` | — | init only (DBA/superuser) |
| `SKYCAT_DB_ADMIN_USER` / `_PASSWORD` | — | owner / migrator |
| `SKYCAT_DB_INGEST_USER` / `_PASSWORD` | — | bulk loader |
| `SKYCAT_DB_READER_USER` / `_PASSWORD` | — | read-only consumer |
| `SKYCAT_DATA_ROOT` | `/srv/agents/catalogs` | read-only source root |
| `SKYCAT_WORK_ROOT` | `/tmp/skycat-work` | writable scratch |

Compose-internal vs host values:

```
Inside Compose:   host=skycat-postgres  port=5432
From the host:    host=127.0.0.1         port=5433
```

### Database roles

- **bootstrap** (container `POSTGRES_USER`, or a DBA) — used by `init` only:
  enables PostGIS, creates the operational roles, applies grants.
- **`catalog_owner`** — owns schemas/objects, applies migrations. Not used for
  runtime queries.
- **`catalog_ingest`** — reads registry, loads staging, inserts release data,
  updates import/release metadata, builds indexes. No cluster admin.
- **`catalog_reader`** — read-only: registry + active/historical data, cone
  searches, crossmatch. Cannot alter schemas, write rows, or activate releases.

Ordinary query consumers must connect as `catalog_reader`, never bootstrap/owner.

---

## Local source data

```
Host source root:       /srv/agents/catalogs       (read-only)
Container source root:  /catalog-data              (read-only bind mount)
Writable work root:     $SKYCAT_WORK_ROOT  (manifests, rejects, checkpoints)
```

Source files are **read-only** and never moved, renamed, or rewritten in place.
Generated artifacts go to the work root, never the source root. macOS/Linux: set
`SKYCAT_DATA_ROOT` to wherever you keep the datasets (e.g.
`/Users/you/catalogs` on macOS, `/srv/agents/catalogs` on the Linux dev box).

Discovered layout (auto-detected, configurable):

```
catalogs/
├── APASS-DR6/   apass_dr6*.zip + zp/zm *_6.sum  (.sum, V B-V B g' r' i' + errors)
├── APASS-DR10/  apass_dr10*.zip + zp/zm *.txt   (.txt, B V u g r i z Y nobs+mag+err; 99.999 = missing)
└── VSX/         vsx.dat (+ ReadMe)              (fixed-width CDS byte format)
```

---

## Quick start (local Docker Compose)

From the repository root:

```bash
# 1. env files
cp infra/docker/.env.example         infra/docker/.env
cp infra/docker/.env.secrets.example infra/docker/.env.secrets   # fill REPLACE_ME

# 2. build + start the catalog database
docker compose -f infra/docker/compose.yaml build skycat-postgres
docker compose -f infra/docker/compose.yaml up -d skycat-postgres

# 3. health
docker compose -f infra/docker/compose.yaml ps skycat-postgres

# 4. init (PostGIS + roles + grants) and migrate (schemas + tables)
docker compose -f infra/docker/compose.yaml --profile skycat-tools run --rm skycat-init
docker compose -f infra/docker/compose.yaml --profile skycat-tools run --rm skycat-migrate

# 5. discover + import + validate + activate (APASS DR6)
docker compose -f infra/docker/compose.yaml --profile skycat-tools run --rm \
  skycat discover
docker compose -f infra/docker/compose.yaml --profile skycat-tools run --rm \
  skycat import apass dr6 --activate

# 6. cone search
docker compose -f infra/docker/compose.yaml --profile skycat-tools run --rm \
  skycat cone apass --ra 10.0 --dec 1.0 --radius-arcmin 5
```

### Host-run tooling

The same CLI runs directly on the host (port 5433):

```bash
export SKYCAT_DB_HOST=127.0.0.1 SKYCAT_DB_PORT=5433
export SKYCAT_DB_ADMIN_USER=catalog_admin SKYCAT_DB_ADMIN_PASSWORD=...
export SKYCAT_DATA_ROOT=/srv/agents/catalogs

skycat health
skycat discover
skycat import apass dr6 --activate
skycat cone apass --ra 10 --dec 1 --radius-arcmin 5 --json
```

---

## CLI

`skycat <command>` (every destructive command is explicit; add
`--json` for machine output):

| Command | Purpose |
|---|---|
| `config` | Show resolved (redacted) configuration |
| `init` | Create roles/PostGIS/grants/schemas + migrate (non-destructive) |
| `migrate` / `migrate-status` | Apply / inspect Alembic migrations |
| `health` | Comprehensive health report |
| `families` / `register-family` | List / register catalog families |
| `discover` | Discover local source releases |
| `register-release` | Register a release row |
| `import <family> <release>` | Full ingest (discover→stage→load→validate→ready[→activate]) |
| `validate <family> <release>` | (Re)validate a release |
| `activate` / `deactivate` | Atomically (de)activate a release |
| `releases` / `history` | List releases / ingestion history |
| `cone <family>` | Cone search (`--radius-deg/-arcmin/-arcsec`, `--release`, `--limit`) |
| `crossmatch <family>` | Batch crossmatch from a CSV of `id,ra,dec` |
| `lookup <family> <native_id>` | Native identifier lookup |
| `sizes` | Table / index sizes |
| `clean-staging` | Remove staging artifacts (non-destructive to production) |
| `remove-release` | Remove an **inactive** release (guarded) |
| `reset` | **Destructive** dev reset (requires `--force`, refuses prod-like) |

---

## Ingestion lifecycle

`discover → register family → register release → ingestion-run → verify
checksums → create staging → parse+COPY → validate staging → transform into a
new (detached) production partition → build indexes → ANALYZE → validate
production → mark READY → (explicit) ATTACH+ACTIVATE → record completion`.

- Bulk load is **streaming COPY** into unlogged staging (bounded memory, no
  per-row ORM inserts).
- A new release is built as a **detached** table, indexed, then `ATTACH
  PARTITION` — so activation is atomic and never rewrites existing rows.
- A failed/incomplete release **cannot** become active. The previous active
  release keeps serving queries until the new one is fully READY and explicitly
  activated. At most one active release per family (enforced by a partial unique
  index).
- Imports are idempotent on (family, release, source checksum, importer version,
  schema version). Malformed/duplicate rows are recorded with reasons, never
  silently dropped. Destructive replacement is never the default.

See [docs](../../../docs) and the module docstrings for details.

---

## Data safety

- Refuses to migrate/ingest against `sky` (or other reserved DB names).
- Production-like hosts/DB names get extra destructive-operation guards.
- Active releases can't be dropped without explicit deactivate/force.
- Source files are read-only; no catalog command deletes `/srv/agents/catalogs`.
- `docker compose down` never deletes `catalog_postgres_data` (use the explicit
  volume `rm`).
- Import commands print target host/port/db/catalog/release/source before
  loading.

---

## Testing

```bash
# unit tests (no DB needed)
pytest packages/py/skycat/tests -q -m "not postgis"

# integration tests against a real PostGIS (point at the Compose catalog DB)
export SKYCAT_TEST_DSN=postgresql+psycopg://catalog_admin:...@127.0.0.1:5433/catalogs
pytest packages/py/skycat/tests -q
```

Integration tests use a real PostgreSQL/PostGIS database (never SQLite) for
anything touching PostGIS, schemas, roles, COPY, partitioning, or spatial
indexes. They are skipped automatically when no catalog DB is reachable.

---

## Kubernetes & production

The catalog PostgreSQL/PostGIS server is externally provisioned in
staging/production (same pattern as `sky`) but exposes the same connection/role
contract. Workloads receive `SKYCAT_DB_*` via the shared ConfigMap
(non-secret) and `skynet-runtime-secrets` (credentials). A migration Job runs
Alembic as the owner role; an ingestion Job imports an explicit family+release as
the ingest role. Neither drops the database nor runs on ordinary app startup.
See `infra/kubernetes/deploy/base/jobs/skycat-*.yaml` and the Ansible
`postgres_server` role's catalog tenant tasks.

### Rename migration boundaries

The package, import, CLI, configuration, image, Compose service, Kubernetes
Jobs, and Job-only Secret use Skycat names exclusively. Operators must provide
all package configuration through `SKYCAT_*`; runtime compatibility aliases
are intentionally not accepted.

Database `catalogs`, schemas `catalog_registry`/`catalog_data`/
`catalog_staging`, roles `catalog_owner`/`catalog_ingest`/`catalog_reader`, and
existing volumes remain unchanged because they contain persisted data or are
shared database contracts. Reuse those resources in place; do not recreate or
copy their data for this application rename. Create `skycat-admin-secrets`
with the existing secret values before applying `skycat-init`,
`skycat-migrate`, or `skycat-ingest`, then retire any superseded Secret only
after the Skycat Jobs are verified.

---

## Consumers

The Skynet **optical pipeline** consumes this package as the *local-first*
backend for its catalog providers (APASS, VSX): cone searches, native-id lookup,
and batch crossmatch are served from here via the read-only `catalog_reader`
role, falling back to remote VizieR when the local store is unavailable or a
catalog isn't imported locally. The integration lives behind the existing
provider interface (no PostGIS/SQLAlchemy leaks into the pipeline) — see
`packages/py/skynet-db/.../optical_data_processing/catalogs/local/README.md`
for backend-selection modes (`SKYCAT_BACKEND`), field mappings, and
failure/health behaviour. The query API it uses (`cone_search`,
`lookup_native_id`, `batch_crossmatch`) accepts an optional caller-managed
`engine=` so a long-lived worker reuses one pooled connection.

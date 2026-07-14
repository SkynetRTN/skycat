# Skycat

A **standalone** PostgreSQL/PostGIS store for large local astronomical reference
catalogs — APASS DR6, APASS DR10, VSX, Landolt (1992 + 2009) and Stetson
globular-cluster standards, plus (by design) future families such as
Pan-STARRS, 2MASS, UCAC5, Tycho-2, SkyMapper, USNO-B1.0.

| Family | Releases | Source (CDS) | Parser format | Rows | Provider |
|--------|----------|--------------|---------------|------|----------|
| `apass` | DR6, DR10 | AAVSO | `apass_dr6_sum` / `apass_dr10_txt` | 42.6M / 128.6M | APASS |
| `vsx` | current | B/vsx | `vsx_dat` | 10.3M | VSX |
| `landolt` | 1992, 2009 | II/183A, J/AJ/137/4186 | `landolt_1992_dat` / `landolt_2009_dat` | 526 / 595 | Landolt |
| `stetson` | StetsonGlobs | J/MNRAS/485/3042 | `stetson_globs_dat` | 4.89M | StetsonGlobs |

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
NULL). VSX has its own typed model. **Landolt** is one family with two releases
(1992 + 2009) sharing a model that stores V plus the five color indices (the
individual U/B/R/I bands are *derived* downstream exactly as the remote provider
does — see below). **Stetson** has its own typed model (U/B/V/R/I with per-band
counts, DAOPHOT chi/sharp, Welch-Stetson variability, cluster name).

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
| `SKYCAT_DB_STATEMENT_TIMEOUT` | — (but `CatalogReader` defaults to `30000`) | per-connection ms. Unset, the CLI and the bare query functions run with **no** timeout; `CatalogReader` applies 30s so one pathological query cannot wedge a worker. Setting this wins everywhere. |
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
├── APASS-DR6/     apass_dr6*.zip + zp/zm *_6.sum  (.sum, V B-V B g' r' i' + errors)
├── APASS-DR10/    apass_dr10*.zip + zp/zm *.txt   (.txt, B V u g r i z Y nobs+mag+err; 99.999 = missing)
├── VSX/           vsx.dat (+ ReadMe)              (fixed-width CDS byte format)
├── Landolt_1992/  table2.dat (+ ReadMe)           (fixed-width II/183A; 526 standard stars)
├── Landolt_2009/  table2.dat (+ ReadMe, table5)   (fixed-width J/AJ/137/4186; 595 stars; table5 aux)
└── StetsonGlobs/  table4.dat (+ ReadMe, table2)   (pipe-delimited J/MNRAS/485/3042; 4.89M rows)
```

The photometry datasets are mounted at `/srv/agents/catalogs/photometry`, which
is also a valid `SKYCAT_DATA_ROOT` (it holds the APASS/VSX dirs too):

```bash
export SKYCAT_DATA_ROOT=/srv/agents/catalogs/photometry
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

## Photometric standard catalogs (Landolt & Stetson)

Landolt and Stetson are imported the same way as APASS/VSX. Import without
activating first, then activate deliberately:

```bash
export SKYCAT_DATA_ROOT=/srv/agents/catalogs/photometry

# Landolt — one family, two releases (1992 and 2009). --source-dir is optional
# when the dirs sit under SKYCAT_DATA_ROOT (Landolt_1992 / Landolt_2009).
skycat import landolt 1992 --source-dir /srv/agents/catalogs/photometry/Landolt_1992
skycat import landolt 2009 --source-dir /srv/agents/catalogs/photometry/Landolt_2009

# Stetson — the StetsonGlobs release (~4.9M rows; the COPY+index step runs for
# a few minutes).
skycat import stetson StetsonGlobs --source-dir /srv/agents/catalogs/photometry/StetsonGlobs

# Activate deliberately: 2009 is the default-active Landolt release (newest /
# most complete); 1992 stays ready and is still queryable explicitly.
skycat activate landolt 2009
skycat activate stetson StetsonGlobs

# Validation / state
skycat validate landolt 1992          # re-validate a production partition
skycat releases landolt               # show release states
```

Querying — active release vs. an explicit release:

```bash
# Active Landolt (2009) near a standard field
skycat cone landolt --ra 7.5375 --dec -46.5228 --radius-arcmin 10

# Explicit Landolt 1992 (the release the remote VizieR provider mirrors)
skycat cone landolt --ra 7.5375 --dec -46.5228 --radius-arcmin 10 --release 1992

# Stetson cone in a globular cluster + native-id lookup (Star id is unique only
# within a cluster, so it repeats across clusters)
skycat cone   stetson --ra 250.4234 --dec 36.4613 --radius-arcmin 1
skycat lookup stetson 1 --release StetsonGlobs
skycat cone   stetson --ra 250.4234 --dec 36.4613 --radius-arcmin 1 --explain  # proves GiST use
```

### Magnitudes, identifiers, and remote parity

- **Landolt** stores `V` plus the five color indices (`B-V`, `U-B`, `V-R`,
  `R-I`, `V-I`) and their errors. The individual **U/B/R/I** bands are *not*
  stored — the optical provider derives them at query time from V + colors,
  reproducing the remote `LandoltCatalog` derivation byte-for-byte
  (`B = V + (B-V)`, `U = B + (U-B)`, `R = V − (V-R)`,
  `I = ((R − (R-I)) + (V − (V-I)))/2`, including the remote's U-error-from-V-R
  quirk). `native_id` is the star designation (e.g. `TPHE A`, `92 309`).
  The remote VizieR provider targets **II/183A = Landolt 1992**, so for a strict
  backend parity comparison query the explicit `1992` release.
- **Stetson** stores U/B/V/R/I with per-band counts and quality columns. The
  `Star` id is unique only *within* a cluster (matching the remote
  `col_mapping['id'] = 'Star'`); `native_id` therefore repeats across clusters,
  and the `cluster` column (indexed) supports field-name filtering. Missing
  bands stay missing; partial-band stars keep the bands they have.

### Troubleshooting malformed `.dat` files

Parsers stream rows and **never silently skip** bad input: a line that cannot be
parsed (bad coordinate, wrong field count, …) is counted in
`ParseStats.malformed` and the first 20 examples are retained on the ingestion
run's `detail.malformed_examples`. Rows that parse but fail a DB-side check
(null id, out-of-range RA/Dec, null cluster) are marked with a `reject_reason`
and copied into a retained `catalog_staging.<family>_<release>_rejects` table
rather than dropped. Duplicate native ids are expected for Stetson (per-cluster
`Star`) and reported as INFO; Landolt designation duplicates are a WARNING.

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
| `families` | List catalog families |
| `discover` | Discover local source releases |
| `import <family> <release>` | Full ingest (discover→stage→load→validate→ready[→activate]); registers the family and release itself |
| `validate <family> <release>` | (Re)validate a release |
| `activate` / `deactivate` | Atomically (de)activate a release |
| `releases` / `history` | List releases / ingestion history |
| `cone <family>` | Cone search (`--radius-deg/-arcmin/-arcsec`, `--release`, `--limit`, `--order-by`) |
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
- Where a release's published row count is known (`ReleaseDef.approx_row_count`),
  an import landing grossly short of it **warns** and will not auto-activate
  without `--allow-warnings`. This is the guard against a truncated download: a
  short read of a multi-GB catalog still parses cleanly.

See [docs](../../../docs) and the module docstrings for details.

---

## Release policy

Releases are a **deployment mechanism, not a science dimension.** They exist so a
DR upgrade is a safe, atomic, reversible operation — not so that callers can pick
which data release to calibrate against.

- **One active release per family** (enforced by a partial unique index).
  Consumers never name a release; they get the active one. A DR upgrade is one
  `skycat activate`, invisible to every consumer.
- **Retain at most the previous (superseded) release** for rollback and for
  re-deriving old calibrations. `remove-release` anything older once the new DR
  has served for an agreed soak period. Do not plan for N simultaneously-live
  DRs.
- **`--release` is an ops and parity-testing affordance** — rolling back, or
  comparing the local store against a remote provider that mirrors an older
  release (VizieR serves Landolt 1992 while 2009 is active). It is **not**
  exposed through the public API. The active release id may be *returned* for
  reproducibility; if a science case ever genuinely needs pinned releases, add it
  then.

Blue/green is the point: stage DR11 beside a live DR10, validate it fully,
activate atomically, and roll back by re-activating the superseded release. For a
store that feeds photometric calibration, a botched import must never degrade the
pipeline.

---

## Adding a catalog family

Six touch points, each small and each in the obviously-named place. There is
deliberately **no** plugin descriptor or registration framework — the cost of a
new family is low enough that indirection would cost more than it saves.

Using Tycho-2 as the worked example:

1. **`registry/catalog_defs.py`** — a `FamilyDef` with one `ReleaseDef` per data
   release. Set `data_table`, the `source_subdir` / `data_globs` the discovery
   step looks for, and `approx_row_count` (the published size; validation warns
   when an import lands below 90% of it — see *Ingestion lifecycle*).
2. **`models/tycho2.py`** — a typed model, `LIST (release_id)`-partitioned.
   Declare `native_id` / `ra_deg` / `dec_deg` explicitly (they vary per family —
   APASS native ids are `String(64)`, VSX's are `String(32)`), and take `geom`
   from `models/mixins.geom_column()`, which every family must share. Typed
   columns for principal magnitudes, errors and coordinates; per-release oddities
   go in the `extra` JSONB rather than becoming new columns. Unit-suffixed names
   (`johnson_v_mag`, `ra_err_arcsec`) — the repo convention. Copy the shape of
   `models/apass.py` and register it in `models/__init__.py`.
3. **`ingestion/parsers/tycho2.py`** — a streaming parser (subclass the base in
   `parsers/base.py`) yielding row tuples in a fixed column order. It must stream,
   never materialize: these files run to hundreds of millions of rows. Register
   the `source_format` key in `parsers/__init__.py`.
4. **A migration** — `skycat/migrations/versions/000N_tycho2.py`, creating the
   partitioned parent + the generated `geom` column. Copy the shape of
   `0002_apass.py`; reuse `GEOM_GENERATED_EXPR` rather than re-typing the
   expression.
5. **`validation/tycho2.py`** *(optional)* — family-specific staging checks
   (implausible magnitudes, required bands), registered in
   `validation/__init__.py`'s `_FAMILY_VALIDATORS`. A family with no entry simply
   gets no extra checks. The catalog-independent ones (coordinate ranges, null
   ids, row counts, spatial index) come free from `validation/common.py`.
6. **Tests + the family table at the top of this README.** Commit a small sample
   fixture under `tests/data/` and add the family to the `imported` fixture in
   `tests/conftest.py`; the existing spatial/cone/crossmatch tests then cover it.

Nothing in the generic engine — discovery, checksums, COPY into staging,
validate-and-mark, the detached partition build, the atomic `ATTACH` swap, the
registry lifecycle — needs to change. That is the whole point of it being
generic.

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

> ⚠️ **Never point the integration tests at a catalog database you care about.**
> The `imported` fixture runs `initialize_catalog_database()` and then
> `import_release(..., replace=True, force=True)` for every family — against a
> provisioned store (e.g. the Compose DB on 5433, which may hold a real 128M-row
> APASS DR10) that **overwrites real releases with six-row samples**. Use a
> throwaway database, as below.

```bash
# unit tests (no DB needed)
pytest packages/py/skycat/tests -q -m "not postgis"
```

```bash
# integration tests: disposable PostGIS on a port of its own (not 5433)
docker run -d --rm --name skycat-test-pg \
  -e POSTGRES_USER=catalog_admin -e POSTGRES_PASSWORD=catalog \
  -e POSTGRES_DB=catalogs -p 127.0.0.1:5434:5432 \
  --tmpfs /var/lib/postgresql/data \
  skycat-postgres:latest

export SKYCAT_DB_HOST=127.0.0.1 SKYCAT_DB_PORT=5434 SKYCAT_DB_NAME=catalogs
export SKYCAT_DB_BOOTSTRAP_USER=catalog_admin SKYCAT_DB_BOOTSTRAP_PASSWORD=catalog
export SKYCAT_DB_ADMIN_USER=catalog_owner    SKYCAT_DB_ADMIN_PASSWORD=catalog
export SKYCAT_DB_INGEST_USER=catalog_ingest  SKYCAT_DB_INGEST_PASSWORD=catalog
export SKYCAT_DB_READER_USER=catalog_reader  SKYCAT_DB_READER_PASSWORD=catalog
export SKYCAT_DB_USER=catalog_reader         SKYCAT_DB_PASSWORD=catalog

skycat init                          # roles + PostGIS + migrations
pytest packages/py/skycat/tests -q   # fixtures import the sample releases

docker stop skycat-test-pg           # tmpfs + --rm: nothing survives
```

Integration tests (marker `postgis`) use a real PostgreSQL/PostGIS database
(never SQLite) for anything touching PostGIS, schemas, roles, COPY, partitioning,
or spatial indexes. They are **skipped automatically** when no catalog DB is
reachable — so an unset `SKYCAT_DB_*` gives you the unit suite, not a failure.

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

**Skycat has no consumers yet** — the optical-pipeline integration is a separate,
forthcoming PR. This section describes the interface consumers will use, so the
first one does not invent its own.

Use `CatalogReader`, not the query functions directly. It owns a pooled reader
engine, caches each family's active release (60s TTL — activation is rare), and
applies a default `statement_timeout` so one pathological query cannot wedge a
worker:

```python
from skycat import CatalogReader

reader = CatalogReader.from_env()          # hold this at app / worker scope
stars = reader.cone("apass", ra, dec, radius_arcmin=12,
                    order_by="johnson_v_mag", limit=500)   # brightest first
hits = reader.crossmatch("vsx", [(id_, ra, dec), ...], radius_arcsec=5)
row = reader.lookup("apass", "090-0000001")
```

**Pass `order_by` whenever you also pass `limit`.** The default ordering is by
angular separation, so a capped cone returns the stars nearest the *centre* — in
a dense field that is a different, fainter star set than the brightest N, and for
photometric calibration it biases the fit. Ordering by a magnitude column
(ascending = brightest) is what nearly every consumer actually wants.

**Direct DB vs the public API.** Anything in-cluster and Python (the pipeline,
schedulers, workers) uses this package directly over the `catalog_reader` role —
no HTTP hop, pooled connections. Anything remote or non-Python (browsers, the
SDKs, SkyNodes at telescope sites) goes through the public API. All SQL and
PostGIS stays inside this package; consumers speak (family, ra, dec, radius,
band, order, limit).

The underlying functions (`cone_search`, `lookup_native_id`, `batch_crossmatch`)
remain available and accept a caller-managed `engine=`, but `CatalogReader` is
the supported entry point.

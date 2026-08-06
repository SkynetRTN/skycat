# Operations

Running a catalog store: what each destructive command actually destroys, how to
watch an import that has been going for two hours, and how to rotate credentials
without dropping queries.

## What each destructive command destroys

Skycat has guards on all of these — reserved database names are refused, active
releases are protected, production-like targets need an override, and `import`
prints its target before loading. The guards are good at preventing the *wrong
target*. They cannot tell you that you picked the wrong *command*, and the names
are close enough that people do.

Read down the column for the command you are about to run:

| | `clean-staging` | `remove-release` | `import --replace` | `reset --force` | `docker volume rm` |
|---|---|---|---|---|---|
| **Role used** | ingest | ingest | ingest | bootstrap | — (host) |
| `catalog_staging` tables | **dropped, all** | untouched | its own recreated | **dropped (schema)** | gone |
| Retained `*_rejects` tables | **dropped, all** | untouched | its own recreated | **dropped (schema)** | gone |
| Production partitions | untouched | **that release's, dropped** | **that release's, replaced** | **all dropped** | gone |
| Other releases' partitions | untouched | untouched | untouched | **all dropped** | gone |
| `catalog_release` rows | untouched | **that one, deleted** | that one, updated | **all dropped** | gone |
| `ingestion_run` / `validation_summary` | untouched | **that release's, cascade-deleted** | new rows added | **all dropped** | gone |
| `catalog_family` rows | untouched | untouched | that one, updated | **all dropped** | gone |
| Alembic version table | untouched | untouched | untouched | **dropped** | gone |
| Roles and grants | untouched | untouched | untouched | untouched | gone |
| The `catalogs` database | untouched | untouched | untouched | untouched | gone |
| Docker volume | untouched | untouched | untouched | untouched | **deleted** |
| Source files under `SKYCAT_DATA_ROOT` | untouched | untouched | untouched | untouched | untouched |
| **Recovery** | re-import | re-import | previous partition is gone after the swap | `init` + `migrate` + re-import everything | restore a backup |

Notes that the table cannot hold:

**`clean-staging` deletes your evidence.** It drops *every* table in
`catalog_staging`, and that includes the retained `<family>_<release>_rejects`
tables — the rows that failed validation, kept deliberately so someone can find
out why. Export what you need before running it. It is the safest command here
in terms of production data and the easiest one to regret.

**`remove-release` deletes the provenance too.** The registry row goes, and the
`ingestion_run` and `validation_summary` rows cascade with it. The source
checksum, the row counts, the importer version, the malformed-line examples — all
of it. If the release is evidence for anything, export the registry query in
[PROVENANCE.md](PROVENANCE.md) first. `--force` additionally allows removing the
**ACTIVE** release, which leaves the family with **no active release**: default
queries then fail rather than falling back to an older one. That is deliberate —
silently serving different data is worse — but it means `--force` here is an
outage, not an inconvenience.

**`import --replace` is not destructive until the swap succeeds.** The
replacement is built as a detached table while the current partition keeps
serving reads; only the final metadata-only swap drops the old one. A failure
anywhere before the swap leaves the previous partition in place and still
serving. After the swap the old partition is gone — a "rollback" means importing
the old source again. `--force` is additionally required when the release is
ACTIVE, and such an import *stays* ACTIVE throughout: there is no window where
the family has no active release.

**`reset --force` is a development command.** It drops all three schemas
CASCADE, which takes the Alembic version table with them, so recovery is `init`,
`migrate`, and re-import every release. It refuses production-like targets
(a host or database name containing `prod`, `production`, `staging`, or `live`)
unless `--allow-production` is also given, and it refuses reserved PostgreSQL
database names outright. It does **not** drop the database, the volume, or the
source files.

**`deactivate` is not on this table but belongs in the same thought.** It moves
the ACTIVE release to SUPERSEDED without touching a row of data. The family then
has no active release and default queries fail until something is activated.
Reversible with `activate`; the partition never moved.

`docker compose down` never removes `catalog_postgres_data` — deleting the
volume is always an explicit `docker volume rm skycat_catalog_postgres_data`.

### Before running any of them

```bash
uv run skycat config              # confirm the target host/port/database
uv run skycat releases            # confirm which release is ACTIVE
uv run skycat sizes               # confirm the thing you are about to drop is the size you expect
```

`skycat config` redacts the password and prints the resolved target. A
surprising number of destructive mistakes are a stale `SKYCAT_DB_PORT` in the
shell, and this is the two-second check that catches it.

## Watching an import

An APASS DR10 import runs for hours. Three signals, in increasing order of
usefulness:

**1. The row-count callback.** `skycat import` prints to stderr every million
rows loaded:

```
importing apass/dr10 -> 127.0.0.1:5433/catalogs (source: /srv/agents/catalogs)
  loaded 1,000,000 rows…
  loaded 2,000,000 rows…
```

This covers Phase A (the `COPY` into staging) only. It goes quiet during the
Phase B1 build — transform, primary key, index replication, `ANALYZE` — which on
a large family is most of the wall clock. **Silence after the last row count is
normal**, and is the single most common reason someone kills a healthy import.

**2. Structured events.** The runner logs one event per phase boundary on the
`skycat.ingestion` logger. Fields are attached under a single `skycat` key on
the `LogRecord`, so a JSON formatter can emit them typed:

```python
import json, logging

class SkycatJson(logging.Formatter):
    def format(self, record):
        payload = getattr(record, "skycat", {"message": record.getMessage()})
        return json.dumps({"ts": record.created, "level": record.levelname, **payload})

handler = logging.StreamHandler()
handler.setFormatter(SkycatJson())
logging.getLogger("skycat.ingestion").addHandler(handler)
logging.getLogger("skycat.ingestion").setLevel(logging.INFO)
```

| Event | Emitted | Key fields |
|---|---|---|
| `import.started` | before any database work | `family`, `release`, `target`, `source_dir`, `source_files`, `source_bytes`, `checksum`, `checksum_mode`, `replace`, `force` |
| `import.skipped` | idempotent no-op | `release_id`, `state`, `reason` |
| `phase_a.completed` | staging loaded and validated | `staging_table`, `parsed`, `loaded`, `rejected`, `malformed`, `validation_status` |
| `phase_b1.started` | detached build begins | `building`, `release_id`, `run_id` |
| `phase_b1.completed` | build indexed, analyzed, validated | `rows`, `validation_status` |
| `phase_b2.swapped` | partition attached | `partition` |
| `import.completed` | release finalized | `state`, `activated`, `imported`, `rejected`, `validation_status`, `partition` |
| `import.failed` | any failure | `error`, `run_id` |

With a plain `logging.basicConfig()` the same fields render into the message
text, so nothing is lost without a JSON formatter.

Two gaps to know about: `phase_a.completed` fires only after the whole `COPY`
finishes, and nothing is emitted *inside* the Phase B1 build. The events tell
you which phase you are in, not how far through it.

**3. The database, from another session.** The most reliable view, because it
does not depend on holding the import's terminal:

```sql
-- Which imports are in flight, and since when
SELECT f.slug, r.name, r.state, r.import_started_at,
       now() - r.import_started_at AS elapsed
FROM catalog_registry.catalog_release r
JOIN catalog_registry.catalog_family f ON f.id = r.family_id
WHERE r.state = 'staging';

-- Rows landed in staging so far (Phase A progress, exact)
SELECT count(*) FROM catalog_staging.apass_dr10_stg;

-- Rows in the detached build (Phase B1 progress)
SELECT count(*) FROM catalog_data.apass_source_r3_incoming;

-- What the server is actually doing right now
SELECT pid, state, wait_event_type, wait_event,
       now() - query_start AS running, left(query, 120) AS query
FROM pg_stat_activity
WHERE datname = 'catalogs' AND state <> 'idle'
ORDER BY query_start;
```

The staging table is named `<family>_<release>_stg`, lowercased with
non-alphanumerics replaced by `_`; the build table is
`<parent>_r<release_id>_incoming`. Both are visible to `catalog_reader` only for
`catalog_data`; reading `catalog_staging` needs the ingest role.

An import that has been in `staging` for more than six hours is reported by
`skycat health` as a failed `no_stuck_imports` check.

**Afterwards**, the record is in the registry:

```bash
uv run skycat releases apass       # state, validation, row count, partition
uv run skycat history apass        # every run: status, stage, loaded, rejected, timings
uv run skycat validate apass dr10  # re-run production validation on the partition
```

and the malformed-line examples are on the run row:

```sql
SELECT detail -> 'malformed'          AS malformed,
       detail -> 'malformed_examples' AS examples
FROM catalog_registry.ingestion_run
ORDER BY started_at DESC LIMIT 1;
```

### When an import fails

The release is marked FAILED with the error and a truncated traceback in
`failure_detail`, the run row records the same, and — this is the part that
matters — **staging is left behind on purpose**. Phase A commits before the
transform runs, so the loaded rows and the retained `*_rejects` table survive a
Phase B failure and can be queried directly. Do not run `clean-staging` until
you have finished diagnosing.

A FAILED release cannot activate, and if the failed import was a `--replace` of
an ACTIVE release, that release is **still ACTIVE and still serving its old
partition**. Nothing is degraded; the rebuild simply did not happen.

## Rotating credentials

Four identities, rotated independently. PostgreSQL does not terminate existing
sessions when a role's password changes, so a rotation does not interrupt
in-flight queries — pooled connections keep working until they are recycled, and
reconnect with whatever the application's environment says at that moment. The
risk is not the database; it is a process holding a stale password in its
environment and only discovering it at the next reconnect.

Rotation is `ALTER ROLE ... WITH LOGIN PASSWORD`, which is exactly what `skycat
init` does — its role statements are idempotent, so re-running it with new
passwords in the environment *is* the rotation:

```bash
export SKYCAT_DB_BOOTSTRAP_USER=catalog_admin SKYCAT_DB_BOOTSTRAP_PASSWORD=...
export SKYCAT_DB_ADMIN_PASSWORD=<new-owner-password>
export SKYCAT_DB_INGEST_PASSWORD=<new-ingest-password>
export SKYCAT_DB_READER_PASSWORD=<new-reader-password>

uv run skycat init      # re-applies roles, passwords, grants; non-destructive
```

`init` re-applies grants as well as passwords, so it is also the repair path for
a permission that drifted.

Order matters, and it is the reverse of what feels natural — rotate the role
with the fewest, most controlled consumers last:

| Role | Consumers | Rotate |
|---|---|---|
| `catalog_reader` | every query client, `CatalogReader` pools, dashboards, ad-hoc `psql` | first, and expect to have missed one |
| `catalog_ingest` | import/activate/validate jobs and the maintenance commands | between imports, never during one |
| `catalog_owner` | migration jobs only | any time no migration is running |
| bootstrap / DBA | `skycat init` only | last; it is the credential that can fix the others |

Per-role notes:

- **`catalog_reader`.** The one with unknown consumers. A `CatalogReader` holds
  a pooled engine at worker scope; existing connections survive, but any
  connection recycled after `SKYCAT_DB_POOL_RECYCLE` (default 300s) reconnects.
  Update every consumer's environment *before* the `ALTER ROLE`, then restart
  or let the pools recycle. Confirm with `skycat health`, which reports
  `credentials_accepted` for the identity it connected as.
- **`catalog_ingest`.** Rotating mid-import breaks the import at its next
  connection — and the runner deliberately uses a *separate* connection for
  registry bookkeeping, so a rotation can break the failure recorder as well as
  the loader. Check for in-flight imports first (`state = 'staging'`, above).
- **`catalog_owner`.** Owns every object. Rotating it is safe outside a
  migration; rotating it *during* one risks a half-applied migration, which is
  the worst state in this system.
- **Bootstrap.** Not a Skycat-managed role — it is the container's
  `POSTGRES_USER` locally, or a DBA account on a provisioned host. Rotate it
  through whatever provisions it, and keep one working copy until the others are
  confirmed: it is the only identity that can create roles and reapply grants.

After any rotation:

```bash
uv run skycat health          # exercises the configured identity end to end
uv run skycat migrate-status  # exercises the admin identity
uv run skycat releases        # exercises the reader identity
```

If a role's password is lost entirely, the bootstrap identity can reset it —
`skycat init` with the new value in the environment. If the *bootstrap*
credential is lost, this package cannot help: recover it through the platform
that provisions PostgreSQL.

### Where the credentials live

| Environment | Location |
|---|---|
| Local Compose | `infra/docker/.env.secrets`, git-ignored, seeded from `.env.secrets.example` |
| Host CLI | the operator's shell environment (`SKYCAT_DB_*`) |
| Kubernetes | a Secret supplying `SKYCAT_DB_*` to the Jobs — see `infra/kubernetes/deploy/base/jobs/` |
| CI | workflow-level `env:` with throwaway values against an ephemeral service container |

The example env files carry documented development-only credentials. They are
allow-listed in `.gitleaks.toml` by exact value, so replacing them with anything
real makes the secret scanner fail — which is the intended behaviour, not an
obstacle to work around.

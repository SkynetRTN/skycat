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
| `catalog_release` rows | untouched | **that one, deleted** | that one, updated once the swap succeeds | **all dropped** | gone |
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
[provenance.md](../guides/provenance.md) first. `--force` additionally allows removing the
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
the family has no active release. The registry row is not touched before the
swap either — its provenance, counts and state keep describing the partition on
disk — so a failed `--replace` is a no-op on the registry as well as on the data.
See "When an import fails" below.

**`import --activate` is safe to re-apply.** If the source checksum already
matches an imported release, Skycat does not rebuild it. An already-ACTIVE
release reports `activated=True` and exits 0; a matching READY or SUPERSEDED
release is activated when validation passed, or when warning-level validation is
explicitly allowed with `--allow-warnings`. Warnings without
`--allow-warnings` still exit 1 because the command did not activate.

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
Reversible with `activate`; the partition never moved. Long-lived readers should
call `CatalogReader.invalidate()` after a planned `activate`, `deactivate`, or
`remove-release` so they pick up the change immediately. Query calls still
re-check cached release handles before use, so missing that invalidation raises
a clear `CatalogQueryError` rather than returning a false empty result.

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
| `phase_b2.lock_wait` | swap could not acquire the family-parent lock before `lock_timeout` | `attempt`, `max_attempts`, `lock_timeout_ms`, `retry`, `parent` |
| `phase_b2.swapped` | partition attached | `partition` |
| `import.completed` | release finalized | `state`, `activated`, `imported`, `rejected`, `validation_status`, `partition` |
| `import.failed` | any failure | `error`, `run_id` |
| `import.record_failed` | **ERROR** — the failure recorder could not write | `error`, `run_id` |

`import.record_failed` is the only ERROR in the table and the only event that
means something is missing from the database rather than describing something in
it: the import failed *and* the attempt to record that failure also failed, so
the registry does not know. See "When an import fails" below.

With a plain `logging.basicConfig()` the same fields render into the message
text, so nothing is lost without a JSON formatter.

Two gaps to know about: `phase_a.completed` fires only after the whole `COPY`
finishes, and nothing is emitted *inside* the Phase B1 build. The events tell
you which phase you are in, not how far through it.

`phase_b2.lock_wait` means the final metadata swap was queued behind an open
reader transaction on the family parent. Skycat sets `SET LOCAL lock_timeout`
for that transaction only (`SKYCAT_DB_LOCK_TIMEOUT`, default 5000 ms), rolls the
attempt back on timeout, and retries with backoff. During a retry the old
partition remains attached and serving. If the bounded retries are exhausted,
the import fails cleanly; closing the blocking reader and re-running the import
will rebuild from the retained source/staging state.

**3. The database, from another session.** The most reliable view, because it
does not depend on holding the import's terminal:

```sql
-- Which imports are in flight, and since when
SELECT f.slug, r.name, r.state AS release_state, ir.stage, ir.started_at,
       now() - ir.started_at AS elapsed
FROM catalog_registry.ingestion_run ir
JOIN catalog_registry.catalog_release r ON r.id = ir.release_id
JOIN catalog_registry.catalog_family f ON f.id = r.family_id
WHERE ir.status = 'running';

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

**Query the run, not the release.** A release's `state` describes the partition
that is on disk, for the whole import: a `--replace` of the ACTIVE release reads
`active` throughout (it is still serving its old partition), and a `--replace` of
a SUPERSEDED one reads `superseded`. That is deliberate — it is what keeps a
failed rebuild from demoting a release whose data it never touched — and it means
the release row is not where an in-flight import shows up. `ingestion_run` is
per-attempt: `status = 'running'` with the phase in `stage`.

The staging table is named `<family>_<release>_stg`, lowercased with
non-alphanumerics replaced by `_`; the build table is
`<parent>_r<release_id>_incoming`. Both are visible to `catalog_reader` only for
`catalog_data`; reading `catalog_staging` needs the ingest role.

An ingestion run still `running` after six hours is reported by `skycat health`
as a failed `no_stuck_imports` check.

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

**Validation warnings can be intentional activation gates.** Invalid coordinate
rows are rejected before production and retained in the `*_rejects` table, but
they still make the import `passed_with_warnings`. So does a high rejected-row
fraction: more than 20% of staged rows rejected means the source may be a wrong
file, shifted columns, or a changed upstream format. Either case imports the
valid rows and leaves the release READY, but `import --activate` will not make
it active unless you rerun with `--allow-warnings` after reviewing the rejects.

### When an import fails

**Staging is left behind on purpose.** Phase A commits before the transform
runs, so the loaded rows and the retained `*_rejects` table survive a Phase B
failure and can be queried directly. Do not run `clean-staging` until you have
finished diagnosing.

**The release keeps describing what is on disk.** Source location, checksum,
size, expected and imported row counts, `production_table` and
`import_started_at` are written only after the Phase B2 swap succeeds. A failed
`--replace` therefore leaves every one of them describing the partition that is
still serving, so the provenance query in
[provenance.md](../guides/provenance.md) stays true through a failure, and a
re-import of the *changed* source is not mistaken for a no-op. (Idempotency keys
on `source_checksum`, so a checksum on the row is by construction one that was
successfully imported.)

**The release's state moves only if there was nothing behind it.** A release
that already had a built partition — ACTIVE, READY or SUPERSEDED — comes out of a
failed import in the state it went in. Phase B1 builds detached, so that
partition was never touched: an ACTIVE release is still serving it and a
SUPERSEDED one can still be activated for rollback. Only a release with nothing
built (REGISTERED, or a previous FAILED) is marked FAILED, and a FAILED release
cannot activate. Nothing is degraded; the rebuild simply did not happen.

**What is recorded, and where:**

| | Written | Notes |
|---|---|---|
| `ingestion_run.status` | `failed` | Per-attempt. Always the first place to look. |
| `ingestion_run.message` | the error | |
| `ingestion_run.detail` | error, truncated traceback, and the attempted source's location / checksum / size / mode | The only record of which tree the failed attempt was reading |
| `catalog_release.failure_detail` | the same error and traceback | Set on every failure, including one that left the state alone |
| `catalog_release.state` | `failed` **only** if nothing was built | See above |

```sql
-- Releases with a recorded failure. `failure_detail` is a real SQL NULL after a
-- clean import, so this predicate means what it says.
SELECT f.slug, r.name, r.state, r.failure_detail ->> 'error' AS error
FROM catalog_registry.catalog_release r
JOIN catalog_registry.catalog_family f ON f.id = r.family_id
WHERE r.failure_detail IS NOT NULL;
```

On a database written by a version before this behaviour existed, clearing the
field stored the JSON scalar `'null'` rather than SQL NULL, so the predicate
above matched every release that had ever been imported. No migration corrects
those rows; either add `AND r.failure_detail IS DISTINCT FROM 'null'::jsonb` to
the predicate, or clean them once:

```sql
UPDATE catalog_registry.catalog_release
   SET failure_detail = NULL
 WHERE failure_detail = 'null'::jsonb;
```

The same vintage could strand a release in `staging` — the state an import used
to take before touching any data. Nothing assigns it now, and `activate` refuses
it. If the release's partition is intact (`SELECT count(*)` on its
`production_table`), the repair is a one-off `UPDATE ... SET state = 'superseded'`;
otherwise re-import it.

**If the failure recorder itself fails**, none of the above is written and the
run row stays `running`. That is what the `import.record_failed` ERROR on the
`skycat.ingestion` logger is for — it carries the recorder's own exception. The
recorder connects independently of the loader (its own short-lived engine, no
statement timeout, `pool_pre_ping` on), so this needs the database itself to be
unreachable or the ingest credentials to have stopped working, not merely the
condition that killed the import. `skycat health`'s `no_stuck_imports` check is
the backstop, and it waits six hours.

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
  connection. The failure recorder then builds its *own* engine from the same
  environment, so it survives anything that was specific to the loader's
  connection — but not a password the running process still holds a stale copy
  of, which breaks both. When that happens it says so: an `import.record_failed`
  ERROR on the `skycat.ingestion` logger, and a run row left `running` for
  `no_stuck_imports` to find. Check for in-flight imports first
  (`status = 'running'`, above).
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

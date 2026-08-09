---
status: open
reviewed: 2026-08-07
branch: docs/code-review-audit
authority: code-inspection (skycat @ 7e7cf2d, origin/dev) + ruff/pyright/pytest gates + full PostGIS suite and twelve reproductions against a throwaway PostgreSQL 16 / PostGIS 3.5 on 127.0.0.1:5435
implementation: not-started
---

# Skycat code review, August 2026

A second full review of the standalone package, the day after the
[design review](archive/design-review.md) whose fourteen findings all landed.
That review was about missing scaffolding — guides, contracts, an ADR, a
`--require-postgis` escape hatch. This one is about the code: what the ingestion
runner, the release state machine, the query path, the guards, and the migration
environment actually do when something goes wrong.

The gates are clean and the happy paths are correct. Every finding below is a
failure path, and twelve of the eighteen were reproduced against a live
database rather than inferred.

## 1. Verdict

| ID | Area | Severity | Summary | Action | Effort |
|---|---|---|---|---|---|
| F1 | ingestion / provenance | **high** | A failed `--replace` writes the new source's checksum onto the release row, so the next plain `import` is silently skipped and the registry describes a source that was never loaded | Write provenance columns only after the swap succeeds; make the idempotency check require a *successful* import of that checksum | M |
| F2 | CLI / exit codes | **high** | `import <fam> <rel> --activate` exits 1 when the release is already imported and ACTIVE — the documented idempotent re-run fails, and the K8s Job with it | Treat "already imported and already ACTIVE" as activated; activate a skipped READY release when `--activate` is given | S |
| F3 | CLI / error contract | **high** | Every command emits a raw Python traceback on an unreachable database, wrong credentials, out-of-range RA/Dec, or `discover` with an unknown family; the resulting exit 1 collides with the "operational failure" code | Extend `_FriendlyGroup` to `sqlalchemy.exc.*`, `ValueError`, `MissingRowError`, `PostgisUnavailableError`; map connectivity/credential failures to 2 | M |
| F4 | migrations / models | **high** | `env.py` sets `include_schemas=True` with no object filter and the ORM omits every data-table index and the one-active-release index; `alembic revision --autogenerate` emits a 518-operation migration that drops every release partition and PostGIS's `tiger`/`topology` schemas | Add `include_object`/`include_name` filters; declare the missing indexes on the models; add a drift test | M |
| F5 | config / migrations | **high** | An admin password containing any character URL-encoding escapes (`@`, `!`, `#`, space) makes `init` / `migrate` / `migrate-status` die with a `configparser` `ValueError` | Pass the URL through `Config.set_main_option` percent-escaped, or bypass `file_config` entirely | S |
| F6 | release state machine | medium | A failed `--replace` of a READY or SUPERSEDED release strands it in STAGING/FAILED even though its partition is intact and indexed, deleting the documented rollback path with no CLI way back | Move to STAGING only after the first destructive step, or add a state-repair command | M |
| F7 | ingestion / locking | medium | Phase B2 sets no `lock_timeout`: the swap waits unboundedly for any open reader transaction, and while queued its ACCESS EXCLUSIVE request blocks every *new* read of the family | `SET LOCAL lock_timeout` on the B2 transaction, retry with backoff | S |
| F8 | config / roles | medium | `SKYCAT_DB_STATEMENT_TIMEOUT` is applied to every role, so bounding reader queries also caps every ingest `COPY` and every migration | Make the timeout per-role, or exempt INGEST/ADMIN/BOOTSTRAP | S |
| F9 | ingestion / observability | medium | The failure recorder uses the same connection identity that just failed and swallows its own exception, so "a failure is always recorded" does not hold | Use an independent short-lived engine with no statement timeout; log the recorder's own failure | S |
| F10 | query API | medium | `cone_search` raises bare `ValueError` and raw `sqlalchemy.exc.*` for caller-input errors that `api-stability.md` promises as `CatalogQueryError` | Wrap coordinate validation, limit validation, and quality-filter type mismatches | S |
| F11 | query path | medium | A `ResolvedRelease` is never re-checked against the registry, so a stale 60 s cache entry — or an explicitly named STAGING/FAILED release — returns `[]`, indistinguishable from an empty cone | Validate the resolved release's existence/state; raise rather than return empty | M |
| F12 | validation | medium | Nothing checks the *fraction* of rejected rows, and `ra_range`/`dec_range` are declared CRITICAL but hard-coded `passed=True` | Add a reject-rate warning; make the two coordinate checks report honestly | S |
| F13 | registry data | low | `release.failure_detail = None` writes JSONB `'null'`, not SQL `NULL`, so `WHERE failure_detail IS NOT NULL` matches every release ever imported | `JSONB(none_as_null=True)` on the column | XS |
| F14 | parsers | low | The APASS DR10 parser silently drops any line whose first token does not start with a digit, without counting it in `ParseStats` | Count non-header non-numeric lines as malformed | XS |
| F15 | maintenance | low | `remove_release` derives the parent table by string-splitting the partition name instead of reading `pg_inherits` | Read the parent from `pg_inherits`, which the same function already queries | XS |
| F16 | docs / code drift | low | The release-state diagram omits the `READY → STAGING` and `SUPERSEDED → STAGING` arcs the runner takes, and "Phase B1 takes no lock on the parent" is literally untrue (it holds ACCESS SHARE) | Correct both statements | XS |
| F17 | ingestion | low | `_replicate_parent_indexes`' `(UNIQUE\s+)?` capture group is unreachable — `pg_get_indexdef` emits `CREATE UNIQUE INDEX`, never `CREATE INDEX UNIQUE` | Delete the group, or fix it to match reality | XS |
| F18 | test coverage | low | `tests/test_docs.py`'s `DOCS` list omits `operations/ci.md` and `operations/release.md`, so their CLI examples are unchecked despite CLAUDE.md's claim | Add both to `DOCS` | XS |

**Overall.** Skycat is in good shape. The architecture is coherent, the
documentation matches the code far more closely than is normal, the type and
lint gates are genuinely at zero rather than suppressed, and the detached-build
mechanism does hold the properties it claims on the success path — verified
directly (F7's reproduction incidentally shows the swap is metadata-only once
the lock is granted, and `test_active_reimport_stays_active_and_swaps` proves
the ACTIVE self-loop). What is thin is the failure half: the runner writes
provenance before it has earned it, the state machine has a one-way door into
STAGING, the CLI's error contract stops at four exception types, and nothing in
the test suite exercises an import that fails after the database has been
touched. None of these are architectural; all of them are the same omission
repeated — the code was written for the path where the next step works.

## 2. What was verified

Everything below was run on this branch at commit `7e7cf2d`, with a throwaway
`skycat-postgres:latest` (PostgreSQL 16 + PostGIS 3.5) container on
`127.0.0.1:5435`, tmpfs-backed, torn down afterwards. Nothing touched the
Compose stack on 5433.

| Gate | Command | Result |
|---|---|---|
| Lint | `uv run ruff check skycat tests` | `All checks passed!` |
| Types | `uv run pyright skycat` | `0 errors, 0 warnings, 0 informations` |
| Unit suite | `uv run pytest tests -q -m "not postgis"` | `122 passed, 57 deselected in 0.70s` |
| Full suite | `uv run pytest tests -q --require-postgis` | **`179 passed in 3.12s`** |
| Bootstrap | `uv run skycat init` | PostGIS 3.5.3, migrated to `0006` |

Beyond the documented gates:

- **Migration round trip.** `alembic upgrade head` → `downgrade base` →
  `upgrade head` against a second scratch database. Clean in both directions;
  the registry index set after the round trip matches a fresh migrate exactly,
  including the partial unique index. Downgrades work, and nothing in CI runs
  them.
- **Autogenerate drift.** `alembic revision --autogenerate` against the
  migrated database, as both `catalog_owner` (fails: `permission denied for
  schema tiger`) and the bootstrap superuser (succeeds, 518 operations). This
  is F4.
- **`--require-postgis` in its failure direction.** Pointed at a dead port it
  prints the documented single message and exits 4 (pytest's usage error) with
  `no tests ran`, rather than 57 skips. `SKYCAT_REQUIRE_POSTGIS=1` behaves
  identically, and the `trylast` interaction the conftest docstring describes
  holds: `-m "not postgis"` with the variable set runs the unit suite
  (`122 passed, 57 deselected`) instead of erroring. It does what it claims.
- **Twelve reproductions**, each noted in its finding: F1, F2, F3 (four
  separate commands), F4, F5 (unit and end-to-end), F6, F7, F8, F9, F10, F11,
  F13.

**Not verified.** No performance measurement was attempted against anything
larger than the six-row fixtures, so nothing here says whether
`docs/operations/performance.md` targets are met — F7's timings measure lock
waits, not query latency. The Compose stack, the container workflows, and the
release workflow were read, not executed. No concurrency testing was done on
`activate_release` under two simultaneous writers (only the single-transaction
`IntegrityError` the existing test covers), so the "one active release per
family" invariant is verified at the index level and inferred at the
application level. F12, F14, F15, F16, F17 and F18 were found by reading and
are not reproduced.

## 3. Findings

### F1 — A failed `--replace` poisons the source checksum, and the next import silently no-ops (high)

**What the code does.** `import_release` writes the *new* source's provenance
onto the release row and commits it before any data work begins
(`skycat/ingestion/runner.py:309-323`):

```python
release.source_location = str(discovered.source_dir)
release.source_format = rel_def.source_format
release.source_checksum = checksum
release.source_size_bytes = discovered.total_bytes
...
meta.commit()
```

If the release was ACTIVE, its state is deliberately left ACTIVE
(`runner.py:319`), and the failure handler correspondingly declines to mark it
FAILED (`runner.py:628-630`). So after a failed `--replace --force`, the row
says: state ACTIVE, `production_table` = the old partition, `imported_row_count`
= the old count, `source_checksum` = **the new source's**.

The idempotency check at the top of the next import
(`runner.py:279-300`) tests exactly `source_checksum == checksum and state in
(ACTIVE, READY, SUPERSEDED) and production_table`. All three now hold.

**Why it is wrong.** `docs/guides/provenance.md` has a section titled "Proving a
release matches an upstream snapshot" whose entire method is reading
`source_location`, `source_checksum`, `source_size_bytes`,
`imported_row_count` and `production_table` off this one row. After the
sequence below, the first three describe one tree and the last two describe a
different one, and the doc's own stated invariant — "`parsed = imported +
rejected` should hold" — is broken with no signal. `docs/guides/provenance.md`
also names the manifest hash as "what idempotency runs on: a re-import of an
unchanged release is skipped rather than repeated", which is the mechanism that
then hides the problem. `docs/operations/runbook.md:203-205` says of a failed
`--replace` of an ACTIVE release: "Nothing is degraded; the rebuild simply did
not happen." The data is indeed intact; the provenance record is not.

**Reproduced.** Throwaway DB, apass/DR10 ACTIVE and imported from a good source:

```
$ skycat import apass dr10 --replace --force --source-dir <bad-source>
Error: Production validation failed: 0 rows in catalog_data.apass_source_r2_incoming
EXIT=1

$ skycat import apass dr10 --source-dir <bad-source>
apass/DR10: parsed=0 loaded=0 rejected=0 imported=6
  state=active validation=passed_with_warnings activated=False table=catalog_data.apass_source_r2
  skipped: already imported (matching checksum); use --replace to force
EXIT=0
```

The registry row afterwards:

```
name                | DR10
state               | active
source_location     | .../scratchpad/baddr10      <- never imported
source_checksum     | 3f0e6c84…                   <- the bad source's
imported_row_count  | 6                           <- the good source's count
import_started_at   | 2026-08-07 10:56:05+00      <- later than…
import_completed_at | 2026-08-07 10:55:02+00      <- …the completion it claims
```

An operator re-running the import after fixing whatever they think went wrong
gets exit 0 and the word "imported". Nothing anywhere says the live rows came
from a different tree.

**Fix.** Two changes, both small. Write `source_*`, `expected_row_count` and
`import_started_at` to a staging area (or to the `IngestionRun` row, which is
already per-attempt) and copy them onto the release only in the finalize block
at `runner.py:525-564`, alongside `production_table` and the row counts. Then
the idempotency check compares against a checksum that, by construction, was
successfully imported. Second, the incoherent-timestamp case disappears for
free once `import_started_at` moves with the rest.

### F2 — `import --activate` exits 1 on an already-active release (high)

**What the code does.** The idempotency branch returns a report with
`activated=False` (`runner.py:286-300`) — it never sets it, even when
`release.state` is already `active`. The CLI then treats that as a failure of
intent (`skycat/cli/main.py:320-324`):

```python
if activate and not report.activated:
    raise click.ClickException(
        f"{family}/{release} imported but NOT activated: ...")
```

**Why it is wrong.** `docs/reference/api-stability.md:52` makes this a
contract: "`0` | The command did what it said. An `--activate` that exits 0
*did* activate." The converse is what the K8s Job reads. Here the release *is*
active and serving, and the command exits 1.

`infra/kubernetes/deploy/base/jobs/skycat-ingest.yaml:42` runs exactly
`["import", "apass", "dr6", "--activate"]`, and its header comment tells the
operator to "Edit the family / release args and apply on demand". Re-applying
an unchanged Job — the ordinary way to retry after a node eviction or a
mistaken edit — now produces a Failed Job against a perfectly healthy release.

The READY variant is worse in a different way: an unchanged, already-imported
READY release skips, is never activated, and exits 1, so `--activate` cannot
activate it at all without a full `--replace` re-import (hours, for APASS).

**Reproduced.**

```
$ skycat import apass dr10 --replace --force --activate --allow-warnings
apass/DR10: … state=active … activated=True
EXIT=0

$ skycat import apass dr10 --activate --allow-warnings
apass/DR10: … state=active … activated=False
  skipped: already imported (matching checksum); use --replace to force
Error: apass/dr10 imported but NOT activated: already imported (matching checksum); use --replace to force
EXIT=1
```

**Fix.** In the skip branch, set `report.activated = True` when
`release.state == ACTIVE`. When `activate` was requested and the skipped
release is READY or SUPERSEDED, run `activate_release` (subject to the same
validation-status gate as the normal path) instead of returning early. Add both
cases to `tests/test_cli_exit_codes.py`, which is the module that exists for
exactly this class of bug.

### F3 — Every command tracebacks on an unreachable database (high)

**What the code does.** `_FriendlyGroup.invoke` (`skycat/cli/main.py:77-86`)
catches exactly four types: `CatalogConfigError` → 2, and
`CatalogQueryError` / `IngestionError` / `ReleaseStateError` → 1. Everything
else escapes Click and reaches the interpreter's default handler.

**Why it is wrong.** CLAUDE.md states the convention as "CLI errors are
messages, not tracebacks", and `api-stability.md:48-54` makes the three-code
taxonomy a contract that a Kubernetes Job reads. The uncaught set includes the
most common operator errors:

- `sqlalchemy.exc.OperationalError` — wrong port, wrong password, server down.
  `docs/operations/runbook.md:86-87` names a stale `SKYCAT_DB_PORT` as the cause
  of "a surprising number of destructive mistakes"; that is the exact input that
  produces a 30-line traceback.
- `ValueError` from `validate_radec` (`skycat/spatial/cone.py:87-91`) — a
  mistyped `--ra`.
- `ValueError` from `discover_one` (`skycat/ingestion/discovery.py:161`) — an
  unknown family or release passed to `skycat discover`.
- `ValueError` from `float(row[1])` in the crossmatch CSV reader
  (`cli/main.py:554`) — a malformed input file.
- `IntegrityError` from a concurrent `activate` losing the partial unique index
  race, `MissingRowError`, `PostgisUnavailableError`,
  `DriverConnectionError`, and `configparser`'s `ValueError` from F5.

All of them exit 1, which the contract reserves for operational failures. A
Job cannot distinguish "the database is unreachable" (a configuration problem,
code 2) from "validation warnings blocked activation" (code 1).

**Reproduced**, four separate commands:

```
$ skycat releases                       # SKYCAT_DB_PORT=5999
…
sqlalchemy.exc.OperationalError: (psycopg.OperationalError) connection failed: … Connection refused

$ skycat cone apass --ra 400 --dec 0 --radius-deg 0.1
…
ValueError: RA out of range [0, 360): 400.0

$ skycat discover bogus x
…
ValueError: Unknown family 'bogus'

$ skycat migrate-status                 # admin password contains '@'
…
ValueError: invalid interpolation syntax in 'postgresql+psycopg://catalog_owner:p%40ss%21word@…'
```

Only `skycat health` degrades gracefully, because it catches broadly on purpose
(`skycat/health.py:70-74, 167-169`).

**Fix.** Add to `_FriendlyGroup.invoke`: `sqlalchemy.exc.OperationalError` and
`sqlalchemy.exc.DBAPIError` → a `_ConfigException` (code 2) with the psycopg
message only, not the SQL; `ValueError`, `MissingRowError`,
`PostgisUnavailableError`, `DriverConnectionError` → `click.ClickException`
(code 1). Keep the traceback available behind an env var or `--debug` so
diagnosis is not lost. The mapping is a stable surface, so the change belongs
with a docs update (see the action plan, phase 4).

### F4 — `alembic revision --autogenerate` produces a migration that drops every catalog partition (high)

**What the code does.** `skycat/migrations/env.py:81-86` configures
autogenerate with `include_schemas=True` and no `include_object` or
`include_name` filter. The models declare no indexes at all on the four data
tables (`skycat/models/apass.py:36-39` and its three siblings carry only
`schema` and `postgresql_partition_by`), and `CatalogRelease.__table_args__`
(`skycat/models/registry.py:79-82`) omits `uq_active_release_per_family`, which
exists only in `skycat/migrations/versions/0001_initial_registry_postgis.py:121-124`.

**Why it is wrong.** Two distinct problems that compound.

The drift is real on its own: `CatalogBase.metadata` is not an accurate
description of the schema. The partial unique index that enforces the central
"one active release per family" invariant is invisible to it, as are all ten
data-table indexes, and the four registry index names in the migrations
(`ix_catalog_release_family_id`) do not match what the metadata's naming
convention would produce (`ix_catalog_registry_catalog_release_family_id`).

`include_schemas=True` with no filter turns that drift into a loaded gun.
Autogenerate reflects *every* schema in the database, which on a PostGIS
install means `tiger`, `tiger_data`, `topology`, and `public`, plus every live
release partition, every retained `*_rejects` table, and every staging table.

**Reproduced.** As `catalog_owner`, autogenerate simply fails:

```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.InsufficientPrivilege) permission denied for schema tiger
[SQL: … cast(%(seqname)s as regclass)] [parameters: {'seqname': 'tiger.faces_gid_seq'}]
```

As the bootstrap superuser it succeeds, and writes a 518-operation migration
whose upgrade begins:

```python
op.drop_table('geocode_settings_default')
op.drop_index(op.f('idx_tiger_edges_the_geom_gist'), table_name='edges', postgresql_using='gist')
op.drop_table('edges')
op.drop_table('topology', schema='topology')
op.drop_index(op.f('apass_source_r2_incoming_geom_idx'), table_name='apass_source_r2', schema='catalog_data')
op.drop_table('apass_source_r2', schema='catalog_data')          # the ACTIVE APASS release
op.drop_table('apass_dr10_stg', schema='catalog_staging')
op.drop_table('landolt_source_r5', schema='catalog_data')
op.drop_table('stetson_stetsonglobs_rejects', schema='catalog_staging')
…
```

together with `Detected removed index 'uq_active_release_per_family'` and a
rename of all four registry indexes.

`docs/guides/add-family.md` never mentions autogenerate — it tells contributors
to hand-write `op.execute` for the partitioned parent — so this needs a
contributor to reach for the standard Alembic tool and commit the result
without reading it. That is a plausible mistake, and nothing catches it:
`tests/test_migration_graph.py` checks only that the graph has one head, that
every revision imports, and that the chain reaches base. A 518-op
data-destroying revision satisfies all three.

**Fix.** Add an `include_object` (or `include_name`) callback to
`env.py`'s two `context.configure` calls that admits only the three catalog
schemas, and within `catalog_data` only the parent tables (a partition matches
`_r\d+$`, a build table `_incoming$`). Declare the missing indexes on the
models so the metadata is honest — including
`Index("uq_active_release_per_family", "family_id", unique=True,
postgresql_where=text("state = 'active'"))` on `CatalogRelease`. Then add a
`postgis`-marked drift test that runs `alembic.autogenerate.compare_metadata`
against a freshly migrated database and asserts the diff is empty; that test is
what makes the fix stay fixed.

### F5 — A password with a special character breaks `init` and `migrate` (high)

**What the code does.** `CatalogDatabaseConfig.url()` percent-encodes the
password (`skycat/config.py:84`, `quote(self.password)`).
`make_alembic_config` then hands that URL to Alembic
(`skycat/database/migrate.py:31`):

```python
cfg.set_main_option("sqlalchemy.url", config.url())
```

`Config.set_main_option` writes through `configparser.ConfigParser.set`, whose
`BasicInterpolation.before_set` rejects any `%` that is not `%%` or `%(name)s`.
Every percent-escape the URL builder produces is exactly that.

**Why it is wrong.** It is not an exotic input. `quote()` escapes everything
outside `[A-Za-z0-9_.-~/]`, so `@`, `!`, `#`, `:`, `$`, `&`, `+`, a space, and
`%` itself all trigger it — which is most of what a password generator emits.
It takes out `skycat init` (at step 6, `database/init.py:92`), `skycat migrate`,
`skycat migrate-status`, the `skycat-migrate` Kubernetes Job, and the
`migrations_current` health check. `docs/operations/runbook.md:216-227`
documents credential rotation as "re-run `skycat init` with new passwords in
the environment", which is the command this breaks.

CI never sees it: `.github/workflows/ci.yml:42-50` uses `catalog`,
`catalog_owner_pw`, `catalog_ingest_pw` — all `[A-Za-z0-9_]`.

**Reproduced**, both directly and end to end:

```
>>> make_alembic_config(CatalogDatabaseConfig(user="catalog_owner", password="p@ssw0rd!"))
ValueError: invalid interpolation syntax in
  'postgresql+psycopg://catalog_owner:p%40ssw0rd%21@127.0.0.1:5435/catalogs' at position 36
```

and, with `catalog_owner`'s real password set to `p@ss!word` in the container,
`skycat migrate-status` dies with the same `ValueError` from
`configparser.py:374`.

**Fix.** Either escape for the config layer at the boundary —
`cfg.set_main_option("sqlalchemy.url", config.url().replace("%", "%%"))`,
which is what Alembic's own template does — or stop routing the URL through
`file_config` at all and pass an `Engine` to `command.upgrade` via
`cfg.attributes["connection"]`. The first is one line; the second is cleaner
and also removes the `alembic.ini`-presence branch at `migrate.py:29`. Add a
unit test with a password containing `@`, `!`, `%`, and a space — no database
needed, so it lands in the fast suite.

### F6 — A failed `--replace` destroys a good release's rollback status (medium)

**What the code does.** Before any data work, a non-ACTIVE release is moved to
STAGING (`runner.py:319-320`). If the import then fails, the recorder moves it
to FAILED (`runner.py:628-630`) — or, if the recorder itself fails (F9), leaves
it in STAGING. `activate_release` accepts only READY and SUPERSEDED
(`skycat/registry/releases.py:107-113`).

**Why it is wrong.** The whole point of retaining a SUPERSEDED release is
rollback: `docs/reference/architecture.md:163` draws `SUPERSEDED → ACTIVE:
activate (rollback)` and the release policy in the vault says to "keep at most
the previous superseded release for rollback and reproducibility". A failed
attempt to replace that release — which never touched its partition, by design
— strips it of the state that makes it rollback-able. The data is intact and
unreachable.

There is no CLI path back. `skycat activate` refuses; `skycat deactivate` only
acts on ACTIVE. Recovery is a manual `UPDATE catalog_registry.catalog_release`,
which `api-stability.md:80-83` explicitly says is unsupported, or a full
re-import.

**Reproduced.** apass/DR6 was SUPERSEDED with an intact six-row partition. A
`--replace --force` that failed early left:

```
$ skycat releases apass
apass    DR10       active      … rows=6 catalog_data.apass_source_r2
apass    DR6        staging     … rows=6 catalog_data.apass_source_r1

$ skycat activate apass DR6
Error: Release 'DR6' is 'staging'; only a READY or SUPERSEDED release can be
activated (it must be fully imported, indexed and validated).
EXIT=1

$ psql -c "SELECT count(*) FROM catalog_data.apass_source_r1"
 6
```

**Fix.** The state move exists to make an in-flight import visible to
`skycat health`'s `no_stuck_imports` check and to the runbook's
`state = 'staging'` query. Both of those can key off the `IngestionRun` row
instead, which already exists, is per-attempt, and carries `stage`. Failing
that: record the pre-import state on the run row and restore it in the failure
handler when the partition was never swapped (which the handler can know — the
swap is the last thing before finalize). Either way, a release whose partition
was not touched must come out of a failed import in the state it went in.

### F7 — Phase B2 has no lock timeout, and while it waits the family is unreadable (medium)

**What the code does.** Phase B2 (`runner.py:495-506`) opens a transaction and
immediately issues `ALTER TABLE … DETACH PARTITION`, which takes ACCESS
EXCLUSIVE on the parent. The ingest engine sets no `lock_timeout` and — unless
`SKYCAT_DB_STATEMENT_TIMEOUT` is set globally, which F8 shows you cannot afford
— no `statement_timeout` either.

**Why it is wrong.** The comment above the block calls B2 "a short
transaction", and `architecture.md:240-243` says the swap is "a short transaction
that drops the old partition, renames, and `ATTACH PARTITION`s the new one".
The *work* is short. Acquiring the lock is not bounded at all, and PostgreSQL's
lock queue means that while the swap waits behind one open reader transaction,
every subsequent read of the family queues behind the swap. A single
`psql` session someone left in `BEGIN` takes the family's whole read path down
for as long as it stays open.

**Reproduced.** One reader holding an open transaction over
`catalog_data.apass_source`, a B2-shaped DETACH/ATTACH, and one ordinary
reader arriving two seconds later with a 4 s `statement_timeout`:

```
[  0.0s] long reader: holds ACCESS SHARE, count=12
[  1.0s] B2-like DETACH: requesting ACCESS EXCLUSIVE on the parent…
[  3.0s] new reader: SELECT on the family parent…
[  6.0s] pg_stat_activity: (577, 'Client', 'idle in transaction', 'SELECT count(*) FROM catalog_data.apass_source')
[  6.0s] pg_stat_activity: (578, 'Lock',   'active', 'ALTER TABLE catalog_data.apass_source DETACH PARTITION catal')
[  6.0s] pg_stat_activity: (579, 'Lock',   'active', 'SELECT count(*) FROM catalog_data.apass_source')
[  7.0s] new reader: BLOCKED, QueryCanceled: canceling statement due to statement timeout
[ 12.0s] long reader: committed
[ 12.0s] B2-like DETACH: acquired after 11.0s
```

The innocent third reader was killed by its own timeout without ever touching
a lock the swap owned. With `CatalogReader`'s 30 s default that is a 30 s
outage per queued query; with no timeout it is indefinite.

**Fix.** `conn.execute(text("SET LOCAL lock_timeout = '5s'"))` as the first
statement of the B2 transaction, and retry the whole B2 block a bounded number
of times with backoff, logging a `phase_b2.lock_wait` event each time. Failing
to acquire is safe — the old partition keeps serving and the `_incoming` table
is still there — so a retry loop costs nothing and converts an outage into a
delay. Document the behaviour in the runbook's "Watching an import" section
alongside the existing pg_stat_activity query.

### F8 — One statement timeout is shared by every role, including ingest (medium)

**What the code does.** `SKYCAT_DB_STATEMENT_TIMEOUT` is read once into
`CatalogSettings.base` (`skycat/config.py:182`), and `config_for(role)` returns
`self.base.with_credentials(...)` (`config.py:218-222`) — the timeout rides
along unchanged for BOOTSTRAP, ADMIN, INGEST and READER alike.
`create_catalog_engine` then installs it on every connection
(`skycat/database/engine.py:68-74`).

**Why it is wrong.** `skycat/client.py:12-15` states the reason the setting
exists: "`SKYCAT_DB_STATEMENT_TIMEOUT` exists but nothing sets it, so one
pathological query can monopolize a worker process indefinitely." An operator
who follows that reasoning and sets it in the deployment's shared environment
also caps every `COPY` of a 128 M-row catalog and every Alembic migration at
the same value. A 30 s cap kills an APASS DR10 import in the first minute.

**Reproduced**, with `SKYCAT_DB_STATEMENT_TIMEOUT=1`:

```
$ skycat import apass dr6 --replace --force
Error: (psycopg.errors.QueryCanceled) canceling statement due to statement timeout
[SQL: CREATE UNLOGGED TABLE catalog_staging."apass_dr6_stg" ( … )]
```

At 50 ms the six-row fixture squeaked through; at 1 ms it did not. The six-row
fixture only demonstrates that the timeout *is* installed on the ingest
connection — on real data the statement that exceeds it is the `COPY` (Phase A)
or the `INSERT … SELECT` (Phase B1), each of which runs for minutes to hours on
APASS DR10 against any timeout an operator would set to bound a reader.

**Fix.** Make the timeout role-scoped: either read
`SKYCAT_DB_READER_STATEMENT_TIMEOUT` alongside the generic one, or have
`config_for` clear `statement_timeout_ms` for INGEST / ADMIN / BOOTSTRAP unless
a role-specific value is set. Add the variable to the README's configuration
table (a stable surface, so the docs change ships in the same commit) and a
unit test that `config_for(INGEST).statement_timeout_ms is None` while
`config_for(READER)` keeps it.

### F9 — The failure recorder can fail silently, and does (medium)

**What the code does.** The recorder (`runner.py:615-643`) opens a new
`Session` on the *same* engine, built from the same `ingest_cfg` that the
failing work used, and wraps everything in:

```python
except Exception:  # noqa: S110, BLE001 -- recording the failure must not mask the original error
    pass
```

**Why it is wrong.** CLAUDE.md and `architecture.md:243-244` both say
"Registry and run rows are written on a separate connection, so a failure is
always recorded." It is a separate *connection*, but not an independent one:
same host, same role, same credentials, same statement timeout, same pool.
Every cause that can break the loader — a revoked grant, a rotated password
(which `runbook.md:250-253` already flags), a dead server, an exhausted disk,
or the statement timeout in F8 — breaks the recorder too. And when it does,
nothing is logged: `pass` swallows it, the `import.failed` event has already
been emitted with only `error=` and no indication that the registry write went
missing, and the release is stranded mid-flight.

**Reproduced** as a side effect of F8: with a 1 ms timeout, apass/DR6's
`state = 'staging'` commit at `runner.py:323` succeeded, the staging
`CREATE TABLE` failed, and the recorder's `UPDATE` hit the same timeout and was
swallowed. DR6 was left in STAGING, not FAILED — which is also how F6's
reproduction arose. `skycat health`'s `no_stuck_imports` check would catch it
after six hours; nothing catches it sooner.

**Fix.** Build the recorder's engine separately, from a config with
`statement_timeout_ms=None` and `pool_pre_ping=True`, disposed immediately
after. Keep the broad catch — the reasoning for it is sound — but log the
recorder's own exception on the `skycat.ingestion` logger at ERROR with an
`import.record_failed` event, so the gap is visible rather than inferred. Then
either soften the docs' "always recorded" to "recorded unless the database
itself is unreachable", or say what the operator should do when it is.

### F10 — Caller-input errors escape as `ValueError` and `sqlalchemy.exc.*` (medium)

**What the code does.** `cone_search` calls `validate_radec`
(`skycat/query/cone.py:191`), which raises a plain `ValueError`
(`skycat/spatial/cone.py:87-91`). `.limit(limit)` at `cone.py:222` passes a
negative limit straight to PostgreSQL. `_quality_clause` (`cone.py:61-75`)
validates the column and the operator, binds the value — and lets a type
mismatch between the two surface as a driver error.

**Why it is wrong.** `api-stability.md:31` lists `CatalogQueryError` as a
stable type that "keeps … the situations that raise them", and
`CatalogReader.cone()` is the supported entry point. A service that maps
`CatalogQueryError` to HTTP 400 turns each of these into a 500 instead. The
`QualityFilter` docstring specifically invites untrusted input: "Callers may
therefore build these from untrusted input."

**Reproduced.**

```
1. cone_search(..., resolved=<release_id 99999>)                 -> []            (see F11)
2. QualityFilter("johnson_v_mag", "<", "'; DROP TABLE x; --")
   -> sqlalchemy.exc.ProgrammingError: operator does not exist: double precision < character varying
3. QualityFilter("extra", "=", "x")
   -> sqlalchemy.exc.ProgrammingError: operator does not exist: jsonb = character varying
4. cone_search(..., limit=-1)
   -> sqlalchemy.exc.DataError: LIMIT must not be negative
5. cone_search(..., order_by="native_id")
   -> CatalogQueryError: Order-by column 'native_id' is not numeric      (correct)
```

Note case 2: the injection payload *was* bound as a parameter, exactly as
`tests/test_quality_filter.py` asserts. The allow-listing is sound; only the
error type is wrong.

**Fix.** Raise `CatalogQueryError` from `validate_radec`'s call sites (keeping
`ValueError` in `skycat/spatial`, which is dependency-free and shared with the
parsers), validate `limit >= 0` in `radius_to_deg`'s neighbourhood, and check
`qf.value`'s Python type against `col.type.python_type` in `_quality_clause` —
the same introspection `_order_by_clause` already does at `cone.py:94-99`.

### F11 — A stale or invalid resolved release returns an empty result, not an error (medium)

**What the code does.** `CatalogReader.active_release` caches a
`ResolvedRelease` for 60 s (`skycat/client.py:128-145`) and `cone_search`
filters on `release_id` alone (`cone.py:206`). `resolve_release`
(`skycat/registry/releases.py:57-70`) applies no state filter, so an explicit
`--release` resolves a STAGING or FAILED release just as happily as an ACTIVE
one. `ResolvedRelease.state` is carried through
(`cone.py:154`) and never read by anything.

**Why it is wrong.** Because release partitions live under one parent, a query
for a `release_id` whose partition no longer exists is not an error — it is a
scan that matches nothing. So:

- Within the 60 s TTL after `remove-release --force` or `deactivate`, a reader
  serves `[]` for every cone. `runbook.md:46-50` says removing the active
  release means "default queries then fail rather than falling back to an older
  one … silently serving different data is worse". Through a warm
  `CatalogReader` they do not fail; they return no stars, which for a
  calibration pipeline is worse than either. (A cold reader, or the CLI, does
  raise `CatalogQueryError` — the gap is the cache, not the resolver.)
- `skycat cone apass --release DR6` against a DR6 stranded in STAGING (F6)
  returns its rows with no indication that the registry considers that release
  mid-import.

**Reproduced.**

```python
ghost = ResolvedRelease("apass", "apass_source", 99999, "ghost", "active")
cone_search(s, "apass", 100.0039, 4.861469, radius_deg=0.5, resolved=ghost)
# -> []
```

and, against the STAGING DR6:

```
$ skycat cone apass --ra 0.000236 --dec 1.886943 --radius-deg 0.1 --release DR6
1 matches
  0020120136           ra=0.000236 dec=1.886943 sep=0.00"
```

**Fix.** Two independent halves. (a) Have `resolve_release_for_query` reject a
release whose state is not one of ACTIVE / READY / SUPERSEDED unless an
explicit opt-in is passed, so `ResolvedRelease.state` stops being dead data.
(b) Give the reader's cache a cheap validation: on a cache hit, the query
already runs; add `AND EXISTS (…)`-style confirmation, or simply catch the
zero-row case and re-resolve once before returning — a stale entry then costs
one extra round trip instead of a wrong answer. Whichever, `invalidate()`
should be documented in the runbook as mandatory after `remove-release` and
`deactivate`, which it currently is not.

### F12 — Nothing warns on the rejected-row fraction, and two CRITICAL checks can never fail (medium)

**What the code does.** `validate_staging_common`
(`skycat/validation/common.py:45-80`) marks bad rows and then reports:

```python
checks.append(Check("ra_range", CRITICAL, True,
                    f"{bad_ra} rows with RA outside [0,360) rejected"))
checks.append(Check("dec_range", CRITICAL, True,
                    f"{bad_dec} rows with Dec outside [-90,90] rejected"))
```

`passed` is the literal `True` in both. `summarize` (`common.py:33-38`) only
looks at `passed` and `level`, so neither check can ever influence the outcome
no matter how many rows were rejected. The only volume guard is
`row_count_vs_expected` in `validate_production` (`common.py:108-117`), a
warning at 90 % of the release definition's `approx_row_count`.

**Why it is wrong.** A source where 40 % of rows have unparseable coordinates
imports, validates `passed`, and activates with `--activate` — no warning, as
long as the remaining 60 % clears the 90 % floor of a deliberately approximate
expected count, which for a growing catalog like VSX it often will. The
rejected count is recorded on the release and the run, but nothing looks at it.
Meanwhile a *single* null `native_id` fails the import outright
(`common.py:55-56` is a real CRITICAL), which is a strange asymmetry: one bad
identifier is fatal, fifty million bad coordinates are not.

Not reproduced — the fixture sources are six rows and one deliberate bad row,
so the ratio is not exercised either way.

**Fix.** Add `Check("reject_rate", WARNING, rejected / max(parsed, 1) <
REJECT_RATE_MAX, …)` with a documented constant next to
`ROW_COUNT_MIN_FRACTION`, which already has the right shape and the right
comment. Make `ra_range`/`dec_range` report `bad_ra == 0` at WARNING level
rather than `True` at CRITICAL — the rows really are rejected, not lost, so a
warning is honest and a critical is not. Both are behaviour changes to
activation gating, so they need a line in the runbook.

### F13 — `failure_detail = None` writes JSON `null`, not SQL `NULL` (low)

`runner.py:322` clears the field at the start of every import. The column is
`JSONB` (`skycat/models/registry.py:123`), and SQLAlchemy's JSON types default
to `none_as_null=False`, so the assignment stores the JSON scalar `null`.

**Reproduced.** After a clean import followed by a failed `--replace` of the
ACTIVE release:

```sql
SELECT failure_detail IS NOT NULL, jsonb_typeof(failure_detail) FROM …;
 t | null
```

The obvious operator query — "which releases have a recorded failure?" — matches
every release that has ever been imported. Fix: `mapped_column(JSONB(none_as_null=True))`.
No migration needed; existing rows can be cleaned with a one-line `UPDATE` in
the runbook, or left alone once the predicate is written as
`failure_detail IS DISTINCT FROM 'null'::jsonb`.

### F14 — The APASS DR10 parser silently skips non-numeric lines (low)

`skycat/ingestion/parsers/apass.py:140-141`:

```python
if not f or not f[0][0].isdigit():
    continue  # header line (e.g. "APASS ID ...")
```

No `stats.record_malformed(line)`. CLAUDE.md's parser convention says
"Malformed lines are counted in `ParseStats` … nothing is silently dropped".
Any corrupted line whose first token starts with a letter or a sign — a
truncated write, a concatenated file with a stray header mid-stream — vanishes
without appearing in `parsed`, `malformed`, or the rejects table. (`str.isdigit()`
also accepts non-ASCII digits, which is harmless here but not intended.)

Fix: recognise the header explicitly (first line, or a token equal to `APASS`)
and count everything else as malformed. The DR6 parser has the same shape at
line 62 for `#` comments, which is a genuine comment convention and fine.

### F15 — `remove_release` reconstructs the parent name by string-splitting (low)

`skycat/ingestion/maintenance.py:113`:

```python
family_def_table = tbl.rsplit("_r", 1)[0]
```

The function is a few lines past a `pg_inherits` query that already knows the
answer. For the four shipped families the split is correct, but it depends on
`production_table` following the `<parent>_r<id>` convention exactly, and it
fails silently — `DETACH PARTITION` against a nonexistent parent — for anything
that does not. Fix: select `inhparent::regclass` in the same query that already
tests attachment.

This path has no test: `test_active_release_deletion_protected` asserts the
refusal and returns before the drop, so the detach/drop/delete sequence is never
executed in the suite.

### F16 — Two documentation claims the code does not hold (low)

`docs/reference/architecture.md:153-168`'s state diagram has no
`READY → STAGING` or `SUPERSEDED → STAGING` arc, but `runner.py:319-320` takes
both on any `--replace`. It shows `SUPERSEDED → ACTIVE` as the only way out of
SUPERSEDED, which is exactly the arc F6 shows can be lost.

`architecture.md:240-241` and CLAUDE.md both say Phase B1 takes "no lock on the
partition parent". `CREATE TABLE … (LIKE catalog_data."<parent>" …)` at
`runner.py:429-432` takes ACCESS SHARE on the parent and holds it for the whole
B1 transaction. That does not block readers, so the *intent* holds and this is
not a correctness defect — but it does mean two concurrent imports of different
releases of the same family will serialise, with the second one's B2 blocked
behind the first one's B1. Worth saying accurately.

### F17 — Dead capture group in the index replicator (low)

`runner.py:173-177` strips the index name with
`r'\bINDEX\s+(UNIQUE\s+)?"?<name>"?\s+ON'`. `pg_get_indexdef` emits
`CREATE UNIQUE INDEX name ON …`, never `CREATE INDEX UNIQUE name ON …`, so the
group never matches. It happens to work — the resulting
`CREATE UNIQUE INDEX ON <child> …` is valid — but the code reads as though it
handles a case it does not. Delete the group.

### F18 — Two stable docs are outside the doc test (low)

`tests/test_docs.py:34-42` lists seven documents. `docs/operations/ci.md` and
`docs/operations/release.md` are not among them, so their `skycat` invocations
(`uv run skycat init` at `ci.md:311`, `skycat --help` at `release.md:110,118`)
are unchecked for command and flag existence and for the group-flag ordering
rule — even though CLAUDE.md says the test asserts "every `skycat`
command/flag … shown in the stable docs", and `docs/README.md`'s own convention
section says "A new page under `guides/`, `reference/`, or `operations/` that
documents CLI or reader surface should be added to `DOCS`". Both pages are
covered by the link test (`_all_markdown`, lines 62-75) but not the CLI test.

Nothing is currently broken — adding both to `DOCS` passes today — so this is
purely preventive. Add them anyway; the point of the test is that nobody
notices the day it stops being true.

### Also noted, below the finding bar

- `looks_production()` is called in exactly one place,
  `database/init.py:144`. CLAUDE.md says it "gates destructive commands",
  plural. `remove-release --force` (drops a live partition and cascades away its
  provenance — an outage, per the runbook) and `clean-staging` (drops every
  retained rejects table) have no production guard. Either add one or narrow the
  CLAUDE.md sentence to name `reset`.
- `assert_not_reserved_database()` *is* on every mutating path — 17 call sites,
  including all four query entry points, the runner, both init paths, the CLI
  session helper, the reader, and both Alembic URL resolutions. Verified by
  grep; no gap found.
- Role selection is correct throughout: reader for `families`/`releases`/
  `history`/`sizes`/queries/health, ingest for import/activate/deactivate/
  validate/clean-staging/remove-release, admin for migrations, bootstrap only
  for `init`/`reset`. The one place a broader role appears is
  `health.py:175`, which falls back from ADMIN to the caller's role when no
  admin credentials are configured — a downgrade, not an escalation, and the
  check simply reports failure if the reader cannot see the version table.
- `CatalogSettings._creds_for` (`config.py:203-216`) falls back to the default
  identity when a role has no credentials. In the documented single-credential
  dev setup the default *is* the reader, so the fallback downgrades; it can only
  escalate if an operator sets `SKYCAT_DB_USER` to a privileged account, which
  the README's configuration table does not suggest. No change recommended,
  but the direction of the fallback is worth one sentence in the docs.

## 4. Gaps in test coverage

Ranked by what a failure would cost, not by how hard the test is to write.

1. **The release state machine has no unit tests at all.** `activate_release`,
   `deactivate_release`, `set_state` and `ReleaseStateError` appear nowhere in
   `tests/` — grep returns zero hits. `tests/test_release_states.py` asserts the
   *enum membership*, not a single transition. The claim CLAUDE.md,
   `architecture.md`, `api-stability.md` and the vault all repeat — "a
   FAILED/incomplete release can never auto-activate" — is enforced by eight
   lines in `releases.py:101-113` that nothing exercises. The one related test,
   `test_prevent_multiple_active`, checks the database index by hand-writing an
   `UPDATE`, bypassing the function entirely. Cost: silent regression of the
   central invariant.
2. **No import failure after the database is touched is ever tested.** The only
   failure test, `test_missing_source_fails_cleanly`, fails in `discover_one`
   before a connection is opened. Nothing covers a Phase A failure, a Phase B1
   validation failure, a Phase B2 failure, the failure recorder, the resulting
   release/run states, `failure_detail`, or the retained staging table the
   runbook tells operators to go read. F1, F6, F9 and F13 all live in that
   untested region, and all four were found by hand in ten minutes.
3. **Idempotency is tested in one direction only.**
   `test_idempotent_reimport_skips` asserts `skipped_reason is not None`. It
   does not assert the exit code, does not pass `--activate`, and does not check
   that the skipped release's provenance still describes the rows on disk. F1
   and F2 both sit exactly there.
4. **The CLI error contract is tested only for `IngestionError`.**
   `test_failed_import_reports_a_message_not_a_traceback` is the whole of it.
   Nothing asserts that a wrong port, a bad password, an out-of-range RA, or an
   unknown family to `discover` produces a message — and F3 shows none of them
   do. This is cheap to fix: `CliRunner` with a bogus port needs no database.
5. **No ORM/migration drift test.** `test_migration_graph.py` checks graph
   shape; nothing compares `CatalogBase.metadata` against a migrated database.
   F4's 518-operation diff would pass CI today.
6. **No test with a realistic password.** Every credential in
   `.github/workflows/ci.yml` and every fixture is `[A-Za-z0-9_]`, which is
   precisely why F5 has survived. A database-free test of
   `make_alembic_config` with `p@ss!word` is three lines.
7. **No concurrency test anywhere.** Not for the B2 swap against an open reader
   (F7), not for two simultaneous `activate_release` calls racing the partial
   unique index, not for a reader's cached release being invalidated
   mid-flight (F11). All three are reproducible with two threads and the
   existing `imported` fixture.
8. **No per-role configuration test.** `test_role_resolution_from_env` checks
   which *user* each role resolves to; nothing checks which *engine settings*
   it inherits, which is where F8 lives.
9. **The rejected-row and malformed-line accounting is asserted at the parser
   level only.** `test_parsers.py` checks `stats.malformed == 1` for Landolt and
   Stetson; nothing asserts what the runner does with `ParseStats`, that
   `malformed_examples` caps at 20, or that the `*_rejects` table contents match
   the rejected count. The integration suite only asserts the tables exist.
10. **`remove_release`'s destructive path is never executed.** The only test
    asserts the refusal. F15 and the cascade behaviour the runbook documents are
    both unverified by the suite.
11. **Downgrades are not in CI.** They work — I ran `head → base → head`
    cleanly — but nothing would notice if `0007` shipped without a
    `downgrade()`.
12. **`--require-postgis` has no regression test of its own.** The flag works —
    verified by hand in both directions and in both spellings (§2) — but the
    suite cannot observe its own collection hook, so a change to
    `pytest_collection_modifyitems` that reintroduced silent skipping would go
    unnoticed until a release was validated against nothing. A subprocess
    `pytest` run against a dead port, asserting the message and a non-zero exit,
    would close it. Lowest cost of everything in this list, and the last one I
    would spend time on.

## 5. Action plan

Six phases. Phases 1–3 carry the four remaining high-severity findings and are
bug fixes to behaviour that is undocumented or actively contradicted by the
docs, so none of them needs a decision record. **Phase 5 changes
documented-stable surfaces and must carry an ADR and a docs update in the same
commit**, per the docs-are-tested convention. Phases 1, 2 and 3 are mutually
independent and can be worked in parallel; 4, 5 and 6 each depend on one of
them.

```
 1 failed imports ──┬── 4 lock/timeout safety
                    ├── 5 stable-surface changes ── (also needs 2)
 2 CLI error paths ─┘
                    └── 6 validation gating + doc accuracy  (needs 1)
 3 migration environment  (independent)
```

### Phase 1 — Make failed imports honest (F1, F6, F9, F13)

*Scope.* Move release provenance writes behind the swap; stop demoting an
untouched release; give the failure recorder an independent connection and a
voice; fix the JSONB null.

*Files.* `skycat/ingestion/runner.py`, `skycat/models/registry.py`,
`skycat/registry/releases.py` (possibly), `docs/operations/runbook.md`
(the "When an import fails" section, which currently overstates what is
recorded, and the credential-rotation note at lines 250-253). The
state-diagram correction (F16) waits for phase 6, because what the diagram
should say depends on what this phase decides about the STAGING transition.

*Tests that must exist first.* A `postgis`-marked module —
`tests/test_import_failures.py` — covering: (a) a Phase B1 validation failure of
an ACTIVE release leaves state ACTIVE, the *old* checksum, and the old
`production_table`; (b) the same failure of a SUPERSEDED release leaves it
SUPERSEDED and still activatable; (c) a subsequent non-`--replace` import of the
changed source is **not** skipped; (d) `failure_detail` is SQL NULL on a clean
import and a real object after a failure; (e) the `IngestionRun` row records
the failure in every case. Plus a unit test that a recorder exception is logged
rather than swallowed.

*Acceptance.* F1's reproduction script exits non-zero on the second import
instead of "already imported". `skycat releases` after a failed replace shows
provenance describing the rows actually on disk. The full suite still passes
with `--require-postgis`.

*Depends on.* Nothing.

### Phase 2 — Restore the CLI error contract, minus the exit-code change (F3 partial, F5, F10, F14, F18)

*Scope.* Extend `_FriendlyGroup` to the uncaught exception families, mapping
all of them to code 1 for now (the 1-vs-2 question is phase 4). Fix the Alembic
URL escaping. Raise `CatalogQueryError` for caller-input errors in the query
layer. Count non-numeric DR10 lines. Add the two missing docs to the doc test.

*Files.* `skycat/cli/main.py`, `skycat/database/migrate.py`,
`skycat/query/cone.py`, `skycat/ingestion/parsers/apass.py`,
`tests/test_docs.py`, `tests/test_cli_exit_codes.py`, `tests/test_parsers.py`,
and whichever docs the widened doc test then flags.

*Tests that must exist first.* Database-free `CliRunner` cases for: unreachable
port, bad password, `cone --ra 400`, `discover bogus x`, and a malformed
crossmatch CSV — each asserting a message, a non-zero exit, and `"Traceback"
not in output`. A `make_alembic_config` test with `p@ss!word`. Query-layer tests
that a negative limit and a type-mismatched `QualityFilter` raise
`CatalogQueryError`.

*Acceptance.* No `skycat` subcommand can produce a traceback for any operator
input reachable from the README. `skycat init` works with a generated password.
`uv run pytest tests -q -m "not postgis"` still runs in under a second.

*Depends on.* Nothing. Can run in parallel with phase 1.

### Phase 3 — Migration environment and model drift (F4, F15, F17)

*Scope.* Filter autogenerate to the catalog schemas and the parent tables;
declare the ten data-table indexes and `uq_active_release_per_family` on the
models so `CatalogBase.metadata` describes the real schema; add the drift test.
While in the same code: read the partition parent from `pg_inherits`, and
delete the dead capture group in the index replicator.

*Files.* `skycat/migrations/env.py`, `skycat/models/registry.py`,
`skycat/models/{apass,vsx,landolt,stetson}.py`,
`skycat/ingestion/maintenance.py`, `skycat/ingestion/runner.py`,
`tests/test_schema_drift.py` (new), `tests/test_integration.py`.

*Tests that must exist first.* A `postgis`-marked drift test that runs
`alembic.autogenerate.compare_metadata` against a freshly migrated database and
asserts an empty diff — the test that would have caught F4 and will catch the
next one. A `remove_release` test that actually reaches the detach/drop/delete
path against a non-active release, asserting the partition is gone, the
registry row is gone, and the `ingestion_run` rows cascaded.

*Acceptance.* `alembic revision --autogenerate` as `catalog_owner` against a
migrated database emits an empty migration and does not touch `tiger`,
`topology`, or any partition. The drift test is in the `required: ci` path
(it needs the `skycat` job's service container, not a new one).

*Depends on.* Nothing. Independent of phases 1 and 2.

### Phase 4 — Lock and timeout safety (F7, F8)

*Scope.* `SET LOCAL lock_timeout` plus bounded retry on the B2 transaction;
role-scoped statement timeouts.

*Files.* `skycat/ingestion/runner.py`, `skycat/config.py`,
`skycat/database/engine.py`, `README.md` (configuration table — a stable
surface, so the doc lands with the code), `docs/operations/runbook.md`
("Watching an import").

*Tests that must exist first.* A `postgis`-marked concurrency test: an open
reader transaction on the family parent, an `import --replace --force` in a
thread, an assertion that the import raises a lock-timeout `IngestionError`
rather than hanging, and that the release is still ACTIVE and queryable
afterwards. A unit test asserting `config_for(INGEST).statement_timeout_ms is
None` while `config_for(READER)` carries the configured value.

*Acceptance.* An import cannot block the family's read path for longer than the
configured `lock_timeout`. `SKYCAT_DB_STATEMENT_TIMEOUT=30000` no longer breaks
an import.

*Depends on.* Phase 1, because the retry loop's failure path exercises the
recorder.

### Phase 5 — Stable-surface changes (F2, F3's exit-code taxonomy, F11)

**This phase changes documented-stable behaviour and needs a decision record
plus a docs update in the same commit.** Three surfaces move:

- **Exit codes.** Connectivity and credential failures become code 2
  (configuration) instead of the accidental 1. `api-stability.md:48-54` must be
  updated in the same commit, and the K8s Job comment with it.
- **`import --activate` semantics.** An already-imported, already-ACTIVE release
  becomes exit 0 with `activated=True`; a skipped READY release is activated
  rather than reported as a failure. This is what the contract already promises
  ("an `--activate` that exits 0 *did* activate"), so the ADR is short, but the
  table in `api-stability.md` gains a row and the runbook gains a paragraph.
- **Query resolution.** Rejecting a STAGING/FAILED release, and re-resolving a
  stale cache entry rather than returning `[]`, changes what
  `CatalogReader.cone()` does for callers who currently get an empty list.
  `api-stability.md`'s "Query row dicts" section and the reader lifecycle
  examples in the README both need a line.

*Files.* `skycat/cli/main.py`, `skycat/ingestion/runner.py`,
`skycat/query/cone.py`, `skycat/registry/releases.py`, `skycat/client.py`,
`docs/decisions/0002-*.md` (new), `docs/reference/api-stability.md`,
`docs/operations/runbook.md`, `README.md`, `tests/test_cli_exit_codes.py`,
`tests/test_client.py`.

*Tests that must exist first.* `--activate` on an already-ACTIVE release exits
0; on a skipped READY release, activates and exits 0; on validation warnings
without `--allow-warnings`, still exits 1 (the existing test must keep passing).
Connectivity failure exits 2. A ghost `ResolvedRelease` raises rather than
returning `[]`. A reader whose cached release is deactivated behind its back
raises on the next call.

*Acceptance.* Re-applying `skycat-ingest.yaml` unchanged against a healthy,
already-imported release produces a Complete Job. `tests/test_docs.py` passes
against the rewritten stability table.

*Depends on.* Phases 1 and 2 — the exit-code taxonomy is only meaningful once
the exceptions are caught at all, and F2's fix touches the same skip branch as
F1's.

### Phase 6 — Validation gating and documentation accuracy (F12, F16)

*Scope.* Add the reject-rate warning and make `ra_range`/`dec_range` report
honestly; correct the release-state diagram and the "no lock on the parent"
sentence.

*Files.* `skycat/validation/common.py`, `docs/reference/architecture.md`,
`docs/operations/runbook.md`, `CLAUDE.md`, `tests/test_integration.py`.

*Tests that must exist first.* A `postgis`-marked test that a source whose
rejected fraction exceeds the threshold produces a `passed_with_warnings`
status and is not activated by `--activate` without `--allow-warnings`, and a
counterpart that a small rejected fraction still passes clean. The existing
`test_short_import_warns_against_the_expected_row_count` must keep passing —
the six-row fixture has one rejected row in seven, so pick the threshold with
that in mind or adjust the fixture deliberately.

*Acceptance.* A source with a high coordinate-rejection rate cannot
auto-activate. `docs/reference/architecture.md`'s state diagram matches the
transitions `runner.py` actually takes, and `tests/test_docs.py` still passes.

*Depends on.* Phase 1 — the validation change alters activation gating, which
phase 1's failure tests assert on, and the diagram correction depends on
whatever phase 1 decides about the STAGING transition.

## 6. Explicitly not findings

These look wrong and are not. Each one is a settled decision with a reason
written down somewhere; re-litigating them costs a reviewer an afternoon.

- **The old partition is gone after the swap, so there is no rollback to the
  previous data.** Deliberate and documented —
  `docs/operations/runbook.md:52-59`, and the vault's
  *Detached Partition Rebuild and Swap*: "A 'rollback' means importing the old
  source again", which is why `docs/guides/provenance.md` treats the source tree
  as something to retain independently. (F6 is about the *registry state* of a
  release whose partition was never touched, which is a different thing.)
- **Index names on a live partition keep an `_incoming` suffix** —
  `apass_source_r2_incoming_geom_idx` on `apass_source_r2`. The runner
  auto-names child indexes so they cannot collide with the parent's, then
  renames only the table; PostgreSQL does not rewrite index names on a table
  rename. Documented in the vault as a key insight, with the instruction to
  match plans on shape, not name.
- **`clean-staging` destroys the retained `*_rejects` tables.**
  `runbook.md:36-40` says so explicitly and calls it "the easiest one to
  regret".
- **`remove-release --force` leaves the family with no active release, and
  default queries then fail.** Deliberate — `runbook.md:46-50`: "silently
  serving different data is worse."
- **A failed import leaves its staging table behind.** By design: Phase A
  commits before the transform so the loaded rows survive for diagnosis
  (`runner.py:16-18`, `runbook.md:194-201`).
- **`catalog_data.release_id` has no foreign key to the registry.** Documented
  in `skycat/models/apass.py:13-16`: attaching a hundred-million-row partition
  would trigger a full FK-validation scan.
- **Registry enum columns are `String`, annotated `Mapped[str]`, and writers
  pass `.value`.** The reasoning is nine lines at the top of
  `skycat/models/registry.py`: a new lifecycle state stays a code change
  instead of a type rewrite across every release row.
- **`QualityFilter` is not an injection surface.** Verified, not assumed: the
  column is looked up in `table.c`, the operator is checked against a six-entry
  allow-list before any SQL is built, and the value arrives as a bound
  parameter — a payload of `'; DROP TABLE x; --` came back as a psycopg type
  error on `$12`, never as SQL. `tests/test_quality_filter.py` covers all three
  paths. Only the *exception type* is wrong (F10).
- **Sexagesimal negative-zero declinations.** `-00 30 00` is handled correctly
  in both fixed-width parsers: Landolt reads the sign from a dedicated byte
  position (`SIGN_BYTE`, `parsers/landolt.py:75`) rather than from `int()`, and
  Stetson tests `ded.strip().startswith("-")` with `abs(int(ded))`
  (`parsers/stetson.py:64-65`). `test_parsers.py:82` pins the `-0.0375` case.
- **`to_float`'s `missing_at_or_above=90.0` default turns a magnitude of 90 into
  NULL.** APASS's sentinel is `99.999` and no real magnitude reaches 90. The
  fields where a large value could be legitimate — the positional RA/Dec errors
  — pass `missing_at_or_above=None` explicitly (`parsers/apass.py:75-76`), and
  every Landolt and Stetson field does the same, with a comment saying why.
- **`nearest_only=True` ignores `max_candidates`.** Documented in
  `batch_crossmatch`'s signature and docstring.
- **The bare query functions have no statement timeout and no release cache.**
  `api-stability.md:33-37`: "`CatalogReader` is the supported entry point … Code
  that calls the bare functions is responsible for all three."
- **Row ordering without an explicit `order_by` is not a contract.**
  `api-stability.md:124-126`.
- **`except Exception` in `skycat/health.py` and `skycat/migrations/env.py`.**
  Both carry a comment and a `per-file-ignores` entry in `pyproject.toml:90-96`,
  which is exactly the process CLAUDE.md prescribes. The only broad catch that
  is *not* justified is the recorder's `pass` in `runner.py:642-643` — see F9.
- **Alembic downgrades.** Not a finding: `upgrade head → downgrade base →
  upgrade head` round-trips cleanly against a real database, and the registry
  index set afterwards is identical. Nothing in CI runs it (§4, item 11), but
  the code is correct.
- **`sqlalchemy.url` in `alembic.ini` is blank and `env.py` reads
  `config.get_main_option("sqlalchemy.url")`.** Not a contradiction of "never
  from `alembic.ini`": the value read there is the one
  `make_alembic_config` set programmatically from `SKYCAT_DB_*` a moment
  earlier. The ini is intentionally empty (`alembic.ini:13-14`). (It is,
  however, the mechanism F5 breaks on.)
- **The `imported` fixture destroys catalog data.** Loudly documented in
  CLAUDE.md, the README, and `tests/conftest.py:30-34`. This review ran it only
  against a tmpfs container on port 5435, created and destroyed for the purpose.

## 7. References

**Code read in full:** `skycat/ingestion/runner.py`, `copy_loader.py`,
`discovery.py`, `maintenance.py`, `parsers/{base,apass,vsx,landolt,stetson}.py`;
`skycat/registry/{releases,families,catalog_defs}.py`;
`skycat/query/{cone,crossmatch}.py`; `skycat/spatial/cone.py`;
`skycat/client.py`; `skycat/config.py`; `skycat/constants.py`;
`skycat/health.py`; `skycat/cli/main.py`;
`skycat/database/{base,engine,init,migrate,orm,postgis,roles}.py`;
`skycat/models/{registry,mixins,apass}.py`;
`skycat/validation/{common,apass,vsx}.py`;
`skycat/migrations/env.py` and `versions/0001`–`0002`.

**Tests read:** `conftest.py`, `test_integration.py`, `test_docs.py`,
`test_cli_exit_codes.py`, `test_migration_graph.py`, `test_release_states.py`,
`test_parsers.py`, plus the test-name inventory of the remaining nine modules.

**Configuration and infrastructure:** `pyproject.toml`, `alembic.ini`,
`Dockerfile`, `.github/workflows/{ci,containers,kubernetes,release,secret-scan,workflow-safety}.yml`,
`infra/docker/skycat-postgresql.dockerfile`,
`infra/kubernetes/deploy/base/jobs/skycat-ingest.yaml`.

**Documentation read in full:** `CLAUDE.md`, `docs/README.md`,
`docs/reference/architecture.md`, `docs/reference/api-stability.md`,
`docs/operations/runbook.md`, `docs/working/README.md`,
`docs/working/archive/design-review.md`.

**Documentation read in part:** `docs/guides/provenance.md` (source layout,
what a release records, checksum modes, proving a snapshot),
`docs/decisions/0001-postgresql-postgis-only.md` (context and the SQLite
alternative), `docs/guides/add-family.md` (the migration section),
`docs/operations/ci.md` and `docs/operations/release.md` (CLI invocations and
job names only). `docs/operations/performance.md` was not read; nothing in this
review makes a performance claim.

**Operational memory:** `wiki/resources/concepts/Detached Partition Rebuild and
Swap.md` and `Catalog Release Lifecycle.md` in full;
`wiki/resources/concepts/Catalog Release Provenance.md` and
`wiki/resources/entities/skycat.md` in part.

**Not read:** `docs/working/remote-catalogs.md` (owned by a concurrent branch),
`docs/working/{ml-capabilities,package-publishing-report}.md` (out of scope).

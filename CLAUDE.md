# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --extra dev                          # install (uv project; no other install path)

uv run ruff check skycat tests               # lint  (CI gate)
uv run pyright skycat                        # types (CI gate)

uv run pytest tests -q -m "not postgis"      # unit suite — no database needed
uv run pytest tests -q                       # full suite — postgis tests SKIP if no DB is reachable
uv run pytest tests -q --require-postgis     # full suite — unreachable DB is a FAILURE
uv run pytest tests/test_parsers.py -q       # one module
uv run pytest tests/test_cli_exit_codes.py::test_withheld_activation_is_not_success -q

uv run skycat <command>                      # the CLI, host-run against SKYCAT_DB_*
```

Plain `pytest` never fails for a missing database — it silently degrades to the unit suite. A green
local run therefore proves nothing about migrations, roles, COPY, partitions, or spatial indexes.
Use `--require-postgis` (or `SKYCAT_REQUIRE_POSTGIS=1`) before claiming a change works end to end;
it aborts collection with one clear message instead of 57 skips, and it is what CI runs.

CI runs these plus a 3.11/3.12/3.13 unit matrix, Alembic graph validation, a wheel-install smoke
test, and workflow/container/manifest/secret checks. `docs/CI.md` maps the workflows to their
required-check names and gives container-based local equivalents for each.

`docs/` also holds the contracts worth reading before changing behaviour: `API_STABILITY.md` (what
may not move), `ADD_FAMILY.md` (the worked version of the six touch points), `PROVENANCE.md`
(source layout, checksum modes), `OPERATIONS.md` (what each destructive command destroys, the
`skycat.ingestion` structured events, credential rotation), `PERFORMANCE.md`, `RELEASE.md`, and
`decisions/` for choices already settled.

### Integration tests destroy catalog data

The session-scoped `imported` fixture (`tests/conftest.py`) runs
`import_release(..., replace=True, force=True)` for every family against whatever `SKYCAT_DB_*`
points at — it will overwrite a real 128M-row APASS DR10 with six-row samples. Never point it at the
Compose database on 5433. Use a throwaway (README "Testing" has the full recipe):

```bash
docker run -d --rm --name skycat-test-pg \
  -e POSTGRES_USER=catalog_admin -e POSTGRES_PASSWORD=catalog -e POSTGRES_DB=catalogs \
  -p 127.0.0.1:5434:5432 --tmpfs /var/lib/postgresql/data skycat-postgres:latest
export SKYCAT_DB_HOST=127.0.0.1 SKYCAT_DB_PORT=5434 SKYCAT_DB_NAME=catalogs
# ... SKYCAT_DB_{BOOTSTRAP,ADMIN,INGEST,READER}_{USER,PASSWORD} + SKYCAT_DB_USER/PASSWORD
uv run skycat init && uv run pytest tests -q
```

Local Compose stack (real dev data, port 5433): `docker compose -f infra/docker/compose.yaml ...`,
with the `skycat-tools` profile for `skycat-init` / `skycat-migrate` / `skycat-import` / `skycat`.

## Architecture

Skycat is a standalone package: its own declarative base and `MetaData`
(`skycat/database/base.py`), Alembic environment (`skycat/migrations`), schemas, PostgreSQL roles,
and config namespace (`SKYCAT_DB_*`). Nothing here is an add-on to a larger service, and no table
may be registered on another application's metadata.

**Three PostgreSQL schemas** (`skycat/constants.py`): `catalog_registry` (families, releases,
ingestion runs, validation summaries), `catalog_data` (one `LIST (release_id)`-partitioned parent
table per family, one partition per release), `catalog_staging` (unlogged bulk-load + retained
rejected rows).

**Family/release model.** `skycat/registry/catalog_defs.py` is the single source of truth for what
catalogs exist — `FamilyDef`/`ReleaseDef` give the source subdir, data globs, parser key, and
`approx_row_count`. There is deliberately **no** plugin/registration framework; adding a family is
six explicit touch points, listed in README "Adding a catalog family". Each family gets its own
scientifically typed table and parser — never a universal row table. Per-release oddities go in the
`extra` JSONB, not new columns.

**Releases are a deployment mechanism, not a science dimension.** One active release per family
(partial unique index). `CatalogReleaseState` walks REGISTERED → STAGING → READY → ACTIVE, with
FAILED/SUPERSEDED as the branches. A failed or incomplete release can never auto-activate.

**Ingestion (`skycat/ingestion/runner.py`)** is the most subtle file in the repo. Phase A streams
parser rows via COPY into unlogged staging and validates there. Phase B1 builds a *detached*
standalone table (transform → PK → replicated parent indexes → ANALYZE → validate), taking no lock
on the partition parent during the multi-minute rebuild. Phase B2 is a short transaction: drop the
old partition, rename, `ATTACH PARTITION`. Preserve those properties when editing — the old release
must keep serving reads until the atomic swap, and a `--replace` of the ACTIVE release stays ACTIVE
throughout. Registry/run rows are written on a separate connection so failures are always recorded.

**Query path (`skycat/query/`)** never filters spatially in Python. Cone search uses
`ST_DWithin`/`ST_Distance` on the GiST-indexed generated `geom geography(Point,4326)` column with
`use_spheroid => false` (spherical, so RA 0/360 wrap and the poles are correct); degrees↔metres uses
`POSTGIS_SPHERE_RADIUS_M`. `geom` comes from `models/mixins.geom_column()` / `GEOM_GENERATED_EXPR` —
identical for every family, never retyped. `native_id`/`ra_deg`/`dec_deg` are declared per family
(their types differ), and there are intentionally no helpers for them.

`CatalogReader` (`skycat/client.py`) is the supported Python entry point: one lazy pooled reader
engine, a 60s active-release cache, a default 30s statement timeout. The bare query functions accept
`engine=`/`resolved=` so the reader can supply both.

**Roles.** `CatalogRole` (bootstrap/admin/ingest/reader/default) maps to concrete DB users through
`CatalogSettings.config_for()`, falling back to the default identity when a role has no credentials.
Pick the narrowest role for new code paths: reader for queries, ingest for imports/activation, admin
for migrations, bootstrap only for `init`.

**Migrations** are a linear numeric chain (`0001`…`0006`); the version table lives in
`catalog_registry`. `migrations/env.py` resolves the URL from `SKYCAT_DB_*` (admin role) or
`-x url=` — never from `alembic.ini`. Catalog data never goes into a migration.

## Conventions

- **Docs are tested.** `tests/test_docs.py` asserts every `skycat` command/flag and every
  `CatalogReader` method/kwarg shown in the stable docs exists, that cited `skycat/*.py` paths
  exist, that every relative markdown link resolves, and that group-level flags (`--json`) are
  written *before* the subcommand — Click rejects them after it. Change a CLI flag or reader
  signature → update the docs in the same commit. `docs/working/` is excluded by design (dated
  planning notes, not current API).
- **Pyright's null-safety rules are errors, the SQLAlchemy-generic ones are warnings**
  (`pyproject.toml` explains the split). `reportOptionalMemberAccess` and friends are at zero;
  fix them with `database/orm.require_row` or an explicit branch, never a suppression. Enum-valued
  registry columns are `String` and annotated `Mapped[str]`; writers pass `.value`.
- **Safety guards are load-bearing.** `assert_not_reserved_database()` before any engine that
  mutates; `looks_production()` gates destructive commands; import prints its target before loading.
- **CLI errors are messages, not tracebacks.** `_FriendlyGroup` maps `CatalogConfigError` → exit 2
  and `CatalogQueryError`/`IngestionError`/`ReleaseStateError` → exit 1. Exit codes are a contract —
  the K8s ingest Job reads them (`--activate` that did not activate must exit non-zero).
- **Parsers stream.** `iter_rows` yields one tuple at a time across files; never materialize a
  catalog. Malformed lines are counted in `ParseStats` (first 20 retained) and DB-rejected rows are
  kept in a `catalog_staging.*_rejects` table — nothing is silently dropped.
- **Ruff's select list is narrow and deliberate**: pyflakes plus `ARG`, `BLE001`, `S110`, `S112`,
  `TRY002`. A blind `except Exception` or bare `pass` is an error, not a style nit; if it is truly
  best-effort, justify it in a comment and add a `per-file-ignores` entry.
- Column names carry units (`johnson_v_mag`, `ra_err_arcsec`). Missing values stay NULL, never 0.
- Prose comments explain *why* a non-obvious choice was made (locking, idempotency, provider parity);
  match that density rather than annotating the obvious.

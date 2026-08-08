---
status: addressed
reviewed: 2026-08-06
addressed: 2026-08-06
authority: code-inspection
---

# Skycat design review

This note reviews the current standalone Skycat package after extraction from
the larger repository. It is intentionally a working document: it names holes,
risks, and follow-up improvements rather than restating the stable design.

> **Status: addressed (2026-08-06).** Every item below has landed. The findings
> are kept verbatim as the record of what was wrong; the table says where each
> one now lives. Two items were resolved differently from the suggestion, and
> one arrived from another branch — both noted.
>
> | # | Item | Where it landed |
> |---|---|---|
> | H1 | PostGIS coverage hard to miss | `--require-postgis` (+ `SKYCAT_REQUIRE_POSTGIS`) in `tests/conftest.py`; CI runs it; README "Release validation" |
> | H2 | Tighten type checking | `database/orm.require_row`, `database/engine.driver_connection`, `Mapped[str]` on registry enum columns; null-safety pyright rules promoted to **error** at zero |
> | H3 | Public stability contract | `docs/reference/api-stability.md` |
> | H4 | New-family developer guide | `docs/guides/add-family.md` |
> | H5 | Source-data provenance | `docs/guides/provenance.md` |
> | M1 | Destructive-command ambiguity | `docs/operations/runbook.md` — one table, plus the two commands that delete diagnostic evidence |
> | M2 | Publishing and versioning | `docs/operations/release.md` + `CHANGELOG.md`, landed on `dev` separately |
> | M3 | Measurable query performance | `docs/operations/performance.md` — targets, conditions, measurement procedure |
> | M4 | Ingestion observability | structured `skycat.ingestion` events at every phase boundary; `docs/operations/runbook.md` documents the fields and how to watch a running import |
> | M5 | Generated artifacts | `.gitignore` extended (source-mirror leftovers, coverage, editor/OS, `catalogs/`) |
> | L1 | PostgreSQL/PostGIS-only ADR | `docs/decisions/0001-postgresql-postgis-only.md` |
> | L2 | Schema diagram | `docs/reference/architecture.md` — ER map + release-lifecycle state diagram |
> | L3 | Reader lifecycle examples | README "Reader lifecycle" |
> | L4 | Credential rotation | `docs/operations/runbook.md` |
>
> **Deviations.** H1 suggested a `make test-postgis` target; a Makefile was not
> added, because `uv` is the project's only entry point and a second one would
> drift. H2 suggested promoting the noisy categories one at a time; the
> null-safety family was promoted together (they share one fix and all reached
> zero together), while the categories that fire on SQLAlchemy generics stay
> warnings with the reasoning recorded in `pyproject.toml`.
>
> **Found while implementing**, outside the review's scope but fixed here:
> `docs/design/skycat-design` had been moved twice and pointed one level above
> the repository root the whole time (that file has since been merged into
> `docs/reference/architecture.md`), and the README documented
> `skycat cone … --json`, which Click rejects because `--json` is a group
> option. `tests/test_docs.py` now has a test for each.

## Summary

Skycat has a sound initial shape for local catalog querying: a bounded Python
package, dedicated PostgreSQL/PostGIS database contract, catalog-specific typed
tables, release activation semantics, read-only query facade, ingestion jobs,
and tests for parser, query, CLI, and PostGIS integration behavior.

The main gaps are around development hardening, operational ergonomics, and
extension mechanics. None block the package from being useful, but they are the
areas most likely to cost time as the package grows beyond the first supported
families.

## High-priority improvements

1. Make PostGIS coverage hard to miss outside CI.

   Local `pytest` skips PostGIS tests when no catalog database is reachable. CI
   starts a PostGIS service and has a reader-role guard, which is good, but local
   development can still get a green run without exercising migrations, roles,
   COPY, partitions, or spatial indexes. Add an explicit documented target such
   as `make test-postgis` or `uv run pytest tests -q -m postgis` plus a CLI
   health preflight. Consider a pytest option like `--require-postgis` that turns
   those skips into failures for release checks.

2. Tighten type checking after the SQLAlchemy model layer settles.

   Pyright currently exits with zero errors but leaves SQLAlchemy-related
   warnings downgraded in `pyproject.toml`. The noisy categories are mostly
   optional ORM lookups and enum/string assignment mismatches in ingestion and
   registry code. Add typed helper functions around required ORM fetches, prefer
   enum values consistently, and raise the highest-value categories back to
   errors one at a time.

3. Clarify the public stability contract.

   `CatalogReader`, the CLI, environment variables, table schemas, registry
   states, and import reports are all practical APIs. The README documents many
   of them, but the package does not yet define which surfaces are stable for
   downstream development. Add an `API stability` section that separates stable
   Python/CLI/config contracts from internal modules that may change freely.

4. Add a first-class "new catalog family" developer guide.

   The README has the needed checklist, but family addition currently requires
   reading models, parsers, registry definitions, migrations, validation, and
   tests together. Add `docs/design/skycat/add-family.md` or a similar guide
   with a minimal worked example, expected parser behavior, migration shape,
   validation requirements, and query exposure requirements.

5. Define release artifact and source-data provenance more rigorously.

   The importer stores source location, checksum, size, modified time, importer
   version, and schema version. That is a good base, but local mirrors of VizieR
   catalogs need repeatable rebuilds. Document the expected source tree layout,
   mirror/download commands, file inventory format, checksum mode tradeoffs, and
   how to prove that a database release corresponds to a specific upstream
   catalog snapshot.

## Medium-priority improvements

1. Reduce operational ambiguity around destructive commands.

   Safety guards exist for reserved PostgreSQL maintenance databases,
   production-like names, active releases, source roots, and Docker volumes.
   The remaining risk is operator confusion about what `reset`,
   `remove-release`, `clean-staging`, `--replace`, and `--force` each destroy.
   Add one table that compares these commands by affected schemas, registry
   rows, production partitions, staging tables, source files, and Docker
   volumes.

2. Add package publishing and versioning documentation.

   The project is versioned as `0.1.0`, but the repo does not yet describe how
   package versions relate to database migrations, importer versions, internal
   schema versions, and Docker image tags. Document the release process before
   external users depend on the package.

3. Make query performance expectations measurable.

   The query layer supports `EXPLAIN`, GiST indexes, KNN-style batch
   crossmatch, nearest-first ordering, and optional magnitude ordering. Add
   benchmark fixtures or an operations note with target latencies for common
   cone sizes, dense fields, high declination, RA wraparound, and batch
   crossmatch sizes.

4. Improve observability around ingestion.

   `ImportReport`, `IngestionRun`, validation summaries, and health checks
   provide the raw state. Follow up with structured log fields, progress event
   expectations, and a documented way to inspect long imports while they are
   running. This matters for large APASS-like or future Gaia/Pan-STARRS imports.

5. Decide how generated bytecode and local artifacts should be handled.

   Local runs create `__pycache__` directories and the package uses Docker
   volumes and work roots. Confirm `.gitignore` covers generated Python,
   database, work, and source-mirror artifacts so future commits stay clean.

## Lower-priority improvements

1. Add a short architecture decision record for PostgreSQL/PostGIS-only scope.

   The current code deliberately avoids SQLite and Python-side spatial fallback.
   Capture that decision so future requests for lighter local modes are judged
   against explicit performance and correctness tradeoffs.

2. Add a schema diagram.

   A compact registry/data/staging diagram would make the release lifecycle
   easier to understand for new contributors and operators.

3. Expand the `CatalogReader` lifecycle examples.

   The reader supports context-manager use, explicit `close()`, cache
   invalidation, explicit-release queries, and engine reuse. Add examples for
   process-scope and short-lived script usage.

4. Document credential rotation.

   Roles are separated cleanly, but the operational docs do not yet describe
   rotating reader, ingest, owner, and bootstrap credentials without disrupting
   active queries.

## Suggested next sequence

1. Add `--require-postgis` or an equivalent test target so release validation
   cannot accidentally skip integration coverage.
2. Fix the highest-volume pyright warnings in ingestion and registry code.
3. Write the add-family guide before adding another catalog family.
4. Define the package/database/image versioning policy.
5. Add ingestion progress and provenance documentation for repeatable catalog
   rebuilds.

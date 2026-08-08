---
status: accepted
date: 2026-08-06
---

# 1. PostgreSQL/PostGIS only — no SQLite, no Python-side spatial fallback

## Status

Accepted. Implemented throughout; this record captures the reasoning so that
future requests for a lighter local mode are judged against it rather than
re-argued from scratch.

## Context

Skycat requires a running PostgreSQL server with PostGIS. There is no embedded
mode, no SQLite backend, and no code path that filters spatially in Python.
That is a real cost, and it shows up in predictable places:

- Unit tests that touch the catalog store cannot run without a database, so 57
  of the tests skip by default and the suite still reports green.
- Contributors must run a container before they can exercise an import.
- A downstream user who wants "just a small local star lookup" gets a
  PostgreSQL dependency.
- CI needs a service container, which makes the deep gate slower and more
  fragile than a pure-Python job.

The obvious mitigation is a fallback: SQLite for small catalogs, or a Python
implementation of cone search over an RA/Dec box for cases where PostGIS is not
available. Both have been considered and rejected.

## Decision

The catalog store is PostgreSQL with PostGIS, always. Spatial filtering happens
in the database, on a GiST-indexed `geography(Point,4326)` column, always.

## Consequences of the alternatives

### A SQLite backend

**Scale.** APASS DR10 is 128.6M rows and VSX is 10.3M. The design centres on
`COPY` into unlogged staging, a detached partition build, index replication, and
an atomic `ATTACH PARTITION` swap. SQLite has none of these. A catalog small
enough for SQLite to handle comfortably is a catalog small enough not to need a
database.

**The release model would not survive.** Releases are `LIST (release_id)`
partitions, and blue/green activation is a metadata-only `DETACH`/`ATTACH` under
a brief lock while the old partition keeps serving reads. SQLite has no
partitioning and no concurrent-writer story. The best available substitute is
copy-and-swap, which loses the property that makes the whole design work: a DR
upgrade that never leaves the family without an active release.

**Two backends means two behaviours.** The moment SQLite exists, every query
needs a second implementation, every migration needs a second dialect, and every
bug report starts with "which backend". Divergence would not be theoretical:
SQLite has no `geography` type, so distances would be computed differently, and
"the same query returns different neighbours on the small backend" is a
correctness failure that would be discovered by a user, in analysis, months
later.

### Python-side spatial fallback

**It is not a fallback, it is a different answer.** `ST_DWithin` with
`use_spheroid => false` computes exact angular separation on a sphere. The
tempting Python substitute — an RA/Dec bounding box plus a haversine filter — is
wrong at both poles (where a fixed RA range spans a vanishing angular distance)
and across RA 0/360 (where the box splits in two). Both are conditions this
package must handle correctly: Stetson's clusters include high-declination
fields, and a cone at `ra = 0.05` is an ordinary query, not an edge case.

**Getting it right in Python does not help.** A correct spherical filter still
has to see the rows to filter them. Without a spatial index that means reading
the partition — 128M rows to answer a 5-arcsec query. The index is the feature;
the maths is the easy part.

**A fallback is used when it matters most.** The path taken when the fast one is
unavailable is the path taken during an incident, on an unfamiliar deployment,
by whoever is on call. A slow, subtly-different query path is worse than a clear
failure to connect, because the failure gets diagnosed and the wrong answer does
not.

## What is done instead

- **PostGIS availability is a checked precondition, not an assumption.**
  `skycat health` reports `postgis_installed` with the extension version, and
  `spatial_index_<family>` per family. `skycat init` installs the extension.
- **The default is honest about being degraded.** Integration tests skip rather
  than pretending to pass, `--require-postgis` turns that into a hard failure
  for release validation, and CI runs with it set — so the deep gate cannot
  silently become a unit run.
- **Getting a database is one command.** A throwaway PostGIS on a tmpfs, with
  the full recipe in [ci.md](../operations/ci.md). It costs about a minute.
- **The unit suite is real.** Parsers, spatial maths, release-state transitions,
  configuration, CLI exit codes, the migration graph, and the documentation
  contract all run with no database at all. Contributors working on those never
  need one.

## Revisiting

This decision should be reopened if a use case appears that is genuinely
bounded — a fixed, small standards catalog (Landolt is 595 rows) shipped as a
read-only artifact for offline use, with no ingestion, no releases, and no
promise of query parity. That would be a *different package* with a narrow
contract, not a backend switch inside this one.

It should **not** be reopened to make the tests easier to run, to avoid a
container in CI, or to make installation lighter. Those costs are real, they are
known, and they are the price of the correctness and scale properties above.

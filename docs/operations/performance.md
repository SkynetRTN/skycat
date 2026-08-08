# Query performance

Skycat's query layer is fast in the way an indexed database is fast: right up
until a plan changes, and then not at all. This page gives targets to measure
against, the conditions under which each is expected to hold, and the procedure
for measuring them — so "the cone search got slow" becomes a number compared to
a number instead of an impression.

> **These are targets, not measurements.** They are the latencies the design is
> intended to deliver on a reasonably provisioned server, and the thresholds at
> which to start investigating. Nothing here is a benchmark result, and nothing
> here is part of the [stability contract](../reference/api-stability.md). Measure on your own
> hardware — the numbers below are for noticing regressions, not for citing.

## What the targets assume

- PostgreSQL 16 with PostGIS 3.4+, `shared_buffers` at ~25% of RAM, and enough
  RAM that the GiST index on the queried partition is cached after warm-up.
- The release's partition has been `ANALYZE`d — the importer does this before
  the swap, so it holds unless statistics have gone stale since.
- Local or same-datacenter network. Round-trip time is added to every number
  here and dominates the small ones.
- Measured server-side (`EXPLAIN ANALYZE` execution time), not wall clock at the
  client. Client wall clock adds connection acquisition, row serialization, and
  the trip home.

The cardinality that matters is **rows returned**, not rows in the table. A cone
over APASS DR10 (128M rows) and one over Landolt (595 rows) cost about the same
if they return the same number of rows: the GiST index makes the table size
almost irrelevant and the returned-row count almost everything.

## Cone search

Nearest-first (the default ordering), warm cache, `limit` at or above the
returned-row count:

| Radius | Typical rows (APASS DR10, mid-latitude field) | Target | Investigate above |
|---|---|---|---|
| 1 arcsec | 0–1 | < 5 ms | 20 ms |
| 10 arcsec | 0–5 | < 5 ms | 20 ms |
| 1 arcmin | ~10–40 | < 10 ms | 50 ms |
| 5 arcmin | ~200–1000 | < 25 ms | 100 ms |
| 15 arcmin | ~2k–10k | < 100 ms | 400 ms |
| 1 degree | ~40k–150k | < 1 s | 3 s |

Radii above about a degree are not what this query shape is for. A 1° cone over
a dense field returns hundreds of thousands of rows, and the cost is dominated
by materializing and shipping them — use `limit` with an explicit `order_by`, or
reconsider the question.

### The conditions that change the answer

**Dense fields.** The Galactic plane and globular-cluster centres hold one to
two orders of magnitude more sources per square degree than a mid-latitude
field. A 5-arcmin cone at `l≈0, b≈0` can return as many rows as a 1-degree cone
at the pole. Budget by expected rows, not by radius, and test against a
deliberately dense field — Stetson's clusters are convenient for this.

**High declination and the poles.** No special case. The geography type works on
a sphere, the GiST index is built on the same geography, and a cone at
`dec = +89.9` is neither faster nor slower than one at the equator for the same
returned-row count. If you measure a difference, it is source density, not
geometry.

**RA wraparound at 0/360.** Also no special case, and this is the point of using
`geography` rather than a lon/lat box: a cone centred at `ra = 0.05` correctly
includes sources at `ra = 359.95` in a single index scan. There is no OR'd
second range, so no plan change and no cost cliff. A query that *is* slower
across the wrap is a bug, not a tuning problem.

**Magnitude ordering.** `order_by="johnson_v_mag"` adds a top-N sort over every
row in the cone, because the brightest star in the cone can be anywhere in it.
Expect roughly 1.5–3x the nearest-first time at a few thousand candidate rows,
growing with the candidate count rather than with `limit`. This is still the
right call whenever you pass a `limit` — see the README's note on why a capped
nearest-first cone returns the wrong stars — but it is not free.

**Magnitude and quality filters.** `mag_band` + `mag_min`/`mag_max` and
`quality_filter` are applied after the spatial predicate, so they reduce rows
returned without reducing index work. They make a query cheaper, never more
expensive, but they do not turn a 1-degree cone into a cheap one.

## Native-id lookup

Backed by the btree on `native_id`.

| Case | Target | Investigate above |
|---|---|---|
| Unique id, one row | < 5 ms | 20 ms |
| Non-unique id (Stetson `Star`, repeated per cluster) | < 20 ms | 100 ms |

## Batch crossmatch

One round trip regardless of batch size: inputs are `COPY`d into a TEMP table
with its own generated geography column, then a LATERAL join uses the catalog's
GiST index for KNN ordering with an `ST_DWithin` radius cap.

Nearest-only, 5 arcsec radius, against a large family:

| Inputs | Target | Investigate above |
|---|---|---|
| 100 | < 100 ms | 400 ms |
| 1,000 | < 500 ms | 2 s |
| 10,000 | < 4 s | 15 s |
| 100,000 | < 45 s | 3 min |

Scaling is close to linear in the input count, which is the property to watch: a
batch that costs disproportionately more than one a tenth its size means the
per-input index probe stopped being an index probe.

`max_candidates > 1` (via `nearest_only=False`) multiplies the per-input work
roughly by the candidate count. A larger radius costs more than a larger batch —
it widens every one of the per-input searches.

For batches beyond ~100k inputs, chunk them. The TEMP table is `ON COMMIT DROP`,
so each chunk is independent, and a chunked run gives you progress and a
bounded transaction instead of one long one.

## Measuring

### Prove the index is being used

The first question about any slow spatial query is whether it is still a GiST
index scan. `--explain` mirrors the real query's ordering, so it reflects what
the actual call does:

```bash
uv run skycat cone apass --ra 250.4234 --dec 36.4613 --radius-arcmin 5 --explain
uv run skycat cone apass --ra 250.4234 --dec 36.4613 --radius-arcmin 5 \
  --order-by johnson_v_mag --explain
```

Look for an index scan (or a bitmap heap scan over one) on the release's
partition, using a GiST index on `geom`:

```
->  Index Scan using apass_source_r3_incoming_geom_idx1 on apass_source_r3
      Index Cond: (geom && _st_expand(...))
      Filter: ((release_id = 3) AND st_dwithin(geom, ..., false))
```

The index name is PostgreSQL-generated and looks wrong at first glance. It is
not: the runner builds the partition detached as `<parent>_r<N>_incoming` and
lets its indexes auto-name — so they cannot collide with the parent's
`ix_apass_source_geom` — then renames the *table* into place. Index names are
not rewritten by a table rename, so the `_incoming` stays. Match on the shape,
not the name. A **sequential scan on a large family is the finding** — usually a
partition that was never `ANALYZE`d, a missing index on a partition that was
attached outside the normal path, or a query touching the parent table without a
`release_id` predicate and so scanning every partition.

The two plans differ by design: the magnitude-ordered one adds a top-N sort. If
they look identical, you are explaining the wrong thing.

### Time it

```bash
uv run python - <<'PY'
import statistics, time
from skycat import CatalogReader

reader = CatalogReader.from_env()
cases = [
    ("1 arcsec",  dict(radius_arcsec=1)),
    ("1 arcmin",  dict(radius_arcmin=1)),
    ("5 arcmin",  dict(radius_arcmin=5)),
    ("15 arcmin", dict(radius_arcmin=15)),
]
ra, dec = 250.4234, 36.4613

for label, radius in cases:
    reader.cone("apass", ra, dec, limit=100_000, **radius)      # warm
    times, rows = [], 0
    for _ in range(5):
        t0 = time.perf_counter()
        out = reader.cone("apass", ra, dec, limit=100_000, **radius)
        times.append((time.perf_counter() - t0) * 1000)
        rows = len(out)
    print(f"{label:10} rows={rows:>7}  median={statistics.median(times):7.1f} ms")
reader.close()
PY
```

Points that make the difference between a measurement and a number:

- **Discard the first run.** It pays for connection setup, the active-release
  lookup, and a cold cache. `CatalogReader` caches the release for 60s, so
  subsequent calls in the loop measure the query rather than the registry.
- **Report the median of at least five runs**, not the mean. One `autovacuum`
  landing mid-run skews a mean and leaves the median honest.
- **Use a `limit` above the expected row count** when measuring the query, or
  you are measuring the limit.
- **Record the field.** "5 arcmin took 40 ms" means nothing without which 5
  arcmin. Keep the coordinates with the number.
- **Vary one thing.** Radius, or density, or ordering — not two.

### Track it over time

The numbers that matter are your own, on your hardware, compared against
themselves. Record a baseline after each release import — a handful of cones at
fixed coordinates, one lookup, one crossmatch — and compare after anything that
could move a plan: a new release, a PostgreSQL or PostGIS upgrade, a
configuration change, a schema migration.

`skycat sizes` is worth capturing alongside it: a partition whose index size
stops growing with its row count is an index that stopped being maintained.

## When a query is slow

In order of how often it is the answer:

1. **The partition has stale statistics.** `ANALYZE catalog_data.<partition>;`
   The importer analyzes before the swap, so this points at something that
   changed the data outside the normal import path.
2. **The plan is a sequential scan.** Confirm with `--explain`. Check that the
   query filters on `release_id`, and that the partition has the GiST index —
   `skycat health` reports `spatial_index_<family>` per family.
3. **The cone is genuinely huge.** Count the rows before blaming the plan; a
   1-degree cone in the Galactic plane is expensive because it is expensive.
4. **`order_by` on an unindexed column with a large candidate set.** Expected,
   and the cost scales with candidates, not with `limit`.
5. **The statement timeout is firing.** `CatalogReader` applies 30s by default;
   a query killed at exactly 30s is not slow, it is cancelled. Raise
   `statement_timeout_ms` deliberately or make the query smaller.
6. **The index is not cached.** The first query after a restart pays for reading
   the index from disk. If every query pays it, the working set does not fit in
   `shared_buffers`.

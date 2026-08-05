---
status: working
audience: skycat author + platform integration planning
last-updated: 2026-07-12
---

# Skycat design review — scalability, simplification, and platform integration

Review of the `skycat` package (branch `feat/skycat`, PR B of the #1631 split)
against the question: *is this the right foundation for platform-wide local
catalog access (APASS etc.), and where can it be simplified?* Companion to
[pr-1631-split-and-rework-plan.md](pr-1631-split-and-rework-plan.md), which
covered the original split + blocking fixes (now addressed on this branch).

> **Status (2026-07-12): recommended actions 1–5 are implemented on `feat/skycat`.**
> Those are the in-package items; §10.6 (PR C pipeline integration) and §10.7
> (Tycho-2) remain open and are separate PRs. See [§10](#10-recommended-actions-ordered)
> for what landed, what changed versus the recommendation, and what the
> implementation turned up that this review missed.

**TL;DR** — The architecture is sound and appropriately sized for what the
platform needs; do not restructure it. Keep the family/release model, but treat
releases as an *operational* (blue/green upgrade) mechanism, not a
science-facing dimension. The generic ingestion runner is a defensible design
that already maps to one-shot Docker/K8s jobs — rebuilding it as per-catalog
tools would multiply, not reduce, code. The remaining bloat is small and
concrete (a handful of dead states/columns/commands, listed in §4). The one
functional gap that blocks nearly every listed use case is **brightest-first
selection** in `cone_search` (§5.1) — add it before any consumer lands. For
PR C, select the catalog backend through the pipeline's existing `Catalog`
adapter seam with a per-worker mode (`remote` default / `local_first` /
`local_only`) and a composite fallback adapter — never call-site
`if skycat:` guards (§7).

---

## 1. What was reviewed

- The whole package (the repository root): config, constants, database
  (engine/init/roles/migrate/postgis), models, registry, ingestion
  (discovery/parsers/copy_loader/runner/maintenance), query
  (cone/crossmatch), spatial, validation, health, CLI, migrations, tests.
- Infra wiring: compose services + profiles, `skycat-postgresql.dockerfile`,
  K8s jobs (`skycat-init/migrate/ingest`), Ansible tenant tasks, configmap /
  secrets, CI.
- Consumer-side plug points across the monorepo (public-api routers, the
  optical pipeline catalog registry, skylib's ATLAS solver, sky-chart,
  observation editor) — surveyed for the use-case walkthroughs in §6.

## 2. Overall assessment

The core design decisions are right, and several are better than they needed
to be for a first version:

- **Standalone database with hard guards.** Own base/metadata/engine/Alembic,
  `SKYCAT_DB_*` namespace, `assert_not_primary_database()` on every mutating
  path. A process can hold a `sky` session and a `catalogs` session with zero
  shared state. This is the correct isolation for data with a completely
  different lifecycle (immutable, huge, spatially indexed).
- **Spatial layer is correct and centralized.** One `GEOM_GENERATED_EXPR`,
  spherical `ST_DWithin`/`ST_Distance` (`use_spheroid=false`), exact
  degrees↔metres round-trip via the shared sphere radius, GiST per partition,
  and an `EXPLAIN`-based test proving index use. RA wraparound and poles are
  handled in the database, not in Python.
- **Ingestion is genuinely scalable.** Streaming parsers → `COPY` into
  unlogged staging → validate-and-mark (never silently drop) → build a
  *detached* partition (no parent lock during the multi-minute build) →
  atomic `ATTACH` swap. A failed import can never become active; the previous
  active release keeps serving throughout. This is the right shape for a
  128M-row DR10 load.
- **Typed per-family models** (no universal star table), with `extra` JSONB
  for per-release quirks, unit-suffixed columns matching repo convention.
- **Three-role least privilege** (`catalog_owner`/`catalog_ingest`/
  `catalog_reader`) with consumers pinned to the read-only role.
- Module organization is clean: `config → database → models → registry →
  ingestion → query → validation → health → cli` with clear one-way
  dependencies. The earlier review's trims (speculative families, dead
  manifest tables, raw-SQL quality filters) all landed.

**Answer to "is the code well organized?": yes.** The per-family cost of
adding a catalog is: a `catalog_defs.py` entry, a parser, a model, a
migration, and optionally a validation module — five touch points, each small
and each in the obviously-named place. That is acceptable; document it as a
checklist in the README rather than refactoring toward a single-file plugin
descriptor.

## 3. The data-release question

> Do we need to keep different data releases? Should each release be a
> separate catalog? Can we simplify by keeping a single DR per catalog?

**Recommendation: keep the family/release model, but adopt a policy that
treats releases as deployment versions, not a science dimension.**

What the release layer actually buys, in order of real value:

1. **Safe upgrades (blue/green for catalog data).** Stage DR11 next to a live
   DR10, validate it fully, activate atomically, roll back by re-activating
   the superseded release. For a store that feeds photometric calibration,
   this is the operationally important property — a botched import must never
   degrade the pipeline. This alone justifies the layer.
2. **In-place refresh for living catalogs.** VSX is a continuously updated
   index; `import vsx current --replace --force` rebuilds the partition
   detached and swaps it in while the release stays ACTIVE. Exactly the
   right flow for a recurring refresh job.
3. **Reproducibility.** The PR C plan records which catalog release served
   each processing run (`catalog_queries` keeps the release id). Superseded
   releases remaining queryable via `--release` makes old calibrations
   re-derivable.
4. **Remote-parity testing.** Landolt 1992 vs 2009: the VizieR provider
   mirrors 1992, so strict local-vs-remote comparison needs the explicit old
   release while 2009 is the active default. This need is real but mostly
   transitional (and Landolt is 526 rows — free to keep).

Would "each DR = a separate catalog" simplify? **Not meaningfully.**
Physically, the layout is *already* one table per release — partitions of a
family parent. Splitting DR6/DR10 into separate families would:

- push the "which release do I use?" decision onto every consumer (each DR
  upgrade becomes a config/code change in the API, the pipeline, sky-chart,
  the editor) instead of one `skycat activate`;
- forfeit atomic upgrade/rollback (activation is the mechanism that makes a
  DR swap invisible to consumers);
- save only the registry indirection (~400 lines of registry + release
  lifecycle), which is the cheapest part of the package.

The right *policy* (write it into the README):

- **One active release per family** (already enforced by a partial unique
  index). Consumers never name a release; they get the active one.
- **Retain at most the previous (superseded) release** for rollback and
  reproducibility; `remove-release` older ones after the new DR has served
  for an agreed soak period. Don't plan for N simultaneously-live DRs.
- The `--release` query parameter is an ops/parity-testing affordance. Do
  **not** expose release selection through the public API by default (see
  §6.1); if a science case ever truly needs pinned releases, add it then.

## 4. Code bloat / dead code — concrete findings

> **Done (2026-07-12).** All five findings addressed; `expected_row_count`
> was wired into validation rather than dropped. See §10.

Post-trim, the package is not bloated; total source is ~5.8k lines of which
~1.5k is the generic ingestion/validation core and ~1.2k is CLI+health (both
earn their keep operationally). The remaining dead weight:

1. **Dead release states.** `LOADING`, `VALIDATING`, and `DISABLED` in
   `CatalogReleaseState` ([constants.py:54](../../../../skycat/constants.py))
   are never assigned anywhere — the runner goes `REGISTERED → STAGING →
   READY/ACTIVE/FAILED`. They appear only in `health.py`'s transient-state
   list and the runner's `_imported_states`, which makes the state machine
   look bigger than it is. Trim to the six real states (or wire `LOADING`/
   `VALIDATING` as actual runner phases if the granularity is wanted for the
   stuck-import health check).
2. **`CatalogFamily.default_release_id` is written but never read.**
   `activate_release`/`deactivate_release` maintain it, the migration builds
   a deferred circular FK for it, and `list_releases` has to disambiguate the
   join because of it — but active-release resolution goes through
   `CatalogRelease.state == ACTIVE` ([releases.py:73](../../../../skycat/registry/releases.py)).
   Two sources of truth, one consumer. Drop the column (and the FK and the
   maintenance code), or make it the single lookup path — don't keep both.
3. **`CatalogRelease.expected_row_count`** is explicitly set to `None` on
   every import ([runner.py:262](../../../../skycat/ingestion/runner.py))
   and never populated or checked. Drop it, or use it: the family defs could
   carry approximate expected counts (42.6M / 128.6M / …) and validation
   could warn when the imported count deviates grossly — that's a genuinely
   useful sanity check for a truncated source file.
4. **Redundant CLI surface.** `register-family`, `register-all-families`, and
   `register-release` exist as standalone commands, but `import` performs the
   same registration itself. Nothing in the documented flows uses them. Keep
   `import`/`activate`/`releases`/`health` lean and drop the three
   registration commands (or fold them behind a single `skycat register`).
5. **Small duplications.** `crossmatch.py` re-declares the generated-geometry
   SQL as `_QGEOM_EXPR` instead of importing `spatial.GEOM_GENERATED_EXPR`,
   and carries a dead `Jsonb` import (`noqa: F401`). Trivial, but both are
   the kind of drift that bites when the expression ever changes.
6. **Not bloat, deliberately kept:** `health.py`'s ~20-check report (ops
   value, and PR C's rollout depends on it), `cone_search_plan` (backs the
   `--explain` CLI and the index-usage test), and the destructive-op guard
   stack (`--force` / production-marker checks). These look heavy but each
   has a consumer.

## 5. Gaps to close before consumers land

### 5.1 Brightest-first selection (the one real blocker)

> **Done (2026-07-12).** `order_by` on `cone_search`, `cone_search_plan`,
> `CatalogReader.cone`, and the CLI `--order-by`.

`cone_search` orders by separation and caps at `limit`
([query/cone.py:174](../../../../skycat/query/cone.py)).
Every planned consumer wants the *brightest* stars in the field, not the
nearest to center:

- the sky-chart finder layer draws bright stars first;
- the saturation model cares only about the brightest stars in the FOV;
- "identify bright stars in a FOV" is definitionally brightest-first;
- field calibration's remote providers sort brightest-first before capping
  (`sort=['+Bmag']` etc.), so nearest-N vs brightest-N returns a *different
  star set* in dense fields — the known PR-C parity bug (§B.5 of the split
  plan): the zero point gets fit against a fainter, spatially biased
  calibrator population.

**Fix in skycat, once, rather than in each consumer:** add an
`order_by: str | None` parameter to `cone_search` validated the same way
`mag_band` is (must name a real numeric column; `NULLS LAST`; separation as
tiebreak). Nearest-first stays the default; `order_by="johnson_v_mag"` serves
all four cases. Also mirror it in the CLI `cone` command.

### 5.2 Consumer ergonomics: a tiny client facade

> **Done (2026-07-12).** `skycat/client.py`. Note the caveat in §10.1: the
> TTL cache also required a `resolved:` passthrough on the query functions.

The query functions correctly accept a caller-managed `engine=`, but every
consumer will re-write the same boilerplate: load settings → build reader
engine → resolve active release per call. Add one small module (~60 lines,
e.g. `skycat/client.py`):

```python
reader = CatalogReader.from_env()          # holds settings + pooled reader engine
reader.cone("apass", ra, dec, radius_deg=..., order_by="johnson_v_mag", limit=500)
reader.crossmatch("vsx", inputs, radius_deg=...)
```

with two behaviors baked in:

- **Active-release caching** with a short TTL (~60 s). Today every query
  opens a Session to resolve the active release from the registry — fine for
  the pipeline, wasteful under public-API QPS. Activation is rare; a TTL
  cache is safe and makes the hot path a single indexed query.
- **A default `statement_timeout`** for reader connections (the config knob
  exists but nothing sets it) so a pathological query can't wedge a
  single-threaded worker — this was flagged in the PR-C review and belongs
  here, not in each consumer.

This facade is *the* shared helper layer the platform question asks about
(§6.5): public-api and the backend pipeline both consume `CatalogReader`;
PostGIS/SQLAlchemy details never leak past the package boundary.

Sync-only is fine: FastAPI runs sync dependencies/handlers in its threadpool,
and the pipeline is sync. An asyncpg/async-psycopg variant can be added later
behind the same facade if the API ever needs it; don't build it now.

### 5.3 Box queries (only if the ATLAS adapter happens)

skylib's solver protocol is `query_box(ra_min, ra_max, dec_min, dec_max)`
over memmapped UCAC zone files. If a skycat-backed astrometric index is ever
wanted (§6.4), skycat needs a `box_search` (PostGIS `ST_Intersects` with a
polygon, or two range predicates on `ra_deg`/`dec_deg` handling the 0/360
seam). **Don't add it speculatively** — the memmap zone files serve the
solver well; add the box query in the same change as the adapter, if ever.

## 6. Use-case walkthroughs

Surveyed plug points are current as of 2026-07-10 on `feat/skycat` + `main`.
Today skycat has **zero live consumers**: `services/runnable_worker` declares
the dependency (staged for PR C) but imports nothing yet.

### 6.1 Public API router (`/catalogs`)

Fits the existing pattern cleanly. `apps/public-api/public_api/routers/`
modules export `router = APIRouter(...)` registered in `main.py`'s
versioned `api_router`. There is already a precedent endpoint to replace:
`catalog_objects.py::vizier_apass` cone-searches **APASS9 via astroquery →
VizieR over the network** for scripted calibration. A skycat-backed
`routers/catalogs.py` mirrors its parameters (ra/dec/radius + per-band mag
filters) against the local store.

Design notes:

- The router needs its **own engine dependency** (a `CatalogReader` from
  §5.2 held at app scope) — the existing `Database` dependency is the `sky`
  DB and must not be conflated.
- Cap `radius` and `limit` at the API layer (e.g. ≤2° / ≤5,000 rows) and
  expose `order_by` as an enum of blessed columns per family. The
  `QualityFilter` allow-list validation makes passing user filters through
  safe, but start with a fixed parameter set (band, mag range, order, limit).
- Response schema: one generic row shape (`native_id`, `ra_deg`, `dec_deg`,
  `separation_deg`, plus a `mags: {band: value}` map) is better for the SDK
  than per-family typed schemas — the families are heterogeneous and the
  clients (sky-chart, editor) want a uniform star list. Keep the full typed
  columns available via a `?fields=all` escape hatch if needed.
- Don't expose `release` selection publicly (per §3 policy); the active
  release id can be *returned* in a response header/envelope for
  reproducibility.

### 6.2 React website — Sky Chart finder-chart layer

`packages/ts/sky-chart` already has the right extension seam:
`useProgressiveStars` fetches static HYG tier files as FOV crosses
75/25/10/5/2.5° thresholds and feeds `starsPlugin({ magnitudeLimit })`. The
deep layer is one more tier: below ~1–2° FOV, fetch the §6.1 endpoint with
the camera center + circumscribing radius, debounced on pan/zoom settle, and
merge into the same entry list (id, ra hours, dec, magnitude — the plugin's
existing shape). A skynet-specific plugin belongs in
`packages/ts/sky-chart-skynet` next to the telescope-FOV/instrument-footprint
overlays it will be used with.

Practical notes: APASS DR10 reaches V≈17 — ample for a finder chart at
arcminute FOVs; request `order_by=<band>` + `limit` so dense fields degrade
gracefully; short-TTL server caching (or client memoization keyed on a
rounded center) is enough — no tile/HEALPix scheme needed at these request
rates.

### 6.3 Backend pipeline — field calibration (PR C)

The consumption point is exactly where the split plan put it: a `Catalog`
subclass registered in
`skynet_db/runners/observation_asset_processing/optical_data_processing/catalogs/__init__.py::CATALOGS`,
querying skycat **directly** (in-cluster, `catalog_reader`, shared pooled
engine) — `field_cal.py`/`catalog_query.py` consume the registry abstractly
so no caller changes. Two things to settle there, not in skycat:

- The nearest-vs-brightest parity bug — resolved by §5.1 plus the local
  provider passing `order_by=<calibration band>`.
- ~~There is a **duplicate catalog-plugin registry** at
  `skynet_db/runners/common/catalog_plugins/` (same files as the
  `optical_data_processing/catalogs/` set). Determine which is live and
  delete the other before wiring skycat in, or the local backend will get
  registered in one and silently missed in the other.~~
  **CORRECTED 2026-07-13 — see below. Both registries are live, on different
  code paths; "delete the other" would have deleted working code.**

#### 6.3a The two registries (corrected)

Verified against `main` @ `eb18d0f8b`. They are divergent forks, not copies,
and **both are imported by the live science path**:

| Registry | Contents | Consumed by |
|---|---|---|
| `optical_data_processing/catalogs/` → `CATALOGS` | 11 catalogs | `field_cal.py`, `catalog_query.py` — the *queries* |
| `common/catalog_plugins/` → `CATALOG_OPTIONS` | **2** (APASS, PanSTARRS) | `runners/utils.py` — `resolve_ref_mag_for_filter` (live), `query_catalogs_for_image` (**dead**, zero callers) |

They also *interact*, and that interaction is a live bug on `main`:
`field_cal.py:455` picks its catalog from `CATALOGS` but resolves the
reference-magnitude band through `resolve_ref_mag_for_filter` →
`_get_catalog_filter_lookup` → **`CATALOG_OPTIONS`**, which has no entry for
Landolt, Stetson or VSX. Those three fall to `CATALOG_OPTIONS.get(name) → None
→ {}`, so the `filter_lookup` they *do* declare in `CATALOGS` (`_OCL_TO_V`,
mapping Open/Clear/Lum → V) **never applies** to band resolution. Steps 2–3 of
the resolution chain are silently skipped for them.

A second, latent defect: the legacy base class mutates a **shared class-level
dict** —

```python
# common/catalog_plugins/catalog.py
filter_lookup: Dict[str, str]          # annotation; subclasses assign a real dict
def __init__(self, filter_lookup=None):
    if filter_lookup:
        self.filter_lookup.update(filter_lookup)   # mutates the *class* dict
```

Confirmed empirically: `APASSCatalog(filter_lookup={"ZZZ": ...})` leaks `ZZZ`
into `APASSCatalog.filter_lookup` and into every other instance. Harmless today
only because each catalog is constructed exactly once at import. PR C
constructing adapters with overrides would contaminate it globally. The
`CATALOGS` base class already fixes this (`self.filter_lookup = {**cls_default,
**override}`).

**Therefore:** unify onto `CATALOGS` — re-point `utils.py`, delete
`common/catalog_plugins/` and the dead `query_catalogs_for_image` — **as a
prerequisite commit, before any skycat wiring.** Note this is a *science*
change, not a refactor: Landolt/Stetson/VSX gain their band lookups and their
zero-points can move. It deserves its own review and its own before/after
comparison, and it is worth doing regardless of whether skycat ever lands.

Because `query_catalogs_for_image` is the only `query_box` consumer and it is
dead, **PR C does not need skycat box search (§5.3)** — consistent with §5.3's
own advice not to add it speculatively.

Note the four families skycat ships (APASS, VSX, Landolt, Stetson) are
exactly the four the remote registry serves from VizieR that matter for
calibration — the scoping is right.

### 6.4 Backend — WCS plate solving

skycat is **not** in the solve path today and doesn't need to be: the ATLAS
solver reads memmapped UCAC4/UCAC5 zone files via a `CatalogIndex` protocol
(`query_box`), which is faster and simpler than a per-solve DB round trip.
Two plausible future roles, both optional:

- a skycat-backed `CatalogIndex` (needs §5.3 box search) if operating the
  zone files becomes a burden or a Gaia-based index is wanted;
- skycat as the *source* from which solver index/zone files are built by a
  one-shot job (better: keeps the solver hot path file-local).

Recommendation: leave solving alone; note the seam
(`atlas/solve/solver.py` → `CATALOG_REGISTRY`) and revisit when an
astrometric family (UCAC5, Gaia, Tycho-2) is actually ingested.

### 6.5 Observation editor — saturation / brightness model

Greenfield, and the pieces are cleanly separable: the editor already has the
instrument FOV footprint (sky-chart overlays) and the exposure-sizing model
(`target_full_well_fraction` mode on `OpticalImagingRequest`); the missing
primitive is "brightest stars in this FOV in this band" — which is §6.1 +
`order_by`. The saturation evaluation itself (star mag + exposure + gain +
full-well → saturated?) belongs in the editor/backend service, not skycat.

**Science caveat that affects catalog planning:** APASS is saturated at the
bright end (roughly V ≲ 7) — the stars most likely to saturate a telescope
image are exactly the ones APASS lacks or measures worst. For a trustworthy
saturation model (and for wide-FOV finder charts) plan **Tycho-2 (V ≈ 2–11)
or Gaia as the next ingested family**. This is the strongest concrete driver
for a new family and slots into the existing per-family pattern without any
framework change.

### 6.6 Direct DB vs public API — the rule

- **Direct (skycat package + `catalog_reader` + shared engine):** anything
  in-cluster and Python — the optical pipeline, schedulers, future workers.
  No HTTP hop, no API coupling, pooled connections.
- **Public API:** anything remote or non-Python — browser (sky-chart,
  editor), the TS/Python SDKs, scripted users, and **SkyNodes** (telescope
  sites are outside the cluster; if on-node QA ever wants expected-source
  counts, it goes through the API or a server-computed expectation shipped
  with the task).

The shared-helper question: **yes, `skycat.query` is that collection**, and
§5.2's `CatalogReader` makes it the single entry point both sides use. Keep
all SQL/PostGIS inside skycat; consumers speak (family, ra, dec, radius,
band, order, limit).

### 6.7 Other likely consumers (roughly in value order)

1. **QA breaker Phase C ("expectation-driven counts")** — expected source
   count for a pointing = skycat cone + mag cut at the frame's expected
   depth. Server-side computation feeding node QA thresholds.
2. **Alert/transient vetting** — `batch_crossmatch` of candidate positions
   against VSX (known variable?) and APASS (known star?) in the GCN/Fink and
   planned NEOCP pipelines; also "is the counterpart bright enough"
   feasibility gates.
3. **Target editor warnings** — "this target is a known VSX variable
   (type, period)" at target-creation time via a native-id/cone lookup.
4. **Afterglow** — its calibration service has a parallel VizieR path
   (`services/calibration.py`); a skycat backend there, plus catalog-star
   overlays in the viewer via the public API.
5. **Server-rendered finder charts** on observation detail pages / PDFs —
   same query as §6.2, rendered server-side.

## 7. PR C fallback architecture — adapters, not call-site guards

Making pipeline processing *require* a provisioned skycat is the wrong
default, both operationally (a catalog-DB outage must not stop science) and
for developers (nobody testing pipeline code should have to import 128M rows
of APASS first). But the fallback must not be `if skycat_available:` branches
at call sites — that yields N decision points, each with its own error
handling, and the astroquery path silently rots the moment prod goes local.

**The interface already exists.** Field calibration consumes catalogs through
the abstract `Catalog` base class (`query_circ`/`query_box`/`query_objects`)
in `skynet_db/runners/.../optical_data_processing/catalogs/catalog.py`; the
astroquery/VizieR implementations are already adapters of it, and
`field_cal.py`/`catalog_query.py` never know which backend they hold. PR C's
job is to add a second adapter family (`SkycatApassCatalog`, …) beside
`VizierCatalog` and decide **once, at registry construction**, which instance
each family slot in `CATALOGS` gets. Concretely:

- **Three modes, configured per worker** (not a global key — see the split
  plan's note about the scheduler): `remote` — astroquery only, **the
  default**; `local_first` — skycat with astroquery fallback (production);
  `local_only` — strict, no fallback, for rollout validation ("prove no
  network catalog access occurs").
- **A composite adapter for fallback, not branching in science code.** A
  `FallbackCatalog(local, remote)` implementing the same `Catalog` interface
  holds all routing logic in one class; the registry installs `remote`,
  `local`, or `Fallback(local, remote)` per family and call sites never
  change.
- **Fall back on availability errors only, never on science outcomes.**
  Connection refused / timeout / no-active-release → fall back with a
  structured warning. An **empty cone result is a legitimate answer** (sparse
  field) and must not trigger a VizieR re-query, or backend disagreements get
  silently papered over.
- **Per-family granularity.** Availability isn't monolithic: skycat may have
  APASS active but Stetson not yet imported. "No active release for
  `stetson`" routes that family to astroquery while APASS stays local.
- **A short-TTL circuit breaker.** When skycat is down, don't pay a
  connection timeout on every query all night — cache "unavailable" for a
  minute or two and go straight to remote.
- **Record which backend + release served each run** (the trimmed
  `catalog_queries` provenance PR C keeps). An unrecorded silent fallback is
  a day of debugging a zero-point shift before noticing half the frames were
  calibrated against VizieR.
- **Lazy import.** Nothing executes `import skycat` unless the mode asks for
  it, so services and developers that don't install the package never
  crash-loop on a missing dependency.

The `remote` default plus lazy import answers the developer-experience
concern directly: a local dev installs nothing, provisions nothing, and gets
today's astroquery behavior; one env var opts into local. The adapter framing
also buys the parity test for free — run the same fixture field through both
adapters and assert the normalized row sets match — which is exactly the
harness that would have caught the nearest-N vs brightest-N calibrator bug.
Prerequisite: §5.1's `order_by` must land first, or the two adapters return
different star sets in dense fields and the fallback is not
behavior-preserving.

## 8. Provisioning & ingestion — complexity verdict

> Overly complex, or defensible? Should adding a catalog be catalog-specific
> tools mapping to one-shot Docker/K8s jobs?

**Defensible, and the proposed alternative is already how it works.** The
deployment surface *is* catalog-specific one-shot jobs: compose profile
services (`skycat-import apass dr6 --activate`) and K8s Jobs
(`skycat-ingest` with explicit family/release args, `backoffLimit: 0`, never
part of app startup). What's generic is the *engine* those jobs share:
discover → checksum → stage(COPY) → validate → detached build → atomic swap
→ registry record. That's the hard 20% every catalog needs identically, and
it's ~1.5k lines written once. Per-catalog scripts would each reimplement or
import that core — you'd end with N copies of the dangerous parts (partition
swap, grants, idempotency) and no uniform `releases`/`history`/`health`
view. The current split — generic runner + ~100–190-line parser + model +
migration per catalog — is the right factoring.

Where the machinery **is** heavier than the need, trim within the design
rather than replacing it:

- The registry could be smaller: §4.1–4.3 (dead states, `default_release_id`,
  `expected_row_count`). `CatalogFamily` as a DB table mostly mirrors
  `catalog_defs.py` via `sync_family`; that's acceptable (readers resolve
  active releases without importing code), but resist adding more mirrored
  fields to it.
- `IngestionRun` + `ValidationSummary` earn their rows: multi-hour imports
  need durable history and the malformed-row diagnostics have already proven
  useful. Keep.
- The roles/grants automation (`roles.py` + the init choreography:
  bootstrap → grants → migrate-as-owner → reassign-to-ingest) is intricate
  but is exactly the least-privilege story a shared prod PostGIS needs, and
  it's idempotent + Ansible-compatible. Keep; it's documented.

Two forward-looking ingestion notes:

- **VSX refresh cadence:** the `--replace` in-place swap of the ACTIVE
  release is the right primitive; once PR C is live, add a K8s **CronJob**
  (monthly?) wrapping `skycat import vsx current --replace --force
  --activate` — the idempotency checksum makes an unchanged source a no-op.
- **DR10-scale imports** are single-stream COPY + one big index build in a
  detached table; hours-long but bounded-memory and lock-free for readers.
  Fine for a one-shot job; do not parallelize speculatively.

## 9. Documentation nits

- [docs/SKYCAT_DATABASE.md](../../SKYCAT_DATABASE.md) links to
  `SKYCAT_LOCAL_ROLLOUT.md`, which does not exist on this branch, and the
  package README's "Consumers" section describes the
  `skynet-db/.../catalogs/local/` provider (also not on this branch) in the
  present tense. Both are PR C artifacts — mark them as forthcoming or strip
  until PR C lands, so the docs don't describe a consumer that isn't there.
- When PR C's provider lands, its README should state the direct-vs-API rule
  from §6.6 so future consumers don't guess.

## 10. Recommended actions (ordered)

Items 1–5 are **done** on `feat/skycat` (2026-07-12); 6–7 remain open.
Test suite: 109 passing (was 49 passing + 26 skipped), ruff clean, pyright 0
errors, all verified against a real PostGIS instance.

1. ✅ **`order_by` (brightest-first) on `cone_search`** + CLI (§5.1) —
   prerequisite for every consumer and the PR-C parity fix. Reaches
   `cone_search_plan` (`--explain`) and `CatalogReader.cone` as well.
2. ✅ **`CatalogReader` facade** with active-release TTL cache + default
   statement timeout (§5.2) — `skycat/client.py`.
3. ✅ **Trimmed dead weight** (§4): dead states, `default_release_id`, the three
   `register-*` CLI commands, the crossmatch expr/import duplication.
   `expected_row_count` was **wired into validation** rather than dropped.
   Migration `0006`.
4. ✅ **Release policy in the README** (§3): one active + one superseded
   retained; `--release` is ops/parity only; not exposed via API.
5. ✅ **Stale doc forward-references fixed** (§9).
6. ⬜ PR C sequencing: **unify** the two catalog registries onto `CATALOGS`
   (§6.3a — they are both live, not duplicates; unifying also fixes a live
   band-resolution bug), then wire the local provider as a `Catalog` adapter
   behind the per-worker mode + composite-fallback design (§7), with
   `order_by`; add the public-api `/catalogs` router (§6.1) after, reusing the
   same facade. **`order_by` (item 1) has landed, so §7's prerequisite is
   satisfied** — the two adapters can now be behavior-preserving.
   Full commit-level plan: `pr-1631-split-and-rework-plan.md` Appendix C.
7. ⬜ When the saturation-model feature is scheduled, ingest **Tycho-2** (or
   Gaia subset) as the next family (§6.5) — it exercises the add-a-family
   path and unblocks both saturation and wide-FOV finder charts.

### 10.1 What the implementation changed about this review

Four places where building it contradicted what is written above. They are
recorded here because the reasoning, not just the outcome, was wrong.

- **The DB was not greenfield, so §10.3's "one small migration while the DB is
  greenfield" was the wrong instruction.** The dev catalog store already sits at
  revision `0005` with APASS DR10 (128.6M rows) and DR6 (42.6M) imported.
  Editing `0001` in place — the clean move on a truly greenfield schema — would
  have permanently diverged that database, and rebuilding it means re-importing
  DR10 over hours. The trims therefore landed as a forward migration (`0006`),
  verified against a populated database with live active releases, with a
  lossless downgrade that backfills `default_release_id` from the state column.

- **The TTL cache in §5.2 does not work as described.** Caching the *active
  release* is useless on its own: `cone_search` takes a release *name* and
  resolves it from the registry itself, so a cached name still costs the round
  trip the cache was meant to remove. `cone_search`, `lookup_native_id` and
  `batch_crossmatch` grew an optional `resolved:` parameter (a `ResolvedRelease`)
  that skips the lookup entirely. Without it the facade's cache is decoration.
  Tests count registry round trips off the engine and assert zero on the hot
  path.

- **`expected_row_count` had published counts available all along.** §4.3
  suggests the family defs "could carry approximate expected counts"; the
  package README's family table already documented them for every family (APASS
  42.6M/128.6M, VSX 10.3M, Landolt 526/595, Stetson 4.89M). All four families
  now carry `ReleaseDef.approx_row_count`, and an import landing below 90% of it
  warns and will not auto-activate without `--allow-warnings`. This is the guard
  against a truncated download — a short read of a multi-GB catalog parses
  cleanly and would otherwise import, validate and activate as a silently
  incomplete release. Ordering matters here: the check has to run on the
  *revalidate* path too (`skycat validate`), not just on import.

- **`order_by` had to reach `cone_search_plan`, not just `cone_search`.** The
  `--explain` flag exists to prove index usage; explaining a nearest-first query
  while running a brightest-first one misrepresents the cost, because the two
  plans genuinely differ (the magnitude sort adds a top-N sort over the cone's
  candidate rows). Measured on the real 128M-row DR10: the GiST index still does
  the filtering (128.6M → 3,718 candidate rows), the sort touches only those, and
  the query returns in ~16ms. **Brightest-first is not a scalability risk** — a
  question this review should have asked and didn't.

The §5.1 diagnosis itself was, if anything, understated. On the real DR10, a
`limit 5` cone at (100.0039, +4.861469, r=0.5°) returns **disjoint star sets**:
nearest-first gives V ≈ 14.8–17.3 including one star with *no V magnitude at
all*; brightest-first gives V ≈ 6.7–8.2. That is ~1500× in flux, and the faint
set is what field calibration was fitting a zero point against.

### 10.2 Two operational hazards found while implementing

Neither is a design flaw in the package; both are traps for the next person.

- **The integration tests will destroy a provisioned catalog store.** The
  `imported` fixture runs `initialize_catalog_database()` and then
  `import_release(..., replace=True, force=True)` for every family — so pointing
  `SKYCAT_DB_*` at the Compose DB on 5433 (which holds the real 128M-row DR10)
  replaces real releases with six-row samples. The README's Testing section
  actively instructed exactly that, and documented a `SKYCAT_TEST_DSN` variable
  that nothing in the codebase reads. Both fixed: the README and the conftest
  skip message now require a throwaway database and show how to create one.

- **The CLI reported operator input errors as Python tracebacks.** `main_entry`
  caught only `CatalogConfigError`, so every `CatalogQueryError` (unknown family,
  no active release, bad `--mag-band`) dumped a stack trace. The handling now
  lives on the Click group, so it applies to `skycat`, `python -m skycat`, and
  the tests alike.

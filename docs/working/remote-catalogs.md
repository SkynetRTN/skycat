---
status: open
reviewed: 2026-08-06
branch: dev
authority: code-inspection (skycat @ 92282d4, skynet @ 0040c28ab, afterglow-core @ 92aaf61) + upstream docs
implementation: not-started
---

# Remote catalog support via astroquery — feasibility

Skycat exists because astroquery could not go local. The 2026-07-04
investigation that seeded this package established that
`astroquery.vizier.Vizier` only POSTs to a VizieR-compatible HTTP endpoint and
has no local-index mode, and the recommendation was to build a local backend
*behind* the existing provider abstraction rather than bend astroquery toward
local files. This note asks the reverse question: now that the local store
exists, should astroquery come back — inside Skycat this time?

The short answer is that "remote catalog support" is four different proposals
wearing one name, and they have four different answers. Sorting them out is most
of the work of this document.

## Verdict

| # | Proposal | Feasible? | Recommendation | Rough effort |
|---|---|---|---|---|
| **A** | Query-time remote fallback inside `CatalogReader` — local first, VizieR when local is unavailable/empty | Technically yes | **No.** Contradicts [decision 0001](../decisions/0001-postgresql-postgis-only.md) on its own reasoning, and the layer already exists downstream in `skynet-db` | 2–4 weeks + permanent behavioural surface |
| **B** | Remote-only families — PanSTARRS, 2MASS, Tycho-2, UCAC5, USNO-B1.0, SkyMapper served straight from VizieR through a Skycat API | Yes | **Defer.** Real value, but no consumer has asked, and it needs a deliberately *non-parallel* API to stay honest | 3–6 weeks for 2–3 families |
| **C** | Automated source acquisition — `skycat fetch` pulling release data via astroquery | Yes, but wrong tool | **No.** astroquery is a cone/region query API, not a bulk mirror. `wget` against `cdsarc.cds.unistra.fr/ftp/` already does this and is documented | 0 (already solved) |
| **D** | Parity harness — dev-only checks comparing a local release against the VizieR catalog it mirrors | Yes | **Yes, do this.** Highest value, lowest risk, `dev` extra only, no runtime surface | 2–4 days |

The one-line version: **adopt D, defer B, reject A and C.** D is already
anticipated by the README, which describes `--release` as "an operations and
parity-testing affordance — … comparing the local store against a remote source
that mirrors an older release".

Read §5 before disagreeing with the rejection of A. The blocker is not
philosophical; it is that VizieR does not carry the APASS release Skycat
actually serves.

---

## 1. What "remote support" actually means

These get conflated because they all involve the word "astroquery". They differ
in what crosses the package boundary, and that is what determines the cost.

| | Who calls astroquery | When | What Skycat contract it touches |
|---|---|---|---|
| **A** Fallback | `CatalogReader.cone()` | Every query, on local miss | Row dicts, release identity, statement timeout, exit codes |
| **B** Remote families | A new remote reader | Every query for that family | A new, separate API surface |
| **C** Acquisition | `skycat fetch` (new) | Once per release, offline | `SKYCAT_DATA_ROOT` read-only invariant, provenance |
| **D** Parity | `tests/` | CI / on demand | None. Dev dependency only |

A and B both put the network on a query path; C puts it on the ingestion path;
D puts it nowhere that ships. The difficulty ranking follows exactly that
ordering, and so does the blast radius of getting it wrong.

---

## 2. The legacy system, inventoried

This section is the reference material the rest of the report argues from.

### 2.0 Three lineages, four copies

The same eleven catalog plugins exist in four places under three distinct
lineages, and they are **not** identical. Reading only one of them gives an incomplete picture of the lookup
tables.

| Copy | Path | Status |
|---|---|---|
| **Afterglow (upstream)** | `afterglow-core/afterglow_core/resources/catalog_plugins/` (`master` @ `92aaf61`) | The original. Flask-config-driven; carries two declarative mechanisms the forks dropped (§2.3) |
| Afterglow (vendored) | `skynet/apps/afterglow/docs/legacy/resources/catalog_plugins/` | Byte-identical snapshot of the above, kept as reference |
| skynet "common" | `skynet/packages/py/skynet-db/skynet_db/runners/common/catalog_plugins/` | Older fork. The vault's [[Catalog Registry Reconciliation]] establishes this one as **vestigial** — its only query consumer is dead code |
| **skynet "optical"** | `skynet/…/optical_data_processing/catalogs/` | **Authoritative in production.** The fork the pipeline actually calls |

The Afterglow → skynet fork replaced Flask's `current_app.config` with module
constants and marshmallow's `CatalogSource` with a pydantic one. Both
substitutions changed behaviour, and §2.3 and §2.4 are the consequences.

### 2.1 The plugin contract

`Catalog` (`catalog.py`, 45 lines) is a bare abstract base: `name`,
`display_name`, `num_sources`, `mags`, `filter_lookup`, and three query methods
(`query_objects`, `query_box`, `query_circ`) that raise `NotImplementedError`.
The constructor's only behaviour is merging a caller-supplied `filter_lookup`
over the class-level one.

`VizierCatalog` (`vizier_catalogs.py`, 352 lines) implements that contract
against `astroquery.vizier.Vizier`. A subclass is almost pure declaration — four
class attributes and, occasionally, a `table_to_sources` override:

| Attribute | Meaning |
|---|---|
| `vizier_catalog` | VizieR catalog designation, e.g. `II/336` |
| `row_limit` | Default cap passed to `Vizier(row_limit=…)` |
| `col_mapping` | `{CatalogSource attr: VizieR column name *or* Python expression}` |
| `mags` | `{band: [mag_col, err_col]}` |
| `extra_cols` | Columns to request that map to no attribute |
| `sort` | VizieR sort keys, `+col` / `-col` |
| `filter_lookup` | `{FITS FILTER value: band name *or* transformation expression}` |

The base `__init__` derives the VizieR column request list by `compile()`-ing
every `col_mapping` value as a Python expression and harvesting `co_names`,
skipping anything that is a numpy export or a `str` method; a `SyntaxError`
means the value was a literal column name rather than an expression, and it is
taken verbatim. That is how `'RAJ2000/15'` yields a request for `RAJ2000` while
`'recno'` yields `recno`.

`table_to_sources` runs the inverse at row scale: for each `col_mapping` entry
it tries `row[expr]` first (fast path, literal column) and falls back to
`eval(expr, {**numpy.__dict__, **row_columns}, {})`. Magnitudes are read from
the `mags` pairs, with a `"'"` → `"_"` retry because VizieR renames `g'mag` to
`g_mag`, and are **kept only when `val and val < 99`** — the sentinel filter that
turns VizieR's 99.99 "no measurement" into an absent band. A source with no
surviving magnitudes is dropped entirely.

### 2.2 The lookup tables

Eleven providers are registered. Ten are `VizierCatalog` subclasses; SDSS uses
`AfterglowSDSS` SQL-region queries and is out of scope for anything astroquery
would do.

| Key | `name` | VizieR ID | `num_sources` | `row_limit` | `sort` | Bands (`mags` keys) |
|---|---|---|---|---|---|---|
| `APASS` | `APASS` | `II/336` | 61,176,401 | 1000 | `+Bmag` | B, V, gprime, rprime, iprime |
| `PanSTARRS` | `PanSTARRS` | `II/349` | 1,919,106,885 | 5000 | `+rmag` | g, r, i, z, y |
| `SDSS` | `SDSS` | — (SQL) | — | — | — | u, g, r, i, z |
| `SkyMapper` | `SkyMapper` | `II/358/smss` | 285,159,194 | 5000 | `+rPSF` | u, v, g, r, i, z |
| `Landolt` | `Landolt` | `II/183A` | 526 | *none* | — | V + B-V, U-B, V-R, R-I, V-I |
| `Stetson` | `StetsonGlobs` | `J/MNRAS/485/3042/table4` | 4,890,955 | 10000 | `+Rmag` | U, B, V, R, I |
| `2MASS` | `2MASS` | `II/246` | 470,992,970 | 5000 | `+Jmag` | J, H, K, B, R |
| `Tycho` | `Tycho2` | `I/259` | 2,539,913 | 1000 | `+BTmag` | B, V |
| `UCAC` | `UCAC5` | `I/340` | 107,758,513 | 5000 | `+f.mag` | Open, G, R, J, H, K |
| `USNO` | `USNOB1` | `I/284` | 1,045,175,762 | 5000 | `+B1mag` | B, R, B1, B2, R1, R2 |
| `VSX` | `VSX` | `B/vsx/vsx` | 2,115,593 | *none* | — | ~40 band names, all empty values |

Note the registry-key/`name` mismatches (`Tycho`→`Tycho2`, `UCAC`→`UCAC5`,
`USNO`→`USNOB1`, `Stetson`→`StetsonGlobs`). The vault's
[[Catalog Selection and Provider Registry]] flags this as a live failure mode:
lookup-based filter resolution keyed on one and not the other can miss a
catalog's custom map, with UCAC's wildcard as the clearest case.

`num_sources` are hardcoded and stale. The VSX entry says 2,115,593; Skycat's
`approx_row_count` for the same family is 10,300,000. Nobody is wrong — VSX is
a living index and the two numbers are snapshots years apart — which is exactly
the point developed in §5.2.

#### Column mappings

| Provider | `id` | `ra_hours` / `dec_degs` |
|---|---|---|
| APASS | `recno` | `RAJ2000/15`, `DEJ2000` |
| PanSTARRS | `objID` | `RAJ2000/15`, `DEJ2000` |
| SkyMapper | `ObjectId` | `RAICRS/15`, `DEICRS` |
| 2MASS | `_2MASS` | `RAJ2000/15`, `DEJ2000` |
| UCAC5 | `SrcIDgaia` | `RAJ2000/15`, `DEJ2000` |
| USNO-B1.0 | `USNO-B1.0` | `RAJ2000/15`, `DEJ2000` |
| Tycho-2 | `"{:04d}-{:05d}-{}".format(TYC1,TYC2,TYC3)` | `RAmdeg/15`, `DEmdeg` |
| Landolt | `Star` | sexagesimal `str.split()` expression |
| StetsonGlobs | `Star` | sexagesimal `str.split()` expression |
| VSX | `OID` (coerced to `str`) | `RAJ2000/15`, `DEJ2000` |

Three things to notice. **RA is in hours at this interface** — every mapping
divides by 15, and Skycat is degrees throughout. **Two providers parse
sexagesimal in an `eval`'d string expression**, including a sign fix-up
(`(1 - 2*(DEJ2000.strip().startswith("-")))`) that exists because `-00 30 00`
loses its sign through `int()`. Skycat's `landolt.py` and `stetson.py` parsers
reimplement precisely this arithmetic in normal Python, deliberately, so the two
backends agree. **Tycho-2's `id` is a `str.format` call** evaluated per row —
the `co_names` harvester picks up `TYC1`, `TYC2`, `TYC3` as columns and
`format` resolves off the string literal.

#### Filter lookups (photometric transformations)

This is the part with the most science in it and the least test coverage.

| Provider | Key | Expression |
|---|---|---|
| APASS | `U` | `B + 0.78*(uprime - gprime) - 0.88` — Jester+ 2005, Jordi+ 2005 |
| APASS | `R` | `rprime - 0.2936*(rprime - iprime) - 0.1439` — Lupton 2005 |
| APASS | `I` | `iprime - 0.3136*(rprime - iprime) - 0.3539` — Lupton 2005 |
| APASS | `g'`,`r'`,`i'` | naming aliases → `gprime`, `rprime`, `iprime` |
| PanSTARRS | `gprime` | `0.94*(g + 0.013 + 0.145*(g-r) + 0.019*(g-r)**2) + 0.06*(r - 0.001 + 0.004*(g-r) + 0.007*(g-r)**2) + 0.0318` |
| PanSTARRS | `rprime`,`iprime`,`zprime` | same shape — Tonry+ 2012 P1→SDSS, then the inverse of the SDSS DR1 `g'r'i'z'`→`griz` equations |
| SkyMapper | `uprime`…`zprime` | constant offsets (`u - 0.069`, `g + 0.088`, `r - 0.006`, `i + 0.001`, `z - 0.005`), row F5V of the SkyMapper transformation table |
| 2MASS | `B`,`V`,`R`,`I` | cubic polynomials in `(J-K)`, e.g. `V = 0.1496 + J + (3.5143 + (-2.3250 + 1.4688*(J-K))*(J-K))*(J-K)` |
| UCAC5 | `*` | wildcard → `Open` (integral bandpass) |

The registry (`catalogs/__init__.py`) then **overlays a second, larger
`filter_lookup`** per provider at construction time. APASS gets 30 additional
keys: `Open`/`Clear`/`Lum` → `V`; `gp`/`rp`/`ip` naming aliases;
astrophotography filters (`Red`, `Green`, `Blue`, `Halpha`, `OIII`, `SII`,
`Hbeta`); and sixteen curriculum-education combinations (`R+Red`, `R,Red`,
`Red+R`, `Red,R`, `Green+V`, …). PanSTARRS gets eight naming aliases; Landolt,
Stetson and Tycho get the `Open`/`Clear`/`Lum` → `V` trio.

Two observations that matter for Skycat:

1. **These are consumer-layer concerns.** They map a *FITS `FILTER` header
   value* to a reference band. Skycat has never seen a FITS file and does not
   know what a curriculum filter wheel is labelled. Importing this table into
   Skycat would be the package acquiring an opinion about instrument metadata.
2. **Skycat already declined to own the analogous derivation.** `models/landolt.py`
   stores V plus the five color indices and explicitly does *not* store U/B/R/I,
   because "the remote VizieR provider derives them from V + the colors at query
   time, and the local provider reproduces that exact derivation in
   normalization". The deriving code lives in `skynet-db`'s `normalize.py`, not
   here. That boundary is deliberate and worth keeping.

### 2.3 Declarative catalog definition — the mechanism the forks dropped

This is the most directly relevant precedent in the whole codebase, and it
survives only in upstream Afterglow (`afterglow_core/default_cfg.py`). Two
config variables let an operator extend the catalog set **without writing
code**.

**`CATALOG_OPTIONS`** — per-catalog overrides merged over an existing plugin's
class attributes in `Catalog.__init__`:

```python
CATALOG_OPTIONS = {
  'APASS': {'vizier_server': 'vizier.u-strasbg.fr',
            'filter_lookup': {'Open': '0.8*V + 0.2*R'}},
}
```

**`CUSTOM_VIZIER_CATALOGS`** — whole new catalogs as plain dicts. At import time
`vizier_catalogs.py` does `type(classname, (VizierCatalog,), kw)` for each
entry, sanitising the name into a Python identifier and appending it to
`__all__`. The shipped (commented-out) example is NOMAD-1:

```python
CUSTOM_VIZIER_CATALOGS = [
    {'name': 'NOMAD', 'display_name': 'NOMAD-1', 'num_sources': 1117612732,
     'vizier_catalog': 'I/297', 'row_limit': 5000,
     'col_mapping': {
         'id': 'NOMAD1', 'ra_hours': 'RAJ2000/15', 'dec_degs': 'DEJ2000',
     },
     'mags': {'B': 'Bmag', 'V': 'Vmag', 'R': 'Rmag', 'J': 'Jmag',
              'H': 'Hmag', 'K': 'Kmag'},
    },
]
```

That is a 1.1-billion-row catalog added in twelve lines of configuration. It is
the strongest available demonstration that **a VizieR catalog is data, not
code** — and therefore the strongest argument that if Skycat ever does Option B,
the family table should be declarative rather than a class per catalog.

It is also a demonstration of what goes wrong when a declarative table has no
schema and no tests. **The shipped example does not work.** `mags` values are
consumed by `table_to_sources` as `mag_col, mag_err_col = item[:2]`, which
expects a sequence of one or two column names. Given the example's plain string
`'Bmag'`, `item[:2]` is `'Bm'`, so `mag_col = 'B'` and `mag_err_col = 'm'`;
`row['B']` raises `KeyError`, the retry raises `KeyError`, and the enclosing
`except Exception: pass` drops the magnitude. With every band dropped, the
`if source.mags:` guard drops every *source* — a NOMAD catalog configured
exactly as documented returns zero rows, silently. The real plugins all use
lists (`['Bmag', 'e_Bmag']`, or `['Bmag']` which takes the `ValueError` →
`item[0], None` path), and VSX's empty-string values survive only because
`''[0]` raises `IndexError` into the `continue`.

Both mechanisms were dropped in the skynet forks: `CUSTOM_VIZIER_CATALOGS` and
the `current_app.config` lookups are gone, replaced by the four-line
`config.py`. The skynet registry (§2.2) re-implements the *overlay* half of
`CATALOG_OPTIONS` by passing `filter_lookup=` to each constructor, but there is
no longer any way to add a catalog without adding a module.

### 2.4 Machinery around the query

Five pieces of the legacy integration are worth reading as warnings rather than
as designs to port.

**astroquery's cache had to be monkey-patched.** `vizier_catalogs.py` replaces
`astroquery.query.to_cache` and `astroquery.query.AstroQuery` at import time so
that a caching failure under concurrent access is swallowed rather than raised,
and so that files older than `VIZIER_CACHE_AGE_DAYS` (30) are pruned on write.
The replacements are four bare `except Exception: pass` blocks reaching into
another package's internals. Under Skycat's ruff configuration this would not
merge: `BLE001` (blind `except Exception`) and `S110` (`try`/`except`/`pass`)
are both selected and are errors, not style nits. Making it pass would require
`per-file-ignores` and a justification comment — which is the correct process,
but it is a signal about what integrating this library costs.

**Cache hit rates required rounding the query.** `query_box`/`query_circ`
quantise the field centre to 10 arcsec, the radius to 0.2 arcmin, and clamp
declination, "to avoid cache misses for querying the same field with tiny
differences in RA/Dec and size". A Skycat cone search returns rows within
`radius_deg` of exactly `(ra, dec)`; a remote-backed one with caching on returns
rows for a nearby cone of a slightly different size. That is a silent,
position-dependent difference in the returned set, not a rounding of a displayed
number.

**And the two lineages disagree about whether caching is even on.** Afterglow
defaults `VIZIER_CACHE = True` and `VIZIER_SERVER = 'vizier.cfa.harvard.edu'`,
read per-instance from `current_app.config`. The skynet fork hardcodes
`cache: bool = False` and `vizier_server = None` as class attributes, and no
subclass overrides either — which means its `config.py` constants
`VIZIER_SERVER = 'vizier.cds.unistra.fr'` and `VIZIER_CACHE_ENABLED = True` are
**dead**: nothing imports them (only `VIZIER_CACHE_AGE_DAYS` is imported). So in
production the fork caches nothing, the quantisation code above never executes,
the `to_cache` monkey-patch is inert, and the server is whatever astroquery
defaults to rather than what `config.py` appears to declare. Anyone porting this
would reasonably read `config.py` and conclude the opposite of what runs.

**Porting these plugins across a schema boundary failed silently — twice.** The
skynet fork's `vsx_catalog.py` differs from Afterglow's by exactly two defensive
patches, both added after the fact, both for the same root cause: Afterglow's
marshmallow `CatalogSource` became a pydantic model, so `id=row['OID']` (an int)
raised a validation error, and `setattr(source, 'G', …)` for a passband with no
declared field raised too. Neither surfaced as an error. The comment left behind on the fix says it
plainly: the validation error was one "which `_filter_variable_stars` swallows,
silently disabling variable-star filtering". A whole safety feature was off,
with no failure visible anywhere. Skycat's row shape is a third schema again (§5.4); the same
class of silent failure is the default outcome, not the unlucky one.

**Constraints go over the wire as strings.** `query_region` splits its
`constraints` dict into `column_filters` (entries with a value) and `keywords`
(entries with `None`). SkyMapper's subclass uses this to force `flags = '0'`,
dropping sources with non-zero SExtractor flags. The VizieR filter syntax is its
own small language (`>10`, `<=5`, `!=0`, ranges) with no relationship to
Skycat's validated `QualityFilter`.

### 2.5 How selection works today

`select_catalogs_for_filter` / `catalog_supports_filter` keep only catalogs whose
`mags` keys — or whose `filter_lookup` expression has all its dependencies
present — can resolve the image's FITS `FILTER`, preserving priority order; if
nothing matches, it logs a warning and falls back to the full list rather than
hard-failing. `resolve_ref_mag_for_filter` in `runners/utils.py` then does the
real resolution: direct band → per-catalog lookup → wildcard `*` → an optional
preferred-band fallback chain (`V, rprime, gprime, B, iprime`, then any).

Preselection is not the final truth: a catalog can pass stage 1 and still lose
every source in stage 2. VSX declares ~40 bands and looks maximally
filter-compatible, but is **not a calibration catalog** — it is used only in
`_filter_variable_stars()`, and letting it into a calibration list is a known
foot-gun.

### 2.6 The local-first layer already exists — downstream

The routing this report might otherwise propose building has already been built,
in `skynet-db`, as a `LocalFirstCatalog` mixin placed in front of the VizieR
providers (`APASSCatalog(LocalFirstCatalog, VizierCatalog)`), overriding only
`query_circ` / `query_box` / `query_objects`:

```
provider.query_*  →  LocalFirstCatalog._route:
   remote_only / family-not-local ──────────────▶ remote (VizieR)
   untranslatable constraints ── fallback? ─▶ remote | no ─▶ raise
   local unavailable ─────────── fallback? ─▶ remote | no ─▶ raise
   local ok ─▶ normalize → CatalogSource        (served from PostGIS)
   local healthy-but-empty ─ fallback_on_empty? ─▶ remote else []
```

with modes `local_first` (default) / `local_only` / `remote_only`, plus
`SKYCAT_REMOTE_FALLBACK`, `SKYCAT_FALLBACK_ON_EMPTY`, `SKYCAT_LOCAL_FAMILIES`,
and per-family release pins (`SKYCAT_APASS_RELEASE=DR10`).

**Caveat on currency:** that code is not in the current `skynet` working tree.
`catalogs/local/` contains only a stale `__pycache__` (`backend`, `config`,
`engine`, `errors`, `health`, `normalize`, `routing`) — the sources live on
`feat/skycat` / `add-landholt-and-stetson`, unmerged into
`pipeline/analysis-descriptive-split`. Confirm the branch state before treating
it as shipped. What the artifacts do establish is that the design was worked
through and implemented once already, at the consumer layer, where the
`CatalogSource`/`mags` shape it normalises into actually lives.

---

## 3. What Skycat is today, and what that constrains

The contracts a remote path would have to satisfy or explicitly break.

**The row shape is typed columns, not bands.** `cone_search` returns
`dict(row)` over the release's actual table columns —
`native_id, ra_deg, dec_deg, johnson_v_mag, johnson_v_err_mag, sloan_g_mag, …,
extra, separation_deg`. There is no `mags` dict, no `ra_hours`, no
`CatalogSource`. Column names carry units by convention. A VizieR row for a
family Skycat does not model has no such columns to fill.

**Spatial work happens in the database, always.** Decision 0001 is accepted and
implemented: no SQLite, no Python-side spatial fallback. `separation_deg` comes
from `ST_Distance(geom, point, false)`.

**Every answer is attributed to a release.** `resolve_release_for_query` returns
a `ResolvedRelease` (family, data table, release id, name, state) before a query
runs, and `--release` exists so results are reproducible. A remote row has no
release.

**The dependency set is five libraries.** `sqlalchemy`, `geoalchemy2`,
`psycopg[binary]`, `alembic`, `click`. No numpy. No astropy. That is not an
accident for a package whose deployment target is a one-shot Kubernetes Job.

**There is deliberately no plugin framework.** Adding a family is six explicit
touch points. `catalog_defs.py` is the single source of truth for what exists,
and `importer_available=False` is how a family that is *declared* but not
*ingestible* fails fast rather than half-importing.

**Exit codes are a contract.** `CatalogConfigError` → 2;
`CatalogQueryError`/`IngestionError`/`ReleaseStateError` → 1. A network failure
class would need to land somewhere in that taxonomy, and the K8s Job reads it.

**`SKYCAT_DATA_ROOT` is read-only.** "Skycat never downloads anything.
Mirroring is a deliberate, separate step, which is what makes a release
reproducible — an importer that fetched from the network would produce a
different result on a different day."

---

## 4. Option-by-option feasibility

### Option A — query-time remote fallback

`CatalogReader.cone()` tries PostGIS; on unavailability (or emptiness) it queries
VizieR, normalises, and returns rows in the same shape.

**Feasible?** Technically, for the four families Skycat models — `apass`, `vsx`,
`landolt`, `stetson` all have VizieR counterparts, and mapping VizieR columns
onto Skycat's typed columns is mechanical.

**Recommended?** No, for three independent reasons, any one of which is
sufficient.

1. **Decision 0001 already rejects this argument in its general form.** Its
   Python-fallback section is not about maths; it is about failure semantics:
   *"A fallback is used when it matters most. The path taken when the fast one is
   unavailable is the path taken during an incident, on an unfamiliar deployment,
   by whoever is on call. A slow, subtly-different query path is worse than a
   clear failure to connect, because the failure gets diagnosed and the wrong
   answer does not."* Every word of that applies to a remote fallback, which is
   additionally subject to CDS availability and rate limiting. Adding one would
   require superseding 0001 or writing a decision record explaining why network
   fallback is categorically different from in-process fallback. It is not
   obvious that it is.

2. **The data would not match** — see §5.2. This is the concrete blocker.

3. **The layer already exists downstream**, in the place where its output shape
   is native (§2.6). Moving it into Skycat inverts the dependency: Skycat would
   have to learn the consumer's `CatalogSource`/`mags` vocabulary, or invent a
   third shape that neither side wants.

### Option B — remote-only families

New coverage: PanSTARRS, 2MASS, Tycho-2, UCAC5, USNO-B1.0, SkyMapper served
straight from VizieR, with no local ingestion. `architecture.md` already names
these as "future families" following the same family/release pattern.

**Feasible?** Yes, and this is the option with the most genuine upside. It also
sidesteps §5.2 entirely — a family with *no* local release cannot disagree with
one.

**The design constraint that keeps it honest:** it must not pretend to be the
local path. `CatalogReader.cone("panstarrs", …)` returning remote rows through
the same method that returns local ones means every caller's error handling,
latency budget, timeout semantics and reproducibility guarantee silently change
based on which family string was passed. Prefer either a separate class
(`RemoteCatalogReader`) or an explicit opt-in with a distinguishing key in every
returned row (`_source: "remote"`, `_release: None`).

**The shape it should take, if it happens:** declarative, per §2.3. Afterglow
already proved that a VizieR catalog is twelve lines of data — designation, row
limit, column mapping, band mapping. Ten classes that differ only in their class
attributes is a worse encoding of the same table.

This runs into a stated position: *"There is deliberately no plugin or
registration framework; adding a family is six explicit touch points."* The
tension resolves cleanly, though, because the six touch points are all about
things a remote family does not have — a parser, a typed model, a migration, a
validation module. A remote family has no local schema to design, so the reason
for the explicitness does not apply to it. That argument belongs in a decision
record, not in an implementation PR.

**Recommended?** Defer until a consumer asks. `skynet-db` has working remote
providers for all six; Skycat would be re-housing code that is not currently
broken, and would inherit the maintenance of the transformation tables in §2.2
along with it.

### Option C — automated acquisition

**Feasible but wrong-tool.** astroquery's VizieR interface is a cone/region/
object query API. Pulling 128M APASS rows or 4.9M Stetson rows through it means
either `row_limit=-1` on an all-sky query — which CDS will refuse or throttle,
and rightly so — or tiling the sky into millions of cone queries. The bulk
distribution channel is `https://cdsarc.cds.unistra.fr/ftp/<designation>/`, and
`provenance.md` already documents the `wget --timestamping` recipe for it,
including the `--cut-dirs` depth trap and why preserving upstream mtimes matters
for the manifest checksum. APASS comes from AAVSO, not CDS, at all.

The one genuinely useful thing astroquery could contribute here is *metadata*:
`Vizier.find_catalogs()` / `get_catalog_metadata()` could verify that a
`reference_url` designation still resolves and has not been superseded
(`II/183` → `II/183A`), which `provenance.md` explicitly warns about. That is a
lint check, not an acquisition path, and it belongs in D.

### Option D — parity harness

A `postgis`-marked, network-marked, dev-only test that takes a handful of fixed
fields, runs `CatalogReader.cone()` against a local release, runs the equivalent
VizieR cone for the designation that release mirrors, and asserts agreement on
positions and magnitudes within tolerance.

**Feasible?** Yes, and the affordances already exist. `--release` is documented
as a parity-testing affordance precisely so `landolt` can be compared against
`II/183A = Landolt 1992` while `2009` is active. Skycat's Landolt and Stetson
parsers were written to reproduce the remote coordinate arithmetic exactly; a
parity test is what makes that claim checkable rather than asserted.

**Recommended?** Yes. It is the only option that converts astroquery from a
runtime dependency into a verification instrument, which is the role it is
actually good at here.

Where it must be careful: the comparison is only meaningful where local and
remote mirror the *same* upstream snapshot. That is `landolt` `1992` and
`stetson` `stetsonglobs`. It is **not** `apass` (§5.2) and it is only loosely
`vsx` (a living index, sampled at different times). Encode that as data — a
table of (family, release, VizieR designation, comparable: yes/no/approximate) —
so the untestable cases are documented rather than quietly omitted.

---

## 5. The difficulties, in detail

Ordered by how likely each is to sink an implementation.

### 5.1 Dependency weight

astroquery 0.4.11 (current stable; `skynet` pins 0.4.10 across three
requirements files) declares:

```
numpy>=1.20, astropy>=5.0, requests>=2.19, beautifulsoup4>=4.8,
html5lib>=0.999, keyring>=15.0, pyvo>=1.5
```

astropy transitively adds `pyerfa`, `PyYAML`, `packaging`; `keyring` on Linux
adds `SecretStorage`/`jeepney`. So a package that today installs five
database-and-CLI libraries would install roughly twenty, including a numerical
stack and a **desktop credential-store library, inside a headless Kubernetes
Job**. Wheel size, image build time, and CVE surface all move accordingly.

There is a second-order problem. The vault records that Skycat was deliberately
*not* made a hard dependency of `skynet-db`, because doing so "would invalidate
every service's frozen `uv.lock`". If Skycat takes a hard astroquery dependency
with a different constraint than `skynet`'s pinned `0.4.10`, the same class of
lockfile conflict reappears from the other direction, in a repository that
cannot resolve it unilaterally.

**Mitigation, and it is a good one:** an optional extra. `skycat[remote]` or
`skycat[dev]` keeps the default install at five dependencies and makes the
import guarded. Option D needs only the `dev` extra. Options A and B do not get
off this cleanly — a *runtime* optional dependency means "does the fallback
exist" becomes deployment-dependent, which is precisely the ambiguity §4A warns
about.

### 5.2 Release identity — the concrete blocker

**VizieR does not carry the APASS release Skycat serves.**

| Family | Skycat releases | Legacy VizieR ID | What VizieR actually serves |
|---|---|---|---|
| `apass` | DR6, **DR10** | `II/336` | **DR9** — and DR10 is not queryable through VizieR at all; AAVSO distributes it directly |
| `landolt` | **1992**, 2009 | `II/183A` | 1992 ✓ (2009 = `J/AJ/137/4186`, which the legacy plugin does not use) |
| `stetson` | stetsonglobs | `J/MNRAS/485/3042/table4` | same ✓ |
| `vsx` | current | `B/vsx/vsx` | a mirror of a living index — no version, no snapshot identity |

For APASS the overlap is **empty**. Skycat's DR6 and DR10 versus VizieR's DR9:
a remote fallback on the `apass` family would serve a data release the local
store does not contain and cannot be compared against, under the same
`ResolvedRelease` API that exists to make answers reproducible. The photometry
would be plausible and the provenance would be a fiction. APASS is also the
largest and most-used family, so this is not an edge case — it is the main case.

VSX is a softer version of the same problem: both sides are real VSX, sampled at
unrelated times. The legacy plugin's stale `num_sources` (2.1M vs Skycat's 10.3M
`approx_row_count`) is that gap made visible.

Only Landolt-1992 and Stetson are cleanly comparable, and both are small
standards catalogs where the local store is never the bottleneck.

Any Option A or B design has to answer: *what `release` does a remote row claim?*
The honest answers are `None` (breaking the invariant that every row is
attributed) or a synthetic `"vizier:II/336"` (honest, but then the row is not
from any release the registry knows about, and `--release` cannot select it).
Neither is free.

### 5.3 Query semantics do not survive the trip

Skycat's cone contract, clause by clause, against what VizieR gives back:

| Skycat | VizieR / astroquery | Gap |
|---|---|---|
| `separation_deg` per row, from `ST_Distance(…, false)` | not returned | Compute in Python — the one place decision 0001's ban is arguably not implicated (no filtering), but it is still a second implementation of the same number |
| `order_by="johnson_v_mag"` → brightest-first, NULLs last, separation tiebreak | `sort=['+Bmag']` server-side, no null policy, no tiebreak | Different row set under `limit`, not just a different order |
| `limit` after ordering | `row_limit` applied by the server, semantics unspecified against `sort` | A capped remote query and a capped local query select different stars in a dense field |
| `QualityFilter` — allow-listed column, one of six operators, bound parameter | `column_filters` — a string mini-language | Not all filters translate; `skynet`'s router has an explicit "untranslatable constraints" branch for exactly this |
| `mag_min`/`mag_max` on a typed column | expressible as a `column_filter` | Translatable |
| `batch_crossmatch` — `COPY` inputs to TEMP, one LATERAL join, one round trip | no batch primitive | N HTTP requests. For a 5,000-source frame this is not a slow version of the same thing; it is a different operation |
| 30s statement timeout | `Vizier(timeout=…)`, plus retries, plus DNS | The timeout contract means something different |
| Nearest-first default | server-defined | Under-specified on both sides, but differently |

`batch_crossmatch` is the one to weigh hardest. It is the operation the optical
pipeline actually needs at scale, and it has no remote analogue that is not a
loop.

### 5.4 Schema impedance

For a family Skycat models, the mapping is mechanical but lossy in both
directions:

- **Units and names.** `ra_hours` (÷15) versus `ra_deg`; `Vmag`/`e_Vmag` versus
  `johnson_v_mag`/`johnson_v_err_mag`; `g'mag` (VizieR-renamed to `g_mag`)
  versus `sloan_g_mag`.
- **Sentinels versus NULL.** The legacy adapter keeps a magnitude only when
  `val and val < 99`. Skycat's convention is that missing is `NULL`, never `0`,
  never `99.999`. These agree in intent, but `val and val < 99` also drops a
  genuine `0.0` magnitude — defensible for stellar photometry, still a rule that
  would have to be restated and tested on Skycat's side.
- **Dropped rows.** `table_to_sources` discards any source with no surviving
  magnitudes. A Skycat cone returns positional rows regardless. A remote-backed
  cone would silently return fewer rows than the equivalent local one, for
  reasons invisible in the response.
- **`extra` JSONB has no remote counterpart** — per-band observation counts, DR6
  `B-V`, `mobs`. Remote rows would carry `extra: null` where local rows carry
  data.
- **Derived bands.** Landolt: local stores V + five colors, remote returns the
  same, and U/B/R/I are derived by the *consumer* in both cases. Consistent
  today — and it stays consistent only as long as Skycat does not start deriving
  them to make remote rows "look complete".
- **`native_id` types differ.** APASS `recno` (int) versus Skycat `String(64)`;
  VSX `OID` needed an explicit `str()` coercion in the legacy code after a
  pydantic validation error silently disabled variable-star filtering. That
  comment is still in `vsx_catalog.py` and is worth reading as a case study in
  how these failures present (§2.4).

### 5.5 Reproducibility and provenance

Skycat's central claim is that a release is rebuildable and provable: manifest
or content checksum, `source_size_bytes`, `source_modified_at`,
`importer_version`, `internal_schema_version`, and a five-step chain of custody.

A remote row has none of it. Worse, the legacy cache-quantisation (§2.4) means
the *same* remote call can return different rows depending on cache state and on
rounding applied to the coordinates. Mixing that into a query path whose
documented purpose includes "re-deriving old calibrations" is a category error.

If Option B proceeds, remote rows should be marked at the row level, not just
documented — a consumer that persists a calibration must be able to tell from
the data whether it is reproducible.

### 5.6 Operational surface

- **Network egress from a K8s Job.** Currently unnecessary; NetworkPolicy,
  proxies and DNS all become Skycat's problem.
- **CDS availability and rate limits** become Skycat availability. VizieR has no
  SLA for programmatic bulk use, and the legacy code's whole caching apparatus
  exists to reduce load on it.
- **A new failure taxonomy.** Timeout, HTTP 5xx, malformed VOTable, rate-limited,
  DNS. Each must map onto exit codes 0/1/2 and onto `CatalogQueryError` or a new
  sibling — a change to a stable exception surface.
- **Caching.** astroquery caches to `astropy` config dirs, which are
  user-scoped, not container-scoped, and which the legacy code had to prune by
  hand. In a read-only container filesystem this needs explicit configuration.
- **`vizier_server` mirrors.** `vizier.cds.unistra.fr` versus
  `vizier.cfa.harvard.edu` (commented out in the legacy config) can return
  different snapshots. A third axis of "which data did I get".

### 5.7 Code-quality gates

Ported as-is, the legacy code does not pass this repository's CI:

| Legacy pattern | Skycat gate |
|---|---|
| `except Exception: pass` ×3 in the astroquery monkey-patch | `ruff` `BLE001`, `S110` — errors |
| `except Exception: val = None` in `table_to_sources` | `BLE001` |
| `eval(expr, ctx, {})` per row | No rule forbids it, but it is the opposite of `_quality_clause`'s allow-listed, parameter-bound approach |
| Untyped `mags: Dict[str, List[str]]` with `item[:2]` / `item[0]` fallbacks | `pyright` — the null-safety rules are errors and are at zero |
| `compile(expr, '<string>', 'eval').co_names` column harvesting | Works; needs a comment explaining *why*, per this repo's convention |
| `type(classname, (VizierCatalog,), kw)` from unvalidated config dicts (§2.3) | `pyright` cannot see the resulting classes at all; the `mags`-shape bug is exactly what a schema would have caught |

None of this is unfixable. It does mean "port the plugins" is a rewrite, not a
copy, and the rewrite has to re-derive the intent of expressions like the 2MASS
`(J-K)` cubics from papers that are cited only by a URL in a comment.

The `mags`-shape bug in §2.3 is the whole argument for typing the declarative
table if Option B proceeds: a frozen dataclass with `tuple[str, str | None]`
bands would have made the broken NOMAD example a startup error instead of a
catalog that silently returns nothing.

### 5.8 Testing

Network tests are non-hermetic: they fail when CDS is slow, they are the first
thing disabled when CI gets flaky, and once disabled they stop catching
anything. The workable pattern is recorded fixtures (VOTable responses committed
as test data) for the deterministic assertions, plus a separately-marked live
test that is allowed to be run on demand rather than per-commit — mirroring how
`postgis`-marked tests already work, including the `--require-postgis`
escalation that turns silent skipping into a hard failure when it matters.

`tests/test_docs.py` also asserts that every CLI flag and `CatalogReader` kwarg
shown in the stable docs exists. Any new flag or kwarg from this work ships with
its documentation in the same commit.

### 5.9 Configuration namespace collision

`skynet-db`'s routing layer already uses `SKYCAT_BACKEND`,
`SKYCAT_REMOTE_FALLBACK`, `SKYCAT_FALLBACK_ON_EMPTY`, `SKYCAT_LOCAL_FAMILIES`,
and `SKYCAT_<NAME>_RELEASE` — the *consumer's* routing config, in Skycat's
namespace but not read by Skycat, whose own reader is `SKYCAT_DB_*` plus
`SKYCAT_DATA_ROOT` / `SKYCAT_WORK_ROOT`. If Skycat introduces its own remote
flags it will be the second thing reading `SKYCAT_*`, and a `SKYCAT_BACKEND` set
for the pipeline could be silently reinterpreted. Namespace any new variables
distinctly (`SKYCAT_REMOTE_*`) and say so in the README's configuration table.

---

## 6. Benefits

Stated at full strength, because the recommendation is mostly negative and a
one-sided case is not worth reading.

1. **Coverage without ingestion.** Six catalogs (PanSTARRS, 2MASS, Tycho-2,
   UCAC5, USNO-B1.0, SkyMapper) become reachable with no mirror, no disk, no
   import, no migration. PanSTARRS alone is 1.9 billion rows — a local mirror is
   a serious infrastructure commitment, and for occasional use it is
   unjustifiable. This is the strongest argument in the document.
2. **One entry point for consumers.** A pipeline could ask `CatalogReader` for
   any catalog and stop caring which are mirrored — the local-first goal, with
   the routing decision made once instead of per-consumer.
3. **Verifiable parity** (Option D). Skycat's parsers claim byte-exact agreement
   with the remote provider's coordinate arithmetic and Landolt derivation.
   Today that is a claim in a docstring. With a harness it is a test.
4. **Upstream drift detection.** `find_catalogs`/`get_catalog_metadata` can
   catch a superseded designation (`II/183` → `II/183A`), which `provenance.md`
   names as a specific hazard.
5. **Graceful degradation during an incident.** A worthwhile property in the
   abstract — and the one decision 0001 argues hardest against, on the grounds
   that a degraded path used during an incident is the worst place for a subtly
   different answer.
6. **Cold-start and pre-mirror development.** Working against a family before
   its data is mirrored, without provisioning 128M rows.
7. **Long-tail coverage.** VizieR has tens of thousands of catalogs. A generic
   remote path makes a one-off comparison against an arbitrary designation
   possible without adding a family.
8. **Declarative extension.** Afterglow's `CUSTOM_VIZIER_CATALOGS` (§2.3) adds a
   1.1-billion-row catalog in twelve lines of configuration, with no code and no
   deploy. Nothing on Skycat's local path can ever be that cheap, because a local
   family needs a parser, a table and a migration. This is the one capability
   remote access offers that local-first structurally cannot match.

## 7. Drawbacks

1. **It contradicts the package's stated identity** — "build and query versioned
   *local* PostgreSQL/PostGIS databases". The local-first architecture exists
   *because* astroquery could not go local.
2. **Two answer paths, one API** — the divergence problem decision 0001
   identifies for SQLite, with network flakiness added.
3. **The data does not match where it matters most** (§5.2). APASS: zero overlap.
4. **~4× the dependency count**, including numpy, astropy and a desktop keyring
   library, in a container image (§5.1).
5. **External availability becomes internal availability.** CDS uptime and rate
   limiting become Skycat's uptime.
6. **Reproducibility is diluted.** Provenance, checksums and release attribution
   have no remote counterpart (§5.5).
7. **`batch_crossmatch` has no remote analogue** — the one operation the pipeline
   most needs at scale (§5.3).
8. **Science-layer coupling.** Owning the filter-lookup transformation tables
   means Skycat acquires opinions about FITS filter names and photometric
   transformations, and inherits the known registry-drift and key/name-mismatch
   bugs along with them (§2.2, §2.5).
9. **Duplication.** The routing layer exists downstream (§2.6). Two
   implementations of local-first is worse than either one.
10. **Test suite becomes non-hermetic** (§5.8).
11. **Namespace collision with the consumer's existing `SKYCAT_*` routing
    variables** (§5.9).

---

## 8. Recommendation

### Do now — Option D, the parity harness

Scope, roughly 2–4 days:

1. `astroquery` in the `dev` extra only. No runtime dependency; no change to
   `dependencies` in `pyproject.toml`.
2. A new test module, marked `postgis` **and** a new `network` marker, skipped by
   default like `postgis` is.
3. A data table of comparable (family, release, VizieR designation) triples —
   `landolt`/`1992`/`II/183A` and `stetson`/`stetsonglobs`/`J/MNRAS/485/3042/table4`
   as `comparable`; `apass` and `vsx` recorded as **not comparable**, with the
   reason inline. The exclusions are the most valuable rows in the table.
4. For each comparable pair: a fixed field, a local cone, a remote cone,
   assertions on matched-source count, position agreement (sub-milliarcsecond —
   these are the same numbers, arrived at by different code), and magnitude
   agreement.
5. A committed-VOTable variant so the mapping logic is tested without a network.

This gives back exactly what is currently unverified: proof that the local
parsers reproduce the remote provider's arithmetic.

### Do not do — Options A and C

C is solved: `wget` against `cdsarc.cds.unistra.fr/ftp/` is the documented,
correct bulk path, and astroquery is not a bulk tool.

A should not proceed without first writing a decision record that supersedes or
carves out 0001, and that record has to answer §5.2. If someone wants remote
fallback for the optical pipeline, the right place is the layer that already has
it (§2.6).

### Reconsider later — Option B

Revisit when a consumer names a specific catalog it needs and cannot mirror.
Then:

- A **separate** API surface (`RemoteCatalogReader`), not an overload of
  `CatalogReader.cone()`.
- Every row carries `_source: "remote"` and a designation string.
- Optional runtime extra `skycat[remote]`, with a clear error — not a silent
  fallback — when it is not installed.
- Start with one family that has **no** local release, so §5.2 cannot bite.
  Tycho-2 (`I/259`, 2.5M rows, two bands) is the cleanest first candidate: small
  schema, no transformation expressions, no local counterpart.

---

## 9. What would change the verdict

Written so this can be judged against evidence rather than re-argued.

**Would justify reopening A:**

- VizieR publishing APASS DR10, closing §5.2's gap. (Track the AAVSO/CDS status;
  it has been "pending" for some time.)
- A documented incident where PostGIS unavailability caused a pipeline outage
  that a remote path would have prevented — weighed against decision 0001's
  argument that the wrong answer is worse than the clear failure.
- The downstream `LocalFirstCatalog` layer being retired, leaving no home for
  routing.

**Would justify accelerating B:**

- A consumer needing PanSTARRS or 2MASS photometry with no plan to mirror it.
- A second consumer of Skycat that has no `skynet-db` provider layer of its own.

**Would not justify either:**

- "It would be nice to have everything in one place."
- Making local development easier without a database — that is decision 0001's
  explicitly-rejected reasoning, and it applies verbatim.

---

## 10. Open items to verify before implementing

1. **Branch state of the downstream routing layer.** `catalogs/local/` in
   `skynet` has only stale bytecode on the current branch. Confirm whether
   `feat/skycat` / `add-landholt-and-stetson` merged, and whether
   `SKYCAT_LOCAL_FAMILIES` in the Compose worker is still pinned to `APASS,VSX`
   (excluding Landolt and Stetson) — the vault lists that as an open confirm
   item.
2. **APASS DR10 on VizieR.** Re-check; it is the single fact that most changes
   this analysis.
3. **Landolt 2009 designation.** `J/AJ/137/4186` is assumed here from
   `catalog_defs.py`'s description; the legacy plugin only knows `II/183A`.
   Confirm before adding a second comparable pair.
4. **astroquery version constraint.** `skynet` pins `0.4.10` in three
   requirements files; current stable is `0.4.11`. Whatever Skycat declares must
   not conflict where both are installed.
5. **VizieR TAP.** The `TAPVizieR` endpoint (via `pyvo`, already an astroquery
   dependency) may offer better batch semantics than the `viz-bin/votable` POST
   path the legacy plugins use — relevant only if B proceeds, but it would
   change the §5.3 crossmatch verdict if true.
6. **Which VizieR server production actually hits.** The skynet fork's
   `config.py` says `vizier.cds.unistra.fr`, but nothing reads it and
   `vizier_server = None` (§2.4), so the effective server is astroquery's
   default. Afterglow's default is `vizier.cfa.harvard.edu`. Confirm before any
   parity test compares against "the" VizieR — mirrors can hold different
   snapshots.
7. **Whether `CUSTOM_VIZIER_CATALOGS` is in use anywhere.** Afterglow ships it
   defaulting to `[]`, and the skynet forks dropped it. If a deployment
   populates it, there are catalog definitions in operator config that exist in
   no repository — and per §2.3, any of them written in the documented `mags`
   shape are silently returning nothing.

---

## References

**Skycat** — `skycat/client.py`, `skycat/query/cone.py`,
`skycat/query/crossmatch.py`, `skycat/registry/catalog_defs.py`,
`skycat/models/{apass,landolt,stetson,vsx}.py`, `pyproject.toml`;
[decisions/0001](../decisions/0001-postgresql-postgis-only.md),
[reference/architecture.md](../reference/architecture.md),
[reference/api-stability.md](../reference/api-stability.md),
[guides/provenance.md](../guides/provenance.md).

**Skynet** (`pipeline/analysis-descriptive-split`) —
`packages/py/skynet-db/skynet_db/runners/observation_asset_processing/optical_data_processing/catalogs/`
(`vizier_catalogs.py`, `catalog.py`, `config.py`, `__init__.py`, and the ten
provider modules), `runners/common/catalog_plugins/` (the vestigial fork),
`apps/afterglow/docs/legacy/resources/catalog_plugins/` (the vendored Afterglow
snapshot), `runners/utils.py`, `requirements.txt`.

**Afterglow Core** (`master` @ `92aaf61`) —
`afterglow_core/resources/catalog_plugins/vizier_catalogs.py` (the
`CUSTOM_VIZIER_CATALOGS` class factory), `afterglow_core/models/catalogs.py`
(the `Catalog`/`CatalogSource` marshmallow base and the `CATALOG_OPTIONS`
merge), `afterglow_core/default_cfg.py` lines 110–145 (the catalog config
block). `resources/data_provider_plugins/imaging_survey_provider.py` uses
`astroquery.skyview` for image cutouts — a separate concern, out of scope here.

**Vault** — `wiki/resources/entities/astroquery-VizieR.md`,
`wiki/resources/concepts/{Local-First Catalog Architecture,Catalog Selection and Provider Registry}.md`,
`wiki/resources/incoming/Astroquery VizieR Local Catalog Investigation.md`.

**Upstream** — [astroquery VizieR docs](https://astroquery.readthedocs.io/en/latest/vizier/vizier.html),
[astroquery on PyPI](https://pypi.org/project/astroquery/),
[VizieR II/336 (APASS DR9)](https://cdsarc.cds.unistra.fr/viz-bin/cat/II/336),
[AAVSO APASS DR10 download](https://www.aavso.org/download-apass-data),
[AAVSO: APASS DR10 VizieR schedule](https://www.aavso.org/apass-dr10-vizier-schedule).

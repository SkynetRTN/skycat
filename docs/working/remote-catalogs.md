---
status: open
reviewed: 2026-08-07
branch: docs/catalog-coverage-split
authority: code-inspection (skycat @ 570883e, skynet @ 0040c28ab, afterglow-core @ 92aaf61) + upstream docs
implementation: not-started
---

# Remote catalogs — an adjacent `RemoteCatalogReader`

Skycat exists because astroquery could not go local. The 2026-07-04 investigation
that seeded this package established that `astroquery.vizier.Vizier` only POSTs to
a VizieR-compatible HTTP endpoint and has no local-index mode, and the
recommendation was to build a local backend *behind* the existing provider
abstraction rather than bend astroquery toward local files.

The previous revision of this note asked the reverse question — *should astroquery
come back, inside Skycat?* — and answered it as a four-way feasibility study whose
verdict was mostly negative. That study is still the evidence base and most of it
survives below. What has changed is the question. It is no longer *whether*
remote catalogs belong in Skycat, but *where they attach*, and the answer this
note is now organised around is:

> **Remote catalog access is an adjacent feature, not a mode of the existing one.**
> `CatalogReader` stays strictly local PostgreSQL/PostGIS, unchanged, forever. A
> new `RemoteCatalogReader` owns every VizieR and SIMBAD connection. They share a
> package, a row convention and a config namespace. They share no call path, no
> fallback, no release vocabulary, and no failure mode.

That single structural decision resolves, or reduces to a documented boundary,
almost every objection the feasibility study raised. The objections did not go
away — they became the reasons the two classes are separate. Sections 1 and 5
make that mapping explicit; section 6 is the action plan.

**What is being built, in one paragraph.** An optional extra, `skycat[remote]`,
adds `skycat/remote/` containing `RemoteCatalogReader` with three operations:
`resolve()` (SIMBAD name → coordinates, the one capability a local mirror
structurally cannot provide), `cone()` (VizieR cone search against a declarative
catalog table), and `describe()` (designation metadata, usable as a provenance
lint). Rows come back in Skycat's dict-of-typed-columns convention with reserved
`_source` / `_service` / `_designation` / `_retrieved_at` keys and **no release
attribution**, because there is none to give. The default install stays at five
dependencies and `import skycat` never imports astroquery.

**And it is built second.** Remote access is worth having for the catalogs that
genuinely cannot be mirrored, and which those are is a measurement, not an
assumption. [local-catalogs.md](local-catalogs.md) takes it against a 4 TB
budget and reaches a sharp boundary: everything except the billion-row full
surveys fits, with room to spare, so the remote catalog set is **five
catalogs** — full PanSTARRS DR2, full Gaia DR3, NOMAD, USNO-B1.0 and SDSS —
totalling roughly 3.1 TB, more than the entire disk. That is the honest
justification for building this at all, and the remote phases are gated on it:
**`RemoteCatalogReader` should ship covering the catalogs local storage cannot
justify, not the catalogs nobody has tried to store yet.**

The companion note owns everything about mirroring: sizing, download sources per
catalog, the six touch points, ingestion at 10⁸–10⁹ rows, and the L-phase plan.
This note owns the reader.

---

## 1. The framing: two readers, one package

### 1.1 The split

| | `CatalogReader` | `RemoteCatalogReader` |
|---|---|---|
| Module | `skycat/client.py` (unchanged) | `skycat/remote/` (new) |
| Data source | PostgreSQL/PostGIS, one active release per family | VizieR (CDS) and SIMBAD over HTTP |
| Dependencies | the five runtime libraries | `skycat[remote]` extra: astroquery + its stack |
| Unit of selection | `family` (`apass`, `vsx`, `landolt`, `stetson`) | `catalog` (a VizieR designation, keyed by a short name) |
| Release attribution | every row belongs to a `ResolvedRelease`; `--release` selects one | none. `_designation` says what was queried; there is no release |
| Reproducibility | manifest/content checksum, `source_modified_at`, `importer_version` | none. `_retrieved_at` is a timestamp, not a guarantee |
| Spatial work | `ST_DWithin`/`ST_Distance` on a GiST-indexed `geography` column | the remote service filters; separation is arithmetic on the returned rows |
| Failure modes | connection, statement timeout, missing release | HTTP 4xx/5xx, DNS, rate limit, malformed VOTable, service timeout |
| Exceptions | `CatalogQueryError` | `RemoteCatalogError` — a **sibling**, never a subclass |
| Latency budget | single-digit ms to low hundreds of ms, bounded by a 30s statement timeout | seconds, unbounded by anything Skycat controls |
| Batch crossmatch | one `COPY` + one `LATERAL` join + one round trip | **not offered.** There is no remote analogue that is not a loop |
| Availability | ours | CDS's |

### 1.2 The rejections become invariants

The feasibility study rejected query-time remote fallback (its "Option A") on
three grounds. Under the adjacent-reader framing, each of those grounds stops
being an argument and becomes a property the code must preserve:

| Study's objection to fallback | Now expressed as |
|---|---|
| [Decision 0001](../decisions/0001-postgresql-postgis-only.md) argues a degraded path used during an incident is worse than a clear failure | **Invariant:** no method on `CatalogReader` may reach the network. A caller who wants remote data constructs the other class and knows it |
| VizieR does not carry the APASS release Skycat serves (§5.2) | **Invariant:** a remote row carries no release. Nothing can accidentally attribute VizieR's APASS DR9 to a local DR10 release id |
| The routing layer already exists downstream, where its output shape is native (§2.8) | **Invariant:** Skycat ships no routing. `local_first` / `remote_only` mode selection stays the consumer's; Skycat provides the two ends, not the switch |

This is why the plan does **not** supersede decision 0001 and does not need to.
0001 is about the query path having one implementation. It still does. A second
class with a different name, a different exception type, a different dependency
set and no release vocabulary is not a fallback — it is a different question
being asked deliberately. Phase R0 (§6) writes that argument down as decision
0002 so it is settled rather than re-derived.

### 1.3 Explicitly out of scope

Four things a reader might reasonably expect from "remote catalog support" that
this plan does not build, each for a stated reason:

1. **No fallback of any kind inside `CatalogReader`** — §1.2. Not on
   unavailability, not on empty results, not behind a flag. A flag is the same
   ambiguity with a switch on it.
2. **No unified facade** (`Reader` that dispatches to either). It would
   re-introduce exactly the property §1.2 removes: error handling, latency
   budget, timeout semantics and reproducibility silently changing based on which
   string was passed. Reconsider only if a consumer demonstrates a real need, and
   then as a *consumer-side* class.
3. **No automated source acquisition** (`skycat fetch`). astroquery is a
   cone/region query API, not a bulk mirror; pulling 128M APASS rows through it
   means either `row_limit=-1` on an all-sky query, which CDS will rightly
   refuse, or tiling the sky into millions of cones. The bulk channel is
   `https://cdsarc.cds.unistra.fr/ftp/<designation>/` and
   [provenance.md](../guides/provenance.md) already documents the
   `wget --timestamping` recipe, the `--cut-dirs` depth trap, and why preserving
   upstream mtimes matters for the manifest checksum. APASS comes from AAVSO, not
   CDS, at all. `SKYCAT_DATA_ROOT` stays read-only and Skycat still downloads
   nothing on the ingestion path.
4. **No photometric transformation tables.** The legacy `filter_lookup` maps a
   FITS `FILTER` header value to a reference band (§2.2). Skycat has never seen a
   FITS file. Importing that table would be the package acquiring an opinion
   about instrument metadata, and it is already owned downstream — Skycat's
   `models/landolt.py` deliberately stores V plus five colour indices and lets
   the consumer derive U/B/R/I, precisely so that the local and remote paths
   agree by not deriving anything.

### 1.4 Three operations, and who owns each

| Operation | Local | Remote | Notes |
|---|---|---|---|
| Cone search | ✅ `CatalogReader.cone()` | ✅ `RemoteCatalogReader.cone()` | Same *shape*, different guarantees. §4.3 |
| Lookup by native id | ✅ `lookup()` | ⏸ deferred | Expressible as a VizieR `column_filter`, but the id column differs per catalog. R4 at the earliest |
| Batch crossmatch | ✅ `crossmatch()` | ❌ never | N HTTP requests is a different operation, not a slow version of this one. §4.10 |
| Name resolution | ❌ impossible | ✅ `resolve()` | SIMBAD. The one capability local-first structurally cannot match. §4.5 |
| Designation metadata | ❌ n/a | ✅ `describe()` | Lints a superseded designation (`II/183` → `II/183A`). §4.11 |
| Parity verification | — | dev-only harness | R1. The highest-value, lowest-risk piece, and it ships nothing |

---

## 2. The legacy system, inventoried

This section is the reference material the design argues from. It is longer than
it looks necessary because the inventory itself is the finding: what looked like
one integration is three, in two repositories, at two different layers, and they
disagree with each other.

### 2.0 Three integrations, three lineages, six copies

| # | Integration | Location | Layer | Status |
|---|---|---|---|---|
| **1** | VizieR plugin layer | `skynet/…/optical_data_processing/catalogs/` | pipeline | **Authoritative in production.** The fork the optical pipeline calls |
| 1′ | — vestigial fork | `skynet/packages/py/skynet-db/skynet_db/runners/common/catalog_plugins/` | — | Older fork. Its only query consumer is dead code |
| 1″ | — upstream original | `afterglow-core/afterglow_core/resources/catalog_plugins/` (`master` @ `92aaf61`) | — | Flask-config-driven. Carries two declarative mechanisms the forks dropped (§2.3) |
| 1‴ | — vendored snapshot | `skynet/apps/afterglow/docs/legacy/resources/catalog_plugins/` | — | Byte-identical copy of 1″, kept as reference |
| **2** | SIMBAD target resolver | `skynet/apps/public-api/public_api/services/target_search.py` | public API + SSR site | **Live.** Mounted by both the `/web` site and `/v1` (§2.6) |
| **3** | Direct VizieR APASS endpoint | `skynet/apps/public-api/public_api/routers/catalog_objects.py:54` | public API | **Live.** Bypasses the plugin layer entirely (§2.7) |

Integrations 2 and 3 are absent from every previous analysis of this problem,
including the first revision of this note. They matter disproportionately:

- **Integration 2 is the only SIMBAD dependency in the system**, and SIMBAD is
  the one remote service with no local substitute — it resolves *names*, and
  Skycat's local store has no name index for anything but its own `native_id`s.
  If `RemoteCatalogReader` ships one operation, it should be this one.
- **Integration 3 proves the plugin layer is bypassable and gets bypassed.**
  Someone needed an APASS cone over HTTP, found the plugin layer too entangled
  with the pipeline to reuse, and wrote 60 lines of astroquery directly into a
  FastAPI route. That is exactly the demand a supported `RemoteCatalogReader`
  absorbs, and §2.7 catalogues what went wrong when it was written by hand.

The Afterglow → skynet fork replaced Flask's `current_app.config` with module
constants and marshmallow's `CatalogSource` with a pydantic one. Both
substitutions changed behaviour; §2.3 and §2.4 are the consequences.

### 2.1 Integration 1 — the plugin contract

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

`table_to_sources` runs the inverse at row scale: for each `col_mapping` entry it
tries `row[expr]` first (fast path, literal column) and falls back to
`eval(expr, {**numpy.__dict__, **row_columns}, {})`. Magnitudes are read from the
`mags` pairs, with a `"'"` → `"_"` retry because VizieR renames `g'mag` to
`g_mag`, and are **kept only when `val and val < 99`** — the sentinel filter that
turns VizieR's 99.99 "no measurement" into an absent band. A source with no
surviving magnitudes is dropped entirely.

**What `RemoteCatalogReader` takes from this:** the declaration/expression split
is the right idea and the `eval` is the wrong implementation (§4.4, §5.7). The
`< 99` sentinel rule is real domain knowledge and must be carried over
explicitly, as a per-column declared sentinel rather than a global magic number.
The drop-sources-with-no-magnitudes rule must **not** be carried over: a Skycat
cone returns positional rows regardless, and silently returning fewer rows for
invisible reasons is the failure mode §5.4 is about.

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
catalog's custom map, with UCAC's wildcard as the clearest case. **A remote
catalog table in Skycat gets one key per catalog and no second name** — the
mismatch is a bug that a single-key schema cannot express.

`num_sources` are hardcoded and stale. The VSX entry says 2,115,593; Skycat's
`approx_row_count` for the same family is 10,300,000. Nobody is wrong — VSX is a
living index and the two numbers are snapshots years apart — which is exactly the
point developed in §5.2.

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
divides by 15, and Skycat is degrees throughout; the boundary conversion is a
named function in the mapping layer, not a `/15` sprinkled through the table.
**Two providers parse sexagesimal in an `eval`'d string expression**, including a
sign fix-up (`(1 - 2*(DEJ2000.strip().startswith("-")))`) that exists because
`-00 30 00` loses its sign through `int()`. Skycat's `landolt.py` and `stetson.py`
parsers reimplement precisely this arithmetic in normal Python, deliberately, so
the two backends agree — which is the claim R1's parity harness exists to
turn into a test. **Tycho-2's `id` is a `str.format` call** evaluated per row; the
`co_names` harvester picks up `TYC1`, `TYC2`, `TYC3` as columns and `format`
resolves off the string literal.

#### Filter lookups (photometric transformations)

This is the part with the most science in it and the least test coverage, and per
§1.3 it is the part Skycat does not take.

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
keys: `Open`/`Clear`/`Lum` → `V`; `gp`/`rp`/`ip` naming aliases; astrophotography
filters (`Red`, `Green`, `Blue`, `Halpha`, `OIII`, `SII`, `Hbeta`); and sixteen
curriculum-education combinations (`R+Red`, `R,Red`, `Red+R`, `Red,R`,
`Green+V`, …). PanSTARRS gets eight naming aliases; Landolt, Stetson and Tycho
get the `Open`/`Clear`/`Lum` → `V` trio.

Both tables stay downstream. What `RemoteCatalogReader` returns is the catalog's
*native* bands under Skycat column names — `bt_mag`, `vt_mag` for Tycho-2 — and
the consumer transforms, exactly as it does for local rows today.

### 2.3 Declarative catalog definition — the mechanism the forks dropped

This is the most directly relevant precedent in the whole codebase, and it
survives only in upstream Afterglow (`afterglow_core/default_cfg.py`). Two config
variables let an operator extend the catalog set **without writing code**.

**`CATALOG_OPTIONS`** — per-catalog overrides merged over an existing plugin's
class attributes in `Catalog.__init__`:

```python
CATALOG_OPTIONS = {
  'APASS': {'vizier_server': 'vizier.u-strasbg.fr',
            'filter_lookup': {'Open': '0.8*V + 0.2*R'}},
}
```

**`CUSTOM_VIZIER_CATALOGS`** — whole new catalogs as plain dicts. At import time
`vizier_catalogs.py` does `type(classname, (VizierCatalog,), kw)` for each entry,
sanitising the name into a Python identifier and appending it to `__all__`. The
shipped (commented-out) example is NOMAD-1:

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
code**, and it is the direct precedent for §4.4's declarative table.

It is also a demonstration of what goes wrong when a declarative table has no
schema and no tests. **The shipped example does not work.** `mags` values are
consumed by `table_to_sources` as `mag_col, mag_err_col = item[:2]`, which expects
a sequence of one or two column names. Given the example's plain string `'Bmag'`,
`item[:2]` is `'Bm'`, so `mag_col = 'B'` and `mag_err_col = 'm'`; `row['B']`
raises `KeyError`, the retry raises `KeyError`, and the enclosing
`except Exception: pass` drops the magnitude. With every band dropped, the
`if source.mags:` guard drops every *source* — a NOMAD catalog configured exactly
as documented returns zero rows, silently. The real plugins all use lists
(`['Bmag', 'e_Bmag']`, or `['Bmag']` which takes the `ValueError` → `item[0], None`
path), and VSX's empty-string values survive only because `''[0]` raises
`IndexError` into the `continue`.

Both mechanisms were dropped in the skynet forks: `CUSTOM_VIZIER_CATALOGS` and
the `current_app.config` lookups are gone, replaced by the four-line `config.py`.
The skynet registry (§2.2) re-implements the *overlay* half of `CATALOG_OPTIONS`
by passing `filter_lookup=` to each constructor, but there is no longer any way
to add a catalog without adding a module.

**The lesson the design takes:** declarative yes, untyped no. A frozen dataclass
with `tuple[str, str | None]` bands would have made the broken NOMAD example a
startup error instead of a catalog that silently returns nothing. §4.4.

### 2.4 Machinery around the query — five warnings

Five pieces of the legacy integration are worth reading as warnings rather than
as designs to port.

**astroquery's cache had to be monkey-patched.** `vizier_catalogs.py` replaces
`astroquery.query.to_cache` and `astroquery.query.AstroQuery` at import time so
that a caching failure under concurrent access is swallowed rather than raised,
and so that files older than `VIZIER_CACHE_AGE_DAYS` (30) are pruned on write.
The replacements are four bare `except Exception: pass` blocks reaching into
another package's internals. Under Skycat's ruff configuration this would not
merge: `BLE001` and `S110` are both selected and are errors, not style nits.

**Cache hit rates required rounding the query.** `query_box`/`query_circ`
quantise the field centre to 10 arcsec, the radius to 0.2 arcmin, and clamp
declination, "to avoid cache misses for querying the same field with tiny
differences in RA/Dec and size". A Skycat cone search returns rows within
`radius_deg` of exactly `(ra, dec)`; a remote-backed one with caching on returns
rows for a nearby cone of a slightly different size. That is a silent,
position-dependent difference in the returned set, not a rounding of a displayed
number. §4.9 forbids it.

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
declared field raised too. Neither surfaced as an error. The comment left behind
says it plainly: the validation error was one "which `_filter_variable_stars`
swallows, silently disabling variable-star filtering". A whole safety feature was
off, with no failure visible anywhere. Skycat's row shape is a third schema again
(§5.4); the same class of silent failure is the default outcome, not the unlucky
one, and §7 is the answer.

**Constraints go over the wire as strings.** `query_region` splits its
`constraints` dict into `column_filters` (entries with a value) and `keywords`
(entries with `None`). SkyMapper's subclass uses this to force `flags = '0'`,
dropping sources with non-zero SExtractor flags. The VizieR filter syntax is its
own small language (`>10`, `<=5`, `!=0`, ranges) with no relationship to Skycat's
validated `QualityFilter` — and §2.7 shows what happens when a caller forgets
that.

### 2.5 How catalog selection works today

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
foot-gun. This whole layer stays downstream (§1.3.4); it is inventoried here so
that nobody mistakes it for something `RemoteCatalogReader` is meant to replace.

### 2.6 Integration 2 — the SIMBAD target resolver

`apps/public-api/public_api/services/target_search.py` (394 lines) resolves a
free-text identifier to a submittable `TargetSearchResult` across five sources:
SIMBAD, NORAD satellites, major solar-system bodies, MPC comets, and MPC orbits.
The last four are local database queries. **SIMBAD is the only remote one**, and
it is the only SIMBAD dependency in the system. It is mounted by both the `/web`
SSR site and `GET /v1/catalog-objects/search`.

```python
customSimbad = Simbad()
SIMBAD_ENABLED = True
try:
    customSimbad.add_votable_fields("otype")
except Exception:
    SIMBAD_ENABLED = False
```

The query is `customSimbad.query_object(name, wildcard=False)`, and each row
yields `main_id`, `otype`, `ra`, `dec`, wrapped into a `FixedPosition` with
`EquatorialCoordinates(epoch=datetime(2000, 1, 1, 12, 0, 0))`. A 200-entry
`SIMBAD_OBJECT_TYPES` dict maps SIMBAD's machine `otype` to a human label.

Seven observations, each of which is a requirement on §4.5:

1. **The module-level singleton is constructed at import time, and a failure
   there disables SIMBAD permanently and silently.** `add_votable_fields("otype")`
   is a network-touching call in current astroquery (the field list is validated
   against the service). If it fails once — CDS slow during a deploy, DNS not yet
   up in a fresh pod — `SIMBAD_ENABLED` is `False` for the life of the process and
   every subsequent search silently returns only the local sources. Nothing logs
   it, nothing retries, nothing surfaces it in a health check.
2. **The lowercase column names pin an astroquery major behaviour change.**
   `ra`, `dec`, `main_id`, `otype` are the post-0.4.8 names; 0.4.7 and earlier
   returned `RA`, `DEC`, `MAIN_ID` — and `RA`/`DEC` were *sexagesimal strings*,
   not float degrees. astroquery 0.4.8 rewrote the SIMBAD module onto SIMBAD's
   **TAP interface**, lowercased every column, removed the `typed_id` votable
   field, deprecated `query_criteria`, and added a `user_specified_id` column.
   `skynet` pins `astroquery==0.4.10`, so this code is correct today and would
   break on a downgrade with no type error to catch it.
3. **`Angle(ra, unit=u.degree)` on a value that is already float degrees** is a
   no-op wrap that exists because the pre-0.4.8 value was a sexagesimal string.
   It is vestigial, and it is the only thing that would keep the code from
   crashing loudly if the columns ever revert.
4. **The name is lowercased before being sent**: `name = (name or "").strip().lower()`.
   Harmless for SIMBAD, which is case-insensitive, but the same lowercased string
   is then used for the four `ilike` local queries, so the two behaviours are
   coupled through one variable.
5. **`limit` does not bound the SIMBAD results.** The docstring says so
   explicitly — "`limit` bounds the per-catalog result count (SIMBAD returns its
   own match set)" — so a caller passing `limit=25` can still get an unbounded
   list back. Every local branch applies `.limit(limit)`; the remote one does not.
6. **The epoch is hardcoded J2000** and no proper motion is applied. SIMBAD
   returns ICRS coordinates at the catalog epoch; for a high-proper-motion star
   the difference is real and unattributed.
7. **Errors are `print()`, not logging**, inside `except Exception` — five of
   them, one per source. A SIMBAD outage degrades to "no fixed-position results"
   with a line on stdout.

**Why this is the first thing `RemoteCatalogReader` should own.** Name resolution
is not a catalog query and has no local analogue — Skycat's `lookup()` takes a
`native_id` for one family, which is a fundamentally different operation from
"what is `M31`". It is a small, well-defined surface: one string in, a list of
`(name, type, ra_deg, dec_deg)` out. It has no release semantics to violate, no
schema impedance, no `mags` mapping, and no photometric transformation. And the
existing implementation has seven identified defects that a supported
implementation with a real failure taxonomy would fix by construction.

### 2.7 Integration 3 — the direct VizieR APASS endpoint

`GET /v1/catalog-objects/vizier/apass`
(`apps/public-api/public_api/routers/catalog_objects.py:54`) is a 60-line
astroquery cone search written directly into a FastAPI route, bypassing the
plugin layer, the `CatalogSource` shape, and the local-first router. It is the
clearest statement of unmet demand in the system, and every one of its defects is
a requirement on the design.

```python
Vizier.ROW_LIMIT = 1000
center_coord = coord.SkyCoord(ra=ra_deg, dec=dec_deg, unit=(u.deg, u.deg))
column_filters = {}
if query.b: column_filters["Bmag"] = query.b
...
tables = Vizier.query_region(center_coord, catalog="APASS9",
                             radius=radius_deg * u.deg, column_filters=column_filters)
```

| # | Defect | Consequence | How §4 avoids it |
|---|---|---|---|
| 1 | `Vizier.ROW_LIMIT = 1000` mutates the **module-global** astroquery class attribute on every request | Process-wide, shared across concurrent requests and with anything else in the worker that imports `Vizier`. A row limit set here leaks into an unrelated caller | The reader owns a per-instance `Vizier` object; nothing global is mutated |
| 2 | `column_filters["Bmag"] = query.b` passes a **float** into VizieR's filter mini-language | A bare numeric value is an *equality* constraint in that language, not an upper limit. `?b=12.5` asks for `Bmag = 12.5` exactly and returns essentially nothing. The magnitude constraints on this endpoint do not do what their names suggest | Typed `mag_min`/`mag_max` floats rendered into the filter string by the mapping layer, never passed through |
| 3 | `b`,`v`,`g`,`r` are typed `float \| None` but `i` is typed `str \| None` (`schemas/catalog_objects.py`) | The one field typed as a string is the only one where a caller can write `<13` and get the intended behaviour. The inconsistency is almost certainly how the equality problem was worked around in practice | One typed constraint shape for all bands |
| 4 | User-supplied values reach a remote query language unvalidated | Not a Skycat-side injection (VizieR filters cannot escape into SQL), but it is an unvalidated pass-through to a third-party parser, and the opposite of `QualityFilter`'s allow-listed, parameter-bound design | Allow-listed columns, six operators, values rendered by Skycat |
| 5 | `ra_j2000=row["RAJ2000"] / 15` | The field is **named degrees and holds hours**, in a public API response schema. The `/15` is the legacy `ra_hours` convention leaking through a field called `ra_j2000` | Degrees everywhere; the conversion is one named function at the boundary (§4.3) |
| 6 | `catalog="APASS9"` | VizieR's DR9 — a release Skycat does not have and cannot reconcile with DR6 or DR10, served through an endpoint whose name says only "apass" | `_designation` on every row; the catalog def carries `mirrors_release` explicitly (§4.4) |
| 7 | No timeout, no retry, no error mapping | A CDS stall becomes a hung request in the public API's worker pool | Explicit `service_timeout_s`, a bounded retry policy, and `RemoteCatalogError` (§4.7) |
| 8 | `ra_deg`, `dec_deg`, `radius_deg` are all `float \| None` with no default | A call with no parameters reaches `SkyCoord(ra=None, dec=None)` | Required arguments, validated by `validate_radec` before anything is sent |

Defect 2 is the one to weigh: this endpoint has shipped magnitude filtering that
almost certainly returns empty results, and nothing would have surfaced it,
because an empty cone is a legitimate answer. It is the same failure signature as
§2.3's NOMAD example and §2.4's VSX validation error — **the remote path fails by
returning nothing, not by raising** — and it is the single strongest argument for
R1 (a parity harness that knows what the answer should be) preceding R3
(shipping the query).

### 2.8 The local-first router — downstream, and why routing stays there

The routing this note might otherwise propose has already been built, in
`skynet-db`, as a `LocalFirstCatalog` mixin placed in front of the VizieR
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

This is the right place for it: it is where `CatalogSource`/`mags` — the shape
routing normalises into — actually lives. Moving it into Skycat would invert the
dependency, forcing Skycat to learn the consumer's vocabulary or invent a third
shape neither side wants. §1.3.1 and §1.3.2 are that conclusion as a rule.

What Skycat's two readers give this layer is *better ends to route between*: a
`RemoteCatalogReader` it can call instead of a `VizierCatalog` subclass, with a
declared failure taxonomy and no global state.

**Caveat on currency:** that code is not in the current `skynet` working tree.
`catalogs/local/` contains only a stale `__pycache__` (`backend`, `config`,
`engine`, `errors`, `health`, `normalize`, `routing`) — the sources live on
`feat/skycat` / `add-landholt-and-stetson`, unmerged into
`pipeline/analysis-descriptive-split`. Confirm the branch state (open item 1)
before treating it as shipped.

### 2.9 What the inventory establishes

1. **A VizieR catalog is data, not code** (§2.3). Twelve lines of configuration
   added a 1.1-billion-row catalog. Ten classes differing only in class
   attributes is a worse encoding of the same table.
2. **Untyped declarative tables fail silently** (§2.3, §2.4, §2.7 defect 2). Three
   independent instances of the same signature: the remote path returns nothing
   and raises nothing.
3. **The plugin layer gets bypassed when it is hard to reuse** (§2.7). A
   supported, importable reader is the thing that stops the next hand-rolled
   route.
4. **SIMBAD is unserved and unservable locally** (§2.6), and its current
   implementation can disable itself permanently at import.
5. **Config that documents the opposite of what runs is worse than no config**
   (§2.4). Any setting `RemoteCatalogReader` exposes must be read by exactly one
   place and covered by a test that would fail if it stopped being read.

---

## 3. What Skycat is today, and which contracts the remote reader must not touch

The invariants the design is bounded by. These are properties of the *existing*
package that must read identically after the remote work lands.

**The row shape is typed columns, not bands.** `cone_search` returns `dict(row)`
over the release's actual table columns — `native_id, ra_deg, dec_deg,
johnson_v_mag, johnson_v_err_mag, sloan_g_mag, …, extra, separation_deg`. There is
no `mags` dict, no `ra_hours`, no `CatalogSource`. Column names carry units by
convention. §4.3 keeps this and adds only underscore-prefixed metadata keys,
which [api-stability.md](../reference/api-stability.md) already permits ("new
keys may be added — index by key, never by position").

**Spatial work happens in the database, always.** Decision 0001 is accepted and
implemented: no SQLite, no Python-side spatial fallback. `separation_deg` comes
from `ST_Distance(geom, point, false)`. §4.3 addresses the one place the remote
path computes a distance in Python and why that is not a 0001 exception.

**Every answer is attributed to a release.** `resolve_release_for_query` returns a
`ResolvedRelease` (family, data table, release id, name, state) before a query
runs, and `--release` exists so results are reproducible. **A remote row has no
release, and the design's answer is to not have one** rather than to synthesise
one (§4.3).

**The dependency set is five libraries.** `sqlalchemy`, `geoalchemy2`,
`psycopg[binary]`, `alembic`, `click`. No numpy. No astropy. That is not an
accident for a package whose deployment target is a one-shot Kubernetes Job, and
§5.1 is how it stays true.

**There is deliberately no plugin framework.** Adding a *local* family is six
explicit touch points; `catalog_defs.py` is the single source of truth. §4.4
argues why a remote catalog table is not a violation of that: the six touch
points are a parser, a typed model, a migration, a validation module, a registry
entry and docs — and a remote catalog has none of the first four, because it has
no local schema to design. That argument belongs in decision 0002 (R0), not
in an implementation PR.

**Exit codes are a contract.** `CatalogConfigError` → 2;
`CatalogQueryError`/`IngestionError`/`ReleaseStateError` → 1, read by the K8s
ingest Job. A new network failure class must land in that taxonomy deliberately
(§4.7).

**`SKYCAT_DATA_ROOT` is read-only.** "Skycat never downloads anything. Mirroring
is a deliberate, separate step, which is what makes a release reproducible."
Unchanged: §1.3.3.

**Quality filters are allow-listed and parameter-bound.** `QualityFilter`
validates the column against the table's real columns, the operator against six
comparisons, and binds the value — "callers may therefore build these from
untrusted input" (`skycat/query/cone.py:46`). The remote equivalent must earn the
same sentence or not claim it (§4.6).

---

## 4. The design

### 4.1 Module layout

```
skycat/
  client.py            # CatalogReader — UNCHANGED, no import from remote/
  remote/
    __init__.py        # RemoteCatalogReader, RemoteCatalogError, RemoteCatalogDef
    reader.py          # the class: cone(), resolve(), describe(), close()
    defs.py            # the declarative catalog table (frozen dataclasses)
    mapping.py         # VizieR row → Skycat dict; sexagesimal; hours→degrees; sentinels
    vizier.py          # transport: the astroquery/TAP client, timeouts, retries
    simbad.py          # transport: name resolution
    errors.py          # RemoteCatalogError and its subclasses
```

Two hard rules, both enforceable by a test:

- **`skycat/client.py` never imports `skycat.remote`.** A grep test asserts it.
- **`import skycat` never imports astroquery.** The astroquery import lives
  inside `skycat/remote/vizier.py` and `simbad.py` at *call* time or behind a
  module-level guarded import that `skycat/__init__.py` does not touch. A test
  asserts `"astroquery" not in sys.modules` after `import skycat`.

`RemoteCatalogReader` is exported from `skycat.remote`, **not** from `skycat`'s
top level in phase 1–3. Promoting it to `skycat.__all__` is a stability
commitment and should follow, not precede, a consumer using it.

### 4.2 The surface

```python
class RemoteCatalogReader:
    def __init__(
        self,
        *,
        service_timeout_s: float = 30.0,
        row_limit: int = 5000,
        vizier_server: str | None = None,
        user_agent: str | None = None,
        cache_dir: str | None = None,
    ) -> None: ...

    @classmethod
    def from_env(cls, **kwargs) -> "RemoteCatalogReader": ...

    def catalogs(self) -> list[RemoteCatalogDef]: ...
    def describe(self, catalog: str) -> dict: ...

    def cone(
        self,
        catalog: str,
        ra_deg: float,
        dec_deg: float,
        *,
        radius_deg: float | None = None,
        radius_arcmin: float | None = None,
        radius_arcsec: float | None = None,
        limit: int = 100,
        mag_band: str | None = None,
        mag_min: float | None = None,
        mag_max: float | None = None,
        order_by: str | None = None,
    ) -> list[dict]: ...

    def resolve(self, name: str, *, limit: int = 25) -> list[dict]: ...

    def close(self) -> None: ...
    def __enter__(self) -> "RemoteCatalogReader": ...
    def __exit__(self, *_exc) -> None: ...
```

Deliberate parallels: `radius_deg`/`radius_arcmin`/`radius_arcsec` reuse
`radius_to_deg` verbatim (`skycat/query/cone.py:112`), so "exactly one of" means
the same thing on both readers. `limit`, `mag_band`, `mag_min`, `mag_max`,
`order_by` keep their local meanings.

Deliberate non-parallels, each a signal rather than an omission:

- **First argument is `catalog`, not `family`.** Different vocabulary for a
  different namespace. `RemoteCatalogReader.cone("apass", …)` is a *different
  catalog* from `CatalogReader.cone("apass", …)` — VizieR's DR9 versus the local
  active release — and the parameter name is the first place to say so.
- **No `release=`.** There is nothing to select.
- **No `crossmatch()`.** §4.10.
- **No `quality_filter=`** in phase 3. §4.6.
- **`resolve()` exists here and nowhere else.** §4.5.

### 4.3 The row shape

Rows are `dict`, same as local. Columns use Skycat naming — units in the name,
degrees not hours, `NULL` (`None`) for missing, never a sentinel. Metadata rides
in reserved underscore-prefixed keys, which cannot collide with a catalog column
because no VizieR column name maps to one:

| Key | Type | Meaning |
|---|---|---|
| `_source` | `"remote"` | Distinguishes a row at the row level, not just in the docs. A consumer persisting a calibration can tell from the data whether it is reproducible |
| `_service` | `"vizier"` \| `"simbad"` | Which service answered |
| `_designation` | `str` | The exact VizieR designation queried, e.g. `"I/259/tyc2"` |
| `_retrieved_at` | ISO-8601 UTC `str` | When. Not a provenance guarantee — a timestamp |
| `extra` | `dict` | Untranslated VizieR columns, mirroring the local `extra` JSONB convention. Where a local row carries per-band observation counts, a remote row carries whatever the service returned that the mapping did not name |

There is **no** `release`, `release_id` or `release_name` key. Absence is the
honest encoding; a synthetic `"vizier:II/336"` would be honest about origin but
would then be a release the registry does not know, that `--release` cannot
select, and that a naive consumer would happily store in a column expecting a
foreign key.

**`separation_deg` is computed in Python, and that is not a decision-0001
exception.** 0001 bans a Python-side *spatial query path* — filtering and
indexing decisions made outside the database. Here the remote service did the
filtering; Skycat is annotating rows it was given with a great-circle distance.
The risk is not correctness-by-architecture, it is *two implementations of one
number*, which is a real risk and gets a real answer: one documented haversine in
`skycat/remote/mapping.py`, and an R1 test that asserts it agrees with
`ST_Distance(…, false)` to sub-milliarcsecond over a grid of positions including
the poles and the RA 0/360 seam. The same `POSTGIS_SPHERE_RADIUS_M` constant
feeds both.

**Sentinels are declared, not global.** The legacy `val and val < 99` rule is
carried over as a per-column `null_above: float | None` in the catalog def, so
`99.99` in a magnitude column becomes `None` and a genuine `0.0` magnitude
survives — the legacy `val and …` truthiness test drops it. Rows are **never**
dropped for having no magnitudes; a positional row is a legitimate result (§2.1).

### 4.4 The declarative catalog table

Per §2.3 and §2.9.1: data, not classes. Per §2.3's NOMAD bug: typed, not dicts.

```python
@dataclass(frozen=True)
class RemoteColumn:
    """One output column, its VizieR source, and how to read it."""
    name: str                       # Skycat name, units included: "vt_mag"
    vizier: str                     # VizieR column: "VTmag"
    kind: Literal["float", "int", "str"] = "float"
    null_above: float | None = None  # sentinel → None (the legacy `< 99` rule)

@dataclass(frozen=True)
class RemoteCatalogDef:
    key: str                        # "tycho2" — the ONLY name. §2.2's mismatch bug
    designation: str                # "I/259/tyc2"
    display_name: str
    native_id: RemoteColumn
    ra: RemoteColumn                # declared in degrees; hours→deg at the boundary
    dec: RemoteColumn
    columns: tuple[RemoteColumn, ...]
    default_row_limit: int = 5000
    default_order_by: str | None = None
    approx_row_count: int | None = None
    reference_url: str = ""
    # Honesty fields — the answer to §5.2, in the data
    mirrors_family: str | None = None      # "apass" if a local family covers this sky
    mirrors_release: str | None = None     # "DR9" — what VizieR actually serves
    comparable: Literal["yes", "no", "approximate"] = "no"
    comparable_note: str = ""
```

Four properties that follow from the shape:

1. **One key per catalog.** §2.2's `Tycho`/`Tycho2` registry-key/`name` mismatch
   is unrepresentable.
2. **No expressions, no `eval`.** Where the legacy table used a Python expression
   (`RAJ2000/15`, the Tycho-2 `str.format` id, the sexagesimal splits), the
   mapping layer has a named, tested function selected by `kind` and by explicit
   composite-id support. Nothing from the def is ever `compile()`d or `eval`'d.
   §5.7.
3. **`mirrors_*` and `comparable` put §5.2 in the data.** `apass` gets
   `mirrors_family="apass", mirrors_release="DR9", comparable="no",
   comparable_note="VizieR serves DR9; Skycat serves DR6 and DR10. No overlap."`
   The parity harness reads the same table (§7), so an untestable case is
   documented rather than quietly omitted.
4. **pyright can see all of it**, which `type(classname, (VizierCatalog,), kw)`
   made impossible.

Whether the table lives in code (`skycat/remote/defs.py`, mirroring
`catalog_defs.py`) or is loadable from operator config is a real question that
R3 should answer with "code first". `CUSTOM_VIZIER_CATALOGS` is the
attractive precedent and also the cautionary one: config-defined catalogs exist in
no repository, cannot be reviewed, and — in that exact case — silently returned
nothing. If external definitions are ever added, they load through the same
frozen dataclasses with a validation error at construction, never a duck-typed
dict.

### 4.5 SIMBAD is a resolver, not a catalog

`resolve(name)` returns a list of dicts, not catalog rows:

```python
{"name": "M  31", "object_type": "Galaxy", "otype": "G",
 "ra_deg": 10.6847, "dec_deg": 41.2687,
 "_source": "remote", "_service": "simbad", "_retrieved_at": "..."}
```

Requirements, each answering a numbered defect in §2.6:

| §2.6 | Requirement |
|---|---|
| 1 | **No import-time network call and no permanent disable.** The SIMBAD client is constructed lazily on first `resolve()`, and a construction failure raises `RemoteServiceError` on *that call* and is retried on the next. A one-off CDS blip must not silently remove a capability for the process lifetime |
| 2, 3 | **Pin `astroquery >= 0.4.8`** in the extra and assert the post-TAP column names in a fixture test, so a resolution that returns sexagesimal strings fails loudly. Drop the vestigial `Angle(…, unit=u.degree)` wrap — the value is float degrees; validate it with `validate_radec` instead |
| 4 | **Do not transform the query string.** Pass what the caller gave |
| 5 | **`limit` bounds the result list**, unconditionally, on every path |
| 6 | **Return ICRS degrees and say so.** Do not stamp a J2000 epoch onto a row Skycat did not compute. If a consumer needs proper-motion propagation, that is the consumer's, and `extra` carries `pmra`/`pmdec` when the service returns them |
| 7 | **Structured logging under `skycat.remote`**, matching the `skycat.ingestion` event convention in [runbook.md](../operations/runbook.md); no `print` |

The `otype` → human-label mapping (200 entries) is **consumer presentation**, and
by the §1.3.4 rule it does not move into Skycat. `resolve()` returns the raw
`otype` and, where astroquery supplies it, the service's own long-form label. The
public API keeps its dict.

### 4.6 Constraints, ordering and limits

The three semantic gaps that make a remote cone not a local cone, and the
position taken on each:

| Skycat local | Remote | Position |
|---|---|---|
| `order_by="johnson_v_mag"` → ascending, NULLs last, numeric-only, validated against the table (`cone.py:78`) | VizieR `sort=['+Bmag']`, server-side, no null policy, no tiebreak | Accept `order_by`, validate it against the def's declared columns, translate to the VizieR sort key. **Document that NULL ordering is service-defined**; do not claim parity |
| `limit` applied after ordering, in SQL | `row_limit` applied by the server with unspecified interaction with `sort` | Request `row_limit = limit` and additionally truncate client-side after the mapping. A capped remote cone and a capped local cone still select different stars in a dense field — that is inherent, and it is stated in the docstring rather than papered over |
| `QualityFilter`: allow-listed column, six operators, bound value, safe for untrusted input | `column_filters`, a string mini-language (`>10`, `<=5`, `!=0`, ranges) | **R3 ships `mag_min`/`mag_max` only** — rendered by the mapping layer into `">=12.0 & <=18.0"`, never passed through (§2.7 defect 2). A general remote `QualityFilter` is R4+, and it must translate through the same allow-list, with an explicit `RemoteConstraintError` for anything untranslatable. Never silently drop a constraint: a filter that did not apply returns wrong rows, and that is the §2.7-defect-2 failure signature again |

### 4.7 Failure taxonomy

```
RemoteCatalogError(RuntimeError)          # base — NOT a CatalogQueryError
├── RemoteServiceError                     # HTTP 5xx, DNS, connection reset
├── RemoteTimeoutError                     # service_timeout_s exceeded
├── RemoteRateLimitError                   # 429 / CDS throttle
├── RemoteResponseError                    # malformed VOTable, unexpected columns
├── RemoteConstraintError                  # a constraint that cannot be translated
└── RemoteCatalogNotFound                  # unknown key, or a designation that 404s
```

**Not a subclass of `CatalogQueryError`**, on purpose. `except CatalogQueryError`
in existing consumer code means "the database said no" and must not start
catching "CDS is down". Adding `RemoteCatalogError` to `skycat`'s exception
exports is an api-stability addition (a new name, not a repurposed one) and gets
a release note.

CLI mapping in `_FriendlyGroup`: `RemoteCatalogError` → **exit 1** (operational
failure), joining `CatalogQueryError`/`IngestionError`/`ReleaseStateError`. A
missing `skycat[remote]` extra is a **configuration** failure → `CatalogConfigError`
→ exit 2, with a message naming the extra. Both rows go into
[api-stability.md](../reference/api-stability.md) in the same commit as the code,
because `tests/test_docs.py` will not let them diverge.

Retries: bounded, on `RemoteServiceError`/`RemoteRateLimitError`/`RemoteTimeoutError`
only, with jittered backoff and a hard ceiling inside `service_timeout_s`, so the
timeout the caller set is the timeout the caller gets. Never retry a 4xx.

### 4.8 Transport — prefer TAP

The legacy plugins use astroquery's `viz-bin/votable` POST path. Two reasons to
target TAP instead where it is available:

- **SIMBAD is already there.** astroquery 0.4.8 rewrote the SIMBAD module onto
  SIMBAD's TAP interface; `Simbad.query_tap()` takes ADQL. The current
  `query_object` path is TAP underneath.
- **VizieR has a TAP endpoint** (`TAPVizieR`, reachable through `pyvo`, which is
  already an astroquery dependency). Whether its batch semantics are good enough
  to change the §4.10 crossmatch verdict is open item 5 and should be measured in
  R3, not assumed.

Either way the transport is isolated behind `skycat/remote/vizier.py` so the
choice is revisitable without touching the mapping layer or the reader surface.
Design for one server, configured once, read in exactly one place (§2.9.5), with
`vizier_server` defaulting to an explicit constant rather than to whatever
astroquery happens to default to — the ambiguity §2.4 documents.

### 4.9 Caching

Default **off**, matching what the skynet fork actually does (as opposed to what
its config says).

If it is ever turned on, two rules, both of which the legacy implementation
broke:

1. **Never quantise the query to improve hit rates.** §2.4's 10-arcsec centre and
   0.2-arcmin radius rounding changes the returned set as a function of position.
   A cache that returns a different answer than the uncached path is not a cache.
2. **Cache location is explicit and container-safe.** astroquery caches to
   `astropy` config dirs, which are user-scoped, not container-scoped, and which
   the legacy code had to prune by hand. `cache_dir` is a constructor argument and
   a `SKYCAT_REMOTE_CACHE_DIR` variable; on a read-only container filesystem, no
   configured dir means no cache, not a crash.

No monkey-patching of `astroquery.query` internals under any circumstances
(§2.4, §5.7).

### 4.10 What has no remote analogue

**`batch_crossmatch` is not offered, and the reason is not performance.** Locally
it is a `COPY` of the inputs into a TEMP table, one `LATERAL` join, one round
trip. Remotely it is N HTTP requests: for a 5,000-source frame that is a
different operation with different failure modes, not a slow version of the same
one. Offering it under the same name would be the §1.2 problem in miniature.

If a consumer genuinely needs remote crossmatch, the honest options are CDS's own
X-Match service or a TAP upload join — both separate designs with separate
semantics, and both out of scope here.

**Provenance has no remote analogue either** (§5.5). `_retrieved_at` is a
timestamp, not a checksum. This is stated in the row shape rather than
approximated.

### 4.11 Designation lint

`describe(catalog)` returns the def plus live service metadata
(`Vizier.find_catalogs()` / `get_catalog_metadata()`), which makes it possible to
check that a `reference_url` designation still resolves and has not been
superseded — `II/183` → `II/183A` is the hazard
[provenance.md](../guides/provenance.md) names specifically. As a scheduled CI
job rather than a runtime path, this is the cheapest real value in the whole
plan and it belongs in R5.

### 4.12 Configuration

All new variables are prefixed `SKYCAT_REMOTE_` and documented in the README's
configuration table in the same commit (§5.9 explains why the prefix matters):

| Variable | Default | Read by |
|---|---|---|
| `SKYCAT_REMOTE_VIZIER_SERVER` | an explicit constant, not astroquery's default | `remote/vizier.py`, once |
| `SKYCAT_REMOTE_TIMEOUT_S` | `30.0` | `remote/reader.py`, once |
| `SKYCAT_REMOTE_ROW_LIMIT` | `5000` | `remote/reader.py`, once |
| `SKYCAT_REMOTE_CACHE_DIR` | unset → no cache | `remote/vizier.py`, once |
| `SKYCAT_REMOTE_USER_AGENT` | `skycat/<version>` | `remote/vizier.py`, once |

Each has a test that fails if the variable stops being read — the direct answer
to §2.4's dead-config problem.

---

## 5. Risks, and how the split handles each

Ordered by how likely each is to sink the implementation. These are the
feasibility study's difficulties, re-scored against the adjacent-reader design.

### 5.1 Dependency weight — **handled by the extra; verify at CI**

astroquery 0.4.11 (current stable, confirmed on PyPI) declares `numpy>=1.20`,
`astropy>=5.0`, `requests>=2.19`, `beautifulsoup4>=4.8`, `html5lib>=0.999`,
`keyring>=15.0`, `pyvo>=1.5`. astropy transitively adds `pyerfa`, `PyYAML`,
`packaging`; `keyring` on Linux adds `SecretStorage`/`jeepney`. A package that
installs five libraries would install roughly twenty, including a numerical stack
and a **desktop credential-store library inside a headless Kubernetes Job**.

The second-order problem is lockfiles. Skycat was deliberately *not* made a hard
dependency of `skynet-db` because that "would invalidate every service's frozen
`uv.lock`". `skynet` pins `astroquery==0.4.10` in three requirements files and
`skylib` declares `~= 0.4.9`. A hard astroquery dependency in Skycat with a
different constraint reproduces that conflict from the other direction, in a
repository that cannot resolve it unilaterally.

**Handled:** optional extra `skycat[remote]`, declared as `astroquery >= 0.4.8`
(the TAP/lowercase-columns floor, §2.6) with no upper pin that would conflict
with `skynet`'s `0.4.10`. Default install stays at five. The CI wheel-install
smoke test gets a second assertion: the default wheel imports with astroquery
absent, and `import skycat` leaves `sys.modules` without it (§4.1).

### 5.2 Release identity — **handled by having no release**

**VizieR does not carry the APASS release Skycat serves.** Re-verified 2026-08-07:
VizieR hosts APASS DR9 as `II/336/apass9`; DR10 is public but, in AAVSO's own
words, "cannot be automatically queried" and is distributed directly by AAVSO.

| Family | Skycat releases | VizieR designation | What VizieR serves |
|---|---|---|---|
| `apass` | DR6, **DR10** | `II/336` | **DR9** — no overlap with either |
| `landolt` | **1992**, 2009 | `II/183A` | 1992 ✓ (2009 = `J/AJ/137/4186`, unused by the legacy plugin) |
| `stetson` | stetsonglobs | `J/MNRAS/485/3042/table4` | same ✓ |
| `vsx` | current | `B/vsx/vsx` | a mirror of a living index — no version, no snapshot identity |

Under the old fallback framing this was the concrete blocker. Under the adjacent
framing it is a labelling problem, and §4.4's `mirrors_family` / `mirrors_release`
/ `comparable` fields are the label. A caller asking `RemoteCatalogReader` for
`apass` gets DR9 rows stamped `_designation: "II/336/apass9"` and no release id;
nothing can silently attribute them to a local DR10 release. `describe("apass")`
says so in one call, and the parity harness reads the same three fields to
exclude the pair from comparison.

### 5.3 Query semantics — **partly handled, partly documented**

| Skycat | VizieR | Disposition |
|---|---|---|
| `separation_deg` from `ST_Distance(…, false)` | not returned | Computed in Python, one implementation, parity-tested (§4.3) |
| `order_by` ascending, NULLs last, separation tiebreak | server-side `sort`, no null policy | Translated; **null ordering documented as service-defined** (§4.6) |
| `limit` after ordering | `row_limit`, unspecified vs `sort` | Requested and re-truncated; divergence in dense fields documented (§4.6) |
| `QualityFilter` allow-listed and bound | `column_filters` mini-language | `mag_min`/`mag_max` rendered by Skycat in phase 3; general filters phase 4+ with `RemoteConstraintError` (§4.6) |
| `batch_crossmatch` — one round trip | no batch primitive | **Not offered** (§4.10) |
| 30s statement timeout | `Vizier(timeout=…)` + retries + DNS | `service_timeout_s` is a wall-clock ceiling including retries (§4.7) |

### 5.4 Schema impedance — **handled by the typed def, verified by the harness**

The mapping is mechanical but lossy in both directions, and every loss is a place
the legacy code failed silently:

- **Units and names.** `ra_hours` (÷15) versus `ra_deg`; `Vmag`/`e_Vmag` versus
  `johnson_v_mag`/`johnson_v_err_mag`; `g'mag` (VizieR-renamed to `g_mag`) versus
  `sloan_g_mag`. → declared per column in `RemoteColumn`, with the `'`→`_` retry
  as a documented rule rather than an inline `try`.
- **Sentinels versus NULL.** `val and val < 99` also drops a genuine `0.0`. →
  `null_above` per column, and no truthiness test (§4.3).
- **Dropped rows.** `table_to_sources` discards sources with no surviving
  magnitudes; a Skycat cone returns positional rows regardless. → **never drop**
  (§2.1).
- **`extra` JSONB.** Local rows carry per-band observation counts, DR6 `B-V`,
  `mobs`; remote rows carry the untranslated VizieR columns in the same key
  (§4.3), so the key is always populated and always means "what this source
  provided that the mapping did not name".
- **Derived bands.** Landolt local stores V + five colours; remote returns the
  same; U/B/R/I are derived by the consumer in both cases. Stays consistent
  exactly as long as Skycat does not start deriving them to make remote rows look
  complete (§1.3.4).
- **`native_id` types differ.** APASS `recno` (int) versus Skycat `String(64)`;
  VSX `OID` needed an explicit `str()` after a pydantic validation error silently
  disabled variable-star filtering (§2.4). → `RemoteColumn.kind` declares it, the
  mapping coerces, and a fixture test covers each catalog's id column.

### 5.5 Reproducibility — **handled by not claiming it**

Skycat's central claim is that a release is rebuildable and provable: manifest or
content checksum, `source_size_bytes`, `source_modified_at`, `importer_version`,
`internal_schema_version`, and a five-step chain of custody. A remote row has
none of it. `_source: "remote"` at the row level (§4.3) is what lets a consumer
persisting a calibration tell, from the data, that it is not reproducible. The
alternative — documenting it — is what §2.4 shows does not work.

### 5.6 Operational surface — **new, and genuinely new**

This is the risk the split reduces least, because it is inherent to talking to
the network at all.

- **Network egress from a K8s Job.** Currently unnecessary; NetworkPolicy, proxies
  and DNS become Skycat's problem *for deployments that install the extra*. The
  ingest Job does not install it, so the ingestion path is unaffected — worth
  stating in [runbook.md](../operations/runbook.md).
- **CDS availability and rate limits** become the caller's availability. VizieR
  has no SLA for programmatic bulk use. The reader sets a `User-Agent` naming
  Skycat and its version so CDS can attribute load.
- **A new failure taxonomy** — §4.7.
- **Caching** — §4.9.
- **Mirror choice.** `vizier.cds.unistra.fr` versus `vizier.cfa.harvard.edu` can
  hold different snapshots. One configured server, one reader, one place it is
  read (§4.12), and `_designation` does not disambiguate mirrors — open item 6.

### 5.7 Code-quality gates — **the port is a rewrite, and that is the point**

| Legacy pattern | Skycat gate | Disposition |
|---|---|---|
| `except Exception: pass` ×3 in the astroquery monkey-patch | ruff `BLE001`, `S110` — errors | No monkey-patching (§4.9) |
| `except Exception: val = None` in `table_to_sources` | `BLE001` | Typed coercion per `RemoteColumn.kind`; a coercion failure is a `RemoteResponseError`, not a silent `None` |
| `eval(expr, ctx, {})` per row | no rule forbids it; it is the opposite of `_quality_clause` | No `eval`, no `compile` (§4.4.2) |
| Untyped `mags: Dict[str, List[str]]` with `item[:2]` / `item[0]` fallbacks | pyright null-safety rules are errors and are at zero | Frozen dataclasses (§4.4) |
| `type(classname, (VizierCatalog,), kw)` from unvalidated dicts | pyright cannot see the classes at all | No dynamic class creation |
| `compile(...).co_names` column harvesting | works, but needs a *why* comment | Not carried over; the def lists its columns |

The rewrite has to re-derive the intent of expressions like the 2MASS `(J-K)`
cubics from papers cited only by a URL in a comment — which is one more reason
those tables stay downstream (§1.3.4).

### 5.8 Non-hermetic tests — **handled by two markers**

Network tests fail when CDS is slow, are the first thing disabled when CI gets
flaky, and once disabled stop catching anything. §7 is the answer: recorded
VOTable/TAP fixtures for the deterministic assertions, plus a separately-marked
live test run on demand — mirroring how `postgis` already works, including the
`--require-postgis` escalation that turns silent skipping into a hard failure.

### 5.9 Namespace collision — **real, and the reason for the prefix**

`skynet-db`'s routing layer already uses `SKYCAT_BACKEND`, `SKYCAT_REMOTE_FALLBACK`,
`SKYCAT_FALLBACK_ON_EMPTY`, `SKYCAT_LOCAL_FAMILIES` and `SKYCAT_<NAME>_RELEASE` —
the *consumer's* routing config, in Skycat's namespace but not read by Skycat.
Note that `SKYCAT_REMOTE_FALLBACK` is already taken, by something Skycat must
never implement. Every new variable is `SKYCAT_REMOTE_<NOUN>` naming a transport
setting, never a routing decision (§4.12), and the README's configuration table
gains a sentence saying which `SKYCAT_*` variables Skycat reads and which belong
to the pipeline.

## 6. Action plan

Six phases, all remote. The local track they follow is
[local-catalogs.md](local-catalogs.md) §5 (L0–L5). Each phase here is
independently valuable and independently abandonable, and each names what it
touches, what must be true before it is done, and the condition under which it
should stop rather than continue.

```
        local track ─── L0 measure ─▶ L1 Tycho-2 ─▶ L2 RefCat2 ─▶ L3 GSPC ─▶ L4 2MASS
                            │              │
   (shares the dev-extra astroquery dep)   └── each mirrored family adds an R1 parity pair
                            ▼              ▼
R0 decision ──┐
              ├─▶ R2 reader + resolve() ─▶ R3 first cone (Tier D) ─┬─▶ R4 more Tier D
R1 mapping + parity (dev) ──┘                                      └─▶ R5 lint + migration
```

**R0–R2 do not depend on the local track** and can run in parallel with it: the
decision record, the mapping layer, the parity harness and SIMBAD name resolution
are all useful whatever gets mirrored, and R1 shares L0's dev-extra astroquery
dependency rather than adding one. **R3 onward should wait for L1**, because the
Tier D boundary is what makes the remote catalog set defensible, and because
mirroring Tycho-2 removes it from the remote candidate list.

If capacity is limited, the highest-value ordering across both notes is
**L1 → R0 → R1 → L0 → L2 → R2**: the cheapest local win, then the decision that
settles the architecture, then the harness that catches silent mapping errors,
then the measurements the large ingests depend on.

### R0 — Decide and record (0.5–1 day)

**Scope.** Write `docs/decisions/0002-remote-catalogs-are-a-separate-reader.md`.
It must state: that `CatalogReader` never reaches the network; that decision 0001
is **not** superseded, with the §1.2 argument for why an adjacent reader is not a
fallback; that remote rows carry no release; that routing stays downstream; that
the six-touch-point rule for local families does not bind a remote catalog,
because a remote catalog has no parser, model, migration or validation module
(§3). Close open items 1, 2 and 6 (§8) or record them as still open.

**Files.** `docs/decisions/0002-*.md`, `docs/decisions/README.md`,
`docs/reference/architecture.md` (one paragraph naming the second reader).

**Acceptance.** Decision record merged. A reader who disagrees with the design
has one file to argue with.

**Stop here if** the decision cannot be written without superseding 0001 — that
means the design is a fallback wearing a different name, and it should go back to
the drawing board rather than into code.

### R1 — Mapping layer and parity harness, dev-only (3–5 days)

The highest-value phase and the one that ships nothing. It builds the part of the
system most likely to be silently wrong (§2.9.2) and proves it against a source
of truth before any of it is on a runtime path.

**Scope.**
- `astroquery >= 0.4.8` in the **`dev` extra only**. No change to `dependencies`.
- `skycat/remote/defs.py` and `skycat/remote/mapping.py` — the typed def (§4.4)
  and the row mapper (§4.3): hours→degrees, sexagesimal with the `-00 30 00` sign
  case, `null_above` sentinels, `'`→`_` column renaming, the haversine.
- Defs for the four families with a local counterpart: `apass` (DR9,
  `comparable="no"`), `vsx` (`"approximate"`), `landolt` (`"yes"`), `stetson`
  (`"yes"`).
- A `network` pytest marker plus `--require-network` / `SKYCAT_REQUIRE_NETWORK=1`,
  mirroring `--require-postgis` exactly.
- `tests/test_remote_mapping.py` — offline, against committed VOTable/TAP
  fixtures. Covers every def's id column, every sentinel, the sexagesimal sign
  case, and the haversine against known separations.
- `tests/test_remote_parity.py` — marked `postgis` **and** `network`. For each
  `comparable="yes"` pair: a fixed field, a local `CatalogReader.cone()`, the
  equivalent remote cone, assertions on matched-source count, position agreement
  (sub-milliarcsecond — these are the same numbers reached by different code) and
  magnitude agreement.
- A `separation_deg` cross-check against `ST_Distance(…, false)` over a grid
  including both poles and the RA 0/360 seam.

**Acceptance.**
- `landolt`/`1992`/`II/183A` and `stetson`/`stetsonglobs` parity tests pass live.
- Every mapping assertion also passes offline against fixtures with no network.
- `apass` and `vsx` appear in the def table with `comparable` set and a reason.
- Default `uv sync` still installs five runtime dependencies.

**Stop here if** the parity tests fail on position. That is a bug in
`skycat/ingestion/parsers/{landolt,stetson}.py` — a claim in a docstring turning
out false — and it outranks every other item in this document.

### R2 — `RemoteCatalogReader` skeleton and `resolve()` (3–5 days)

Smallest honest runtime surface: the one operation with no local analogue, no
release semantics, no schema impedance and no photometric mapping (§4.5).

**Scope.**
- Runtime extra `skycat[remote]`; `skycat/remote/{__init__,reader,simbad,errors}.py`.
- `RemoteCatalogReader.__init__`, `from_env`, `close`, context manager,
  `resolve()`.
- The §4.7 exception tree; `_FriendlyGroup` mapping (`RemoteCatalogError` → 1,
  missing extra → `CatalogConfigError` → 2).
- Structured logging under `skycat.remote`, per the `skycat.ingestion` convention.
- Guard tests: `skycat/client.py` does not import `skycat.remote`;
  `import skycat` leaves `astroquery` out of `sys.modules`; a clear, actionable
  error when the extra is absent.
- `SKYCAT_REMOTE_*` variables (§4.12), each with a test that fails if it stops
  being read.
- Docs: `docs/reference/remote.md`, README configuration table, api-stability
  entries for the new exception names — **same commit**, because
  `tests/test_docs.py` enforces it.

**Acceptance.** `resolve("M31")` returns bounded, ICRS-degree results; a SIMBAD
outage raises `RemoteServiceError` on that call and the *next* call retries
(§2.6.1); `limit` bounds the list on every path; nothing about `CatalogReader`
changed.

### R3 — First remote catalog: USNO-B1.0 (5–8 days)

**Scope.** `cone()` for exactly one Tier D catalog — one with **no local
counterpart and no prospect of one**, so §5.2 cannot bite and the phase is not
building something the local plan will later obsolete. **USNO-B1.0** (`I/284`) is the
pick: 1.05 billion rows at ~420 GB puts it out of mirroring range permanently
([local-catalogs.md](local-catalogs.md) §2.7), its schema is plain (`USNO-B1.0` designation as `native_id`, four plate
magnitudes plus B1/B2/R1/R2, no transformation expressions), and remote is
therefore its only path — which is precisely the demand `RemoteCatalogReader`
exists to serve. UCAC5 (`I/340`) is the alternative if a narrower schema is
wanted first, but note it is only 45 GB and could be argued back into the local
track.

Plus `skycat/remote/vizier.py` (transport, timeouts, bounded retries),
`catalogs()`, `describe()`, `mag_min`/`mag_max` rendered by the mapping layer
(§4.6), and a CLI `skycat remote cone` behind the extra.

Tycho-2 was this phase's candidate in an earlier revision. The local plan
mirrors it instead ([local-catalogs.md](local-catalogs.md) §2.1, ~0.9 GB); it
becomes a parity target for R1 rather than a remote catalog.

Measure TAP versus the POST path here (open item 5) and record the result; it is
the only phase where the answer is cheap to get.

**Acceptance.** Every row carries `_source`/`_service`/`_designation`/`_retrieved_at`
and no release key; `mag_min=12, mag_max=15` demonstrably filters (the §2.7
defect-2 regression test); a CDS timeout raises `RemoteTimeoutError` within
`service_timeout_s` including retries; `skycat remote cone` exits 1 on a service
failure and 2 with the extra uninstalled; offline fixture tests cover the whole
path.

**Stop here if** no consumer has asked by the time R2 lands. R0–R2 are
valuable standalone, and once the local plan lands the remaining remote
candidates are exactly Tier D — real, but nobody has requested them, and
`skynet-db` already has working remote providers for all six candidates.

### R4 — Remaining Tier D catalogs (1–2 days each)

NOMAD (`I/297`), full Gaia DR3 (`I/355/gaiadr3`), full PanSTARRS DR2 (`II/349`),
plus USNO-B1.0, and whichever candidates the local plan's measurements pushed
into Tier D on budget grounds —
added one def at a time, each with fixture tests, in priority order set by
whoever asked. **Anything the local plan mirrored does not get a remote def**, except as a
parity target; two paths to the same catalog is §1.2's problem re-entering through
the back door.

Photometric transformations stay out (§1.3.4). A general remote `QualityFilter`
with `RemoteConstraintError` belongs here if it is needed. `lookup()` by native
id, if it is wanted, lands here too.

**Acceptance, per catalog.** A def, a fixture, an id-column test, a sentinel test,
and a row in the docs table. No catalog ships without a fixture.

### R5 — Designation lint and consumer migration (2–4 days)

**Scope.** A scheduled CI job using `describe()` to verify every
`reference_url`/designation in both `catalog_defs.py` and `remote/defs.py` still
resolves and has not been superseded (§4.11). Then, separately, retire
`skynet`'s hand-rolled integrations: `/v1/catalog-objects/vizier/apass` (§2.7)
onto `RemoteCatalogReader.cone()`, and `target_search.py`'s SIMBAD block (§2.6)
onto `resolve()`. Both are `skynet` PRs, not Skycat ones, and both are the
payoff: eight defects and seven defects respectively, deleted rather than fixed
in place.

**Acceptance.** The lint job runs weekly and fails loudly on a superseded
designation. The public-api endpoint returns degrees in a field named degrees,
bounded and timed out, with its magnitude filters actually filtering.

### Explicitly not phases

- Remote fallback in `CatalogReader` — §1.3.1. Not behind a flag either.
- A unified `Reader` facade — §1.3.2.
- `skycat fetch` — §1.3.3.
- Photometric transformation tables — §1.3.4.
- Remote `batch_crossmatch` — §4.10.
- **A remote def for any catalog the local plan mirrored.** Locally mirrored catalogs
  get a remote def only as an R1 parity target, never as a servable `cone()`
  catalog. Offering both paths to one catalog is the ambiguity §1.2 exists to
  prevent, arriving from the other direction.

---

## 7. Testing strategy

Three tiers, because the failure mode this whole document is about is *returning
nothing without raising* (§2.9.2), and only the third tier catches it.

1. **Offline fixture tests** — committed VOTable/TAP responses, no marker, run on
   every commit in the unit matrix. Cover the entire mapping layer: every def's
   id column, every sentinel, unit conversion, sexagesimal signs, the `'`→`_`
   rename, the haversine, and the error mapping for a malformed response. These
   must be able to fail — capture a real response, then hand-edit variants
   (missing column, sentinel value, null id, empty table) rather than only
   recording the happy path.
2. **Live service tests** — marked `network`, skipped by default, escalated by
   `--require-network` / `SKYCAT_REQUIRE_NETWORK=1`. These catch upstream drift:
   a renamed column, a superseded designation, a changed default server. Run
   nightly, not per-commit.
3. **Parity tests** — marked `postgis` **and** `network`. The only tier that can
   detect a mapping that is self-consistent and wrong, because it has an
   independent source of truth for the same sky. Restricted by construction to
   `comparable="yes"` defs; the harness reads `comparable` from the def table
   (§4.4) so an excluded pair is visibly excluded rather than quietly absent.

Two standing rules:

- **A def without a fixture does not ship.** §2.3's NOMAD example is what a
  declarative table looks like with no test behind it.
- **An empty result is a suspicious result.** Every fixture test asserts a
  non-zero row count where one is expected; a filter test asserts the filter
  changed the count in the direction claimed. §2.7 defect 2 would have been
  caught by one such assertion.

`tests/test_docs.py` asserts that every CLI flag and `CatalogReader` kwarg shown
in the stable docs exists. Extend it to `RemoteCatalogReader` when R2 lands,
so the remote surface is held to the same standard.

---

## 8. Open items to verify before implementing

1. **Branch state of the downstream routing layer.** `catalogs/local/` in
   `skynet` has only stale bytecode on `pipeline/analysis-descriptive-split`.
   Confirm whether `feat/skycat` / `add-landholt-and-stetson` merged, and whether
   `SKYCAT_LOCAL_FAMILIES` in the Compose worker is still pinned to `APASS,VSX`
   (excluding Landolt and Stetson). Blocks R0's claim that routing is
   already downstream.
2. ~~**APASS DR10 on VizieR.**~~ **Verified 2026-08-07: still not queryable.**
   VizieR serves DR9 as `II/336/apass9`; AAVSO states DR10 "cannot be
   automatically queried" and distributes it directly. §5.2 stands. Re-check
   before R4; the AAVSO page tracking the VizieR schedule is the source.
3. **Landolt 2009 designation.** `J/AJ/137/4186` is inferred from
   `catalog_defs.py`'s description; the legacy plugin only knows `II/183A`.
   Confirm before adding a second `comparable="yes"` pair in R1.
4. **astroquery version floor and ceiling.** `>= 0.4.8` is required for the TAP
   SIMBAD columns (§2.6.2). `skynet` pins `==0.4.10` in three requirements files
   and `skylib` declares `~= 0.4.9`; current stable is 0.4.11. Confirm the extra's
   constraint cannot conflict where both are installed.
5. **VizieR TAP batch semantics.** Whether `TAPVizieR` via `pyvo` offers upload
   joins good enough to revisit §4.10. Measure in R3; do not assume.
6. **Which VizieR server production actually reaches.** The skynet fork's
   `config.py` says `vizier.cds.unistra.fr` but nothing reads it and
   `vizier_server = None` (§2.4); Afterglow's default is
   `vizier.cfa.harvard.edu`. Mirrors can hold different snapshots, so this must
   be settled before any parity test compares against "the" VizieR.
7. **Whether `CUSTOM_VIZIER_CATALOGS` is populated in any deployment.** Afterglow
   ships it defaulting to `[]` and the skynet forks dropped it. If a deployment
   populates it, there are catalog definitions in operator config that exist in
   no repository — and per §2.3, any written in the documented `mags` shape are
   silently returning nothing.
8. **Does anything besides the two public-api routes call SIMBAD?** §2.6 found one
   integration by grep. Confirm before R5 plans its retirement.
9. **Whether the Tier D set holds.** It is derived from
   [local-catalogs.md](local-catalogs.md)'s sizing, which is itself derived from
   a schema model rather than a measurement. If L0's measured bytes-per-row comes
   in far above the model, catalogs move from the local plan into Tier D and R4
   grows. That note's open items 1–3 are therefore inputs to this one.

---

## 9. What would change the plan

**Would justify accelerating R3–R4 past the local track:**

- **The disk budget turning out to be much smaller than 4 TB, or shared.**
  [local-catalogs.md](local-catalogs.md) open item 1. Then the local plan ends at
  L1–L2 and everything above it becomes remote by measurement rather than by
  default.
- A consumer naming a Tier D catalog it needs now — full PanSTARRS, full Gaia
  DR3, NOMAD or SDSS. At ~1 TB each the first two cannot even be *rebuilt* inside
  4 TB, so remote is their only path.
- A second consumer of Skycat with no `skynet-db` provider layer of its own.
- Long-tail need: VizieR has tens of thousands of catalogs, and a generic remote
  path makes a one-off comparison against an arbitrary designation possible
  without adding a family. Nothing on the local path can ever be that cheap,
  because a local family needs a parser, a table and a migration.

**Would justify deferring the remote track further:**

- **The local plan landing in full.** Once L1–L4 are mirrored, Tier D is five
  catalogs of which three (NOMAD, USNO-B1.0, SDSS) are of marginal scientific
  value to this pipeline and two (full PanSTARRS, full Gaia) are largely
  subsumed by ATLAS RefCat2 and GSPC respectively. At that point R3–R5 serve very
  little and R0–R2 — decision record, parity harness, SIMBAD name resolution —
  are the whole worthwhile remote scope. **This is the most likely outcome**, and
  it is an argument for treating R0–R2 as the real remote deliverable and R3–R5
  as optional.
- L1 taking materially longer than 3–5 days. That would mean the six-touch-point
  path is more expensive than [guides/add-family.md](../guides/add-family.md)
  implies — a finding about the local track worth fixing before adding a second
  surface to maintain.

**Would justify reopening the fallback question** (and would require superseding
decision 0001 in a written record, not a PR):

- VizieR publishing APASS DR10, closing §5.2's gap.
- A documented incident where PostGIS unavailability caused a pipeline outage
  that a remote path would have prevented — weighed against 0001's argument that
  the wrong answer is worse than the clear failure.
- The downstream `LocalFirstCatalog` layer being retired, leaving no home for
  routing.

**Would not justify anything:**

- "It would be nice to have everything in one place."
- Making local development easier without a database — decision 0001's
  explicitly-rejected reasoning, and it applies verbatim.

---

## References

**Companion note** — [local-catalogs.md](local-catalogs.md): the local mirroring
plan this one is sequenced behind. It owns catalog sizing against the 4 TB
budget, per-catalog download sources, the six touch points, ingestion at
10⁸–10⁹ rows, and the L0–L5 phases. The Tier D set referenced throughout this
note is defined there.

**Skycat** — `skycat/client.py`, `skycat/query/cone.py`,
`skycat/query/crossmatch.py`, `skycat/registry/catalog_defs.py`,
`skycat/models/{apass,landolt,stetson,vsx}.py`, `skycat/cli/main.py`,
`pyproject.toml`; [decisions/0001](../decisions/0001-postgresql-postgis-only.md),
[reference/architecture.md](../reference/architecture.md),
[reference/api-stability.md](../reference/api-stability.md),
[guides/provenance.md](../guides/provenance.md),
[guides/add-family.md](../guides/add-family.md),
[operations/runbook.md](../operations/runbook.md).

**Skynet** (`pipeline/analysis-descriptive-split` @ `0040c28ab`) —
`packages/py/skynet-db/skynet_db/runners/observation_asset_processing/optical_data_processing/catalogs/`
(`vizier_catalogs.py`, `catalog.py`, `config.py`, `__init__.py`, and the ten
provider modules); `runners/common/catalog_plugins/` (the vestigial fork);
`apps/afterglow/docs/legacy/resources/catalog_plugins/` (the vendored Afterglow
snapshot); **`apps/public-api/public_api/services/target_search.py`** (SIMBAD);
**`apps/public-api/public_api/routers/catalog_objects.py`** and
`apps/public-api/public_api/schemas/catalog_objects.py` (the direct VizieR APASS
endpoint); `runners/utils.py`; `requirements.txt`,
`apps/public-api/requirements.txt`,
`packages/py/skynet-db/requirements.txt` (all pinning `astroquery==0.4.10`);
`packages/py/skylib/pyproject.toml` (`astroquery ~= 0.4.9`).

**Afterglow Core** (`master` @ `92aaf61`) —
`afterglow_core/resources/catalog_plugins/vizier_catalogs.py` (the
`CUSTOM_VIZIER_CATALOGS` class factory), `afterglow_core/models/catalogs.py` (the
`Catalog`/`CatalogSource` marshmallow base and the `CATALOG_OPTIONS` merge),
`afterglow_core/default_cfg.py` lines 110–145 (the catalog config block).
`resources/data_provider_plugins/imaging_survey_provider.py` uses
`astroquery.skyview` for image cutouts and mentions SIMBAD only as SkyView's own
name resolver — a separate concern, out of scope here.

**Vault** — `wiki/resources/entities/astroquery-VizieR.md`,
`wiki/resources/concepts/{Local-First Catalog Architecture,Catalog Selection and Provider Registry,Catalog Release Provenance}.md`,
`wiki/resources/incoming/Astroquery VizieR Local Catalog Investigation.md`.

**Upstream** — [astroquery VizieR docs](https://astroquery.readthedocs.io/en/latest/vizier/vizier.html),
[astroquery SIMBAD docs](https://astroquery.readthedocs.io/en/stable/simbad/simbad.html),
[astroquery SIMBAD module evolutions (the 0.4.8 TAP rewrite)](https://astroquery.readthedocs.io/en/stable/simbad/simbad_evolution.html),
[astroquery on PyPI](https://pypi.org/project/astroquery/) (0.4.11 current),
[VizieR II/336 (APASS DR9)](https://cdsarc.cds.unistra.fr/viz-bin/cat/II/336),
[AAVSO APASS DR10 download](https://www.aavso.org/download-apass-data),
[AAVSO: APASS DR10 VizieR schedule](https://www.aavso.org/apass-dr10-vizier-schedule).

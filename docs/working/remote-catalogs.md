---
status: open
reviewed: 2026-08-07
branch: docs/catalog-coverage-split
authority: service documentation (CDS/SIMBAD, IPAC/NED, CfA/ADS, astroquery) + code inspection (skycat @ 2c36084, skynet @ 0040c28ab, afterglow-core @ 92aaf61)
implementation: not-started
document-type: research survey — inputs for a future design, not a design
---

# Remotely queried catalog services — VizieR, SIMBAD, NED, ADS

**What this document is.** A survey of the astronomical data services Skycat
would have to query live rather than mirror: what each one is, what question it
answers, how it is accessed, what it costs operationally, and whether Skycat
should support it. It also inventories the three places the Skynet/Afterglow
codebase already talks to these services, because how that has gone before is the
best available evidence about how it will go again.

**What this document is not.** It is not a design or an architecture decision
record. It proposes no classes, signatures, module layouts or phases. Where it
states a requirement, it is because the survey turned up a constraint — an
existing contract, a service limit, a failure mode already observed in production
— that any design will have to answer. A design agent should be able to read this
plus [local-catalogs.md](local-catalogs.md) and have the material to plan the
work.

**Companion:** [local-catalogs.md](local-catalogs.md) surveys the sources that
*can* be mirrored, sizes them against a 4 TB budget, and says where to download
them. The two documents divide the same landscape along one line: **does the
provider distribute bulk files, and is the volume worth the disk?**

## The one position that is not open

**`CatalogReader` stays strictly local PostgreSQL/PostGIS, and any remote access
lives in a separate, adjacent surface** — working name `RemoteCatalogReader`. Not
a mode, not a fallback, not a flag on the existing class.

The reasoning, because everything below assumes it.
[Decision 0001](../decisions/0001-postgresql-postgis-only.md)'s argument against a
Python-side query fallback is about failure semantics rather than mathematics:
*the path taken when the fast one is unavailable is the path taken during an
incident, on an unfamiliar deployment, by whoever is on call; a slow,
subtly-different query path is worse than a clear failure to connect, because the
failure gets diagnosed and the wrong answer does not.* That applies with more
force to a network path, which adds third-party availability and rate limiting on
top. A separate class with a different name, a different dependency set and no
release vocabulary is not a fallback — it is a different question asked
deliberately. §4 lists the contracts that keeps intact.

---

## 1. Four systems, four questions

| System | Operator | Question it answers | Returns | Access | Auth | Support? |
|---|---|---|---|---|---|---|
| **VizieR** | CDS, Strasbourg | *What sources are in this region of sky, in catalog X?* | Table rows | HTTP POST to `viz-bin/votable`; TAP/ADQL via `TAPVizieR` | None | ✅ Yes — for the catalogs [local-catalogs.md](local-catalogs.md) defers |
| **SIMBAD** | CDS, Strasbourg | *Where is the thing called "M31"? What kind of object is it?* | One object: identifiers, object type, basic data, bibcodes | TAP/ADQL | None | ✅ **Yes — highest value.** No local substitute exists |
| **NED** | IPAC, Caltech | *What is this galaxy's redshift? What else is this object called?* | One extragalactic object: cross-IDs, redshift, photometry | New IVOA TAP API; legacy CGI APIs deprecated | None | ⚠️ Conditional — only if the pipeline does extragalactic work |
| **ADS** | CfA, Harvard | *What has been published about this?* | Papers | REST API | **API token required** | ❌ Not as a catalog surface. Narrow provenance use at most |

The structural point, and the reason this document exists separately from its
companion: **only VizieR distributes bulk files.** SIMBAD, NED and ADS publish no
dump — their product is curation across the literature, which is a living process
rather than a snapshot. A mirrored SIMBAD would be stale the day it landed.

**These are not four sources of the same thing.** VizieR answers a spatial
question about catalog rows, which is what Skycat already does locally. The other
three answer questions Skycat has never been able to answer at all, and the gap
between them matters more than the overlap:

- **SIMBAD and NED both resolve names, and they disagree** — legitimately. SIMBAD
  is curated predominantly around galactic and stellar objects, NED exclusively
  around extragalactic ones. Which one is right depends on what was asked. Any
  design treating "name resolution" as one operation with one backend will be
  wrong for half its inputs.
- **ADS is not about objects at all.** §2.4.

---

## 2. The services

### 2.1 VizieR — the catalogs Skycat will not mirror

**What it is.** The CDS catalog service: an archive and query interface over
roughly 50,000 published astronomical catalogs and tables. It is simultaneously a
bulk distribution channel — covered in [local-catalogs.md](local-catalogs.md)
§3.1 — and a query service. This section is the query side.

**What Skycat would use it for.** The catalogs the local survey deferred, on
evidence, because they cannot be rebuilt inside 4 TB or are not worth the disk:

| Catalog | Designation | Rows | Why remote |
|---|---|---|---|
| Pan-STARRS DR2 | `II/349` | 1.92 B | ~1.15 TB; cannot be rebuilt in 4 TB. ATLAS-RefCat2 already carries PS1-derived griz all-sky to m<19 |
| Gaia DR3 main | `I/355/gaiadr3` | 1.81 B | ~1.10 TB; cannot be rebuilt in 4 TB. GSPC is the photometrically useful subset |
| NOMAD | `I/297` | 1.12 B | ~450 GB of a *compilation* whose components are better mirrored individually |
| USNO-B1.0 | `I/284` | 1.05 B | ~420 GB of photographic-plate photometry; fits, but not worth it |
| SDSS DR17 | — | ~1.2 B | A SQL-region service with a survey footprint; structurally not a mirroring candidate |
| The long tail | ~50,000 others | — | A one-off comparison against an arbitrary designation, with no family added |

That last row is the capability local mirroring structurally cannot match. A
local family costs six touch points, a parser, a table and a migration; a remote
query against an arbitrary designation costs a designation string.

**Access mechanisms, and there are two.**

| | `viz-bin/votable` POST | TAP / ADQL |
|---|---|---|
| Used by | The legacy plugins (§3.1) | `TAPVizieR`, reachable through `pyvo` (already an astroquery dependency) |
| Query language | Form parameters plus a filter mini-language | ADQL |
| Batch / upload joins | None | Possibly — unverified, open item 3 |

Whether TAP offers upload joins good enough to change the crossmatch picture
(§4.3) is the single most consequential unknown about this service.

**Operational notes.**

- **No SLA for programmatic use.** CDS availability becomes the caller's
  availability. The legacy code's entire caching apparatus exists to reduce load
  on it.
- **Mirrors can hold different snapshots.** `vizier.cds.unistra.fr` versus
  `vizier.cfa.harvard.edu`. Which one a query reaches is a third axis of "which
  data did I get", after catalog and release — and §3.4 shows the existing code
  does not actually know which one it uses.
- **The release-identity problem is concrete.** VizieR serves APASS **DR9** as
  `II/336/apass9`. Skycat serves DR6 and DR10. AAVSO states DR10 "cannot be
  automatically queried" and distributes it directly. The overlap is empty, on
  the largest and most-used family. Verified 2026-08-07. §4.2.

**Verdict: support.** It is the only one of the four that returns catalog rows,
and there is a defined, evidence-backed set of catalogs for it to serve.

### 2.2 SIMBAD — name and object resolution

**What it is.** The CDS reference database for astronomical objects outside the
solar system: identifiers, cross-identifications, object types, basic data,
bibliography and measurements, curated from the literature. Predominantly
galactic and stellar in emphasis.

**Scale, as reported by the service on 2026-08-09:**

| | |
|---|---|
| Objects | 21,820,449 |
| Identifiers | 72,552,525 |
| Bibliographic references | 466,020 |
| Object citations in papers | 53,873,557 |

**What it gives that no local mirror can.** Name resolution. Skycat's `lookup()`
takes a `native_id` within one family — "the APASS source whose id is X". SIMBAD
answers "where is the thing called M31", across 72.5 million identifiers spanning
every naming convention in the literature. That is a fundamentally different
operation, it has no local analogue, and it does not become mirrorable with more
disk: the value *is* the ongoing curation.

**Access.** TAP/ADQL. `astroquery.simbad` was **rewritten onto SIMBAD's TAP
interface in astroquery 0.4.8**, which is a hard version boundary:

- All query methods now return **lowercase** column names (`main_id`, `ra`,
  `dec`, `otype`). 0.4.7 and earlier returned `MAIN_ID`, `RA`, `DEC` — and
  `RA`/`DEC` were *sexagesimal strings*, not float degrees.
- `query_objects` gained a `user_specified_id` column carrying the input name.
- The `typed_id` votable field was removed and `query_criteria` deprecated.

Code depending on the post-0.4.8 shape breaks silently on a downgrade — and §3.6
shows the production code already depends on it.

**Bulk availability: none.** The service documents query interfaces only. One
official mirror exists, hosted by the CfA team at Harvard
(`simbad.cfa.harvard.edu`), alongside Strasbourg.

**Verdict: support, and this is the highest-value remote capability in the
survey.** Small, well-defined surface — one string in, a list of (identifier,
object type, coordinates) out. No release semantics to violate, no photometric
band mapping, no schema impedance with Skycat's typed columns, and no local
substitute. It is also where the existing production integration has the most
identified defects that a supported implementation would fix by construction
(§3.6).

### 2.3 NED — the extragalactic database

**What it is.** The NASA/IPAC Extragalactic Database: a master list of
extragalactic objects — galaxies, quasars, radio/X-ray/infrared sources — with
cross-identifications established across the literature, positions, redshifts and
collected basic data.

**Scale, as of June 2026:**

| | |
|---|---|
| Distinct objects | 1.107 billion |
| Photometric data points | 13.96 billion |
| Redshifts | 15.73 million |
| Objects with redshifts | 8.99 million |

Photometry from GALEX, 2MASS and AllWISE has been joined in.

**What it would give the pipeline.** Two things nothing else here provides:

1. **Extragalactic name resolution.** SIMBAD resolves names too, but the two
   databases are curated around different populations and will not always agree —
   or even both know the object.
2. **Redshifts and host-galaxy data.** For supernova follow-up — a substantial
   part of what Skynet is used for — a host galaxy's redshift and distance are
   directly useful, and 8.99 million objects have one.

**Access, and a live risk.**

- The current interface is a **new set of APIs layered on IVOA TAP**.
- **The legacy APIs are deprecated and cannot access data ingested after January
  2026.** That is not a future deprecation; it is already in effect.
- `astroquery.ipac.ned` provides `query_object`, `query_region`,
  `query_region_iau`, `query_refcode`, `get_table` (photometry, diameters,
  redshifts, references, positions) and image/spectra retrieval. Its
  documentation states that service URLs are taken from Mazzarella et al. (2007)
  and mentions neither the new TAP API nor any deprecation — which strongly
  suggests it is built on the legacy interface.

**If that inference is right, `astroquery.ipac.ned` is already blind to anything
NED ingested after January 2026, silently.** Confirming which endpoint the module
calls is the first thing anyone should do before designing around it (open item
2). This is the §3.9 signature again: a remote path that returns *something*,
just not the right something, and raises nothing.

- **Stated volume limits.** NED documents that its "ability to support automated
  access involving large data volumes or high query rates is limited." That is an
  explicit request to be gentle, and it rules out any pattern looping over
  thousands of sources.

**Bulk availability: none in general.** One exception — the Local Volume Sample
(NED-LVS), a curated table rather than a service query, surveyed in
[local-catalogs.md](local-catalogs.md) §3.6.

**Verdict: conditional.** Genuinely valuable *if* the pipeline does extragalactic
work; otherwise it is a second name resolver that mostly disagrees with the first
one. Needs a consumer to state a requirement (open item 6), and the endpoint
question (open item 2) needs answering before any design work, because it may
mean the obvious library is the wrong one.

### 2.4 ADS — the literature database

**What it is.** The Astrophysics Data System: the bibliographic database for
astronomy. It indexes papers, not sources.

**Access.**

| | |
|---|---|
| Library | `astroquery.nasa_ads` |
| Auth | **API token required** — `ADS_DEV_KEY` environment variable, module config, or `~/.ads/dev_key` |
| Rate limit | **5,000 requests/day** per token for most request types |
| Increases | By email to `adshelp@cfa.harvard.edu` with usage statistics and justification |

**The scoping question, stated plainly.** ADS returns papers. Skycat's row shape
is astronomical sources with typed photometric columns carrying units in their
names. There is no sense in which a bibliography is a catalog query result, and
putting one behind a catalog reader would be a category error — the surface would
share nothing with the other three services except the word "remote".

**The one narrow role that does make sense: provenance verification.**
[guides/provenance.md](../guides/provenance.md) names a specific hazard — CDS
occasionally supersedes a catalog with a new designation (`II/183` → `II/183A`),
and a silently-different upstream is exactly what the provenance chain exists to
prevent. Every `FamilyDef` carries a `reference_url`, and most catalogs have a
canonical paper with a bibcode. Checking that a catalog's reference still
resolves and matches the release it claims to be is a legitimate provenance lint.
It is also entirely optional, would run in CI rather than at query time, and
could plausibly be done with VizieR's own metadata calls, which need no token.

**ADS would also introduce an operational class Skycat has never had: a
third-party API credential.** Today the only secrets are database passwords under
`SKYCAT_DB_*`, with a documented rotation procedure in the runbook. An ADS token
means a new secret in the Kubernetes manifests, a new rotation path, a new
failure mode when it expires, and a per-token quota shared with whatever else
uses it. That is real cost for a lint.

**Verdict: not as a catalog surface. At most an optional provenance check, and
only after establishing that VizieR's own metadata cannot do the same job without
a credential** (open item 5).

---

## 3. How this has been done before

The Skynet/Afterglow codebase already talks to these services in three places,
under three lineages, in six copies. This is the best available evidence about
what integrating them costs, and the recurring failure signature (§3.9) is the
most important finding in this document.

### 3.0 The inventory

| # | Integration | Location | Layer | Status |
|---|---|---|---|---|
| **1** | VizieR plugin layer | `skynet/…/optical_data_processing/catalogs/` | pipeline | **Authoritative in production** |
| 1′ | — vestigial fork | `skynet/packages/py/skynet-db/skynet_db/runners/common/catalog_plugins/` | — | Older fork; only query consumer is dead code |
| 1″ | — upstream original | `afterglow-core/afterglow_core/resources/catalog_plugins/` @ `92aaf61` | — | Flask-config-driven; carries two declarative mechanisms the forks dropped (§3.3) |
| 1‴ | — vendored snapshot | `skynet/apps/afterglow/docs/legacy/resources/catalog_plugins/` | — | Byte-identical copy of 1″ |
| **2** | SIMBAD target resolver | `skynet/apps/public-api/public_api/services/target_search.py` | public API + SSR site | **Live** (§3.6) |
| **3** | Direct VizieR APASS route | `skynet/apps/public-api/public_api/routers/catalog_objects.py:54` | public API | **Live**, bypasses the plugin layer entirely (§3.7) |

Integrations 2 and 3 are absent from every previous analysis of this problem.
They matter disproportionately: integration 2 is the system's only SIMBAD
dependency, and integration 3 is direct evidence that when the plugin layer is
too entangled to reuse, people write astroquery into a route by hand.

The Afterglow → skynet fork replaced Flask's `current_app.config` with module
constants and marshmallow's `CatalogSource` with a pydantic one. Both
substitutions changed behaviour; §3.3 and §3.4 are the consequences.

### 3.1 The VizieR plugin contract

`Catalog` (`catalog.py`, 45 lines) is a bare abstract base: `name`,
`display_name`, `num_sources`, `mags`, `filter_lookup`, and three query methods
that raise `NotImplementedError`.

`VizierCatalog` (`vizier_catalogs.py`, 352 lines) implements it against
`astroquery.vizier.Vizier`. A subclass is almost pure declaration — four class
attributes and occasionally a `table_to_sources` override:

| Attribute | Meaning |
|---|---|
| `vizier_catalog` | VizieR designation, e.g. `II/336` |
| `row_limit` | Cap passed to `Vizier(row_limit=…)` |
| `col_mapping` | `{CatalogSource attr: VizieR column name *or* Python expression}` |
| `mags` | `{band: [mag_col, err_col]}` |
| `extra_cols` | Columns requested that map to no attribute |
| `sort` | VizieR sort keys, `+col` / `-col` |
| `filter_lookup` | `{FITS FILTER value: band name *or* transformation expression}` |

The base `__init__` derives the column request list by `compile()`-ing every
`col_mapping` value as a Python expression and harvesting `co_names`; a
`SyntaxError` means the value was a literal column name and is used verbatim.
That is how `'RAJ2000/15'` requests `RAJ2000` while `'recno'` requests `recno`.

`table_to_sources` runs the inverse per row: try `row[expr]` first, fall back to
`eval(expr, {**numpy.__dict__, **row_columns}, {})`. Magnitudes come from the
`mags` pairs with a `"'"` → `"_"` retry (VizieR renames `g'mag` to `g_mag`) and
are **kept only when `val and val < 99`** — the sentinel filter turning VizieR's
99.99 "no measurement" into an absent band. **A source with no surviving
magnitudes is dropped entirely.**

Three things a future design has to decide about deliberately:

- The declaration/expression split is a good idea; per-row `eval` is not (§4.8).
- The `< 99` sentinel rule is real domain knowledge and must survive in some form
  — but note `val and val < 99` also drops a genuine `0.0` magnitude.
- **The drop-sources-with-no-magnitudes rule must not survive.** A Skycat cone
  returns positional rows regardless of photometry; silently returning fewer rows
  for invisible reasons is the §3.9 failure mode.

### 3.2 The lookup tables

Eleven providers. Ten are `VizierCatalog` subclasses; SDSS uses SQL-region
queries.

| Key | `name` | VizieR ID | `num_sources` | `row_limit` | `sort` | Bands |
|---|---|---|---|---|---|---|
| `APASS` | `APASS` | `II/336` | 61,176,401 | 1000 | `+Bmag` | B, V, gprime, rprime, iprime |
| `PanSTARRS` | `PanSTARRS` | `II/349` | 1,919,106,885 | 5000 | `+rmag` | g, r, i, z, y |
| `SDSS` | `SDSS` | — (SQL) | — | — | — | u, g, r, i, z |
| `SkyMapper` | `SkyMapper` | `II/358/smss` | 285,159,194 | 5000 | `+rPSF` | u, v, g, r, i, z |
| `Landolt` | `Landolt` | `II/183A` | 526 | *none* | — | V + B−V, U−B, V−R, R−I, V−I |
| `Stetson` | `StetsonGlobs` | `J/MNRAS/485/3042/table4` | 4,890,955 | 10000 | `+Rmag` | U, B, V, R, I |
| `2MASS` | `2MASS` | `II/246` | 470,992,970 | 5000 | `+Jmag` | J, H, K, B, R |
| `Tycho` | `Tycho2` | `I/259` | 2,539,913 | 1000 | `+BTmag` | B, V |
| `UCAC` | `UCAC5` | `I/340` | 107,758,513 | 5000 | `+f.mag` | Open, G, R, J, H, K |
| `USNO` | `USNOB1` | `I/284` | 1,045,175,762 | 5000 | `+B1mag` | B, R, B1, B2, R1, R2 |
| `VSX` | `VSX` | `B/vsx/vsx` | 2,115,593 | *none* | — | ~40 band names, all empty values |

**Two defects visible in the table itself.** The registry keys and `name`
attributes disagree for four providers (`Tycho`→`Tycho2`, `UCAC`→`UCAC5`,
`USNO`→`USNOB1`, `Stetson`→`StetsonGlobs`); the vault's *Catalog Selection and
Provider Registry* note flags this as a live failure mode, because lookup-based
filter resolution keyed on one and not the other misses a catalog's custom map.
And `num_sources` are hardcoded and stale — the VSX entry says 2,115,593 while
Skycat's own `approx_row_count` for the same catalog is 10,300,000.

**Column mappings.** RA is in *hours* at this interface — every mapping divides
by 15 — and Skycat is degrees throughout. Two providers parse sexagesimal inside
an `eval`'d string expression, including a sign fix-up
(`(1 - 2*(DEJ2000.strip().startswith("-")))`) that exists because `-00 30 00`
loses its sign through `int()`. **Skycat's `landolt.py` and `stetson.py` parsers
reimplement precisely that arithmetic in normal Python, deliberately, so the two
backends agree** — a claim currently asserted in a docstring and verified
nowhere. Tycho-2's id is a `str.format` call evaluated per row.

**Filter lookups.** The most science-laden and least-tested part:
Jester/Jordi and Lupton transformations for APASS, Tonry P1→SDSS for
Pan-STARRS, constant offsets for SkyMapper, cubic polynomials in `(J−K)` for
2MASS, a `*` wildcard for UCAC5. The registry then overlays a *second, larger*
`filter_lookup` per provider at construction — APASS gains 30 keys covering
`Open`/`Clear`/`Lum`, naming aliases, astrophotography filters and sixteen
curriculum combinations (`R+Red`, `R,Red`, `Green+V`, …).

Two observations for a designer:

1. **These are consumer-layer concerns.** They map a FITS `FILTER` header value
   to a reference band. Skycat has never seen a FITS file.
2. **Skycat already declined to own the analogous derivation.**
   `models/landolt.py` stores V plus five colour indices and explicitly does not
   store U/B/R/I, because the remote provider derives them at query time and the
   local provider reproduces that derivation in normalization — in the consumer.
   That boundary is deliberate. [local-catalogs.md](local-catalogs.md) §3.4 notes
   that Gaia GSPC makes it cheaper to hold, by supplying *measured* Johnson
   magnitudes instead of derived ones.

### 3.3 Declarative catalog definition — the mechanism the forks dropped

Surviving only in upstream Afterglow (`default_cfg.py`), two config variables let
an operator extend the catalog set **without writing code**: `CATALOG_OPTIONS`
(per-catalog attribute overrides merged in `Catalog.__init__`) and
`CUSTOM_VIZIER_CATALOGS` (whole new catalogs as plain dicts, instantiated at
import time via `type(classname, (VizierCatalog,), kw)`).

The shipped, commented-out example adds NOMAD-1 — a 1.1-billion-row catalog — in
twelve lines of configuration. **That is the strongest available demonstration
that a VizieR catalog is data, not code.**

It is also a demonstration of what goes wrong when a declarative table has no
schema and no tests. **The shipped example does not work.** `mags` values are
consumed as `mag_col, mag_err_col = item[:2]`, which expects a sequence of column
names. Given the example's plain string `'Bmag'`, `item[:2]` is `'Bm'`, so
`mag_col = 'B'`; `row['B']` raises `KeyError`, the retry raises `KeyError`, and
the enclosing `except Exception: pass` drops the magnitude. With every band
dropped, the `if source.mags:` guard drops every *source*. **A NOMAD catalog
configured exactly as documented returns zero rows, silently.** The real plugins
all use lists; VSX's empty-string values survive only because `''[0]` raises
`IndexError` into a `continue`.

Both mechanisms were dropped in the skynet forks. The registry re-implements the
overlay half by passing `filter_lookup=` to each constructor, but there is no
longer any way to add a catalog without adding a module.

**The lesson: declarative yes, untyped no.** A schema with explicitly typed band
pairs would have made the broken example a startup error rather than a catalog
that silently returns nothing.

### 3.4 Machinery around the query — five warnings

**astroquery's cache had to be monkey-patched.** `vizier_catalogs.py` replaces
`astroquery.query.to_cache` and `astroquery.query.AstroQuery` at import time so a
caching failure under concurrency is swallowed and files older than
`VIZIER_CACHE_AGE_DAYS` (30) are pruned on write. Four bare
`except Exception: pass` blocks reaching into another package's internals.

**Cache hit rates required rounding the query.** `query_box`/`query_circ`
quantise the field centre to 10 arcsec, the radius to 0.2 arcmin, and clamp
declination, "to avoid cache misses for querying the same field with tiny
differences in RA/Dec and size". **That is a silent, position-dependent
difference in the returned set, not a rounding of a displayed number.** A cache
that returns a different answer than the uncached path is not a cache.

**The two lineages disagree about whether caching is even on — and the config
documents the opposite of what runs.** Afterglow defaults `VIZIER_CACHE = True`
and `VIZIER_SERVER = 'vizier.cfa.harvard.edu'`, read per-instance from
`current_app.config`. The skynet fork hardcodes `cache: bool = False` and
`vizier_server = None` as class attributes with no subclass overriding either,
which makes its `config.py` constants `VIZIER_SERVER = 'vizier.cds.unistra.fr'`
and `VIZIER_CACHE_ENABLED = True` **dead** — nothing imports them. In production
the fork caches nothing, the quantisation never executes, the monkey-patch is
inert, and the server is whatever astroquery defaults to. Anyone reading
`config.py` would conclude the opposite of what runs.

**Porting these plugins across a schema boundary failed silently — twice.** The
fork's `vsx_catalog.py` differs from Afterglow's by exactly two defensive
patches, both added after the fact, both for the same root cause: marshmallow's
`CatalogSource` became a pydantic model, so `id=row['OID']` (an int) raised a
validation error, and `setattr(source, 'G', …)` for an undeclared passband raised
too. Neither surfaced. The comment left behind says it plainly: the validation
error was one "which `_filter_variable_stars` swallows, silently disabling
variable-star filtering". **A whole safety feature was off, with no failure
visible anywhere.** Skycat's row shape is a third schema again.

**Constraints go over the wire as strings.** `query_region` splits `constraints`
into `column_filters` (with a value) and `keywords` (`None`). SkyMapper forces
`flags = '0'`. The VizieR filter syntax is its own small language (`>10`, `<=5`,
`!=0`, ranges) with no relationship to Skycat's validated `QualityFilter`.

### 3.5 How catalog selection works today

`select_catalogs_for_filter` / `catalog_supports_filter` keep only catalogs whose
`mags` keys — or whose `filter_lookup` expression has all dependencies present —
can resolve the image's FITS `FILTER`, preserving priority order; if nothing
matches, it logs a warning and falls back to the full list rather than failing.
`resolve_ref_mag_for_filter` then resolves: direct band → per-catalog lookup →
wildcard `*` → a preferred-band fallback chain.

Preselection is not the final truth: a catalog can pass stage 1 and lose every
source in stage 2. VSX declares ~40 bands and looks maximally filter-compatible
but is **not a calibration catalog** — it is used only in
`_filter_variable_stars()`, and letting it into a calibration list is a known
foot-gun.

This layer stays in the consumer. It is inventoried so nobody mistakes it for
something a Skycat remote surface is meant to replace.

### 3.6 The SIMBAD target resolver

`apps/public-api/public_api/services/target_search.py` (394 lines) resolves a
free-text identifier to a submittable target across five sources: SIMBAD, NORAD
satellites, major solar-system bodies, MPC comets and MPC orbits. The last four
are local database queries. **SIMBAD is the only remote one**, and the only
SIMBAD dependency in the system. It is mounted by both the `/web` SSR site and
`GET /v1/catalog-objects/search`.

```python
customSimbad = Simbad()
SIMBAD_ENABLED = True
try:
    customSimbad.add_votable_fields("otype")
except Exception:
    SIMBAD_ENABLED = False
```

The query is `customSimbad.query_object(name, wildcard=False)`; each row yields
`main_id`, `otype`, `ra`, `dec`, wrapped into a fixed position with a hardcoded
J2000 epoch. A 200-entry dict maps SIMBAD's machine `otype` to a human label.

**Seven observations, each a requirement on any replacement:**

1. **The singleton is constructed at import time, and a failure there disables
   SIMBAD permanently and silently.** `add_votable_fields("otype")` validates the
   field list against the service. If it fails once — CDS slow during a deploy,
   DNS not yet up in a fresh pod — `SIMBAD_ENABLED` is `False` for the life of
   the process and every subsequent search silently returns only local sources.
   Nothing logs it, nothing retries, nothing surfaces it in a health check.
2. **The lowercase column names pin astroquery ≥ 0.4.8** (§2.2). Correct today
   against the pinned `0.4.10`; breaks silently on a downgrade.
3. **`Angle(ra, unit=u.degree)` on a value already in float degrees** is a
   vestigial wrap from the pre-0.4.8 sexagesimal strings — and the only thing
   that would make a reversion crash loudly rather than quietly.
4. **The name is lowercased before being sent**, and the same lowercased string
   feeds four local `ilike` queries, coupling the two behaviours through one
   variable.
5. **`limit` does not bound the SIMBAD results.** The docstring says so
   explicitly. Every local branch applies `.limit(limit)`; the remote one does
   not.
6. **The epoch is hardcoded J2000** with no proper motion applied. SIMBAD returns
   ICRS coordinates at the catalog epoch; for a high-proper-motion star the
   difference is real and unattributed.
7. **Errors are `print()` inside `except Exception`** — five of them, one per
   source. A SIMBAD outage degrades to "no fixed-position results" with a line on
   stdout.

### 3.7 The direct VizieR APASS endpoint

`GET /v1/catalog-objects/vizier/apass` is a 60-line astroquery cone search
written directly into a FastAPI route, bypassing the plugin layer, the
`CatalogSource` shape and the local-first router. It is the clearest statement of
unmet demand in the system.

```python
Vizier.ROW_LIMIT = 1000
center_coord = coord.SkyCoord(ra=ra_deg, dec=dec_deg, unit=(u.deg, u.deg))
column_filters = {}
if query.b: column_filters["Bmag"] = query.b
...
tables = Vizier.query_region(center_coord, catalog="APASS9",
                             radius=radius_deg * u.deg, column_filters=column_filters)
```

| # | Defect | Consequence |
|---|---|---|
| 1 | `Vizier.ROW_LIMIT = 1000` mutates the **module-global** astroquery class attribute per request | Process-wide, shared across concurrent workers and anything else importing `Vizier` |
| 2 | `column_filters["Bmag"] = query.b` passes a **float** into VizieR's filter mini-language | A bare numeric value is an *equality* constraint there, not an upper limit. `?b=12.5` asks for `Bmag = 12.5` exactly and returns essentially nothing |
| 3 | `b`,`v`,`g`,`r` typed `float \| None`, but `i` typed `str \| None` | The one field typed as a string is the only one where `<13` works — almost certainly how defect 2 was worked around in practice |
| 4 | User values reach a remote query language unvalidated | Not a SQL injection, but the opposite of `QualityFilter`'s allow-listed, parameter-bound design |
| 5 | `ra_j2000 = row["RAJ2000"] / 15` | **A field named degrees holding hours**, in a public API response schema |
| 6 | `catalog="APASS9"` | VizieR's DR9 — a release Skycat does not have — through an endpoint named only "apass" |
| 7 | No timeout, retry or error mapping | A CDS stall becomes a hung worker in the public API pool |
| 8 | `ra_deg`, `dec_deg`, `radius_deg` all `float \| None` with no default | A parameterless call reaches `SkyCoord(ra=None, dec=None)` |

**Defect 2 is the one to weigh.** This endpoint has shipped magnitude filtering
that almost certainly returns empty results, and nothing surfaced it, because an
empty cone is a legitimate answer.

### 3.8 The local-first router — already built, downstream

The routing a remote surface might otherwise propose has already been built in
`skynet-db`, as a `LocalFirstCatalog` mixin in front of the VizieR providers:

```
provider.query_*  →  LocalFirstCatalog._route:
   remote_only / family-not-local ──────────────▶ remote (VizieR)
   untranslatable constraints ── fallback? ─▶ remote | no ─▶ raise
   local unavailable ─────────── fallback? ─▶ remote | no ─▶ raise
   local ok ─▶ normalize → CatalogSource        (served from PostGIS)
   local healthy-but-empty ─ fallback_on_empty? ─▶ remote else []
```

with modes `local_first` / `local_only` / `remote_only`, plus
`SKYCAT_REMOTE_FALLBACK`, `SKYCAT_FALLBACK_ON_EMPTY`, `SKYCAT_LOCAL_FAMILIES` and
per-family release pins.

**This is the right place for it**: it is where `CatalogSource`/`mags` — the
shape routing normalises into — actually lives. Moving it into Skycat would
invert the dependency. Skycat's job is to provide good *ends* to route between,
not the switch.

*Currency caveat:* that code is not in the current `skynet` working tree.
`catalogs/local/` holds only stale `__pycache__`; the sources live on unmerged
branches. Confirm before treating it as shipped (open item 1).

### 3.9 What the inventory establishes

1. **A VizieR catalog is data, not code** (§3.3). Twelve lines of configuration
   added a 1.1-billion-row catalog.
2. **Untyped declarative tables fail silently** — three independent instances of
   one signature: the NOMAD example returning zero rows, the swallowed VSX
   validation error disabling variable-star filtering, and the float-as-filter in
   §3.7. **The remote path fails by returning nothing, not by raising.** Any
   design has to assume this is the default outcome, not the unlucky one, and
   build verification accordingly.
3. **The plugin layer gets bypassed when it is hard to reuse** (§3.7). A
   supported, importable surface is what stops the next hand-rolled route.
4. **SIMBAD is unserved locally and its current implementation can disable itself
   permanently at import** (§3.6).
5. **Config that documents the opposite of what runs is worse than no config**
   (§3.4). Any setting a remote surface exposes should be read in exactly one
   place, with a test that fails if it stops being read.

---

## 4. Constraints any remote support must respect

Not solutions — the existing contracts and service realities a design has to
answer to.

### 4.1 Skycat's current contracts

- **The row shape is typed columns, not bands.** `cone_search` returns
  `dict(row)` over the release's real columns — `native_id, ra_deg, dec_deg,
  johnson_v_mag, …, extra, separation_deg`. No `mags` dict, no `ra_hours`, no
  `CatalogSource`. Column names carry units.
  [api-stability.md](../reference/api-stability.md) permits adding keys but not
  repurposing them.
- **Spatial work happens in the database, always.** Decision 0001.
  `separation_deg` comes from `ST_Distance(geom, point, false)`. A remote path
  necessarily computes that number somewhere else, which means two
  implementations of one quantity unless something verifies they agree.
- **Every answer is attributed to a release.** `resolve_release_for_query`
  returns a `ResolvedRelease` before any query runs, and `--release` exists so
  results are reproducible. **A remote row has no release**, and both available
  answers cost something: `None` breaks the attribution invariant, while a
  synthetic designation is honest about origin but is not a release the registry
  knows and `--release` cannot select it.
- **The dependency set is five libraries** — `sqlalchemy`, `geoalchemy2`,
  `psycopg[binary]`, `alembic`, `click`. No numpy, no astropy. Deliberate, for a
  package whose deployment target is a one-shot Kubernetes Job.
- **Exit codes are a contract.** `CatalogConfigError` → 2;
  `CatalogQueryError`/`IngestionError`/`ReleaseStateError` → 1, read by the ingest
  Job. A network failure class has to land in that taxonomy deliberately — and
  note that `except CatalogQueryError` in existing consumer code means "the
  database said no" and should not start catching "CDS is down".
- **Quality filters are allow-listed and parameter-bound.** `QualityFilter`
  validates the column against the table's real columns and the operator against
  six comparisons, and binds the value — "callers may therefore build these from
  untrusted input" (`skycat/query/cone.py:46`). Any remote equivalent has to earn
  that sentence or not claim it; §3.7 defect 4 is what not earning it looks like.
- **There is deliberately no plugin framework.** Adding a *local* family is six
  explicit touch points. Note that the reasons for that explicitness — a parser,
  a typed model, a migration, a validation module — do not exist for a remote
  catalog, which has no local schema to design. That asymmetry deserves an
  explicit decision rather than an assumption either way.

### 4.2 Release identity

| Family | Skycat has | VizieR serves |
|---|---|---|
| `apass` | DR6, **DR10** | **DR9** (`II/336/apass9`) — no overlap |
| `landolt` | **1992**, 2009 | 1992 ✓ (`II/183A`) |
| `stetson` | stetsonglobs | same ✓ |
| `vsx` | current | a mirror of a living index — no snapshot identity |

The APASS overlap is **empty**, on the largest and most-used family. Only
Landolt-1992 and Stetson are cleanly comparable, and both are small standards
catalogs. Any remote surface has to make it impossible to mistake VizieR's APASS
DR9 for a local DR10 release.

### 4.3 Query semantics that do not survive the trip

| Skycat local | Remote equivalent | Gap |
|---|---|---|
| `separation_deg` from `ST_Distance(…, false)` | not returned | Computed elsewhere; needs verification against the database's own answer |
| `order_by` ascending, NULLs last, numeric-only, validated | server-side `sort`, no null policy, no tiebreak | Different row *set* under a limit, not just a different order |
| `limit` applied after ordering, in SQL | `row_limit` applied by the server, unspecified interaction with `sort` | A capped remote cone and a capped local cone select different stars in a dense field |
| `QualityFilter` — allow-listed, bound, safe for untrusted input | `column_filters` string mini-language | Not all filters translate. **A filter that silently did not apply returns wrong rows** — the §3.9 signature |
| `batch_crossmatch` — `COPY` to TEMP, one LATERAL join, one round trip | no batch primitive | N HTTP requests. For a 5,000-source frame this is a different operation, not a slow one. It is also the operation the pipeline needs most at scale |
| 30 s statement timeout | client timeout + retries + DNS | The word means something different |

### 4.4 Schema impedance

Mechanical but lossy in both directions, and every loss is somewhere the legacy
code failed silently: `ra_hours` (÷15) versus `ra_deg`; `Vmag`/`e_Vmag` versus
`johnson_v_mag`/`johnson_v_err_mag`; `g'mag`→`g_mag` versus `sloan_g_mag`;
sentinel `99.99` versus `NULL`; sources dropped for having no magnitudes; `extra`
JSONB with no remote counterpart; `native_id` type differences (APASS `recno` is
an int, Skycat's is `String(64)`; VSX's `OID` needed an explicit `str()` after a
pydantic error silently disabled a safety feature).

### 4.5 Reproducibility has no remote counterpart

Skycat's central claim is that a release is rebuildable and provable: manifest or
content checksum, `source_size_bytes`, `source_modified_at`, `importer_version`,
`internal_schema_version`, a five-step chain of custody. A remote row has none of
it, and §3.4's cache quantisation means the *same* remote call can return
different rows depending on cache state. A consumer persisting a calibration
needs to be able to tell, from the data, whether it is reproducible — and §3.4
establishes that documenting it is not sufficient.

### 4.6 Dependency weight and lockfile conflict

astroquery 0.4.11 (current stable) declares `numpy>=1.20`, `astropy>=5.0`,
`requests>=2.19`, `beautifulsoup4>=4.8`, `html5lib>=0.999`, `keyring>=15.0`,
`pyvo>=1.5`. astropy transitively adds `pyerfa`, `PyYAML`, `packaging`; `keyring`
on Linux adds `SecretStorage`/`jeepney`. Five libraries would become roughly
twenty, including a numerical stack and a **desktop credential-store library
inside a headless Kubernetes Job**.

Second-order: Skycat was deliberately *not* made a hard dependency of `skynet-db`
because that "would invalidate every service's frozen `uv.lock`". `skynet` pins
`astroquery==0.4.10` in three requirements files and `skylib` declares
`~= 0.4.9`. A hard astroquery dependency in Skycat reproduces that conflict from
the other direction, in a repository that cannot resolve it unilaterally. An
optional extra is the obvious mitigation; note that it makes "does this
capability exist" deployment-dependent, which is its own ambiguity.

**astroquery ≥ 0.4.8 is a hard floor** for the SIMBAD TAP column names the
production code already depends on (§2.2, §3.6).

### 4.7 Operational surface

- **Network egress from a Kubernetes Job** is currently unnecessary.
  NetworkPolicy, proxies and DNS become Skycat's problem for any deployment that
  enables remote access.
- **Third-party availability becomes the caller's availability.** CDS has no SLA
  for programmatic use; NED explicitly limits high query rates; ADS caps at 5,000
  requests/day per token.
- **Caching.** astroquery caches to `astropy` config directories, which are
  user-scoped rather than container-scoped, and which the legacy code had to
  prune by hand. On a read-only container filesystem this needs explicit
  handling. And §3.4's quantisation must not come back.
- **Mirror choice is a data-provenance axis**, not just a routing detail (§2.1).
- **Credentials.** ADS would be the first third-party API token Skycat has ever
  held (§2.4). Today the only secrets are `SKYCAT_DB_*` passwords with a
  documented rotation path in the runbook.
- **A new failure taxonomy** — timeout, HTTP 5xx, rate-limited, malformed
  response, DNS — each of which has to map onto the existing exit-code contract.

### 4.8 Code-quality gates

Ported as-is, the legacy code does not pass this repository's CI:

| Legacy pattern | Gate |
|---|---|
| `except Exception: pass` ×3 in the astroquery monkey-patch | ruff `BLE001`, `S110` — errors, not style nits |
| `except Exception: val = None` in `table_to_sources` | `BLE001` |
| `eval(expr, ctx, {})` per row | No rule forbids it; it is the opposite of `_quality_clause`'s allow-listed, bound approach |
| Untyped `mags: Dict[str, List[str]]` with `item[:2]` / `item[0]` fallbacks | pyright null-safety rules are errors and are at zero |
| `type(classname, (VizierCatalog,), kw)` from unvalidated dicts | pyright cannot see the resulting classes at all |

"Port the plugins" is therefore a rewrite, and the rewrite would have to
re-derive the intent of expressions like the 2MASS `(J−K)` cubics from papers
cited only by a URL in a comment. A further argument for leaving the
transformation tables in the consumer.

### 4.9 Test hermeticity

Network tests fail when a service is slow, are the first thing disabled when CI
gets flaky, and once disabled stop catching anything. The workable pattern is
recorded response fixtures for deterministic assertions plus a separately-marked
live test — mirroring how `postgis`-marked tests already work, including the
`--require-postgis` escalation that turns silent skipping into a hard failure.

Given §3.9, one rule matters more than the rest: **an empty result is a
suspicious result.** Fixtures should assert non-zero row counts where they are
expected, and a filter test should assert the filter changed the count in the
direction claimed. That single assertion would have caught §3.7 defect 2.

There is also a verification opportunity worth naming. Skycat's Landolt and
Stetson parsers claim to reproduce the remote provider's coordinate arithmetic
exactly (§3.2). Today that is a docstring. Comparing a local release against the
VizieR catalog it mirrors would make it a test — and each catalog
[local-catalogs.md](local-catalogs.md) adds locally creates another such pair.
Tycho-2 in particular becomes comparable once mirrored.

### 4.10 Configuration namespace collision

`skynet-db`'s routing layer already uses `SKYCAT_BACKEND`,
`SKYCAT_REMOTE_FALLBACK`, `SKYCAT_FALLBACK_ON_EMPTY`, `SKYCAT_LOCAL_FAMILIES` and
`SKYCAT_<NAME>_RELEASE` — the *consumer's* routing config, living in Skycat's
namespace but not read by Skycat, whose own configuration is `SKYCAT_DB_*` plus
`SKYCAT_DATA_ROOT`/`SKYCAT_WORK_ROOT`. Note that `SKYCAT_REMOTE_FALLBACK` is
already taken, by a thing Skycat must never implement. Any new variables need to
be distinguishable, and the README's configuration table needs to say which
`SKYCAT_*` variables Skycat reads and which belong to the pipeline.

---

## 5. Verdicts

| Service | Verdict | Reasoning |
|---|---|---|
| **SIMBAD** | ✅ **Support — highest value** | Name resolution has no local substitute and cannot acquire one; the surface is small and free of release, band-mapping and schema-impedance problems; and the production implementation has seven identified defects a supported one would fix by construction |
| **VizieR** | ✅ Support | The only service returning catalog rows, with an evidence-backed set of five deferred catalogs plus a ~50,000-catalog long tail. Carries the most constraints (§4.2–§4.5) |
| **NED** | ⚠️ Conditional | Genuinely valuable for extragalactic work — 8.99 M objects with redshifts — but needs a stated consumer requirement, and the `astroquery.ipac.ned` endpoint question (open item 2) must be answered first |
| **ADS** | ❌ Not as a catalog surface | Returns papers, not sources. A narrow provenance-lint role exists but is optional, and it would introduce Skycat's first third-party API credential for a check VizieR metadata may do without one |

Two sequencing observations that fall out of the survey rather than from a plan:

- **The local track should go first**, because "cannot be mirrored" is a
  measurement and [local-catalogs.md](local-catalogs.md) makes it. Building the
  remote surface first would bias that measurement: once a catalog is reachable
  remotely, the pressure to size and mirror it drops to zero, and it stays remote
  by default rather than by decision. Tycho-2 is the concrete case — it is under
  a gigabyte locally, so it should never become a remote catalog.
- **SIMBAD support does not depend on any of that.** It is orthogonal to the
  mirroring question, useful whatever gets mirrored, and the smallest honest
  surface to build first.

---

## 6. Open questions

1. **Branch state of the downstream routing layer.** `catalogs/local/` in
   `skynet` holds only stale bytecode on `pipeline/analysis-descriptive-split`;
   the sources live on unmerged branches. Confirm before treating §3.8's router
   as shipped, and confirm whether `SKYCAT_LOCAL_FAMILIES` in the Compose worker
   is still pinned to `APASS,VSX`.
2. **Which NED endpoint does `astroquery.ipac.ned` actually call?** If it is the
   legacy CGI interface, it is already blind to anything NED ingested after
   January 2026 — silently. Answer before designing anything around it.
3. **Does VizieR TAP offer upload joins?** `TAPVizieR` via `pyvo`. It is the only
   thing that could change the §4.3 crossmatch verdict.
4. **Which VizieR server does production actually reach?** `config.py` says
   `vizier.cds.unistra.fr`, nothing reads it, and `vizier_server = None` (§3.4);
   Afterglow's default is `vizier.cfa.harvard.edu`. Mirrors can hold different
   snapshots.
5. **Can VizieR's own metadata calls do ADS's provenance job?**
   `find_catalogs()` / `get_catalog_metadata()` need no credential. If they can
   verify a designation has not been superseded, ADS has no role at all.
6. **Does the pipeline do extragalactic work needing redshifts or host-galaxy
   data?** Determines whether NED is in scope, and pairs with
   [local-catalogs.md](local-catalogs.md) open item 9 on NED-LVS.
7. **Landolt 2009 designation.** `J/AJ/137/4186` is inferred from
   `catalog_defs.py`'s description; the legacy plugin only knows `II/183A`.
   Affects how many comparable local/remote pairs exist for verification.
8. **astroquery version floor and ceiling.** `>= 0.4.8` is required for the TAP
   SIMBAD columns. `skynet` pins `==0.4.10` in three requirements files and
   `skylib` declares `~= 0.4.9`; current stable is 0.4.11. Confirm no conflict
   where both are installed.
9. **Is `CUSTOM_VIZIER_CATALOGS` populated in any deployment?** Afterglow ships it
   defaulting to `[]` and the forks dropped it. If a deployment populates it,
   there are catalog definitions in operator config that exist in no repository —
   and per §3.3, any written in the documented shape are silently returning
   nothing.
10. **Does anything besides the two public-api routes call SIMBAD?** §3.6 found
    one integration by grep.
11. **Does the deferred set hold?** It derives from
    [local-catalogs.md](local-catalogs.md)'s sizing, which is modelled rather
    than measured. If measured bytes-per-row comes in far above the model,
    catalogs move from the local plan into this one.

---

## 7. References

**Companion** — [local-catalogs.md](local-catalogs.md): the mirrorable sources,
sized against 4 TB, with download channels. The deferred set in §2.1 is defined
there.

**Skycat** — `skycat/client.py`, `skycat/query/cone.py`,
`skycat/query/crossmatch.py`, `skycat/registry/catalog_defs.py`,
`skycat/models/{apass,landolt,stetson,vsx}.py`, `skycat/cli/main.py`,
`pyproject.toml`; [decisions/0001](../decisions/0001-postgresql-postgis-only.md),
[reference/architecture.md](../reference/architecture.md),
[reference/api-stability.md](../reference/api-stability.md),
[guides/provenance.md](../guides/provenance.md),
[operations/runbook.md](../operations/runbook.md).

**Skynet** (`pipeline/analysis-descriptive-split` @ `0040c28ab`) —
`packages/py/skynet-db/skynet_db/runners/observation_asset_processing/optical_data_processing/catalogs/`
(`vizier_catalogs.py`, `catalog.py`, `config.py`, `__init__.py`, ten provider
modules); `runners/common/catalog_plugins/`;
`apps/afterglow/docs/legacy/resources/catalog_plugins/`;
**`apps/public-api/public_api/services/target_search.py`** (SIMBAD);
**`apps/public-api/public_api/routers/catalog_objects.py`** and
`apps/public-api/public_api/schemas/catalog_objects.py` (the direct VizieR APASS
route); `runners/utils.py`; the three `requirements.txt` pinning
`astroquery==0.4.10`; `packages/py/skylib/pyproject.toml` (`astroquery ~= 0.4.9`).

**Afterglow Core** (`master` @ `92aaf61`) —
`afterglow_core/resources/catalog_plugins/vizier_catalogs.py` (the
`CUSTOM_VIZIER_CATALOGS` class factory), `afterglow_core/models/catalogs.py`,
`afterglow_core/default_cfg.py` lines 110–145.

**Vault** — `wiki/resources/entities/astroquery-VizieR.md`,
`wiki/resources/concepts/{Local-First Catalog Architecture,Catalog Selection and Provider Registry,Catalog Release Provenance}.md`,
`wiki/resources/incoming/Astroquery VizieR Local Catalog Investigation.md`.

**Services**

- [VizieR](https://vizier.cds.unistra.fr/);
  [astroquery VizieR docs](https://astroquery.readthedocs.io/en/latest/vizier/vizier.html);
  [VizieR II/336 (APASS DR9)](https://cdsarc.cds.unistra.fr/viz-bin/cat/II/336);
  [AAVSO APASS DR10 VizieR schedule](https://www.aavso.org/apass-dr10-vizier-schedule).
- [SIMBAD](https://simbad.u-strasbg.fr/simbad/) (counts as of 2026-08-09);
  [SIMBAD mirror at CfA](https://simbad.cfa.harvard.edu/simbad/);
  [astroquery SIMBAD docs](https://astroquery.readthedocs.io/en/stable/simbad/simbad.html);
  [SIMBAD module evolutions — the 0.4.8 TAP rewrite](https://astroquery.readthedocs.io/en/stable/simbad/simbad_evolution.html);
  [CDS: a new access to SIMBAD TAP in astroquery](https://cds.unistra.fr/news/2024/04/05-access-simbad-tap-from-astroquery/).
- [NED](https://ned.ipac.caltech.edu/);
  [About NED — database contents](https://ned.ipac.caltech.edu/Documents/Guides/Database);
  [astroquery NED docs](https://astroquery.readthedocs.io/en/latest/ipac/ned/ned.html).
- [ADS API rate-limit policy](https://ui.adsabs.harvard.edu/help/policies/rate-limits);
  [ADS terms of use](https://ui.adsabs.harvard.edu/help/terms/);
  [astroquery NASA ADS docs](https://astroquery.readthedocs.io/en/stable/nasa_ads/nasa_ads.html).
- [astroquery on PyPI](https://pypi.org/project/astroquery/) — 0.4.11 current.

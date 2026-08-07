---
status: open
reviewed: 2026-08-07
branch: docs/catalog-coverage-split
authority: code-inspection (skycat @ 89d45a7) + upstream distribution docs (CDS, IRSA, MAST, ESA, AAVSO)
implementation: not-started
---

# Local catalog coverage — what to mirror into Skycat, and how

Companion to [remote-catalogs.md](remote-catalogs.md), which covers the adjacent
`RemoteCatalogReader`. This note is the one that comes first: **a mirrored
catalog beats a remote one on every axis Skycat cares about** — reproducibility,
release attribution, latency, `batch_crossmatch`, availability, and the failure
mode being *cannot connect* rather than *returned nothing*. Remote access is what
you do when the good option is unaffordable, so the first job is finding out what
is actually unaffordable.

With **4 TB** on the catalog server, the answer is: less than you would guess is
out of reach, and the boundary is sharp.

## Verdict

| | Catalog | Rows | On disk | Why | Do it |
|---|---|---|---|---|---|
| **L1** | **Tycho-2** | 2.5 M | **~0.9 GB** | The bright end (V < 11.5) that APASS saturates through. Costs nothing | **Yes, now** |
| **L2** | **ATLAS RefCat2** (m<19) | 991 M | **~595 GB** | All-sky griz + JHK, homogenised, purpose-built as a photometric reference. The anchor of the whole plan | **Yes** |
| **L3** | **Gaia DR3 GSPC** | ~220 M | **~155 GB** | *Standardised* synthetic Johnson-Kron-Cousins UBVRI and SDSS ugriz. Replaces transformation coefficients with measurements | **Yes** |
| **L4** | **2MASS PSC** | 471 M | **~190 GB** | The only all-sky JHK source in full, with its quality flags | Yes, lower priority |
| **L5** | SkyMapper DR2 | 285 M | ~170 GB | Native southern uvgriz | Only if the south needs native photometry |
| — | UCAC5 | 108 M | ~45 GB | Superseded by Gaia for astrometry, by RefCat2 for photometry | Skip |
| — | PanSTARRS DR2 (full) | 1.92 B | ~1.15 TB | **RefCat2 already carries PS1-derived griz, all-sky, to m<19** | **Defer — remote** |
| — | Gaia DR3 main | 1.81 B | ~1.10 TB | GSPC is the photometrically useful subset | **Defer — remote** |
| — | NOMAD | 1.12 B | ~450 GB | A *compilation* of Tycho-2, UCAC, USNO-B, 2MASS — mirror components, not merges | **Defer — remote** |
| — | USNO-B1.0 | 1.05 B | ~420 GB | Photographic-plate photometry; RefCat2 is better for every calibration use | **Defer — remote** |
| — | SDSS DR17 | ~1.2 B | — | A SQL-region service with a survey footprint, not a bulk mirror | **Defer — remote** |

**Recommended set: L1–L4 ≈ 1.05 TB steady state, ≈ 2.1 TB at peak** during the
largest rebuild (§1.2). That leaves roughly 1.9 TB of headroom in a 4 TB budget —
enough to add L5 and still not be tight.

**Deferred set ≈ 3.1 TB**, which is more than the entire budget. That is the
honest justification for building `RemoteCatalogReader` at all, and it is now
backed by arithmetic rather than assumption.

The single most important finding: **ATLAS RefCat2 makes mirroring PanSTARRS
unnecessary.** RefCat2 is built *from* Pan-STARRS DR1, ATLAS Pathfinder,
ATLAS-reflattened APASS, SkyMapper DR1, APASS DR9, Tycho-2 and the Yale Bright
Star Catalog, with Gaia DR2 astrometry, homogenised into one all-sky griz
catalogue complete to m < 19. For the pipeline's actual job — finding reference
stars with reliable magnitudes near an arbitrary field — it is a *better* product
than any of its inputs, at half the disk of full PanSTARRS.

---

## 1. The budget

### 1.1 What a row costs

Derived from the shipped schema, not measured. `ApassSource`
(`skycat/models/apass.py`) is the wide case: 28 attributes, sixteen `Double`
magnitude/error columns, a `JSONB extra`, and the generated
`geography(Point,4326)`. Catalog tables do **not** carry `TimestampMixin`, so
there is no per-row `created_at`/`updated_at`.

| Component | Wide (APASS-shaped) | Narrow (Tycho-2-shaped) |
|---|---|---|
| Tuple header + null bitmap, MAXALIGNed | 32 | 32 |
| `release_id` int4 + `id` int8, with padding | 16 | 16 |
| `native_id` varchar | 10 | 14 |
| Coordinates + errors, 4 × float8 | 32 | 32 |
| Photometry, 2 × *n* bands × float8 | 128 (8 bands) | 32 (2 bands) |
| Other typed columns (counts, proper motions, flags) | 8 | 24 |
| `geom` geography(Point,4326), MAXALIGNed | 32 | 32 |
| `extra` JSONB | ~150 | 0 (NULL) |
| Item pointer + page slack (~8 %) | ~45 | ~15 |
| **Heap subtotal** | **~455 B** | **~200 B** |
| `PRIMARY KEY (release_id, id)` btree | ~28 | ~28 |
| `native_id` btree | ~36 | ~40 |
| GiST on `geom` | ~55 | ~55 |
| **Index subtotal** | **~120 B** | **~125 B** |
| **Total** | **≈ 575 B/row** | **≈ 325 B/row** |

Planning constants used throughout this note: **600 B/row wide, 400 B/row
mid, 350 B/row narrow.** These are ±30 % until L0 calibrates them against the
real DR10 partition with `skycat sizes`. `extra` size and `native_id` length are
the two terms that move most, and both are per-catalog. Every GB figure here is
an order-of-magnitude sort key, not a procurement number.

### 1.2 Peak disk is not steady-state disk

This falls straight out of the ingestion design and is the easiest planning
mistake to make. Phase A streams rows through unlogged staging. Phase B1 builds a
**detached standalone table** — a full second copy of the release with its own PK
and replicated indexes — while the old partition keeps serving reads. Only Phase
B2 swaps them. So during a `--replace` of the largest release, the database holds
the old partition, the new standalone table, *and* the staging copy at once:

> **Budget ≈ steady state + ~1.75 × the largest single release.**
>
> (1.0 for the standalone build, ~0.75 for unlogged staging, which carries the
> heap but no indexes.)

This is a property worth preserving, not engineering away: it is exactly what
lets a `--replace` of the ACTIVE release stay ACTIVE throughout. It does mean
the largest catalog you mirror sets your headroom requirement, which is a second
argument against the 1-TB-class candidates: full PanSTARRS would need ~2 TB of
free space on top of itself every time it was rebuilt.

### 1.3 What is stored today

| Family | Release | Rows | Shape | Est. |
|---|---|---|---|---|
| `apass` | DR6 | 42.6 M | wide (u/z/Y NULL) | ~23 GB |
| `apass` | **DR10** | 128.6 M | wide | **~77 GB** |
| `vsx` | current | 10.3 M | mid | ~5 GB |
| `stetson` | stetsonglobs | 4.9 M | mid | ~2 GB |
| `landolt` | 1992 + 2009 | 1.1 k | narrow | <1 MB |
| | | | **steady state** | **≈ 107 GB** |
| | | | **peak during a DR10 replace** | **≈ 240 GB** |

### 1.4 The 4 TB envelope

| Scenario | Steady | Largest release | Peak | Fits 4 TB? |
|---|---|---|---|---|
| Today | 107 GB | 77 GB | 240 GB | ✅ (6 %) |
| + L1–L3 | 858 GB | 595 GB | 1.90 TB | ✅ (47 %) |
| **+ L1–L4 (recommended)** | **1.05 TB** | **595 GB** | **2.09 TB** | **✅ (52 %)** |
| + L5 SkyMapper | 1.22 TB | 595 GB | 2.26 TB | ✅ (57 %) |
| + UCAC5, USNO-B1.0 | 1.68 TB | 595 GB | 2.72 TB | ✅ (68 %) |
| + full PanSTARRS DR2 | 2.83 TB | 1.15 TB | **4.84 TB** | ❌ |
| + full Gaia DR3 main | 2.78 TB | 1.10 TB | **4.71 TB** | ❌ |

The boundary is clean and it is set by the *rebuild* rule, not by the catalog
size: either 1-TB-class catalogue fits on the disk and neither can be rebuilt on
it. A one-shot import that is never replaced would technically fit, but a release
you cannot re-import is a release you cannot fix, which defeats the point of the
release model.

**So: everything except the billion-row full surveys, and there is room to
spare.** The deferred set is defined by a measurement, not by taste.

---

## 2. The catalogs

For each: what it is, what it gives the pipeline that nothing else does, exactly
where to get it, and what it costs.

All CDS catalogues follow the pattern already documented in
[guides/provenance.md](../guides/provenance.md):

```
https://cdsarc.cds.unistra.fr/ftp/<designation>/
```

with `--timestamping` to preserve upstream mtimes (the default checksum mode
reads them) and `--cut-dirs` equal to the number of path components after the
host. That guide is the authority for the mirroring mechanics; this section adds
the per-catalog specifics it does not cover.

### 2.1 Tycho-2 — the bright end (L1)

**What it adds.** APASS saturates around V ≈ 10. Tycho-2 covers V < 11.5 with
BT/VT photometry and proper motions, so it answers the one question the current
store cannot answer at all: *give me reference stars for a bright field.* At 0.9
GB there is no counter-argument.

| | |
|---|---|
| Designation | `I/259` |
| Rows | 2,539,913 (main catalogue) |
| Download | `https://cdsarc.cds.unistra.fr/ftp/I/259/` |
| Data files | `tyc2.dat` (CDS serves large `.dat` files as gzipped chunks — confirm the layout at mirror time, see open item 4), `suppl_1.dat`, `suppl_2.dat` |
| Aux files | `ReadMe`, `index.dat`, `guide.pdf`, `guide.ps` |
| Format | Fixed-width ASCII, ReadMe-specified byte columns |
| `source_subdir` | `Tycho2/` |
| Est. on disk | **~0.9 GB** |

`suppl_1.dat` (Hipparcos/Tycho-1 stars absent from the main catalogue) is
arguably data rather than aux; decide during L1 and record the choice, because
`data_globs` membership changes the checksum and the row count.

**Parser shape.** Fixed-width, closest to the existing `landolt.py` /
`stetson.py`. `native_id` is the `TYC1-TYC2-TYC3` designation as text
(e.g. `1-13-1`), per
[guides/add-family.md](../guides/add-family.md) — which uses Tycho-2 as its
worked example throughout, so L1 is largely a matter of following a guide that
already exists.

### 2.2 ATLAS RefCat2 — the anchor (L2)

**What it adds.** This is the most valuable single item in the plan. RefCat2 is
991 million stars from −1.5 < m ≲ 19 with griz photometry, **virtually complete
to m < 19 over the entire sky**, plus J/H/K, proper motions and parallaxes.
Astrometry is Gaia DR2. Photometry is homogenised from Pan-STARRS DR1, the ATLAS
Pathfinder project, ATLAS-reflattened APASS, SkyMapper DR1, APASS DR9, Tycho-2
and the Yale Bright Star Catalog.

Read that input list again: it is most of the rest of this document's candidate
set, already cross-calibrated onto one system by people who did it carefully. For
the pipeline's actual job it is a better product than any of its inputs, and it
is the reason PanSTARRS and SkyMapper drop down the priority list.

| | |
|---|---|
| Designation | MAST HLSP; VizieR `J/ApJ/867/105` |
| Rows | 991 M (m < 19); ~1.05 B including the 19–20 bin |
| Download | `https://archive.stsci.edu/hlsp/atlas-refcat2` → files under `https://archive.stsci.edu/hlsps/atlas-refcat2/` |
| Format A | Original scaled-integer, **split by magnitude** (see below), ~47 GB total compressed |
| Format B | MAST CSV in astronomical units, five declination zones, ~17–19 GB each, ~91 GB total, `hlsp_atlas-refcat2_atlas_ccd_<lo>-<hi>_multi_v1_cat.csv.gz` |
| Columns | RA/Dec, PM, parallax, g/r/i/z + errors, J/H/K, astrometric quality metrics, bitmasks naming the contributing survey per band |
| `source_subdir` | `ATLAS-RefCat2-m19/` |
| Est. on disk | **~595 GB** at m < 19 |

**The magnitude split is the important property.** The original format ships as
five tarballs:

| File | Size | Cumulative on disk in Skycat |
|---|---|---|
| `hlsp_atlas-refcat2_atlas_ccd_00-m-16_multi_v1_cat.tbz` | 5.9 GB | ~78 GB |
| `hlsp_atlas-refcat2_atlas_ccd_16-m-17_multi_v1_cat.tbz` | 5.6 GB | ~153 GB |
| `hlsp_atlas-refcat2_atlas_ccd_17-m-18_multi_v1_cat.tbz` | 9.8 GB | ~285 GB |
| `hlsp_atlas-refcat2_atlas_ccd_18-m-19_multi_v1_cat.tbz` | 17 GB | ~595 GB |
| `hlsp_atlas-refcat2_atlas_ccd_19-m-20_multi_v1_cat.tbz` | 8.7 GB | ~712 GB |

This solves the magnitude-cut honesty problem (§4.4) for free: **the cut is a
mirroring decision, not an import filter.** A release named `v1-m18` is the three
tarballs you downloaded; there is nothing to filter, nothing to validate as
filtered, and `skycat discover` shows exactly which files are present. Contrast
PanSTARRS, where any cut is a predicate applied during import that someone has to
remember, document and enforce.

Recommendation: **start at m < 18** (~285 GB, three tarballs, and deeper than any
Skynet frame needs a reference star to be), and extend to m < 19 as a second
release if a use case appears. The release model makes that a partition swap, not
a re-architecture.

**Parser shape.** The MAST CSV format (B) is the lower-risk choice for a first
implementation — plain CSV, astronomical units, no integer descaling. The
original format (A) is smaller to mirror but stores scaled integers that the
parser must divide. Pick one in L2 and record why; do not support both.

### 2.3 Gaia DR3 synthetic photometry (GSPC) — standardised Johnson (L3)

**What it adds.** The one thing nothing else in this list provides:
*standardised* synthetic magnitudes in Johnson-Kron-Cousins **U, B, V, R, I** and
SDSS **u, g, r, i, z** (plus PanSTARRS1 y and HST ACS/WFC F606W/F814W), computed
from Gaia BP/RP low-resolution spectra and calibrated against external standard
stars. Typical accuracy for reproducing Hipparcos Hp/BT/VT photometry is quoted
at better than 2.5 mmag all-sky.

This is the direct answer to the photometric transformation tables inventoried in
[remote-catalogs.md](remote-catalogs.md) §2.2 — the Lupton, Jester, Jordi and
Tonry coefficients the legacy providers use to synthesise Johnson bands from
Sloan ones. A pipeline calibrating in Johnson V against a *measured* synthetic V
does not need coefficients at all. That is a science-quality improvement, not
just a storage decision, and it is absent from the entire legacy provider list
purely because it postdates it.

| | |
|---|---|
| Table | `gaiadr3.synthetic_photometry_gspc` |
| Rows | ~220 M sources with XP spectra, **G < 17.65** |
| Archive | ESA Gaia Archive, `https://gea.esac.esa.int/archive/` |
| Bulk | The Gaia archive publishes bulk CSV under `cdn.gea.esac.esa.int`; the exact path for this table is **unverified** — open item 5. ADQL export from the archive is the fallback and is workable at 220 M rows with pagination |
| Format | CSV / ECSV / VOTable depending on the access route |
| `source_subdir` | `Gaia-DR3-GSPC/` |
| Est. on disk | **~155 GB** |

**Design decision this phase carries.** GSPC publishes many passbands. Store the
ones the pipeline actually resolves filters to as typed columns — the Johnson
UBVRI and Sloan ugriz sets are the obvious keep — and put the rest in `extra`.
Resist a column per band "for completeness": at 2.2 × 10⁸ rows every unused
`Double` is ~1.8 GB, and a magnitude plus its error is ~3.5 GB.

The G < 17.65 limit is inherent to the catalogue, not a cut Skycat applies, so
§4.4 does not bite — but it must be stated in the family description, because a
user coning at G = 19 will correctly get nothing.

### 2.4 2MASS Point Source Catalog — the NIR (L4)

**What it adds.** The only all-sky JHK source in full, with its native quality
flags (`ph_qual`, `rd_flg`, `cc_flg`, `gal_contam`). RefCat2 carries J/H/K, but
only for sources in its griz selection and without the flag detail. If the
pipeline does infrared work, or wants the `(J−K)` inputs the legacy 2MASS
transformation cubics consume, this is the source.

| | |
|---|---|
| Designation | `II/246` (VizieR); IRSA is the bulk channel |
| Rows | 470,992,970 |
| Download | `https://irsa.ipac.caltech.edu/2MASS/download/allsky/` |
| Data files | `psc_aaa.gz` … `psc_ace.gz` (Dec < 0°), `psc_baa.gz` … `psc_bbi.gz` (Dec > 0°) |
| Volume | **~43 GB gzipped** |
| Format | Pipe-delimited ASCII, explicitly "formatted in a manner consistent with convenient loading into a database server" — i.e. it is already `COPY`-shaped |
| Column spec | `https://irsa.ipac.caltech.edu/2MASS/download/allsky/format_psc.html` |
| `source_subdir` | `2MASS-PSC/` |
| Est. on disk | **~190 GB** |

Use IRSA, not the VizieR mirror: IRSA is the authoritative distribution, the file
set is designed for bulk loading, and 43 GB compressed is a manageable mirror.

### 2.5 SkyMapper — native southern photometry (L5)

**What it adds.** Native southern-hemisphere u, v, g, r, i, z. Whether this is
needed depends on whether RefCat2's all-sky griz is sufficient in the south —
RefCat2 incorporates SkyMapper DR1, so for griz it largely is. SkyMapper's
distinctive contribution is the **u and v** bands, which nothing else here
provides.

| | |
|---|---|
| Designation | `II/358/smss` (DR2); newer releases exist — confirm the current designation before mirroring |
| Rows | 285,159,194 (DR2) |
| Download | `https://cdsarc.cds.unistra.fr/ftp/II/358/`, or the SkyMapper archive at ANU |
| Est. on disk | **~170 GB** |

Do this only if a consumer names u/v photometry or demonstrates a southern gap
RefCat2 does not cover.

### 2.6 UCAC5 — skip

108 M rows, ~45 GB, and it is superseded: Gaia DR3 is better astrometry and
RefCat2 is better photometry. `I/340` if someone insists.

### 2.7 The deferred set

Each of these goes to `RemoteCatalogReader` (Tier D in
[remote-catalogs.md](remote-catalogs.md)), with a reason that is now a
measurement:

| Catalog | Est. | Why deferred |
|---|---|---|
| **PanSTARRS DR2** `II/349` | ~1.15 TB | Cannot be rebuilt inside 4 TB (§1.4). And RefCat2 already carries PS1-derived griz all-sky to m < 19, homogenised — mirroring PanSTARRS would be paying 1.15 TB for the un-homogenised version of data we already have |
| **Gaia DR3 main** `I/355/gaiadr3` | ~1.10 TB | Same rebuild constraint. GSPC is the photometrically useful subset and costs 155 GB |
| **NOMAD** `I/297` | ~450 GB | A compilation of Tycho-2, UCAC, USNO-B and 2MASS. Mirror the components, never the merge — a merged catalogue inherits every input's errors with none of their provenance |
| **USNO-B1.0** `I/284` | ~420 GB | Photographic-plate photometry. It fits, but RefCat2 is better for every calibration use, and 420 GB of data the pipeline should prefer not to use is a poor trade |
| **SDSS DR17** | — | A SQL-region service with a survey footprint, not a bulk VizieR mirror. Structurally not a mirroring candidate |

Note that USNO-B1.0 and NOMAD *fit* the budget. They are deferred because they
are not worth the disk, which is a different and more defensible reason than "too
big" — and it is the kind of judgement that only becomes possible once the
arithmetic is done.

---

## 3. The recommended set, costed

| Step | Catalog | Rows | Adds | Steady | Peak | % of 4 TB |
|---|---|---|---|---|---|---|
| — | *(today)* | 186 M | — | 107 GB | 240 GB | 6 % |
| L1 | Tycho-2 | 2.5 M | 0.9 GB | 108 GB | 243 GB | 6 % |
| L2 | ATLAS RefCat2 m<18 | 475 M | 285 GB | 393 GB | 892 GB | 22 % |
| L2′ | *(extend to m<19)* | +516 M | +310 GB | 703 GB | 1.74 TB | 44 % |
| L3 | Gaia DR3 GSPC | 220 M | 155 GB | 858 GB | 1.90 TB | 47 % |
| L4 | 2MASS PSC | 471 M | 190 GB | **1.05 TB** | **2.09 TB** | **52 %** |
| L5 | SkyMapper DR2 | 285 M | 170 GB | 1.22 TB | 2.26 TB | 57 % |

Peak assumes the largest single release is being rebuilt (§1.2) — RefCat2 m<19
from L2′ onward.

Two observations worth carrying into the plan:

1. **Stopping RefCat2 at m < 18 halves its cost** (285 GB vs 595 GB) and keeps
   peak under 1 TB through L4. Since Skynet frames rarely need an m = 19
   reference star, this is very likely the right default, and extending later is
   a partition swap.
2. **Even the full recommended set uses just over half the budget.** The
   constraint is not tight; the plan is limited by implementation effort, not
   disk. That reverses the usual assumption and it should be stated in L0's
   findings.

---

## 4. How a catalog becomes a Skycat family

### 4.1 The six touch points

[guides/add-family.md](../guides/add-family.md) is the authority and walks
through all six against Tycho-2 specifically. In brief:

| # | File | What it does |
|---|---|---|
| 1 | `skycat/registry/catalog_defs.py` | `FamilyDef` + `ReleaseDef`: slug, source subdir, data/aux globs, parser key, `approx_row_count`, `reference_url` |
| 2 | `skycat/models/<family>.py` | The typed table. `LIST (release_id)` partitioned parent, explicit `native_id`/`ra_deg`/`dec_deg`, `geom_column()`, `extra` JSONB |
| 3 | `skycat/ingestion/parsers/<family>.py` | Streaming `iter_rows(paths, stats)` yielding one tuple at a time |
| 4 | `skycat/migrations/versions/000N_<family>.py` | Parent table + the three indexes (GiST on `geom`, btree on `native_id`, PK) |
| 5 | `skycat/validation/<family>.py` | Family checks *(optional)* |
| 6 | Tests, fixtures, README table | Including `tests/test_docs.py` coverage |

Two rules the guide states that matter more at scale: per-release oddities go in
`extra` JSONB rather than new columns, and `importer_available=False` is how a
family that is *declared* but not yet *ingestible* fails fast rather than
half-importing — useful for landing touch points 1–2 before 3.

### 4.2 What changes at 10⁸–10⁹ rows

The existing families top out at 128 M rows (APASS DR10). RefCat2 at m<19 is
nearly eight times that. Things that are free today and are not free there:

- **Parsers must genuinely stream.** `iter_rows` yields one tuple at a time
  across files and never materialises. This is already the convention
  (`ParseStats` counts malformed lines and retains the first 20), but at 10⁹ rows
  a single accidental `list()` is fatal rather than slow.
- **Column count is a first-class cost.** Every `Double` is 8 B/row: at 991 M
  rows that is 7.9 GB per column, ~16 GB per band-with-error. §2.3's "store what
  the pipeline resolves filters to, `extra` for the rest" is not tidiness, it is
  hundreds of gigabytes.
- **`extra` JSONB is the most expensive column.** ~150 B/row modelled — at 991 M
  rows, ~150 GB, a quarter of RefCat2's footprint. Keep it small or leave it
  NULL; JSONB keys are stored per row.
- **Index build dominates wall clock**, not `COPY`. The Phase B1 sequence
  (transform → PK → replicated parent indexes → ANALYZE → validate) builds a GiST
  index over ~10⁹ geographies. `maintenance_work_mem` and
  `max_parallel_maintenance_workers` on the ingest connection are the levers, and
  they should be recorded in the runbook once measured.
- **Peak disk** — §1.2. Check free space *before* starting, not after failing.
- **The swap stays short.** Phase B2 (drop old partition, rename, `ATTACH
  PARTITION`) must remain a short transaction regardless of table size. That is
  the property that lets a nine-hour RefCat2 rebuild happen while the old release
  serves reads, and preserving it is the main correctness risk in touching the
  runner for large-catalog support.
- **Timings are unmeasured.** No throughput number exists in
  [operations/performance.md](../operations/performance.md) for import. L0 fixes
  that by timing an APASS DR10 re-import and extrapolating; until then, treat the
  week-scale estimates in §5 as guesses.

### 4.3 Mirroring: the runbook

[guides/provenance.md](../guides/provenance.md) covers the layout and the CDS
`wget` recipe. What this plan adds:

**One directory per release**, named by `source_subdir`, files directly inside it
(globs are non-recursive):

```
$SKYCAT_DATA_ROOT/
├── APASS-DR6/            (existing)
├── APASS-DR10/           (existing)
├── VSX/                  (existing)
├── Landolt_1992/         (existing)
├── Landolt_2009/         (existing)
├── StetsonGlobs/         (existing)
├── Tycho2/               tyc2.dat*            + ReadMe, index.dat, guide.*
├── ATLAS-RefCat2-m18/    *.csv.gz             + ReadMe, the .tbz if using format A
├── Gaia-DR3-GSPC/        *.csv.gz             + ReadMe
└── 2MASS-PSC/            psc_*.gz             + format_psc.html, README_ftp.html
```

**Non-CDS sources need their own recipe.** The `wget --cut-dirs` pattern is
CDS-specific. IRSA and MAST are plain HTTPS directories; use `wget
--timestamping` with an explicit file list rather than `--recursive`, because
recursive crawls of an archive index page pull in navigation cruft that then has
to be excluded from `data_globs`. The Gaia archive may require an ADQL export
rather than a file fetch (open item 5) — if so, the export is a **one-time,
recorded, checksummed** step whose output lands in `SKYCAT_DATA_ROOT` like any
other mirror, and the ADQL text goes in the release `notes`.

**Always inventory before importing:**

```bash
uv run skycat discover                          # every family/release
uv run skycat discover refcat2 v1-m18           # one, with issues explained
uv run skycat --json discover > inventory-$(date -I).json
```

Keep the JSON. It is the only record of what the tree looked like before the
import.

**Then import, watching the target.** Import prints its target before loading;
`looks_production()` gates destructive commands. For a multi-hour ingest, run it
where it can survive a disconnect and capture the `skycat.ingestion` structured
events.

### 4.4 Magnitude-limited releases and the honesty they require

A magnitude-limited release is the largest storage lever available, and it sits
awkwardly against a stated principle: *releases are a deployment mechanism, not a
science dimension.* A cut is unambiguously a science dimension. Resolvable, but
only explicitly:

1. **The cut goes in the release name.** `v1-m18`, never `v1`. `ResolvedRelease`
   already carries `release_name` into every resolution, so the cut travels with
   every answer at no extra cost.
2. **The cut is a validated predicate, not a comment.** The family's validation
   module asserts no imported row violates it, so a release named `-m18`
   containing an m = 19 source fails validation instead of quietly
   misrepresenting itself.
3. **`skycat families` and `--json` surface it**, and the README family table
   states it.
4. **A magnitude-limited family is never described as "the catalog."** A user
   who cones at m = 20 and gets nothing must be able to discover why from the
   data.

**RefCat2 sidesteps all of this** (§2.2): the cut is which tarballs you
downloaded, so `discover` shows it and no predicate is needed. That is a genuine
reason to prefer it. Rules 1–4 apply to any catalogue where the cut is an import
filter instead — which, in the deferred set, means PanSTARRS.

Whether this needs its own decision record is a judgement call; it changes what a
release means, so probably yes.

---

## 5. Action plan

```
L0 measure ─▶ L1 Tycho-2 ─▶ L2 RefCat2 m<18 ─▶ L3 Gaia GSPC ─▶ L4 2MASS ─▶ L5 SkyMapper
                  │                │                                          (optional)
                  │                └── L2′ extend to m<19 (partition swap, any time after L2)
                  ▼
        each mirrored family adds a parity pair for remote-catalogs.md R1
```

L1 is the only phase that is unambiguously worth doing before anything else is
known. L2 onward should wait for L0's measurements.

### L0 — Measure and confirm (1–2 days)

**Scope.** Turn §1's estimates into numbers.

- Run `skycat sizes` against the real store; record heap, index and `extra` sizes
  for the DR10 partition specifically, and correct §1.1's constants here.
- Time an APASS DR10 re-import end to end, with per-phase breakdown (staging
  `COPY`, transform, PK, GiST, ANALYZE, swap). This is the only way to estimate
  L2, which is ~8× larger. Record it in
  [operations/performance.md](../operations/performance.md), which currently has
  no import throughput figure at all.
- Confirm the 4 TB figure: is it dedicated to catalogs, or shared with WAL,
  backups and everything else? WAL during a 10⁹-row ingest is not small.
- Confirm RefCat2's format choice (§2.2) by downloading one declination zone or
  one magnitude tarball and inspecting it — 5.9 GB is cheap for a decision this
  structural.
- Resolve the Gaia GSPC bulk-download route (open item 5).

**Files.** None in `skycat/`. Results tables appended to this note and to
`performance.md`.

**Acceptance.** A measured B/row for APASS DR10; a measured rows/second for
import; a confirmed disk budget with the §1.2 peak multiplier applied; a decided
RefCat2 format; a working GSPC download path.

**Stop and re-plan if** the measured B/row is more than ~1.5× the modelled value,
or the disk turns out to be shared. Both would move the L2′/L4/L5 boundary.

### L1 — Tycho-2 (3–5 days)

The cheapest real capability gain available: 0.9 GB buys the bright-star range
APASS cannot serve. `guides/add-family.md` is a complete worked example for
exactly this family, including the `native_id` discussion and why Tycho-2 is a
new family rather than a release.

**Files.** All six touch points (§4.1): `catalog_defs.py`, `models/tycho2.py`,
`ingestion/parsers/tycho2.py`, `migrations/versions/0007_tycho2.py`,
`validation/tycho2.py`, tests + README table.

**Acceptance.** `skycat import tycho2 …` end to end against a throwaway PostGIS;
`uv run pytest tests -q --require-postgis` green; a documented cone at V ≈ 8
returning Tycho-2 rows where `apass` returns none; measured on-disk size compared
against §1.1's narrow constant, and the constant corrected here.

**This phase also validates the guide.** If L1 takes materially longer than five
days, the six-touch-point path is more expensive than the guide implies, and that
is a finding worth fixing before L2 — which is the same work at 400× the row
count.

### L2 — ATLAS RefCat2, m < 18 (2–3 weeks)

The anchor. First family where ingestion is a genuine engineering exercise rather
than a formality, and the first real test of the detached-rebuild path at
~5 × 10⁸ rows.

**Scope.** The six touch points, plus:

- A decision, recorded, between the MAST CSV format and the original
  scaled-integer format (§2.2). CSV is the lower-risk first implementation.
- A typed model carrying g/r/i/z + errors, J/H/K, proper motions and parallax;
  survey-provenance bitmasks and quality metrics in `extra` — but see §4.2 on
  `extra` cost at this scale, and prefer typed columns for anything the pipeline
  filters on.
- Release named `v1-m18` from three tarballs / the equivalent CSV subset, per
  §4.4.
- Ingest-tuning notes (`maintenance_work_mem`, parallel maintenance workers)
  captured in the runbook.

**Acceptance.** Import and activate against a throwaway; `--require-postgis`
green; timings and final on-disk size recorded in `performance.md`; a cone in a
crowded field returning RefCat2 rows with `separation_deg` ordering intact; the
old release demonstrably serving reads throughout a `--replace`.

**Stop here if** L0's measured import rate puts the ingest beyond a maintenance
window that operations can accept. Then L2 becomes "m < 17" (~153 GB) and the
question moves to whether that is still worth it.

### L2′ — Extend RefCat2 to m < 19 (2–4 days, any time after L2)

Download the fourth tarball, import as release `v1-m19`, activate. A partition
swap, not new code. Do it when a use case appears, not speculatively — it more
than doubles the family's footprint and raises peak disk by ~1 TB.

### L3 — Gaia DR3 synthetic photometry (1–2 weeks)

**Scope.** Six touch points. The design decision is the band set (§2.3): store
the Johnson UBVRI and Sloan ugriz standardised magnitudes as typed columns, the
rest in `extra`. State the G < 17.65 limit in the family description.

**Acceptance.** Import and activate; `--require-postgis` green; **a comparison
against the transformation coefficients** — take a field, resolve Johnson V from
APASS via the legacy Lupton/Jester expressions, and compare against GSPC's
measured synthetic V. That comparison is the whole argument for this phase and it
should be in the acceptance criteria, not left as future work.

### L4 — 2MASS Point Source Catalog (1–2 weeks)

**Scope.** Six touch points. The IRSA distribution is already `COPY`-shaped
pipe-delimited ASCII, so the parser is the simplest of the large families.
Preserve the native quality flags (`ph_qual`, `rd_flg`, `cc_flg`) as typed
columns — they are what distinguishes this from RefCat2's J/H/K.

**Acceptance.** As L2. Plus: a documented query showing 2MASS JHK for a source
RefCat2 does not carry.

### L5 — SkyMapper (1–2 weeks, conditional)

Only if a consumer names u/v photometry or a southern gap RefCat2 does not
cover. Confirm the current designation before mirroring — DR2 (`II/358/smss`) is
not the newest release.

### Explicitly not in this plan

- **Full PanSTARRS DR2, full Gaia DR3 main, NOMAD, USNO-B1.0, SDSS** — §2.7.
  These are [remote-catalogs.md](remote-catalogs.md)'s Tier D.
- **`skycat fetch` or any automated download.** `SKYCAT_DATA_ROOT` stays
  read-only and mirroring stays a deliberate, separate step; that is what makes a
  release reproducible.
- **Photometric transformation tables.** Skycat stores measured magnitudes; the
  consumer transforms. GSPC (L3) makes that boundary cheaper to hold, not harder.

---

## 6. Risks

| Risk | Consequence | Mitigation |
|---|---|---|
| **Bytes/row model is wrong** | Every figure here scales | L0 measures it first. The model is transparent (§1.1) so the error is bounded and correctable |
| **Import throughput unknown** | L2's "2–3 weeks" could be badly wrong | L0 times DR10 and extrapolates. No number exists today |
| **Peak disk exceeded mid-rebuild** | A failed import with a half-built standalone table | Check free space before starting; §1.2 gives the formula. Consider a pre-flight check in the importer |
| **RefCat2 format choice wrong** | Rework in the middle of the largest ingest | L0 downloads one file and looks at it. 5.9 GB is cheap insurance |
| **GSPC has no bulk path** | L3 becomes a paginated ADQL export | Open item 5, resolved in L0. The export is workable at 220 M rows, just slower |
| **Long ingest blocks reads** | An outage during a maintenance window | The detached-rebuild design already prevents this. §4.2 flags preserving it as the main correctness risk when touching the runner |
| **4 TB is shared** | The whole envelope shifts | L0 confirms. Everything past L2 is contingent on it |
| **`extra` JSONB bloat** | Hundreds of GB of per-row keys | §4.2. Keep it small at 10⁹ rows or leave it NULL |

---

## 7. Open items

1. **Is the 4 TB dedicated to catalog data?** WAL, backups, and the staging/
   standalone copies during a rebuild all live on the same volume unless
   deliberately separated. Blocks everything past L1.
2. **Measured bytes per row.** §1.1 is schema-derived, not measured. L0's first
   task; every GB figure scales with it.
3. **Import throughput.** Unmeasured. `performance.md` has query numbers and no
   import numbers. Needed before L2 can be scheduled.
4. **Tycho-2 file layout at CDS.** The ReadMe confirms `tyc2.dat`, `suppl_1.dat`,
   `suppl_2.dat`, `index.dat` and 2,539,913 main-catalogue records; the FTP
   directory listing was not reachable when this note was written (bot
   protection), so whether `tyc2.dat` arrives whole or as gzipped chunks must be
   confirmed at mirror time. Also decide whether `suppl_1.dat` is `data_globs` or
   `aux_globs`.
5. **Gaia GSPC bulk download path.** The table is
   `gaiadr3.synthetic_photometry_gspc` in the ESA archive; whether there is a
   direct bulk CSV path under `cdn.gea.esac.esa.int` or whether an ADQL export is
   required is unverified. Changes L3's mirroring step, not its design.
6. **RefCat2 format: MAST CSV or original scaled-integer?** §2.2. Decide in L0.
7. **RefCat2 row counts per magnitude bin.** The cumulative figures in §2.2 are
   inferred from compressed tarball sizes, not from published counts. Confirm
   before committing to m < 18 vs m < 19.
8. **Does the pipeline need u/v photometry?** Determines whether L5 happens at
   all.
9. **Does the pipeline need JHK beyond what RefCat2 carries?** Determines
   whether L4 is worth 190 GB.
10. **Do magnitude-limited releases need a decision record?** §4.4 argues they
    change what a release means. Settle before L2 ships `v1-m18`.
11. **Current SkyMapper designation.** `II/358/smss` is DR2; newer releases
    exist.

---

## 8. References

**Skycat** — `skycat/models/apass.py`, `skycat/models/mixins.py`,
`skycat/ingestion/runner.py`, `skycat/ingestion/parsers/`,
`skycat/registry/catalog_defs.py`, `skycat/migrations/versions/`;
[guides/add-family.md](../guides/add-family.md),
[guides/provenance.md](../guides/provenance.md),
[operations/runbook.md](../operations/runbook.md),
[operations/performance.md](../operations/performance.md),
[reference/architecture.md](../reference/architecture.md),
[decisions/0001](../decisions/0001-postgresql-postgis-only.md).

**Companion note** — [remote-catalogs.md](remote-catalogs.md) for the adjacent
`RemoteCatalogReader` and the Tier D catalogs this plan defers to it.

**Upstream distribution**

- [CDS/VizieR FTP](https://cdsarc.cds.unistra.fr/ftp/) — the `ftp/<designation>/`
  pattern; [Tycho-2 `I/259`](https://cdsarc.cds.unistra.fr/viz-bin/cat/I/259).
- [2MASS All-Sky bulk download (IRSA)](https://irsa.ipac.caltech.edu/2MASS/download/allsky/)
  and the [PSC column format](https://irsa.ipac.caltech.edu/2MASS/download/allsky/format_psc.html).
- [ATLAS-REFCAT2 at MAST](https://archive.stsci.edu/hlsp/atlas-refcat2);
  [Tonry et al. 2018, ApJ 867, 105](https://iopscience.iop.org/article/10.3847/1538-4357/aae386).
- [Gaia DR3 `synthetic_photometry_gspc` data model](https://gea.esac.esa.int/archive/documentation/GDR3/Gaia_archive/chap_datamodel/sec_dm_performance_verification/ssec_dm_synthetic_photometry_gspc.html);
  [Gaia Collaboration / Montegriffo et al. 2023, "The Galaxy in your preferred colours"](https://www.aanda.org/articles/aa/full_html/2023/06/aa43709-22/aa43709-22.html).
- [AAVSO APASS download](https://www.aavso.org/download-apass-data).

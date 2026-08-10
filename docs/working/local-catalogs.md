---
status: open
reviewed: 2026-08-09
branch: docs-deprecate-remote-catalogs
authority: upstream distribution documentation (CDS, IRSA, MAST, ESA, AAVSO, IPAC) + skycat code inspection @ 2c36084
implementation: local-only-planning
document-type: research survey - local catalog support only
---

# Local catalog support - what to store with 12 TB available

**What this document is.** A survey of the astronomical data sources Skycat could
support by mirroring into PostgreSQL/PostGIS now that the storage envelope is
**12 TB** rather than 4 TB. For each candidate: what it is, what it uniquely
provides, exactly where the bulk files live, what format and volume they arrive
in, what they would cost on disk, and whether it deserves local operational
priority.

Skycat catalog support is local support. A supported catalog family has local
source files, an ingestion path, a typed model, migrations, validation coverage,
documentation, and PostgreSQL/PostGIS query behavior. Remote catalog services are
not an alternate implementation path, fallback, or supported Skycat catalog
surface.

**What this document is not.** It is not a design, an architecture decision, or
an implementation plan. It does not specify models, parsers, migrations or APIs.
Where it touches Skycat's internals it is to establish a *constraint* a future
design has to respect, not to propose how. A design agent should be able to read
this and [guides/add-family.md](../guides/add-family.md), and have everything
needed to plan local catalog work.

**Deprecated context:** the former remote catalog research and remote query
architecture notes are archived in `docs/working/archive/`. They are retained as
historical research only and should not be used as implementation guidance.

---

## 1. Four systems, and why only one is a Skycat catalog channel

The four astronomical data systems in scope are not four catalogs. They are four
different kinds of thing, and the difference determines whether Skycat can
support them locally.

| System | Operator | Kind of thing | Unit of answer | Skycat local support position |
|---|---|---|---|---|
| **VizieR** | CDS, Strasbourg | Catalog **distribution service** - an archive of ~50,000 published catalogs and tables | Table rows | **Yes, when the needed table has bulk files worth mirroring.** |
| **SIMBAD** | CDS, Strasbourg | Object **database** - curated, cross-identified, predominantly galactic/stellar | One object: identifiers, object type, basic data, bibcodes | **No**, unless CDS publishes a usable bulk snapshot for a specific local family. Query-only access is not Skycat catalog support. |
| **NED** | IPAC, Caltech | Object **database** - extragalactic | One object: cross-IDs, redshift, photometry, references | **No** general Skycat catalog support. Curated file products such as NED-LVS can be evaluated as local families. |
| **ADS** | CfA, Harvard | **Literature** database | A paper | **No.** Papers are not catalog rows. |

**Only VizieR distributes broad catalog bulk files among these four systems, so
only VizieR is a general catalog source channel for Skycat.** Skycat should not
add live service query surfaces to compensate for catalogs that cannot be
mirrored locally.

Three important qualifications:

1. **VizieR is rarely the best channel for a catalog it carries.** Most of the
   large surveys have a native archive — IRSA for 2MASS, MAST for
   Pan-STARRS and ATLAS-RefCat2, the ESA Gaia Archive for Gaia — and the native
   archive usually offers a better-organised, better-documented, database-shaped
   bulk product than the VizieR mirror. §3 is organised by *distribution channel*
   for exactly this reason. VizieR remains the right channel for small published
   tables (Landolt, Stetson, Tycho-2) and the canonical place to check a
   designation.
2. **SIMBAD and NED are not mirrorable, but they are also not trying to be.**
   Their value is curation — cross-identification across the literature — which
   is a living process, not a snapshot. A mirrored SIMBAD would be stale the day
   it landed, neither operator publishes a general dump, and Skycat should not
   expose their query interfaces as catalog support.
3. **NED has one product that *is* a file:** the Local Volume Sample (§3.6). It
   is the single mirrorable thing in the extragalactic half of this landscape,
   and it is small.

**ADS has no role in this document at all.** It returns papers, not sources. Its
narrow legitimate use near Skycat is provenance research outside the catalog
query surface.

---

## 2. What mirroring costs

The sizing evidence behind every verdict in §3. All figures are modelled from the
shipped schema, **not measured** — see open item 2.

### 2.1 Bytes per row

`ApassSource` (`skycat/models/apass.py`) is the wide case: 28 attributes, sixteen
`Double` magnitude/error columns, a `JSONB extra`, and the generated
`geography(Point,4326)`. Catalog tables do not carry `TimestampMixin`, so there
is no per-row `created_at`/`updated_at`.

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

Planning constants used below: **600 B/row wide, 400 B/row mid, 350 B/row
narrow**, ±30 %. `extra` size and `native_id` length are the terms that move
most, and both are per-catalog.

### 2.2 Peak disk is not steady-state disk

Skycat's ingestion builds a **detached standalone copy** of a release — with its
own primary key and replicated indexes — while the old partition keeps serving
reads, and swaps them in a short final transaction. Unlogged staging is live at
the same time. So during a rebuild the database holds the old partition, the new
standalone table and the staging copy simultaneously:

> **Disk needed ≈ steady state + ~1.75 × the largest single release.**

This is a consequence of a design property worth keeping — it is what lets a
`--replace` of the active release stay active throughout — and it means **the
largest catalog you mirror sets your headroom requirement.** It is the single
most important number in this document, because it determines whether a release
can be rebuilt safely rather than merely stored.

### 2.3 What is stored today

| Family | Release | Rows | Est. |
|---|---|---|---|
| `apass` | DR6 | 42.6 M | ~23 GB |
| `apass` | **DR10** | 128.6 M | **~77 GB** |
| `vsx` | current | 10.3 M | ~5 GB |
| `stetson` | stetsonglobs | 4.9 M | ~2 GB |
| `landolt` | 1992 + 2009 | 1.1 k | <1 MB |
| | | **steady** | **≈ 107 GB** |
| | | **peak during a DR10 rebuild** | **≈ 240 GB** |

### 2.4 The 12 TB envelope

With 12 TB available, the storage conclusion changes materially. The rebuild rule
still matters, but it no longer disqualifies the terabyte-class catalogs. Even a
broad local mirror set fits with rebuild headroom; the binding constraints become
science value, import time, operational maintenance and schema complexity. The
sets below are cumulative.

| Set | Steady | Largest release | Peak | Uses 12 TB |
|---|---|---|---|---|
| Today | 107 GB | 77 GB | 240 GB | 2 % |
| Today + core additions: Tycho-2, RefCat2 m<19, GSPC, 2MASS | 1.05 TB | 595 GB | 2.09 TB | 17 % |
| + SkyMapper, UCAC5, USNO-B1.0 | 1.68 TB | 595 GB | 2.72 TB | 23 % |
| + Pan-STARRS DR2 | 2.83 TB | 1.15 TB | 4.84 TB | 40 % |
| + Gaia DR3 main instead of Pan-STARRS | 2.78 TB | 1.10 TB | 4.71 TB | 39 % |
| + both Pan-STARRS DR2 and Gaia DR3 main | 3.93 TB | 1.15 TB | 5.94 TB | 50 % |
| + NOMAD as well | 4.38 TB | 1.15 TB | 6.39 TB | 53 % |

The arithmetic means **"too large to rebuild" is no longer a valid local
verdict for this hardware**. Pan-STARRS DR2 and Gaia DR3 main are now feasible
from a disk perspective. They may still be wrong local priorities, but that
argument has to be about duplication, consumer need, import duration or query
shape — not capacity.

This also changes what a low-priority local verdict means. It should no longer
mean "we cannot afford the disk." It means "do not spend local ingestion effort
until a consumer proves the local mirror is useful."

---

## 3. The candidates, by distribution channel

### 3.1 CDS / VizieR — `https://cdsarc.cds.unistra.fr/ftp/<designation>/`

The archive behind ~50,000 published catalogs. Layout, `wget --timestamping`
recipe, and the `--cut-dirs` depth trap are already documented in
[guides/provenance.md](../guides/provenance.md); that guide is the authority for
the mechanics.

VizieR is the right bulk channel for **small-to-medium published tables**. For
the big surveys it mirrors, prefer the native archive (§3.2–§3.4) for local
ingest.

#### Tycho-2 — `I/259` ✅ **mirror**

| | |
|---|---|
| What it is | Astrometric catalog from the Hipparcos/Tycho star mapper: positions, proper motions, BT/VT photometry |
| Unique contribution | **The bright end.** APASS saturates around V ≈ 10; Tycho-2 covers V < 11.5. Skycat currently cannot answer "reference stars for a bright field" at all |
| Rows | 2,539,913 (main catalog) |
| Files | `tyc2.dat`, `suppl_1.dat` (Hipparcos/Tycho-1 stars absent from the main table), `suppl_2.dat`, `index.dat`, `ReadMe`, `guide.pdf` |
| Format | Fixed-width ASCII, ReadMe-specified byte columns |
| Est. on disk | **~0.9 GB** |

**Reasoning.** Under a gigabyte for a capability the store does not have. It is
also the family
[guides/add-family.md](../guides/add-family.md) uses as its worked example
throughout — including the `native_id` question (`TYC1-TYC2-TYC3`, e.g. `1-13-1`)
and why Tycho-2 is a new family rather than a release of an existing one — so the
implementation path is unusually well documented before anyone starts.

*Unresolved:* whether `tyc2.dat` arrives whole or as gzipped chunks, and whether
`suppl_1.dat` should count as data or auxiliary. Both change the checksum and the
row count. See open item 4.

#### SkyMapper — `II/358/smss` (DR2) ⚠️ **conditional**

Southern-hemisphere u, v, g, r, i, z; 285,159,194 rows (DR2), ~170 GB. The **u
and v bands are its distinctive contribution** — nothing else in this survey
carries them. For griz in the south, ATLAS-RefCat2 (§3.3) already incorporates
SkyMapper DR1 and covers the sky.

**Reasoning.** Mirror only if a consumer names u/v photometry or demonstrates a
southern gap RefCat2 does not cover. Newer releases than DR2 exist; confirm the
current designation before mirroring (open item 8).

#### UCAC5 — `I/340` ❌ **skip**

107,758,513 rows, ~45 GB. Superseded on both axes it might serve: Gaia DR3 is
better astrometry, ATLAS-RefCat2 is better photometry. It fits easily; it is just
not worth having.

#### USNO-B1.0 — `I/284` ❌ **low-priority local**

1,045,175,762 rows, ~420 GB. Photographic-plate photometry.

**Reasoning.** It fits easily in 12 TB. It remains low priority because RefCat2
is better for every calibration use, and plate photometry carries scatter the
pipeline should prefer to avoid. Legacy comparison and arbitrary VizieR queries
belong outside Skycat's supported catalog surface; local ingestion should wait
for a named consumer.

#### NOMAD — `I/297` ❌ **low-priority local**

1,117,612,732 rows, ~450 GB. A *compilation* of Tycho-2, UCAC, USNO-B and 2MASS.

**Reasoning.** Mirror the components, not the merge. A merged catalog inherits
every input's errors while obscuring which input each value came from. With 12 TB
it could be stored, but capacity does not make the science case stronger.

#### The long tail

VizieR carries tens of thousands of catalogs, most of them small published
tables. Any of them could in principle be mirrored the same way Landolt and
Stetson already are. That is a real capability and it is why the CDS FTP pattern
is worth having documented — but adding a family is six explicit touch points, so
the long tail is a case-by-case decision, not a program.

### 3.2 IRSA (NASA/IPAC) — `https://irsa.ipac.caltech.edu/`

#### 2MASS All-Sky Point Source Catalog ✅ **mirror**

| | |
|---|---|
| What it is | All-sky near-infrared J, H, Ks photometry and positions |
| Unique contribution | **The only all-sky JHK source in full**, with its native quality flags (`ph_qual`, `rd_flg`, `cc_flg`, `gal_contam`). RefCat2 carries J/H/K but only for sources in its griz selection and without the flag detail |
| Rows | 470,992,970 |
| Download | `https://irsa.ipac.caltech.edu/2MASS/download/allsky/` |
| Files | `psc_aaa.gz` … `psc_ace.gz` (Dec < 0°), `psc_baa.gz` … `psc_bbi.gz` (Dec > 0°) |
| Volume | **~43 GB gzipped** |
| Format | Pipe-delimited ASCII, explicitly "formatted in a manner consistent with convenient loading into a database server" |
| Column spec | `https://irsa.ipac.caltech.edu/2MASS/download/allsky/format_psc.html` |
| Est. on disk | **~190 GB** |

**Reasoning.** Use IRSA, not the VizieR mirror (`II/246`): IRSA is the
authoritative distribution, the file set is designed for bulk database loading —
it is effectively already `COPY`-shaped — and 43 GB compressed is a manageable
mirror. The open question is whether the pipeline needs JHK beyond what RefCat2
already carries (open item 6); if it does not, this is 190 GB that could wait.

### 3.3 MAST (STScI) — `https://archive.stsci.edu/`

#### ATLAS-RefCat2 ✅ **mirror — the strongest candidate in this document**

| | |
|---|---|
| What it is | An all-sky stellar reference catalog assembled specifically for photometric calibration |
| Rows | 991 M at −1.5 < m ≲ 19; ~1.05 B including the 19–20 bin |
| Coverage | **Virtually complete to m < 19 over the entire sky** |
| Photometry | g, r, i, z with errors, plus J, H, K |
| Astrometry | Gaia DR2, with proper motions and parallaxes |
| Assembled from | Pan-STARRS DR1, ATLAS Pathfinder, ATLAS-reflattened APASS, SkyMapper DR1, APASS DR9, Tycho-2, Yale Bright Star Catalog |
| Download | `https://archive.stsci.edu/hlsp/atlas-refcat2` → files under `https://archive.stsci.edu/hlsps/atlas-refcat2/` |
| Reference | Tonry et al. 2018, ApJ 867, 105 |
| Est. on disk | **~285 GB** at m < 18, **~595 GB** at m < 19 |

**Reasoning — this is the finding that reorganises the whole plan.** Read the
"assembled from" row again: it is most of the rest of this survey's candidate
list, already cross-calibrated onto one photometric system by people who did it
carefully. For the pipeline's actual job — finding reference stars with reliable
magnitudes near an arbitrary field — RefCat2 is a *better product than any of its
inputs*, at roughly half the disk of full Pan-STARRS.

The practical consequence: **mirroring Pan-STARRS becomes unnecessary rather than
merely unaffordable**, and SkyMapper drops to conditional.

**Two distribution formats, and the choice matters:**

| Format | Organisation | Volume | Note |
|---|---|---|---|
| Original | Scaled-integer columns, **split by magnitude** | ~47 GB total | Parser must descale |
| MAST | CSV in astronomical units, five declination zones (~17–19 GB each) | ~91 GB total | `hlsp_atlas-refcat2_atlas_ccd_<lo>-<hi>_multi_v1_cat.csv.gz` |

**The magnitude split is a structurally significant property.** The original
format ships as five tarballs:

| File | Compressed | Cumulative in Skycat |
|---|---|---|
| `hlsp_atlas-refcat2_atlas_ccd_00-m-16_multi_v1_cat.tbz` | 5.9 GB | ~78 GB |
| `hlsp_atlas-refcat2_atlas_ccd_16-m-17_multi_v1_cat.tbz` | 5.6 GB | ~153 GB |
| `hlsp_atlas-refcat2_atlas_ccd_17-m-18_multi_v1_cat.tbz` | 9.8 GB | ~285 GB |
| `hlsp_atlas-refcat2_atlas_ccd_18-m-19_multi_v1_cat.tbz` | 17 GB | ~595 GB |
| `hlsp_atlas-refcat2_atlas_ccd_19-m-20_multi_v1_cat.tbz` | 8.7 GB | ~712 GB |

This means a magnitude limit is a **mirroring decision rather than an import
filter** — you simply do not download the faint tarball. Nothing has to be
filtered during ingest, nothing has to be validated as filtered, and `skycat
discover` shows which files are present. §4.2 explains why that matters.

*Unresolved:* the per-bin row counts above are inferred from compressed tarball
sizes, not from published counts (open item 5), and the format choice is
undecided (open item 7). Downloading one 5.9 GB tarball would settle both.

#### Pan-STARRS DR2 — `II/349` ⚠️ **feasible, but not a core mirror**

1,919,106,885 rows, ~1.15 TB. It passes the §2.2 rebuild test on 12 TB, so the
old storage objection is gone. RefCat2 already carries PS1-derived griz, all-sky,
to m < 19, homogenised; mirroring Pan-STARRS locally means paying import and
schema cost for the native, un-homogenised product. Do it only if a consumer
needs native PS1 quantities that RefCat2 deliberately does not expose.

### 3.4 ESA Gaia Archive — `https://gea.esac.esa.int/archive/`

#### Gaia DR3 synthetic photometry (GSPC) ✅ **mirror**

| | |
|---|---|
| What it is | Synthetic photometry computed from Gaia DR3 BP/RP low-resolution spectra, **standardised** against external standard stars |
| Table | `gaiadr3.synthetic_photometry_gspc` |
| Rows | ~220 M sources with XP spectra, limited to **G < 17.65** |
| Passbands | Johnson-Kron-Cousins **U, B, V, R, I**; SDSS **u, g, r, i, z**; Pan-STARRS1 y; HST ACS/WFC F606W, F814W |
| Quoted accuracy | Reproduces Hipparcos Hp/BT/VT to better than ~2.5 mmag all-sky |
| Reference | Gaia Collaboration / Montegriffo et al. 2023 |
| Est. on disk | **~155 GB** |

**Reasoning.** This is the one thing nothing else in the survey provides:
*measured* standardised Johnson magnitudes. It is the direct answer to the
photometric transformation coefficients the legacy providers apply — the Lupton,
Jester, Jordi and Tonry expressions used by legacy remote providers — because a
pipeline calibrating in Johnson V against a measured synthetic V does not need
coefficients at all.
That is a science-quality improvement, not merely a storage decision.

It is absent from the entire legacy provider list purely because it postdates it.

The G < 17.65 limit is inherent to the catalog, not something Skycat would
impose, but it must be stated wherever the family is described: a user coning at
G = 19 will correctly get nothing.

*Unresolved:* the bulk download route. The table is queryable through the Gaia
Archive; whether there is a direct bulk CSV path or whether a paginated ADQL
export is required is unverified (open item 3). This changes the mirroring
procedure, not the verdict.

#### Gaia DR3 main — `I/355/gaiadr3` ⚠️ **feasible, but not a core mirror**

1,811,709,771 rows, ~1.10 TB. It also passes the rebuild test on 12 TB. GSPC is
the photometrically useful subset at one seventh the size, so the main table
should be treated as a separate astrometry/source-identity product rather than a
photometric-calibration prerequisite. Mirror it when a consumer needs that
broader Gaia source model locally.

### 3.5 AAVSO — `https://www.aavso.org/download-apass-data`

**APASS** (DR6 and DR10) is already mirrored and is the current workhorse:
B, V, g′, r′, i′ to V ≈ 17, saturating around V ≈ 10. Distributed by AAVSO
directly, not by CDS. Note that VizieR carries only DR9 (`II/336/apass9`), a
release Skycat does not have.

**VSX** (`B/vsx/vsx`) is also already mirrored: a living index of variable stars,
used to *exclude* variables from calibration star lists rather than as a
calibration source.

### 3.6 IPAC / NED — the one extragalactic file

#### NED Local Volume Sample (NED-LVS) 🔍 **investigate**

NED as a whole is not mirrorable (§1). NED-LVS is the exception: a curated sample
of nearby galaxies with positions, distances and multiwavelength photometry,
published as a table rather than served only through the object interface. NED
reports that roughly 90 % of NED-LVS objects have at least one photometric
measurement, with GALEX, 2MASS and AllWISE photometry joined in.

**Reasoning for investigating rather than deciding.** Whether this belongs in
Skycat depends entirely on whether the pipeline does extragalactic work — for
supernova follow-up, a local host-galaxy sample with distances is genuinely
useful, and Skynet is used for exactly that kind of observing. But it is a
different *kind* of catalog from everything else here: galaxies rather than point
sources, distances and redshifts rather than calibration photometry. It would be
the first family whose rows are not reference stars, which raises modelling
questions this document is not the place to answer.

It is also small enough that size is not the question. See open item 9.

---

## 4. Cross-cutting observations for whoever designs this

Findings that emerged from the survey and that constrain any implementation
plan. Stated as constraints, not as solutions.

### 4.1 Column count is a first-class storage cost

At 991 M rows every `Double` column is ~7.9 GB, and a magnitude plus its error is
~16 GB. `extra` JSONB modelled at ~150 B/row is ~150 GB — a quarter of RefCat2's
footprint — because JSONB stores its keys per row.

This makes "which bands get typed columns and which go in `extra`" a
hundred-gigabyte decision for the large catalogs, not a matter of taste. GSPC
(§3.4) is the sharpest case: it publishes at least a dozen passbands and storing
all of them with errors would roughly double its footprint.

### 4.2 A magnitude limit changes what a release means

Skycat's model holds that releases are a deployment mechanism, not a science
dimension. A magnitude-limited release is unambiguously a science dimension: a
user who cones at m = 20 and gets nothing needs to be able to discover why from
the data rather than from a wiki page.

This is a real tension and it needs deciding before any magnitude-limited release
ships. The survey's contribution is to note that **RefCat2 sidesteps it entirely**
(§3.3) because the cut is which files were downloaded rather than a predicate
applied during import — which is an argument for preferring RefCat2, and a reason
the tension only actually binds for Pan-STARRS-style candidates that would need
filtering.

### 4.3 The largest catalog sets the disk headroom

§2.2. Adding RefCat2 at m < 19 raises the peak-disk requirement by ~1 TB even
though the catalog itself is ~595 GB. Whoever sizes the server needs the rebuild
rule, not the steady-state number.

### 4.4 Import throughput is unmeasured

[operations/performance.md](../operations/performance.md) documents query
performance in detail and contains **no import throughput figure at all**. There
is therefore no basis in the repository for estimating how long a 10⁹-row ingest
takes. Timing an APASS DR10 re-import (128 M rows, already mirrored) is the
cheapest way to get a number, and everything scheduled after it depends on that
number.

Related unknowns that only measurement will settle: whether index build or `COPY`
dominates wall clock at this scale, and what `maintenance_work_mem` and
`max_parallel_maintenance_workers` should be for a GiST build over ~10⁹
geographies.

### 4.5 Native archives are better bulk channels than VizieR

For every large survey in this document, the native archive (IRSA, MAST, ESA)
offers a better-organised bulk product than the VizieR mirror — often explicitly
designed for database loading, as 2MASS's is. The `wget --cut-dirs` recipe in
[provenance.md](../guides/provenance.md) is CDS-specific and does not transfer:
IRSA and MAST are plain HTTPS directories where a recursive crawl pulls in
navigation pages that then have to be excluded from the data globs, and the Gaia
Archive may not be a file fetch at all.

Any mirroring runbook needs per-channel recipes, not one recipe.

### 4.6 What does not change

`SKYCAT_DATA_ROOT` stays read-only and mirroring stays a deliberate, separate
step — that is what makes a release reproducible, and nothing in this survey
argues for an automated downloader. Skycat also continues to store *measured*
magnitudes and leave photometric transformation to the consumer; GSPC (§3.4)
makes that boundary cheaper to hold rather than harder.

---

## 5. Summary and suggested ordering

| Verdict | Catalog | Channel | On disk | Reason |
|---|---|---|---|---|
| ✅ **Core mirror** | Tycho-2 | CDS `I/259` | ~0.9 GB | Bright end; costs nothing; already the worked example in the guide |
| ✅ **Core mirror** | ATLAS-RefCat2 (m<19) | MAST | ~595 GB | All-sky griz+JHK to m<19, homogenised; subsumes Pan-STARRS and much of SkyMapper for calibration |
| ✅ **Core mirror** | Gaia DR3 GSPC | ESA | ~155 GB | Measured standardised Johnson UBVRI + Sloan ugriz; replaces transformation coefficients |
| ✅ **Core mirror** | 2MASS PSC | IRSA | ~190 GB | Only full all-sky JHK with native quality flags |
| ⚠️ Conditional local | SkyMapper | CDS / ANU | ~170 GB | Only for native southern u/v |
| ⚠️ Feasible optional | Pan-STARRS DR2 | MAST / VizieR | ~1.15 TB | Fits on 12 TB; mirror only for native PS1 quantities not supplied by RefCat2 |
| ⚠️ Feasible optional | Gaia DR3 main | ESA / VizieR | ~1.10 TB | Fits on 12 TB; mirror only for broader Gaia astrometry/source identity beyond GSPC |
| 🔍 Investigate | NED-LVS | IPAC | small | Extragalactic; depends on whether the pipeline needs host galaxies |
| ❌ Skip | UCAC5 | CDS `I/340` | ~45 GB | Superseded by Gaia and RefCat2 |
| ❌ Low-priority local | USNO-B1.0 | CDS `I/284` | ~420 GB | Fits, but plate photometry is not worth ingestion effort without a named consumer |
| ❌ Low-priority local | NOMAD | CDS `I/297` | ~450 GB | A compilation — mirror the components |

**Core local store: current mirrors + Tycho-2 + RefCat2 m<19 + GSPC + 2MASS ≈
1.05 TB steady, ≈ 2.09 TB peak — about 17 % of 12 TB.**

**Broad all-candidate store: the core local store plus SkyMapper, UCAC5,
USNO-B1.0, Pan-STARRS DR2, Gaia DR3 main and NOMAD ≈ 4.38 TB steady, ≈ 6.39 TB
peak — about 53 % of 12 TB.**

**Suggested ordering, and the reasoning behind it** (a research verdict, not a
project plan — a design agent should re-derive the sequencing against its own
effort estimates):

1. **Measure first.** Bytes/row and import throughput are both unknown (§4.4,
   open item 2), and every subsequent estimate scales with them. Confirm how much
   of the 12 TB is available after WAL, backups and staging policy.
2. **Tycho-2**, because it is under a gigabyte, adds a capability the store
   lacks, and the guide already walks through it. It also serves as a low-stakes
   validation that the six-touch-point path costs what the guide implies — worth
   knowing before attempting the same work at 400× the row count.
3. **RefCat2 at m < 19**, because the storage envelope now supports the full
   calibration-depth mirror from the start. If import throughput is worse than
   expected, the magnitude-binned distribution still gives a reversible way to
   stage m < 18 first.
4. **GSPC**, with a direct comparison against the transformation coefficients as
   the thing that demonstrates the value.
5. **2MASS**, unless RefCat2's JHK coverage proves sufficient for all current
   consumers.
6. **Optional native products** — SkyMapper, Pan-STARRS DR2, Gaia DR3 main,
   USNO-B1.0 and NOMAD — only when a consumer names the native quantities it
   needs locally.

---

## 6. Open questions

Ordered by how much they gate.

1. **How much of the 12 TB is usable catalog headroom?** WAL, backups, and the
   staging/standalone copies during a rebuild share the volume unless
   deliberately separated, and WAL during a 10⁹-row ingest is not small. The new
   envelope has room for every candidate in this survey, but operational policy
   still determines how aggressively to rebuild large releases.
2. **Measured bytes per row, and measured import throughput.** §2.1 is
   schema-derived; §4.4 has no number at all. `skycat sizes` and a timed APASS
   DR10 re-import settle both. Every GB and every schedule in this document
   scales with them.
3. **Gaia GSPC bulk download route.** Direct file path under the ESA archive, or
   paginated ADQL export? Changes the mirroring procedure for §3.4.
4. **Tycho-2 file layout at CDS.** The ReadMe confirms `tyc2.dat`, `suppl_1.dat`,
   `suppl_2.dat`, `index.dat` and 2,539,913 main-catalog records; the FTP
   directory listing was not reachable when this note was written (bot
   protection), so whether `tyc2.dat` arrives whole or chunked must be confirmed
   at mirror time. Also: is `suppl_1.dat` data or auxiliary?
5. **RefCat2 row counts per magnitude bin.** Inferred from compressed tarball
   sizes, not published counts. Confirm before estimating import duration or
   deciding whether to stage m < 18 before m < 19.
6. **Does the pipeline need JHK beyond what RefCat2 carries?** Determines whether
   2MASS is worth 190 GB.
7. **RefCat2 format: MAST CSV or original scaled-integer?** One 5.9 GB download
   settles it.
8. **Does the pipeline need native u/v photometry, or a southern depth RefCat2
   does not reach?** Determines whether SkyMapper happens. Also: what is the
   current SkyMapper designation? `II/358/smss` is DR2 and newer releases exist.
9. **Does the pipeline do extragalactic work that needs host-galaxy data?**
   Determines whether NED-LVS is in scope at all, and whether Skycat should model
   non-stellar sources.
10. **Do magnitude-limited releases need a decision record?** §4.2. Settle before
    any such release ships.
---

## 7. References

**Archived deprecated context** — `docs/working/archive/remote-catalogs.md` and
`docs/working/archive/remote-query-architecture-plan.md`: historical research
about remote catalog services. Remote catalog support has been deprecated and is
not a Skycat implementation track.

**Skycat** — `skycat/models/apass.py`, `skycat/models/mixins.py`,
`skycat/ingestion/runner.py`, `skycat/registry/catalog_defs.py`;
[guides/add-family.md](../guides/add-family.md),
[guides/provenance.md](../guides/provenance.md),
[operations/performance.md](../operations/performance.md),
[operations/runbook.md](../operations/runbook.md),
[reference/architecture.md](../reference/architecture.md),
[decisions/0001](../decisions/0001-postgresql-postgis-only.md).

**Distribution channels**

- [CDS/VizieR FTP archive](https://cdsarc.cds.unistra.fr/ftp/) — the
  `ftp/<designation>/` pattern; [Tycho-2 `I/259`](https://cdsarc.cds.unistra.fr/viz-bin/cat/I/259).
- [2MASS All-Sky bulk download (IRSA)](https://irsa.ipac.caltech.edu/2MASS/download/allsky/);
  [PSC column format](https://irsa.ipac.caltech.edu/2MASS/download/allsky/format_psc.html).
- [ATLAS-REFCAT2 at MAST](https://archive.stsci.edu/hlsp/atlas-refcat2);
  [Tonry et al. 2018, ApJ 867, 105](https://iopscience.iop.org/article/10.3847/1538-4357/aae386).
- [Gaia DR3 `synthetic_photometry_gspc` data model](https://gea.esac.esa.int/archive/documentation/GDR3/Gaia_archive/chap_datamodel/sec_dm_performance_verification/ssec_dm_synthetic_photometry_gspc.html);
  [Montegriffo et al. 2023, "The Galaxy in your preferred colours"](https://www.aanda.org/articles/aa/full_html/2023/06/aa43709-22/aa43709-22.html).
- [AAVSO APASS download](https://www.aavso.org/download-apass-data).
- [NED Local Volume Sample](https://ned.ipac.caltech.edu/NED::LVS/).

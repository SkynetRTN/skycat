---
status: open
reviewed: 2026-08-07
branch: docs/ml-capability-study
authority: code-inspection (skycat @ d814fc5) + Skynet field-calibration records + published literature
implementation: not-started
---

# Machine learning for Skycat — a capability study

Five proposed ML capabilities, assessed against what Skycat actually stores and
what it actually promises. The question this note answers is not "can a model be
trained on catalog data" — it can, trivially, and the result is usually
worthless. The question is which of these would produce an astronomical result
that a referee, a photometry pipeline, or a Skynet user should act on.

The conclusions are more conservative than the brief implies, for three reasons
that recur throughout:

1. **Two of the five are not primarily machine-learning problems.** Probabilistic
   crossmatching has a forty-year-old Bayesian formalism that is calibrated by
   construction; calibration-star selection is mostly a deterministic rubric.
   In both cases the honest scope for ML is a narrow, checkable sub-problem, and
   claiming more would be the kind of thing this study is supposed to prevent.
2. **Skycat holds no proper motions, no per-source epochs, no images, no light
   curves, and no spectra.** Three of the five proposals are gated on data the
   package does not have and has made no decision to acquire.
3. **A derived score is a release-scoped object.** Skycat's entire value
   proposition is that a query against a named release is reproducible. An ML
   score that is not versioned alongside the release it describes silently
   destroys that property. This is the single most important integration
   constraint and it applies to four of the five proposals.

## Verdict

| # | Project | Category | 1-week prototype | Gating dependency |
|---|---|---|---|---|
| **4** | Scientific-quality / calibration-star prediction | **Build immediately** | **Very High** | None. Labels improve with Skynet field-cal records |
| **3** | Probabilistic intelligent crossmatching | **Build immediately** (statistical core; defer the learned parts) | **High** | Gaia, for proper motions and epochs |
| **1** | Rare-object and anomaly discovery | **Prototype now, develop later** | **High** | None to start; Gaia/2MASS/WISE to be scientifically interesting |
| **2** | Semantic image and archival similarity search | **Architect for now** (build the deterministic footprint index; defer the embeddings) | **Low** | An image archive Skycat does not have |
| **5** | Multimodal object representation | **Long-term research** | **Very Low** | Spectra, light curves, images, and paired data — none of which exist here |

One line: **build 4, build the statistical half of 3, prototype 1, build the
non-ML half of 2, and do not start 5.** §8.10 argues that 5 should be replaced
in the near-term portfolio by a sixth idea — cross-system photometric
transformation with honest uncertainty — which is more useful, uses only data
already in the database, and solves a problem Skynet is documented to have.

## How to read this

§0 is the evidence base: what Skycat holds, what it does not, and the four
architectural constraints any ML feature must satisfy. Every later claim about
feasibility rests on it, so disagreements about a rating usually turn out to be
disagreements about §0.

§1–§5 assess the five proposals against the thirteen questions in the brief.
§6 compares them. §7 says what one week actually buys. §8 answers the ten
questions. §9 proposes the long-term shape.

---

## 0. The evidence base

### 0.1 What Skycat holds today

Four families, all release-partitioned, all with a generated
`geography(Point,4326)` column and a GiST index
(`skycat/registry/catalog_defs.py`, `skycat/models/`).

| Family | Rows | Scientific content |
|---|---|---|
| **APASS** DR6 / DR10 | 42.6M / 128.6M | Johnson B,V + Sloan u,g,r,i,z + Y, each with an error; `n_obs_total`; `ra_err_arcsec`/`dec_err_arcsec`; per-band observation counts in `extra` |
| **VSX** current | ~10.3M | `var_flag`, `var_type`, max/min magnitude with passbands, amplitude, `period_days`, `epoch_hjd`, `spectral_type` (a string) |
| **Landolt** 1992 / 2009 | 526 / 595 | V plus five Johnson-Kron-Cousins colour indices with mean errors, `n_obs`, `n_nights` |
| **Stetson** StetsonGlobs | ~4.89M | U,B,V,R,I with errors **and per-band counts**, DAOPHOT `chi` and `sharp`, Welch-Stetson `variability_index` and `variability_weight`, cluster name |

The query surface is `cone_search`, `batch_crossmatch`, `lookup_native_id`,
validated `QualityFilter` predicates, and validated numeric `order_by`
(`skycat/query/`), all reached through `CatalogReader`.

### 0.2 What Skycat does not hold

This list does more work in this study than the previous one.

| Absent | Consequence |
|---|---|
| **Proper motion** — no family carries one. Landolt 2009's `table5.dat`, which does, is explicitly imported as an *auxiliary* file and not parsed | Epoch propagation is currently impossible. This is the hard gate on Project 3 |
| **Per-source epoch** — `FamilyDef.epoch_jyear` is a family-level constant (2000.0) | Two catalogs cannot be reconciled in time even approximately |
| **Parallax, radial velocity, astrometric quality flags** | No distance, no kinematics, no astrometric-excess-noise proxy for binarity or blending |
| **Images, cutouts, footprints, WCS** | Projects 2 and 5 have no substrate inside this package |
| **Light curves** — VSX stores a *summary* (period, amplitude, type), never a time series | No time-domain modelling; no precovery photometry |
| **Spectra** — VSX's `spectral_type` is a literal string like `M6III` | No spectral modality for Project 5 |
| **Source classification** — VSX `var_type` is the only one, and it covers only variables | Almost no labels of any kind exist in the database |
| **Local source density, isolation, blend flags** | Computable from the data by a spatial self-join; not stored, and not free at 128M rows |
| **Telescope / instrument / observing metadata** | Nothing to condition a domain-shift model on |

The practical summary: Skycat is a **static, multi-band, positional photometric
store**. Every proposal that stays inside that description is tractable now;
every proposal that leaves it needs a separate decision about what Skycat is.

### 0.3 Four architectural constraints on any ML feature

These come from the package's own contracts, not from ML practice, and they are
the reason several ratings below are lower than they would be for a greenfield
project.

**C1 — Derived values are release-scoped or they are wrong.** `release_id` is
the partition key and the hinge between all three schemas. An anomaly score, a
quality score, or a match probability computed against APASS DR10 is not valid
for DR6, and it is not necessarily valid for a *re-import* of DR10 either.
Ingestion's Phase B2 drops the old partition and attaches a new one atomically;
any derived table keyed to the old rows is orphaned at that instant. Whatever is
built must either (a) live in a release-scoped table that participates in the
same lifecycle, or (b) be computed at query time. Bolting a score column onto a
128M-row partition after the fact also conflicts with the detached-rebuild
design in Phase B1, which exists precisely so the parent is never locked.

**C2 — The model is a version, exactly like a release.** Skycat records
`source_checksum`, `imported_row_count`, ingestion runs, and validation
summaries so that a result can be traced to a byte-identical source. A model
whose weights are not pinned and recorded the same way makes every score it
produces unreproducible, which is the one thing this package is built not to
do. A model version is provenance, not configuration.

**C3 — ML annotates and ranks; it must not filter by default.** The stable API
promises that documented keys keep their meaning and that a cone search returns
what is in the cone. A model that silently removes rows changes the meaning of
an existing contract. Adding a score key and an optional ordering does not.
This is a hard boundary, and it happens to also be the scientifically correct
one: a photometry pipeline needs to see the star the model distrusted.

**C4 — Skycat must not acquire a runtime dependency on Skynet.** The package is
deliberately standalone — its own metadata, migrations, roles, and config
namespace. The best label source for Project 4 lives in Skynet's
field-calibration records. Those labels may be used *offline* to fit and
validate a model; the shipped artifact must be a model and a score, never a
call back into the pipeline.

### 0.4 What is deterministically fixable before any model is trained

Three defects surfaced during this review that are not ML problems, are cheap,
and would each improve the science more reliably than the model that is supposed
to sit on top of them. They are noted here because "add ML" is a bad answer to
any of them.

- **`batch_crossmatch` is one-directional and non-mutual.** It finds the nearest
  catalog source to each input. It never checks whether that catalog source's
  own nearest input is the same one. In a crowded field this produces
  many-to-one collapses with no indication that it happened. Skynet's field
  calibration already uses mutual nearest-neighbour matching via `cKDTree`;
  Skycat's own batch path does not. Symmetry is a deterministic fix.
- **No candidate context is returned.** With `nearest_only=True` a caller sees
  one row and no evidence about how close the runner-up was. The single most
  informative crossmatch diagnostic — the ratio of the first to the second
  separation — is discarded before the caller sees it. This costs nothing to
  expose and is a prerequisite for Project 3.
- **Landolt proper motions are on disk and unparsed.** `table5.dat` is listed in
  `aux_globs`. The stated reason is remote-provider row parity, which is a good
  reason for the *row contract* but not a reason to leave the information
  unavailable to anything else.

---

## 1. Rare-object and astronomical anomaly discovery

### 1.1 Purpose and scientific problem

The problem is real and it is a search problem. A catalog of 128 million rows in
eight bands is a point cloud in a space no one can inspect. Astronomers who want
unusual objects currently write colour cuts — `u-g < 0.6 AND g-r < 0.2` for
quasar candidates, an infrared excess criterion, a reduced-proper-motion cut —
and each cut is a hand-drawn box in a two-dimensional projection of a
high-dimensional distribution. Boxes find what their author already suspected
was there. They cannot find the object that is unremarkable in every pairwise
projection and unusual only in the joint distribution, and they cannot find the
object whose oddity is that its *errors* are inconsistent with its brightness.

That is a genuine capability gap and it is what density estimation buys. An
unsupervised model estimates where the bulk of the population lives in the full
observable space and ranks each source by how far outside it sits. No cut is
drawn by hand, no class is specified in advance, and — critically for early
development — no labels are required.

**Where conventional methods remain better.** If the target class is known,
a hand-built cut is superior on every axis: it is explainable, reproducible,
citable, and it does not need validating. Nobody should use an anomaly detector
to find RR Lyraes; VSX already lists them. The gap is specifically "I do not
know what I am looking for," and that is a narrower problem than it sounds,
because most astronomers usually do.

**The distinction the brief asks about is the whole project.** On APASS, the
overwhelming majority of statistical outliers are not rare objects. They are
blends in the Galactic plane, sources near the saturation limit, single-epoch
measurements, and photometry contaminated by a nearby bright star. This is not
a defect to be engineered away — it is the finding. An outlier ranking over
APASS is, first and honestly, a **catalog data-quality instrument**, and only
second a discovery tool. Skycat is a catalog-management system; a tool that
surfaces the ten thousand rows most likely to be wrong is squarely within its
purpose and is arguably worth more to it than a tool that surfaces ten rows that
might be interesting.

There is one discriminant that actually works, and it should be designed in from
the start: **instrumental anomalies are spatially clustered and astrophysical
ones are not.** APASS is built from per-field plate blocks; a bad zero point, a
bad night, or a crowded region produces outliers that pile up inside a field
boundary or a density contour. A carbon star does not. Measuring the spatial
autocorrelation of the anomaly score is a cheap, decisive test, and it converts
the project's biggest weakness into its most useful diagnostic.

### 1.2 How it would fit into Skycat

As an **annotation and an ordering**, never a filter (C3). The natural shape is
that a cone search can be asked to return its rows ranked by unusualness instead
of by separation or magnitude, with the score and its supporting evidence as
additional keys. This composes with what already exists: `order_by` already
supports "brightest first" over a validated numeric column, and an anomaly score
is another such column conceptually.

Two framings are available and they are not equally good.

- **Global anomaly** — unusual relative to the whole catalog. Easy, and mostly
  finds the faint end and the saturated end.
- **Contextual anomaly** — unusual relative to *sources like it in its
  neighbourhood*, conditioning on Galactic latitude, local density, and
  magnitude. Harder, and scientifically far more valuable: a blue outlier in a
  globular cluster field is a blue straggler or a horizontal-branch star; the
  same colour in a halo field is a white dwarf or a quasar. The astronomy is in
  the conditioning.

The contextual framing is the one to build. It also aligns with Skycat's spatial
architecture rather than fighting it, because "sources like it nearby" is a cone
query.

Per C1, the score is release-scoped. The cleanest expression is a derived
release-scoped artifact that participates in the same lifecycle as the data
release, so that re-importing DR10 invalidates and rebuilds its scores rather
than leaving them silently attached to rows that no longer exist.

### 1.3 Inputs and scientific evidence

All of this exists today.

| Input | Why it matters |
|---|---|
| Multi-band magnitudes and all pairwise colours | Colour is the primary observable proxy for temperature, and colour-colour space is where stellar populations, quasars, and reddened objects separate |
| Per-band photometric errors | An object is only anomalous if its offset exceeds its uncertainty. Ignoring errors produces a detector that finds the faint end and nothing else |
| Per-band and total observation counts | Distinguishes "measured badly once" from "measured well many times"; a single-epoch outlier is a measurement, not an object |
| Astrometric errors (`ra_err_arcsec`, `dec_err_arcsec`) | Inflated positional error is the classic blend signature |
| The pattern of NULL bands | Which bands are missing is highly informative — and almost entirely instrumental. It must be modelled explicitly or the detector will learn survey coverage instead of astrophysics |
| VSX association within a few arcsec | Converts "unusual" into "unusual and already known to vary," which is the difference between a discovery and a rediscovery |
| Stetson `chi`, `sharp`, `variability_index` | Actual per-star quality and variability measurements from DAOPHOT — the nearest thing to labels the database contains |
| Local source density | Crowding drives blending, which drives most spurious photometry |

**The systematic that will generate the most false anomalies, stated plainly:**
APASS bands are not simultaneous. A variable star measured in B on one night and
V on another yields a colour that corresponds to no physical state of the star.
This is a *guaranteed* producer of colour outliers, it is concentrated exactly
in the variable population an anomaly hunter cares about, and it cannot be
detected from a single row. VSX cross-referencing partially mitigates it, but
VSX is incomplete, so the residual contamination is unbounded and unmeasurable.
Any claim of a "colour anomaly" in APASS must survive this objection first.

**What would change with new catalogs.** Gaia contributes parallax and proper
motion, which turn a colour outlier into a point on a colour-absolute-magnitude
diagram — the single largest step-change available, because it separates
intrinsically odd objects from ordinary stars at odd distances. 2MASS and WISE
extend the baseline into the infrared, where dust-obscured and cool objects live
and where APASS is blind. With those, the project becomes scientifically
interesting. Without them, it is mostly a QA tool.

### 1.4 Expected output

A per-source score, plus — mandatorily — the evidence that produced it: which
observables contributed most to the score, the local density and the field
context, the number of observations, and whether a VSX source lies within a few
arcsec. A bare score is not a scientific product.

**What the score means:** *this row is far from the bulk of the population in
this observable space, measured in units of its own uncertainty.* **What it is
not:** a probability that the object is rare, a probability that the photometry
is wrong, a detection significance, or anything comparable across releases or
across models. It is a rank within one release under one model version. Two
sources with scores of 0.91 and 0.89 are not meaningfully ordered.

The improvement over current Skycat is that today the only ways to explore a
region are "everything in the cone" and "the brightest N." Neither surfaces the
twenty rows most worth a human's attention. This does.

### 1.5 Example scientific use cases

- **Pre-import quality gate on a new release.** Rank the outliers in a freshly
  staged APASS release before activation and check whether they cluster
  spatially. A pile-up along a field boundary is a bad plate block; a pile-up in
  a magnitude bin is a saturation or zero-point problem. This is a real check
  that the existing validation suite — which tests ranges, nulls, and counts —
  cannot perform, and it runs on data Skycat already stages.
- **Release-to-release regression.** Score DR6 and DR10 sources over the same
  sky and look for objects that became anomalous. Some are genuine variables;
  the rest are a pipeline change between releases. Either answer is useful, and
  the second is exactly what a catalog maintainer needs before activating.
- **Blue-straggler and horizontal-branch candidates in Stetson clusters.**
  Contextual anomaly within a globular cluster's own colour-magnitude
  distribution finds stars that are odd *for that cluster*. This is a
  well-posed, checkable astronomical question with a known right answer, which
  makes it the best validation target the project has.
- **Triage for a transient follow-up.** Given a new alert's position, rank
  nearby catalog sources by unusualness to see whether the field contains a
  plausible quiescent progenitor — a cataclysmic variable in quiescence, a
  known-blue object — rather than reading fifty rows by eye.
- **Student projects.** "Find the strangest star in M13's field and explain why
  it is strange" is a genuinely good exercise, because the second half is the
  astronomy and the tool only does the first half.

### 1.6 Benefits

**Immediate**, on today's data: a catalog-QA capability the validation suite
lacks; a release-comparison diagnostic; an exploratory ranking that makes a
128M-row table browsable; a demonstration that costs no new data.

**Deferred**, requiring Gaia/2MASS/WISE: actual rare-object discovery. Without
parallax or infrared coverage, APASS colour space is too degenerate for the
outliers to be reliably astrophysical. This should be stated in any internal
proposal, because the temptation to describe the QA tool as a discovery engine
will be strong and the first astronomer to look at the output will notice.

### 1.7 Drawbacks and scientific risks

- **Contamination is the default outcome, not an edge case.** The base rate of
  genuinely rare objects in APASS is far below the rate of bad photometry.
  Precision at the top of the ranking will be dominated by artifacts unless the
  spatial-clustering diagnostic is used aggressively.
- **The non-simultaneity systematic (§1.3) is unbounded and unmeasurable.**
- **Heteroscedasticity.** Unless "distance from normal" is measured in units of
  each source's own error, the detector rediscovers the survey's magnitude
  limits. This is the single most common way tabular anomaly detection in
  astronomy fails, and it fails silently and convincingly.
- **Missingness leakage.** NULL-band patterns encode survey footprints. A model
  given them naively learns geography.
- **Unfalsifiable output.** There is no ground truth for "rare." Precision can
  only be established by expert review of a sample, which does not scale and
  which will itself be biased toward objects the reviewer recognises.
- **The authority problem.** An "anomaly score of 0.97" reads as a measurement.
  It is a model-relative rank. Presenting it beside real measured quantities in
  the same row dict invites exactly the confusion the brief warns about — which
  is an argument for naming and documenting it defensively.
- **Model drift across releases.** A model fitted on DR10 applied to DR6 will
  report the differences between the releases as properties of the objects.

**Where deterministic astronomy stays authoritative:** every known object class.
If VSX says a source is an RR Lyrae, that is the answer. If a colour cut from a
published paper defines a candidate class, the cut is the answer and is citable.
The model's jurisdiction is the residual — sources no existing description
covers.

### 1.8 Feasibility

**One-week prototype: High.** Unsupervised, no labels, data in hand, tractable
row counts if scoped to a region or to Stetson. A team can plausibly reach: a
contextual outlier ranking over a defined sky region, a per-source evidence
breakdown, the spatial-autocorrelation diagnostic, and a short expert review of
the top ranked sources. That is a genuine proof of concept.

What it will *not* reach: any defensible statement about whether the top-ranked
objects are astrophysically rare.

**Short-term (weeks to months):** a useful catalog-QA feature, integrated as a
pre-activation report. Realistic and worth doing.

**Production/research grade:** requires Gaia and infrared photometry, an
expert-reviewed benchmark set of confirmed anomalies and confirmed artifacts,
demonstrated stability across releases, and — for any discovery claim —
spectroscopic or time-domain follow-up of a sample. That is a multi-year arc and
it depends on catalogs Skycat has not yet decided to ingest.

### 1.9 Difficulty

| Axis | Score | Reason |
|---|---:|---|
| ML / model | **4** | The algorithms are standard and well-documented. Handling heteroscedastic errors and structured missingness correctly is the part juniors will get wrong, and it is the part that decides whether the output is meaningful |
| Astronomy / domain | **7** | Interpreting an outlier requires knowing APASS's systematics, stellar populations, and what is already catalogued. This cannot be delegated to a junior developer |
| Data | **3** | In hand, clean, well-typed, well-documented |
| Validation | **8** | No ground truth. Expert review does not scale and is itself biased |
| Skycat integration | **4** | Fits the annotate-and-rank shape naturally; the release-scoping question (C1) is a real design decision but a bounded one |
| **Overall research** | **6** | Easy to build, hard to trust |

### 1.10 Data availability

**Can begin primarily with existing Skycat data.** APASS provides scale, Stetson
provides quality parameters and a well-understood test population, VSX provides
the known-variable cross-reference. Gaia would be transformative rather than
enabling; 2MASS/WISE similarly.

### 1.11 Evaluation and scientific success

- **Injection recovery.** Insert synthetic sources with known odd colours at
  known error levels and measure what fraction the ranking surfaces. This gives
  a genuine quantitative sensitivity curve and is the most valuable single
  measurement available.
- **Known-population recovery.** Does the ranking surface Stetson's blue
  stragglers, and VSX's high-amplitude variables, without ever being told they
  exist? A detector that misses known odd objects will not find unknown ones.
- **Artifact/astrophysics separation.** What fraction of the top ranks are
  spatially clustered? A good detector should show a *decreasing* clustered
  fraction as the model improves.
- **Expert review of a blind sample.** Mixed real and shuffled scores, reviewed
  without knowing which is which. Anything less is not evidence.
- **Cross-release stability.** Scores for the same physical sources should be
  correlated across DR6 and DR10 except where the photometry genuinely changed.

Success is not "we found something." Success is a measured sensitivity curve, a
measured artifact fraction, and a maintainer who trusts the pre-activation
report.

### 1.12 Interpretability

**High importance, and achievable.** Unlike an image embedding, a tabular
anomaly score decomposes: it is possible to state which observables drove it.
Every score should ship with the observables that produced it, the local context
it was measured against, and the nearest VSX association. An astronomer who sees
"anomalous mainly in `u-g`, 1.4 mag from the local mean, with `sloan_u_err_mag`
= 0.31 and 2 u-band observations" can dismiss it in five seconds. That is the
correct outcome, and it only happens if the evidence travels with the score.

### 1.13 Long-term potential

Its likely destination is not a rare-object discovery system — Gaia, ZTF, LSST
and their dedicated anomaly programmes occupy that ground with better data. Its
likely destination is a **catalog-integrity system**: the thing Skycat runs
before activating a release, that says "this release has a problem at these
coordinates in this magnitude range." That is less glamorous, genuinely novel
for a local catalog store, and directly serves what Skycat exists to do. If
Gaia and infrared photometry arrive, the discovery framing reopens on much
firmer ground.

---

## 2. Semantic astronomical image and archival similarity search

### 2.1 Purpose and scientific problem

The stated problem is real: archival astronomy is retrieval-limited. Millions of
frames exist that nobody will ever look at, and the only handles on them are
coordinate, time, filter, and whatever the observer typed in the target field.
"Find me images that look like this" is not expressible in that vocabulary, and
neither is "find me the frames where something odd is happening."

But the brief bundles two capabilities under one name, and they have completely
different answers.

**Capability A — archival coverage and precovery.** "Which archived
observations cover this coordinate, before this date, deep enough in this
filter to have detected a source of this brightness?" This is the question that
matters most scientifically, because it is the precovery question, and **it
contains no machine learning at all.** It is a spatial-temporal query over image
footprint polygons with a depth constraint — which is to say it is a PostGIS
problem, and PostGIS problems are precisely Skycat's competence. The pieces
needed are footprint geometry derived from each frame's WCS, a timestamp, a
filter, and a limiting magnitude. Skynet's field calibration already persists
`limmag5_mag` per image, so the depth constraint is answerable from data that
exists today. Once the covering frames are identified, the actual scientific
answer — was the source there or not — comes from forced photometry at a fixed
position, which is deterministic, is what `skylib` already does, and gives an
upper limit when there is no detection. An upper limit from forced photometry is
a publishable measurement. A similarity ranking is not.

**Capability B — visual/morphological similarity.** "Find objects that look like
this galaxy." This is genuinely a representation-learning problem, it has
credible published precedent, and it is the part with no near-term home in
Skycat.

Conflating them is the main risk in this proposal, because A is cheap, decisive,
and unglamorous, while B demos beautifully and delivers little. If only one
thing from this project is built, it must be A.

**What ML genuinely adds, where it adds anything.** Morphology is not in any
catalog Skycat holds and is not reducible to columns. Two interacting galaxies
with identical integrated photometry are the same row and different objects.
Learned representations are the only practical way to make "shape" queryable at
scale, and the literature shows it works — Stein et al. (2021) built
similarity search over ~42M DECaLS galaxies with self-supervised representations;
Walmsley's Zoobot line does morphology at Galaxy Zoo scale. Both are real
results. Both were trained on **one homogeneous survey**, and that is the crux of
§2.7.

**Where conventional methods remain better.** Anything with a catalogued
answer. Galaxy Zoo morphologies, catalogued Sérsic indices, concentration and
asymmetry statistics, and published class lists are all superior to a learned
similarity when they exist, because they are measurements with definitions.

### 2.2 How it would fit into Skycat

Uncomfortably, and this deserves a direct answer rather than a diplomatic one.

Skycat is a store of *reference catalogs*: static, curated, versioned,
externally published, checksummed against an upstream snapshot. An image archive
is the opposite in every respect — it grows continuously, it is
observatory-generated, it has no upstream to checksum, and its unit is an
observation rather than an astronomical object. Putting it into the same package
would require reopening what the package is for, and probably a decision record
against [decision 0001](../decisions/0001-postgresql-postgis-only.md)'s framing.

There is, though, a shape that fits. Skycat's genuine strength is that it is
the place in the Skynet ecosystem that knows how to index the sky. A
**footprint-coverage index** — one polygon per archived frame, with time,
filter, and depth — is spatially indexed sky data, is queried exactly the way
cone search is queried, and is the natural extension of what already exists. It
would be a new *kind* of family (observation coverage rather than object
catalog), and that distinction should be made explicit rather than smuggled in.

The embedding search is a different service. Its data (cutouts), its storage
profile (dense vectors at frame scale), its hardware (GPU), and its lifecycle
(continuous, not release-based) all disagree with Skycat's design. It should
consume Skycat, not live in it.

The honest user-facing sequence, if all of it existed:

1. *Skycat, deterministic:* which frames cover this position, before this date,
   in a filter and to a depth that could have detected magnitude 19?
2. *Pipeline, deterministic:* forced photometry at that position on each frame →
   a light curve of detections and upper limits.
3. *ML, optional:* rank the remaining frames by whether they contain something
   visually anomalous, for a human to look at.

Step 3 is the interesting demo. Steps 1 and 2 are the science.

### 2.3 Inputs and scientific evidence

Almost none of this exists inside Skycat.

| Input | Why relevant | Where it is |
|---|---|---|
| WCS-derived footprint polygons | The entire basis of coverage and precovery search | Skynet observation assets; not in Skycat |
| Observation timestamp | Precovery is defined by "before discovery" | Skynet |
| Filter | A precovery limit in the wrong band is meaningless | Skynet |
| Limiting magnitude per frame | Turns a non-detection into a scientific upper limit rather than "we saw nothing" | Skynet field-cal (`limmag5_mag`) — already persisted |
| Seeing / PSF FWHM | Governs whether morphology is resolvable at all; the dominant nuisance parameter for similarity | Skynet |
| Pixel scale, field of view | An embedding that ignores angular scale compares a 2′ galaxy to a 20′ one | Skynet instrument metadata |
| Image cutouts | The actual model input | Would require extraction and storage at scale |
| Instrument identity | Needed to *measure* domain shift, and to train against it | Skynet |
| Existing morphological catalogs | The only available supervision and evaluation signal | External (Galaxy Zoo, DECaLS, HSC) |

**This project requires Skycat to expand into an entirely new data type.** That
is not a detail; it is the project.

### 2.4 Expected output

For capability A: a list of frames with coverage geometry, epoch, filter, and
depth. Fully deterministic, fully explainable, publishable as a table.

For capability B: ranked neighbours in a learned space, with a distance. The
distance means *these two cutouts are nearby under this model*. It does not
mean the objects are physically similar, does not mean they are the same class,
and is not comparable across model versions. The failure mode is specific and
well-attested: the nearest neighbours of a cutout are frequently images from the
same telescope on the same night with the same seeing, because that is the
strongest signal in the pixels.

### 2.5 Example scientific use cases

- **Precovery for a Skynet transient.** A supernova is discovered; the question
  is whether Skynet imaged that field in the preceding months and to what depth.
  A coverage query plus forced photometry produces pre-discovery detections or
  upper limits, which constrain the explosion epoch and the progenitor. This is
  a real, frequently-needed, publishable result, and — worth repeating — it
  requires no ML.
- **Serendipitous artifact triage across the network.** Find frames containing
  satellite trails, diffraction ghosts, or bad columns. This is the ML
  application with the best labels in the whole project, because operations
  staff already recognise these and per-telescope artifacts are consistent. It
  is also the one that saves real staff time on a robotic network. It is a
  better first image-ML project than galaxy similarity, and it is not in the
  brief.
- **Lens and interacting-galaxy candidate expansion.** Given a handful of
  confirmed examples, retrieve visually similar objects from a survey. This is
  the published use case and it works — on homogeneous survey data.
- **"Was this always here?"** Given a coordinate, retrieve the archival frames
  and let a human compare. Simple, constantly needed, and mostly a coverage
  query with a viewer attached.
- **Teaching morphology.** A student explores what "spiral" means as a
  continuum. Genuinely nice, and needs no scientific validation because nothing
  is claimed.

### 2.6 Benefits

**Immediate** — only from capability A, and only once footprints exist: a
precovery capability the Skynet ecosystem does not currently have in queryable
form, and a large increase in the usable value of the existing archive. This is
the strongest benefit in the project and it is the non-ML half.

**Deferred** — visual search, morphological discovery, artifact triage. All
require an image pipeline, cutout storage, GPU capacity, and curated evaluation
sets. Nothing here is visible in one week, or one quarter.

### 2.7 Drawbacks and scientific risks

This is where the project is weakest, and the weaknesses are technical and
specific.

- **Domain shift is the dominant signal, not a nuisance.** A heterogeneous
  robotic network spans pixel scales, apertures, filter sets, focal ratios, and
  sites. A self-supervised embedding trained across it will organise primarily
  by instrument and observing conditions, because those explain more pixel
  variance than morphology does. The published successes avoided this by
  training within a single survey. Skynet's archive is the opposite of a single
  survey, and this is the central technical risk.
- **Normalization *is* the representation.** Astronomical images are
  high-dynamic-range floating-point arrays. The choice of stretch, background
  subtraction, and clipping determines what the model sees. Two teams making
  different reasonable normalization choices get different similarity spaces.
  There is no neutral choice.
- **Seeing masquerades as morphology.** A compact galaxy in poor seeing and an
  elliptical in good seeing look alike. Without conditioning on PSF, the model
  will confuse them, and the confusion is systematic rather than random.
- **Augmentation choices encode astrophysical assumptions.** Rotation invariance
  is usually right. Scale invariance is usually wrong — angular size is
  physical. Brightness invariance is definitely wrong — it discards the flux.
  Every augmentation is a claim about what does not matter, and each such claim
  is arguable.
- **Similarity has no ground truth.** Evaluation requires curated expert sets,
  which are small, subjective, and biased toward recognisable classes — which
  are exactly the classes that do not need a similarity search.
- **Cost.** Cutout extraction and storage at archive scale, GPU inference, and a
  vector index are a standing operational commitment, not a one-off.
- **The authority problem is severe here.** A grid of visually similar images is
  extremely persuasive to a human viewer and carries no error bars. A similarity
  demo will convince an audience of something it has not demonstrated. This is
  the project with the largest gap between how good it looks and how much it
  proves.

**Deterministic astronomy stays authoritative** for coverage, depth, epoch,
photometry, and any measured morphological statistic. The model's jurisdiction
is triage.

### 2.8 Feasibility

**One-week prototype: Low** — for anything scientifically meaningful about
Skycat. There is no image data in the package, no footprint index, and no
cutout path.

The rating deserves a split, because it is easy to be misled here. A team can
absolutely, in a week, take a public homogeneous cutout set, run a pretrained or
self-supervised encoder over a few thousand galaxies, and build a
strikingly good nearest-neighbour browser. That is a **High**-feasibility
*demo* and a **Low**-feasibility *Skycat prototype*, because it demonstrates
nothing about Skycat's data, Skycat's archive, or the heterogeneity problem that
is the actual difficulty. Presenting the former as evidence for the latter is
the single most likely way this study could be misused.

The genuinely valuable one-week deliverable here is capability A's foundation: a
footprint-coverage design, and a demonstration on a sample of Skynet frames that
coverage-plus-depth queries answer precovery questions. That is not ML, and it
is worth more.

**Short-term:** the coverage index is a real, achievable feature over weeks.
Embedding search is not.

**Production/research grade:** requires solving instrument-invariant
representation learning for a heterogeneous network — an open research problem,
not an engineering task — plus curated benchmarks, storage, and serving.

### 2.9 Difficulty

| Axis | Score | Reason |
|---|---:|---|
| ML / model | **7** | Self-supervised representation learning is well-trodden, but the augmentation design carries the astrophysics and getting it wrong is silent |
| Astronomy / domain | **8** | PSF, depth, artifacts, morphology, precovery semantics, and forced-photometry limits are all expert territory |
| Data | **9** | Skycat has none of it. Footprints, cutouts, WCS, depth, and instrument metadata all have to come from somewhere else |
| Validation | **8** | Similarity has no ground truth; the honest tests are expensive and small |
| Skycat integration | **9** | Conflicts with what the package is. Needs either a charter change or a separate service |
| **Overall research** | **9** | The demo is easy, the science is an open problem |

### 2.10 Data availability

**Requires archival image data** — and, separately, **requires additional
infrastructure**. It is the only proposal that cannot begin at all with what
Skycat holds. Adding Gaia or Pan-STARRS does not help capability B, because the
constraint is pixels, not catalog rows. Pan-STARRS image stacks would help as a
homogeneous *training* corpus, which is a different and useful observation.

### 2.11 Evaluation and scientific success

- **Capability A is verifiable outright.** Take known transients with published
  pre-discovery limits, run the coverage query, and check that the frames and
  depths are found and correct. This is a pass/fail test against ground truth,
  and it is the strongest evaluation available anywhere in this study.
- **Instrument-invariance probe, for capability B.** Take the same object imaged
  by two different Skynet telescopes. Do the two cutouts land near each other,
  or does each land near others from its own instrument? This single test
  decides whether the embedding is astrophysical or instrumental, and it should
  be run before anything else. If it fails, nothing downstream is meaningful.
- **Retrieval against curated sets.** Precision@k for known lens candidates,
  mergers, and planetary nebulae, reviewed by someone who did not build the
  model.
- **Artifact detection is measurable normally** — labels exist, so ordinary
  precision/recall applies. Another reason it is the better first target.

### 2.12 Interpretability

**Important and largely unavailable**, which is a significant mark against the
project. Embedding distances do not decompose into reasons. The available
mitigations — showing the retrieved neighbours (which is inherent), attention or
saliency maps (suggestive, not explanatory), and always displaying the
instrument, filter, seeing, and depth alongside each result so a human can spot
instrumental clustering themselves — are partial. The last one is cheap,
essential, and would be easy to omit.

Skycat's stated preference for deterministic explanation where one is possible
argues strongly for building capability A, where every answer is a geometric
fact, before capability B, where no answer is.

### 2.13 Long-term potential

Capability A's destination is an **archival coverage and precovery service** for
the Skynet network: modest-sounding, genuinely valuable, achievable, and a real
increase in the scientific yield of data already collected. Capability B's
destination is an astronomical visual search engine, which is a legitimate
research direction and one where the community — with homogeneous surveys and
GPU budgets — is better positioned than Skycat is. The defensible long-term
position for Skycat is to be the sky-indexed substrate such a search runs
against, not the trainer of the model.

---

## 3. Probabilistic intelligent catalog crossmatching

### 3.1 Purpose and scientific problem

This is the most scientifically serious proposal in the study, and the one whose
framing needs the most correction.

The problem is unambiguous. Skycat's `batch_crossmatch` returns the nearest
catalog source within a radius. Nearest is frequently wrong, and the ways it is
wrong are well understood:

- **Crowding.** In a globular cluster or the Galactic plane, the expected number
  of unrelated sources within radius *r* is *πr²ρ*. At Stetson's cluster
  densities this exceeds one for radii that are still smaller than APASS's
  effective resolution. The nearest source is then a coin flip, and the API
  reports it with a separation that looks reassuringly small.
- **Proper motion.** Skycat holds no proper motions and no per-source epochs
  (§0.2). A star moving 0.5″/yr has moved 7.5″ between a 2010-epoch APASS
  measurement and a 2025 image — several times a typical match radius. High
  proper-motion stars are also disproportionately the *interesting* ones:
  nearby, low-mass, old, high-velocity. Nearest-neighbour matching does not
  merely fail on them, it fails **selectively against the scientifically
  valuable population**, which is worse than failing at random.
- **Resolution mismatch.** APASS resolves a few arcsec; Stetson's cluster
  photometry resolves far better. One APASS row legitimately corresponds to
  several Stetson stars. There is no correct one-to-one answer, and the current
  API cannot express that.
- **Asymmetry.** The nearest catalog source to input *A* may have some other
  input as *its* nearest. Skycat's batch path never checks (§0.4).
- **Photometric incompatibility.** A candidate 3 magnitudes brighter than
  expected is almost certainly the wrong source, however close it sits. Position
  alone throws this evidence away.

**The correction: this is mostly not machine learning.** The problem has a
mature and well-founded statistical treatment — Sutherland & Saunders (1992) for
the likelihood-ratio formulation, Budavári & Szalay (2008) for symmetric
Bayesian matching, and NWAY (Salvato et al. 2018) for the multi-catalog case
with magnitude priors. These produce posteriors that are **calibrated by
construction** from an astrometric likelihood and a source-density prior, and
they are transparent: every term corresponds to a stated astronomical
assumption. A learned classifier trained to imitate them would be strictly worse
— less interpretable, needing labels that do not exist, and calibrated only
empirically.

**Where ML genuinely earns its place** is narrow, real, and worth doing:

1. **Learning the photometric prior.** The probability of a counterpart's
   magnitude given the primary's, *p(m_B | m_A)*, is an empirical density with
   real structure (stellar loci, saturation, survey limits) that nobody wants to
   write down parametrically. Estimating it flexibly from matched data is a
   legitimate density-estimation problem and directly improves the posterior.
2. **Learning astrometric error inflation.** Quoted catalog errors are
   optimistic in ways that depend on magnitude, crowding, and band. Learning the
   inflation factor from the empirical distribution of separations between
   confident matches is a real, checkable modelling task.
3. **Learning the "no counterpart" prior** — completeness as a function of
   magnitude, band, and density. This is the term most often gotten wrong, and
   it determines whether "no reliable match" is ever returned.

So the correct scoping is: **build the Bayesian core, learn only the priors.**
That is defensible to an astronomer, and it is what a referee would ask for.

### 3.2 How it would fit into Skycat

More naturally than any other proposal, because the deterministic candidate
generation already exists and is exactly what should generate candidates. PostGIS
finds everything within a radius using the GiST index; the probabilistic layer
then ranks and scores those candidates. Spatial search stays authoritative and
unchanged, which is the brief's own stipulation and also the right design.

The user-visible change is that a crossmatch stops returning "the match" and
starts returning *the evidence about the candidates*: each candidate with a
posterior, an ambiguity measure, and — as a first-class outcome — the
possibility that none of them is reliable. This is a **new output shape rather
than a new search**, which is why it fits so cleanly: it enriches an existing
operation instead of adding a subsystem.

Per C3, the deterministic `nearest_only` behaviour must remain exactly as it is.
The probabilistic path is additive. Per C2, the model version (and the priors it
encodes) must be recorded with any stored result.

An important architectural consequence emerges once Gaia is in scope. Pairwise
matching between *N* catalogs is an *N²* problem, and every pair needs its own
error model. Matching every catalog to **Gaia as an identity spine** is an *N*
problem, and Gaia is the right spine: sub-milliarcsecond positions, five-parameter
astrometry, and near-completeness over the magnitude range Skycat's other
families occupy. If Skycat ever ingests Gaia, "match through Gaia" should be the
default architecture rather than an optimisation, and that decision is worth
making before three more pairwise matchers are written.

### 3.3 Inputs and scientific evidence

| Input | Why it matters | Available? |
|---|---|---|
| Angular separation | The primary evidence | Yes |
| Per-source positional uncertainty | Converts separation into significance. A 2″ separation is decisive for Gaia and meaningless for a photographic catalog | APASS only |
| **Epoch** | Without it, separations mix real offsets with time-baseline drift | **No** |
| **Proper motion** | The correction that makes cross-epoch matching possible at all | **No** |
| Local source density | Sets the prior odds of a chance alignment; the single most important non-positional term | Computable, not stored |
| Magnitudes and colours | Photometric compatibility resolves cases position cannot | Yes |
| Variability (VSX, Stetson index) | A large magnitude discrepancy is expected for a variable and damning for a constant star. Without this the photometric prior mislabels every variable | Yes |
| Object type | Priors differ by class | Partial (VSX only) |
| Catalog resolution and completeness | Determines whether one-to-many is legitimate and whether "no match" is plausible | Documented, not modelled |

**Epoch and proper motion are the binding constraint**, and they are absent.
This is the finding that should most change planning: a probabilistic matcher
built today cannot correct for the effect that causes its most damaging and most
selectively-biased errors. It will be better than nearest-neighbour — the
density prior and photometric compatibility alone are worth a great deal — but
it will remain blind to high proper-motion stars.

**Gaia changes this completely**, and it is worth being concrete about how much:
positions two to three orders of magnitude more precise than APASS's,
five-parameter astrometry for over a billion sources, and a reference frame all
other catalogs can be transported into. Every limitation in the preceding
paragraph is a Gaia-shaped hole. Pan-STARRS, 2MASS, Tycho-2, and UCAC5 each add
value — wavelength coverage, an old astrometric epoch that lengthens proper-motion
baselines — but Gaia is categorical rather than incremental.

### 3.4 Expected output

Per input: a ranked candidate list, each with a posterior probability; an
ambiguity measure (how much better the best candidate is than the second); and
an explicit **"no reliable counterpart"** outcome with its own probability.

The multi-hypothesis structure matters more than it appears to. Probabilities
must be normalised over *{candidate 1, …, candidate k, none}*, and the "none"
term requires a completeness prior. Omitting or mis-setting it is the most
common way these systems mislead, because it forces the probability mass onto
candidates and manufactures confident matches out of fields that contain no
counterpart at all.

**What a posterior of 0.95 means:** given this astrometric error model, this
density prior, and this photometric prior, 95% of the posterior mass falls on
this candidate. **What it does not mean:** that 95 of 100 such matches are
correct *unless calibration has been demonstrated on that population* — priors
transfer badly between density regimes, and a model calibrated in the halo will
be overconfident in the plane. It is also not a statement that the physical
object is the same, only that the associations are consistent.

The improvement over the current API is substantial and concrete. Today a caller
gets a row and a separation and must decide alone. With this, a caller can
distinguish "one obvious counterpart," "two candidates and no way to choose,"
and "nothing believable here" — and those three cases warrant three different
scientific responses that are currently indistinguishable.

### 3.5 Example scientific use cases

- **Field calibration in a crowded field.** Skynet's calibration matches
  detected sources to APASS. In a cluster field, ambiguous matches inject wrong
  reference magnitudes into the zero-point fit. An ambiguity score lets the
  pipeline *drop the ambiguous ones* rather than trusting the nearest. This
  directly addresses the crowded-field failure documented in the NGC 5286
  zero-point parity investigation, and it is the most immediately valuable
  application in the entire study after Project 4.
- **Is this transient's host also a known variable?** A VSX source 2″ from a new
  transient may be the same object or an unrelated star. The answer changes the
  classification. A posterior with an explicit alternative is the right output;
  a nearest-neighbour row is not.
- **APASS ↔ Stetson in globular clusters.** A deliberately hard, deliberately
  well-understood test case where blending is guaranteed and the correct answer
  is frequently one-to-many. If a matcher cannot express that, it is not ready.
- **Building a merged reference catalog.** Any future multi-catalog reference
  layer is a crossmatch problem end to end. Propagating match probabilities
  rather than committing to hard associations is the difference between a
  defensible merged catalog and an untraceable one.
- **Auditing an existing pipeline.** Re-run historical matches probabilistically
  and find the ones that were ambiguous. Some fraction of past photometry rests
  on coin flips, and knowing which is a real reproducibility gain.

### 3.6 Benefits

**Immediate**, on existing data: measurably better matching in crowded fields
via the density prior and photometric compatibility; ambiguity reporting that
lets consumers make their own decisions; a direct improvement path for Skynet
field calibration; and the deterministic fixes in §0.4, which are prerequisites
anyway.

**Deferred**, requiring Gaia: proper-motion-aware matching, a shared identity
spine, and the correct handling of the high-proper-motion population. Also
deferred: any use of this as the basis of a merged multi-catalog product.

### 3.7 Drawbacks and scientific risks

- **A number that looks calibrated and is not.** This is the central risk, and
  it is worse than in Project 1 because a posterior *is* a probability and will
  reasonably be treated as one. Priors fitted in one density regime are wrong in
  another; a matcher tuned on Stetson clusters will be badly overconfident in
  the halo, and nothing in the output will say so.
- **The "no match" prior is the hardest term and the easiest to fudge.** Getting
  it wrong manufactures matches.
- **Circularity in evaluation.** The obvious way to validate is against
  Gaia-mediated matches — but if Gaia is the arbiter, the evaluation cannot
  measure performance where Gaia is incomplete, which is precisely the crowded
  and faint regimes where matching is hardest.
- **Ground truth barely exists.** Outside injection tests and a small number of
  well-studied fields, nobody knows the right answer.
- **Cost at scale.** Multi-candidate retrieval plus per-pair scoring over 128M
  rows is far more expensive than nearest-neighbour, and the current query path
  is tuned for the cheap case.
- **Contract creep.** `CatalogReader.crossmatch()` is a stable surface. Growing
  it carelessly, or changing what `nearest_only=True` returns, breaks callers
  that cannot be refactored by search-and-replace.
- **Overreach.** If the probabilistic path becomes the default, downstream
  systems inherit a statistical model they did not choose and may not
  understand. It must be opt-in.

**Deterministic astronomy stays authoritative** for the candidate set itself —
what lies within a radius is a geometric fact and must never be model-mediated —
and for any match already established by a published catalog cross-identification.

### 3.8 Feasibility

**One-week prototype: High**, with a scoping caveat that matters. The
likelihood-ratio formulation over APASS × Stetson × VSX is implementable and
evaluable in a week, and the globular-cluster fields provide a stress test where
nearest-neighbour demonstrably fails, which makes for an unusually honest
demonstration. Injection-recovery gives a real quantitative result in the same
week.

The caveat: the week produces a **statistical** prototype, not a machine-learned
one, and the deliverable should say so. The learned priors are a later increment.
A team that spends the week training a classifier instead will produce something
less useful and less defensible.

**Short-term:** a genuinely useful feature over weeks to months — ambiguity
scores and multi-candidate output, consumed by Skynet field calibration.

**Production/research grade:** requires Gaia, per-catalog astrometric error
models validated against data, demonstrated calibration across density regimes
(reliability diagrams per regime, not one global curve), and injection-recovery
benchmarks that cover crowding and proper motion. This is a serious programme
and it is the one most likely to produce something citable.

### 3.9 Difficulty

| Axis | Score | Reason |
|---|---:|---|
| ML / model | **5** | The core is Bayesian statistics, which is easier to get *right* than ML but unforgiving of sloppiness. The learned priors are modest models with subtle calibration requirements |
| Astronomy / domain | **8** | Astrometric error models, epoch propagation, blending, resolution mismatch, and per-catalog systematics are expert territory throughout. The highest domain load in this study |
| Data | **6** | Works on existing data but is starved without epochs and proper motions |
| Validation | **6** | The best-off of the five: injection-recovery and reliability diagrams give real quantitative answers, tempered by the Gaia-circularity problem |
| Skycat integration | **5** | Extends an existing operation cleanly; the risk is stable-API creep and query cost |
| **Overall research** | **7** | Well-posed, well-founded, and demanding |

### 3.10 Data availability

**Can begin with existing Skycat data, but requires additional catalogs to
reach its potential.** APASS × Stetson × VSX is enough to build and evaluate a
prototype and to deliver real value to field calibration. Gaia converts it from
a useful feature into an identity service.

### 3.11 Evaluation and scientific success

- **Injection-recovery.** Displace known sources by realistic astrometric errors
  and proper motions, re-match, and measure completeness and reliability as a
  function of density and separation. The most decisive test available, and it
  needs no external truth.
- **Reliability diagrams, per density regime.** Of pairs assigned p ≈ 0.9, are
  ~90% correct — in the plane, in the halo, and in clusters separately? A single
  global curve hides the failure that matters.
- **Gaia-mediated cross-validation**, with the circularity caveat stated.
- **Downstream effect.** Does using ambiguity to drop uncertain matches reduce
  zero-point scatter on crowded fields? This is measurable against Skynet's
  persisted `zero_point_slop_mag` and `rejection_pct`, and it is the test that
  would convince a pipeline maintainer.
- **Self-consistency.** Matching A→B and B→A must agree. Disagreement rate is a
  free, continuous health metric.

Success is a calibrated posterior in every density regime and a demonstrated
improvement in a downstream measurement — not a higher match rate. A higher
match rate is trivially achievable by being wrong more confidently.

### 3.12 Interpretability

**Essential, and fully achievable — which is the strongest argument for the
Bayesian formulation over a learned one.** Every term decomposes: the astrometric
likelihood, the density prior, the photometric prior, the completeness term. A
posterior can be presented as "position favours this candidate 12:1, photometry
disfavours it 3:1, local density is high enough that a chance alignment at this
separation is expected once per 40 sources." An astronomer can audit every one
of those and disagree with any of them.

Every returned match must carry: separation, both positional uncertainties, the
separation to the next-best candidate, local density, the photometric residual,
and the prior assumptions in force. That set is enough to independently redo the
judgement by hand, which is the correct standard.

### 3.13 Long-term potential

An **intelligent cross-catalog identity service**: the layer that says which
rows across every family refer to the same physical object, with quantified
uncertainty. That is genuine infrastructure. It is what makes a multi-catalog
Skycat coherent rather than a collection of tables, it is a prerequisite for
Project 5, and the calibration work needed to do it properly across surveys and
density regimes is publishable in its own right. Of the five, this has the
highest ratio of scientific substance to novelty-of-technique — which is usually
the sign of a good project and always the sign of an unglamorous one.

---

## 4. Scientific-quality and calibration-star prediction

### 4.1 Purpose and scientific problem

A photometric zero point is only as good as the reference stars it is fitted
against. Skynet's field calibration queries APASS for a footprint, drops known
VSX variables within 5″, matches, photometers, applies an SNR floor, and solves
robustly — and the robust fit's job is largely to survive reference stars that
should not have been used. `rejection_pct` is a measurement of how often that
happens.

The current selection is a stack of independent thresholds: an error cut, an
observation-count cut, a variability exclusion, a magnitude window. Thresholds
have two well-known failure modes and both bite in practice.

- **They are conjunctive and brittle.** A star with an excellent error and two
  observations fails one cut; a star with three observations and a mediocre error
  passes them all. The thresholds cannot express that the first is better.
- **They collapse in sparse fields.** At high Galactic latitude, in a small
  field, or at the bright end, applying every cut can leave three usable stars —
  or none. The pipeline then either fails or silently relaxes a cut. What is
  actually wanted at that point is a *ranking*: use the best available and
  report how good "best available" was. A threshold cannot do that, and this is
  the clearest case in the entire study where ML is genuinely better than the
  incumbent method rather than merely different.

**What ML contributes** is a single calibrated ordering over heterogeneous
evidence, with a predictive uncertainty, learned from how stars actually
performed rather than from what a threshold assumed. It can capture
interactions — that quoted errors are underestimated in a magnitude-dependent
and crowding-dependent way — that no independent cut expresses.

**The critical correction, and the most important claim in this section:**
calibration suitability is a property of the **(star, image)** pair, not of the
star. Saturation depends on exposure time and aperture. The SNR floor depends on
depth. Isolation depends on the seeing that night. Whether a colour is usable
depends on the filter being calibrated. A model that returns a single "this star
is good" number for a star, independent of the image, is answering a question
nobody asked and will be wrong in a way that varies by telescope.

The correct division of labour follows directly and defines the API:

- **Skycat returns an intrinsic reliability prior** — is this star's photometry
  internally consistent, well-observed, non-variable, of a well-behaved colour,
  and in agreement with independent catalogs? These are properties of the
  catalog row and are image-independent.
- **The pipeline applies image-conditional constraints** — saturation, SNR,
  crowding at the achieved seeing, chip position. These require the image and
  belong where the image is.

This split is what keeps the feature honest, keeps Skycat free of a Skynet
dependency (C4), and happens to be the design an experienced pipeline engineer
would ask for.

**Where conventional methods remain better, and this must be said first:** a
well-constructed deterministic rubric already captures most of the available
value, is fully explainable, is reproducible forever, and needs no validation
beyond the reasoning behind each term. The right way to build this project is to
**build the deterministic rubric first and treat it as the thing that ships**,
then require any model to beat it measurably on held-out zero-point scatter
before it replaces anything. If the model does not beat it, the rubric was the
deliverable, and that is a success, not a failure.

### 4.2 How it would fit into Skycat

As a per-star score returned alongside catalog rows, with an optional ordering
by it — an enrichment of existing rows, not a new search. A caller asking for
calibration stars in a field would receive the same rows in the same shape, plus
a suitability score and the evidence behind it, optionally ordered best-first.

This is the tightest fit of the five. It changes no query semantics, adds no new
data type, respects C3 completely (it ranks, never filters), and its consumer
already exists and is asking a question it currently answers with thresholds.

Per C1, the score is release-scoped: APASS DR6 and DR10 have different
photometry for the same stars, so the score is a property of the row in a
release, not of the sky position. Per C2, the model version is recorded with any
stored score, because a zero point derived from model-ranked stars is only
reproducible if the ranking is.

### 4.3 Inputs and scientific evidence

Almost everything needed is already in the database, which is why this is the
recommended first project.

| Input | Why it matters | Available? |
|---|---|---|
| Per-band photometric error | The direct statement of measurement precision | Yes |
| Observation counts, per band and total | Distinguishes a well-averaged magnitude from a single measurement; also the basis for judging whether the quoted error is trustworthy | Yes (APASS `n_obs_total` and per-band in `extra`; Stetson per-band; Landolt `n_obs`/`n_nights`) |
| Nights observed | Separates repeat measurements within one night from independent nights — an important distinction for detecting slow variability | Landolt only |
| VSX association | Known variables are disqualifying; this is already how Skynet does it | Yes |
| Stetson `variability_index`, `variability_weight` | A quantitative variability statistic, not a binary flag. Catches low-amplitude variables VSX has never listed — the population that most quietly damages a zero point | Yes (Stetson only) |
| Stetson `chi`, `sharp` | DAOPHOT goodness-of-fit and PSF-shape parameters. `sharp` deviations flag blends and non-stellar sources directly. These are the closest thing to a quality label in the database | Yes (Stetson only) |
| Magnitude | Bright stars saturate, faint stars are noisy — but the usable window depends on the image, so this is evidence, not a cut | Yes |
| Colour | Colour terms and transformation validity. A star far outside the colour range where a transformation was fitted is unusable regardless of its errors | Yes |
| Cross-catalog agreement | An APASS star that agrees with Stetson to 0.01 mag has been independently confirmed. Disagreement is the single most informative available signal, because it is the only one not produced by the catalog judging itself | Yes, where families overlap |
| Local source density / isolation | Blending is the dominant cause of catalog photometric error in crowded fields | Computable |
| Historical fit residuals | Empirical performance: did this star get rejected by the robust fit, and by how much? | **Skynet-side** — see below |

**The label source is real and it is unusually good.** Skynet's field
calibration now persists a per-source match table (`match_sources`) alongside
`zero_point_slop_mag`, `rejection_pct`, `n_calibration_sources`, and
`limmag5_mag`. Aggregated over many images, that yields, for each catalog star,
how often it was rejected and how large its residuals were. That is an empirical
reliability measurement with direct astronomical meaning, and it is a far better
supervision signal than anything the other four projects have access to.

Two caveats, both important. First, per C4, these labels are for **offline**
fitting and validation; the shipped artifact must not call into Skynet. Second,
the label is a joint property of star, image, and pipeline: a star that scatters
on a 0.4 m telescope in poor seeing may be excellent on a larger aperture. The
label must therefore be conditioned on the observing context or aggregated
across enough contexts to average it out. Treating raw residuals as a pure star
property will bake in the biases of whichever telescopes contributed most data.

### 4.4 Expected output

A score, a category, and the evidence — all three, not one.

- **A continuous suitability score** for ranking, which is what sparse fields
  need.
- **A small number of coarse categories** (for example: standard / reliable /
  usable / avoid) because a continuous score invites false precision and most
  consumers want a decision.
- **The evidence vector**: the photometric error, observation counts, VSX
  status, cross-catalog residual, isolation, and colour — the physical reasons.
- **A predictive uncertainty**, which matters most exactly where the score is
  based on thin evidence.

**What the score means:** *based on catalog evidence alone, this star's
photometry is expected to be internally reliable for calibration purposes.*
**What it does not mean:** that it is unsaturated in your image, detectable at
your depth, isolated at your seeing, or valid in your filter. Those are the
pipeline's determination, and the documentation must say so in those words, or
someone will eventually calibrate a bright-star field against stars the model
liked and the image saturated.

The improvement over current practice is concrete: today a pipeline applies
thresholds and gets a set; with this it gets an ordering, a confidence, and a
reason — and in a sparse field it gets a usable answer instead of a failure.

### 4.5 Example scientific use cases

- **A sparse high-latitude field with four APASS stars.** Thresholds leave one
  or zero. A ranking says "use these three, expect ~0.04 mag scatter, the fourth
  disagrees with Stetson by 0.12 mag." The observation gets calibrated with an
  honest uncertainty instead of failing. This is the case that justifies the
  project on its own.
- **A crowded cluster field.** Blended APASS sources carry good formal errors
  and bad photometry. Isolation and cross-catalog disagreement catch what error
  cuts cannot — and this is the documented crowded-field failure mode from the
  NGC 5286 zero-point investigation.
- **Undetected low-amplitude variables.** VSX is incomplete. Stetson's
  variability index catches sub-threshold variables in cluster fields directly;
  learning what such stars look like in APASS's own observables (error inflation
  relative to magnitude, band-to-band inconsistency) extends that protection to
  fields Stetson does not cover. This is one of the clearer genuine ML wins in
  the study.
- **A student's first photometry assignment.** "Why is this star a bad
  calibrator?" answered with reasons rather than a filter that silently removed
  it. Educationally this is strictly better than the current behaviour.
- **Robotic scheduling.** Knowing in advance whether a target field contains
  reliable calibrators is a scheduling input: a field with no good calibrators
  may need a standard-field observation adjacent to it. That is a real
  operational capability for a telescope network.
- **Auditing archival photometry.** Re-score the reference stars used in past
  calibrations and identify which historical zero points rest on stars now known
  to be poor. A reproducibility gain that costs one batch job.

### 4.6 Benefits

**Immediate**, on today's data and with no new catalogs: a defensible ranking to
replace brittle thresholds; graceful behaviour in sparse fields; explicit
reasons a star was avoided; cross-catalog agreement made available as a signal
for the first time; and a direct, measurable improvement path for Skynet field
calibration. This is the only proposal in the study whose primary benefit needs
nothing new.

**Deferred**: Gaia would add a strong isolation and binarity indicator via
astrometric quality and improve the magnitude range; Pan-STARRS would add
independent photometry for cross-catalog agreement outside Stetson's clusters
(currently the agreement signal is only available where families overlap, which
is a small fraction of the sky); accumulated calibration history makes the
empirical labels progressively better, which means the feature *improves with
use* — an unusual and valuable property.

### 4.7 Drawbacks and scientific risks

- **A colour-selective model injects a colour term into the zero point.** This
  is the most serious risk in the project and the one an astronomer will raise
  first. If the model systematically prefers stars in a particular colour range
  — plausibly, because they have smaller errors — then every zero point derived
  from its selections carries a systematic offset that depends on the filter and
  on the science target's colour. It would be invisible in the fit statistics,
  because the fit would look *better*. Colour balance of the selected set must be
  an explicit monitored constraint, not an assumption.
- **The model may not beat the rubric.** Entirely possible, and the project must
  be structured so that this outcome is a clean result rather than an
  embarrassment. Hence: build the rubric first.
- **Overriding standards.** Landolt and Stetson stars *define* the photometric
  system. A model must never rank a Landolt standard below an APASS field star
  on the basis of learned features. Standards are deterministic authority; this
  should be a hard rule in the code, not a training objective.
- **Label bias from the training telescopes.** Residual labels come from
  whichever instruments contributed the most images. A model fitted on them
  encodes their apertures, sites, and typical seeing.
- **Feedback loops.** If the pipeline preferentially uses high-scoring stars,
  future labels are collected mostly for high-scoring stars, and the low-scoring
  population never gets the observations that would correct the model's opinion
  of it. This is self-confirming and needs deliberate exploration to prevent.
- **Hidden reasons.** A star excluded silently is a debugging nightmare. The
  evidence vector is not a nicety; without it the feature is a regression
  against the current explicit thresholds.
- **Scope confusion.** If anyone reads the intrinsic score as an
  image-conditional verdict (§4.1), the feature will be misused. This is a
  documentation and naming problem and it deserves real attention.

**Deterministic astronomy stays authoritative** for: the standards themselves,
saturation and SNR limits, published transformation relations, and the robust
fit. The model chooses *inputs*; it never touches the solve.

### 4.8 Feasibility

**One-week prototype: Very High.** All the primary data is in the database. A
team can reach, in a week: a deterministic composite rubric; a
cross-catalog-agreement analysis over APASS × Stetson overlap regions (a real
scientific measurement, independently interesting); a first ranking model; and
an evaluation showing whether the ranking's top-N reduces scatter relative to
threshold selection. That is a working proof of concept with a genuine
quantitative result, which no other project in this study can claim after a
week.

The schedule risk is access to Skynet's calibration residuals. If they are not
available in time, the week's evaluation falls back to cross-catalog agreement
(does APASS agree with Stetson where they overlap, and does the score predict
disagreement?), which is a weaker but still real test on data entirely inside
Skycat. The project does not stall either way.

**Short-term:** a shippable feature within weeks — scores plus evidence,
consumed by field calibration.

**Production/research grade:** requires validation against a large sample of
real calibrations across multiple telescopes and filters, demonstrated absence
of colour bias in the selected sets, demonstrated stability across releases, and
agreement with Landolt/Stetson standards where they are available. All of this
is achievable with data that exists or accumulates naturally. That is the
strongest maturity path in the study.

### 4.9 Difficulty

| Axis | Score | Reason |
|---|---:|---|
| ML / model | **3** | Tabular ranking/regression with uncertainty. The discipline is in the baseline and the calibration, not the algorithm — well-suited to juniors with supervision |
| Astronomy / domain | **6** | Photometric systems, saturation, blending, variability, and colour-term validity all matter. Real, but bounded and teachable |
| Data | **4** | Primary evidence in hand; labels need an offline path from Skynet |
| Validation | **4** | Genuinely measurable against zero-point scatter and rejection rate. The best-validated proposal of the five |
| Skycat integration | **4** | Fits the enrich-and-rank shape exactly; C1 release-scoping is the only real design question |
| **Overall research** | **4** | The most tractable, with the clearest success criterion |

### 4.10 Data availability

**Can begin primarily with existing Skycat data.** APASS supplies scale, Stetson
supplies quality parameters and a variability statistic, Landolt supplies
standards, VSX supplies the variability exclusion, and family overlap supplies
cross-catalog agreement. Pan-STARRS would extend the agreement signal beyond
Stetson's clusters; Gaia would add isolation and binarity indicators.

### 4.11 Evaluation and scientific success

- **Held-out zero-point scatter.** Calibrate the same images with
  threshold-selected and score-selected stars and compare `zero_point_slop_mag`.
  Unambiguous, quantitative, and directly meaningful.
- **Rejection rate.** A good selector should have fewer of its stars thrown out
  by the robust fit. `rejection_pct` measures this already.
- **Standard-star recovery.** Do Landolt and Stetson standards score highly
  without being told they are standards? A selector that ranks a Landolt star
  poorly is broken, and this is a free, decisive sanity check.
- **Cross-catalog residual prediction.** Does a low score predict APASS–Stetson
  disagreement on held-out overlap regions? Entirely inside Skycat, needs no
  pipeline data.
- **Colour balance audit.** Is the colour distribution of selected stars
  consistent with the parent population? This must be a standing check, not a
  one-off.
- **Sparse-field behaviour.** Does the ranking degrade gracefully where
  thresholds fail outright? The motivating case, and it must be measured.
- **Expert review.** A photometrist inspecting a sample of high- and low-scored
  stars should agree with the reasoning. If they cannot follow it, the evidence
  vector is inadequate.

Success is a measured reduction in zero-point scatter, no colour bias, and
agreement with standards — and, importantly, the deterministic rubric is the
baseline that must be beaten, not the threshold stack.

### 4.12 Interpretability

**Non-negotiable, and fully achievable.** A calibration decision propagates into
every magnitude derived from the image. An astronomer must be able to see why a
star was preferred, and every score must be accompanied by its physical
evidence.

There is a stronger claim available here: because a deterministic explanation is
almost always possible for this problem, the deterministic rubric should be
retained permanently as a *presentation layer* even if a model does the ranking.
"Ranked 3rd; error 0.021, 8 observations, no VSX match within 10″, agrees with
Stetson to 0.008 mag, nearest neighbour 24″" is the useful output. The model's
contribution is the ordering; the reasons are the product. This is Skycat's
stated preference for deterministic explanation applied literally, and this
project is where it costs nothing to honour.

### 4.13 Long-term potential

An **automated calibration advisor** for the Skynet network: a service that,
given a field, filter, and instrument, reports which stars to use, how good the
resulting calibration should be, and whether a standard field is needed instead.
Combined with the coverage index from Project 2's deterministic half, it becomes
a scheduling input rather than a post-processing step.

Its research contribution is modest but real — an empirical study of what
actually makes a catalog star a good calibrator, across many telescopes and
filters, is a useful and citable technical result, and nobody has published it
for a heterogeneous robotic network. It is not a breakthrough. It is the kind of
thing that quietly improves every photometric measurement a network produces,
which is worth more than it sounds.

---

## 5. Multimodal astronomical object representation and discovery

### 5.1 Purpose and scientific problem

The aspiration is coherent and, in the abstract, correct: an astronomical object
is not a row, it is everything ever observed about it — photometry across
wavelengths, astrometry, a light curve, a spectrum, an image, a classification
history. Every catalog is a projection of that, and every projection loses
information. A learned representation combining modalities could in principle
support "find objects like this one" in a sense that means *astrophysically*
like, rather than "nearby in one colour-colour diagram."

The precedent is real. AstroCLIP (Parker et al. 2024) aligns galaxy images and
spectra into a shared space and shows the resulting embeddings support retrieval
and property estimation without task-specific training. The Multimodal Universe
effort assembles paired astronomical data at scale for exactly this. These are
credible results, not vapour.

**But the problem this would solve is not one Skycat currently has**, and this
is the decisive point. Skycat holds one modality: tabular photometry from
static reference catalogs. It has no images, no light curves, no spectra, and no
paired data of any kind. A multimodal representation over one modality is a
representation, and the word "multimodal" is doing no work.

**Where conventional methods remain better** covers almost the entire near-term
space. Colour-colour and colour-magnitude diagrams, period-luminosity relations,
spectral classification, and SED fitting are interpretable, physically grounded,
and centuries-deep. A learned embedding beats them only where the relationship
is genuinely unknown and the data volume genuinely exceeds human capacity —
which is a real situation in modern surveys, and not the situation of a
128M-row photometric catalog with no time domain.

### 5.2 How it would fit into Skycat

It would not, in any near-term form, and the strategically useful conclusion is
about what role Skycat *should* play rather than whether it should train a model.

Skycat's actual comparative advantage is that it is a **disciplined store**:
versioned releases, checksummed provenance, atomic activation, spatial indexing,
reproducible queries. Those properties are rare and they are exactly what
large-scale representation learning lacks and needs. Almost every published
astronomical embedding effort has a reproducibility problem — which data
version, which preprocessing, which model checkpoint.

So the defensible long-term position is: **Skycat should be the substrate a
multimodal model is trained on and the store its outputs are versioned in — not
the producer of the model.** Storing, versioning, and spatially querying
embeddings produced elsewhere plays entirely to Skycat's strengths. Training a
bespoke foundation model plays to none of them, and would compete with
better-resourced community efforts on their own ground.

If embeddings are ever stored, C1 and C2 apply with full force: an embedding is
a derived, release-scoped, model-versioned artifact, and an embedding whose model
version is not recorded is scientifically worthless within a year.

### 5.3 Inputs and scientific evidence

| Modality | Why it matters | Available? |
|---|---|---|
| Multi-band photometry | The SED shape — temperature, reddening, class | Yes |
| Astrometry (parallax, proper motion) | Distance and kinematics; converts apparent to absolute quantities. Without it, photometry alone is deeply degenerate | **No** |
| Light curves | Period, amplitude, shape — the primary discriminant for most variable classes | **No** (VSX stores summaries only) |
| Spectra | The most information-dense modality; the arbiter of classification | **No** |
| Images / morphology | Extended structure, environment, interaction | **No** |
| Classifications | Anchors and evaluation | Minimal (VSX types) |
| Observation metadata | Needed to model and remove instrumental structure | **No** |

Four of the seven modalities are entirely absent and one is present only as a
summary. **This requires multiple new modalities**, each with its own ingestion,
storage, and validation programme.

### 5.4 Expected output

Embeddings, similarity rankings, and joint-evidence anomaly scores.

**What an embedding distance means:** these two objects are nearby under this
model given the modalities available for both. **What it does not mean:** they
are the same class, physically similar, or comparably distant in any other
model. And a subtlety that matters more than it sounds: two objects with
different available modalities are not comparable at all in a naive
implementation, because the missingness pattern dominates the geometry.

### 5.5 Example scientific use cases

All of these are hypothetical for Skycat, and are listed to show what would have
to exist first.

- **Cross-survey transient analogue search** — "find objects that behaved like
  this one" across surveys. Needs light curves from multiple surveys and a
  crossmatch layer (Project 3).
- **Classification-disagreement discovery** — objects whose images resemble one
  class while their catalog classification says another. This is genuinely
  interesting: it finds either misclassifications or physically unusual objects,
  and either is useful. Needs images and classifications.
- **Joint-evidence anomalies** — unremarkable in every individual modality,
  unusual in combination. The scientifically strongest framing, and the hardest
  to validate.
- **Spectroscopic target selection** — rank objects by how informative a
  spectrum would be. Real and valuable; needs a spectroscopic training set.

### 5.6 Benefits

**Immediate: none.** This should be stated without hedging.

**Long-term, conditional on infrastructure that does not exist:** a unified
discovery interface across modalities; cross-survey transfer; and a genuine
research contribution *if* the survey-bias problem in §5.7 is solved rather than
ignored. All of it is downstream of Projects 3 and 2, and of ingestion
programmes not yet decided on.

### 5.7 Drawbacks and scientific risks

- **Survey selection function dominates the representation.** This is the
  fundamental objection and it applies to every multimodal astronomical
  embedding, not just this one. Training data is not a sample of the universe;
  it is a sample of *what surveys chose to observe*, and targeting decisions are
  functions of the objects' properties. A learned embedding therefore encodes
  *p(observable | selected)*, and similarity in that space is partly "these
  objects were treated similarly by surveys." Distinguishing that from
  astrophysical similarity is a research problem, not a hyperparameter.
- **Structured missingness leaks the answer.** Which modalities exist for an
  object is itself informative — a source with a spectrum was selected for
  spectroscopy, which usually means it was already interesting. A model will
  exploit this, appear to perform well, and have learned the target selection
  function. This failure is *invisible in standard validation* because the same
  leakage exists in the test set.
- **No ground truth for astrophysical similarity.** There is no metric. Class
  labels are a poor proxy, since the interesting objects are precisely those the
  classes fail.
- **Compounded domain shift.** Every problem in §2.7 recurs per modality and
  then again at the alignment step.
- **Cost.** Multi-modal training at scale is a serious hardware and staffing
  commitment, in competition with community efforts that have more of both.
- **Maximum authority risk.** A multimodal similarity result is the most
  impressive-looking and least verifiable output in this study.

**Deterministic astronomy stays authoritative** for essentially everything with
an established method: classification, distances, periods, SED fitting.

### 5.8 Feasibility

**One-week prototype: Very Low.** A team could produce a self-supervised
embedding of APASS photometry and a nearest-neighbour browser, and it would be
neither multimodal nor informative — colour-space nearest neighbours are already
computable in SQL, and the embedding would mostly reproduce them with less
interpretability. That is a negative result dressed as a demo.

**Short-term:** not a feature. The honest short-term deliverable is a *design
note* on how embeddings would be versioned and stored if they ever arrive — which
is genuinely worth writing, because getting C1 and C2 right for derived vector
artifacts is the kind of decision that is cheap now and expensive later.

**Production/research grade:** requires spectra, light curves, images, paired
data, a crossmatch identity layer, GPU infrastructure, and a solution to the
selection-function problem. Multi-year, and only sensible in collaboration.

### 5.9 Difficulty

| Axis | Score | Reason |
|---|---:|---|
| ML / model | **9** | Multimodal contrastive learning with structured missingness at astronomical scale |
| Astronomy / domain | **9** | Requires fluency across photometry, spectroscopy, time domain, and morphology simultaneously |
| Data | **10** | Four of seven modalities absent; paired data absent; acquisition is itself a multi-year programme |
| Validation | **10** | No ground truth, and the dominant failure mode is invisible to standard validation |
| Skycat integration | **9** | Requires Skycat to become something it is not |
| **Overall research** | **10** | A frontier research programme, not a feature |

### 5.10 Data availability

**Requires multiple new modalities.** The most data-dependent proposal by a wide
margin. Gaia and Pan-STARRS would help but not enable it — the binding
constraints are spectra, light curves, and paired observations.

### 5.11 Evaluation and scientific success

The evaluation design is more interesting than the project, because these probes
are the only way to tell a real result from a survey artifact — and they apply
to any embedding Skycat might one day *store*, whoever trained it.

- **Same-object, different-instrument retrieval.** Does the embedding place two
  observations of one object together, or two observations from one instrument
  together? Decisive, cheap, and it should gate everything else.
- **Emergent physical structure.** Does the space recover the main sequence, the
  red clump, the instability strip, or the period-luminosity relation without
  ever being given them? This is the strongest positive evidence available.
- **Held-out survey transfer.** Train excluding one survey; test on it. If
  performance collapses, the model learned the surveys.
- **Missingness ablation.** Does performance survive when the missingness
  pattern is randomised? If not, the model learned target selection.
- **Expert blind review** of retrieved neighbours, by someone who did not build
  it.

Success is not retrieval performance. Success is demonstrating that similarity
survives an instrument change — and no published astronomical embedding has
conclusively shown that across heterogeneous instruments.

### 5.12 Interpretability

**Critical and largely unattainable**, which is the deepest problem with the
proposal. Embedding geometry does not decompose into physical reasons. The
mitigations — probing for known physical quantities, showing per-modality
contributions, always displaying the underlying measurements alongside a
retrieval — reduce opacity without removing it.

For a package whose stated preference is deterministic explanation wherever one
is possible, this proposal is the furthest from that principle. That is not a
reason to never do it. It is a reason not to do it first, and a reason that if
it is ever done, the deterministic evidence must travel with every result.

### 5.13 Long-term potential

Genuinely transformative *if* the field solves the selection-function problem —
a unified discovery space where "similar" means astrophysically similar across
instruments and modalities. It is the most scientifically ambitious idea here.

But the realistic assessment for *this* package is that Skycat is not the
vehicle. The community efforts have the paired data, the compute, and the
survey homogeneity. Skycat's distinctive contribution — versioned, provenanced,
spatially indexed, reproducible storage — is on the infrastructure side, and it
is a real contribution: an embedding you can reproduce a year later is worth
more than a slightly better embedding you cannot.

---

## 6. Cross-project comparison

### 6.1 Scored comparison

Scores are 1–10. Two rows are inverted and marked, because a high score there is
a *cost*: **Difficulty** and **Dependence on new infrastructure**.

| Dimension | 1 Anomaly | 2 Image similarity | 3 Crossmatch | 4 Calibration | 5 Multimodal |
|---|---:|---:|---:|---:|---:|
| Scientific value | 7 | 6 | **9** | 7 | **9** |
| Benefit to Skycat | 8 | 4 | **9** | **9** | 3 |
| Novelty | 5 | 7 | 4 | 5 | **9** |
| Immediate usefulness | 7 | 2 | 6 | **9** | 1 |
| One-week prototype feasibility | 8 | 3 | 7 | **9** | 1 |
| Data availability today | **9** | 2 | 6 | 8 | 1 |
| Difficulty *(higher = harder)* | 6 | 9 | 7 | **4** | 10 |
| Dependence on new infrastructure *(higher = worse)* | **3** | 9 | 5 | **3** | 10 |
| Research / publication potential | 6 | 7 | 7 | 5 | **9** |
| Usefulness to Skynet | 6 | 7 | 7 | **10** | 3 |
| Demo / presentation value | 7 | **10** | 4 | 6 | 8 |
| Long-term transformative potential | 7 | 8 | 8 | 6 | **10** |

Three patterns are worth naming, because they are not obvious from the
individual sections.

**Demo value is anti-correlated with immediate usefulness.** Project 2 scores
10 on demo and 2 on immediate usefulness; Project 3 scores 4 and 6. The most
persuasive thing to show is the least ready thing to build, and the most
scientifically serious thing to build is the least persuasive thing to show.
Any decision made by watching demos will get this portfolio backwards.

**Projects 1, 3 and 4 are one capability wearing three names.** All three answer
"how much should I believe this?" — this row, this association, this star. They
share machinery (per-source evidence over existing catalogs), a design principle
(the evidence travels with the score), and a validation philosophy. Building
them together is substantially cheaper than building them separately, and each
makes the others' evidence more useful.

**Projects 2 and 5 are a different axis entirely** — representation and
retrieval rather than trust. They share the image and multi-modality
dependencies, the interpretability problem, and the domain-shift problem. They
should be funded, staffed, and scheduled as one research line, not as two
features.

### 6.2 Classification

| Project | Category | Reasoning |
|---|---|---|
| **4 — Calibration-star prediction** | **Build immediately** | Only proposal with all its primary data in hand, a waiting consumer, a measurable success criterion, and a deterministic baseline that ships even if the model fails |
| **3 — Probabilistic crossmatching** | **Build immediately** (statistical core) | Highest scientific value; the Bayesian core is buildable now and improves field calibration. Defer the learned priors; fix the deterministic defects in §0.4 first |
| **1 — Anomaly discovery** | **Prototype now, develop later** | Cheap, unsupervised, immediately useful as catalog QA. Its discovery framing needs Gaia and infrared data before it is honest |
| **2 — Image / archival similarity** | **Architect for now** | The deterministic footprint-coverage half is valuable and should be designed now; the embedding half is long-term research and belongs in a different service |
| **5 — Multimodal representation** | **Long-term research** | No data, no near-term consumer, no validation methodology. Worth one design note on how derived vector artifacts would be versioned — nothing more |

A caveat on Project 2's category, since it straddles: **the deterministic half
is "architect for now" and the ML half is "long-term research."** Splitting them
in the plan is the whole point, because funding them as one item guarantees the
cheap valuable half is delayed by the expensive speculative half.

---

## 7. What one week actually buys

Assume one experienced technical/research lead and several junior developers,
one intensive week. The distinction that matters is maturity level, and these
five terms are used precisely throughout:

| Level | Meaning |
|---|---|
| **Working proof of concept** | It runs on real data and produces plausible output. No claim about correctness |
| **Convincing scientific demonstration** | Output has been checked against something with a known answer; a domain expert agrees the result is real |
| **Useful development feature** | A developer or pipeline can use it and get value, with known limitations |
| **Validated scientific tool** | Quantitatively evaluated on held-out data with characterised failure modes; an astronomer can cite results derived from it |
| **Production-quality system** | Validated, plus versioned, monitored, documented, reproducible, and supported |

### 7.1 Realistic one-week maturity

| Project | Attainable after one week | Explicitly not attainable |
|---|---|---|
| **4 Calibration** | **Convincing scientific demonstration**, approaching a useful development feature. Deterministic rubric + cross-catalog agreement analysis over APASS × Stetson + a ranking model + a measured comparison against threshold selection | Validation across telescopes and filters; any claim of no colour bias; production integration |
| **3 Crossmatch** | **Convincing scientific demonstration** of the Bayesian core, evaluated by injection-recovery, demonstrated on Stetson cluster fields where nearest-neighbour visibly fails | Calibration across density regimes; proper-motion handling (impossible without new data); learned priors |
| **1 Anomaly** | **Working proof of concept**, plausibly a convincing demonstration if scoped to Stetson clusters where blue stragglers give a known-answer target | Any claim that top-ranked APASS outliers are astrophysically rare |
| **2 Image similarity** | For the deterministic half: a **working proof of concept** of footprint-coverage search on a sample of frames. For the ML half: a visually impressive demo on public survey cutouts that demonstrates nothing about Skycat | Anything on Skynet's heterogeneous archive; any instrument-invariance claim |
| **5 Multimodal** | A design note. Attempting a prototype produces a colour-space nearest-neighbour browser that SQL already does better | Everything |

### 7.2 The strongest one-week demonstration

**Project 4 as the deliverable, Project 3's ambiguity score as the second panel,
Project 1 as the exploratory third.** The three share a spine — per-source
evidence over data already in the database, every score accompanied by its
reasons — and together they tell one honest story:

> Skycat can already tell you what is at a coordinate. These three additions
> tell you **how much to trust it**: which stars to calibrate against and why,
> which catalog associations are ambiguous and by how much, and which rows in a
> release do not look like the population they belong to.

That framing is defensible in front of astronomers because every number comes
with the measurements that produced it, every claim is scoped to what was
actually evaluated, and nothing depends on data the project does not have.

Two things this demonstration should deliberately *not* do. It should not
include an image-similarity panel, because it would dominate the audience's
attention while being the least substantiated thing on screen. And it should not
report a single headline accuracy number, because the honest results here are
curves and comparisons, not a figure of merit.

If a visual is required, the right one is Project 3 on a globular cluster field:
a picture of nearest-neighbour matching failing, and the ambiguity score
catching it. That is visually clear, scientifically real, and it demonstrates a
limitation of the current system rather than a claim about a new one.

---

## 8. Final recommendation

### 8.1 Which project should be Skycat's first serious ML capability?

**Project 4 — scientific-quality and calibration-star prediction**, without much
hesitation. It is the only proposal where all four of the following hold: the
primary data is already in the database; a consumer already exists and is
already solving the problem worse; success is measurable against a number the
pipeline already records (`zero_point_slop_mag`); and there is a deterministic
baseline that ships and delivers value even if the model turns out not to beat
it.

That last property is what makes it the right *first* project specifically. A
first ML capability whose failure mode is "we shipped a well-designed
deterministic scoring rubric instead" is a first ML capability worth starting.

### 8.2 Which offers the largest long-term scientific opportunity?

**Project 5**, in the abstract — a genuine cross-modal discovery space would
change how objects are found. But the realistic answer for this package is
**Project 3**, because a calibrated cross-catalog identity layer is both
achievable here and a prerequisite for everything else, including Project 5. An
opportunity that requires infrastructure nobody has committed to is not an
opportunity Skycat has.

### 8.3 Best combination of feasibility and scientific value?

**Project 4**, again. Project 3 has higher scientific value and lower
feasibility; Project 1 has higher feasibility and lower value. Project 4 sits at
the maximum of the product and is the only one of the three with a consumer
waiting.

### 8.4 Most likely to produce meaningful astronomical research?

**Project 3.** Cross-matching heterogeneous catalogs with calibrated posteriors
across density regimes is a real methodological problem, it is evaluable, and
the calibration study alone — reliability diagrams per density regime, injection
recovery, and a demonstrated downstream improvement in zero-point scatter — is a
publishable technical result.

Project 1 could produce research, but only after Gaia and infrared photometry
arrive, and then it competes with dedicated survey anomaly programmes that have
better data. Project 4's research contribution is a solid technical paper rather
than a discovery. Projects 2 and 5 could produce important research; not here,
and not soon.

### 8.5 Most impressive demonstration while still scientifically credible?

**Project 3 on a globular cluster field.** Showing nearest-neighbour matching
producing confident wrong answers where the density makes it a coin flip, and a
posterior with an ambiguity score correctly refusing to choose, is visually
immediate and scientifically airtight — the failure is real, the fix is
principled, and the evaluation is injection-recovery rather than opinion.

Project 2 would be more *impressive*. It would not be more credible; §2.8
explains why the impressive version demonstrates nothing about Skycat.

### 8.6 Which should NOT be prioritized yet?

**Project 5, unambiguously.** No modalities, no paired data, no consumer, no
validation methodology, and a dominant failure mode invisible to standard
validation. Its correct near-term output is one design note on versioning
derived vector artifacts (C1/C2).

**Project 2's ML half should also not be prioritized**, though its deterministic
half should be designed now. The distinction matters: "do not build the
embedding search" and "do not think about images" are different instructions,
and the second one would be wrong.

### 8.7 Which can use current Skycat data immediately?

- **Project 4** — fully. APASS errors and counts, Stetson `chi`/`sharp`/variability
  index, Landolt standards, VSX exclusions, cross-family agreement.
- **Project 1** — fully. Unsupervised, no labels needed.
- **Project 3** — partially. The density prior, photometric compatibility, and
  ambiguity scoring all work today; epoch and proper-motion handling do not
  exist and cannot be approximated.

### 8.8 Which require expansion into new data?

- **Project 2** — archival images, footprints, WCS, per-frame depth, instrument
  metadata. A new data type, not a new catalog.
- **Project 5** — spectra, light curves, images, and paired observations across
  all of them. Multiple new modalities.
- **Project 3** — Gaia, for proper motions and per-source epochs. This is a
  catalog ingestion, which is a well-understood operation for this package, not
  an architectural change. It is by far the cheapest of the three expansions and
  the highest-leverage.
- **Project 1** — Gaia, 2MASS, WISE to move from catalog QA to discovery.

The ordering is worth stating plainly: **ingesting Gaia unlocks more of this
portfolio than any other single action**, and it is the expansion Skycat is
already architecturally prepared for.

### 8.9 Do the five form a coherent strategy?

**Partially.** Projects 1, 3, and 4 form a genuinely coherent programme — a
*quantified-trust* layer over deterministic catalog data, sharing machinery, a
design principle, and a validation philosophy. That programme is well-matched to
what Skycat is and to who uses it.

Projects 2 and 5 belong to a different programme — *representation and
retrieval* — with different data, different infrastructure, different hardware,
and a different scientific culture. Treating all five as one roadmap invites the
predictable failure where the speculative half consumes the resources and the
deliverable half is late.

The strategy becomes coherent if it is stated as two tracks with an explicit
gate: build the trust layer now; do the representation track's *deterministic
prerequisites* now (footprint coverage, identity spine); and open the
representation track properly only when there is an image archive worth
searching and a crossmatch layer to hang identities on.

### 8.10 If one should be replaced, what replaces it?

**Project 5 should be removed from the near-term portfolio** — not deleted, but
demoted from "project" to "posture," per §8.6.

Its slot should go to a sixth idea that is not in the brief and is stronger than
anything it would displace:

> **Project 6 — cross-system photometric transformation with honest
> uncertainty.**

**The problem.** Skynet observes in filters that frequently do not match the
catalog's bands. The reference-magnitude resolver falls back through a preferred
band list (`V, r', g', B, i'`), which is documented as being able to pair a B
image with a V catalog magnitude — a known contributor to field-calibration
divergence, and one that silently biases zero points. The classical fixes are
published colour transformations (Jester et al. 2005, Lupton 2005, and the
Gaia-to-Johnson relations), which are low-order polynomials in a single colour,
fitted on restricted stellar samples. They are known to break for red stars,
metal-poor stars, reddened stars, and emission-line objects, and they are
routinely applied outside the colour range they were fitted in, where they
degrade without warning.

**Why it is the right ML problem.** It is regression with a predictive
uncertainty and an explicit in-domain/out-of-domain determination — a
well-posed, well-understood formulation with unambiguous ground truth
(measured magnitudes) and a clean evaluation (held-out residuals, and whether
the stated predictive intervals actually cover).

**Why it is unusually feasible here.** The training data requires no
crossmatching at all: **an APASS row already contains Johnson B, V and Sloan
g, r, i for the same star**, so it is a paired training set sitting in the
database. Landolt and Stetson supply Johnson-Cousins ground truth on the same
system the pipelines target. No new catalog, no new modality, no labelling.

**Why it matters scientifically.** A transformed magnitude used as a calibration
reference without an uncertainty is a systematic error with no error bar. Making
the transformation uncertainty explicit — and refusing to extrapolate outside
the colour range where it was fitted — directly improves every zero point
derived through a band mismatch, and it composes with Project 4: a star is only
a good calibrator *in the band you actually need*.

**Ratings.** One-week prototype feasibility: **Very High**. Data availability:
*can begin primarily with existing Skycat data*. Difficulty: ML 3, astronomy 6,
data 2, validation 3, integration 4, overall **4**.

**Its principal risk**, stated honestly: interstellar reddening and metallicity
are confounders. A reddened blue star and an intrinsically red star occupy the
same colour but transform differently, and Skycat holds no extinction or
metallicity information. The model must therefore report out-of-domain rather
than extrapolate, and its uncertainty must widen where the training density is
low. A transformation model that quietly extrapolates is worse than the
published relation it replaced, because at least the published relation's
validity range is printed in a paper.

I would rank Project 6 above Projects 1, 2, and 5 for near-term work, and behind
only 4 and 3.

---

## 9. Long-term vision

### 9.1 The layered model

The principle in the brief — *machine learning should extend Skycat's scientific
capabilities without replacing reliable deterministic astronomy where
deterministic methods are more appropriate* — is achievable, but only if it is
expressed as an architecture rather than an intention. Intentions erode; layers
with rules do not.

| Layer | Contents | Property |
|---|---|---|
| **L0 — Catalog store** | Rows, releases, partitions, spatial index, provenance, cone search, geometric crossmatch | Deterministic, reproducible forever, unchanged by anything above it |
| **L1 — Derived deterministic evidence** | Isolation and local density, symmetric matching, next-best-candidate separation, cross-catalog agreement, footprint coverage, band transformations with analytic uncertainty | Computed, explainable, no model. Most of the value in this study lives here |
| **L2 — Statistical inference** | Bayesian match posteriors, calibrated quality scores, predictive uncertainties | Assumptions stated, calibration measurable, every term auditable |
| **L3 — Learned representation** | Anomaly rankings, embeddings, multimodal similarity | Model-relative, version-pinned, never authoritative |

The critical observation is that **L1 is under-built and carries most of the
near-term value.** Isolation, cross-catalog agreement, symmetric matching, and
next-best separation are all deterministic, all cheap, all explainable, and all
currently missing — and each is a prerequisite for the L2 and L3 work that would
sit on it. A team that builds L1 well may find that a meaningful fraction of the
motivating problems are solved before any model is fitted. That would be a good
outcome, not a wasted one.

### 9.2 Five governance rules

These follow from Skycat's existing contracts and should be settled before code,
because each is cheap now and expensive later.

1. **A model version is provenance, not configuration.** Every derived value
   records the model version that produced it, exactly as a release records its
   source checksum. Unversioned scores are unreproducible, and reproducibility is
   what this package is for. *(C2)*
2. **Derived values are release-scoped.** A score belongs to a row in a release,
   not to a sky position. Re-importing a release invalidates its scores. *(C1)*
3. **ML annotates and ranks; it never filters.** A cone search returns what is in
   the cone. Model output may add keys and offer an ordering; it may not remove
   rows. *(C3)*
4. **Every score ships with its evidence.** A number without the measurements
   behind it is not a scientific product. Where a deterministic explanation
   exists, it is presented alongside — and where a deterministic answer exists,
   it wins.
5. **Standards and geometry are never model-mediated.** Landolt and Stetson
   define the photometric system. Spatial containment is a geometric fact. No
   model overrides either.

### 9.3 Where this ends up

If the trust layer is built well, Skycat becomes the component in the Skynet
ecosystem that answers not only *what is at this position* but *how much of it
to believe* — which stars to calibrate against and why, which associations are
ambiguous, which rows in a release do not fit their population, and which
transformed magnitudes are outside the range where anyone should trust them.
Every one of those answers is accompanied by the measurements that produced it,
and none of them can change what a deterministic query returns.

That is a smaller claim than "an AI-powered astronomical discovery engine." It
is also achievable with the data in the database today, useful to a real
pipeline immediately, and defensible in front of the kind of astronomer who asks
what the error bar means. The larger ambitions — visual archival search,
multimodal discovery — remain open, and the layered architecture above is
precisely what would let them be added later without anyone having to trust
them prematurely.

---

## 10. What would change these conclusions

Stated so that this note can be checked against reality later rather than
quietly aging into wrongness.

| If this happens | These conclusions change |
|---|---|
| **Gaia is ingested** | Project 3 moves from "useful feature" to "identity service" and should be re-planned around Gaia as the matching spine. Project 1's discovery framing becomes defensible. Project 4 gains isolation and binarity indicators |
| **Skynet's field-calibration residuals become available in bulk** | Project 4's validation strengthens from "predicts cross-catalog disagreement" to "predicts real zero-point performance," and it can move toward validated-scientific-tool maturity |
| **Skycat takes on observation footprints as a data type** | Project 2's deterministic half becomes buildable immediately and precovery becomes a real capability. The ML half remains long-term |
| **2MASS or WISE are ingested** | Project 1's colour space becomes physically informative and the QA framing can be genuinely supplemented by discovery |
| **A community multimodal foundation model publishes usable embeddings** | Project 5 flips from "train a model" to "version and spatially index someone else's embeddings," which is a Skycat-shaped problem and considerably more attractive |
| **The deterministic L1 work lands** | Several motivating problems may resolve without models. Re-assess Projects 1 and 3's scope against what remains |

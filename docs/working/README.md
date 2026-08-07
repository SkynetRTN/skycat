# Working notes

Dated planning notes and review reports. They are snapshots of what was true and
what was open on the day they were written — **not** descriptions of the current
API. They are deliberately excluded from the documentation tests
(`tests/test_docs.py`), because pinning a dated snapshot to today's code would
be wrong.

If you want current behaviour, everything here is the wrong file. Start at
[`../README.md`](../README.md).

## The rule

A note lives in one of exactly two places, and the frontmatter `status` says
which:

- **`working/`** — `status: open`. There is work in it that has not been done.
- **`working/archive/`** — `status: archived`. Every item has landed and nothing
  in it is outstanding. It is kept as the record of what was decided and why.

Moving a note to `archive/` means asserting the second thing. Check it against
the repository before you move one, and if a note is archived with a caveat
("still needs an admin to…"), that caveat is an open item — the note is not
archivable until it is closed or explicitly dropped.

## Current state (audited 2026-08-08)

| Note | Status | Why |
|---|---|---|
| [ml-capabilities.md](ml-capabilities.md) | **open** | Capability study of five proposed ML features. Recommends building two (calibration-star quality, probabilistic crossmatching), prototyping one, deferring two, and replacing one with a sixth idea. Nothing implemented, and the four architectural constraints in §0.3 are not yet decided. |
| [package-publishing-report.md](package-publishing-report.md) | **open** | Repository side is done, but no tag or release exists, so the install-from-a-real-release-asset check has never run. |
| [local-catalogs.md](local-catalogs.md) | **open** | What to mirror into Skycat against a 4 TB budget, and how. Sizes every candidate, gives per-catalog download sources (CDS, IRSA, MAST, ESA), covers the six touch points and what changes at 10⁸–10⁹ rows, and plans L0–L5: measure, then Tycho-2, ATLAS RefCat2, Gaia DR3 synthetic photometry, 2MASS, SkyMapper. Nothing implemented; L0's measurements block everything past L1. |
| [remote-catalogs.md](remote-catalogs.md) | **open** | The adjacent `RemoteCatalogReader` — `CatalogReader` stays local-only, a new class owns VizieR and SIMBAD. Inventories the three legacy integrations (the pipeline plugin layer, the public-API SIMBAD resolver, the hand-rolled VizieR APASS route), designs the reader, and plans R0–R5. Sequenced behind [local-catalogs.md](local-catalogs.md), which defines the five Tier D catalogs it serves. Nothing implemented; R0's decision record is outstanding. |
| [archive/design-review.md](archive/design-review.md) | archived | All 14 findings landed; the header table says where each one lives. |
| [archive/github-workflow-recommendations.md](archive/github-workflow-recommendations.md) | archived | Recommendations 1–13 plus CODEOWNERS landed; the last open item, branch protection, is now live as the `Protect Main` and `Protect release tags` rulesets. |

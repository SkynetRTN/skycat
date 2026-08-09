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
| [code-review-2026-08.md](code-review-2026-08.md) | **open** | Engineering review of the runner, release state machine, query path, guards, roles, migrations, parsers, and CI. Eighteen findings — five high, seven medium, six low — of which twelve were reproduced against a throwaway PostGIS database. Nothing implemented; the five-phase action plan has not started. |
| [ml-capabilities.md](ml-capabilities.md) | **open** | Capability study of five proposed ML features. Recommends building two (calibration-star quality, probabilistic crossmatching), prototyping one, deferring two, and replacing one with a sixth idea. Nothing implemented, and the four architectural constraints in §0.3 are not yet decided. |
| [package-publishing-report.md](package-publishing-report.md) | **open** | Repository side is done, but no tag or release exists, so the install-from-a-real-release-asset check has never run. |
| [local-catalogs.md](local-catalogs.md) | **open** | **Research survey**, not a design. Which astronomical sources distribute bulk files, where exactly to download them (CDS, IRSA, MAST, ESA Gaia Archive, AAVSO), what they cost on disk against a 4 TB budget, and whether each is worth mirroring — with reasoning. Verdicts: mirror Tycho-2, ATLAS-RefCat2, Gaia DR3 synthetic photometry, 2MASS; defer the billion-row surveys. Nothing implemented; two of its measurements gate the rest. |
| [remote-catalogs.md](remote-catalogs.md) | **open** | **Research survey**, not a design. The four catalog systems as *services* — VizieR, SIMBAD, NED, ADS: what question each answers, how it is accessed, what it costs, and whether Skycat should support it. Inventories the three existing Skynet/Afterglow integrations and the recurring failure mode they share. Verdicts: SIMBAD yes (highest value), VizieR yes, NED conditional, ADS no. Companion to [local-catalogs.md](local-catalogs.md). |
| [archive/design-review.md](archive/design-review.md) | archived | All 14 findings landed; the header table says where each one lives. |
| [archive/github-workflow-recommendations.md](archive/github-workflow-recommendations.md) | archived | Recommendations 1–13 plus CODEOWNERS landed; the last open item, branch protection, is now live as the `Protect Main` and `Protect release tags` rulesets. |

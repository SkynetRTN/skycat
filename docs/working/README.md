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

## Current state (audited 2026-08-06)

| Note | Status | Why |
|---|---|---|
| [package-publishing-report.md](package-publishing-report.md) | **open** | Repository side is done, but no tag or release exists, so the install-from-a-real-release-asset check has never run. |
| [archive/design-review.md](archive/design-review.md) | archived | All 14 findings landed; the header table says where each one lives. |
| [archive/github-workflow-recommendations.md](archive/github-workflow-recommendations.md) | archived | Recommendations 1–13 plus CODEOWNERS landed; the last open item, branch protection, is now live as the `Protect Main` and `Protect release tags` rulesets. |

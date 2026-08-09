---
status: accepted
date: 2026-08-09
---

# 2. Stable failures are explicit

## Status

Accepted. Implemented for CLI exit codes, idempotent `import --activate`, and
query release resolution.

## Context

Skycat has several stable surfaces that are used by automation rather than by a
human watching a terminal:

- Kubernetes Jobs read the `skycat` process exit code.
- Operators re-apply `skycat import <family> <release> --activate` jobs.
- Applications hold a `CatalogReader` and rely on `CatalogQueryError` to mean
  "the request cannot be answered as asked."

The old behavior was ambiguous in all three places. A bad database host or
password exited with the same code as validation warnings. Re-applying an
unchanged ingest Job against an already-active release exited non-zero. A stale
`ResolvedRelease` could return an empty list when the registry no longer had a
queryable release behind it.

## Decision

Failures on these stable surfaces are explicit:

- Database-driver connection and authentication failures are configuration
  failures and exit with code 2.
- `import --activate` is idempotent for matching imported releases: an already
  ACTIVE release reports `activated=True`, and a matching READY or SUPERSEDED
  release is activated when the validation gate permits it.
- Query functions reject non-queryable releases and re-check supplied
  `ResolvedRelease` objects against the registry before using them.

## Consequences of the alternatives

### Keep database driver failures as code 1

Code 1 is the operational bucket: an import failed validation, a requested
release is not activatable, or a query is invalid. A stale port or rotated
password is different. Retrying the same Job cannot fix it, and automation needs
to route it to configuration/secret repair instead of data triage.

### Treat repeated `import --activate` as a failed intent

The point of the ingest Job is "this release is imported and active." If that is
already true, the command has done what it said. Reporting failure makes a
declarative re-apply look broken and teaches operators to ignore a red Job.

### Trust cached release ids until the TTL expires

Returning no rows is a valid scientific answer, so it must not also mean "the
release was removed, failed, or deactivated behind this reader." The extra
registry check costs a small round trip; the alternative costs correctness and
incident clarity.

## What is done instead

- `SKYCAT_DEBUG=1` still disables the CLI wrapper so developers can see the
  original traceback.
- Validation warnings still block `import --activate` unless `--allow-warnings`
  is passed, including on the idempotent skip path.
- `CatalogReader.invalidate()` remains useful after planned activation/removal,
  but correctness no longer depends on every long-lived process calling it at
  exactly the right time.

## Revisiting

Revisit the per-query registry validation if measured production query latency
shows the extra lookup dominates the request budget. Do not revisit it merely
because the old TTL cache was faster in isolation; an ambiguous empty result is
not an acceptable cache hit.

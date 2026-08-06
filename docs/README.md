# Skycat documentation

[`README.md`](../README.md) at the repository root is the package guide — install,
configure, import, query, and the add-a-family checklist. Everything here is the
material that would make that README too long to read.

Four directories, by the question you arrived with.

## guides/ — you are doing something for the first time

| Document | Read it when |
|---|---|
| [guides/add-family.md](guides/add-family.md) | You are adding a catalog family. The worked version of the README's six-touch-point checklist. |
| [guides/provenance.md](guides/provenance.md) | You are mirroring source data, or need to prove a release matches an upstream snapshot. |

## reference/ — you need to know what is true

| Document | Read it when |
|---|---|
| [reference/architecture.md](reference/architecture.md) | You want the whole design in one pass: schemas, the release model, the spatial model, the ingestion lifecycle, deployment. |
| [reference/api-stability.md](reference/api-stability.md) | You are building against Skycat and need to know what will not move under you. |

## operations/ — you are running it

| Document | Read it when |
|---|---|
| [operations/runbook.md](operations/runbook.md) | You are about to run something destructive, watching a long import, or rotating credentials. |
| [operations/performance.md](operations/performance.md) | A query got slow, or you want targets to measure against. |
| [operations/ci.md](operations/ci.md) | A required check failed, or you are changing a workflow. |
| [operations/release.md](operations/release.md) | You are cutting a package release. |

## decisions/ — you are about to propose something already settled

[decisions/](decisions/) holds the architecture decision records: what was
chosen, what it costs, and what would justify reopening it. Start with
[decisions/README.md](decisions/README.md).

## working/ — dated snapshots, not current API

[working/](working/) holds planning notes and review reports. They are snapshots
of open work at a moment in time, **not** descriptions of the current API, and
they are deliberately excluded from the documentation tests
(`tests/test_docs.py`). Notes whose work has fully landed move to
[working/archive/](working/archive/) and stay there as the record of what was
decided and why.

## Conventions

**Docs are tested.** `tests/test_docs.py` asserts that every `skycat` command
and flag shown in the stable docs exists, that every `CatalogReader` method and
keyword argument in a Python example is real, that cited `skycat/*.py` paths
exist, and that every relative markdown link resolves. Change a CLI flag or a
reader signature and the docs change in the same commit — or CI fails.

That test also pins the list of stable docs. A new page under `guides/`,
`reference/`, or `operations/` that documents CLI or reader surface should be
added to `DOCS` in `tests/test_docs.py`; link and heading checks pick it up
automatically.

**Filenames are kebab-case** and live in one of the four directories above.
Nothing new belongs at the top level of `docs/` except this index.

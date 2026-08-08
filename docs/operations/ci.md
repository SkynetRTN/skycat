# Continuous integration

Eight workflows under [`.github/workflows/`](../../.github/workflows). Two of them
end in an aggregate job whose name is the thing branch protection should
require; the rest are advisory or path-filtered.

| Workflow | Jobs | Triggers | Status |
|---|---|---|---|
| `ci.yml` | `skycat: ruff, pyright, pytest`, `unit: python 3.11/3.12/3.13`, `migration graph`, `package build` → **`required: ci`** | every PR, push to `main` | required |
| `workflow-safety.yml` | `actionlint`, `zizmor` → **`required: workflow safety`** | every PR, push to `main` | required |
| `dependency-review.yml` | `dependency review` | every PR | required |
| `containers.yml` | `docker build`, `compose config`, `container scan` | container paths, weekly | advisory |
| `kubernetes.yml` | `kubernetes manifests` | `infra/kubernetes/**` | advisory |
| `codeql.yml` | `codeql python` | every PR, weekly | advisory |
| `secret-scan.yml` | `secret scan` | every PR, push to `main` | advisory |
| `release.yml` | `build release distributions`, `publish draft GitHub Release`, `publish distributions to TestPyPI`, `validate TestPyPI release`, `publish distributions to PyPI` | release tags, manual dispatch | release-only |

## Require the aggregates, not the jobs

`required: ci` and `required: workflow safety` exist so that branch protection
has one stable name per workflow. They run with `if: always()`, depend on every
must-pass job, and fail if any dependency failed, was cancelled, **or was
skipped** — a skipped dependency is treated as failure precisely because that is
how a required check silently stops protecting anything.

The cost is one rule: **a new must-pass job has to be added to the aggregate's
`needs:` list**, or it is not actually required no matter how red it goes.

## Why some jobs are not path-filtered

`migration graph` and `package build` are recommended as path-filtered checks
and are deliberately not filtered here. A required check that is skipped by a
path filter stays queued as *Expected* forever, and the PR cannot merge. The
workarounds (a no-op fallback job, or `dorny/paths-filter` plus conditionals)
cost more than the seconds these jobs take. Same reasoning for
`dependency review`: it runs on every PR so it can safely be required.

The genuinely expensive checks — container builds, image scanning, Kubernetes
schemas — are path-filtered and therefore stay advisory.

## What each non-obvious job is defending

- **`unit: python …`** — `pyproject.toml` declares `>=3.11,<3.14` but the deep
  gate only ever proves 3.12. The unit suite needs no database, so covering the
  declared range is cheap.
- **`migration graph`** — runs `tests/test_migration_graph.py`, which asserts one
  Alembic head, a chain that reaches base, and no duplicate revision ids. Two
  heads is the classic merge accident: both branches add a migration on the same
  parent, the merge is textually clean, and nothing complains until
  `alembic upgrade head` refuses to choose. The test is in the unit suite too,
  so it fails locally before it fails in CI.
- **`package build`** — the test suite imports `skycat` from the checkout, where
  `alembic.ini` and `skycat/migrations/` are always present. An installed wheel
  has neither guarantee: `alembic.ini` is intentionally *not* packaged, and
  `make_alembic_config()` falls back to setting `script_location` itself. The
  job installs the wheel into a clean environment and proves the packaged
  migrations still resolve to exactly one head. It also runs
  `twine check --strict dist/*` so PyPI long-description and metadata failures
  are caught before a release tag.
- **`compose config`** — asserts the eight service names the README and the
  documented local test workflow tell people to run.
- **`container scan`** — advisory on purpose. Base-image CVEs frequently have no
  fixed version available, and blocking merges on a feed the repository cannot
  act on just teaches people to ignore the check. Switch `--exit-code 0` to `1`
  in `containers.yml` once there is a triage policy.

## Prerequisites and failure modes

- **`dependency review`** and **`codeql python`** both need GitHub features that
  are free on public repositories and GHAS-only on private ones — the dependency
  graph and code scanning respectively. Skycat is public, so both run at no
  cost. If it is ever made private without GHAS, delete those two workflows
  rather than leaving permanently red checks, and replace the dependency check
  with something visibility-independent (`pip-audit` over `uv export`, or
  Trivy's filesystem scanner).

  **Free is not the same as enabled.** The dependency graph was off for this
  repository on its first run, and the job failed with "Dependency review is not
  supported on this repository" — an organisation can disable it even where it
  costs nothing. It is a one-time toggle under
  *Settings → Code security and analysis*, and it must be on **before**
  `dependency review` is added to the required contexts below, or branch
  protection blocks every merge on a check that cannot pass. Confirm it has data
  rather than trusting the setting page:

  ```bash
  gh api repos/SkynetRTN/skycat/dependency-graph/sbom --jq '.sbom.packages | length'
  ```

  Code scanning has the mirror-image trap: if *default setup* is ever enabled in
  settings, it conflicts with `codeql.yml` and the advanced workflow fails on
  SARIF upload. Check with
  `gh api repos/SkynetRTN/skycat/code-scanning/default-setup --jq .state`;
  `not-configured` is what this workflow needs.
- **`secret scan`** uses the gitleaks *image*, not the Action, because the
  Action requires an organisation licence key. Allowlists live in
  [`.gitleaks.toml`](../../.gitleaks.toml); every entry there is a claim that the
  matched value is safe to be public. Scans both the working tree and full
  history, since a credential that was committed and later deleted is still
  published and still needs rotating.
- **`release.yml`** is not a PR gate. It runs for `v*.*.*` tags or explicit
  manual dispatch, verifies the tag matches `pyproject.toml`, builds the wheel
  and sdist once, checks package metadata with Twine, smoke-tests both local
  install paths, then attaches those exact artifacts to a draft GitHub Release
  from the `github-release` environment. Tag pushes also publish the tested
  artifacts to TestPyPI and run a TestPyPI install/rendered-page validation.
  Real PyPI uploads are still manual-dispatch only, and the PyPI job
  re-validates TestPyPI before uploading.

## Action versions

Most actions track a **moving major** (`@v7`, not `@v7.1.2`). Exact tags are
used where a repository-specific security concern makes a moving ref a poor
fit. JavaScript actions should resolve to a `node24` runtime. Node 20 actions
still execute — the runner force-runs them on Node 24 — but they emit a
deprecation warning on every step and are on a removal path, so "it worked
yesterday" is not a reason to leave one behind.

Two constraints when bumping:

- **`upload-artifact` and `download-artifact` must stay a matched pair.** They
  interoperate through `@actions/artifact`, and the majors are staggered:
  upload `v4` ↔ download `v4`/`v5` (artifact 2.x), and upload `v7` ↔ download
  `v8` (artifact 6.x). Bumping one alone produces a job that uploads
  successfully and a dependent job that cannot find the artifact — which is how
  `container scan` and the release publish step would break.
- **`actions/dependency-review-action` is pinned exactly**, to `v5.0.0`. That
  repository publishes majors as branches rather than tags, and `@v5` is
  flagged by zizmor's ref-confusion audit as resolvable from either namespace.
  Since zizmor gates `required: workflow safety`, that is a blocking finding,
  not a style note.
- **`pypa/gh-action-pypi-publish` is pinned exactly**, currently to `v1.14.1`.
  The project recommends Trusted Publishing, but its moving `release/v1` branch
  is a worse fit for a workflow that already treats release publishing as a
  code-owned supply-chain boundary.

Check a JavaScript action bump before making it — the ref must exist, the
runtime must be `node24`, and the inputs in use must survive:

```bash
gh api repos/actions/checkout/git/ref/tags/v7 --jq .object.sha
gh api "repos/actions/checkout/contents/action.yml?ref=v7" --jq .content \
  | tr -d '\n' | base64 -d | grep -E "^  [a-z-]+:|node[0-9]+"
```

## Branch protection

Workflows cannot configure this — it is a repository setting, and it is what
decides whether any of the above actually protects `main`.

Skycat uses **rulesets** (Settings → Rules → Rulesets), not classic branch
protection. The two are separate systems with separate APIs: this repository has
no classic protection at all, so
`gh api repos/SkynetRTN/skycat/branches/main/protection` returns
`Branch not protected` even though `main` is fully protected. Read the rulesets
instead:

```bash
gh api repos/SkynetRTN/skycat/rulesets --jq '.[] | "\(.name)\t\(.target)\t\(.enforcement)"'
gh api repos/SkynetRTN/skycat/rules/branches/main --jq '.[].type'   # what applies to a branch
```

Three things are configured, and they are edited in the UI rather than from a
script — a ruleset is security configuration, and a `PUT` that drops a rule by
omission is not the failure you want at 2am.

### `Protect Main` — branch ruleset, active, `refs/heads/main`

| Rule | Setting |
|---|---|
| `pull_request` | 1 approving review, **require review from Code Owners**, require conversation resolution |
| `required_status_checks` | strict (branch must be up to date) — the three contexts below |
| `deletion` | `main` cannot be deleted |
| `non_fast_forward` | no force pushes |

No bypass actors, so the rules apply to administrators too.

**The required contexts must be the check names GitHub actually reports.** This
is the one place a typo is silent and total: a required context that never
reports is not an error, it is a check that stays *Expected* forever, and the PR
can never merge. The names are:

```
required: ci
required: workflow safety
dependency review
```

The `required:` prefix is part of the name, not a description of it. Verify
against a real PR rather than from memory — this listing is the source of truth:

```bash
gh pr checks <PR> --json name --jq '.[].name' | sort
```

Requiring the two aggregates rather than their member jobs is deliberate: see
*Require the aggregates, not the jobs* above. Adding `container scan`,
`kubernetes manifests`, or any other path-filtered check to this list will
deadlock merges for the reason given in *Why some jobs are not path-filtered*.

### `Protect release tags` — tag ruleset, active, `refs/tags/v*.*.*`

Blocks `deletion`, `non_fast_forward`, `creation`, and `update`, with a
repository-role bypass so a maintainer can still cut a release. This is what
makes a published version immutable: `release.yml` triggers on `v*.*.*` and
attaches built artifacts to the tag, so a tag that can be moved is a release
whose contents can be changed after the fact.

### `github-release` environment

`release.yml`'s publish job runs in this environment, which carries a
**required-reviewer** rule. The draft release is therefore gated on a human
approving the deployment, not merely on the tag existing. The build job runs
first and uploads artifacts; the publish job waits for approval and attaches
those exact files without rebuilding.

### `testpypi` and `pypi` environments

The package-index publish jobs use GitHub environments named `testpypi` and
`pypi`, matching the names configured in PyPI/TestPyPI Trusted Publishers. The
jobs request only job-level `id-token: write`, download the tested
`release-dists` artifact, and call `pypa/gh-action-pypi-publish@v1.14.1`.

Protect `pypi` with required reviewer approval. Leave `testpypi` without
required reviewers if tag pushes should publish rehearsal uploads
automatically; protect it too only if the project wants the same manual gate for
TestPyPI uploads.

### CODEOWNERS

[`.github/CODEOWNERS`](../../.github/CODEOWNERS) assigns the CI definition, schema
migrations, deployment assets, and packaging to the active maintainer
(`@archon774`, James Atkisson — also recorded in `pyproject.toml`). It is
binding because `Protect Main` sets `require_code_owner_review`; without that,
it is advisory. This is what enforces the closed maintenance model on a public
repository.

### `dev` is not protected

The rulesets target `refs/heads/main` and `refs/tags/v*.*.*`. A pull request
into `dev` runs every workflow but **enforces nothing** — it can be merged red.
That is a reasonable trade for an integration branch, but it means a green
`dev` PR is evidence, not a gate, and the first PR from `dev` into `main` is
where the required contexts get tested for real. Check the names before opening
it.

### Deliberately not implemented

The **`feature → dev → main` PR-target check.** Whether `main` may take PRs from
branches other than `dev` is a policy call, and an unwritten one — a
merge-blocking rule invented here would be a guess with teeth. Rulesets have no
native condition for a pull request's *source* branch either, so enforcing it
would mean a workflow job that inspects `github.head_ref` and fails, which is a
rule worth writing only once the policy is decided.

## Running the checks locally

The workflows deliberately use containers (actionlint, kubeconform, trivy,
gitleaks) and `uv` rather than distro packages, so the same commands work on
Fedora/RHEL, Debian/Ubuntu, and macOS. `podman` and `docker` are interchangeable
below; on Fedora with SELinux enforcing, add `:Z` to bind mounts as shown.

```bash
# Fedora / RHEL
sudo dnf install -y podman jq

# the gates
uv run ruff check skycat tests
uv run pyright skycat
uv run pytest tests -q -m "not postgis"          # unit suite, no database
uv run pytest tests/test_migration_graph.py -q   # migration graph

# workflow safety
podman run --rm -v "$PWD:/repo:Z" --workdir /repo rhysd/actionlint:latest -color
uvx zizmor@1 --min-severity medium --min-confidence medium .github/workflows

# packaging
uv build
uv run --frozen --extra dev twine check --strict dist/*
uv venv /tmp/pkgcheck && uv pip install --python /tmp/pkgcheck/bin/python dist/*.whl
/tmp/pkgcheck/bin/skycat --help

# containers, manifests, secrets
podman build --tag skycat:ci --file Dockerfile .
docker compose -f infra/docker/compose.yaml --profile skycat-tools config --quiet
podman run --rm -v "$PWD:/work:Z" --workdir /work \
  ghcr.io/yannh/kubeconform:latest -strict -summary -ignore-missing-schemas infra/kubernetes
podman run --rm -v "$PWD:/repo:Z" --workdir /repo \
  zricethezav/gitleaks:latest dir /repo --config /repo/.gitleaks.toml --redact
```

The full PostGIS suite needs a throwaway database. Never point it at the
Compose stack on 5433 or any catalog store you care about.

One disposable local path is:

```bash
docker run -d --rm --name skycat-test-pg \
  -e POSTGRES_USER=catalog_admin -e POSTGRES_PASSWORD=catalog \
  -e POSTGRES_DB=catalogs -p 127.0.0.1:5434:5432 \
  --tmpfs /var/lib/postgresql/data \
  skycat-postgres:latest

export SKYCAT_DB_HOST=127.0.0.1 SKYCAT_DB_PORT=5434 SKYCAT_DB_NAME=catalogs
export SKYCAT_DB_BOOTSTRAP_USER=catalog_admin SKYCAT_DB_BOOTSTRAP_PASSWORD=catalog
export SKYCAT_DB_ADMIN_USER=catalog_owner SKYCAT_DB_ADMIN_PASSWORD=catalog
export SKYCAT_DB_INGEST_USER=catalog_ingest SKYCAT_DB_INGEST_PASSWORD=catalog
export SKYCAT_DB_READER_USER=catalog_reader SKYCAT_DB_READER_PASSWORD=catalog
export SKYCAT_DB_USER=catalog_reader SKYCAT_DB_PASSWORD=catalog

uv run skycat init
uv run pytest tests -q --require-postgis

docker stop skycat-test-pg
```

# Continuous integration

Eight workflows under [`.github/workflows/`](../.github/workflows). Two of them
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
| `release.yml` | `build release distributions`, `publish draft GitHub Release` | release tags, manual dispatch | release-only |

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
  migrations still resolve to exactly one head.
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
  [`.gitleaks.toml`](../.gitleaks.toml); every entry there is a claim that the
  matched value is safe to be public. Scans both the working tree and full
  history, since a credential that was committed and later deleted is still
  published and still needs rotating.
- **`release.yml`** is not a PR gate. It runs for `v*.*.*` tags or explicit
  manual dispatch, verifies the tag matches `pyproject.toml`, builds the wheel
  and sdist once, smoke-tests both install paths, then attaches those exact
  artifacts to a draft GitHub Release from the `github-release` environment.

## Action versions

Every action tracks a **moving major** (`@v7`, not `@v7.1.2`), and every one
resolves to a `node24` runtime. Node 20 actions still execute — the runner
force-runs them on Node 24 — but they emit a deprecation warning on every step
and are on a removal path, so "it worked yesterday" is not a reason to leave one
behind.

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

Check a bump before making it — the ref must exist, the runtime must be
`node24`, and the inputs in use must survive:

```bash
gh api repos/actions/checkout/git/ref/tags/v7 --jq .object.sha
gh api "repos/actions/checkout/contents/action.yml?ref=v7" --jq .content \
  | tr -d '\n' | base64 -d | grep -E "^  [a-z-]+:|node[0-9]+"
```

## Branch protection

Workflows cannot configure this — it is a repository setting, and it is what
decides whether any of the above actually protects `main`.

Settings → Branches → add a rule for `main`:

- Require a pull request before merging.
- Require status checks to pass, and require the branch to be up to date.
  - `required: ci`
  - `required: workflow safety`
  - `dependency review`
- Require review from Code Owners — this is what makes
  [`.github/CODEOWNERS`](../.github/CODEOWNERS) binding rather than advisory,
  and it is the setting that enforces the closed maintenance model on a public
  repository.
- Require conversation resolution before merging.
- Restrict who can push to `main`; disable force pushes and branch deletion.
- Require signed commits if that matches the release policy.

Or with the `gh` CLI:

```bash
gh api -X PUT repos/SkynetRTN/skycat/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["required: ci", "required: workflow safety", "dependency review"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "require_code_owner_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
```

[`.github/CODEOWNERS`](../.github/CODEOWNERS) assigns the CI definition, schema
migrations, deployment assets, and packaging to the active maintainer
(`@archon774`, James Atkisson — also recorded in `pyproject.toml`). It has no
force until "Require review from Code Owners" is enabled above.

One item from the planning document is deliberately **not** implemented: the
**`feature → dev → main` PR-target check.** Whether `main` may take PRs from
branches other than `dev` is a policy call, and an unwritten one — a
merge-blocking rule invented here would be a guess with teeth.

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

The full PostGIS suite needs a throwaway database — see README "Testing". Never
point it at the Compose stack on 5433.

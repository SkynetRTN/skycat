---
status: implemented
reviewed: 2026-08-06
branch: dev
authority: code-inspection
implementation: workflows-landed-2026-08-06
---

# GitHub workflow recommendations

> **Implementation status (2026-08-06).** Recommendations 1–13 have landed, plus
> CODEOWNERS; see [`docs/CI.md`](../CI.md) for the resulting workflow map, the
> required-check set, and how to run each check locally. One item was
> deliberately left out and is documented there: the `feature → dev → main`
> PR-target rule (an unwritten policy). Branch protection itself is a repository
> setting and still has to be applied by an admin — `docs/CI.md` has the
> settings list and the equivalent `gh api` call.
>
> CODEOWNERS was implementable after all: the planning note conditions it on
> ownership being clear, and `pyproject.toml` now names James Atkisson
> (`@archon774`) as the active maintainer. Skycat is a public repository with a
> closed maintenance model, so CODEOWNERS plus "Require review from Code Owners"
> is what makes that model enforceable rather than customary.
>
> Two deviations from the scope below, both to keep required checks workable:
> `migration graph`, `package build`, and `dependency review` run on every PR
> rather than path-filtered, because a required check skipped by a path filter
> stays queued as *Expected* and blocks the merge forever. And the text below
> describes `ci.yml` as a working baseline — it was not. The job name on line 15
> made it invalid YAML (`name: skycat: ruff, pyright, pytest`, an unquoted
> scalar containing `": "`); `actionlint` catches exactly this, and the name is
> now quoted.

This report scopes recommended GitHub Actions jobs and branch-protection checks
for Skycat. It is intentionally a planning document only: no workflow changes
are made by this report.

The goal is to protect `main` from risky merges while keeping CI useful for a
Python/PostgreSQL/PostGIS catalog package with Docker and Kubernetes deployment
assets.

## Current workflow baseline

Skycat currently has one workflow:

- `.github/workflows/ci.yml`
- Runs on `pull_request`, `push` to `main`, and `workflow_dispatch`
- Starts a PostgreSQL/PostGIS service
- Installs the project with `uv sync --frozen --extra dev`
- Runs `ruff check skycat tests`
- Runs `pyright skycat`
- Runs `skycat init`
- Verifies the reader role can connect to the test database
- Runs `pytest tests -q`

This is already a strong core gate. It exercises linting, type checking,
database initialization, roles, migrations, sample imports, spatial behavior,
CLI behavior, and documentation/API consistency tests.

The main remaining risk is not lack of basic tests. The gaps are around workflow
integrity, dependency and supply-chain review, declared Python-version
compatibility, packaging/deployment artifacts, and stable branch-protection
status names.

## Recommended required checks for `main`

These are the checks most worth making required before a PR can merge into
`main`.

### 1. Keep the existing PostGIS CI as the primary required check

Recommended status check:

- `CI / skycat: ruff, pyright, pytest`

Benefit:

- Protects the most important runtime contract: Skycat only supports real
  PostgreSQL/PostGIS behavior for schemas, roles, migrations, COPY-style ingest,
  partitioning, and spatial queries.
- Prevents merges where unit tests pass but integration behavior breaks.
- Already includes `uv sync --frozen`, so dependency-lock drift is caught.

Notes:

- This job should remain required for `main`.
- If the job name is ever changed, branch protection must be updated unless an
  aggregate "required" job is introduced.
- The existing "reader role reachable" guard is valuable because local test
  fixtures skip PostGIS tests when no database is reachable. CI should continue
  making that skip path impossible.

### 2. Add a stable aggregate required check

Recommended job name:

- `required: ci`

Benefit:

- Gives branch protection one stable check name even as internal CI jobs are
  renamed, split, or expanded.
- Reduces the chance that a required check silently stops protecting `main`
  after workflow refactors.
- Makes it easier to add optional jobs without blocking merges.

Scope:

- The aggregate job should depend on all must-pass jobs.
- It should run with `if: always()` and fail if any required dependency failed,
  was cancelled, or was skipped.

Tradeoff:

- Adds a small amount of workflow boilerplate.
- Requires discipline: every newly required job must be added to the aggregate
  dependency list.

### 3. Add workflow syntax and security linting

Recommended workflow:

- `.github/workflows/workflow-safety.yml`

Recommended jobs:

- `actionlint`: validate GitHub Actions syntax, event syntax, expressions, shell
  snippets, and referenced contexts.
- `zizmor` or equivalent: audit workflow security posture.

Benefit:

- Catches broken workflow YAML before it reaches `main`.
- Catches unsafe patterns such as overly broad permissions, risky
  `pull_request_target` usage, untrusted checkout patterns, and credential
  exposure risks.
- Protects the CI system itself, which is important because a compromised or
  broken workflow can bypass the value of all other checks.

Scope:

- Run on PRs that touch `.github/workflows/**`.
- Also run on all PRs if the runtime cost is acceptable.
- Run on `push` to `main` so direct workflow changes are still audited.

Tradeoff:

- Workflow-security linters can be noisy at first. Start in warning mode if the
  first run produces a large backlog, then make it required once the baseline is
  clean.

### 4. Add dependency review on pull requests

Recommended workflow:

- `.github/workflows/dependency-review.yml`

Recommended job:

- GitHub dependency review for PRs that modify `pyproject.toml` or `uv.lock`.

Benefit:

- Blocks known-vulnerable dependency additions before they merge.
- Makes dependency risk visible at review time instead of after a release.
- Useful for Skycat because database, CLI, and packaging dependencies are in the
  runtime path.

Scope:

- Run on `pull_request`.
- Restrict with path filters to `pyproject.toml`, `uv.lock`, and workflow files
  if desired.
- Configure severity and license policy explicitly.

Tradeoff:

- Requires GitHub dependency graph support.
- Vulnerability databases can report issues in transitive packages before an
  upstream fix exists. Decide whether medium-severity issues should block or
  only warn.

### 5. Add Python compatibility unit matrix

Recommended workflow or job:

- Run non-PostGIS unit tests across Python `3.11`, `3.12`, and `3.13`.

Benefit:

- `pyproject.toml` declares `requires-python = ">=3.11, <3.14"`.
- Current deep CI uses Python 3.12 only.
- A compatibility matrix catches syntax, typing, packaging, dependency, and
  standard-library behavior differences across the supported range.

Scope:

- Run `uv sync --frozen --extra dev` for each Python version.
- Run `pytest tests -q -m "not postgis"` for each Python version.
- Keep the full PostGIS integration job on one Python version to avoid tripling
  database runtime.

Tradeoff:

- Adds CI minutes.
- Should not duplicate the full PostGIS suite unless a release is being cut or
  there is a version-specific database-driver issue.

### 6. Add migration graph validation

Recommended workflow or job:

- `migration-graph`

Benefit:

- Catches multiple Alembic heads, broken revision imports, missing revision
  links, or accidental migration graph forks before database CI gets slower and
  harder to interpret.
- Reduces the chance of merging schema changes that cannot be applied cleanly in
  staging or production.

Scope:

- Run without a database by loading Alembic script metadata.
- Assert exactly one migration head unless the project intentionally supports
  branches.
- Run on PRs that touch `skycat/migrations/**`, `skycat/models/**`,
  `skycat/database/**`, or `alembic.ini`.

Tradeoff:

- Does not replace the existing PostGIS migration smoke test. It only validates
  the revision graph and script loadability.

### 7. Add package build and install validation

Recommended workflow or job:

- `package-build`

Benefit:

- Catches packaging errors that normal editable or project-root test runs can
  miss.
- Verifies the wheel/sdist include runtime files needed for CLI use and
  migrations.
- Protects downstream users who install Skycat as a package instead of running
  from the repository checkout.

Scope:

- Run `uv build`.
- Create a clean virtual environment.
- Install the built wheel.
- Run smoke commands such as `python -c "import skycat"` and `skycat --help`.
- Optionally run a no-database command such as `skycat config`.

Tradeoff:

- Adds moderate runtime.
- If package metadata is intentionally incomplete before public release, this
  can start as advisory and become required later.

## Recommended conditional checks

These checks are valuable but do not necessarily need to run on every PR.

### 8. Docker image build validation

Recommended workflow or job:

- `docker-build`

Benefit:

- Catches Dockerfile regressions that Python tests miss.
- Verifies the runtime image can install Skycat with `uv sync --frozen --no-dev`.
- Protects Kubernetes jobs and operators that run Skycat through the container
  image.

Scope:

- Build the top-level `Dockerfile`.
- Build `infra/docker/skycat-postgresql.dockerfile`.
- Do not push images from PR workflows.
- Run on PRs touching `Dockerfile`, `.dockerignore`, `pyproject.toml`,
  `uv.lock`, `skycat/**`, or `infra/docker/**`.

Tradeoff:

- Docker builds add runtime and can be noisy if base images have transient
  registry issues.
- Consider making this required only for paths that affect container builds.

### 9. Docker Compose config validation

Recommended workflow or job:

- `compose-config`

Benefit:

- Catches invalid Compose syntax, bad build contexts, renamed services, broken
  profiles, and environment interpolation mistakes.
- Useful because the README and local test workflow rely on Compose behavior.

Scope:

- Run `docker compose -f infra/docker/compose.yaml config`.
- Run on PRs touching `infra/docker/**`, `Dockerfile`, `.dockerignore`, or
  environment-example files.

Tradeoff:

- Validates structure, not full service startup.
- Full Compose startup can be added later if Docker build time is acceptable.

### 10. Kubernetes manifest validation

Recommended workflow or job:

- `kubernetes-manifests`

Benefit:

- Catches schema errors in starter Kubernetes Jobs before they are copied into
  staging or production.
- Protects operational workflows for `skycat init`, `skycat migrate`, and
  `skycat import`.

Scope:

- Validate `infra/kubernetes/deploy/base/**/*.yaml` with a Kubernetes schema
  validator.
- Keep this as static validation only; do not require a live cluster.

Tradeoff:

- Static validators can lag new Kubernetes versions.
- The current manifests reference external ConfigMaps, Secrets, PVCs, and image
  names that are intentionally placeholders, so the job should validate schema
  rather than cluster existence.

### 11. Secret scanning

Recommended workflow or job:

- `secret-scan`

Benefit:

- Catches accidentally committed credentials, tokens, database URLs, dumps, or
  catalog access secrets.
- Especially useful because this repo has `.env` examples, database credentials
  for local dev, Docker assets, and operational docs.

Scope:

- Run on PRs.
- Treat `.env.example` and `.env.secrets.example` carefully to avoid false
  positives for documented placeholder credentials.

Tradeoff:

- Secret scanners need allowlists for intentional examples.
- Start as advisory if the initial baseline has expected findings.

### 12. CodeQL or Python security analysis

Recommended workflow or job:

- `codeql-python`

Benefit:

- Finds some classes of security defects that tests and Ruff do not target,
  such as unsafe data handling, injection paths, or risky library use.
- Helps protect CLI and ingestion code paths that parse external catalog files.

Scope:

- Run on PRs and scheduled weekly.
- Keep permissions minimal.

Tradeoff:

- For a small Python package, signal may be modest.
- CodeQL runtime is longer than lint/unit tests, so it can be required later
  after baseline quality is known.

### 13. Container vulnerability scan

Recommended workflow or job:

- `container-scan`

Benefit:

- Finds vulnerabilities in OS packages and Python packages inside the built
  runtime image.
- Protects deployments that run the Docker image.

Scope:

- Run after `docker-build`.
- Scan PR images locally without pushing.
- Run on Docker-related changes and on a scheduled cadence.

Tradeoff:

- Vulnerability feeds can flag issues in base images before patched images are
  available.
- Treat critical/high findings as blocking only after the team defines a
  triage policy.

## Recommended branch protection settings

These are repository settings rather than workflow jobs, but they determine
whether the jobs actually protect `main`.

Recommended for `main`:

- Require pull requests before merging.
- Require status checks to pass before merging.
- Require the branch to be up to date before merging.
- Require the existing PostGIS CI check.
- Require aggregate checks such as `required: ci` and `required: workflow
  safety` if those jobs are added.
- Require conversation resolution before merging.
- Restrict who can push directly to `main`.
- Disable force pushes and branch deletion.
- Require signed commits if that matches the repository's release policy.
- Add CODEOWNERS for `.github/workflows/**`, `skycat/migrations/**`,
  `infra/**`, and `pyproject.toml` if ownership is clear.

Optional policy:

- If the desired flow is always `feature -> dev -> main`, add a lightweight
  PR-target check that fails when a PR targets `main` from a branch other than
  `dev` or an approved release/hotfix pattern.

Tradeoff:

- Strict source-branch policies reduce accidental direct merges to `main`, but
  can slow urgent hotfixes unless exceptions are documented.

## Suggested implementation sequence

1. No-code planning only: review this report and choose which jobs should be
   required versus advisory.
2. Add workflow-level hardening: minimal `permissions`, no persisted checkout
   credentials unless needed, and stable aggregate required jobs.
3. Add workflow syntax/security linting and make it required once the baseline
   is clean.
4. Add dependency review for PRs that change dependencies or lockfiles.
5. Add Python compatibility unit matrix across `3.11`, `3.12`, and `3.13`.
6. Add migration graph and package build validation.
7. Add Docker, Compose, Kubernetes, CodeQL, secret scan, and container scan jobs
   as path-filtered or scheduled checks.

## Recommended required set after rollout

A practical required set for `main` would be:

- Existing full CI: Ruff, Pyright, PostGIS init, and pytest.
- Workflow safety: action syntax and workflow-security linting.
- Dependency review: required for dependency and lockfile changes.
- Python compatibility: non-PostGIS unit tests across the supported Python
  range.
- Package/schema safety: package build smoke plus Alembic migration graph
  validation.

Docker, Kubernetes, CodeQL, secret scanning, and container vulnerability scans
are useful but can start as advisory or path-filtered required checks. They are
worth promoting to required once their false-positive rate and runtime are
known.


# CI Visual-Test Prebuilt Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this
> plan task-by-task. Multi-agent execution is intentionally excluded for this delivery.

- Date: 2026-07-19
- Status: Approved
- Owner: project maintainer
- Related specification: [CI visual-test prebuilt image design](../superpowers/specs/2026-07-19-ci-visual-prebuilt-image-design.md)
- Related architecture: [Current architecture](../architecture.md#current-architecture--implemented), [Accepted constraints](../architecture.md#accepted-constraints)
- Related ADRs: [ADR 0003](../adr/0003-docker-compose-yandex-cloud.md), [ADR 0005](../adr/0005-promote-images-through-staging.md)
- ADR impact: Conforms to ADR 0003 and ADR 0005

## Goal

Run complete pull-request CI once and reuse a dependency-keyed GHCR image for ordinary visual tests
without granting package write permission to pull requests.

## Architecture

The existing POSIX runner remains the single selector of the dependency-keyed image. Locally it
uses the current Docker-daemon cache; in CI it attempts a read-only GHCR pull and falls back to the
same Compose build. A separate trusted workflow publishes the keyed image only from `main` when its
three dependency inputs or the publisher definition change.

## Tech stack

GitHub Actions, GHCR, POSIX shell, Docker Compose, pytest, PyYAML, Playwright.

## Global constraints

- Keep `pull_request` CI and restrict branch `push` CI to `main`.
- CI package permissions are read-only; only the main-only publisher has `packages: write`.
- The image key remains the Git object hash of `Dockerfile.visual-tests`, `package-lock.json`, and
  `src/backend/requirements.txt`.
- Local runs never pull from or push to GHCR.
- Preserve the current Debian Bookworm, Node 22, Python 3.12, Playwright 1.61.0, and Chromium
  rendering environment.
- Do not change deployed services, application images, database state, or S3 state.

## Scope

### In scope

- Main-only branch-push CI.
- Read-only CI reuse of `ghcr.io/${GITHUB_REPOSITORY}-visual-tests:<dependency-key>`.
- Main-only publication of that image after dependency changes.
- Behavioral tests and engineering-job evidence.

### Out of scope

- Splitting the current quality job into parallel jobs.
- Replacing the custom Dockerfile with Microsoft's Playwright image.
- Publishing images from pull requests or changing application deployment.

## Acceptance criteria

- A pull request causes only its `pull_request` CI run; a merge causes the `main` push run.
- A registry hit runs the visual suite without `docker compose build`.
- A registry miss falls back to the current build and still runs the suite.
- Local repeated runs retain build-once behavior and do not call `docker pull`.
- Only the publisher workflow has `packages: write`, and it runs only on trusted `main` changes.
- Focused contracts, repository checks, workflow parsing, and Docker Compose configuration pass.

## Implementation

### Task 1: Make registry reuse a tested runner behavior

**Files:**
- Modify: `tests/test_visual_test_runner.py`
- Modify: `tests/visual/run-in-container.sh`

**Interfaces:**
- Consumes: optional `VISUAL_TEST_IMAGE_PREFIX` environment variable.
- Produces: `VISUAL_TEST_IMAGE=<prefix>:<dependency-key>` and pull-before-build behavior when the
  registry prefix is present.

- [ ] Extend the fake Docker executable with a `pull` case controlled by `DOCKER_PULL_HIT`. Add a
  registry-hit test that asserts one pull, zero builds, and one test run. Add a registry-miss test
  that asserts one pull, one build, and one test run. Keep the existing local build-once and
  snapshot-update assertions.
- [ ] Run the focused runner tests and expect the new registry tests to fail because the runner does
  not call `docker pull`.
- [ ] In `tests/visual/run-in-container.sh`, select
  `image_prefix="${VISUAL_TEST_IMAGE_PREFIX:-photo-prjct-visual-deps}"`; on a missing local image,
  attempt `docker pull` only when `VISUAL_TEST_IMAGE_PREFIX` is non-empty, otherwise or on pull
  failure execute the existing Compose build.
- [ ] Re-run the focused runner tests and expect all tests to pass.
- [ ] Commit both files with message `feat: reuse visual test image from registry`.

### Task 2: Enforce workflow triggers and least-privilege publishing

**Files:**
- Modify: `tests/test_repository_foundation.py`
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/visual-test-image.yml`

**Interfaces:**
- Consumes: GitHub `GITHUB_TOKEN`, `github.repository`, and the three dependency-key inputs.
- Produces: read-only CI image prefix and a main-only GHCR tag
  `ghcr.io/${GITHUB_REPOSITORY}-visual-tests:<dependency-key>`.

- [ ] Add a repository test that asserts CI `push.branches == ["main"]`, the quality job permissions
  equal `{"contents": "read", "packages": "read"}`, one GHCR login step exists, and the visual
  step receives `VISUAL_TEST_IMAGE_PREFIX` with no publishing variable. Add a publisher test that
  asserts main-only and path-filtered triggers, permissions `contents: read` plus `packages: write`,
  one GHCR login, one key-computation step, and `docker/build-push-action@v6` with `push: true`.
- [ ] Run the new repository tests and expect failures because CI has broad push triggers and the
  publisher workflow is absent.
- [ ] Restrict `.github/workflows/ci.yml` push branches to `main`, declare the read-only job
  permissions, log in with `docker/login-action@v3`, and pass
  `VISUAL_TEST_IMAGE_PREFIX: ghcr.io/${{ github.repository }}-visual-tests` only to the visual step.
- [ ] Create `.github/workflows/visual-test-image.yml`, triggered on `main` changes to the Dockerfile,
  lock files, or its own workflow plus `workflow_dispatch`. Give its sole build job only read/write
  package permissions, calculate the exact dependency key with `git hash-object`, log in to GHCR,
  and build/push `Dockerfile.visual-tests` via `docker/build-push-action@v6`.
- [ ] Re-run the focused repository tests and parse all workflow YAML files successfully.
- [ ] Commit the tests and workflows with message `ci: publish prebuilt visual test image`.

### Task 3: Record delivered CI behavior and perform final reconciliation

**Files:**
- Modify: `docs/engineering-jobs.md`
- Modify: `docs/superpowers/specs/2026-07-19-ci-visual-prebuilt-image-design.md`
- Modify: `docs/plans/2026-07-19-ci-visual-prebuilt-image.md`

**Interfaces:**
- Consumes: verified workflow and runner behavior from Tasks 1 and 2.
- Produces: current EJ-002/EJ-005 evidence and final architecture/ADR result.

- [ ] Update EJ-002 to state that PR checks run once while `main` retains branch-push validation.
  Update EJ-005 to state that CI pulls the dependency-keyed GHCR image and falls back to building a
  missing key. Append one `Validated -> Validated` history row for each job with code/test evidence.
- [ ] Mark this plan `Status: Complete` only after all local verification succeeds.
- [ ] Run the complete verification matrix below, then compare the result with the specification,
  ADR 0003, ADR 0005, and `docs/architecture.md`.
- [ ] Record in the PR: `Conforms to ADR 0003 and ADR 0005; docs/architecture.md needs no update
  because the pinned visual-test container boundary is unchanged.`
- [ ] Commit documentation with message `docs: record prebuilt visual CI image`.

## Verification

Run from the isolated worktree with the repository virtual environment and CI-safe environment:

```sh
SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=localhost \
DB_NAME=app DB_USER=app DB_PASSWORD=app DB_HOST=localhost DB_PORT=5432 \
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/pytest \
  tests/test_visual_test_runner.py tests/test_repository_foundation.py -q
sh -n tests/visual/run-in-container.sh
docker compose -f docker-compose.visual.yml config >/dev/null
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/ruff format --check .
/Users/petrnikitin/Documents/Sites/photo-prjct/.venv/bin/ruff check .
git diff --check
```

Expected: all commands exit 0, all focused tests pass, and no formatting or diff errors are printed.
The draft PR must then show CI reaching terminal success. The publisher workflow will be verified on
the eventual `main` merge because its trusted trigger does not run on a pull request. CI remains
authoritative for PostgreSQL and the full visual suite.

## Operational impact and rollout

The change adds one GHCR package and a main-only publisher workflow. The first publisher run and any
later dependency-key change build Chromium once; ordinary jobs pull the resulting image. There are
no migrations, runtime configuration changes, cloud mutations, S3 writes, or deployment changes.

## Rollback

Delete `.github/workflows/visual-test-image.yml`, remove GHCR login and registry prefix from CI,
restore unrestricted `push`, and restore the runner's local-only image selection. Existing GHCR
package versions can remain unused and may be removed later through a separately authorized package
cleanup; no application data is affected.

## Open questions

None.

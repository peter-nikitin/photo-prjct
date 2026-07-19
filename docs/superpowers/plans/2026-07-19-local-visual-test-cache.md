# Local Visual Test Dependency Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse one dependency-keyed local Docker image so repeated visual-test runs do not rebuild the image or download Chromium after source-only changes.

**Architecture:** Build a dependency-only image tagged with a deterministic Git object hash of its Dockerfile and lock files. Mount the runtime source files into that image and make the host runner build only when the keyed tag does not exist.

**Tech Stack:** POSIX shell, Docker Compose, Docker BuildKit, Python/pytest, Playwright.

## Global Constraints

- Keep GitHub Actions behavior unchanged; this change optimizes the local reusable Docker cache only.
- Do not mount `.env`, `.git`, or the whole repository into the visual-test container.
- Keep application and general test sources read-only; preserve writable snapshot, report, and result mounts.
- Continue removing transient Compose containers and PostgreSQL volumes after every run.

---

### Task 1: Specify dependency-image reuse behavior

**Files:**
- Create: `tests/test_visual_test_runner.py`
- Modify: `tests/visual/run-in-container.sh`

**Interfaces:**
- Consumes: `sh tests/visual/run-in-container.sh test|update`
- Produces: `VISUAL_TEST_IMAGE=photo-prjct-visual-deps:<dependency-key>` and build-once behavior.

- [ ] **Step 1: Write the failing behavioral test**

Create a fake `docker` executable that records invocations, treats `docker image inspect` as missing
until `docker compose build` runs, and execute the real runner twice. Assert that both invocations run
the test container while only the first invocation builds it. Add a second test asserting that update
mode selects `test:visual:update:inside`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_visual_test_runner.py -q`

Expected: FAIL because the existing runner executes `docker compose build` on both invocations and
does not inspect a dependency-keyed image.

- [ ] **Step 3: Implement dependency-keyed build reuse**

Update the runner to hash `Dockerfile.visual-tests`, `package-lock.json`, and
`src/backend/requirements.txt` with `git hash-object`, export the resulting `VISUAL_TEST_IMAGE`, inspect
that tag, and invoke `docker compose build visual-tests` only when the image is absent. Preserve mode
validation, test execution, exit codes, and trap-based cleanup.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `pytest tests/test_visual_test_runner.py -q`

Expected: `2 passed`.

### Task 2: Make the visual image dependency-only

**Files:**
- Modify: `Dockerfile.visual-tests`
- Modify: `docker-compose.visual.yml`
- Modify: `tests/test_repository_foundation.py`

**Interfaces:**
- Consumes: `VISUAL_TEST_IMAGE` exported by the runner.
- Produces: a stable dependency image with runtime source provided by scoped bind mounts.

- [ ] **Step 1: Extend the repository contract test**

Update `test_visual_regression_runs_in_a_pinned_container_environment` to assert the Compose service
uses `${VISUAL_TEST_IMAGE:-photo-prjct-visual-deps:local}`, the Dockerfile does not copy the repository,
and the exact scoped read-only source/config mounts exist alongside writable output mounts.

- [ ] **Step 2: Run the contract test to verify it fails**

Run: `pytest tests/test_repository_foundation.py::test_visual_regression_runs_in_a_pinned_container_environment -q`

Expected: FAIL because the image key and scoped runtime mounts are not configured yet.

- [ ] **Step 3: Implement the dependency-only image and mounts**

Move the Node/Playwright dependency installation under `/opt/visual-test-deps`, expose its `.bin`
directory through `PATH`, keep Python packages global, remove `COPY . .`, and restore `/workspace` as
the runtime workdir. Add the keyed `image:` field and read-only mounts for `src`, `tests`,
`package.json`, and `playwright.config.js`; keep nested snapshot/report/result mounts writable.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_visual_test_runner.py tests/test_repository_foundation.py::test_visual_regression_runs_in_a_pinned_container_environment -q`

Expected: `3 passed`.

### Task 3: Verify real cache reuse and publish

**Files:**
- Modify: `docs/engineering-jobs.md`

**Interfaces:**
- Consumes: the completed runner, Dockerfile, and Compose configuration.
- Produces: verified local cache behavior and durable capability evidence.

- [ ] **Step 1: Record the optimized local behavior**

Update EJ-005 evidence text to state that local runs use a dependency-keyed visual-test image and
source-only changes reuse it without rebuilding Chromium.

- [ ] **Step 2: Run static and behavioral verification**

Run:

```sh
ruff format --check .
ruff check .
pytest tests/test_visual_test_runner.py tests/test_repository_foundation.py -q
docker compose -f docker-compose.visual.yml config
```

Expected: all commands exit `0`.

- [ ] **Step 3: Run the real visual workflow twice**

Run `npm run test:visual` twice. Expected: both suites pass; the first run may build the dependency
image, while the second starts without any `docker compose build` output or Chromium download.

- [ ] **Step 4: Commit and publish**

Commit the implementation, push `visual-local-cache`, create a pull request against `main`, and report
the PR plus exact verification results.

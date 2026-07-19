# Local Visual Test Dependency Cache Design

## Goal

Make repeated local visual-test runs reuse the same Docker image containing Python, Node.js,
Playwright, and Chromium. Editing application source, visual fixtures, or snapshots must not rebuild
that image or download Chromium again.

CI behavior is outside this change: clean GitHub-hosted runners may still build the dependency image
and download Chromium.

## Design

`Dockerfile.visual-tests` will become a dependency-only image. It will install the pinned backend and
Node dependencies plus Chromium, but it will not copy the repository source into the image.
`docker-compose.visual.yml` will mount only the runtime inputs needed by the tests: `src`, `tests`,
`package.json`, and `playwright.config.js`. Existing writable mounts for snapshots, reports, and test
results remain writable; application and test sources remain read-only.

`tests/visual/run-in-container.sh` will derive a deterministic image tag from the files that define
the dependency environment:

- `Dockerfile.visual-tests`
- `package-lock.json`
- `src/backend/requirements.txt`

If that tagged image is absent, the script builds it. If it exists, the script immediately runs the
tests without invoking `docker compose build`. Docker images persist across the existing Compose
cleanup, while PostgreSQL data and transient containers continue to be removed after every run.

## Behavior and failure handling

- The first run for a dependency key builds the image and then runs the requested test mode.
- Subsequent `test` and `update` runs with the same dependency key reuse that image.
- Changing a dependency-defining file selects a new tag and triggers one rebuild.
- Build or test failures retain their current nonzero exit behavior, and cleanup still runs through
  the shell trap.
- No `.env`, Git metadata, or the whole repository is bind-mounted into the container.

## Verification

Behavioral tests will execute the runner against a fake Docker CLI and verify build-once/reuse behavior
without inspecting shell-script prose. Repository contract tests will continue to validate the pinned
container boundary. A real two-run Docker check will confirm that the second run skips the build and
uses the already-tagged dependency image.

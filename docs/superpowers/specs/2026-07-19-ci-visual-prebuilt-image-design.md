# CI visual-test prebuilt image design

**Status:** Approved for planning  
**Date:** 2026-07-19  
**Related architecture:** `docs/architecture.md` — Current architecture, Accepted constraints  
**Related ADRs:** ADR 0003, ADR 0005  
**ADR impact:** Conforms to ADR 0003 and ADR 0005

## Outcome

Pull requests run the complete CI suite once, and ordinary visual-test jobs reuse a dependency-keyed
image from GHCR instead of reinstalling Chromium. A merge to `main` remains the only branch-push CI
event.

## Scope

- Keep `pull_request` CI for every pull request.
- Restrict the `push` CI trigger to `main`.
- Reuse the existing dependency key derived from `Dockerfile.visual-tests`, `package-lock.json`, and
  `src/backend/requirements.txt` as the GHCR image tag.
- Use `ghcr.io/${GITHUB_REPOSITORY}-visual-tests:<dependency-key>` in CI and retain the existing
  `photo-prjct-visual-deps:<dependency-key>` name locally.
- Authenticate CI to GHCR with the repository `GITHUB_TOKEN` and narrowly declared package
  permissions.
- Pull the keyed image before building. On a cache miss, build the existing pinned Dockerfile. Push
  a newly built image only from a `push` event on `main`; pull requests never publish packages.
- Keep the same Compose service, runtime source mounts, Playwright version, Chromium version, and
  Python dependencies.

## Data flow

The runner computes the dependency key and selects its image name. If the image is not already in
the local Docker daemon, CI first attempts a GHCR pull. A hit proceeds directly to the existing
Compose test command. A miss builds the image locally; `main` then publishes that keyed image before
running the visual suite. Later jobs with the same dependencies pull the image layers from GHCR.

Local runs do not receive a registry prefix, so they preserve the current build-once local cache and
never log in to or push to GHCR.

## Failure handling and security

- A missing registry image is a cache miss, not a test failure: CI falls back to the current build.
- A build failure or a failed `main` image push fails CI before the visual suite, preventing a green
  result that did not publish the reusable artifact.
- Pull requests receive read-only package behavior and cannot publish an image, including forked
  pull requests where GitHub downgrades token permissions.
- Dependency-keyed tags are created only after a successful image build and are not mutable aliases
  such as `latest`.

## Verification

Behavioral runner tests will cover local reuse, registry pull hits, cache-miss builds, main-only
publishing, and snapshot-update mode. Repository workflow tests will verify the `main`-only push
trigger, package permissions, GHCR authentication, and CI environment passed to the runner. Static
workflow parsing, shell syntax, focused pytest, Docker Compose configuration, and the real GitHub CI
run provide the final evidence.

## Alternatives considered

1. Use Microsoft's Playwright image. Rejected because it changes the current Debian-based rendering
   environment, includes more browser payload than required, and still needs exact Playwright
   version coordination.
2. Use only GitHub Actions BuildKit cache. Rejected as the primary mechanism because it keeps the
   browser inside an opaque build cache and does not provide one explicit reusable test artifact.
3. Publish the dependency-keyed current image to GHCR. Selected because it preserves rendering
   parity, matches the repository's existing GHCR operations, and has a direct build fallback.

## Rollback

Remove the GHCR login and registry environment from CI, restore unrestricted `push`, and leave the
runner's existing local build-once path in place. No deployed service, database, or persistent
application data changes.

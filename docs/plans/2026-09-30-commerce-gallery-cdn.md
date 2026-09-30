# Commerce gallery CDN implementation plan

- Date: 2026-09-30
- Status: Approved
- Related specification: maintainer request to deliver cart and order thumbnails through the gallery CDN.
- Related architecture: [current architecture](../architecture.md#current-architecture--implemented).
- ADR impact: conforms to [ADR 0045](../adr/0045-deliver-commerce-thumbnails-through-gallery-cdn.md), preserving ADRs 0029 and 0031.

## Scope and acceptance

Emit existing six-hour `gallery-v1` CDN URLs for small previews in cart, checkout cart presentation and browser/grant order pages when `gallery-cdn-images` is enabled for the viewer. Preserve existing page authorization, watermarked-source selection, hidden/unpublished order entitlement, gate-off routes, lightbox and download behavior. Missing required preview evidence fails closed. No schema, stored artifacts, workers, infrastructure or data migration changes.

## Implementation

Execute one cohesive task through `$execute-implementation-plan`: add focused failing commerce view tests, wire the existing preview issuer/signer into already-authorized page presentation, make the tests pass, normalize changed Python files and select package verification. Own `src/backend/commerce/views.py`, `src/backend/commerce/presentation.py` only if needed, and focused commerce tests. Reuse `picflow.gallery_preview_grants` and `GalleryImageUrlSigner`; add no speculative delivery abstraction. Cart lightbox remains its current large URL. Order capabilities use exact OrderItem membership and cached watermarked projection, never the current public-gallery queryset.

Root updates architecture/ADR links and performs independent review, final `make check`, selector-required suites, one commit, PR/CI and canonical deployment. Focus tests on HTML URL hosts and decoded accepted watermarked keys, flag policy, unauthorized/cross-order denial, realistic missing-preview failure and existing paid order entitlement. Reuse unchanged expensive-suite evidence only at the exact final package fingerprint.

## Verification and rollout

Run focused commerce tests with `make test TESTS="<changed test selectors>"`, `.venv/bin/pre-commit run --files <changed Python files>`, `.venv/bin/python scripts/select_test_suites.py select --base 87b06d1bacdaf1721c261817cc54edbc8a0b1b53`, final `make check`, and each selected expensive suite. Existing gallery flag is on in canonical runtime; application rollout therefore activates this extension. Existing origin already supports `gallery-v1`; no origin or CDN resource change is required. Before merge record live flags and deployed version; after deployment verify the exact image version, flags and sanitized generated CDN JPEG response. Do not create carts, orders, payment attempts or access grants in production for testing.

## Rollback and reconciliation

Revert this application change to restore commerce media routes without changing stored media or commerce data. Disabling the shared gallery gate also disables commerce CDN emission but affects the normal gallery. Root checks final implementation against ADR 0045 and updates implemented architecture facts before review. Worker/state/artifact release safeguards do not apply: no worker, durable state or derived artifact changes. Open questions: none.

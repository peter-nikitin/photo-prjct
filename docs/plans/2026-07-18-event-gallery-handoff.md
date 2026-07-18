# Event gallery planning handoff

- Date: 2026-07-18
- Status: Paused after spec review
- Branch: `event-gallery`
- Worktree: `.worktrees/event-gallery`
- Last committed design revision: `b9e8802`
- Implementation: not started
- Cloud mutations: not started and not authorized

## User-approved direction

The work is intentionally split into two independently planned parts.

1. The event gallery receives a presentation model derived from `Photo`. That model encapsulates
   the preview and enlarged media choices, so adding a dedicated thumbnail later does not change the
   page contract. Stable photo identity remains the future bridge to a server-validated cart item.
2. The thirteen legacy seed-photo rows should reference fixed private S3 originals uploaded once.
   Creating a new developer database or redeploying a dev stand must never create more S3 objects.

The gallery should use the visual language of the original prototype from PR 1, but only as a
simple responsive list with an enlarged lightbox view. Search, recognition, selection, pricing,
checkout, and downloads remain out of scope.

## Artifacts written

- `docs/superpowers/specs/2026-07-18-event-photo-gallery-design.md`
- `docs/superpowers/specs/2026-07-18-staging-seed-photo-media-design.md`

Both specifications are marked `Needs revision after spec review`. No implementation plan should be
written until the blocking review findings below are resolved and the specifications pass another
review.

## Verified repository state

- Main checkout was left unchanged; its pre-existing `.gitignore` modification belongs to the user.
- The isolated worktree started from `93c0394`.
- Baseline verification passed:

  ```text
  DB_HOST=127.0.0.1 pytest -q \
    src/backend/picflow/tests/test_models.py \
    src/backend/picflow/tests/test_views.py \
    tests/test_repository_foundation.py

  38 passed, 13 subtests passed
  ```

- The local database contained 14 photos: thirteen legacy `src` rows and one uploaded
  `original_key` row.
- `picflow/migrations/0001_initial.py` creates only the historical `Photo(id, event, src)` shape.
  Private-original fields appear in `0003`; the uploaded-row constraints are added and validated in
  `0004` and `0005`.
- The existing private-storage key validator accepts only `originals/<32 lowercase hex chars>` for
  final objects.
- PhotoSwipe was rejected during design verification because it requires intrinsic image dimensions
  that `Photo` does not store. GLightbox 3.3.1 is the proposed local, MIT-licensed lightbox.

## Blocking spec-review findings

### Event gallery

1. The media URL contract is inconsistent. The spec uses “stable”, “opaque short-lived
   capability”, deterministic event/photo/variant endpoint, and signed fallback terminology without
   choosing one routing, expiry, authorization, and caching model.
2. The factory is described as a pure converter, but another section asks it to omit photos after
   storage resolution failures. Those failures can occur only when the media endpoint reads S3, not
   while the event page is built.
3. Streaming behavior is incomplete. The contract must distinguish failures before response headers
   from failures after streaming begins and must guarantee that the S3 body closes on normal
   completion, exception, and client disconnect.
4. Cache and revocation semantics are undefined for a stable public URL when an event is later
   unpublished. Exact `Cache-Control`, `Content-Length`, ETag, and revocation behavior are required.

### Seed media

1. An unconditional data migration would create rows in local and future production databases that
   reference staging-only bucket keys. The seed mechanism must be explicitly environment-scoped
   without making schema migrations depend on environment variables or S3 availability.
2. The inactive seed-uploader contract lacks an exact identifier, collision checks,
   historical-model-safe password value, database-alias usage, and reverse lifecycle.
3. The proposed manifest says checksums are committed but does not define a checksum field.
   `HEAD` alone is insufficient to prove bytes match the intended source.
4. “Upload exactly once” is not yet race-safe. The runbook needs compare-and-skip behavior for a
   matching object and conditional create-or-abort behavior for an absent or conflicting key.
5. Legacy-object deletion appears in the workflow without being in scope or having an inventory and
   rollback. Cleanup must be removed from this increment or specified as a separate destructive
   operation.

## Recommended decisions for the next session

These are recommendations, not yet approved spec text.

### Gallery media contract

- Use one deterministic Django endpoint addressed by event slug, photo ID, and requested variant.
- Do not sign the application URL and do not redirect to a presigned S3 URL; a redirect would expose
  the permanent object key.
- Keep `GalleryPhotoFactory` database-only. It creates URLs for eligible rows and performs no S3
  calls. Storage failures belong exclusively to the media endpoint.
- Restrict original-backed rendering to published free events. Paid originals remain unavailable
  until a watermarked preview exists.
- Return `404` for missing or ineligible media. Map S3 failures that occur before headers to a
  sanitized `503`. If the body fails after headers, log the safe photo identity, close the S3 body,
  and terminate the stream; the status can no longer be changed.
- Require the iterator or response wrapper to close the S3 body in `finally`, including client
  disconnects.
- Prefer `Cache-Control: no-store` for the first mechanism so unpublishing revokes access on the next
  request. Return safe `Content-Type`, `Content-Length`, `Content-Disposition: inline`, and
  `X-Content-Type-Options: nosniff`. Defer Range and conditional GET support.

### Environment-scoped seed records

- Do not put staging object references in an unconditional Django data migration.
- Prefer an idempotent, database-only management command such as `seed_reference_photos`, invoked
  explicitly for staging/dev environments after normal migrations. It must never upload or inspect
  S3 and must not run in production entrypoint paths unless separately enabled by an explicit
  environment contract.
- Use an exact inactive username, for example `findme-photo-seed-uploader`, and require all expected
  flags before reuse. Store the historical-model-safe unusable password sentinel `!`; do not assume
  `set_unusable_password()` exists on a historical model.
- Use the selected database alias for every ORM operation. Define cleanup only for rows and the
  uploader that still exactly match the seed contract.
- Put photo ID, event slug, source Git blob path, destination key, filename, byte size, content type,
  and SHA-256 in one committed manifest consumed by command tests and the operator runbook.
- Make the one-time object operation compare first. Matching size and checksum means skip; a
  conflicting existing object means abort. Creating an absent object must use a conditional
  no-overwrite mechanism supported by the chosen S3 client or stop for a reviewed alternative.
- Keep S3 deletion out of this increment. Any later cleanup requires a separate exact inventory,
  read-only reference check, rollback description, and fresh destructive-action confirmation.

## Source asset evidence

The six prototype blobs referenced by the thirteen historical rows still exist in Git commit
`cb4ce51`. Their measured metadata is:

| Source asset | Bytes | SHA-256 |
| --- | ---: | --- |
| `run-city-1842.png` | 112267 | `f62fc170134dc541db3f923e8be4451d4a6116dfacc79d3d738c2fbe2175e2d4` |
| `run-track-1190.png` | 108295 | `e034e81322e3bc68d86a2b530b15b43101f9f6b834a1d7092f1d327e435a18ef` |
| `run-finish-1842.png` | 113160 | `baeebc3924d57505bb55476f0b808e715d8502954e1a8bb59ed5fe2fbbbd2ae4` |
| `run-park-1204.png` | 107844 | `fd25fc1c10dea881f94dba41009e34533259592252abdd5ec01bd9c436a89356` |
| `run-finish-1204.png` | 113569 | `5b6fd0f142b21ae49427d994409c197f1b109bc9d427438497c1fd98a71dec4e` |
| `run-expo-3125.png` | 111727 | `aa6e51a45f9cfea60fbbda593de9b3b0ec38dd7b592b050cfe6e02afd7bf3219` |

The thirteen rows reuse these six byte sequences. Because `Photo.original_key` is globally unique,
keeping all thirteen photo IDs requires thirteen distinct final keys even when bytes repeat. Those
copies are created once, not once per database or deployment.

## Exact continuation sequence

1. Revise the two specifications to resolve every blocking item above.
2. Re-run the independent spec review until both specifications are approved.
3. Ask the maintainer to review the revised spec files.
4. Only after approval, create two separate decision-complete implementation plans under
   `docs/plans/`: one for the gallery and one for seed media.
5. Run independent plan review for each plan chunk.
6. Stop again before implementation unless the maintainer explicitly asks to proceed.
7. Immediately before any Yandex Object Storage mutation, show the active non-secret cloud/folder
   context, exact bucket/object targets, commands, impact, verification, rollback, and price delta or
   state that it is unknown; then obtain fresh explicit confirmation.


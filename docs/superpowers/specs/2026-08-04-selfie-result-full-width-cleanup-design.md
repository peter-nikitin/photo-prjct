# Selfie result full width and cleanup UI removal

## Status

Approved follow-up to `2026-08-04-selfie-feedback-bottom-layout-design.md`.

## Product outcome

The selfie-search result page uses the same 1180 px content width as the ordinary event gallery and
does not expose an internal browser-storage cleanup action to customers.

## Behavior

- Remove the explicit 900 px maximum from `.selfie-search-result`; the shared `.page-shell` width
  (`--content-width: 1180px`) becomes authoritative.
- Keep the 900 px maximum on `.selfie-search`, the event-page search entry form.
- Remove the `Повторить очистку` button from submitted-feedback markup.
- Remove the retry button lookup, click handler, and visibility state from feedback-cleanup
  JavaScript.
- Automatic local selfie deletion after submission remains. If it fails, keep the existing cleanup
  error message; do not offer an in-page retry action.
- Do not change server feedback, consent, object-storage, or submission contracts.

## Verification

Use only focused markup/JavaScript assertions and the affected desktop/mobile selfie-result visual
snapshots. Confirm that production code contains no cleanup-retry selector or copy and that the
desktop result container matches the ordinary gallery width.


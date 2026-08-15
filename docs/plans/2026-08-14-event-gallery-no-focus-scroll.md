# Event Gallery Without Programmatic Focus Scrolling Implementation Plan

- Date: 2026-08-14
- Status: Approved
- Owner: project maintainer
- Related specification: [Event gallery without programmatic focus scrolling](../superpowers/specs/2026-08-14-event-gallery-no-focus-scroll-design.md)
- Related architecture: [Architecture](../architecture.md)
- Related ADRs: [ADR 0024](../adr/0024-use-gallery-face-as-search-query.md)
- ADR impact: None — reversible implementation detail conforming to ADR 0024

## Goal

Implement the approved removal of gallery-owned programmatic focus restoration.

## Scope

None beyond the approved specification.

## Acceptance criteria

The focused Node suite must prove both close paths no longer call `.focus()` while preserving the
existing chooser and lightbox behavior.

## Implementation

Execute this plan with `$execute-implementation-plan`.

### Task 1: Remove gallery focus restoration

**Files:** `tests/js/event-gallery.test.js`, `src/backend/static/ui/event-gallery.js`.

- **Specification:** Goal, Scope, Acceptance criteria, Testing.
- **Depends on:** None.
- **Produces:** Event gallery close paths without programmatic focus or scroll restoration.

- [ ] Change the focused JS expectations so chooser and GLightbox closure require zero focus calls.
- [ ] Run `node --test tests/js/event-gallery.test.js` and confirm failure from the existing calls.
- [ ] Remove the chooser restore-focus option/call and the lightbox trigger tracking/on-close call.
- [ ] Run `node --test tests/js/event-gallery.test.js` and expect 7 passing tests, 0 failures.
- [ ] Run `git diff --check` and expect no output.

### Final task: Architecture and ADR reconciliation

- [ ] Confirm the result remains a reversible UI detail inside ADR 0024.
- [ ] Confirm no architecture document or ADR update is required.
- [ ] Record the reconciliation outcome in delivery notes.

## Verification

- `node --test tests/js/event-gallery.test.js` — 7 tests pass with 0 failures.
- `git diff --check` — no whitespace errors.
- Live browser check after deployment — closing either surface does not change the gallery scroll
  position through programmatic focus restoration.

## Operational impact and rollout

No configuration, migration, or special deployment ordering. The normal application deployment is
sufficient.

## Rollback

Revert the change; there are no persistent data effects.

## Open questions

None.

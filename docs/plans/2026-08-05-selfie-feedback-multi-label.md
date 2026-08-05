# Selfie Feedback Multi-Label Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- Date: 2026-08-05
- Status: Approved for execution
- Owner: project maintainer
- Related specification: [Selfie-search multi-photo feedback](../superpowers/specs/2026-08-05-selfie-feedback-multi-label-design.md)
- Related architecture: [Current architecture — implemented](../architecture.md#current-architecture--implemented), [Accepted constraints](../architecture.md#accepted-constraints)
- Related ADRs: [ADR 0019](../adr/0019-use-public-event-selfie-search.md), [ADR 0023](../adr/0023-store-consented-selfie-search-feedback.md)
- ADR impact: Conforms to ADR 0023. One immutable feedback per search, explicit consent, browser-local draft labels, feedback-selfie handling, access, and retention remain unchanged. Making the existing contact value optional is a reversible product-validation detail and requires no new ADR.

**Goal:** Preserve one final immutable feedback submission per search while allowing any number of photo labels in that submission and moving an optional contact field under a collapsed disclosure.

**Architecture:** Keep the existing browser-local `FeedbackMarkStore`, single CSRF-protected multipart submission, and one-to-one feedback model. Accept an empty string through the form and model, remove the database non-empty constraint, and change only the canonical production template/CSS/JavaScript plus their focused tests and snapshots.

**Tech Stack:** Django 6 forms/models/migrations/templates, browser JavaScript with `sessionStorage`, CSS, Node test runner, and Playwright visual snapshots.

## Global constraints

- One search accepts at most one immutable feedback record; no update endpoint, autosave request, compatibility path, or second feedback record is added.
- Before submission, `Я есть` and `Меня нет` remain mutually exclusive per photo, any number of photos may be labelled, unlabelled photos are allowed, and the draft remains scoped to the search across numbered result pages.
- After a successful submission, labels are not editable and the page shows `Спасибо, отзыв отправлен.`
- The collapsed disclosure label is exactly `Оставить контакт для связи — необязательно`; its hint remains `Телефон, Telegram или email`.
- The required consent copy is exactly `Я согласен на обработку моего селфи и оценки результатов поиска для анализа качества поиска, а если оставлю контакт — также контактных данных для связи со мной в соответствии с Политикой обработки персональных данных.` with the policy title remaining a link.
- The consent-text version changes from `2026-08-04` to `2026-08-05` with that customer-copy change.
- An empty contact is stored as an empty string; a non-empty contact is trimmed and continues to reject more than 254 characters or control characters.
- Existing selfie retention, private storage, bearer authorization, idempotent resubmission, failure-state draft preservation, and post-submission browser cleanup remain unchanged.
- The production screen remains canonical in `src/backend/selfie_search/templates/selfie_search/result.html`; no design-reference screen is added.
- The implementer must leave all changes unstaged and must not commit, push, or modify Git history or the index.

---

## Goal

Implement the approved specification without scope changes.

## Scope

None beyond the approved specification.

## Acceptance criteria

- A ready search permits multiple `present`/`absent` labels in one final submission, including labels collected across numbered result pages.
- A blank contact passes browser and server validation; unsafe non-empty contact still fails.
- The optional contact control is collapsed initially on desktop and mobile.
- An already-submitted search exposes neither the form nor marking controls and remains immutable.

## Implementation

### Task 1: Deliver final multi-photo feedback with optional contact

**Files:**

- Modify: `src/backend/selfie_search/forms.py`
- Modify: `src/backend/selfie_search/models.py`
- Create: `src/backend/selfie_search/migrations/0003_optional_feedback_contact.py`
- Modify: `src/backend/selfie_search/templates/selfie_search/result.html`
- Modify: `src/backend/static/ui/selfie-search.js`
- Modify: `src/backend/static/ui/selfie-search.css`
- Modify: `src/backend/selfie_search/tests/test_forms.py`
- Modify: `src/backend/selfie_search/tests/test_models.py`
- Modify: `src/backend/selfie_search/tests/test_feedback_submission.py`
- Modify: `src/backend/selfie_search/tests/test_views.py`
- Modify: `tests/js/selfie-search.test.js`
- Modify when intentional rendering changes are accepted: `tests/visual/visual.spec.js-snapshots/desktop-selfie-search-feedback-problem.png`
- Modify when intentional rendering changes are accepted: `tests/visual/visual.spec.js-snapshots/mobile-selfie-search-feedback-problem.png`
- Modify when intentional rendering changes are accepted: `tests/visual/visual.spec.js-snapshots/desktop-selfie-search-feedback-marking.png`
- Modify when intentional rendering changes are accepted: `tests/visual/visual.spec.js-snapshots/mobile-selfie-search-feedback-marking.png`

- **Specification:** Goal, User flow, Optional contact, Data and privacy contract, Failure handling, Verification.
- **Depends on:** Existing `FeedbackMarkStore`, feedback submission service, immutable feedback model, and result-page feedback presentation.
- **Produces:** One complete, reviewable working-tree change; no new cross-task interface.

- [ ] Add failing form/model tests proving blank and whitespace-only contact are normalized to `""`, while a supplied contact containing a control character and an overlength non-empty contact remain invalid; update consent-version expectations to `2026-08-05`.
- [ ] Add or strengthen failing view/JavaScript tests proving the native contact disclosure is collapsed and optional, an empty `contact` is included in the multipart request, multiple labels are submitted together, and the post-submission state remains final.
- [ ] Run `make test TESTS="selfie_search.tests.test_forms.FeedbackSubmissionFormTests selfie_search.tests.test_models.SelfieSearchModelTests selfie_search.tests.test_feedback_submission selfie_search.tests.test_views.SelfieSubmissionFeedbackTests selfie_search.tests.test_views.PublicSelfieResultViewTests"` and confirm failures are limited to the new optional-contact/template expectations.
- [ ] Run `npm run test:js` and confirm failures are limited to the new optional-contact client behavior.
- [ ] Set `FeedbackSubmissionForm.contact` to `required=False`; trim empty and non-empty values in `clean_contact`, returning `""` for blank input, preserving control-character rejection for supplied values, and set `FEEDBACK_CONSENT_TEXT_VERSION = "2026-08-05"`.
- [ ] Set `SelfieSearchFeedback.contact` to `blank=True`; allow the normalized empty string in `clean`; remove `selfie_feedback_contact_nonempty_chk`; generate `0003_optional_feedback_contact.py` with only the corresponding `AlterField` and `RemoveConstraint` operations.
- [ ] Remove the client-side blank-contact rejection without changing consent, selfie availability, error retry, label-store, or cleanup behavior.
- [ ] Wrap the contact label, input, hint, and contact error in a closed native `<details>` using the exact disclosure copy from Global constraints; remove the HTML `required` attribute; apply the exact conditional consent copy and keep the policy link intact.
- [ ] Add focused CSS for an accessible native summary and its expanded body, reusing existing design tokens and maintaining the current single-column mobile layout.
- [ ] Run the targeted Django and JavaScript commands again; expect all selected tests to pass.
- [ ] Run `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run`; expect `No changes detected`.
- [ ] Run `npm run test:visual:update` once, inspect all four changed feedback snapshot images, and reject unrelated baseline changes.
- [ ] Run `npm run test:visual`; expect all visual tests to pass without updating snapshots.
- [ ] Run `git diff --check`; expect no whitespace errors. Self-review the entire unstaged diff against the specification and report exact test counts and any concerns.

### Final task: Architecture and ADR reconciliation

- [ ] Compare the delivered behavior with the approved specification, ADR 0019, ADR 0023, and the two linked architecture sections.
- [ ] Confirm that browser-local selfie retention, one-search/one-feedback immutability, consent evidence, private feedback media, and staff access are unchanged.
- [ ] Confirm that `docs/architecture.md` needs no update because the existing implemented statement does not claim contact is mandatory.
- [ ] Record the reconciliation outcome in the pull request; stop instead of contradicting an accepted ADR.

## Verification

- `make test TESTS="selfie_search.tests.test_forms.FeedbackSubmissionFormTests selfie_search.tests.test_models.SelfieSearchModelTests selfie_search.tests.test_feedback_submission selfie_search.tests.test_views.SelfieSubmissionFeedbackTests selfie_search.tests.test_views.PublicSelfieResultViewTests"` — selected Django tests pass.
- `npm run test:js` — all JavaScript unit tests pass.
- `.venv/bin/python src/backend/manage.py makemigrations --check --dry-run` — prints `No changes detected`.
- `npm run test:visual` — all visual tests pass against inspected intentional snapshots.
- `git diff --check` — no whitespace errors.
- After independent review fixes, the root controller runs `make check`; the full repository suite passes before staging or commit.

## Operational impact and rollout

The deployment applies one Django migration that removes the non-empty database check and records `blank=True` in migration state; it does not rewrite rows or change column nullability. No configuration, credential, bucket, KMS, lifecycle, worker, or endpoint change is required. Normal merge-to-main staging deployment remains the rollout path; live activation is outside this plan.

## Rollback

Disable feedback through the existing feature flag for immediate behavioral rollback. Reverting the code and migration is safe while all new empty-contact rows are absent; after an empty-contact feedback row exists, restoring the non-empty constraint requires deleting or correcting those rows and is therefore an explicit data operation, not an automatic rollback step.

## Open questions

None.

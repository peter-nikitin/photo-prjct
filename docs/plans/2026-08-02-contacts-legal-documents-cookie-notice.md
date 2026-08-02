# Contacts, Legal Documents, Cookie Notice, and Yandex Metrika Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

- Date: 2026-08-02
- Status: Ready for implementation
- Owner: project maintainer
- Related specification:
  [Contacts, Legal Documents, Cookie Notice, and Yandex Metrika Design](../superpowers/specs/2026-08-02-contacts-legal-documents-cookie-notice-design.md)
- Related architecture: [Current architecture — implemented](../architecture.md#current-architecture--implemented)
- Related ADRs: none
- ADR impact: None — reversible implementation detail

## Goal

Deliver the outcome and acceptance criteria in the
[approved specification](../superpowers/specs/2026-08-02-contacts-legal-documents-cookie-notice-design.md#outcome):
a public contacts/document catalog with the three accepted PDFs, plus the approved global cookie
notice and Yandex Metrika counter on eligible user-facing Django pages. Public selfie bearer-result
pages remain eligible for the notice but suppress Metrika, because its bootstrap reports
`location.href` and that URL carries a non-expiring authorization token.

## Architecture

Keep Django templates and packaged static assets as the production source of truth. The existing
`/legal/` route serves the contacts/document catalog; the shared `ui/base.html` owns the notice and
analytics inclusion. A small context processor makes the fixed production counter suppressible in
the test-only visual settings and for requests marked by the existing public-selfie bearer
protection middleware, without introducing runtime configuration, template URL matching, or a new
service.

## Tech stack

Django 6 templates and staticfiles, WhiteNoise manifest storage, plain JavaScript and `localStorage`,
CSS, pytest/Django test client, and Playwright visual/browser tests.

## Global constraints

- Treat the approved specification as authoritative; do not edit or re-export the supplied PDFs.
- Preserve the accepted SHA-256 values and publish no standalone email contact.
- Render `+7 (903) 127-57-66` as `tel:+79031275766`.
- Use the exact approved Russian notice copy, `OK`, and
  `findme_cookie_notice=2026-08-02`.
- Load Yandex Metrika counter `111239706` immediately with the exact supplied initialization
  options; acknowledgement does not gate analytics.
- Emit no production analytics from Django Admin, non-HTML endpoints, or test-only visual pages.
- Emit no Metrika bootstrap or no-JavaScript pixel from a valid public selfie bearer-result page;
  retain its cookie notice.
- Add no dependency, model, migration, CMS, or external document store.

## Scope

Implements the approved specification without scope changes.

## File structure

- `src/backend/static/ui/legal/public-offer.pdf`: immutable supplied public offer.
- `src/backend/static/ui/legal/user-agreement.pdf`: immutable supplied user agreement.
- `src/backend/static/ui/legal/personal-data-policy.pdf`: immutable supplied combined consent and
  personal-data policy; also the notice's policy target.
- `src/backend/templates/ui/legal.html`: contacts and document-link catalog.
- `src/backend/static/ui/catalog.css`: legal-page-only presentation.
- `src/backend/config/context_processors.py`: exposes the configured Metrika counter ID to shared
  templates.
- `src/backend/config/settings.py`: fixed production counter setting and context-processor wiring.
- `tests/visual/settings.py`: disables the real counter in the test-only visual environment.
- `src/backend/templates/ui/base.html`: shared notice markup, Metrika bootstrap, no-JavaScript pixel,
  and notice script inclusion.
- `src/backend/static/ui/cookie-notice.js`: versioned acknowledgement behavior and storage failure
  handling.
- `src/backend/static/ui/design-system.css`: shared fixed-notice desktop/mobile/accessibility styles.
- `src/backend/picflow/tests/test_views.py`: server-rendered page, assets, counter, and suppression
  contracts.
- `src/backend/selfie_search/tests/test_views.py`: bearer-result analytics-suppression regression
  contract.
- `tests/test_visual_reference.py`: deterministic visual-setting contract.
- `tests/visual/visual.spec.js`: notice behavior, persistence, failure-path, outbound-analytics, link,
  and responsive checks.
- `tests/visual/snapshots/`: approved legal-page and dedicated cookie-notice desktop/mobile baselines
  using the repository's existing snapshot naming convention.

## Acceptance criteria

The plan is complete when all eight
[specification acceptance criteria](../superpowers/specs/2026-08-02-contacts-legal-documents-cookie-notice-design.md#acceptance-criteria)
pass and staging verification observes the same behavior from the deployed immutable image.

## Implementation

### Task 1: Publish the contacts page and exact legal PDFs

**Deliverable:** `/legal/` shows the approved phone and three working document links, backed by
byte-identical packaged PDFs and responsive FindMe Photo styling.

**Files:**

- Create: `src/backend/static/ui/legal/public-offer.pdf`
- Create: `src/backend/static/ui/legal/user-agreement.pdf`
- Create: `src/backend/static/ui/legal/personal-data-policy.pdf`
- Modify: `src/backend/templates/ui/legal.html`
- Modify: `src/backend/static/ui/catalog.css`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `tests/visual/visual.spec.js`
- Update: legal desktop/mobile files under `tests/visual/snapshots/`

**Specification:**
[Accepted inputs](../superpowers/specs/2026-08-02-contacts-legal-documents-cookie-notice-design.md#accepted-inputs),
[Contacts and documents page](../superpowers/specs/2026-08-02-contacts-legal-documents-cookie-notice-design.md#contacts-and-documents-page),
and acceptance criteria 1–2 and the relevant portion of 8.

**Depends on:** None.

**Produces:** These exact static template targets for Task 2:
`ui/legal/public-offer.pdf`, `ui/legal/user-agreement.pdf`, and
`ui/legal/personal-data-policy.pdf`.

- [ ] Add failing `PublicShellTests` assertions that `/legal/` contains
  `tel:+79031275766`, contains no `mailto:`, resolves the three expected static links, and no longer
  renders the four placeholder sections. Add a repository-file assertion that each packaged PDF's
  SHA-256 equals the accepted value.
- [ ] Run
  `SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=testserver /Users/petrnikitin/Documents/Projects/photo-prjct/.venv/bin/pytest -q src/backend/picflow/tests/test_views.py::PublicShellTests`
  and confirm failure because the catalog and packaged files do not exist yet.
- [ ] Copy each source file from `/Users/petrnikitin/Downloads/Фотобанк 2` to its specified static
  path without transforming it. Replace the placeholder legal markup with the phone and three
  `{% static %}` document links, retaining the shared shell and `/legal/` route.
- [ ] Adjust only the legal-page rules in `catalog.css` for a readable document list, visible focus,
  and single-column narrow-screen layout; do not introduce an alternate design system.
- [ ] Re-run the targeted pytest command and confirm all `PublicShellTests` pass. Independently run
  `shasum -a 256 src/backend/static/ui/legal/*.pdf` and compare every result with the specification.
- [ ] Update only the legal desktop/mobile visual baselines with `npm run test:visual:update`, inspect
  both rendered images for clipping, broken Cyrillic, phone/link focus, and horizontal overflow,
  then run `npm run test:visual` and confirm the full browser suite passes with all `/legal/` links
  returning below HTTP 400.
- [ ] Prepare the complete unstaged task diff for independent review. After approval, the root
  controller reruns the targeted checks, stages only this task's files, and creates its single task
  commit.

### Task 2: Add the global cookie notice and Yandex Metrika

**Deliverable:** Every eligible production user-facing base-template page emits one approved counter
and a resilient versioned notice; valid public selfie bearer-result pages retain the notice but emit
no counter. Test-only visual pages emit no real counter and exercise the notice deterministically.

**Files:**

- Create: `src/backend/config/context_processors.py`
- Modify: `src/backend/config/settings.py`
- Modify: `tests/visual/settings.py`
- Modify: `src/backend/templates/ui/base.html`
- Create: `src/backend/static/ui/cookie-notice.js`
- Modify: `src/backend/static/ui/design-system.css`
- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `src/backend/selfie_search/tests/test_views.py`
- Modify: `tests/test_visual_reference.py`
- Modify: `tests/visual/visual.spec.js`
- Create/update: dedicated cookie-notice desktop/mobile files under `tests/visual/snapshots/`

**Specification:**
[Cookie notice](../superpowers/specs/2026-08-02-contacts-legal-documents-cookie-notice-design.md#cookie-notice),
[Yandex Metrika](../superpowers/specs/2026-08-02-contacts-legal-documents-cookie-notice-design.md#yandex-metrika),
[Failure and compatibility behavior](../superpowers/specs/2026-08-02-contacts-legal-documents-cookie-notice-design.md#failure-and-compatibility-behavior),
and acceptance criteria 3–8.

**Depends on:** Task 1's `ui/legal/personal-data-policy.pdf` static target.

**Produces:**

- `analytics(request) -> dict[str, int | None]` with template key
  `yandex_metrika_counter_id`;
- DOM hooks `[data-cookie-notice]` and `[data-cookie-notice-accept]`;
- static entrypoint `ui/cookie-notice.js` using key `findme_cookie_notice` and version
  `2026-08-02`.

- [ ] Add failing server-rendered tests proving eligible production public pages contain exactly one
  counter ID/bootstrap, the no-JavaScript watch URL, exact notice copy, the personal-data-policy
  link, the two data hooks, and the deferred local script. Add a valid bearer-result regression
  proving it contains no `mc.yandex.ru` while a normal public catalog page still has exactly one
  bootstrap. Add suppression tests under `YANDEX_METRIKA_COUNTER_ID=None`, plus a visual-settings
  assertion that the real counter is disabled.
- [ ] Add failing Playwright cases for a fresh profile, `OK` dismissal and reload persistence,
  stale-version redisplay, a thrown `localStorage` read/write path that leaves the page operable,
  keyboard activation, mobile non-overflow, and absence of requests to `mc.yandex.ru` in the visual
  environment. Keep existing page snapshots stable by preloading the accepted value in the common
  capture helper; add dedicated fresh-profile desktop/mobile notice snapshots instead.
- [ ] Run the focused pytest command from Task 1 plus
  `SECRET_KEY=test DEBUG=False ALLOWED_HOSTS=testserver /Users/petrnikitin/Documents/Projects/photo-prjct/.venv/bin/pytest -q tests/test_visual_reference.py`
  and `npm run test:visual`; confirm the new assertions fail for missing settings, markup, script,
  and behavior.
- [ ] Define fixed production setting `YANDEX_METRIKA_COUNTER_ID = 111239706`, add the named context
  processor to the existing Django template backend, and override the setting to `None` in
  `tests/visual/settings.py`. Have the context processor also suppress the counter for the existing
  public-selfie bearer middleware marker; do not inspect URLs in the template. Keep the value out of
  `.env` because it is public, fixed, and not a secret.
- [ ] Add one guarded Metrika block to `ui/base.html`: render it only when
  `yandex_metrika_counter_id` is not `None`, retain the supplied asynchronous bootstrap and exact
  initialization options, and include the supplied no-JavaScript image. Add the notice outside
  `main` with its policy link, data hooks, and `defer` script include.
- [ ] Implement `cookie-notice.js` as a dependency-free IIFE: read the exact version under a
  `try/catch`, hide the notice only on an exact match, and on `OK` attempt the write before hiding;
  if either storage operation throws, leave the notice usable and never block the page.
- [ ] Add shared fixed-notice styles with a layer above page content but below the focused skip link,
  a 44px minimum button target, visible focus, contained text width, and a vertical narrow-screen
  layout. Do not alter unrelated catalog/gallery styling.
- [ ] Re-run the focused pytest and Playwright cases until green. Update and inspect the two
  dedicated notice snapshots, then run `npm run test:visual` and confirm all existing and new
  browser checks pass without external Metrika traffic.
- [ ] Prepare the complete unstaged task diff for independent review. After approval, the root
  controller reruns the targeted checks, stages only this task's files, and creates its single task
  commit.

### Final task: Full verification, deployment, and architecture reconciliation

**Deliverable:** The approved implementation passes repository-wide regression gates, deploys
through the normal immutable-image pipeline, and is verified on the public staging environment.

**Files:** No planned source changes; update this plan only if verification uncovers a documentation
fact that must be recorded.

**Specification:** All sections and acceptance criteria.

**Depends on:** Approved Task 1 and Task 2 commits.

- [ ] Run formatting, lint, typing, full pytest coverage, Django checks, migration drift, JavaScript,
  and visual regression using the exact commands in **Verification**. Do not overlap full pytest
  runs.
- [ ] Compare the final diff and rendered behavior line by line with all eight specification
  acceptance criteria. Confirm the three packaged hashes again from the final Git tree.
- [ ] Compare delivered behavior with `docs/architecture.md` and `docs/adr/README.md`. Record
  `None — reversible implementation detail; no architecture or ADR update required` in the pull
  request unless the implementation changed a documented system boundary; stop instead of silently
  widening the scope.
- [ ] Push the reviewed branch, open a draft pull request, wait for required CI, address only
  blocking findings under `AGENTS.md`, and merge after CI and review approval.
- [ ] Let the normal `main` workflow build and deploy the immutable image to staging. Verify the
  deployed commit/image rather than a mutable checkout.
- [ ] On `https://findme-photo.ru/` and `/legal/`, verify the phone, all three PDFs, fresh-profile
  notice, `OK` persistence, responsive layout, one `tag.js?id=111239706` request, and a successful
  Metrika watch request. Open a valid public selfie bearer-result and verify its notice remains but
  neither a `tag.js` nor a Metrika watch request is made. Confirm `/admin/` HTML contains neither
  the counter nor notice and `/health/` remains the unchanged JSON response.
- [ ] Inspect application/edge logs for new 4xx/5xx failures and confirm Metrika begins receiving
  public page views. Report separately anything not observable immediately in the Metrika UI.

## Verification

Use the repository virtual environment and CI-equivalent PostgreSQL variables. Start one local
PostgreSQL 16 service if it is not already available; never point these commands at staging.

```bash
export SECRET_KEY=ci-not-a-secret
export DEBUG=False
export ALLOWED_HOSTS=localhost,127.0.0.1,testserver
export DB_NAME=app
export DB_USER=app
export DB_PASSWORD=app
export DB_HOST=localhost
export DB_PORT=5432

/Users/petrnikitin/Documents/Projects/photo-prjct/.venv/bin/ruff format --check .
/Users/petrnikitin/Documents/Projects/photo-prjct/.venv/bin/ruff check .
/Users/petrnikitin/Documents/Projects/photo-prjct/.venv/bin/mypy
/Users/petrnikitin/Documents/Projects/photo-prjct/.venv/bin/pytest --cov --cov-report=term-missing
/Users/petrnikitin/Documents/Projects/photo-prjct/.venv/bin/python src/backend/manage.py check
/Users/petrnikitin/Documents/Projects/photo-prjct/.venv/bin/python src/backend/manage.py makemigrations --check --dry-run
npm run test:js
npm run test:visual
shasum -a 256 src/backend/static/ui/legal/*.pdf
git diff --check
```

Expected outcomes: every command exits zero; pytest retains the configured repository coverage
gate; Django reports no issues and no migration changes; Playwright reports no failed browser or
snapshot cases; and PDF hashes exactly match the specification.

## Operational impact and rollout

- No database migration, data backfill, secret, cost change, or environment variable is required.
- `collectstatic` packages the PDFs, notice script, and CSS into the normal immutable web image;
  WhiteNoise serves them through the existing manifest static storage.
- The standard merged-`main` workflow deploys the candidate image to the current staging
  environment. Metrika network traffic begins as soon as the new HTML is served.
- Verify browser behavior and outbound counter requests after deployment; treat a blocked analytics
  request as non-blocking for site operation but report whether it is an application defect,
  network policy, or client-side blocker.

## Rollback

Redeploy the last verified image through the existing deployment workflow. This removes the page
changes, packaged PDFs, notice, and counter from served HTML. Previously written
`findme_cookie_notice` values may remain in visitor browsers but are inert without the script and
contain no personal data. Metrika data already received is not deleted by an application rollback;
no database or media rollback is needed.

## Open questions

None.

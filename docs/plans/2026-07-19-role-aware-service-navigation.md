# Role-aware service navigation implementation plan

- Date: 2026-07-19
- Status: Ready for implementation
- Owner: project maintainer
- Related specification: [Role-aware service navigation design](../superpowers/specs/2026-07-19-role-aware-service-navigation-design.md)
- Related architecture: [Accepted constraints](../architecture.md#accepted-constraints),
  [Security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries)
- Related ADRs: [ADR 0012 — Use Django photographer permissions](../adr/0012-use-django-photographer-permissions.md)
- ADR impact: Conforms to ADR 0012; no new or superseding ADR is required.

## Goal

Hide photographer and administration navigation from visitors while showing each service link only
to an authenticated user who has its corresponding Django capability.

## Scope

### In scope

- Make the administration link conditional on `request.user.is_staff` in the shared production
  template.
- Preserve the existing feature-flagged `ingestion.upload_photos` permission check for the upload
  link.
- Add behavioral response tests for anonymous, ordinary authenticated, photographer, staff, and
  staff-plus-photographer navigation.

### Out of scope

- Route protection, Django Admin authorization, upload authorization, accounts, permissions,
  feature flags, CSS, layout, migrations, configuration, and deployment changes.

## Acceptance criteria

- Anonymous and ordinary authenticated users see neither service link.
- A permitted photographer sees only the upload link when uploads are enabled.
- A staff user sees only the administration link unless also granted upload permission.
- A staff user with upload permission sees both links when uploads are enabled.
- Public catalog, event detail, and legal links remain visible.

## Implementation

### Task 1: Enforce capability-aware navigation

**Files:**

- Modify: `src/backend/picflow/tests/test_views.py`
- Modify: `src/backend/templates/ui/base.html`

**Interfaces:**

- Consumes: Django's `request.user.is_staff` and the existing
  `photographer_upload_navigation` context boolean.
- Produces: rendered navigation whose service links match the approved role matrix.

- [ ] **Step 1: Write the failing behavioral tests**

  Update anonymous shell assertions so `reverse("admin:index")` is absent. Add a database-backed
  test that creates an ordinary user, photographer, staff user, and staff photographer; log each in
  and assert upload/admin link presence independently. Use
  `Permission.objects.get(content_type__app_label="ingestion", codename="upload_photos")` and
  `@override_settings(PHOTO_UPLOAD_ENABLED=True)` so the test exercises real permission evaluation.

- [ ] **Step 2: Run the focused tests and verify RED**

  Run:

  ```bash
  set -a
  source ../../src/backend/.env
  set +a
  ../../.venv/bin/pytest \
    src/backend/picflow/tests/test_views.py::PublicShellTests \
    src/backend/picflow/tests/test_views.py::PageTests::test_service_navigation_matches_user_capabilities \
    -q
  ```

  Expected: anonymous assertions fail because the unconditional administration link is present,
  and non-staff authenticated cases fail for the same reason.

- [ ] **Step 3: Implement the minimal template guard**

  Wrap only the existing administration anchor in:

  ```django
  {% if request.user.is_staff %}
    <a href="{% url 'admin:index' %}" aria-label="Администрирование">
      ...
    </a>
  {% endif %}
  ```

  Do not change the upload-link condition or public links.

- [ ] **Step 4: Run targeted and regression checks**

  Run the complete view and template suites:

  ```bash
  set -a
  source ../../src/backend/.env
  set +a
  ../../.venv/bin/pytest \
    src/backend/picflow/tests/test_views.py \
    src/backend/ingestion/tests/test_templates.py \
    -q
  ../../.venv/bin/ruff check src/backend/picflow/tests/test_views.py
  ../../.venv/bin/ruff format --check src/backend/picflow/tests/test_views.py
  ```

  Expected: all tests and static checks pass.

- [ ] **Step 5: Commit the behavior change**

  ```bash
  git add src/backend/picflow/tests/test_views.py src/backend/templates/ui/base.html
  git commit -m "fix: hide service navigation from visitors"
  ```

### Final task: Architecture and ADR reconciliation

- [ ] Compare delivered behavior with the approved specification and ADR 0012.
- [ ] Confirm the change remains a reversible UI detail and does not alter authentication or
  authorization boundaries.
- [ ] Confirm `docs/architecture.md` needs no update because implemented system structure and role
  boundaries remain unchanged.
- [ ] Record conformance to ADR 0012 in the pull request.

## Verification

```bash
set -a
source ../../src/backend/.env
set +a
../../.venv/bin/pytest \
  src/backend/picflow/tests/test_views.py \
  src/backend/ingestion/tests/test_templates.py \
  -q
../../.venv/bin/ruff check src/backend/picflow/tests/test_views.py
../../.venv/bin/ruff format --check src/backend/picflow/tests/test_views.py
git diff --check origin/main...HEAD
```

Expected: every command succeeds; service-link response assertions cover the complete role matrix.

## Operational impact and rollout

No configuration, migration, dependency, storage, deployment-order, or monitoring change. The
normal staging deployment of the merged Django template applies the navigation behavior.

## Rollback

Revert the template guard and its navigation assertions. No data or configuration rollback is
required.

## Open questions

None.

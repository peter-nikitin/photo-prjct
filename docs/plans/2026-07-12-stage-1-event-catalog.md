# Stage 1 Event Catalog Implementation Plan

- Date: 2026-07-12
- Status: Implemented
- Owner: project maintainer
- Related architecture: [Current architecture](../architecture.md#current-architecture--implemented)
- Related ADRs: [0001](../adr/0001-django-modular-monolith.md), [0002](../adr/0002-postgresql-system-of-record.md), [0006](../adr/0006-yandex-object-storage-media.md)

## Goal

An administrator creates and publishes an event in Django Admin, and a visitor sees published events
in a public catalog and event detail page.

## Scope

### In scope

- Target event fields, safe preservation of existing event IDs and photo relationships, Admin-only
  editing, public catalog/detail pages, durable event covers, and removal of active prototype routes.

### Out of scope

- Photo ingestion, galleries, search, prices, authentication roles, locations, photographer
  assignments, audit, and media cleanup automation.

## Acceptance criteria

- Only published events appear publicly; drafts return 404 by direct URL.
- Event date ranges and slugs are constrained and tested.
- Existing events and `Photo.event_id` relationships survive migration.
- Covers use environment-selected filesystem or Yandex Object Storage backends.
- Event deletion is unavailable in Admin; unpublishing is the removal workflow.
- Required repository checks pass.

## Implementation

### Task 1: Record durable media storage

**Files:** `docs/adr/0006-yandex-object-storage-media.md`, `docs/architecture.md`, settings and environment examples.

- [x] Accept separate Yandex Object Storage access boundaries for public and private media.
- [x] Configure filesystem storage locally and S3 storage by environment in deployments.
- [x] Use immutable UUID-based event cover keys.

### Task 2: Migrate the event domain

**Files:** `src/backend/picflow/models.py`, `src/backend/picflow/migrations/0002_event_catalog.py`.

- [x] Add target fields and backfill dates, city, type, status, and collision-safe Unicode slugs.
- [x] Preserve event primary keys and existing photo foreign keys.
- [x] Add unique slug and date-range database constraints with a two-second lock timeout.

### Task 3: Deliver Admin and public catalog

**Files:** Admin, URL/view configuration, catalog templates, and catalog CSS.

- [x] Make Django Admin the only event editor and disable deletion.
- [x] Render published events with upcoming-first ordering and consistent draft 404 behavior.
- [x] Redirect `/events/` to `/` and remove prototype feature routes.

### Task 4: Verify behavior and migration

**Files:** `src/backend/picflow/tests/`, repository foundation tests.

- [x] Cover model constraints, Admin workflow, public visibility, ordering, empty states, and routes.
- [x] Run the repository's formatting, lint, typing, test, Django, and migration checks.

## Verification

Run the commands required by `AGENTS.md`; all must exit successfully. Inspect `sqlmigrate` output for
the catalog migration and verify the two-second lock timeout precedes legacy column removal.

## Operational impact and rollout

Provision the public bucket and least-privilege IAM credentials before setting
`MEDIA_STORAGE_BACKEND=s3`. Deploy the migration before publishing new catalog links. Bucket creation
is a billing-sensitive Yandex Cloud action and requires separate manual confirmation.

## Rollback

Unpublish affected events and redeploy the previous image. The migration has a reverse data mapping
for legacy event fields. Object Storage files are retained and must not be deleted during rollback.

## Open questions

- None.

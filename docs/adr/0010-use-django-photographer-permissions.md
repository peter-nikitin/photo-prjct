# 0010: Use Django photographer permissions

- Status: Accepted
- Date: 2026-07-13
- Deciders: project maintainer
- Supersedes: none
- Superseded by: none

## Context

Stage 2 adds a photographer upload workflow to the existing Django application. Upload access must
remain independent from Django Admin access, while the application needs a clear event-visibility
and batch-ownership boundary before ingestion state is introduced.

## Decision drivers

- Reuse the implemented Django session and permission model.
- Grant upload capability without granting staff or administrative capability.
- Keep authorization rules explicit at both route and object level.
- Avoid photographer-to-event assignment until the product requires it.

## Considered options

1. Django sessions with an additive upload permission.
2. Treat every staff user as a photographer.
3. Introduce a separate photographer identity and authentication system.

## Decision

Use Django session authentication and the additive `ingestion.upload_photos` permission for the
photographer uploader. Staff status and photographer permission are independent: staff users do not
receive upload access automatically, and non-staff users with the permission do not receive Django
Admin access. Active superusers inherit the permission through Django's standard permission
semantics.

An authenticated user with the permission may select any event, including an unpublished event.
Non-superusers may read and mutate only upload batches and items that they own. This stage does not
introduce photographer-to-event assignments, public registration, or password-reset flows.

## Consequences

### Positive

- The upload capability composes with existing accounts and Django administration.
- Upload and administrative access can be granted and revoked independently.
- Object ownership provides a simple isolation boundary between photographers.

### Negative

- Event access is intentionally broad for every authorized photographer.
- Operators must manage group membership and permissions through existing administration.

### Follow-up

- Add explicit permission-matrix and cross-owner access tests with the ingestion module.
- Record a new decision if event assignment or a separate photographer identity becomes necessary.

## Validation and rollback

Validate that anonymous users are redirected to login, authenticated users without the permission
are denied, staff and photographer flags compose independently, superusers retain access, and batch
lookups cannot cross owners. Reconsider if all-event visibility no longer meets operating or privacy
requirements.

## References

- [Stage 2 photographer upload design](../superpowers/specs/2026-07-13-stage-2-photographer-upload-design.md)
- [Architecture: accepted constraints](../architecture.md#accepted-constraints)

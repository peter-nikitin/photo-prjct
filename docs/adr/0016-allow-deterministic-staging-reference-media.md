# 0016: Allow deterministic staging reference media

- Status: Proposed
- Date: 2026-07-19
- Deciders: project maintainers
- Supersedes: none
- Superseded by: none

## Context

The gallery needs stable staging reference photos, while the thirteen prototype photo rows refer to
legacy packaged assets rather than private uploaded originals. Normal photographer ingestion is
governed by ADR 0013, but repeatedly ingesting fixed reference data would create non-deterministic
objects and couple deployments to storage mutation.

## Decision drivers

- Keep reference identities and private object keys deterministic across staging rebuilds.
- Ensure migrations, startup, deployment, and database seeding never create storage objects.
- Prevent overwrite or deletion of existing private objects.
- Keep production disabled and preserve ADR 0013 for normal photographer ingestion.

## Considered options

1. Keep the thirteen legacy `src` references.
2. Upload new copies during every staging deployment.
3. Pass the reference photos through the normal ADR 0013 photographer-ingestion flow.
4. Permit a narrow deterministic staging reference-media exception.

## Decision

Define a versioned manifest containing exactly thirteen fixed prototype photo identities, their
metadata, and immutable final object keys in the existing private `hires-staging` bucket. A
separately authorized operator may conditionally create only absent manifest objects after a full
all-key dry run and fresh explicit confirmation. Existing mismatches abort the operation; creation
must not overwrite or delete an object.

Database reconciliation is opt-in, idempotent, and database-only. Production is forced disabled.
Migrations, application startup, normal deployment, and the seed command perform zero S3 writes;
none may upload, overwrite, copy, or delete reference objects. Automatic seed enablement and
per-database object copies are excluded.

This is an adjacent staging reference-data exception to ADR 0013. ADR 0013 remains authoritative
for every normal photographer upload through incoming keys, verification, and promotion. This
decision conforms to ADRs 0002, 0003, 0005, and 0006 and supersedes none. It does not authorize
production reference media, arbitrary legacy backfill, IAM, ACL, CORS, lifecycle, quota, or public
access changes.

## Consequences

### Positive

- Staging databases can reconcile the same identities without creating duplicate objects.
- Routine deployment and database paths remain free of storage mutation.
- Conditional creation and immutable keys prevent accidental replacement of reference media.

### Negative

- A separate operator workflow and immutable manifest require maintenance.
- Partially created objects remain in the private bucket for verified idempotent retry.
- The exception does not exercise the normal photographer-ingestion path.

### Follow-up

- Implement and test the manifest, database-only reconciliation, and dry-run-by-default uploader.
- Obtain fresh confirmation before any operator creates absent objects, and enable staging only
  after every manifest object verifies.

## Validation and rollback

Validate the exact thirteen-entry manifest, complete all-key preflight before any conditional PUT,
zero S3 writes from migrations/startup/deployment/seed paths, forced-false production configuration,
and no overwrite or delete path. Roll back database reconciliation only; immutable objects remain
for reuse and require a separate decision before deletion.

## References

- [Staging seed photo media design](../superpowers/specs/2026-07-18-staging-seed-photo-media-design.md)
- [Staging seed photo media implementation plan](../plans/2026-07-18-staging-seed-photo-media.md)
- [Architecture: accepted constraints](../architecture.md#accepted-constraints)
- [Architecture: photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing)
- [Architecture: open decisions](../architecture.md#open-decisions)
- [ADR 0002](0002-postgresql-system-of-record.md)
- [ADR 0003](0003-docker-compose-yandex-cloud.md)
- [ADR 0005](0005-promote-images-through-staging.md)
- [ADR 0006](0006-yandex-object-storage-media.md)
- [ADR 0013](0013-use-direct-private-object-storage-ingestion.md)

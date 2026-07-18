# Staging Seed Photo Media Design

- Date: 2026-07-18
- Status: Approved design
- Owner: project maintainer
- Related gallery design: [Event photo gallery](2026-07-18-event-photo-gallery-design.md)
- Related architecture: [System architecture](../../architecture.md)

## Outcome

The existing thirteen prototype-backed seed photos become valid uploaded-photo records whose
originals live in the staging private S3 bucket. The media objects are uploaded exactly once. Every
new developer database or dev-stand deployment creates only deterministic PostgreSQL seed records
that reference those fixed object keys; migrations and deployment never create additional S3
objects.

## Scope

### In scope

- The thirteen seed photo identities currently created by `picflow/migrations/0001_initial.py`.
- One manually controlled upload of thirteen objects to fixed private staging keys.
- Deterministic metadata for the uploaded shape: key, filename, size, content type, uploader, and
  upload timestamp.
- A database migration after the private-original schema exists that converts existing legacy seed
  rows and produces the same final rows in fresh databases.
- An inactive system-owned seed uploader used only to satisfy provenance and row-shape constraints.
- Verification that applying migrations repeatedly or deploying another dev stand performs no S3
  writes.

### Out of scope

- Uploading seed media during a Django migration, container startup, or deployment.
- Creating separate copies per developer, database, or deployment.
- Treating seed media as production customer uploads.
- Migrating arbitrary non-seed legacy `src` rows.
- Deleting or replacing visual-test fixtures under `tests/visual/static/`.
- Changing bucket lifecycle, public access, quotas, or other pricing-affecting cloud configuration.

## Why the initial migration cannot simply be edited

`picflow/migrations/0001_initial.py` creates the old `Photo(id, event, src)` shape. The
`original_key`, upload metadata, and `uploaded_by` fields do not exist until migration `0003`, and
the valid uploaded-row constraint is installed by `0004` and validated by `0005`.

Adding private-original values to the `0001` seed function would therefore target fields absent
from that migration's historical model. Rewriting the already-applied migration chain would also
make fresh and existing databases follow different histories.

The safe additive design leaves the historical migration intact and adds one deterministic data
migration after `0005`.

## Seed media manifest

A versioned Python manifest in the migration contains one entry per existing seed photo ID:

- event natural key;
- fixed final S3 object key;
- original filename;
- exact byte size;
- `image/png` content type; and
- a fixed seed timestamp.

Each photo ID receives a unique final object key because `Photo.original_key` is globally unique,
even where prototype rows reuse the same image bytes. The keys use the same immutable final-prefix
rules as the photographer upload flow. The manifest never contains credentials or signed URLs.

The exact keys, sizes, and checksums are generated and verified during the one-time staging upload,
then committed before the database migration is allowed to reference them.

## Database migration behavior

The additive data migration depends on `picflow.0005` and the configured user model. It:

1. creates or reuses a deterministically named inactive seed uploader with an unusable password;
2. locates only the thirteen known seed photo IDs and verifies their expected event relationships;
3. converts each legacy row atomically to the valid uploaded shape by clearing `src` and assigning
   the manifest's uploader, key, filename, size, content type, and timestamp;
4. fails loudly on conflicting identities or unexpected existing metadata instead of overwriting
   non-seed uploads; and
5. performs no network, storage, filesystem, or credential-dependent operation.

On a fresh database, `0001` creates the historical rows and this migration converts them after the
new schema and constraints are available. On an existing staging or developer database, the same
migration converts the same known rows once. Django's migration ledger prevents repeated writes.

The reverse migration restores the known legacy database values only; it does not delete S3
objects. Cloud object deletion is never coupled to database rollback.

## One-time S3 operation

Before deploying the data migration, an operator prepares a complete table of the thirteen source
files, destination keys, byte sizes, content types, and checksums. The operator then uploads each
object once using existing least-privilege credentials and verifies every destination with `HEAD`.

This is a manual, separately authorized staging operation. Immediately before any Yandex Cloud
mutation, the agent must show the exact object list and request explicit confirmation. No bucket,
IAM, lifecycle, quota, or billing configuration changes are included.

After successful verification, obsolete legacy media objects may be removed only from the exact
resolved source keys and only after another read-only check proves that no database row references
them. Repository visual-test copies remain untouched.

## Deployment ordering and failure behavior

1. Implement and test the manifest and data migration locally without S3 access.
2. Prepare the exact staging upload/delete inventory and checksums.
3. Obtain explicit manual confirmation for the S3 mutation.
4. Upload and `HEAD`-verify all thirteen fixed objects.
5. Deploy the application and apply the database migration.
6. Verify the seeded gallery through the public staging URL.
7. Resolve and confirm any obsolete S3 deletion separately.

The database migration must not deploy before all referenced objects pass `HEAD` verification. If
an upload fails, no database deployment occurs. If the later database migration fails, the fixed
objects remain harmless and can be reused on retry; they are not duplicated.

## Verification

- Migration tests prove a fresh historical database ends with thirteen valid uploaded-shape rows.
- Forward-migration tests prove the known legacy rows are converted without changing IDs or events.
- Conflict tests prove unexpected rows or metadata abort rather than being overwritten.
- Reverse tests prove database metadata is restored without issuing storage operations.
- A network-denial test or patched storage client proves migration execution performs zero S3 calls.
- A second migrate/deploy simulation proves no new rows or objects are created.
- A manifest test proves photo IDs and object keys are unique and metadata is complete.
- The manual staging run records checksums and successful `HEAD` results for every fixed key before
  application deployment.

# Staging Seed Photo Media Design

- Date: 2026-07-18
- Status: Approved by independent spec review; pending maintainer approval
- Owner: project maintainer
- Related gallery design: [Event photo gallery](2026-07-18-event-photo-gallery-design.md)
- Related architecture: [System architecture](../../architecture.md)
- Related ADRs: [0006](../../adr/0006-yandex-object-storage-media.md),
  [0013](../../adr/0013-use-direct-private-object-storage-ingestion.md)

## Outcome

The thirteen prototype-backed seed photos become valid uploaded-photo records whose originals live
under fixed keys in the existing private staging bucket `hires-staging`. The objects are uploaded
once through a separately confirmed operator action. New developer databases and staging
deployments create or reconcile only deterministic PostgreSQL rows; routine startup performs no S3
read or write and never creates another object copy.

## Scope

### In scope

- The thirteen seed identities currently created as legacy `src` rows by
  `picflow/migrations/0001_initial.py`.
- One conditional, no-overwrite upload of thirteen objects to fixed keys in `hires-staging`.
- A versioned manifest shared by database seeding tests and the operator uploader.
- An opt-in, idempotent, database-only `seed_reference_photos` management command.
- Automatic command execution at container startup with a no-op unless
  `REFERENCE_PHOTO_SEED_ENABLED=True`.
- An exact inactive system uploader used only as seed provenance.
- A database-only `--revert` mode that restores the historical legacy rows after strict validation.
- Verification that repeated command execution or deployment performs zero S3 calls.

### Out of scope

- Any network or storage access from a Django migration, startup hook, or seed management command.
- Seeding production or silently enabling seed data in an unknown environment.
- Creating separate object copies per developer database or deployment.
- Migrating arbitrary non-seed legacy `src` rows.
- Deleting any S3 object, including old unreferenced objects.
- Deleting or replacing visual-test fixtures under `tests/visual/static/`.
- Changing bucket lifecycle, IAM, public access, quotas, or other cloud configuration.

## Why this is not a data migration

`picflow/migrations/0001_initial.py` creates the historical `Photo(id, event, src)` shape.
Private-original fields appear only in `0003`, and `0004`/`0005` install and validate the uploaded-row
constraints. Editing `0001` to use later fields is invalid, while an unconditional additive data
migration would place staging-only object references into every local and future production
database.

The historical migrations therefore remain unchanged. Environment-scoped reference data is owned
by an explicit management command after normal migrations complete. The command is enabled only by
configuration and has no storage client dependency.

## Seed media manifest

One immutable Python manifest under `picflow` is the source of truth for both the database command
and operator uploader. Every entry contains:

- photo ID and event name/slug;
- legacy `src` value;
- source Git commit and blob path;
- fixed final object key;
- original filename, byte size, and `image/png` content type;
- SHA-256 checksum; and
- fixed upload timestamp `2026-06-21T00:00:00Z`.

The source commit is `cb4ce5137b6a4a47d4e0e7e164d6cfd316765d3d`; every source asset in the
table is a blob below `src/proto/assets/` at that commit. Each seed identity receives a unique final
key because `Photo.original_key` is globally unique, even where multiple identities reuse the same
source bytes.

| Photo | Event | Source asset | Key | Bytes | SHA-256 |
| --- | --- | --- | --- | ---: | --- |
| `LDN-1048` | London 10K | `run-city-1842.png` | `originals/d62316bbe15a54ce9e040a7a293e5677` | 112267 | `f62fc170134dc541db3f923e8be4451d4a6116dfacc79d3d738c2fbe2175e2d4` |
| `LDN-1190` | London 10K | `run-track-1190.png` | `originals/865c5e832de05962a238e8d281c5bdf7` | 108295 | `e034e81322e3bc68d86a2b530b15b43101f9f6b834a1d7092f1d327e435a18ef` |
| `LDN-1202` | London 10K | `run-track-1190.png` | `originals/b1a5bef0f7d95eb095b4395533f735d5` | 108295 | `e034e81322e3bc68d86a2b530b15b43101f9f6b834a1d7092f1d327e435a18ef` |
| `LDN-1316` | London 10K | `run-finish-1842.png` | `originals/2448f85359725aa7aae523fcab74878d` | 113160 | `baeebc3924d57505bb55476f0b808e715d8502954e1a8bb59ed5fe2fbbbd2ae4` |
| `LDN-1432` | London 10K | `run-finish-1842.png` | `originals/242a5f8f950d5c48b5937fe506b1b0c7` | 113160 | `baeebc3924d57505bb55476f0b808e715d8502954e1a8bb59ed5fe2fbbbd2ae4` |
| `BRI-2044` | Brighton Ride | `run-park-1204.png` | `originals/fe614fb600af5e3e8ef1269d77592410` | 107844 | `fd25fc1c10dea881f94dba41009e34533259592252abdd5ec01bd9c436a89356` |
| `BRI-2148` | Brighton Ride | `run-park-1204.png` | `originals/51422e3f3da85ebfb42213499a954691` | 107844 | `fd25fc1c10dea881f94dba41009e34533259592252abdd5ec01bd9c436a89356` |
| `BRI-2291` | Brighton Ride | `run-park-1204.png` | `originals/31f4b940b60c5b73a9f483ab9a72eac3` | 107844 | `fd25fc1c10dea881f94dba41009e34533259592252abdd5ec01bd9c436a89356` |
| `BRI-2366` | Brighton Ride | `run-finish-1204.png` | `originals/835996dc492d52999d9fdabf608f16a1` | 113569 | `5b6fd0f142b21ae49427d994409c197f1b109bc9d427438497c1fd98a71dec4e` |
| `EXP-3011` | Expo Run | `run-expo-3125.png` | `originals/ee8483a24394592e963aee2f4a14c696` | 111727 | `aa6e51a45f9cfea60fbbda593de9b3b0ec38dd7b592b050cfe6e02afd7bf3219` |
| `EXP-3125` | Expo Run | `run-expo-3125.png` | `originals/6ad826c1472752498b31693b0a8e4757` | 111727 | `aa6e51a45f9cfea60fbbda593de9b3b0ec38dd7b592b050cfe6e02afd7bf3219` |
| `EXP-3270` | Expo Run | `run-track-1190.png` | `originals/e31af46bdda753d6b1f2389d6ddae5a3` | 108295 | `e034e81322e3bc68d86a2b530b15b43101f9f6b834a1d7092f1d327e435a18ef` |
| `EXP-3338` | Expo Run | `run-finish-1842.png` | `originals/9e10e424aac357a3a99e01e87b66929d` | 113160 | `baeebc3924d57505bb55476f0b808e715d8502954e1a8bb59ed5fe2fbbbd2ae4` |

The operator uploader stores the SHA-256 as object metadata `sha256`; the manifest's exact size,
content type, checksum, and destination key form the comparison contract.

## Database seed command

`seed_reference_photos` accepts `--database` with Django's default database alias as its default. It
uses that alias for every query and write and wraps reconciliation in one transaction.

The exact provenance account is:

- username: `findme-photo-seed-uploader`;
- unusable password created with the current user model's `set_unusable_password()`;
- `is_active=False`, `is_staff=False`, and `is_superuser=False`; and
- blank email, first name, and last name.

An existing account is reusable only when every field above already matches. Any username collision
with a different account aborts the command without changing photos.

For each manifest entry the command accepts only two starting states:

1. the exact historical legacy row with the expected ID, event, and `src`, with every private field
   empty; or
2. the exact final seed row with all manifest metadata and the seed uploader already assigned.

The first state is converted with one row update that clears `src` and assigns `uploaded_by`,
`original_key`, `original_filename`, `original_size`, `original_content_type`, and `uploaded_at`.
The second state is skipped. A missing row, wrong event, partially converted row, key collision, or
non-seed upload using a seed ID aborts the whole transaction. The command imports no storage module
and never checks whether an object exists.

`--revert` performs the inverse only when all thirteen rows exactly match the manifest and uploader.
It restores their historical `src` values and clears all private fields in one transaction, then
deletes the inactive uploader only if no other row references it. It never deletes S3 objects.

## Environment and startup contract

`REFERENCE_PHOTO_SEED_ENABLED` defaults to `False`. Container startup runs the command after normal
migrations only when this setting is true. When false, startup performs no seed command and creates
no seed account or rows.

- Local developer stands opt in through `.env` only when configured to read `hires-staging`.
- The staging workflow passes an explicit repository environment variable and may enable it only
  after all thirteen objects are verified.
- The production workflow hard-codes the value to `False`; a staging variable cannot flow into
  production.

The setting controls database reconciliation only. No startup path uploads, inspects, lists, copies,
or deletes objects.

## One-time S3 uploader

A repository operator script reads the manifest and source bytes directly from the pinned Git blobs.
It defaults to dry-run and requires all of the following for mutation:

- explicit bucket `hires-staging`;
- an `--apply` flag;
- existing private-media credentials from the environment; and
- fresh manual confirmation outside the script immediately before execution.

The script has two phases. First it performs `HEAD` for all thirteen keys without writing anything:

- an exact size, `image/png` content type, and `sha256` metadata match is reported as `skip`;
- any existing mismatch aborts the entire run before any write; and
- a 404 is eligible for conditional creation.

Only after the complete preflight has found no mismatch does the second phase create the keys that
were absent. Creation uses Yandex Object Storage `PutObject` with `If-None-Match: *`, `Content-MD5`,
content type, and SHA-256 user metadata. Yandex documents that this condition succeeds only when the
key is absent. If a concurrent request wins and the PUT returns a precondition failure, the script
repeats `HEAD` and accepts only an exact match; otherwise it aborts. After every successful PUT it
repeats `HEAD` and compares all manifest metadata. No overwrite, multipart upload, ACL change, or
delete is allowed.

The current read-only preflight on 2026-07-18 confirmed that all thirteen selected keys return 404
in `hires-staging`. This observation is not execution approval and must be repeated before mutation.

## Rollout and failure behavior

1. Implement and test the manifest, database command, startup gate, and dry-run uploader locally.
2. Re-run read-only bucket/key preflight and show the active profile, cloud, folder, bucket, thirteen
   target keys, exact mutation command, impact, rollback, and current price estimate or state that it
   is unknown.
3. Obtain explicit manual confirmation immediately before object creation.
4. Run the conditional uploader and verify all thirteen objects.
5. Enable `REFERENCE_PHOTO_SEED_ENABLED=True` for staging and deploy database reconciliation plus the
   gallery.
6. Verify all three seeded free events and the public media route through staging.

An upload failure leaves already-created immutable objects available for an idempotent retry; the
database is not enabled until all thirteen verify. A database command failure rolls back all row and
account changes. Application rollback leaves the harmless fixed private objects and seed rows in
place; `--revert` is available if database restoration is required. S3 deletion is a separate future
operation and is not part of rollback.

## Verification

- Manifest tests prove thirteen unique photo IDs and object keys, exact source metadata, valid final
  key format, complete SHA-256 values, and immutable timestamp.
- Command tests prove legacy-to-uploaded conversion, second-run idempotence, database-alias use,
  exact-account reuse, collision rejection, all row-conflict paths, transactional rollback, and
  strict `--revert` behavior.
- Import guards and patched-client tests prove the command and startup path make zero S3 calls.
- Startup tests prove false means no command, staging true invokes it after migrations, and production
  is always false.
- Uploader unit tests prove dry-run, exact-match skip, mismatch abort, conditional PUT parameters,
  race recovery, post-write verification, and no overwrite/delete operation.
- Deployment configuration tests prove the staging variable cannot leak into production.
- The confirmed operator run records before/after `HEAD` evidence for every fixed key.

# Staging Seed Photo Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reconcile thirteen historical prototype photo identities into valid private-original database rows and provide a separately confirmed, dry-run-by-default, conditional one-time uploader for fixed objects in `hires-staging`.

**Architecture:** One immutable Python manifest is shared by a database-only Django command and a repository operator uploader. Routine startup may reconcile PostgreSQL only behind an explicit environment gate; no migration, startup path, or management command imports or calls S3. The uploader validates pinned Git blobs, HEAD-checks every key before writing, aborts on conflict, and only with `--apply` conditionally creates absent objects without overwrite/delete.

**Tech Stack:** Django 6.0.6, PostgreSQL 16, boto3/botocore 1.40.76, Yandex Object Storage S3 API, Python standard library, pytest, existing entrypoint/deployment workflows.

## Global Constraints

- Keep migrations `0001`-`0005` unchanged; this is environment-scoped reference data, not a migration.
- Manifest exactly thirteen identities, source commit `cb4ce5137b6a4a47d4e0e7e164d6cfd316765d3d`, timestamp `2026-06-21T00:00:00Z`, and approved fixed keys/sizes/type/checksums.
- `seed_reference_photos`, migrations, and startup make zero S3 calls and import no storage/uploader module.
- `REFERENCE_PHOTO_SEED_ENABLED=False` by default; staging may opt in only after object verification; production is forced `False`.
- Uploader defaults dry-run, accepts only explicit `hires-staging`, and requires `--apply`, credentials, and future fresh confirmation outside the script before mutation. This plan performs no cloud mutation.
- HEAD all thirteen before any write. Exact match skips; mismatch aborts; absence allows only `IfNoneMatch="*"` conditional PUT with Content-MD5, PNG type, and SHA-256 metadata.
- Never overwrite, multipart-upload, alter ACL, copy, or delete. Rollback is database-only.
- Add no worker, broker, derivative/readiness representation, gallery implementation, or commerce.

---

- Date: 2026-07-18
- Status: Blocked — ADR 0016 is Proposed and not accepted
- Owner: project maintainer
- Delivery disposition: Deferred to a separate future task where the design may change; no seed
  implementation is authorized in the current gallery continuation.
- Related specification: [Staging seed photo media design](../superpowers/specs/2026-07-18-staging-seed-photo-media-design.md)
- Related architecture: [Current architecture — implemented](../architecture.md#current-architecture--implemented),
  [Accepted constraints](../architecture.md#accepted-constraints),
  [Target MVP architecture — proposed](../architecture.md#target-mvp-architecture--proposed),
  [Photo ingestion and indexing](../architecture.md#photo-ingestion-and-indexing),
  [Security, privacy, and legal boundaries](../architecture.md#security-privacy-and-legal-boundaries),
  [Evolution stages](../architecture.md#evolution-stages), and
  [Open decisions](../architecture.md#open-decisions)
- Related ADRs: [0002](../adr/0002-postgresql-system-of-record.md),
  [0003](../adr/0003-docker-compose-yandex-cloud.md),
  [0005](../adr/0005-promote-images-through-staging.md),
  [0006](../adr/0006-yandex-object-storage-media.md),
  [0013](../adr/0013-use-direct-private-object-storage-ingestion.md), and
  [0016 (Proposed)](../adr/0016-allow-deterministic-staging-reference-media.md)
- ADR impact: Requires accepted ADR 0016 — the deterministic staging reference-media exception is
  not authoritative while ADR 0016 remains Proposed.

## Scope

### In scope

- Frozen manifest; transactional multi-database command with forward/idempotent/revert states and inactive provenance user.
- Startup gate and staging/production propagation.
- Separate pinned-blob uploader with two-phase preflight, conditional creation, race recovery, verification.
- Focused command/startup/deploy/uploader tests and a non-mutating operator handoff.

### Out of scope

- Running `--apply`, enabling staging, cloud configuration/mutation, production rows, arbitrary legacy conversion, per-database copies, S3 cleanup, visual fixture changes, gallery/UI/media processing.

## File map

- Create `src/backend/picflow/reference_photos.py` and `src/backend/picflow/tests/test_reference_photos.py`: manifest.
- Create `src/backend/picflow/management/commands/seed_reference_photos.py` with package initializers and `src/backend/picflow/tests/test_seed_reference_photos.py`.
- Modify `src/backend/config/settings.py`, `src/backend/entrypoint.sh`, and `.env.example`; create `tests/deployment/test_entrypoint.py`.
- Modify `.github/workflows/deploy.yml`, `.github/workflows/promote-production.yml`, `deploy/apply-deployment.sh`, and `tests/deployment/test_deployment_scripts.py`.
- Create `deploy/upload_reference_photos.py` and `tests/deployment/test_reference_photo_uploader.py`.
- Modify `README.md` and `docs/architecture.md` without claiming upload/activation.

## Exact manifest contract

`ReferencePhotoSeed` is frozen with `photo_id, event_name, event_slug, legacy_src, source_commit, source_blob_path, final_key, original_filename, size, content_type, sha256, uploaded_at`. All entries use approved commit, `src/proto/assets/<blob>`, filename `<blob>`, `image/png`, and aware UTC `datetime(2026, 6, 21, tzinfo=UTC)`.

```python
@dataclass(frozen=True)
class ReferencePhotoSeed:
    photo_id: str
    event_name: str
    event_slug: str
    legacy_src: str
    source_commit: str
    source_blob_path: str
    final_key: str
    original_filename: str
    size: int
    content_type: str
    sha256: str
    uploaded_at: datetime
```

| ID | Event / slug | Legacy src and blob | Final key | Bytes | SHA-256 |
| --- | --- | --- | --- | ---: | --- |
| `LDN-1048` | London 10K / `london-10k` | `photos/run-city-1842.png`; `run-city-1842.png` | `originals/d62316bbe15a54ce9e040a7a293e5677` | 112267 | `f62fc170134dc541db3f923e8be4451d4a6116dfacc79d3d738c2fbe2175e2d4` |
| `LDN-1190` | London 10K / `london-10k` | `photos/run-track-1190.png`; `run-track-1190.png` | `originals/865c5e832de05962a238e8d281c5bdf7` | 108295 | `e034e81322e3bc68d86a2b530b15b43101f9f6b834a1d7092f1d327e435a18ef` |
| `LDN-1202` | London 10K / `london-10k` | `photos/run-track-1190.png`; `run-track-1190.png` | `originals/b1a5bef0f7d95eb095b4395533f735d5` | 108295 | `e034e81322e3bc68d86a2b530b15b43101f9f6b834a1d7092f1d327e435a18ef` |
| `LDN-1316` | London 10K / `london-10k` | `photos/run-finish-1842.png`; `run-finish-1842.png` | `originals/2448f85359725aa7aae523fcab74878d` | 113160 | `baeebc3924d57505bb55476f0b808e715d8502954e1a8bb59ed5fe2fbbbd2ae4` |
| `LDN-1432` | London 10K / `london-10k` | `photos/run-finish-1842.png`; `run-finish-1842.png` | `originals/242a5f8f950d5c48b5937fe506b1b0c7` | 113160 | `baeebc3924d57505bb55476f0b808e715d8502954e1a8bb59ed5fe2fbbbd2ae4` |
| `BRI-2044` | Brighton Ride / `brighton-ride` | `photos/run-park-1204.png`; `run-park-1204.png` | `originals/fe614fb600af5e3e8ef1269d77592410` | 107844 | `fd25fc1c10dea881f94dba41009e34533259592252abdd5ec01bd9c436a89356` |
| `BRI-2148` | Brighton Ride / `brighton-ride` | `photos/run-park-1204.png`; `run-park-1204.png` | `originals/51422e3f3da85ebfb42213499a954691` | 107844 | `fd25fc1c10dea881f94dba41009e34533259592252abdd5ec01bd9c436a89356` |
| `BRI-2291` | Brighton Ride / `brighton-ride` | `photos/run-park-1204.png`; `run-park-1204.png` | `originals/31f4b940b60c5b73a9f483ab9a72eac3` | 107844 | `fd25fc1c10dea881f94dba41009e34533259592252abdd5ec01bd9c436a89356` |
| `BRI-2366` | Brighton Ride / `brighton-ride` | `photos/run-finish-1204.png`; `run-finish-1204.png` | `originals/835996dc492d52999d9fdabf608f16a1` | 113569 | `5b6fd0f142b21ae49427d994409c197f1b109bc9d427438497c1fd98a71dec4e` |
| `EXP-3011` | Expo Run / `expo-run` | `photos/run-expo-3125.png`; `run-expo-3125.png` | `originals/ee8483a24394592e963aee2f4a14c696` | 111727 | `aa6e51a45f9cfea60fbbda593de9b3b0ec38dd7b592b050cfe6e02afd7bf3219` |
| `EXP-3125` | Expo Run / `expo-run` | `photos/run-expo-3125.png`; `run-expo-3125.png` | `originals/6ad826c1472752498b31693b0a8e4757` | 111727 | `aa6e51a45f9cfea60fbbda593de9b3b0ec38dd7b592b050cfe6e02afd7bf3219` |
| `EXP-3270` | Expo Run / `expo-run` | `photos/run-track-1190.png`; `run-track-1190.png` | `originals/e31af46bdda753d6b1f2389d6ddae5a3` | 108295 | `e034e81322e3bc68d86a2b530b15b43101f9f6b834a1d7092f1d327e435a18ef` |
| `EXP-3338` | Expo Run / `expo-run` | `photos/run-finish-1842.png`; `run-finish-1842.png` | `originals/9e10e424aac357a3a99e01e87b66929d` | 113160 | `baeebc3924d57505bb55476f0b808e715d8502954e1a8bb59ed5fe2fbbbd2ae4` |

## Acceptance criteria

- Manifest exactly matches the table, with unique IDs/keys.
- Command accepts only exact historical or exact final state; every collision/missing/partial/wrong state aborts all changes.
- Second forward run is no-op; strict `--revert` restores all thirteen and removes the uploader only when every reverse relation from `_meta.related_objects` and every local M2M relation from `_meta.local_many_to_many` is empty. Group membership, direct permissions, cascade relations, protected `UploadBatch`, or any other relation retain the account with `uploader_deleted=False`.
- Every ORM operation uses selected `--database`.
- Disabled startup invokes no command; enabled staging invokes after migrate; production always false; routine paths call no S3.
- Dry-run validates all sources and HEADs all keys with zero writes. Apply performs all HEADs before first conditional PUT and verifies writes/races.
- No cloud mutation or staging activation occurs while implementing this plan.

## Implementation

### Task 1: Immutable manifest

**Files:** Create `src/backend/picflow/reference_photos.py`, `src/backend/picflow/tests/test_reference_photos.py`.

**Interfaces:** Produce `SEED_UPLOAD_USERNAME="findme-photo-seed-uploader"`, source/timestamp constants, frozen `ReferencePhotoSeed`, and `REFERENCE_PHOTOS: tuple[ReferencePhotoSeed, ...]`.

- [ ] Add `test_reference_photo_manifest_matches_approved_contract`, `test_reference_photo_manifest_has_unique_ids_and_keys`, `test_reference_photo_seed_is_frozen`, and `test_reference_photo_manifest_imports_no_storage` with literal expected tuples for all thirteen table rows.
- [ ] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_reference_photos.py`; expected RED is `ModuleNotFoundError: No module named 'picflow.reference_photos'`.
- [ ] Add the exact frozen dataclass above, constants, and thirteen literal entries. Use `datetime(2026, 6, 21, tzinfo=UTC)` and the full source commit/path in each entry; derive no key, filename, size, checksum, or timestamp at runtime; import no Django/storage module.
- [ ] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_reference_photos.py`; expected GREEN is all four tests passed.
- [ ] Commit: `git add src/backend/picflow/reference_photos.py src/backend/picflow/tests/test_reference_photos.py && git commit -m "feat: define immutable reference photo manifest"`.

### Task 2: Transactional forward reconciliation

**Files:** Create `src/backend/picflow/management/__init__.py`, `src/backend/picflow/management/commands/__init__.py`, `src/backend/picflow/management/commands/seed_reference_photos.py`, and `src/backend/picflow/tests/test_seed_reference_photos.py`.

**Interfaces:** `seed_reference_photos [--database <alias>]`, where omitted alias is `DEFAULT_DB_ALIAS`; stdout `Reference photos reconciled: converted=<n> unchanged=<n>.`.

- [ ] Add tests named `test_seed_converts_all_legacy_rows_atomically`, `test_seed_second_run_is_idempotent`, `test_seed_reuses_only_exact_inactive_uploader`, parametrized `test_seed_rejects_conflicting_row_state`, `test_seed_rejects_username_and_key_collisions`, and `test_seed_late_failure_rolls_back_rows_and_account` for every listed branch.
- [ ] Add second-alias proof: every User/Event/Photo read/write and transaction uses selected alias; both databases assert isolation.
- [ ] In `test_seed_uses_selected_database_only` configure a second test alias and assert all User/Event/Photo effects stay there. In `test_seed_makes_zero_storage_calls` patch `boto3.client`, `PrivateUploadStorage`, and `socket.create_connection`; assert zero calls and no storage import.
- [ ] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_seed_reference_photos.py -k 'not revert'`; expected RED is `CommandError: Unknown command: 'seed_reference_photos'`.
- [ ] Implement `add_arguments` with `--database` default `DEFAULT_DB_ALIAS`; forward path uses one `transaction.atomic(using=database)`, user manager `.db_manager(database)`, and `.using(database).select_for_update()`. Prevalidate all thirteen exact legacy/final states, event name+slug, uploader contract, and key collisions before any update; create uploader with `set_unusable_password()`/exact fields and `save(using=database)`; then update legacy rows to exact manifest fields.

  ```python
  def add_arguments(self, parser: CommandParser) -> None:
      parser.add_argument("--database", default=DEFAULT_DB_ALIAS)


  def handle(self, *args: object, **options: object) -> None:
      database = cast(str, options["database"])
      with transaction.atomic(using=database):
          uploader = reconcile_exact_uploader(database=database)
          rows = lock_and_validate_all_seed_rows(database=database, uploader=uploader)
          reconcile_legacy_rows(database=database, uploader=uploader, rows=rows)
  ```

  The three named helpers are private functions in the command module with explicit `database: str` parameters; none has a default database or storage dependency. `lock_and_validate_all_seed_rows` returns only after all thirteen states and destination-key collisions validate, so `reconcile_legacy_rows` performs no branching or partial acceptance.
- [ ] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_seed_reference_photos.py -k 'not revert'`; expected GREEN is all non-revert command tests passed.
- [ ] Commit: `git add src/backend/picflow/management src/backend/picflow/tests/test_seed_reference_photos.py && git commit -m "feat: reconcile reference photos in database"`.

### Task 3: Strict database revert

**Files:** Modify `src/backend/picflow/management/commands/seed_reference_photos.py` and `src/backend/picflow/tests/test_seed_reference_photos.py`.

**Interfaces:** `seed_reference_photos --revert [--database]`; exact legacy fields; stdout `Reference photos reverted: restored=13 uploader_deleted=<True|False>.`.

- [ ] Add `test_revert_restores_exact_legacy_rows`, parametrized `test_revert_rejects_any_non_exact_seed_row_atomically`, `test_revert_keeps_uploader_when_another_photo_references_it`, `test_revert_keeps_uploader_with_group_membership`, `test_revert_keeps_uploader_with_direct_permission`, `test_revert_keeps_uploader_with_cascade_relation`, and `test_revert_keeps_uploader_with_protected_upload_batch`. Create group membership through `seed_uploader.groups.add(group)`, direct permission through `seed_uploader.user_permissions.add(permission)`, a cascade relation through an exact current-model `LogEntry` for the user, and the protected relation through `UploadBatch.objects.create(event=seed_event, uploader=seed_uploader, expected_item_count=1)`. Every relation case expects the rows reverted, account retained, and `uploader_deleted=False`.
- [ ] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_seed_reference_photos.py -k 'revert'`; expected RED is `CommandError: unrecognized arguments: --revert`.
- [ ] Add `parser.add_argument("--revert", action="store_true")` and dispatch it to a `_revert(database: str)` branch. Inside the selected-alias transaction, lock and validate all thirteen exact final rows before mutation, restore each legacy `src`, and clear all private fields. Before deletion, call `_user_has_any_relation(user=uploader, database=database)`: inspect every reverse relation in `user._meta.related_objects` through the related model's `_base_manager.using(database)` and every local M2M field in `user._meta.local_many_to_many` through its selected-alias manager. Delete only when every query is empty. Keep `ProtectedError` as a race/backstop, not the primary relation detector. Never inspect/delete S3.

  ```python
  def _user_has_any_relation(*, user: Model, database: str) -> bool:
      for relation in user._meta.related_objects:
          if (
              relation.related_model._base_manager.using(database)
              .filter(**{relation.field.name: user})
              .exists()
          ):
              return True
      for field in user._meta.local_many_to_many:
          if getattr(user, field.name).using(database).exists():
              return True
      return False


  uploader_deleted = False
  if not _user_has_any_relation(user=uploader, database=database):
      try:
          uploader.delete(using=database)
      except ProtectedError:
          uploader_deleted = False
      else:
          uploader_deleted = True
  ```
- [ ] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_seed_reference_photos.py -k 'revert'`; expected GREEN includes the photo-reference and protected-batch cases. Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_seed_reference_photos.py`; expected GREEN is the complete command module passed.
- [ ] Commit: `git add src/backend/picflow/management/commands/seed_reference_photos.py src/backend/picflow/tests/test_seed_reference_photos.py && git commit -m "feat: add strict reference photo seed revert"`.

### Task 4: Startup gate

**Files:** Modify `src/backend/config/settings.py`, `src/backend/entrypoint.sh`, and `.env.example`; create `tests/deployment/test_entrypoint.py`.

**Interfaces:** `REFERENCE_PHOTO_SEED_ENABLED` default false; exact order migrate, conditional seed, bootstrap group, collectstatic, Gunicorn.

- [ ] Add `test_reference_photo_seed_setting_defaults_false`/`test_reference_photo_seed_setting_accepts_true` and entrypoint tests `test_entrypoint_skips_reference_seed_by_default`, `test_entrypoint_runs_reference_seed_after_migrate`, and `test_entrypoint_stops_when_reference_seed_fails` using fake executables/command logs.
- [ ] Run `DB_HOST=127.0.0.1 pytest -q src/backend/ingestion/tests/test_settings.py tests/deployment/test_entrypoint.py -k 'reference_photo_seed or reference_seed'`; expected RED is absent setting and no seed invocation for True.
- [ ] Add `REFERENCE_PHOTO_SEED_ENABLED = env.bool("REFERENCE_PHOTO_SEED_ENABLED", default=False)`, example false, and exact shell branch `if [ "${REFERENCE_PHOTO_SEED_ENABLED:-False}" = "True" ]; then python manage.py seed_reference_photos; fi` immediately after migrate. Do not invoke Django shell or any storage code.

  ```sh
  python manage.py migrate --noinput
  if [ "${REFERENCE_PHOTO_SEED_ENABLED:-False}" = "True" ]; then
      python manage.py seed_reference_photos
  fi
  python manage.py bootstrap_photographer_group
  ```
- [ ] Run `DB_HOST=127.0.0.1 pytest -q src/backend/ingestion/tests/test_settings.py tests/deployment/test_entrypoint.py -k 'reference_photo_seed or reference_seed'` and `sh -n src/backend/entrypoint.sh`; expected GREEN is selected tests passed and shell exit zero.
- [ ] Commit: `git add src/backend/config/settings.py src/backend/entrypoint.sh tests/deployment/test_entrypoint.py .env.example && git commit -m "feat: gate reference photo seed at startup"`.

### Task 5: Deployment scoping

**Files:** Modify `.github/workflows/deploy.yml`, `.github/workflows/promote-production.yml`, `deploy/apply-deployment.sh`, and `tests/deployment/test_deployment_scripts.py`.

**Interfaces:** Staging uses GitHub Environment variable `REFERENCE_PHOTO_SEED_ENABLED` with fallback `False`; production uses literal `"False"` and never the staging variable.

- [ ] Add `test_staging_apply_propagates_reference_photo_seed_flag`, `test_staging_apply_defaults_reference_photo_seed_false`, `test_production_apply_forces_reference_photo_seed_false`, and `test_workflows_scope_reference_photo_seed_to_staging` with exact generated-env/YAML assertions.
- [ ] Run `pytest -q tests/deployment/test_deployment_scripts.py -k 'reference_photo_seed'`; expected RED is missing generated `REFERENCE_PHOTO_SEED_ENABLED`.
- [ ] In staging workflow pass `${{ vars.REFERENCE_PHOTO_SEED_ENABLED || 'False' }}` through SSH `env`/`envs`; production workflow passes literal `False` and contains no variable reference. In apply script reject staging values outside exact True/False, force production false regardless of caller, and write exactly one env line without changing recovery.
- [ ] Run `pytest -q tests/deployment/test_deployment_scripts.py -k 'reference_photo_seed'`; expected GREEN is four selected tests passed. Run `pytest -q tests/deployment/test_deployment_scripts.py && sh -n deploy/apply-deployment.sh`; expected GREEN is the full module passed and shell exit zero.
- [ ] Commit: `git add .github/workflows/deploy.yml .github/workflows/promote-production.yml deploy/apply-deployment.sh tests/deployment/test_deployment_scripts.py && git commit -m "ops: scope reference photo seed to staging"`.

### Task 6: Dry-run source and all-key preflight

**Files:** Create `deploy/upload_reference_photos.py`, `tests/deployment/test_reference_photo_uploader.py`.

**Interfaces:** frozen `PreparedReferencePhoto(seed: ReferencePhotoSeed, body: bytes, content_md5: str)`; `prepare_sources(repo_root: Path) -> tuple[PreparedReferencePhoto, ...]`; `inspect_key(client, bucket: str, prepared: PreparedReferencePhoto) -> Literal["missing", "match"]`; `preflight(client, bucket: str, prepared: tuple[PreparedReferencePhoto, ...]) -> tuple[PreparedReferencePhoto, ...]` returning only missing prepared entries. CLI/credential settings remain exact as above.

```python
@dataclass(frozen=True)
class PreparedReferencePhoto:
    seed: ReferencePhotoSeed
    body: bytes
    content_md5: str
```

- [ ] Add `test_script_runs_from_repository_root_without_pythonpath`, invoking `[sys.executable, "deploy/upload_reference_photos.py", "--help"]` with cwd repository root and `PYTHONPATH` removed; expect exit zero after implementation. Add source tests proving all thirteen exact `git show` calls, size/SHA-256 validation, and base64 MD5 calculation complete before client construction/HEAD.
- [ ] Add dry-run tests for bucket rejection, missing explicit private credentials in either mode, all thirteen HEAD calls, exact match/missing/conflict mapping, sanitized failures, and zero mutations. Assert `prepare_sources` returns frozen prepared values and `preflight` returns only prepared missing entries without rereading Git.
- [ ] Run `pytest -q tests/deployment/test_reference_photo_uploader.py -k 'repository_root or prepare or preflight or dry_run'`; expected RED is missing script/module (the subprocess test returns 2 and import tests fail).
- [ ] At script top compute `REPO_ROOT = Path(__file__).resolve().parents[1]`, prepend `str(REPO_ROOT / "src" / "backend")` to `sys.path` only if absent, then load `picflow.reference_photos` with `importlib.import_module` rather than a late import statement. This makes direct repo-root execution self-contained without caller PYTHONPATH and avoids Ruff E402. Implement `prepare_sources` to validate every blob and compute `content_md5` before client construction/HEAD. Implement `preflight` to HEAD every prepared item, abort on mismatch/error, and only after the full loop return missing prepared items. Default CLI performs no PUT.

  ```python
  REPO_ROOT = Path(__file__).resolve().parents[1]
  BACKEND_ROOT = REPO_ROOT / "src" / "backend"
  if str(BACKEND_ROOT) not in sys.path:
      sys.path.insert(0, str(BACKEND_ROOT))
  reference_photos = importlib.import_module("picflow.reference_photos")
  ReferencePhotoSeed = reference_photos.ReferencePhotoSeed
  REFERENCE_PHOTOS = reference_photos.REFERENCE_PHOTOS


  def main(argv: Sequence[str] | None = None) -> int:
      args = parse_args(argv)
      prepared = prepare_sources(REPO_ROOT)
      client = build_client_from_environment()
      missing = preflight(client, args.bucket, prepared)
      if not args.apply:
          return 0
      apply_missing(client, args.bucket, missing)
      return 0
  ```
- [ ] Run `pytest -q tests/deployment/test_reference_photo_uploader.py -k 'repository_root or prepare or preflight or dry_run'`; expected GREEN is all selected tests passed, including subprocess exit zero. Run `python deploy/upload_reference_photos.py --help`; expected GREEN is usage text and exit zero from repository root. Run `ruff check deploy/upload_reference_photos.py`; expected GREEN is exit zero with no E402 or other lint finding.
- [ ] Commit: `git add deploy/upload_reference_photos.py tests/deployment/test_reference_photo_uploader.py && git commit -m "feat: add dry-run reference media preflight"`.

### Task 7: Conditional creation and race recovery

**Files:** Modify `deploy/upload_reference_photos.py` and `tests/deployment/test_reference_photo_uploader.py`.

**Interfaces:** `create_absent(client, bucket: str, prepared: PreparedReferencePhoto) -> Literal["created","race-matched"]`; `apply_missing(client, bucket: str, missing: tuple[PreparedReferencePhoto, ...]) -> None`.

```python
client.put_object(
    Bucket=bucket,
    Key=prepared.seed.final_key,
    Body=prepared.body,
    IfNoneMatch="*",
    ContentMD5=prepared.content_md5,
    ContentType=prepared.seed.content_type,
    Metadata={"sha256": prepared.seed.sha256},
)
```

- [ ] Add `test_apply_starts_only_after_all_key_preflight`, `test_create_absent_uses_exact_conditional_put`, and `test_successful_put_is_verified_by_head`; assert exact prepared body/MD5 and every request field.
- [ ] Add `test_precondition_race_accepts_only_exact_match` for 409/412 and `test_partial_apply_is_idempotently_retryable`; assert no PUT retry and earlier objects remain.
- [ ] Add `test_uploader_has_no_forbidden_mutation_path` with fake forbidden methods and an AST guard rejecting copy/delete/multipart/ACL and any PUT without `IfNoneMatch="*"`.
- [ ] Run `pytest -q tests/deployment/test_reference_photo_uploader.py -k 'apply or race or forbidden or conditional'`; expected RED is missing `create_absent`/no PUT calls.
- [ ] Implement only the exact PUT above, consuming the already prepared body/MD5 and the all-key preflight's immutable missing tuple. On 409/412, re-HEAD the same prepared item and accept exact match only; after successful PUT, re-HEAD exact metadata; never recompute/reload source, retry PUT, overwrite, copy, multipart, ACL, or delete.
- [ ] Run `pytest -q tests/deployment/test_reference_photo_uploader.py -k 'apply or race or forbidden or conditional'`; expected GREEN is all selected safety/race tests passed. Run `pytest -q tests/deployment/test_reference_photo_uploader.py`; expected GREEN is the full uploader module passed.
- [ ] Commit: `git add deploy/upload_reference_photos.py tests/deployment/test_reference_photo_uploader.py && git commit -m "feat: conditionally upload reference photo media"`.

### Task 8: Architecture and ADR reconciliation

**Files:** Modify `README.md`, `docs/architecture.md`.

**Interfaces:** Consumes verified Tasks 1-7; produces exact dry-run/forward/revert operator commands and architecture wording that explicitly leaves cloud upload and staging activation unperformed.

- [ ] Run `DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_reference_photos.py src/backend/picflow/tests/test_seed_reference_photos.py src/backend/ingestion/tests/test_settings.py tests/deployment/test_entrypoint.py tests/deployment/test_reference_photo_uploader.py tests/deployment/test_deployment_scripts.py`; expected GREEN is every selected module passed.
- [ ] Run `ruff format --check . && ruff check . && mypy`; expected GREEN is three zero exits. Run `python src/backend/manage.py check && python src/backend/manage.py makemigrations --check --dry-run`; expected GREEN is no issue and `No changes detected`.
- [ ] Compare delivered behavior with the approved specification, accepted ADR 0016, ADR 0013's
  normal-ingestion boundary, all other applicable ADRs, and `docs/architecture.md`.
- [ ] Document exact dry-run, explicit DB command, opt-in flag, and `--revert`; warn that `--apply` needs future fresh scope display/confirmation.
- [ ] Record only implemented mechanisms, not uploaded objects, enabled staging, live gallery, or production rows.
- [ ] Stop for a new decision rather than contradicting an accepted ADR; supersede rather than
  edit an accepted decision.
- [ ] Run `git diff --check && test -z "$(git diff -- src/backend/picflow/migrations)"`; expected GREEN is exit zero and empty migration diff. Run `rg -n 'delete_object|copy_object|create_multipart_upload|put_object' deploy/upload_reference_photos.py`; expected output is exactly the reviewed conditional `put_object` call and no forbidden call.
- [ ] Run `git status --short`; expected scope contains no asset/credential/.env, fixture deletion, worker/broker, derivative/readiness, or unrelated file.
- [ ] Record the architecture and ADR reconciliation outcome in the pull request before push.
- [ ] Commit: `git add README.md docs/architecture.md && git commit -m "docs: describe safe reference photo seeding"`.

## Verification

    DB_HOST=127.0.0.1 pytest -q src/backend/picflow/tests/test_reference_photos.py src/backend/picflow/tests/test_seed_reference_photos.py src/backend/ingestion/tests/test_settings.py tests/deployment/test_entrypoint.py tests/deployment/test_reference_photo_uploader.py tests/deployment/test_deployment_scripts.py
    ruff format --check .
    ruff check .
    mypy
    python src/backend/manage.py check
    python src/backend/manage.py makemigrations --check --dry-run
    sh -n src/backend/entrypoint.sh
    sh -n deploy/apply-deployment.sh
    git diff --check
    git diff -- src/backend/picflow/migrations

Expected: zero exits, no migration diff/drift, no default/disabled S3 write, production seed false. Only this read-only operator command is allowed during implementation verification:

    python deploy/upload_reference_photos.py --bucket hires-staging

Expected: pinned sources validate and thirteen HEADs report `skip` or `would-create`; no PUT/overwrite/copy/ACL/multipart/delete. Do not run the mutation command with `--apply` in this plan.

## Operational impact and rollout

- Default/staging fallback/production remain false. Local opt-in is allowed only when configured to read `hires-staging`.
- Uploader reuses existing credentials/endpoint/region; it changes no IAM, policy, CORS, lifecycle, quota, public access, or billing resource.
- Before any future `--apply`, show active non-secret profile/cloud/folder, exact bucket and thirteen keys, fresh dry-run, exact command, exact total bytes calculated from manifest, price delta or unknown, verification, and no-delete rollback; then obtain fresh explicit confirmation in that session.
- That confirmation covers only displayed object creates, not enabling staging, IAM/lifecycle, deletion, or production.
- After separately confirmed upload: verify all HEADs; only then set staging flag true and deploy through `apply-deployment.sh`; verify 13 converted/unchanged and repeat deploy zero-S3/no-op; keep production false.
- Monitor entrypoint status and counts. Contract mismatch stops before Gunicorn and existing deployment recovery applies. Partial uploader success is retained and idempotently skipped on retry.

## Rollback

- Redeploy previous immutable image and set staging flag false. Exact final rows can remain harmless.
- If required, run database-only `seed_reference_photos --revert --database <alias>`; strict transaction prevents partial reversal.
- Never delete S3 objects as rollback. Created immutable objects remain reusable; deletion requires a separate inventory/reference audit/recovery plan and fresh destructive confirmation.

## Open questions

- Acceptance gate: ADR 0016 remains Proposed. Before this deferred plan can be resumed, the project
  maintainer must explicitly accept ADR 0016's exact decision, and the separate future task must
  confirm whether the approved seed design is still the desired implementation.

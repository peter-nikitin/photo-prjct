# Feature-flag Definition Reconciliation Design

- **Status:** Awaiting written review
- **Date:** 2026-08-22
- **Owner:** FindMe Photo
- **Related architecture:** [`docs/architecture.md`](../../architecture.md)
- **Related ADRs:**
  [ADR 0028](../../adr/0028-operate-one-canonical-deployment.md),
  [ADR 0032](../../adr/0032-reconcile-code-owned-feature-flags-at-startup.md)
- **ADR impact:** Conforms to ADR 0028 and implements accepted ADR 0032.

## Outcome

A pull request delivers every runtime release-gate definition to the canonical deployment without
requiring manual row creation and without activating the feature. The running database contains
exactly the definitions owned by the deployed image. Operators continue to change exposure through
Django Admin without a redeploy.

## Scope

This design adds:

- one authoritative registry for runtime release-gate keys and descriptions;
- a definition-based evaluation interface for production call sites;
- explicit transactional reconciliation during web startup;
- definition immutability in Django Admin;
- reuse of registered definitions by local bootstrap tooling; and
- CI and operational evidence for the first canonical reconciliation.

It does not add percentage rollout, cohorts, per-user overrides, a third-party flag service, a
second deployment, permanent authorization rules, or automatic activation.

## Registry and evaluation contract

`feature_flags.registry` owns immutable `FeatureDefinition` values. A definition contains only:

- `key`: a stable lowercase hyphenated database key; and
- `description`: concise operator-facing text.

The module exports named definition constants and one deterministic tuple containing every active
definition. Registry validation rejects duplicate keys, empty or malformed keys, empty descriptions,
and values that exceed the persisted model limits.

`is_enabled` and `is_server_enabled` accept a `FeatureDefinition`, not an arbitrary string. They
query by `definition.key` and retain ADR 0028 behavior: a missing row or unrecognized stored state
evaluates as `off`; `staff` requires an authenticated active staff user; `on` permits every otherwise
authorized caller. A feature definition never grants authorization or bypasses its independent
runtime prerequisites.

Tests may use explicit test-only `FeatureDefinition` values. Production call sites import named
definitions from the registry. Static type checking and repository tests reject retained raw-string
production calls.

## Reconciliation contract

The `sync_feature_flags` management command performs one transaction:

1. Validate the complete registry before any database write.
2. Lock existing feature rows for reconciliation.
3. Create each missing registered row with `state=off`.
4. Update a registered row's description when code changed it, without changing its key, state, or
   creation timestamp.
5. Preserve unchanged registered rows and their current `off`, `staff`, or `on` states.
6. Delete every row whose key is absent from the registry.
7. Print only sanitized counts for created, updated, preserved, and deleted rows.

The command is idempotent. It never accepts an option for initial `staff` or `on`, never changes the
state of an existing registered row, and never prints descriptions or operator-selected states.
Registry validation or database failure aborts the transaction and exits nonzero.

The deployed web entrypoint runs the command immediately after `migrate --noinput` and before
photographer-group bootstrap, static collection, metrics setup, and Gunicorn. A failed command keeps
the candidate unhealthy and leaves the prior deployment rollback path authoritative. The Commerce
and photo workers do not independently reconcile definitions.

## Admin and lifecycle

Django Admin lists the key, description, state, and update timestamp. Only `state` is editable.
Admin cannot add or delete feature rows and treats key, description, and timestamps as read-only.
Normal Django permissions continue to control who may change state, and normal Admin history records
those operator state changes.

Adding a gated feature means adding its registry definition and using that definition at every
authoritative seam in the same pull request. It appears after deployment as `off`. Moving it to
`staff` or `on` remains a separate operator action.

Removing a stable or rejected feature means removing its guarded branches and registry definition
in one pull request. Reconciliation deletes the stale row. Existing Admin log history remains. A
rollback to code that knows the definition recreates it in `off`; it does not restore the deleted
state automatically.

The existing local paid-purchase bootstrap imports the registered purchase definitions and may
explicitly move only those rows to `on` after the general reconciliation has created them. Its
DEBUG-only and local-review boundaries remain unchanged.

## Existing data and first rollout

No schema migration or backfill is required. Before merging the first implementation, a read-only
canonical inventory records the existing feature keys without exposing unrelated data. Every
existing key must be classified as registered or stale. The deployment preserves the state of every
registered row, refreshes descriptions, creates missing definitions in `off`, and deletes only the
reviewed stale set.

After deployment, read-only verification compares the canonical key set with the deployed registry,
confirms that newly created definitions are `off`, and confirms that pre-existing registered states
were preserved. Logs and reports contain keys only when needed for operator verification and never
contain secrets, customer data, bearer values, or unrelated database content.

## Failure and rollback

- Invalid registry: startup fails before database mutation.
- Database or reconciliation failure: the transaction rolls back and the candidate does not become
  healthy.
- Missing row during request evaluation: the feature remains `off`.
- Accidental definition removal caught before merge: restore the registry entry; no deployment has
  changed data.
- Incorrect removal after deployment: roll back to the previous image; the restored definition is
  recreated in `off`, then an operator may deliberately restore `staff` or `on`.
- Incorrect activation: change the row to `off` through Admin; no deploy is required.

## Acceptance criteria

1. Adding a registered definition and deploying creates exactly one row in `off` without manual
   database or Admin work.
2. Reconciliation preserves existing `off`, `staff`, and `on` states and updates only changed
   descriptions.
3. Removing a definition deletes its stale row, and a repeated reconciliation makes no changes.
4. Invalid or duplicate definitions fail before mutation.
5. Production feature checks use registered definition objects rather than raw keys and retain all
   ADR 0028 exposure semantics.
6. Admin users with change permission can modify only state; they cannot create, delete, rename, or
   redescribe a definition.
7. Startup runs migration, reconciliation, remaining bootstrap, and Gunicorn in that order and
   fails closed when reconciliation fails.
8. The local purchase-review bootstrap uses the same registered definitions and still enables its
   exact local flag set.
9. Focused tests, full repository verification, deployment contracts, automatic deployment, and
   read-only live reconciliation verification pass.

## Rejected alternatives

### Per-flag data migrations

Data migrations make creation explicit but bind temporary release controls to immutable schema
history and require a new migration for routine description or lifecycle changes. They are not
selected.

### `post_migrate` reconciliation

A signal hides the mutation inside every command that emits `post_migrate`, makes ordering and
failure evidence less explicit, and is harder to isolate in deployment logs. It is not selected.

### Request-time creation

Creating missing rows during evaluation turns ordinary reads into writes, introduces races, and
makes activation inventory depend on traffic. It is not selected.

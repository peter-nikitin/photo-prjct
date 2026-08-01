# Staging processing-state reset during selfie-search rollout

- Date: 2026-07-31
- Environment: staging
- Classification: release-process failure; no production incident
- Scope: derived photo-processing and selfie-search state
- Outcome: staging was reset to a known processing state; events, photos, users, and original media
  were preserved

## Summary

The public selfie-search rollout required clearing derived processing data from staging before the
critical path could be validated reliably. Deleting this data was not an inherent requirement of
the feature or its database migrations. It was a recovery decision after several worker and API
contract revisions had left staging with processing rows produced by incompatible intermediate
versions.

The central planning error was treating a schema migration as if it also migrated the operational
state of the background-processing system. The release changed processor identities, result
payloads, limits, and enrollment behavior, but did not define how existing states, jobs, attempts,
and artifacts would move to the new contract.

## What was deleted

The reset removed rebuildable, derived staging data:

- processing runs, states, jobs, and attempts;
- derived detections, face embeddings, and preview artifacts;
- incomplete selfie-search jobs and results;
- temporary objects under the processing preview and selfie-search storage prefixes.

The reset preserved authoritative source data:

- users and permissions;
- events and their publication/access configuration;
- photo rows and uploaded original media;
- unrelated Object Storage objects.

This distinction matters: the operation rebuilt an index and its execution history. It did not
recreate the application database or its users.

## What happened

1. Staging accumulated processing rows from several iterations of the face worker and its Django
   API contract.
2. The rollout introduced new processor identities and a larger real face-embedding result. An
   intermediate 8 KiB terminal-result limit was too small for the version 2 payload, producing
   failed attempts.
3. New migrations updated the schema, but existing rows still described work requested or completed
   under older identities, configuration hashes, payload limits, and state transitions.
4. Enrollment is intentionally idempotent. Existing processing state therefore prevented some
   photos from being treated as clean, never-processed inputs for the new worker path.
5. Attempts and evidence are intentionally immutable. A normal ORM delete was rejected by the
   database trigger rather than silently rewriting history.
6. The first manual `TRUNCATE` set was incomplete and was rejected by foreign-key dependencies from
   selfie-search tables. The transaction rolled back without a partial reset.
7. A single transaction then truncated the complete set of related derived tables. Counts were
   checked afterwards, and events, photos, users, and originals remained intact.
8. A bounded representative backfill produced accepted embeddings, after which the live selfie
   upload, cleanup, stable result URL, and free/paid media paths were verified.

## Root cause

We planned and tested the following two release concerns:

- application and database schema compatibility;
- worker behavior for newly created jobs.

We did not plan the third concern:

- migration of durable processing state already present in the environment.

Background processing state is a persistent graph, not an interchangeable queue:

```text
photo
  -> processing state
  -> job
  -> attempt
  -> accepted result or artifact
```

Changing a processor version, configuration hash, payload shape, size limit, input source, or
enrollment edge requires an explicit decision for every existing node in that graph. Database
migrations alone cannot decide whether old work should be retained, drained, retried, superseded,
backfilled, or purged.

## Contributing factors

- The worker, callback contract, payload limits, deployment configuration, and public search were
  integrated through several successive changes. Staging accumulated valid intermediate evidence
  between those changes.
- The real version 2 embedding payload was not exercised through every size boundary before the
  first activation attempt.
- Existing-photo behavior was known to require a bounded backfill, but backfill and re-enrollment
  were not initially treated as part of the release path.
- Tests primarily proved a fresh database and newly created jobs. They did not rehearse an upgrade
  from a snapshot containing old successful, failed, leased, and terminal processing states.
- There was no supported operational command to inspect compatibility and requeue or purge a
  bounded set of derived processing state.
- Although staging was disposable, there was no reviewed reset runbook listing the complete
  foreign-key closure and the authoritative data that must be preserved.

## Why the database protections were not the problem

The immutable-attempt trigger and foreign keys behaved correctly:

- immutability prevented a normal application path from erasing accepted execution evidence;
- foreign keys prevented an incomplete destructive reset;
- transactions prevented the failed attempts from leaving the database partially cleared.

The mistake was attempting an ad hoc cleanup without first choosing a supported state-migration or
reset path. Weakening these protections would hide the release-process error and make future data
loss more likely.

## Required safeguards for future worker changes

Every plan that changes a worker contract, processing state, or derived artifact must include the
following sections before implementation begins.

### 1. Live-state inventory

Record counts grouped by:

- processor type, contract version, processor version, and configuration hash;
- processing state and job/attempt status;
- current leases and retry eligibility;
- accepted results and published artifacts;
- related Object Storage prefixes.

### 2. Compatibility matrix

State whether each combination is supported during rollout:

| Django | Worker | Existing row | Required behavior |
| --- | --- | --- | --- |
| old | old | old | current baseline |
| new | old | old | drain or remain readable |
| new | new | old | retain, retry, supersede, backfill, or purge |
| new | new | new | target behavior |

Do not activate the new worker until every applicable row has an explicit outcome.

### 3. Data-state migration

Choose one reviewed path:

- backward-compatible drain of old jobs;
- version-aware reconciliation and requeue;
- bounded event/photo backfill;
- explicit purge of rebuildable derived state;
- documented full staging reset when the environment is intentionally disposable.

This decision is separate from Django schema migrations.

### 4. End-to-end contract sizing

Run at least one representative maximum result through:

```text
worker serialization
  -> HTTP client limit
  -> Nginx/Django request limit
  -> callback validation
  -> model-field validation
  -> database persistence
```

All limits must be derived from one named contract rather than changed independently.

### 5. Upgrade rehearsal

Test or rehearse deployment against a previous-version snapshot containing:

- a successful old attempt;
- a failed old attempt;
- an active or expired lease;
- a terminal processing state;
- published derived artifacts;
- photos that have never been enrolled.

A fresh empty database is not sufficient release evidence.

### 6. Staged activation order

Use this order unless the plan records a justified exception:

```text
feature flags off
  -> deploy compatible Django and migrations
  -> inspect existing state
  -> run exact worker-image model smoke
  -> reconcile or backfill a bounded cohort
  -> verify results, cleanup, latency, and memory
  -> enable public submissions
```

### 7. Supported operational commands

Prefer reviewed, bounded commands over ad hoc SQL:

- inspect processing compatibility;
- requeue one processor/version for an event or bounded cohort;
- backfill missing derived state with an explicit limit;
- purge rebuildable derived state with confirmation and preservation checks.

If a full staging reset remains useful, its runbook must enumerate the complete related-table set,
run transactionally, clear only approved storage prefixes, and prove that users, events, photos,
and originals remain unchanged.

## Release review questions

Before approving a future worker-related rollout, the implementer and reviewer must answer:

1. What happens to every existing processing state after this change?
2. Can the new Django version safely coexist with the old worker during deployment?
3. Can the new worker safely consume old jobs and callbacks?
4. How are old photos enrolled or intentionally excluded?
5. Has the largest realistic payload crossed every configured limit?
6. Is retry/requeue supported without rewriting immutable attempt evidence?
7. If cleanup is required, is it a reviewed migration or runbook rather than release-time SQL?
8. What exact evidence proves that authoritative rows and original media are preserved?

If any answer is unknown, the rollout is not decision-complete.

## Final lesson

Worker releases have three independent migration surfaces:

1. schema migration;
2. executable contract compatibility;
3. durable processing-state migration.

The staging reset was required because the third surface was omitted from the rollout plan. Future
plans must make it explicit before code is activated against an environment with existing work.

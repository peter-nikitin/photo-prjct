# Gated pgvector face-search rollout

Related: [plan](../plans/2026-09-27-pgvector-exact-face-search.md),
[ADR 0040](../adr/0040-use-pgvector-for-exact-face-search.md),
[ADR 0041](../adr/0041-accept-pgvector-numerical-boundaries.md).

## Delivery states

Repository implementation and local tests do not prove deployment, production completeness,
measured speedup or public activation. The temporary `pgvector-face-search-read` gate starts in
`off`. Production reads stay on the old reader until an operator changes its state in Admin.
`staff` uses the new reader only for validated active staff submissions; queued selfie callbacks
use that trusted submission context and the current gate. `on` exposes the new reader publicly.

Both representations remain complete during this transition. Native exact cosine arithmetic may
change very borderline results within the accepted numerical band. Unexplained differences,
eligibility drift, privacy failures and capacity regression stop advancement. No ANN, top-K,
Python candidate refinement, recognition reprocessing or old-table deletion belongs to this release.

## Agent and operator responsibilities

| Phase | Agent | Operator |
| --- | --- | --- |
| Repository | Implement, review and run selected package gates; prepare one commit and PR | Review concrete release package |
| Before deployment | Read-only inventory; prepare backup/restore evidence and exact rollout scope | Approve PostgreSQL image restart and deployment |
| Population | Run reviewed bounded dry runs, apply batches and reconcile | Select events and retain private reports/cursors |
| Staff review | Run explicit same-input comparisons and report numerical/capacity evidence | Select `staff` in Admin; accept numeric latency/capacity targets from fresh baseline |
| Recovery/public | Verify `off` recovery, immutable saved results and transient cleanup | Select `off`, return to `staff`, then select `on` only after evidence is accepted |

Merge to `main` triggers Deploy and can restart the database on this release. Obtain deployment
approval after the concrete PR and local proof are ready. Use the canonical workflow; do not
replace volumes or bypass it with an ad hoc production Compose restart.

## Before changing the image

Use the existing personal SSH access and protected Compose context. Do not copy credentials into
reports. Capture the deployed SHA, database image ID/digest, PostgreSQL major, volume identity,
extension availability, collation versions, current flags and aggregate model/job/lease/search/
projection/cluster state. The candidate inventory command tolerates the absent new schema.

A read-only inspection on 2026-09-27 found PostgreSQL 16.15 on Debian Trixie, GLIBC 2.41,
recorded and actual database collation version 2.41 and zero named-collation mismatches.
Refresh this before rollout. The pinned pgvector 0.8.6 PostgreSQL 16 Trixie image must retain
compatible collation behavior. Deployment refuses mismatch before extension/schema changes.
Never run automatic REINDEX or collation REFRESH to hide a mismatch.

Take a verified database backup and restore it on an isolated compatible PostgreSQL 16 instance.
Include old processing states, accepted projections, queued/terminal searches and saved results.
Retain reports/dumps privately outside Git. Prove restoration again after vectors are present.
The candidate database image starts before extension preflight and migrations; schema migration
adds tables/context only, without mass population or recognition reprocessing.

## Protected command context

On the canonical VM:

```sh
cd /opt/photo-prjct
compose() {
  sudo docker compose --project-name photo-prjct --env-file .env \
    -f docker-compose.deployment.yml -f docker-compose.https.yml "$@"
}
compose exec -T web python manage.py inspect_pgvector_face_search
```

The initial inventory can also run from the candidate application image before schema expansion
through the same protected environment. Do not print the environment or request payloads. After
deployment verify the exact image, compatible extension/schema, preserved old flags and new gate
in `off`; prove ongoing accepted callbacks publish both stores atomically.

## Bounded storage population

Choose a reviewed event slug. Dry run is default:

```sh
compose exec -T web python manage.py backfill_pgvector_face_embeddings \
  --event-slug EVENT --batch-size 500 --max-rows 5000
compose exec -T web python manage.py backfill_pgvector_face_embeddings \
  --event-slug EVENT --batch-size 500 --max-rows 5000 --apply
compose exec -T web python manage.py verify_pgvector_face_embeddings --event-slug EVENT
```

Batch size is 1–1000; each invocation scans at most 1–50000 rows, including a partial last batch.
Retain the returned `cursor` and `has_more` privately; resume with `--after UUID` when needed.
Each batch commits independently. Repeated invocations insert or verify; they never overwrite
immutable divergent vectors or modify old processing state. This copies existing embeddings and
does not run inference or convert SFace to AdaFace. Nonzero missing/divergent/invalid eligible
counts prohibit new reading. Account for inactive history separately. Fill other reviewed events
in bounded invocations and rerun reconciliation after concurrent callbacks or app rollback.

## Comparison, capacity and activation

Select `staff` only after complete eligible data. Explicit active-staff POST opt-in
`compare_readers=1` uses one transient query, frozen configuration and corpus snapshot, publishing
one selected result. Ordinary visitors retain old reading. Test both gallery and uploaded-selfie
sources. Retain only aggregate comparison reports, with no vectors, face IDs, bearer links or
selfie objects. Review classified threshold/tie/anchor differences and resulting expansion
changes; every unexplained difference and distance delta above `1e-6` blocks public activation.

The bounded private command uses gallery sources:

```sh
compose exec -T web python manage.py review_pgvector_face_search \
  --event-slug EVENT --max-sources 100 --repeats 10
```

Never exceed 100 sources or 30 repeats. Measure warm/cold cache, alternating events, both model
dimensions and bounded concurrent search with normal gallery and processing traffic. Record total
p50/p95, SQL/cohort time including completeness checks, CPU/RSS, transferred bytes, DB waits and
connections, gallery latency, processing queue age and errors. Agree numeric acceptance targets
from a fresh baseline before `on`; no speedup is assumed from the distance operator alone.

Witness Admin `off` rollback: both query sources return to old reading, saved results remain
unchanged and submitted selfies are deleted before terminal publication. Return to `staff` for
final review, then the operator selects `on` only with complete reconciliation, no unexplained
differences and accepted capacity evidence. Monitor the same metrics after activation; regression
returns the gate to `off` while both stores remain intact.

## App rollback and later model migration

Retain a compatible vector-capable PostgreSQL image once extension/schema expansion succeeds,
even when rolling the app back. A plain image cannot restore support for vector-bearing schema.
If candidate collation compatibility fails before extension changes, deployment may restore the
previous image. Never reverse schema expansion or remove the persistent volume for read rollback.

Later worker separation and SFace-to-AdaFace backfill are separate work. Publishing new-only
AdaFace evidence ends complete old-reader rollback for affected events. Establish that explicit
recovery boundary, migrate all old-store dependents and then remove old embedding storage, old
reader, parallel publication and temporary gate together. Preserve detections, projections,
immutable results and their provenance.

## Local acceptance scope

Reuse the existing local dump in a separate task-owned PostgreSQL 16 pgvector instance.
The source dump and any database on port 5432 remain unchanged. Before schema expansion,
record old-column table counts and deterministic hashes of ordered per-row digests using
bounded memory. After migration, flag synchronization, bounded population and review, compare
the same old columns again. Capture vector-bearing dump/restore evidence on the compatible image.
Never use the restored snapshot as a pytest database.

Run the local contract acceptance against the separate test database:

```sh
.venv/bin/python tests/deployment/pgvector_acceptance/run.py --db-port 25432
DB_PORT=25432 make test-migrations
DB_PORT=25432 make test-operational
```

The runner uses serialized JSON through Django's actual authenticated callback endpoints,
including maximum 32-face 512-dimensional gallery and one-face 512-dimensional selfie bodies,
body limits, leases, atomic parallel publication, replay and cleanup. It also runs the existing
both-source off/staff/on, explicit comparison, one-result and immutable-result regression fixtures.
It uses Django's HTTP test client; external networking, Nginx and deployed performance remain
separate rollout evidence. Snapshot measurements are local, with uncontrolled PostgreSQL cache;
application-cache cold/warm timing and diagnostic scans are reported separately.

Before the first future new-only AdaFace publication, change the transition completeness check
(which currently requires matching legacy evidence identity) and gallery source selection
(which retains a parallel legacy scalar dependency) to independent vector eligibility, and
establish the new recovery boundary. Current readers require both stores during this release.
That work belongs to the future worker/model migration project and must precede its new-only writes.

The base and deployment database services set `/dev/shm` to a 256 MiB tmpfs ceiling.
Local proof reproduced shared-memory exhaustion with two concurrent ordinary native reads
at Docker's default 64 MiB; the identical two reads completed at 256 MiB on the same volume.
This ceiling is allocated as used; it does not reserve 256 MiB or increase VM capacity.
The deployment restart for this change still requires the rollout approval above.

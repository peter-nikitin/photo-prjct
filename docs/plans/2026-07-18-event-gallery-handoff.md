# Event gallery lifecycle handoff

- Date: 2026-07-19
- Status: Gallery unblocked; seed media retired after ADR 0016 was Rejected in favor of ADR 0028
- Branch: `event-gallery`
- Worktree: `/Users/petrnikitin/Documents/Sites/photo-prjct/.worktrees/event-gallery`
- Lifecycle baseline: `origin/main` at `a51cb6d` was merged into the branch by merge commit
  `3f279d1`
- Authorized scope: ADR 0015 lifecycle remediation and documentation handoff only; no runtime,
  deployment, cloud, or seed implementation

## Current delivery state

- Gallery Tasks 1-6 in
  [the gallery plan](2026-07-18-event-photo-gallery.md) are implemented in commit range
  `676bbf2^..0ee60ac` (inclusive of `676bbf2`). The range includes the planned task commits and
  follow-up interaction, accessibility, and lifecycle fixes.
- Task 6's last recorded visual verification passed all 43 tests.
- Gallery Task 7, deployment propagation and candidate private-media preflight, has not started.
- Gallery Task 8, final verification and repository-truth reconciliation, has not started.
- Every task in
  [the staging seed-media plan](2026-07-18-staging-seed-photo-media.md) is unstarted and retired.
  It must not be resumed. There is no manifest, seed command, startup gate, uploader, or seed
  deployment configuration.
- No cloud object was uploaded. Reference-photo seeding is not enabled. The gallery is not activated
  on staging. No IAM, bucket policy, ACL, CORS, lifecycle, quota, or other cloud configuration was
  changed.

## Lifecycle review verdict

The two specifications were approved before the repository introduced the specification-to-ADR
lifecycle gate. That approval selected the designs but did not itself accept either architectural
decision. On 2026-07-19 the maintainer explicitly accepted ADR 0015 only. ADR 0016 remained
Proposed at that time and was later Rejected in favor of ADR 0028; accepting ADR 0015 does not
authorize or unblock seed-media implementation.

| Artifact | Current lifecycle state | Delivery effect |
| --- | --- | --- |
| [ADR 0015](../adr/0015-allow-anonymous-free-event-original-delivery.md) | Accepted on 2026-07-19 | Gallery plan is unblocked |
| [Gallery specification](../superpowers/specs/2026-07-18-event-photo-gallery-design.md) | Approved; conforms to accepted ADR 0015 | Gallery Tasks 7-8 may continue in order |
| [Gallery plan](2026-07-18-event-photo-gallery.md) | In progress; Tasks 1-6 complete | Task 7 is next, followed by final Task 8 reconciliation |
| [ADR 0016](../adr/0016-allow-deterministic-staging-reference-media.md) | Rejected in favor of ADR 0028 | No seed implementation is authorized |
| [Seed specification](../superpowers/specs/2026-07-18-staging-seed-photo-media-design.md) | Retired historical proposal | Must not be resumed |
| [Seed plan](2026-07-18-staging-seed-photo-media.md) | Retired; every checkbox remains open | No seed task may start or resume |

## Lifecycle remediation recorded

- Both specifications and plans retain exact architecture and ADR links.
- ADRs 0015 and 0016 were created as Proposed decisions. The maintainer's later explicit acceptance
  applies only to ADR 0015. ADR 0016 was later Rejected in favor of ADR 0028.
- The gallery specification now conforms to ADR 0015. The gallery plan records Tasks 1-6 as complete
  from the inclusive range `676bbf2^..0ee60ac`, leaves Tasks 7-8 open, and ends with architecture
  and ADR reconciliation.
- The seed specification and plan are retired. Their original proposal content and open tasks are
  preserved as history, but neither may be resumed.

## Repository truth that remains stale

- `docs/architecture.md` does not yet record the branch's implemented gallery presentation,
  event-scoped private-original route, or local lightbox. After gallery Task 7 verification, Task 8
  must add only verified implemented facts. The accepted-constraint summary/link for ADR 0015 and
  the narrowed paid-event and broader attachment/download-policy open decision are already
  recorded; Task 8 must not claim staging activation, seed media, derivatives, workers, or commerce.
- `docs/product-jobs.md` still lists PJ-005 as `Candidate` and is intentionally unchanged during
  lifecycle remediation. Gallery Task 8 owns the evidence-backed transition after final Python,
  JavaScript, visual, and deployment verification. PJ-009 stays `Candidate`: inline gallery
  delivery does not implement the separate download product job.
- `docs/engineering-jobs.md` has no capability entry for safe private gallery-media delivery. The
  final gallery reconciliation should add the next stable job only if Task 7's candidate-image read
  preflight and final checks are complete, with evidence limited to the verified route, safe stream,
  and deployment gate. It must not claim IAM mutation or live staging evidence.
- The retired seed proposal must not add implementation or accepted-ADR wording to architecture.
  Any future reference-media work requires a new decision and separately approved scope.

## Continuation order

1. Complete Gallery Task 7: deployment propagation and the candidate private-media read preflight.
2. Complete Gallery Task 8: final verification, architecture/ADR/job reconciliation, and pull-request
   outcome. The remaining continuation work is Gallery Tasks 7-8. The eventual PR contains the
   complete Gallery Tasks 1-8 delivery and its specifications, plans, ADRs, and lifecycle
   documentation; it contains no seed implementation.
3. Do not resume seed media. ADR 0016 was later Rejected in favor of ADR 0028, and the linked seed
   specification and plan are retired. Any future reference-media work requires a new decision and
   separately approved scope.
4. Do not run the uploader or any `--apply` path under the retired seed plan.

No step in this handoff authorizes object creation, seed enablement, gallery activation, IAM change,
or any other cloud mutation.

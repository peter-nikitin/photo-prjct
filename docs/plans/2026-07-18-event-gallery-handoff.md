# Event gallery lifecycle handoff

- Date: 2026-07-19
- Status: Blocked on specification and ADR lifecycle reconciliation
- Branch: `event-gallery`
- Worktree: `/Users/petrnikitin/Documents/Sites/photo-prjct/.worktrees/event-gallery`
- Lifecycle baseline: `origin/main` at `a51cb6d` was merged into the branch by merge commit
  `3f279d1`
- Authorized scope: documentation handoff only; no lifecycle remediation or implementation in this
  handoff

## Current delivery state

- Gallery Tasks 1-6 in
  [the gallery plan](2026-07-18-event-photo-gallery.md) are implemented in commit range
  `676bbf2..0ee60ac`. The range includes the planned task commits and follow-up interaction,
  accessibility, and lifecycle fixes.
- Task 6's last recorded visual verification passed all 43 tests.
- Gallery Task 7, deployment propagation and candidate private-media preflight, has not started.
- Gallery Task 8, final verification and repository-truth reconciliation, has not started.
- Every task in
  [the staging seed-media plan](2026-07-18-staging-seed-photo-media.md) is unstarted. There is no
  manifest, seed command, startup gate, uploader, or seed deployment configuration.
- No cloud object was uploaded. Reference-photo seeding is not enabled. The gallery is not activated
  on staging. No IAM, bucket policy, ACL, CORS, lifecycle, quota, or other cloud configuration was
  changed.

## Lifecycle review verdict

The two specifications were approved before the repository introduced the specification-to-ADR
lifecycle gate. That approval selected the designs; it did **not** accept either architectural
decision below. The specifications and plans are now blocked under the current workflow until the
exact remediation in this handoff is complete.

| Artifact | Current text | Required lifecycle state |
| --- | --- | --- |
| [Gallery specification](../superpowers/specs/2026-07-18-event-photo-gallery-design.md) | `Approved`; broad architecture link; no `Related ADRs` or `ADR impact` | Approved design requiring ADR 0015; blocked for further implementation |
| [Seed specification](../superpowers/specs/2026-07-18-staging-seed-photo-media-design.md) | `Approved`; broad architecture link; ADRs 0006 and 0013 only; no `ADR impact` | Approved design requiring ADR 0016; blocked before implementation |
| [Gallery plan](2026-07-18-event-photo-gallery.md) | `Draft`; Tasks 1-6 implemented despite unchecked boxes | Lifecycle-blocked; Tasks 7-8 must not start |
| [Seed plan](2026-07-18-staging-seed-photo-media.md) | `Draft`; no task started | Lifecycle-blocked; no task may start |

## Exact specification corrections

### Gallery specification

Update
`docs/superpowers/specs/2026-07-18-event-photo-gallery-design.md` as follows:

- Replace the broad `Related architecture` link with exact links to `Current architecture —
  implemented`, `Target MVP architecture — proposed`, `Photo ingestion and indexing`, `Purchase
  and download`, `Security, privacy, and legal boundaries`, `Evolution stages`, and `Open
  decisions` in `docs/architecture.md`.
- Add `Related ADRs` links for ADRs 0001, 0002, 0006, 0013, and 0014. Add ADR 0015 after it is
  created.
- Add `ADR impact: Requires new ADR — anonymous complete-original delivery for uploaded photos on
  published free events`.
- Preserve the selected presentation-model, eligibility, close-safe streaming, and local-lightbox
  design. Do not relabel the complete original as a derivative or silently broaden it into a
  general download policy.

### Seed specification

Update
`docs/superpowers/specs/2026-07-18-staging-seed-photo-media-design.md` as follows:

- Replace the broad `Related architecture` link with exact links to `Current architecture —
  implemented`, `Accepted constraints`, `Target MVP architecture — proposed`, `Photo ingestion and
  indexing`, `Security, privacy, and legal boundaries`, `Evolution stages`, and `Open decisions`.
- Set `Related ADRs` to ADRs 0002, 0003, 0005, 0006, and 0013. Add ADR 0016 after it is created.
- Add `ADR impact: Requires new ADR — deterministic thirteen-photo staging reference-media
  exception to the normal ADR 0013 ingestion path`.
- Preserve the database-only routine path and separately confirmed operator uploader. Do not
  describe the exception as conformance to ADR 0013: ADR 0013 remains authoritative for normal
  photographer ingestion.

## Required proposed ADRs

Create both records from `docs/adr/0000-template.md`, add them to `docs/adr/README.md`, and leave
each `Proposed` until the maintainer accepts that exact decision separately.

### ADR 0015: anonymous complete-original delivery on published free events

Suggested path:
`docs/adr/0015-allow-anonymous-free-event-original-delivery.md`.

The decision must be narrow: an anonymous client may receive the complete stored original inline
only for an eligible completed upload belonging to a currently published free event. Delivery goes
through the stable event/photo/variant Django route; every request rechecks database eligibility;
the private bucket, permanent object key, and credentials remain undisclosed; and the initial
response is private/no-store with no Range, response ETag, or redirect to Object Storage.

Explicitly exclude paid-event media, attachment/download product UX, purchased entitlements,
derivative generation or readiness, watermarks, cart/checkout/commerce, public-bucket or CDN
delivery, general signed-URL policy, retention/deletion, and broader anonymous-original access.

### ADR 0016: deterministic thirteen-photo staging reference-media exception

Suggested path:
`docs/adr/0016-allow-deterministic-staging-reference-media.md`.

The decision must be narrow: the thirteen frozen prototype identities may use fixed final keys in
the existing private `hires-staging` bucket and deterministic PostgreSQL reconciliation. A separate
operator action may conditionally create only absent fixed objects after an all-key dry run and
fresh confirmation. This is an explicit staging reference-data exception; ADR 0013 continues to
govern all normal photographer ingestion through incoming keys, verification, and promotion.

Explicitly exclude production, arbitrary legacy backfill, per-database object copies, deployment,
migration, startup, or seed-command S3 access, overwrite, copy, multipart upload, ACL/IAM/CORS or
lifecycle changes, deletion, and automatic seed enablement.

Specification approval is decision evidence only. It must not be recorded as ADR acceptance. Ask
the maintainer to accept ADR 0015 and ADR 0016 separately, naming each exact decision; acceptance of
one does not unblock the other plan.

## Exact plan corrections after ADR acceptance

Only after the applicable ADR is explicitly accepted may its plan be unblocked and edited.

For `docs/plans/2026-07-18-event-photo-gallery.md`:

- Change `Status: Draft` to `Status: In progress — Tasks 1-6 complete; Tasks 7-8 not started`.
- Replace `Approved specification` with the template field `Related specification` and retain the
  exact gallery-specification link.
- Add exact architecture links matching the corrected specification, add accepted ADR 0015 to the
  existing applicable ADR links, and add
  `ADR impact: Resolved — conforms to accepted ADR 0015 and applicable ADRs 0001, 0002, 0006, 0013,
  and 0014`.
- Mark Tasks 1-6 complete and record commit range `676bbf2..0ee60ac` plus the 43-passed Task 6 visual
  result. Leave every Task 7 and Task 8 checkbox open.
- Keep Task 7 before Task 8. Task 8 must be the final architecture and ADR reconciliation after all
  behavior verification and before push.

For `docs/plans/2026-07-18-staging-seed-photo-media.md`:

- Change `Status: Draft` to `Status: Ready for implementation`.
- Replace `Approved specification` with `Related specification`, add accepted ADR 0016, use the
  corrected exact architecture/ADR links, and add
  `ADR impact: Resolved — conforms to accepted ADR 0016 while ADR 0013 remains authoritative for
  normal ingestion`.
- Leave every task checkbox open. Its final task must reconcile the implemented exception with ADRs
  0016 and 0013 and with `docs/architecture.md` before push.

## Repository truth that remains stale

- `docs/architecture.md` does not yet record the branch's implemented gallery presentation,
  event-scoped private-original route, or local lightbox. After gallery Task 7 verification, Task 8
  must add only verified implemented facts, summarize/link accepted ADR 0015, and narrow the open
  free/paid media-policy item to the unresolved paid-event and broader download-policy decisions.
  It must not claim staging activation, seed media, derivatives, workers, or commerce.
- `docs/product-jobs.md` still lists PJ-005 as `Candidate`. During lifecycle remediation it should
  move to `In progress` with the implementation range as evidence; after Task 8 it may move to
  `Validated` only with the final Python, JavaScript, visual, and deployment evidence. PJ-009 stays
  `Candidate`: inline gallery delivery does not implement the separate download product job. Each
  transition must update the current row and detail and append one history row.
- `docs/engineering-jobs.md` has no capability entry for safe private gallery-media delivery. The
  final gallery reconciliation should add the next stable job only if Task 7's candidate-image read
  preflight and final checks are complete, with evidence limited to the verified route, safe stream,
  and deployment gate. It must not claim IAM mutation or live staging evidence.
- Seed implementation, if later completed, may add implemented reference-data mechanism wording and
  accepted ADR 0016 to architecture, but must still state that no object upload or environment
  enablement occurred unless separately evidenced.

## Continuation order

1. Correct both specifications with the exact metadata and classifications above.
2. Create ADR 0015 and ADR 0016 as `Proposed`; update the ADR index.
3. Obtain separate explicit maintainer acceptance for each ADR.
4. Unblock and update only the plan whose ADR is accepted, using the exact status and metadata
   changes above.
5. For the gallery, complete Task 7, then Task 8 final verification and architecture/ADR/job
   reconciliation.
6. Execute seed-media Tasks 1-8 separately only after ADR 0016 is accepted; do not combine seed work
   or operator mutation with the gallery continuation.
7. Stop before any uploader `--apply`. A future apply still requires a fresh session-specific scope
   display, dry run, exact bucket and thirteen keys, active non-secret context, total bytes, price
   impact or unknown, verification and no-delete rollback, followed by fresh explicit confirmation.

No step in this handoff authorizes object creation, seed enablement, gallery activation, IAM change,
or any other cloud mutation.

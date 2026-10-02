# 0046: Isolate worker-pool management in a separate folder

- Status: Accepted
- Date: 2026-09-30
- Deciders: project maintainer; explicitly selected a separate worker folder and approved the shared-VPC design in conversation on 2026-09-30
- Supersedes: none
- Superseded by: [ADR 0049](0049-retire-local-photo-worker-recovery-after-remote-acceptance.md)
  only for post-acceptance recovery through on-host photo workers

## Context

ADR 0042 selects Yandex Instance Groups for bulk/selfie processing. Yandex requires the group's manager account to have `compute.editor` on the folder containing its group. The existing folder also owns the canonical VM and PostgreSQL boot disk, so that grant would include customer-serving stateful infrastructure. The current provisioning contract assumes that worker and canonical resources share a folder; that assumption must be removed before paid activation.

## Decision drivers

- Confine worker control-plane Compute privileges to disposable worker resources.
- Keep the existing canonical VM, PostgreSQL, secrets, public edge and release authority in place.
- Retain private connectivity, queue-driven scaling and bulk idle-zero savings.
- Use supported Yandex folder/VPC boundaries without a new network appliance or monitoring stack.

## Considered options

1. Keep groups in the existing folder and accept folder-wide Compute management over the canonical VM.
2. Place worker resources in a dedicated folder in the same cloud and extend the existing VPC to its new worker subnet.

## Decision

Select option 2. Place worker groups, manager/runtime identities, bootstrap secret, clean image and new worker network resources in a dedicated folder. Keep existing canonical resources and the shared VPC in their current folder. Extend the VPC using a new worker subnet; do not move existing resources.

Grant the manager `compute.editor` only in the worker folder. Grant access to cross-folder VPC resources at the minimum supported scope, without Compute management on the canonical folder, cloud or organization. Keep worker runtime separate, with payload read only on its exact bootstrap secret. Validate effective inherited and direct access before activation; a separate folder alone does not prove identity isolation.

Represent worker and canonical folders explicitly in provisioning, observation, release and rollback contracts. Observe worker membership in its folder while publishing and selecting native autoscaling metrics in the existing canonical Monitoring folder. Reject ambiguous or same-folder inputs. Preserve ADR 0042's one-SHA release, private authenticated TLS and durable processing semantics, the approved ceiling-one policy, and ADR 0043's diagnostic authority.

This acceptance governs the architecture; it does not approve paid creation, access changes or cutover. The existing local workers serve until separately approved activation and live acceptance complete.

## Consequences

### Positive

- Worker-manager folder-wide Compute authority does not cover the canonical VM or its disk.
- Existing private networking and metric publication can remain in use.
- Worker resource inventory, lifecycle receipts and cleanup have a clear ownership boundary.

### Negative

- Cross-folder VPC grants and ownership checks add deployment configuration and acceptance work.
- Current single-folder host configuration must be replaced before first remote activation.
- Folder isolation does not remove the shared canonical host/VPC dependency or change cloud-wide quotas and charges.

### Follow-up

- Implement and verify explicit folder contracts under the linked plan.
- Prepare exact IAM/network/image commands and cost before separate operational approval.
- Close the cap-one alert delivery prerequisite before customer cutover; keep unresolved monitoring API requirements visible.

## Validation and rollback

Require tests proving correct ownership, metric namespace and release configuration; actual effective-access read-back; private TLS/public denial; representative worker processing; bulk zero/wakeup; serial VM/disk replacement and whole-release rollback. A cross-folder permission failure stops activation and preserves local processing. Initial rollback fences and drains remote claims before restoring local placement, preserving attempts, results, artifacts and pgvector state. No database restore is part of this decision.

## References

- [Approved specification](../superpowers/specs/2026-09-30-isolated-worker-folder-activation-design.md)
- [Implementation plan](../plans/2026-09-30-isolated-worker-folder-support.md)
- [ADR 0042](0042-isolate-autoscaled-photo-worker-pools.md)
- [ADR 0043](0043-observe-isolated-workers-with-git-managed-alerts.md)
- [Dated preflight](../operations/2026-09-30-worker-folder-activation-preflight.md)
- [Instance Groups access](https://yandex.cloud/ru/docs/compute/concepts/instance-groups/access)
- [Multi-folder VPC](https://yandex.cloud/ru/docs/vpc/tutorials/multi-folder-vpc)

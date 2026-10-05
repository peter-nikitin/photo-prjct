# Worker isolation preparation inventory

- Date: 2026-09-27
- Status: Read-only preparation; no code deployment or cloud mutation
- Related plan: [Isolation](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/plans/2026-09-27-autoscaled-worker-isolation.md)

## Observations

Fresh `origin/main` and actual canonical application/worker images agree on
`7bfb0598dbfe81e715aeb608150d61b936a26e79`. The planning worktree starts at `b28d8b7` and
contains unstaged documentation; integrate current main before implementation, preserving
its pgvector specification/plan and accepted ADR.

SSH read-only Docker inspection found two bulk workers and one selfie worker, plus import,
commerce, healthy web and healthy `postgres:16`. A retained `photo-prjct-staging-db-1` also
exists; it is not a relocation target and must not be stopped/deleted as cleanup.

Both inspected worker containers have a configured limit of 2 CPU and 5 GiB memory. These are
configuration limits, not measured consumption. A hypothetical 4 GiB worker VM would not preserve
the current container budget; propose 2 vCPU / 8 GiB VM shapes pending approval rather than
treating the earlier 2/4 price example as a deployable configuration.

Bulk identities:

```text
1/capture_metadata/2
2/generate_preview/1
2/generate_watermarked_preview/1
2/face_embedding/3
3/face_embedding/5
1/bib_recognition/1
```

Selfie identity: `1/selfie_query/2`. Both use the existing Compose HTTP worker API.
These lists are configured allowlists, not evidence that every corresponding feature is active
or currently has jobs. Preserve actual feature states and inspect jobs/attempts separately before
cutover; do not add identities from a hypothetical future backfill.

The running Nginx container has the existing
`/etc/letsencrypt/live/photo-prjct/fullchain.pem` link. This proves file presence, not the proposed
private listener's TLS acceptance or automatic renewal/reload. Reusing this managed certificate
with matching hostname and worker-only private address resolution remains a configuration
proposal; do not disable certificate verification or open a public worker route.

## Remaining evidence and authority

Actual queue/attempt/lease/artifact inventory, private network/SG/service-account mapping, full
callback and upgrade rehearsal, fixed VM/disk/spend approval and reviewed coordinated retirement
are still outstanding. No resource measurement/benchmark is scheduled. This partial report does
not mark the plan's complete release-safeguard inventory GREEN.

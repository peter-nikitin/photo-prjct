# Worker-pool activation cost

- Date checked: 2026-09-28.
- Status: current public tariff estimate for approval; no resources created and no account bill inspected.
- Region/currency: Russia, RUB, published prices including VAT; no CVoS commitment, grant or individual discount.
- Scope: incremental cost; the existing main VM remains 8 vCPU / 16 GiB and yields no assumed savings.
- Contracts: [worker runbook](../runbooks/worker-pools.md), [pool specification](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/superpowers/specs/2026-09-23-autoscaled-photo-worker-pools-design.md), [telemetry specification](https://github.com/peter-nikitin/photo-prjct/blob/c20ea18e8f9647ad6b29f3163279646220927198/docs/superpowers/specs/2026-09-28-worker-pool-telemetry-design.md).

The fixed recurring subtotal is **4,511.02 RUB/month at the floor**, **5,957.00 RUB/month if the initial 1+1 machines run all month**, and **11,605.45 RUB/month at continuous 2+2 capacity**, using 730 hours. These subtotals include worker compute, worker boot disks, one new NAT gateway and one bootstrap-secret version. Add image storage, bootstrap reads, telemetry, traffic and temporary image-building resources below. They are not an all-inclusive invoice or a workload prediction.

## Verified prices and billing units

The official HTML documentation extract omitted dynamic tariff tables. The linked official Markdown versions contain them. Rates were independently checked against the public price-list API used by the official website, discovered in its own JavaScript: `/api/priceList/listServices` and `/api/priceList/getPriceList`. These are unauthenticated public catalogue reads, not the billing-account API. The interactive price-list page could not be loaded reliably; no calculator quote is claimed.

Sources: [Compute pricing](https://yandex.cloud/ru/docs/compute/pricing), [Compute tariff Markdown](https://docs.yandex.cloud/ru/ru/compute/pricing.md), [current Compute catalogue response](https://yandex.cloud/api/priceList/getPriceList?installationCode=ru&currency=RUB&lang=ru&services%5B%5D=dn22pas77ftg9h3f2djj&withExpired=false&withThresholds=true&pageSize=1000&from=2026-09-28&to=2026-09-28).

| Resource | RUB per pricing unit | Pricing unit | Catalogue SKU | Effective UTC instant |
| --- | ---: | --- | --- | --- |
| Regular Intel Ice Lake 100% CPU | 1.24 | vCPU-hour | `compute.vm.cpu.c100.v3` | 2026-04-30 21:00Z |
| Regular Intel Ice Lake RAM | 0.33 | GiB-hour | `compute.vm.ram.v3` | 2026-04-30 21:00Z |
| Preemptible Intel Ice Lake 100% CPU | 0.34 | vCPU-hour | `compute.vm.cpu.c100.preemptible.v3` | 2026-04-30 21:00Z |
| Preemptible Intel Ice Lake RAM | 0.083 | GiB-hour | `compute.vm.ram.preemptible.v3` | 2026-04-30 21:00Z |
| Fast network SSD (`network-ssd`) | 0.0199 | GiB-hour | `nbs.network-nvme.allocated` | 2026-04-30 21:00Z |
| Compute image | 0.0051 | GiB-hour | `compute.image` | 2026-04-30 21:00Z |
| Compute snapshot, if separately retained | 0.0051 | GiB-hour | `compute.snapshot` | 2026-04-30 21:00Z |

The documentation labels these dates April 30; the exact API instant is May 1 at 00:00 Moscow time. Compute uses binary GB (`2^30` bytes), hence GiB here. CPU/RAM and disks are billed per second; image/snapshot usage is reported as MiB-seconds and priced per GiB-hour. Disks remain chargeable while stopped and until deleted; images are separate chargeable objects. Instance Groups itself has no fee. See [Compute billing rules](https://docs.yandex.cloud/ru/ru/compute/pricing.md).

| Additional resource | RUB per pricing unit | Pricing unit | Effective UTC instant | Source |
| --- | ---: | --- | --- | --- |
| NAT gateway | 0.39528 | gateway-hour; usage gateway-seconds | 2025-12-31 21:00Z | [VPC tariffs](https://docs.yandex.cloud/ru/ru/vpc/pricing.md) |
| Bootstrap secret version | 0.0274 | version-hour; usage version-seconds | 2026-04-30 21:00Z | [Lockbox tariffs](https://docs.yandex.cloud/ru/ru/lockbox/pricing.md) |
| Lockbox payload get | 3.79 | 10,000 requests | 2026-04-30 21:00Z | [Lockbox tariffs](https://docs.yandex.cloud/ru/ru/lockbox/pricing.md) |
| Native custom metric write | 0.32 | million values | 2026-03-03 21:00Z | [Monitoring tariffs](https://docs.yandex.cloud/ru/ru/monitoring/pricing.md) |
| Prometheus Remote Write, first 50 million/month | 0 | million values | 2025-12-31 21:00Z | [Monitoring tariffs](https://docs.yandex.cloud/ru/ru/monitoring/pricing.md) |
| Prometheus Remote Write, 50–10,000 million/month | 2.3058 | million values | 2025-12-31 21:00Z | [Monitoring tariffs](https://docs.yandex.cloud/ru/ru/monitoring/pricing.md) |
| Prometheus Remote Write, above 10,000 million/month | 0.6588 | million values | 2025-12-31 21:00Z | [Monitoring tariffs](https://docs.yandex.cloud/ru/ru/monitoring/pricing.md) |

These tables reproduce current catalogue rates, not the earlier 4 GiB planning assumptions. Catalogue effective timestamps were checked for VPC, Lockbox and Monitoring as well; [public catalogue entry point](https://yandex.cloud/ru/price-list).

## Floor, initial creation and hard ceiling

Each worker is `standard-v3`, 2 vCPU at 100%, **8 GiB RAM**, 32 GiB `network-ssd`, no public IP. Selfie is regular with floor 1 and ceiling 2; bulk is preemptible with floor 0 and ceiling 2. The [prepared template](../../deploy/worker-pools/provision.py) specifies `initialSize=1` for both and removes the boot disk with its instance. Rollout fits inside the same maximum two per group, not additional permanent workers.

```text
regular compute/hour       = 2 × 1.24 + 8 × 0.33   = 5.12 RUB
preemptible compute/hour   = 2 × 0.34 + 8 × 0.083  = 1.344 RUB
32 GiB SSD/hour            = 32 × 0.0199           = 0.6368 RUB
regular worker/hour        = 5.7568 RUB
preemptible worker/hour    = 1.9808 RUB
NAT + one version/month    = (0.39528 + 0.0274) × 730 = 308.5564 RUB
```

| Scenario, all listed machines present for 730 h | Selfie | Bulk | Worker compute | Worker SSD disks | NAT | One secret version | Fixed subtotal |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Minimum idle after scale-down | 1 | 0 | 3,737.60 | 464.864 | 288.5544 | 20.002 | **4,511.02** |
| Initial creation, held continuously | 1 | 1 | 4,718.72 | 929.728 | 288.5544 | 20.002 | **5,957.00** |
| Continuous hard ceiling, including rollout | 2 | 2 | 9,437.44 | 1,859.456 | 288.5544 | 20.002 | **11,605.45** |

730 hours is a comparison convention. Actual invoices use elapsed billable time and calendar-month allowance. Preemption/cold start is not a promised saving or guaranteed capacity. Initial bulk warm acceptance requires paid time even with an empty queue. Each hour that this temporary bulk worker and its disk exist adds about **1.9808 RUB** to the floor; 600-second startup or 300-second stabilization is not a contractual shutdown time.

For varying utilization, sum actual instance-running hours and disk-existence hours separately:

```text
worker cost = 5.12 × regular-VM-hours + 1.344 × preemptible-VM-hours
            + 0.0199 × allocated-disk-GiB-hours
```

Disk deletion can lag compute shutdown. The floor assumes scale-down actually deletes bulk instances and disks; stopped, leaked or retained disks remain an additional charge. NAT remains a fixed expense while bound to the route table, including bulk-zero periods. [Compute rules](https://yandex.cloud/ru/docs/compute/pricing), [NAT billing start](https://yandex.cloud/ru/docs/vpc/pricing).

## Image, bootstrap and build costs

Build a clean worker OS image from an explicitly reviewed official Linux image with no paid Marketplace licence. Never clone the canonical VM, its disk, application state or credentials. The temporary image-builder VM/disk are additional resources outside the four-worker ceiling, and need their own bounded lifetime in approval.

- Retained image storage is `0.0051 × billed image GiB × retained hours`. One image billed as 32 GiB for 730 h adds **119.14 RUB/month**; actual billed image size is unknown until built. Two such retained images add **238.27 RUB/month**. Each additional snapshot, if chosen, is charged separately at the same unit rate. Do not assume an OS image has the full boot-disk size.
- An illustrative builder using the same regular worker shape costs **5.7568 RUB per running hour including a simultaneously existing 32 GiB SSD**. Its actual chosen size/runtime remains to be specified. A builder disk retained after shutdown costs **0.6368 RUB/hour**. If operator access requires a temporary public IP, the verified VPC price is **0.26352 RUB/IP-hour**; a private builder using the planned NAT has no new per-worker public-IP fee. These are alternatives for an exact later builder recipe, not approval to add an IP.
- One separate bootstrap-secret version is already in the fixed table. Every additional retained bootstrap version adds **20.002 RUB/month** at 730 h. Canonical application-secret rotation may also leave extra billed versions; that delta depends on the reviewed retention decision. `get` charge is `3.79 × requests / 10,000`; VM churn/reboots and retries, not just simultaneous count, determine requests. The secret is not created by Connection Manager, so its exception is inapplicable.
- The existing GHCR worker image stays in use; no new Yandex Container Registry is required by the [bootstrap contract](../runbooks/worker-pools.md#worker-bootstrap-authority). No object-storage bucket, backup service or separately hosted Prometheus/Grafana VM is added by this estimate.

Price sources: [Compute image/disk catalogue](https://docs.yandex.cloud/ru/ru/compute/pricing.md), [VPC public IP tariff](https://docs.yandex.cloud/ru/ru/vpc/pricing.md), [Lockbox rules](https://yandex.cloud/ru/docs/lockbox/pricing).

For an explicitly illustrative single 32 GiB billed retained image, the fixed subtotals become **4,630.16 / 6,076.14 / 11,724.59 RUB/month**, before the remaining variable costs. These are not substituted for the unknown image size in the approval budget.

## Traffic and shared allowances

The current [public VPC catalogue](https://yandex.cloud/api/priceList/getPriceList?installationCode=ru&currency=RUB&lang=ru&services%5B%5D=dn21qssbrdtcaus362kp&withExpired=false&withThresholds=true&pageSize=100&from=2026-09-28&to=2026-09-28) has a separate `network.egress.nat` SKU (`dn26lqnno6gtkru5b1me`) priced **0 RUB/GiB for all quantities**, effective 2025-04-30 21:00Z. Generic `network.egress.inet` (`dn28ml7sjbb5v98jkuj3`) is free for the first **100 GiB/month**, then **1.42 RUB/GiB**, effective 2026-04-30 21:00Z. Thus there is no additional NAT-specific per-GiB processing charge in this current catalogue; retain ordinary billable internet-egress accounting. The documentation still describes a 100 GiB NAT allowance; it does not imply a current nonzero NAT surcharge.

Allowances apply to the **payment account across its linked clouds/organizations**, reset monthly, and are not a fresh allowance per pool. Existing consumption was not inspected. For additional ordinary billable egress `B` GiB and existing account egress `E`, the incremental tariff calculation is:

```text
1.42 × (max(0, E + B − 100) − max(0, E − 100)) RUB
```

Incoming internet traffic and private-address traffic inside Yandex Cloud are not billed as ordinary internet egress. Public-address intra-cloud traffic has documented exceptions for Object Storage, Cloud Backup and CDN; classify actual flows rather than calling every bootstrap/download byte outgoing. Large GHCR pulls are mainly inbound payload; no traffic volume is assumed. [VPC traffic rules](https://docs.yandex.cloud/ru/ru/vpc/pricing.md), [account-wide free tier](https://yandex.cloud/ru/docs/billing/concepts/serverless-free-tier).

## Native demand metrics and diagnostic Prometheus

Authoritative autoscaling demand/capacity remains in native Monitoring. The [existing publisher](../../src/backend/processing/services/worker_pool_metrics.py) emits eight always-present gauges per pool plus one running-instances gauge with fresh cloud membership: **at most 18 values per successful publication**, independent of worker count. The [canonical timer](../../deploy/worker-pools/metrics.timer) targets 30 seconds. At that cadence for 730 h, `18 × 730 × 3600 / 30 = 1,576,800` values and **0.504576 RUB/month**, approximately **0.50 RUB**, at 0.32 RUB/million. Slower/failing runs write fewer points; this arithmetic is not live delivery proof.

Managed Prometheus diagnostics are a different stream. The approved specification bounds host/container/coordinator labels to pool, instance and zone; runtime may add fixed processor/outcome enums. Eight histogram buckets including infinity plus sum/count produce **10 scalar series per instantiated histogram label set**. Runtime/host probe interval is 30 seconds, but final scalar families, enum counts, published series, backend Remote Write cadence, missing-source behavior and recording-rule output are not implemented here. **Absolute diagnostic sample volume and its ruble total remain unknown.** Do not insert a guessed exporter count into the fixed subtotal or presume native points automatically appear in Prometheus.

Once the implementation supplies measured values-per-publication `s(t)`, compute writes as `N = sum(s(t))`; a constant one-series 30-second stream over 730 h produces 87,600 values. Enumerate histogram bucket/count/sum expansion, actual enabled kinds/outcomes, staged nodes and any written recording-rule output. Labels must not include build/boot/process/container IDs or product data.

For total account Prometheus writes `x` in millions, the current write tariff is:

```text
F(x) = 2.3058 × max(0, min(x, 10000) − 50)
     + 0.6588 × max(0, x − 10000)
increment = F(existing account writes + diagnostic writes) − F(existing account writes)
```

Do not assume the full 50-million free allowance is available. Remote Read and console/dashboard reading are free; native API reading is charged at 7.686 RUB/million up to 50 million, then 4.6116 RUB/million. Automatically collected cloud-resource metrics are free; custom diagnostic metrics are not automatically exempt. No separate metric-storage/workspace fee is listed in the retrieved Monitoring tariff. [Monitoring pricing](https://docs.yandex.cloud/ru/ru/monitoring/pricing.md), [shared allowance](https://yandex.cloud/ru/docs/billing/concepts/serverless-free-tier).

The four concurrent-worker limit does not bound historical instance-labelled cardinality: preemption, retirement and rollout create new series. Maintain an instance-churn/series ledger over retention. Managed Prometheus documents deletion after **60 days without new values**, ongoing series may remain indefinitely, and historical points older than one week are aggregated to five-minute intervals. This is a retention/cardinality consideration, not a separate per-series price invented by this estimate. [Managed Prometheus retention](https://docs.yandex.cloud/ru/ru/monitoring/operations/prometheus/index.md), [historical aggregation](https://docs.yandex.cloud/ru/ru/monitoring/concepts/decimation.md).

## Values still needed for a concrete activation budget

1. Exact image-builder shape, OS/licence, access method, maximum running/disk lifetime and actual billed image size; count retained images/snapshots.
2. Exact bootstrap/canonical-secret versions retained and bootstrap request volume under observed churn.
3. Existing payment-account internet-egress and Prometheus usage; expected new billable bytes and diagnostic samples, including label churn and recording rules.
4. A paid acceptance window and cleanup stop condition: initial 1+1 warmup, floor/scale-down proof, rollout within 2+2, builder/disk cleanup and no unplanned retained resources.

The root controller's fresh read-only quota inventory supplied on 2026-09-28 reports
SSD quota **200 GiB**, already allocated **100 GiB**. Initial 1+1 workers add 64 GiB,
reaching 164 GiB; an overlapping 32 GiB builder reaches 196 GiB. The approved 2+2 ceiling
adds 128 GiB, reaching **228 GiB: 28 GiB over the present quota**. An overlapping 32 GiB
builder at that ceiling would reach 260 GiB. These are allocation calculations from the
controller's inventory, not quota API calls performed by this researcher. The maximum-price
row is therefore a tariff scenario, not proof that the current cloud can provision it.
Quota resolution and builder overlap are activation prerequisites; no automatic quota change
or reduction of the approved shape/capacity is authorized by this note.

The research used only public vendor data and repository contracts. It performed no cloud writes, SSH, credential reads, account inspection or production deployment. Python/test execution is unnecessary for this documentation-only pricing note; arithmetic and current tariff fields were checked directly.

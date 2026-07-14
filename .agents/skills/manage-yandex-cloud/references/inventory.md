# FindMe Photo Yandex Cloud Inventory

## Verified local context

- CLI: Yandex Cloud CLI 1.14.0 for darwin/amd64
- Active profile observed on 2026-07-11: `default`
- Cloud ID: `b1gmcsmr51o5kvp86l55`
- Folder ID: `b1g2qttgfhb4gdunvlge`

## Environment mapping

| Environment | Resource mapping | Lifecycle |
| --- | --- | --- |
| Staging | Existing preemptible VM; stable VM, disk, subnet, security-group, and address IDs still require successful read-only discovery | Disposable application/data; automatic deploy from `main` |
| Production | Not provisioned | Separate non-preemptible VM after sizing evidence and pricing approval |

## Public endpoint observations

Observed through public DNS on 2026-07-13:

- `findme-photo.ru` A: `111.88.151.64`
- `www.findme-photo.ru` A: `111.88.151.64`
- no AAAA answer was observed for either name;
- authoritative nameservers: `ns3-l2.nic.ru`, `ns4-cloud.nic.ru`, `ns4-l2.nic.ru`,
  `ns8-cloud.nic.ru`, and `ns8-l2.nic.ru`.

These are public DNS observations, not proof that the address is statically allocated or attached to
a particular Yandex Cloud resource ID. The current VM remains preemptible staging and its deployed
edge was still HTTP-only when these facts were recorded. The HTTPS preparation work did not activate
TLS or change live server/cloud state.

## Discovery status

Read-only resource listing through the local profile timed out without returning resource data on
2026-07-11; discovery remained unresolved on 2026-07-13. Do not guess names or IDs from the
deployment workflow or public DNS. Retry safe inventory before a resource-specific operation and
update this reference only with reviewed stable identifiers.

Required inventory:

- compute instances and their preemptibility, platform, resources, zone, status, labels, and NIC IDs;
- attached/boot disks, disk types, sizes, deletion rules, and snapshot schedules;
- networks, subnets, route tables, security groups/rules, and reserved addresses;
- attached service accounts and relevant access bindings;
- current quotas and operations relevant to planned changes.

## Production sizing record

Before proposing production, create a dated report linked from the implementation plan. It must
contain traffic/latency, Gunicorn CPU and RSS, PostgreSQL growth/connections/IOPS, disk throughput and
latency, deployment timings, network volume, RTO/RPO and restore evidence, photo-processing workload,
two viable configurations, official cost estimates, and the selected headroom. The exact proposed
commands remain unapproved until the maintainer confirms them immediately before execution.

---
name: manage-yandex-cloud
description: Use when inspecting, operating, troubleshooting, or changing FindMe Photo resources in Yandex Cloud with the yc CLI, including VMs, disks, VPC, IAM, deployment infrastructure, costs, quotas, and billing-sensitive configuration.
---

# Manage FindMe Photo in Yandex Cloud

## Purpose

Operate only the Yandex Cloud resources in scope for this repository. Prefer read-only discovery,
make the target explicit, protect secrets, and require fresh human approval for cost, destruction,
access, or availability risk.

Read [references/inventory.md](references/inventory.md) before addressing live resources. Treat
installed `yc ... --help` and the official Yandex Cloud documentation as authoritative when syntax
or behavior differs from this skill.

## Safety gates

Classify every requested command before running it. If classification is uncertain, use the stricter
class.

### Read-only

Listing, getting, describing, reading status, quotas, operations, metrics, and public configuration
may proceed without another confirmation. First show the active profile name, cloud ID, folder ID,
and intended resource IDs. Redact credentials and secret payloads from output.

Use these safe selectors individually:

```bash
yc config profile list
yc config get cloud-id
yc config get folder-id
```

`yc config list` must not be run or pasted into logs because a configured service-account key or
credential can be included. Never print OAuth/IAM/API tokens, private keys, static access secret
keys, Lockbox payloads, SSH private keys, database passwords, or GitHub secrets.

### Operational mutation

Before a mutation that is not expected to change pricing, present:

1. active profile, explicit cloud/folder, and stable resource ID;
2. current state and intended state;
3. exact command;
4. availability, access, data, and deployment impact;
5. validation and rollback commands.

Request confirmation immediately before destructive, access-changing, or availability-affecting
commands, including stop, restart, metadata replacement, IAM binding changes, key rotation,
detach/move, restore, and delete. VM metadata updates can replace the existing metadata set; prefer
the narrow `add-metadata` or `remove-metadata` command where appropriate.

### Potential pricing mutation

Always stop and obtain **explicit manual confirmation** immediately before any command that might
start, increase, extend, reserve, or materially alter charges. Approval of a plan is not approval to execute
these commands.

This gate includes:

- creating, cloning, starting, or resizing a VM;
- changing platform, vCPU, RAM, core fraction, GPU, preemptibility, or instance group size;
- creating or expanding disks, snapshots, images, backups, retained logs, or object storage;
- reserving a public IP or creating NAT, load balancers, CDN, DNS, certificates, registries, managed
  databases, Kubernetes, serverless, monitoring, Lockbox, KMS, or other chargeable services;
- changing retention, paid support, billing accounts, budgets with automation, committed volume, or
  quotas intended to enable new spend;
- enabling a feature whose pricing effect is unknown.

The confirmation request must name the resource and folder, show old and new configuration, provide
the estimated monthly price delta from an official calculator/source or state that it is unknown,
and show every exact `yc` command. A general “continue” from an earlier turn is insufficient.

## Workflow

1. Read the inventory reference and relevant ADR/plan.
2. Confirm local CLI availability with `yc version`; use a named profile when more than one exists.
3. Select context with non-secret `yc config get` commands. Pass `--folder-id` explicitly to live
   resource commands.
4. Discover dependencies read-only in JSON. For current scope, inspect clouds/folders, instances,
   disks, networks, subnets, security groups, addresses, service accounts, and operations.
5. Resolve names to stable IDs. Do not infer environment from a public IP or VM name alone.
6. Classify each planned command and apply the gates above.
7. For an approved mutation, run the smallest command. Prefer `--async` for long operations and
   follow it with `yc operation get <operation-id>` until completion.
8. Verify the final state read-only and report actual IDs/status. If verification fails, stop before
   further mutation and present rollback or recovery options.
9. Propose an inventory reference update when stable resource mappings change. Never persist secret
   or ephemeral credential values.

## Command patterns

Prefer JSON and explicit scope:

```bash
yc compute instance list --folder-id <folder-id> --format json
yc compute instance get --id <instance-id> --folder-id <folder-id> --format json
yc compute disk list --folder-id <folder-id> --format json
yc vpc network list --folder-id <folder-id> --format json
yc vpc subnet list --folder-id <folder-id> --format json
yc vpc security-group list --folder-id <folder-id> --format json
yc vpc address list --folder-id <folder-id> --format json
yc iam service-account list --folder-id <folder-id> --format json
yc operation get <operation-id> --format json
```

Use labels on new resources only after pricing approval:

```text
project=findme-photo,env=staging|production,managed-by=yc-cli
```

Production resources use deletion protection where supported. Custom security groups default to
deny and must contain only explicitly required ingress/egress. Do not broaden SSH or application
ports to `0.0.0.0/0` without a separately explained and confirmed security exception.

## Project boundaries

- The current preemptible VM is staging.
- Production is a later separate non-preemptible VM; do not create it until ADR 0005's evidence and
  pricing gates are satisfied.
- GitHub Actions deploys containers. Do not use `yc` to bypass the staging/promotion workflow for an
  application release unless executing a documented recovery.
- Do not introduce managed databases, Kubernetes, load balancers, object storage, or other proposed
  services without an accepted ADR when required by `AGENTS.md`.

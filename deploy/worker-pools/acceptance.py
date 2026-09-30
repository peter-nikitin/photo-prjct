#!/usr/bin/env python3
"""Run the isolated functional fixture, or print the separate approved live acceptance gates."""

import argparse
import json
import subprocess
from pathlib import Path

COMMON_LIVE_GATES = (
    "distinct worker and canonical folders in reviewed cloud; effective direct and inherited "
    "manager/runtime grants and shared-VPC ownership read back",
    "worker group, VM, disk and image inventory in worker folder; native autoscaling and both "
    "publishers use canonical Monitoring folder with fresh metric samples",
    "approved identities, effective IAM, exact groups/subnet and canonical SG allow union",
    "private TLS success, public8443 denial and worker DNS/HTTPS egress",
    "actual candidate OCI revision/digest on web, launch template and both warm worker pools",
    "bounded initial bulk acceptance VM and one selfie job with packaged real models",
    "native bulk scale to zero and wake from recoverable demand; selfie floor one",
    "host grant reconciliation, guest poweroff and actual boot-disk deletion",
    "bounded local drain and no changes to historical attempts/artifacts/enrollment",
    "initial local recovery and later previous-compatible-build staged rollback",
    "production processing/selfie results and fresh native metrics, telemetry and alert delivery",
)
POLICY_LIVE_GATES = {
    1: (
        "native WORKLOAD policy bulk 0..1/selfie 1..1; backlog cannot allocate a second instance",
        "cap-one Git-owned alert apply/read-back/rollback, native Alarm/NoData/recovery "
        "and approved operator notification before customer cutover",
        "serial forward/rollback: only current pool temporarily maxSize=2, <=3 worker boot disks; "
        "restore maxSize=1 and prove obsolete disks absent before next pool expansion",
        "actual provider recovery/replacement survivor and physical disk allocation rehearsal",
    ),
    2: (
        "separately approved native WORKLOAD policy bulk 0..2/selfie 1..2 "
        "and second-instance demand",
        "two independent selfie searches before enabling a second claim slot",
        "native update/replacement total <=2 per pool and actual provider disk lifecycle",
    ),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--functional-fixture", action="store_true")
    parser.add_argument("--pool-max-size", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    if not args.functional_fixture:
        print(
            json.dumps(
                {
                    "live_verified": False,
                    "approval_required": True,
                    "pool_max_size": args.pool_max_size,
                    "scale_bounds": {
                        "bulk": [0, args.pool_max_size],
                        "selfie": [1, args.pool_max_size],
                    },
                    "gates": COMMON_LIVE_GATES + POLICY_LIVE_GATES[args.pool_max_size],
                }
            )
        )
        return 0
    root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        [
            "make",
            "test",
            "TESTS=-m operational tests/deployment/test_worker_pool_acceptance.py "
            "tests/deployment/test_worker_pool_provisioning.py "
            "tests/deployment/test_worker_pool_release.py "
            "tests/deployment/test_worker_pool_retire.py",
        ],
        cwd=root,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())

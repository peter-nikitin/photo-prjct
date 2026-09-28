#!/usr/bin/env python3
"""Run the isolated functional fixture, or print the separate approved live acceptance gates."""

import argparse
import json
import subprocess
from pathlib import Path

LIVE_GATES = (
    "approved identities, effective IAM, exact groups/subnet and canonical SG allow union",
    "private TLS success, public8443 denial and worker DNS/HTTPS egress",
    "actual candidate OCI revision/digest on web, launch template and both warm worker pools",
    "bounded initial bulk acceptance VM and one selfie job with packaged real models",
    "native bulk scale to zero and wake from recoverable demand; selfie floor one",
    "native update/replacement total <=2, host grant reconciliation and guest poweroff",
    "bounded local drain and no changes to historical attempts/artifacts/enrollment",
    "initial local recovery and later previous-compatible-build staged rollback",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--functional-fixture", action="store_true")
    args = parser.parse_args()
    if not args.functional_fixture:
        print(json.dumps({"live_verified": False, "approval_required": True, "gates": LIVE_GATES}))
        return 0
    root = Path(__file__).resolve().parents[2]
    return subprocess.run(
        ["make", "test", "TESTS=tests/deployment/test_worker_pool_acceptance.py -m operational"],
        cwd=root,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())

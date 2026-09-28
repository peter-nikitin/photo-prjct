"""Run local contract acceptance on an explicitly isolated test database.

This invokes Django's HTTP test client, not a network/Nginx deployment probe.
Existing integration fixtures cover both readers/sources, opt-in comparison,
immutable saved results, off rollback, publication and transient cleanup.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SELECTORS = (
    "tests/deployment/test_pgvector_acceptance.py",
    "src/backend/processing/tests/test_pgvector_callbacks.py",
    "src/backend/selfie_search/tests/test_read_selection.py",
    "src/backend/selfie_search/tests/test_vector_ranking.py",
    "src/backend/selfie_search/tests/test_reader_comparison.py",
    "src/backend/selfie_search/tests/test_submission.py",
    "src/backend/selfie_search/tests/test_jobs.py",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-port", type=int, required=True)
    args = parser.parse_args()
    if args.db_port != 25432:
        parser.error("local contract acceptance requires the task-owned test database on 25432")
    environment = os.environ | {"DB_HOST": "localhost", "DB_PORT": str(args.db_port)}
    return subprocess.run(
        ["sh", "scripts/run-in-test-env.sh", ".venv/bin/pytest", "-n", "0", *SELECTORS],
        cwd=ROOT,
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())

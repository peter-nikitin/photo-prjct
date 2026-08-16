from __future__ import annotations

import argparse
import json
from pathlib import Path

from .foreground import (
    build_foreground_review,
    derive_foreground_run,
    finalize_foreground_review,
    verify_foreground_run,
)
from .offline import run_offline, verify_run
from .review import build_review_from_run, finalize_run_review
from .snapshot import SnapshotRecord, export_snapshot, load_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Private offline selfie detector benchmark")
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--records-json", type=Path, required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    snapshot.add_argument("--expected-count", type=int, default=40)
    verify = commands.add_parser("verify-snapshot")
    verify.add_argument("--snapshot", type=Path, required=True)
    verify.add_argument("--expected-count", type=int, default=40)
    run = commands.add_parser("run")
    run.add_argument("--snapshot", type=Path, required=True)
    run.add_argument("--yunet-model", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--experiment-revision", required=True)
    verify_run_command = commands.add_parser("verify-run")
    verify_run_command.add_argument("--run", type=Path, required=True)
    review = commands.add_parser("build-review")
    review.add_argument("--run", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--run", type=Path, required=True)
    finalize.add_argument("--labels-csv", type=Path, required=True)
    finalize.add_argument("--output", type=Path, required=True)
    derive_foreground = commands.add_parser("derive-foreground")
    derive_foreground.add_argument("--source-run", type=Path, required=True)
    derive_foreground.add_argument("--output", type=Path, required=True)
    derive_foreground.add_argument("--experiment-revision", required=True)
    verify_foreground = commands.add_parser("verify-foreground")
    verify_foreground.add_argument("--run", type=Path, required=True)
    build_foreground = commands.add_parser("build-foreground-review")
    build_foreground.add_argument("--run", type=Path, required=True)
    build_foreground.add_argument("--output", type=Path, required=True)
    finalize_foreground = commands.add_parser("finalize-foreground")
    finalize_foreground.add_argument("--run", type=Path, required=True)
    finalize_foreground.add_argument("--labels-csv", type=Path, required=True)
    finalize_foreground.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "snapshot":
        export_snapshot(
            _records_from_json(args.records_json), args.output, expected_count=args.expected_count
        )
    elif args.command == "verify-snapshot":
        print(
            json.dumps(
                {
                    "verified_records": len(
                        load_snapshot(args.snapshot, expected_count=args.expected_count)
                    )
                }
            )
        )
    elif args.command == "run":
        rows = run_offline(
            args.snapshot,
            args.yunet_model,
            args.output,
            experiment_revision=args.experiment_revision,
        )
        print(json.dumps({"cases": len(rows) // 3, "variant_results": len(rows)}))
    elif args.command == "verify-run":
        print(json.dumps({"run_identity": verify_run(args.run)}))
    elif args.command == "build-review":
        build_review_from_run(args.run, args.output)
    elif args.command == "finalize":
        print(
            json.dumps(finalize_run_review(args.run, args.labels_csv, args.output), sort_keys=True)
        )
    elif args.command == "derive-foreground":
        rows = derive_foreground_run(
            args.source_run, args.output, experiment_revision=args.experiment_revision
        )
        print(json.dumps({"cases": len(rows), "variant": "normalized-1600-foreground"}))
    elif args.command == "verify-foreground":
        print(json.dumps({"run_identity": verify_foreground_run(args.run)}))
    elif args.command == "build-foreground-review":
        build_foreground_review(args.run, args.output)
    else:
        print(
            json.dumps(
                finalize_foreground_review(args.run, args.labels_csv, args.output), sort_keys=True
            )
        )
    return 0


def _records_from_json(path: Path) -> tuple[SnapshotRecord, ...]:
    """Read the operator's private, local export description; no network calls are supported."""
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, list):
            raise ValueError
        records = []
        for value in values:
            content_path = Path(value.pop("content_path"))
            records.append(SnapshotRecord(content=content_path.read_bytes(), **value))
        return tuple(records)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("private records JSON is invalid") from error

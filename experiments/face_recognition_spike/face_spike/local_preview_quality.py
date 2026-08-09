"""Command-line entry point for filesystem-only local preview profiling inputs."""
# ruff: noqa: E501

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from .preview_corpus import PreviewCorpusError, materialize_preview_corpus
from .preview_profile_comparison import ComparisonError, compare_preview_profiles

_PHOTO_ID = re.compile(r"^[0-9a-f]{32}$")


def _positive_workers(value: str) -> int:
    try:
        workers = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("workers must be a positive integer") from error
    if workers < 1:
        raise argparse.ArgumentTypeError("workers must be a positive integer")
    return workers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m face_spike.local_preview_quality")
    commands = parser.add_subparsers(dest="command", required=True)
    materialize = commands.add_parser("materialize")
    materialize.add_argument("--source-manifest", type=Path, required=True)
    materialize.add_argument("--originals", type=Path, required=True)
    materialize.add_argument("--output", type=Path, required=True)
    materialize.add_argument("--workers", type=_positive_workers, required=True)
    compare = commands.add_parser("compare")
    compare.add_argument("--preview-corpus", type=Path, required=True)
    compare.add_argument("--sample", type=Path, required=True)
    compare.add_argument("--yunet-model", type=Path, required=True)
    compare.add_argument("--sface-model", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--problem-photo-id", action="append", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command == "compare":
        ids = tuple(arguments.problem_photo_id)
        if (
            len(ids) != 7
            or len(set(ids)) != 7
            or not all(_PHOTO_ID.fullmatch(item) for item in ids)
        ):
            print(
                "compare requires exactly seven unique lowerhex problem photo IDs", file=sys.stderr
            )
            return 1
        try:
            result = compare_preview_profiles(
                arguments.preview_corpus,
                arguments.sample,
                arguments.yunet_model,
                arguments.sface_model,
                arguments.output,
                problem_photo_ids=ids,
            )
        except ComparisonError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(
            f"photos={result.photo_count} manifest_sha256={result.manifest_sha256} output={result.output}"
        )
        return 0
    try:
        manifest = materialize_preview_corpus(
            arguments.source_manifest,
            arguments.originals,
            arguments.output,
            workers=arguments.workers,
        )
    except PreviewCorpusError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        f"generated={manifest.generated} reused={manifest.reused} "
        f"photos={len(manifest.photos)} manifest_sha256={manifest.manifest_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Command-line entry point for filesystem-only local preview profiling inputs."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .preview_corpus import PreviewCorpusError, materialize_preview_corpus


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
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

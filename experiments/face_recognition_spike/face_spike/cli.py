from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RunConfig:
    photos: Path
    labels: Path
    yunet_model: Path
    sface_model: Path
    output: Path
    detection_threshold: float
    min_face_px: int
    top_k: int
    max_image_dimension: int
    max_image_pixels: int


class ExperimentResult(Protocol):
    unexpected_labelled_image_failures: int


def run_experiment(config: RunConfig) -> ExperimentResult:
    raise NotImplementedError("Experiment orchestration is implemented in a later task.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="face_spike")
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run")
    run.add_argument("--photos", type=Path, required=True)
    run.add_argument("--labels", type=Path, required=True)
    run.add_argument("--yunet-model", type=Path, required=True)
    run.add_argument("--sface-model", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--detection-threshold", type=float, default=0.75)
    run.add_argument("--min-face-px", type=int, default=32)
    run.add_argument("--top-k", type=int, default=10)
    run.add_argument("--max-image-dimension", type=int, default=12000)
    run.add_argument("--max-image-pixels", type=int, default=100000000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
        config = RunConfig(
            photos=arguments.photos,
            labels=arguments.labels,
            yunet_model=arguments.yunet_model,
            sface_model=arguments.sface_model,
            output=arguments.output,
            detection_threshold=arguments.detection_threshold,
            min_face_px=arguments.min_face_px,
            top_k=arguments.top_k,
            max_image_dimension=arguments.max_image_dimension,
            max_image_pixels=arguments.max_image_pixels,
        )
        if os.path.lexists(config.output):
            parser.error(f"output path already exists: {config.output}")
    except SystemExit as error:
        return int(error.code)

    result = run_experiment(config)
    return 3 if result.unexpected_labelled_image_failures else 0

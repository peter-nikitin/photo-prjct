from __future__ import annotations

import argparse
import math
import os
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image

from face_spike.dataset import load_image, load_labels
from face_spike.domain import DatasetError, DatasetItem, ErrorCode, ImageLimits, LoadedImage
from face_spike.opencv_models import SFaceRecognizer, YuNetDetector
from face_spike.pipeline import (
    FaceDetection,
    FaceDetector,
    FaceEmbedding,
    FaceRecognizer,
    ImageAnalysis,
)
from face_spike.retrieval import evaluate_retrieval

if TYPE_CHECKING:
    from face_spike.artifacts import ExperimentResult


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


class ExperimentConfigurationError(Exception):
    """A fatal input or model setup failure that must prevent output publication."""


def run_experiment(config: RunConfig) -> ExperimentResult:
    """Run one complete local experiment, recording recoverable image failures as evidence."""
    from face_spike.artifacts import ExperimentResult, write_run

    limits, items = _validate_inputs(config)
    detector, recognizer = _initialize_models(config)
    started_at = datetime.now(UTC)
    durations: dict[str, float] = {"decode": 0.0, "detection": 0.0, "embedding": 0.0}
    timed_detector = _TimedDetector(detector, durations)
    timed_recognizer = _TimedRecognizer(recognizer, durations)
    analyses = tuple(
        _analyze_item(
            item, config.photos, limits, timed_detector, timed_recognizer, config, durations
        )
        for item in items
    )
    unexpected_failures = sum(
        analysis.image.item.face_expected.value == "yes"
        and analysis.status not in {"ok", "no_detection"}
        for analysis in analyses
    )
    result = ExperimentResult(
        config=config,
        analyses=analyses,
        retrieval=evaluate_retrieval(analyses, config.top_k),
        started_at=started_at,
        finished_at=datetime.now(UTC),
        durations=durations,
        unexpected_labelled_image_failures=unexpected_failures,
        dependency_versions=_dependency_versions(),
    )
    write_run(config.output, result)
    return result


def _validate_inputs(config: RunConfig) -> tuple[ImageLimits, tuple[DatasetItem, ...]]:
    if os.path.lexists(config.output):
        raise ExperimentConfigurationError
    if not config.photos.is_dir() or not config.labels.is_file():
        raise ExperimentConfigurationError
    if not config.yunet_model.is_file() or not config.sface_model.is_file():
        raise ExperimentConfigurationError
    if (
        not math.isfinite(config.detection_threshold)
        or not 0.0 <= config.detection_threshold <= 1.0
        or config.min_face_px < 1
        or config.top_k < 0
    ):
        raise ExperimentConfigurationError
    try:
        limits = ImageLimits(config.max_image_dimension, config.max_image_pixels)
        return limits, load_labels(config.labels, config.photos)
    except (DatasetError, OSError, ValueError):
        raise ExperimentConfigurationError from None


def _initialize_models(config: RunConfig) -> tuple[FaceDetector, FaceRecognizer]:
    try:
        return (
            YuNetDetector(
                config.yunet_model,
                threshold=config.detection_threshold,
                min_face_px=config.min_face_px,
            ),
            SFaceRecognizer(config.sface_model),
        )
    except Exception:
        raise ExperimentConfigurationError from None


def _analyze_item(
    item: DatasetItem,
    photos: Path,
    limits: ImageLimits,
    detector: FaceDetector,
    recognizer: FaceRecognizer,
    config: RunConfig,
    durations: MutableMapping[str, float],
) -> ImageAnalysis:
    start = _perf_counter()
    try:
        image = load_image(item, photos, limits)
    except DatasetError as error:
        durations["decode"] += _perf_counter() - start
        return _failed_image_analysis(item, error.code)
    except Exception:
        durations["decode"] += _perf_counter() - start
        return _failed_image_analysis(item, "image_decode_failed")
    durations["decode"] += _perf_counter() - start

    from face_spike.pipeline import analyze_image

    return analyze_image(image, detector, recognizer, config.min_face_px)


def _failed_image_analysis(item: DatasetItem, status: ErrorCode) -> ImageAnalysis:
    pixels = np.zeros((1, 1, 3), dtype=np.uint8)
    image = LoadedImage(item=item, rgb=pixels, bgr=pixels.copy(), width=1, height=1)
    return ImageAnalysis(
        image=image,
        detections=(),
        primary_detection=None,
        embedding=None,
        status=status,
    )


class _TimedDetector:
    def __init__(self, detector: FaceDetector, durations: MutableMapping[str, float]) -> None:
        self._detector = detector
        self._durations = durations

    def detect(self, bgr: np.ndarray) -> tuple[FaceDetection, ...]:
        start = _perf_counter()
        try:
            return self._detector.detect(bgr)
        finally:
            self._durations["detection"] += _perf_counter() - start


class _TimedRecognizer:
    def __init__(self, recognizer: FaceRecognizer, durations: MutableMapping[str, float]) -> None:
        self._recognizer = recognizer
        self._durations = durations

    def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
        start = _perf_counter()
        try:
            return self._recognizer.extract(bgr, detection)
        finally:
            self._durations["embedding"] += _perf_counter() - start


def _perf_counter() -> float:
    from time import perf_counter

    return perf_counter()


def _dependency_versions() -> dict[str, str]:
    return {
        "numpy": np.__version__,
        "opencv": cv2.__version__,
        "pillow": Image.__version__,
    }


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

    try:
        result = run_experiment(config)
    except ExperimentConfigurationError:
        return 2
    return 3 if result.unexpected_labelled_image_failures else 0

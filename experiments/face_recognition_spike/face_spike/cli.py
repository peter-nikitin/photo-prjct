from __future__ import annotations

import argparse
import math
import os
import sys
from collections.abc import Callable, MutableMapping, Sequence
from contextvars import ContextVar, Token
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


_ACTIVE_LOG: ContextVar[Callable[[str], None] | None] = ContextVar("active_log", default=None)


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
    progress = _RunProgress(_ACTIVE_LOG.get())
    detector, recognizer = _initialize_models(config, progress)
    started_at = datetime.now(UTC)
    progress.start_processing(len(items))
    durations: dict[str, float] = {"decode": 0.0, "detection": 0.0, "embedding": 0.0}
    timed_detector = _TimedDetector(detector, durations)
    timed_recognizer = _TimedRecognizer(recognizer, durations)
    analyses: list[ImageAnalysis] = []
    for index, item in enumerate(items, start=1):
        analysis = _analyze_item(
            item, config.photos, limits, timed_detector, timed_recognizer, config, durations
        )
        analyses.append(analysis)
        progress.item_processed(index, len(items), analysis)
    completed_analyses = tuple(analyses)
    unexpected_failures = sum(
        analysis.image.item.face_expected.value == "yes"
        and analysis.status not in {"ok", "no_detection"}
        for analysis in completed_analyses
    )
    result = ExperimentResult(
        config=config,
        analyses=completed_analyses,
        retrieval=evaluate_retrieval(completed_analyses, config.top_k),
        started_at=started_at,
        finished_at=datetime.now(UTC),
        durations=durations,
        unexpected_labelled_image_failures=unexpected_failures,
        dependency_versions=_dependency_versions(),
    )
    progress.writing_artifacts()
    write_run(config.output, result)
    progress.completed(config.output)
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


def _initialize_models(
    config: RunConfig, progress: _RunProgress
) -> tuple[FaceDetector, FaceRecognizer]:
    try:
        progress.initializing_yunet()
        detector = YuNetDetector(
            config.yunet_model,
            threshold=config.detection_threshold,
            min_face_px=config.min_face_px,
        )
        progress.initializing_sface()
        recognizer = SFaceRecognizer(config.sface_model)
        return detector, recognizer
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


class _RunProgress:
    """Compact human-readable console status for an interactive CLI run."""

    def __init__(self, log: Callable[[str], None] | None) -> None:
        self._log = log
        self._started_at: float | None = None
        self._ok = 0
        self._no_face = 0
        self._errors = 0

    def initializing_yunet(self) -> None:
        self._emit("Initializing YuNet...")

    def initializing_sface(self) -> None:
        self._emit("Initializing SFace...")

    def start_processing(self, total: int) -> None:
        self._started_at = _monotonic()
        self._emit(f"Processing {total} images...")

    def item_processed(self, current: int, total: int, analysis: ImageAnalysis) -> None:
        status = analysis.status
        if status == "ok":
            self._ok += 1
        elif status == "no_detection":
            self._no_face += 1
        else:
            self._errors += 1
            self._emit(f"[ERROR] {analysis.image.item.filename} status={status}")

        if current % 10 == 0 or current == total:
            elapsed = _monotonic() - self._started_at if self._started_at is not None else 0.0
            eta = elapsed * (total - current) / current if current else 0.0
            percentage = 100 * current / total if total else 100.0
            self._emit(
                f"[{current}/{total}] {percentage:.1f}% "
                f"elapsed={_format_duration(elapsed)} eta={_format_duration(eta)} "
                f"ok={self._ok} no_face={self._no_face} errors={self._errors}"
            )

    def writing_artifacts(self) -> None:
        self._emit("Writing artifacts...")

    def completed(self, output: Path) -> None:
        self._emit(
            f"Completed: output={output} ok={self._ok} "
            f"no_face={self._no_face} errors={self._errors}"
        )

    def _emit(self, message: str) -> None:
        if self._log is not None:
            self._log(message)


def _monotonic() -> float:
    from time import monotonic

    return monotonic()


def _format_duration(seconds: float) -> str:
    rounded_seconds = max(0, round(seconds))
    minutes, seconds = divmod(rounded_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{seconds:02d}s"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


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

    token: Token[Callable[[str], None] | None] = _ACTIVE_LOG.set(
        lambda message: print(message, file=sys.stderr, flush=True)
    )
    try:
        result = run_experiment(config)
    except ExperimentConfigurationError:
        return 2
    finally:
        _ACTIVE_LOG.reset(token)
    return 3 if result.unexpected_labelled_image_failures else 0

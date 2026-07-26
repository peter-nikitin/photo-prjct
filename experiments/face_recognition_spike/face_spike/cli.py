from __future__ import annotations

import argparse
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any

from .comparison import ComparisonConfig, ComparisonError, run_comparison

if TYPE_CHECKING:
    from .cluster_artifacts import ClusterRunResult
    from .inventory import EventPhotoInventory


YuNetDetector: Any | None = None
SFaceRecognizer: Any | None = None


def run_review(config: Any) -> Any:
    """Load the review builder only for the local review command."""
    from .review import run_review as build_review

    return build_review(config)


@dataclass(frozen=True)
class ClusterConfig:
    photos: Path
    yunet_model: Path
    sface_model: Path
    output: Path
    detection_threshold: float = 0.75
    min_face_px: int = 32
    cluster_threshold: float = 0.363
    representative_threshold: float = 0.363
    distance_block_size: int = 512
    image_limit: int | None = None
    max_image_dimension: int = 12000
    max_image_pixels: int = 100_000_000

    def validate(self) -> None:
        if (
            not math.isfinite(self.detection_threshold)
            or not 0.0 <= self.detection_threshold <= 1.0
            or not math.isfinite(self.cluster_threshold)
            or not 0.0 <= self.cluster_threshold <= 2.0
            or not math.isfinite(self.representative_threshold)
            or not 0.0 <= self.representative_threshold <= 2.0
            or self.min_face_px < 1
            or self.distance_block_size < 1
            or (self.image_limit is not None and self.image_limit < 1)
            or self.max_image_dimension < 1
            or self.max_image_pixels < 1
        ):
            raise ClusterConfigurationError("invalid cluster configuration")


class ClusterConfigurationError(Exception):
    """A fatal setup error that prevents publication."""


def run_cluster(config: ClusterConfig) -> ClusterRunResult:
    from .analysis import analyze_event_photo_inventory
    from .cluster_artifacts import (
        ClusterArtifactWriter,
        ClusterRunResult,
        abort_preserving_exception,
    )
    from .clustering import cluster_successful_faces
    from .image_decoder import ImageLimits, PillowImageDecoder
    from .inventory import InventoryError, load_event_photo_inventory

    config.validate()
    if os.path.lexists(config.output):
        raise ClusterConfigurationError("output path already exists")
    if not config.yunet_model.is_file() or not config.sface_model.is_file():
        raise ClusterConfigurationError("model file does not exist")
    try:
        inventory = _limited_inventory(
            load_event_photo_inventory(config.photos), config.image_limit
        )
    except (InventoryError, OSError, ValueError):
        raise ClusterConfigurationError("invalid photo inventory") from None
    try:
        decoder = PillowImageDecoder(
            ImageLimits(config.max_image_dimension, config.max_image_pixels)
        )
        detector_type = YuNetDetector
        recognizer_type = SFaceRecognizer
        if detector_type is None or recognizer_type is None:
            from .models import SFaceRecognizer as loaded_recognizer
            from .models import YuNetDetector as loaded_detector

            detector_type = loaded_detector
            recognizer_type = loaded_recognizer
        detector = detector_type(
            config.yunet_model,
            threshold=config.detection_threshold,
        )
        recognizer = recognizer_type(config.sface_model)
    except Exception:
        raise ClusterConfigurationError("model initialization failed") from None

    writer = ClusterArtifactWriter(config.output, config.photos)
    started_at = datetime.now(UTC)
    processing_start = perf_counter()
    try:
        analyses = analyze_event_photo_inventory(
            inventory,
            decoder,
            detector,
            recognizer,
            min_face_px=config.min_face_px,
            write_diagnostics=writer.write_diagnostics,
        )
        processing_seconds = perf_counter() - processing_start
        clustering_start = perf_counter()
        clusters = cluster_successful_faces(
            analyses,
            cluster_threshold=config.cluster_threshold,
            representative_threshold=config.representative_threshold,
            distance_block_size=config.distance_block_size,
        )
        clustering_seconds = perf_counter() - clustering_start
        result = ClusterRunResult(
            photos=config.photos,
            yunet_model=config.yunet_model,
            sface_model=config.sface_model,
            parameters={
                "cluster_threshold": config.cluster_threshold,
                "detection_threshold": config.detection_threshold,
                "distance_block_size": config.distance_block_size,
                "image_limit": config.image_limit,
                "max_image_dimension": config.max_image_dimension,
                "max_image_pixels": config.max_image_pixels,
                "min_face_px": config.min_face_px,
                "representative_threshold": config.representative_threshold,
            },
            analyses=analyses,
            clusters=clusters,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            durations={
                "clustering": clustering_seconds,
                "decode_detection_embedding": processing_seconds,
            },
            dependency_versions={
                "numpy": _dependency_version("numpy"),
                "opencv": _dependency_version("cv2"),
                "pillow": _dependency_version("PIL.Image"),
            },
        )
        writer.finish(result)
        return result
    except BaseException as failure:
        abort_preserving_exception(writer, failure)
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="face_spike")
    commands = parser.add_subparsers(dest="command", required=True)
    cluster = commands.add_parser("cluster")
    cluster.add_argument("--photos", type=Path, required=True)
    cluster.add_argument("--yunet-model", type=Path, required=True)
    cluster.add_argument("--sface-model", type=Path, required=True)
    cluster.add_argument("--output", type=Path, required=True)
    cluster.add_argument("--detection-threshold", type=float, default=0.75)
    cluster.add_argument("--min-face-px", type=int, default=32)
    cluster.add_argument("--cluster-threshold", type=float, default=0.363)
    cluster.add_argument("--representative-threshold", type=float, default=0.363)
    cluster.add_argument("--distance-block-size", type=int, default=512)
    cluster.add_argument("--image-limit", type=int)
    cluster.add_argument("--max-image-dimension", type=int, default=12000)
    cluster.add_argument("--max-image-pixels", type=int, default=100_000_000)
    compare = commands.add_parser("compare")
    compare.add_argument("--run", type=Path, required=True)
    compare.add_argument("--peakshot-export", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    review = commands.add_parser("review")
    review.add_argument("--run", type=Path, required=True)
    review.add_argument("--comparison", type=Path, required=True)
    review.add_argument("--peakshot-export", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
        if arguments.command == "compare":
            comparison_config = ComparisonConfig(
                run=arguments.run,
                peakshot_export=arguments.peakshot_export,
                output=arguments.output,
            )
            comparison_config.validate()
        elif arguments.command == "review":
            from .review import ReviewConfig

            review_config = ReviewConfig(
                run=arguments.run,
                comparison=arguments.comparison,
                peakshot_export=arguments.peakshot_export,
                output=arguments.output,
            )
            review_config.validate()
        else:
            config = ClusterConfig(
                photos=arguments.photos,
                yunet_model=arguments.yunet_model,
                sface_model=arguments.sface_model,
                output=arguments.output,
                detection_threshold=arguments.detection_threshold,
                min_face_px=arguments.min_face_px,
                cluster_threshold=arguments.cluster_threshold,
                representative_threshold=arguments.representative_threshold,
                distance_block_size=arguments.distance_block_size,
                image_limit=arguments.image_limit,
                max_image_dimension=arguments.max_image_dimension,
                max_image_pixels=arguments.max_image_pixels,
            )
            config.validate()
            if os.path.lexists(config.output):
                parser.error(f"output path already exists: {config.output}")
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2
    except (ClusterConfigurationError, ComparisonError, ValueError):
        return 2

    try:
        if arguments.command == "compare":
            run_comparison(comparison_config)
        elif arguments.command == "review":
            run_review(review_config)
        else:
            run_cluster(config)
    except (ClusterConfigurationError, ComparisonError, FileExistsError, OSError, ValueError):
        return 2
    return 0


def _limited_inventory(
    inventory: EventPhotoInventory, image_limit: int | None
) -> EventPhotoInventory:
    if image_limit is None:
        return inventory
    from .inventory import EventPhotoInventory

    return EventPhotoInventory(inventory.photos[:image_limit])


def _dependency_version(module_name: str) -> str:
    from importlib import import_module

    module = import_module(module_name)
    return str(module.__version__)

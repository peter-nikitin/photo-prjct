from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import tempfile
import tracemalloc
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Any, TypedDict

import numpy as np

from .comparison import ComparisonConfig, ComparisonError, run_comparison

if TYPE_CHECKING:
    from .cluster_artifacts import ClusterRunResult
    from .inventory import EventPhotoInventory


YuNetDetector: Any | None = None
SFaceRecognizer: Any | None = None
_IMAGE_STATUSES = frozenset(
    {
        "ok",
        "no_detection",
        "image_decode_failed",
        "unsupported_image",
        "image_too_large",
        "detection_failed",
    }
)
_FACE_STATUSES = frozenset(
    {
        "ok",
        "quality_rejected",
        "alignment_failed",
        "embedding_failed",
        "invalid_embedding",
    }
)


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
    max_candidate_edges: int = 100_000
    image_limit: int | None = None
    max_image_dimension: int = 12000
    max_image_pixels: int = 100_000_000
    severe_blur_threshold: float = 25.0
    borderline_blur_threshold: float = 50.0
    minimum_relative_area: float = 0.0009
    minimum_confidence: float = 0.82

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
            or self.max_candidate_edges < 1
            or (self.image_limit is not None and self.image_limit < 1)
            or self.max_image_dimension < 1
            or self.max_image_pixels < 1
            or not math.isfinite(self.minimum_confidence)
            or not 0 <= self.minimum_confidence <= 1
            or not math.isfinite(self.minimum_relative_area)
            or not 0 <= self.minimum_relative_area <= 1
            or not math.isfinite(self.severe_blur_threshold)
            or not math.isfinite(self.borderline_blur_threshold)
            or self.severe_blur_threshold < 0
            or self.severe_blur_threshold >= self.borderline_blur_threshold
        ):
            raise ClusterConfigurationError("invalid cluster configuration")


class ClusterConfigurationError(Exception):
    """A fatal setup error that prevents publication."""


@dataclass(frozen=True)
class BuildIndexConfig:
    run: Path
    photos: Path
    yunet_model: Path
    sface_model: Path
    output: Path


class BuildIndexConfigurationError(Exception):
    """A fatal setup error that prevents index publication."""


@dataclass(frozen=True)
class BuildBenchmarkConfig:
    run: Path
    index: Path
    photos: Path
    output: Path
    query_count: int = 30


@dataclass(frozen=True)
class FinalizeBenchmarkConfig:
    proposal: Path
    annotations_csv: Path
    output: Path


@dataclass(frozen=True)
class EvaluateClusterExpansionConfig:
    benchmark: Path
    index: Path
    cluster_run: Path
    output: Path
    direct_threshold: float
    anchor_threshold: float
    configuration_hash: str
    generations_json: Path


class BenchmarkConfigurationError(Exception):
    """A fatal benchmark setup error that prevents publication."""


@dataclass(frozen=True)
class _StrictClusterRunArtifact:
    """One parsed, schema-validated cluster corpus for benchmark consumers."""

    benchmark_run: Any
    source_faces: Mapping[str, _BenchmarkSourceFace]
    source_manifest: Mapping[str, object]
    clusters: tuple[Any, ...]
    corpus_build_duration_ms: int
    corpus_build_peak_memory_bytes: int
    corpus_evidence_sha256: str


@dataclass(frozen=True)
class SmokeSearchConfig:
    proposal: Path
    index: Path
    run: Path
    photos: Path
    yunet_model: Path
    sface_model: Path
    output: Path
    query_count: int = 5
    limit: int = 10


class SmokeSearchConfigurationError(Exception):
    """A fatal smoke-search setup error that prevents publication."""


@dataclass(frozen=True)
class CompareQualityConfig:
    baseline_run: Path
    candidate_run: Path
    output: Path
    minimum_face_px: int = 32
    severe_blur_threshold: float = 25.0
    borderline_blur_threshold: float = 50.0
    minimum_relative_area: float = 0.0009
    minimum_confidence: float = 0.82


@dataclass(frozen=True)
class FinalizeQualityReviewConfig:
    comparison: Path
    labels_csv: Path
    search_comparison: Path
    baseline_run: Path
    candidate_run: Path
    benchmark: Path
    baseline_index: Path
    candidate_index: Path
    run: Path
    yunet_model: Path
    sface_model: Path
    reviewer: str
    reviewed_at: str
    output: Path


@dataclass(frozen=True)
class CompareSearchConfig:
    benchmark: Path
    baseline_index: Path
    candidate_index: Path
    run: Path
    yunet_model: Path
    sface_model: Path
    quality_comparison: Path
    output: Path


class QualityComparisonConfigurationError(Exception):
    """Private quality comparison evidence is incomplete or incompatible."""


@dataclass(frozen=True)
class _BenchmarkSourceFace:
    face_id: str
    filename: str
    face_index: int
    crop_path: str
    x: float
    y: float
    width: float
    height: float
    confidence: float
    minimum_side_px: float
    relative_area: float
    sharpness: float


class _IndexParameters(TypedDict):
    detection_threshold: float
    image_limit: int | None
    max_image_dimension: int
    max_image_pixels: int
    min_face_px: int
    quality_algorithm_version: str
    quality_crop_size: int
    severe_blur_threshold: float
    borderline_blur_threshold: float
    minimum_confidence: float
    minimum_relative_area: float


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
    from .quality import FaceQualityThresholds

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
    tracing_was_active = tracemalloc.is_tracing()
    if not tracing_was_active:
        tracemalloc.start()
    try:
        analyses = analyze_event_photo_inventory(
            inventory,
            decoder,
            detector,
            recognizer,
            min_face_px=config.min_face_px,
            quality_thresholds=FaceQualityThresholds(
                algorithm_version="normalized-laplacian-v1",
                crop_size=112,
                minimum_face_px=config.min_face_px,
                severe_blur_threshold=config.severe_blur_threshold,
                borderline_blur_threshold=config.borderline_blur_threshold,
                minimum_relative_area=config.minimum_relative_area,
                minimum_confidence=config.minimum_confidence,
            ),
            write_diagnostics=writer.write_diagnostics,
        )
        processing_seconds = perf_counter() - processing_start
        clustering_start = perf_counter()
        clusters = cluster_successful_faces(
            analyses,
            cluster_threshold=config.cluster_threshold,
            representative_threshold=config.representative_threshold,
            distance_block_size=config.distance_block_size,
            max_candidate_edges=config.max_candidate_edges,
        )
        clustering_seconds = perf_counter() - clustering_start
        _current, peak_memory_bytes = tracemalloc.get_traced_memory()
        result = ClusterRunResult(
            photos=config.photos,
            yunet_model=config.yunet_model,
            sface_model=config.sface_model,
            parameters={
                "cluster_threshold": config.cluster_threshold,
                "detection_threshold": config.detection_threshold,
                "distance_block_size": config.distance_block_size,
                "max_candidate_edges": config.max_candidate_edges,
                "image_limit": config.image_limit,
                "max_image_dimension": config.max_image_dimension,
                "max_image_pixels": config.max_image_pixels,
                "min_face_px": config.min_face_px,
                "quality_algorithm_version": "normalized-laplacian-v1",
                "quality_crop_size": 112,
                "severe_blur_threshold": config.severe_blur_threshold,
                "borderline_blur_threshold": config.borderline_blur_threshold,
                "minimum_confidence": config.minimum_confidence,
                "minimum_relative_area": config.minimum_relative_area,
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
            peak_memory_bytes=peak_memory_bytes,
        )
        writer.finish(result)
        return result
    except BaseException as failure:
        abort_preserving_exception(writer, failure)
        raise
    finally:
        if not tracing_was_active:
            tracemalloc.stop()


def run_build_index(config: BuildIndexConfig) -> None:
    """Rebuild a private face index from one immutable cluster run."""
    from .analysis import BoundingBox, face_crop_path
    from .image_decoder import ImageLimits, PillowImageDecoder
    from .index import SourceFaceRecord, build_face_index
    from .index_artifacts import FaceIndexArtifactWriter, FaceIndexManifest
    from .inventory import InventoryError, load_event_photo_inventory
    from .quality import FaceQualityThresholds

    try:
        manifest_bytes, faces_bytes, source_manifest, source_faces = _load_index_source_run(
            config.run, BoundingBox, SourceFaceRecord, face_crop_path
        )
        parameters = _index_parameters(source_manifest)
        _validate_model_compatibility(source_manifest, config.yunet_model, config.sface_model)
        if os.path.lexists(config.output):
            raise BuildIndexConfigurationError("output path already exists")
        try:
            inventory = _limited_inventory(
                load_event_photo_inventory(config.photos), parameters["image_limit"]
            )
        except (InventoryError, OSError, ValueError):
            raise BuildIndexConfigurationError("invalid photo inventory") from None
        decoder = PillowImageDecoder(
            ImageLimits(parameters["max_image_dimension"], parameters["max_image_pixels"])
        )
        detector_type = YuNetDetector
        recognizer_type = SFaceRecognizer
        if detector_type is None or recognizer_type is None:
            from .models import SFaceRecognizer as loaded_recognizer
            from .models import YuNetDetector as loaded_detector

            detector_type = loaded_detector
            recognizer_type = loaded_recognizer
        detector = detector_type(config.yunet_model, threshold=parameters["detection_threshold"])
        recognizer = recognizer_type(config.sface_model)
        index_manifest = FaceIndexManifest(
            source_run_manifest_sha256=_sha256_bytes(manifest_bytes),
            source_faces_sha256=_sha256_bytes(faces_bytes),
            yunet_model=_model_metadata(config.yunet_model),
            sface_model=_model_metadata(config.sface_model),
            parameters=dict(parameters),
            dependency_versions={
                "numpy": _dependency_version("numpy"),
                "opencv": _dependency_version("cv2"),
                "pillow": _dependency_version("PIL.Image"),
            },
            entry_count=0,
            embedding_dimension=0,
            created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        )
        index = build_face_index(
            source_faces,
            inventory,
            decoder,
            detector,
            recognizer,
            quality_thresholds=FaceQualityThresholds(
                algorithm_version="normalized-laplacian-v1",
                crop_size=112,
                minimum_face_px=parameters["min_face_px"],
                severe_blur_threshold=parameters["severe_blur_threshold"],
                borderline_blur_threshold=parameters["borderline_blur_threshold"],
                minimum_relative_area=parameters["minimum_relative_area"],
                minimum_confidence=parameters["minimum_confidence"],
            ),
            manifest=index_manifest,
        )
        writer = FaceIndexArtifactWriter(config.output)
        try:
            writer.finish(index)
        finally:
            if not os.path.lexists(config.output):
                writer.abort()
    except BuildIndexConfigurationError:
        raise
    except (OSError, TypeError, ValueError, KeyError):
        raise BuildIndexConfigurationError("invalid source run") from None


def _load_index_source_run(
    run: Path, bounding_box_type: Any, source_face_type: Any, face_crop_path_factory: Any
) -> tuple[bytes, bytes, Mapping[str, object], tuple[Any, ...]]:
    if not run.is_dir():
        raise BuildIndexConfigurationError("source run does not exist")
    manifest_bytes = _read_json_bytes(run / "manifest.json")
    faces_bytes = _read_json_bytes(run / "faces.json")
    manifest = _json_object(manifest_bytes)
    faces = _json_object(faces_bytes)
    _validate_completed_run_manifest(manifest)
    _validate_run_source_identity(manifest, faces)
    return (
        manifest_bytes,
        faces_bytes,
        manifest,
        _source_face_records(faces, bounding_box_type, source_face_type, face_crop_path_factory),
    )


def _read_json_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        raise BuildIndexConfigurationError("source run is incomplete") from None


def _json_object(payload: bytes) -> Mapping[str, object]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BuildIndexConfigurationError("source run JSON is invalid") from None
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise BuildIndexConfigurationError("source run schema is invalid")
    return value


def _validate_completed_run_manifest(manifest: Mapping[str, object]) -> None:
    expected = {
        "counts",
        "dependency_versions",
        "duration_seconds",
        "durations_seconds",
        "finished_at",
        "model_hashes",
        "parameters",
        "photo_materialization",
        "peak_memory_bytes",
        "platform",
        "python_version",
        "source",
        "started_at",
    }
    if set(manifest) != expected:
        raise BuildIndexConfigurationError("source run manifest schema is incompatible")
    if not isinstance(manifest["peak_memory_bytes"], int) or manifest["peak_memory_bytes"] < 1:
        raise BuildIndexConfigurationError("source run peak memory is invalid")
    _index_parameters(manifest)
    model_hashes = _mapping(manifest["model_hashes"])
    if set(model_hashes) != {"sface", "yunet"} or any(
        not _is_sha256(value) for value in model_hashes.values()
    ):
        raise BuildIndexConfigurationError("source run model hashes are invalid")
    if not isinstance(manifest["dependency_versions"], Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in manifest["dependency_versions"].items()
    ):
        raise BuildIndexConfigurationError("source run manifest schema is incompatible")


def _validate_run_source_identity(
    manifest: Mapping[str, object], faces: Mapping[str, object]
) -> None:
    source = _mapping(manifest["source"])
    if set(source) != {
        "faces_sha256",
        "generation_sha256",
        "inventory_sha256",
        "media_sha256",
    }:
        raise BuildIndexConfigurationError("source run identity is incompatible")
    images = faces.get("images")
    if not isinstance(images, list):
        raise BuildIndexConfigurationError("source run identity is invalid")
    filenames = [_safe_filename(_mapping(image).get("filename")) for image in images]
    media = source["media_sha256"]
    if not isinstance(media, list):
        raise BuildIndexConfigurationError("source run identity is invalid")
    media_names: list[str] = []
    for value in media:
        item = _mapping(value)
        if set(item) != {"filename", "sha256"} or not _is_sha256(item["sha256"]):
            raise BuildIndexConfigurationError("source run identity is invalid")
        media_names.append(_safe_filename(item["filename"]))
    parameters = _mapping(manifest["parameters"])
    generation_parameters = {
        key: value
        for key, value in parameters.items()
        if key
        not in {
            "input_photos_basename",
            "sface_model_filename",
            "yunet_model_filename",
        }
    }
    generation = {
        "model_hashes": dict(_mapping(manifest["model_hashes"])),
        "parameters": generation_parameters,
    }
    if (
        filenames != sorted(filenames)
        or media_names != filenames
        or source["faces_sha256"] != _sha256_bytes(_canonical_json(faces))
        or source["inventory_sha256"] != _sha256_bytes(_canonical_json(filenames))
        or source["generation_sha256"] != _sha256_bytes(_canonical_json(generation))
    ):
        raise BuildIndexConfigurationError("source run identity is invalid")


def _index_parameters(manifest: Mapping[str, object]) -> _IndexParameters:
    source = _mapping(manifest["parameters"])
    expected = {
        "cluster_threshold",
        "detection_threshold",
        "distance_block_size",
        "max_candidate_edges",
        "image_limit",
        "input_photos_basename",
        "max_image_dimension",
        "max_image_pixels",
        "min_face_px",
        "quality_algorithm_version",
        "quality_crop_size",
        "severe_blur_threshold",
        "borderline_blur_threshold",
        "minimum_confidence",
        "minimum_relative_area",
        "representative_threshold",
        "sface_model_filename",
        "yunet_model_filename",
    }
    if set(source) != expected:
        raise BuildIndexConfigurationError("source run parameters are incompatible")
    values: _IndexParameters = {
        "detection_threshold": _finite_number(source["detection_threshold"]),
        "image_limit": _optional_positive_integer(source["image_limit"]),
        "max_image_dimension": _positive_integer(source["max_image_dimension"]),
        "max_image_pixels": _positive_integer(source["max_image_pixels"]),
        "min_face_px": _positive_integer(source["min_face_px"]),
        "quality_algorithm_version": _string(source["quality_algorithm_version"]),
        "quality_crop_size": _positive_integer(source["quality_crop_size"]),
        "severe_blur_threshold": _finite_number(source["severe_blur_threshold"]),
        "borderline_blur_threshold": _finite_number(source["borderline_blur_threshold"]),
        "minimum_confidence": _finite_number(source["minimum_confidence"]),
        "minimum_relative_area": _finite_number(source["minimum_relative_area"]),
    }
    if (
        not 0 <= values["detection_threshold"] <= 1
        or values["quality_algorithm_version"] != "normalized-laplacian-v1"
        or values["quality_crop_size"] != 112
        or not 0 <= values["minimum_confidence"] <= 1
        or not 0 <= values["minimum_relative_area"] <= 1
        or values["severe_blur_threshold"] < 0
        or values["severe_blur_threshold"] >= values["borderline_blur_threshold"]
    ):
        raise BuildIndexConfigurationError("source run parameters are invalid")
    return values


def _validate_model_compatibility(
    manifest: Mapping[str, object], yunet_model: Path, sface_model: Path
) -> None:
    model_hashes = _mapping(manifest["model_hashes"])
    for path, model_name in ((yunet_model, "yunet"), (sface_model, "sface")):
        if not path.is_file():
            raise BuildIndexConfigurationError("model file does not exist")
        if _sha256_file(path) != model_hashes[model_name]:
            raise BuildIndexConfigurationError("model file does not match source run")


def _source_face_records(
    payload: Mapping[str, object],
    bounding_box_type: Any,
    source_face_type: Any,
    face_crop_path_factory: Any,
) -> tuple[Any, ...]:
    if set(payload) != {"images"} or not isinstance(payload["images"], list):
        raise BuildIndexConfigurationError("source faces schema is incompatible")
    records: list[Any] = []
    filenames: set[str] = set()
    for image in payload["images"]:
        image_mapping = _mapping(image)
        if set(image_mapping) != {"faces", "filename", "height", "status", "width"}:
            raise BuildIndexConfigurationError("source faces schema is incompatible")
        filename = _safe_filename(image_mapping["filename"])
        status = image_mapping["status"]
        dimensions_are_valid = (
            _nonnegative_integer(image_mapping["height"])
            and _nonnegative_integer(image_mapping["width"])
            if status in {"image_decode_failed", "unsupported_image", "image_too_large"}
            else _positive_integer(image_mapping["height"])
            and _positive_integer(image_mapping["width"])
        )
        if (
            filename in filenames
            or status not in _IMAGE_STATUSES
            or not dimensions_are_valid
            or not isinstance(image_mapping["faces"], list)
        ):
            raise BuildIndexConfigurationError("source faces schema is invalid")
        filenames.add(filename)
        image_records: list[Any] = []
        for face in image_mapping["faces"]:
            record = _source_face_record(
                face, filename, bounding_box_type, source_face_type, face_crop_path_factory
            )
            image_records.append(record)
            records.append(record)
        if [record.face_index for record in image_records] != list(
            range(1, len(image_records) + 1)
        ):
            raise BuildIndexConfigurationError("source face schema is invalid")
    return tuple(records)


def _source_face_record(
    value: object,
    image_filename: str,
    bounding_box_type: Any,
    source_face_type: Any,
    face_crop_path_factory: Any,
) -> Any:
    face = _mapping(value)
    expected = {
        "confidence",
        "crop_path",
        "error_code",
        "face_id",
        "face_index",
        "filename",
        "height",
        "landmarks",
        "quality",
        "status",
        "width",
        "x",
        "y",
    }
    if set(face) != expected or _safe_filename(face["filename"]) != image_filename:
        raise BuildIndexConfigurationError("source face schema is incompatible")
    face_index = _positive_integer(face["face_index"])
    face_id = face["face_id"]
    crop_path = face["crop_path"]
    if (
        not isinstance(face_id, str)
        or face_id != f"{image_filename}#face-{face_index:03d}"
        or crop_path != face_crop_path_factory(face_id)
        or face["status"] not in _FACE_STATUSES
        or not isinstance(face["error_code"], str)
    ):
        raise BuildIndexConfigurationError("source face schema is invalid")
    confidence = _finite_number(face["confidence"])
    quality = _validate_face_quality(face["quality"])
    if (
        confidence != quality.confidence
        or (face["status"] == "ok" and quality.decision != "accepted")
        or (face["status"] == "quality_rejected" and quality.decision != "quality_rejected")
    ):
        raise BuildIndexConfigurationError("source face quality is inconsistent")
    _validate_landmarks(face["landmarks"])
    return source_face_type(
        face_id=face_id,
        filename=image_filename,
        face_index=face_index,
        bounding_box=bounding_box_type(
            _finite_number(face["x"]),
            _finite_number(face["y"]),
            _finite_number(face["width"]),
            _finite_number(face["height"]),
        ),
        crop_path=crop_path,
        status=face["status"],
    )


def _validate_face_quality(value: object) -> Any:
    from photo_worker.face_quality import FaceQualityEvidence

    quality = _mapping(value)
    if set(quality) != {
        "algorithm_version",
        "confidence",
        "crop_size",
        "decision",
        "minimum_side_px",
        "reasons",
        "relative_area",
        "sharpness",
    } or not isinstance(quality["reasons"], list):
        raise BuildIndexConfigurationError("source face schema is invalid")
    try:
        return FaceQualityEvidence(
            _string(quality["algorithm_version"]),
            _positive_integer(quality["crop_size"]),
            _finite_number(quality["confidence"]),
            _finite_number(quality["minimum_side_px"]),
            _finite_number(quality["relative_area"]),
            _finite_number(quality["sharpness"]),
            _string(quality["decision"]),
            tuple(_string(reason) for reason in quality["reasons"]),
        )
    except ValueError:
        raise BuildIndexConfigurationError("source face schema is invalid") from None


def _validate_landmarks(value: object) -> None:
    landmarks = _mapping(value)
    if set(landmarks) != {
        "left_eye",
        "left_mouth_corner",
        "nose",
        "right_eye",
        "right_mouth_corner",
    }:
        raise BuildIndexConfigurationError("source face schema is invalid")
    for point in landmarks.values():
        if not isinstance(point, list) or len(point) != 2:
            raise BuildIndexConfigurationError("source face schema is invalid")
        _finite_number(point[0])
        _finite_number(point[1])


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise BuildIndexConfigurationError("source run schema is invalid")
    return value


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BuildIndexConfigurationError("source run schema is invalid")
    return float(value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise BuildIndexConfigurationError("source run schema is invalid")
    return value


def _positive_integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BuildIndexConfigurationError("source run schema is invalid")
    return value


def _optional_positive_integer(value: object) -> int | None:
    if value is None:
        return None
    return _positive_integer(value)


def _nonnegative_integer(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _safe_filename(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise BuildIndexConfigurationError("source face schema is invalid")
    path = Path(value)
    if path.is_absolute() or path.name != value or value in {".", ".."}:
        raise BuildIndexConfigurationError("source face schema is invalid")
    return value


def _safe_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        return ""
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return ""
    return value


def _model_metadata(path: Path) -> dict[str, object]:
    return {"basename": path.name, "size": path.stat().st_size, "sha256": _sha256_file(path)}


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def run_build_benchmark(config: BuildBenchmarkConfig) -> None:
    """Build one review-only proposal from immutable run and index artifacts."""
    from .benchmark import build_benchmark_proposal
    from .index_artifacts import load_face_index
    from .inventory import InventoryError, load_event_photo_inventory

    _validate_build_benchmark_config(config)
    artifact = _load_benchmark_run(config.run)
    run = artifact.benchmark_run
    try:
        index = load_face_index(config.index)
    except (OSError, TypeError, ValueError):
        raise BenchmarkConfigurationError("benchmark index is invalid") from None
    _validate_benchmark_index(run, artifact.source_faces, artifact.source_manifest, index)
    try:
        inventory = load_event_photo_inventory(config.photos)
    except (InventoryError, OSError, ValueError):
        raise BenchmarkConfigurationError("invalid photo inventory") from None
    _validate_benchmark_media(config.run, config.photos, run, inventory)
    try:
        proposal = build_benchmark_proposal(run, index, config.query_count)
    except (TypeError, ValueError):
        raise BenchmarkConfigurationError("benchmark proposal cannot be built") from None
    _publish_benchmark_proposal(config, proposal, run)


def run_finalize_benchmark(config: FinalizeBenchmarkConfig) -> None:
    """Finalize a thirty-query reviewed proposal without inferring relevance."""
    from .benchmark import QUERY_COUNT, finalize_benchmark
    from .benchmark_artifacts import (
        BenchmarkFinalArtifactWriter,
        load_annotations_csv,
        load_benchmark_proposal,
    )

    _validate_finalize_benchmark_config(config)
    try:
        proposal = _load_finalizable_proposal(config.proposal, load_benchmark_proposal)
        annotations = load_annotations_csv(config.annotations_csv, proposal, {})
        if (
            len(proposal.queries) != QUERY_COUNT
            or sum(query.split == "calibration" for query in proposal.queries) != 15
            or sum(query.split == "evaluation" for query in proposal.queries) != 15
        ):
            raise ValueError("proposal is not finalizable")
        final = finalize_benchmark(proposal, annotations)
    except (OSError, TypeError, ValueError):
        raise BenchmarkConfigurationError("benchmark finalization is invalid") from None
    writer = BenchmarkFinalArtifactWriter(config.output)
    try:
        writer.finish(final)
    finally:
        if not os.path.lexists(config.output):
            writer.abort()


def run_evaluate_cluster_expansion(config: EvaluateClusterExpansionConfig) -> None:
    """Publish an aggregate-only held-out evaluation from immutable artifacts."""
    from .benchmark_artifacts import load_final_benchmark
    from .cluster_expansion import (
        ClusterExpansionConfiguration,
        evaluate_cluster_expansion,
        production_corpus_configuration_hash,
        stable_evaluation_source,
        write_evaluation_report,
    )
    from .index_artifacts import load_face_index

    _validate_evaluate_cluster_expansion_config(config)
    try:
        benchmark = load_final_benchmark(config.benchmark)
        index = load_face_index(config.index)
        artifact = _load_benchmark_run(config.cluster_run)
        _validate_benchmark_index(
            artifact.benchmark_run, artifact.source_faces, artifact.source_manifest, index
        )
        derived_configuration_hash = production_corpus_configuration_hash(
            index,
            source_parameters=_mapping(artifact.source_manifest["parameters"]),
            generations=_load_face_embedding_generations(config.generations_json),
        )
        if config.configuration_hash != derived_configuration_hash:
            raise ValueError("supplied corpus configuration hash does not match artifact")
        report = evaluate_cluster_expansion(
            benchmark,
            index,
            artifact.clusters,
            ClusterExpansionConfiguration(
                config.direct_threshold,
                config.anchor_threshold,
                config.configuration_hash,
            ),
            corpus_build_duration_ms=artifact.corpus_build_duration_ms,
            corpus_build_peak_memory_bytes=artifact.corpus_build_peak_memory_bytes,
            cluster_parameters=_mapping(artifact.source_manifest["parameters"]),
            source_identities=stable_evaluation_source(
                benchmark,
                index,
                artifact.clusters,
                _mapping(artifact.source_manifest["parameters"]),
                corpus_evidence_sha256=artifact.corpus_evidence_sha256,
            ),
        )
        write_evaluation_report(config.output, report)
    except (OSError, TypeError, ValueError, KeyError):
        raise BenchmarkConfigurationError("cluster expansion cannot be evaluated") from None


def run_smoke_search_command(config: SmokeSearchConfig) -> None:
    """Run a bounded qualitative search from a trusted proposal and face index."""
    from .analysis import analyze_decoded_event_photo
    from .benchmark import _index_manifest_sha256
    from .benchmark_artifacts import load_benchmark_proposal
    from .image_decoder import ImageLimits, PillowImageDecoder
    from .index_artifacts import load_face_index
    from .inventory import EventPhoto, InventoryError, load_event_photo_inventory
    from .quality import FaceQualityThresholds
    from .smoke_search import run_smoke_search, write_smoke_search_output

    _validate_smoke_search_config(config)
    try:
        proposal = _load_finalizable_proposal(config.proposal, load_benchmark_proposal)
        index = load_face_index(config.index)
        if (
            proposal.source.index_manifest_sha256 != _index_manifest_sha256(index)
            or proposal.source.run_manifest_sha256 != index.manifest.source_run_manifest_sha256
            or proposal.source.faces_sha256 != index.manifest.source_faces_sha256
        ):
            raise ValueError("proposal and index do not match")
        if dict(index.manifest.yunet_model) != _model_metadata(config.yunet_model) or dict(
            index.manifest.sface_model
        ) != _model_metadata(config.sface_model):
            raise ValueError("model files do not match index")
        parameters = _smoke_index_parameters(index.manifest.parameters)
        thresholds = FaceQualityThresholds(
            algorithm_version="normalized-laplacian-v1",
            crop_size=112,
            minimum_face_px=parameters["min_face_px"],
            severe_blur_threshold=parameters["severe_blur_threshold"],
            borderline_blur_threshold=parameters["borderline_blur_threshold"],
            minimum_relative_area=parameters["minimum_relative_area"],
            minimum_confidence=parameters["minimum_confidence"],
        )
        decoder = PillowImageDecoder(
            ImageLimits(parameters["max_image_dimension"], parameters["max_image_pixels"])
        )
        try:
            inventory = load_event_photo_inventory(config.photos)
        except (InventoryError, OSError, ValueError):
            raise ValueError("photo inventory is invalid") from None
        if not config.run.is_dir() or {entry.filename for entry in index.entries} - {
            photo.filename for photo in inventory.photos
        }:
            raise ValueError("smoke-search media is incomplete")
        selected_queries = proposal.queries[: config.query_count]
        if any(not (config.run / query.query_crop_path).is_file() for query in selected_queries):
            raise ValueError("smoke-search query crops are incomplete")
        detector_type = YuNetDetector
        recognizer_type = SFaceRecognizer
        if detector_type is None or recognizer_type is None:
            from .models import SFaceRecognizer as loaded_recognizer
            from .models import YuNetDetector as loaded_detector

            detector_type = loaded_detector
            recognizer_type = loaded_recognizer
        detector = detector_type(config.yunet_model, threshold=parameters["detection_threshold"])
        recognizer = recognizer_type(config.sface_model)
        result = run_smoke_search(
            proposal,
            index,
            lambda query: _process_smoke_query(
                query,
                config.run,
                decoder,
                detector,
                recognizer,
                thresholds,
                EventPhoto,
                analyze_decoded_event_photo,
            ),
            query_count=config.query_count,
            limit=config.limit,
        )
        write_smoke_search_output(config.output, result, config.run, config.photos)
    except (OSError, TypeError, ValueError, KeyError):
        raise SmokeSearchConfigurationError("smoke search cannot be completed") from None


def run_compare_quality_command(config: CompareQualityConfig) -> None:
    """Compare two strict production-shaped quality runs and publish one review bundle."""
    from photo_worker.face_quality import FaceQualityThresholds

    from .quality_comparison import compare_quality_runs
    from .quality_comparison_artifacts import (
        load_quality_run,
        write_quality_comparison_bundle,
    )

    try:
        thresholds = FaceQualityThresholds(
            algorithm_version="normalized-laplacian-v1",
            crop_size=112,
            minimum_face_px=config.minimum_face_px,
            severe_blur_threshold=config.severe_blur_threshold,
            borderline_blur_threshold=config.borderline_blur_threshold,
            minimum_relative_area=config.minimum_relative_area,
            minimum_confidence=config.minimum_confidence,
        )
        if os.path.lexists(config.output):
            raise FileExistsError(config.output)
        baseline, _baseline_configuration = load_quality_run(config.baseline_run)
        candidate, candidate_configuration = load_quality_run(config.candidate_run)
        supplied_configuration = {
            "algorithm_version": thresholds.algorithm_version,
            "crop_size": thresholds.crop_size,
            "minimum_face_px": thresholds.minimum_face_px,
            "severe_blur_threshold": thresholds.severe_blur_threshold,
            "borderline_blur_threshold": thresholds.borderline_blur_threshold,
            "minimum_relative_area": thresholds.minimum_relative_area,
            "minimum_confidence": thresholds.minimum_confidence,
        }
        if candidate_configuration != supplied_configuration:
            raise ValueError("candidate threshold configuration differs")
        comparison = compare_quality_runs(baseline, candidate, thresholds=thresholds)
        write_quality_comparison_bundle(config.output, comparison, config.candidate_run)
    except (OSError, TypeError, ValueError, KeyError):
        raise QualityComparisonConfigurationError(
            "quality comparison cannot be completed"
        ) from None


def run_finalize_quality_review_command(config: FinalizeQualityReviewConfig) -> None:
    """Finalize exact reviewed detection and search evidence into a bounded approval."""
    from .benchmark_artifacts import load_final_benchmark
    from .quality_comparison_artifacts import (
        load_quality_comparison_bundle,
        load_quality_review_labels,
        load_quality_run,
        load_search_comparison,
    )
    from .quality_comparison_report import finalize_quality_review, write_quality_approval

    try:
        if os.path.lexists(config.output):
            raise FileExistsError(config.output)
        comparison, bundle_sha256 = load_quality_comparison_bundle(config.comparison)
        labels = load_quality_review_labels(config.labels_csv, comparison, bundle_sha256)
        search = load_search_comparison(config.search_comparison)
        baseline_run, _baseline_configuration = load_quality_run(config.baseline_run)
        candidate_run, _candidate_configuration = load_quality_run(config.candidate_run)
        benchmark = load_final_benchmark(config.benchmark)
        with tempfile.TemporaryDirectory(prefix="findme-quality-finalize-") as directory:
            recomputed_path = Path(directory) / "search-comparison"
            run_compare_search_command(
                CompareSearchConfig(
                    benchmark=config.benchmark,
                    baseline_index=config.baseline_index,
                    candidate_index=config.candidate_index,
                    run=config.run,
                    yunet_model=config.yunet_model,
                    sface_model=config.sface_model,
                    quality_comparison=config.comparison,
                    output=recomputed_path,
                )
            )
            recomputed_search = load_search_comparison(recomputed_path)
        reviewed_at = datetime.fromisoformat(config.reviewed_at.replace("Z", "+00:00"))
        approval = finalize_quality_review(
            comparison,
            labels,
            search,
            recomputed_search,
            baseline_run,
            candidate_run,
            benchmark,
            bundle_sha256,
            config.reviewer,
            reviewed_at,
        )
        write_quality_approval(config.output, approval)
    except (OSError, TypeError, ValueError, KeyError):
        raise QualityComparisonConfigurationError("quality review cannot be finalized") from None


def run_compare_search_command(config: CompareSearchConfig) -> None:
    """Run the finalized closed queries against baseline and candidate exact-cosine indexes."""
    from .analysis import analyze_decoded_event_photo
    from .benchmark import _index_manifest_sha256
    from .benchmark_artifacts import final_benchmark_sha256, load_final_benchmark
    from .image_decoder import ImageLimits, PillowImageDecoder
    from .index_artifacts import face_index_sha256, load_face_index
    from .inventory import EventPhoto
    from .quality import default_face_quality_thresholds
    from .quality_comparison_artifacts import (
        load_quality_comparison_bundle,
        quality_comparison_sha256,
        write_search_comparison,
    )
    from .smoke_search import SearchComparisonQuery, compare_search_indexes

    try:
        if os.path.lexists(config.output):
            raise FileExistsError(config.output)
        benchmark = load_final_benchmark(config.benchmark)
        baseline = load_face_index(config.baseline_index)
        candidate = load_face_index(config.candidate_index)
        comparison, _bundle_sha256 = load_quality_comparison_bundle(config.quality_comparison)
        if (
            _sha256_file(config.run / "manifest.json") != benchmark.source.run_manifest_sha256
            or _sha256_file(config.run / "faces.json") != benchmark.source.faces_sha256
        ):
            raise ValueError("query run does not match finalized benchmark")
        query_run_sha256 = _sha256_bytes(
            _canonical_json(
                {
                    "faces_sha256": benchmark.source.faces_sha256,
                    "manifest_sha256": benchmark.source.run_manifest_sha256,
                }
            )
        )
        _validate_quality_comparison_indexes(comparison, baseline, candidate)
        if benchmark.source.index_manifest_sha256 != _index_manifest_sha256(baseline):
            raise ValueError("benchmark does not match baseline index")
        supplied_yunet = _model_metadata(config.yunet_model)
        supplied_sface = _model_metadata(config.sface_model)
        if any(
            dict(index.manifest.yunet_model) != supplied_yunet
            or dict(index.manifest.sface_model) != supplied_sface
            for index in (baseline, candidate)
        ):
            raise ValueError("search indexes do not share the supplied models")
        parameters = _smoke_index_parameters(baseline.manifest.parameters)
        decoder = PillowImageDecoder(
            ImageLimits(parameters["max_image_dimension"], parameters["max_image_pixels"])
        )
        detector_type = YuNetDetector
        recognizer_type = SFaceRecognizer
        if detector_type is None or recognizer_type is None:
            from .models import SFaceRecognizer as loaded_recognizer
            from .models import YuNetDetector as loaded_detector

            detector_type = loaded_detector
            recognizer_type = loaded_recognizer
        detector = detector_type(config.yunet_model, threshold=parameters["detection_threshold"])
        recognizer = recognizer_type(config.sface_model)
        face_by_id = benchmark.face_by_id
        labels_by_query: dict[str, set[str]] = {}
        for annotation in benchmark.annotations:
            if annotation.label == "relevant":
                labels_by_query.setdefault(annotation.query_id, set()).add(
                    face_by_id[annotation.candidate_face_id].filename
                )
        for query in benchmark.queries:
            if (
                _query_crop_sha256(config.run, query.query_crop_path)
                != face_by_id[query.query_face_id].crop_sha256
            ):
                raise ValueError("query crop differs from finalized benchmark")
        queries = tuple(
            SearchComparisonQuery(
                query.query_id,
                query.query_filename,
                query_run_sha256,
                _query_crop_sha256(config.run, query.query_crop_path),
                _process_smoke_query(
                    query,
                    config.run,
                    decoder,
                    detector,
                    recognizer,
                    default_face_quality_thresholds(),
                    EventPhoto,
                    analyze_decoded_event_photo,
                ),
                tuple(sorted(labels_by_query.get(query.query_id, set()))),
            )
            for query in benchmark.queries
        )
        if any(
            _query_crop_sha256(config.run, query.query_crop_path) != evidence.query_crop_sha256
            for query, evidence in zip(benchmark.queries, queries, strict=True)
        ):
            raise ValueError("query crop changed during search preparation")
        result = compare_search_indexes(
            queries,
            baseline,
            candidate,
            quality_rejected_baseline_face_ids=tuple(
                sorted({item.baseline_face_id for item in comparison.new_rejections})
            ),
            quality_comparison_sha256=quality_comparison_sha256(comparison),
            benchmark_sha256=final_benchmark_sha256(benchmark),
            baseline_index_sha256=face_index_sha256(baseline),
            candidate_index_sha256=face_index_sha256(candidate),
        )
        write_search_comparison(config.output, result)
    except (OSError, TypeError, ValueError, KeyError):
        raise QualityComparisonConfigurationError("search comparison cannot be completed") from None


def _validate_quality_comparison_indexes(comparison: Any, baseline: Any, candidate: Any) -> None:
    def run_sha256(index: Any) -> str:
        return _sha256_bytes(
            _canonical_json(
                {
                    "faces_sha256": index.manifest.source_faces_sha256,
                    "manifest_sha256": index.manifest.source_run_manifest_sha256,
                }
            )
        )

    if (
        run_sha256(baseline) != comparison.baseline_run_sha256
        or run_sha256(candidate) != comparison.candidate_run_sha256
    ):
        raise ValueError("search indexes differ from quality comparison source runs")
    parameters = _smoke_index_parameters(candidate.manifest.parameters)
    configuration = {
        "algorithm_version": parameters["quality_algorithm_version"],
        "crop_size": parameters["quality_crop_size"],
        "minimum_face_px": parameters["min_face_px"],
        "severe_blur_threshold": parameters["severe_blur_threshold"],
        "borderline_blur_threshold": parameters["borderline_blur_threshold"],
        "minimum_relative_area": parameters["minimum_relative_area"],
        "minimum_confidence": parameters["minimum_confidence"],
    }
    if configuration != comparison.quality_configuration:
        raise ValueError("candidate index quality configuration differs")


def _query_crop_sha256(run_root: Path, relative: str) -> str:
    path = Path(relative)
    if path.is_absolute() or path.parts[:1] != ("faces",) or len(path.parts) != 2:
        raise ValueError("query crop path is unsafe")
    crop = run_root / path
    if crop.is_symlink() or crop.parent.resolve() != (run_root / "faces").resolve():
        raise ValueError("query crop path is unsafe")
    return _sha256_file(crop)


def _validate_smoke_search_config(config: SmokeSearchConfig) -> None:
    if not isinstance(config, SmokeSearchConfig):
        raise TypeError("config must be a SmokeSearchConfig")
    if (
        isinstance(config.query_count, bool)
        or not isinstance(config.query_count, int)
        or not 5 <= config.query_count <= 10
    ):
        raise SmokeSearchConfigurationError("query count must be between 5 and 10")
    if isinstance(config.limit, bool) or not isinstance(config.limit, int) or config.limit < 1:
        raise SmokeSearchConfigurationError("result limit must be positive")
    if os.path.lexists(config.output):
        raise SmokeSearchConfigurationError("output path already exists")


def _smoke_index_parameters(parameters: Mapping[str, object]) -> _IndexParameters:
    try:
        if (
            parameters["quality_algorithm_version"] != "normalized-laplacian-v1"
            or parameters["quality_crop_size"] != 112
        ):
            raise ValueError("index quality identity is invalid")
        values: _IndexParameters = {
            "detection_threshold": _finite_number(parameters["detection_threshold"]),
            "image_limit": None,
            "max_image_dimension": _positive_integer(parameters["max_image_dimension"]),
            "max_image_pixels": _positive_integer(parameters["max_image_pixels"]),
            "min_face_px": _positive_integer(parameters["min_face_px"]),
            "quality_algorithm_version": _string(parameters["quality_algorithm_version"]),
            "quality_crop_size": _positive_integer(parameters["quality_crop_size"]),
            "severe_blur_threshold": _finite_number(parameters["severe_blur_threshold"]),
            "borderline_blur_threshold": _finite_number(parameters["borderline_blur_threshold"]),
            "minimum_confidence": _finite_number(parameters["minimum_confidence"]),
            "minimum_relative_area": _finite_number(parameters["minimum_relative_area"]),
        }
        if values["severe_blur_threshold"] >= values["borderline_blur_threshold"]:
            raise ValueError("index quality thresholds are invalid")
        return values
    except (KeyError, BuildIndexConfigurationError):
        raise ValueError("index processing parameters are invalid") from None


def _process_smoke_query(
    query: Any,
    run_root: Path,
    decoder: Any,
    detector: Any,
    recognizer: Any,
    thresholds: Any,
    event_photo_type: Any,
    analyze_photo: Any,
) -> Any:
    photo = event_photo_type(query.query_crop_path, run_root / query.query_crop_path)
    decoded = _decode_smoke_query_crop(photo, decoder)
    try:
        analysis = analyze_photo(
            photo,
            decoded,
            detector,
            recognizer,
            quality_thresholds=thresholds,
        )
    finally:
        del decoded
    accepted = [face.embedding for face in analysis.faces if face.status == "ok"]
    if len(accepted) != 1 or accepted[0] is None:
        raise ValueError("query must produce exactly one accepted face")
    return accepted[0].vector


def _decode_smoke_query_crop(photo: Any, decoder: Any) -> Any:
    """Decode trusted PNG query crops without broadening the JPEG event-photo decoder."""
    if photo.path.suffix.lower() != ".png":
        return decoder.decode(photo)
    from PIL import Image, ImageOps, UnidentifiedImageError

    from .analysis import DecodedImage, ImageProcessingError

    limits = decoder._limits
    try:
        with Image.open(photo.path) as verified:
            verified.verify()
        with Image.open(photo.path) as source:
            width, height = source.size
            _validate_smoke_query_image_size(width, height, limits)
            oriented = ImageOps.exif_transpose(source)
            rgb = np.ascontiguousarray(np.asarray(oriented.convert("RGB"), dtype=np.uint8))
    except ImageProcessingError:
        raise
    except Image.DecompressionBombError:
        raise ImageProcessingError("image_too_large") from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise ImageProcessingError("image_decode_failed") from None
    height, width = rgb.shape[:2]
    _validate_smoke_query_image_size(width, height, limits)
    return DecodedImage(
        rgb=rgb,
        bgr=np.ascontiguousarray(rgb[:, :, ::-1]),
        width=width,
        height=height,
    )


def _validate_smoke_query_image_size(width: int, height: int, limits: Any) -> None:
    from .analysis import ImageProcessingError

    if (
        width < 1
        or height < 1
        or width > limits.maximum_dimension
        or height > limits.maximum_dimension
        or width * height > limits.maximum_pixels
    ):
        raise ImageProcessingError("image_too_large")


def _load_finalizable_proposal(path: Path, loader: Any) -> Any:
    root = path.resolve()
    if (
        not root.is_dir()
        or {child.name for child in root.iterdir()}
        != {
            "manifest.json",
            "proposal.json",
            "report.html",
            "queries",
        }
        or not (root / "report.html").is_file()
        or not (root / "queries").is_dir()
    ):
        raise BenchmarkConfigurationError("proposal bundle is invalid")
    with tempfile.TemporaryDirectory(
        prefix=".benchmark-proposal-load.", dir=root.parent
    ) as staging:
        strict_root = Path(staging)
        shutil.copyfile(root / "manifest.json", strict_root / "manifest.json")
        shutil.copyfile(root / "proposal.json", strict_root / "proposal.json")
        proposal = loader(strict_root)
    expected_pages = {query.query_id for query in proposal.all_queries}
    if {child.name for child in (root / "queries").iterdir()} != expected_pages or any(
        not (root / "queries" / query_id / "index.html").is_file()
        or {child.name for child in (root / "queries" / query_id).iterdir()} != {"index.html"}
        for query_id in expected_pages
    ):
        raise BenchmarkConfigurationError("proposal report is invalid")
    return proposal


def _validate_build_benchmark_config(config: BuildBenchmarkConfig) -> None:
    if not isinstance(config, BuildBenchmarkConfig):
        raise TypeError("config must be a BuildBenchmarkConfig")
    if isinstance(config.query_count, bool) or not isinstance(config.query_count, int):
        raise BenchmarkConfigurationError("query count must be an integer")
    if config.query_count < 1:
        raise BenchmarkConfigurationError("query count must be positive")
    if os.path.lexists(config.output):
        raise BenchmarkConfigurationError("output path already exists")


def _validate_finalize_benchmark_config(config: FinalizeBenchmarkConfig) -> None:
    if not isinstance(config, FinalizeBenchmarkConfig):
        raise TypeError("config must be a FinalizeBenchmarkConfig")
    if os.path.lexists(config.output):
        raise BenchmarkConfigurationError("output path already exists")


def _validate_evaluate_cluster_expansion_config(config: EvaluateClusterExpansionConfig) -> None:
    if not isinstance(config, EvaluateClusterExpansionConfig):
        raise TypeError("config must be an EvaluateClusterExpansionConfig")
    try:
        from .cluster_expansion import ClusterExpansionConfiguration

        ClusterExpansionConfiguration(
            config.direct_threshold,
            config.anchor_threshold,
            config.configuration_hash,
        )
    except ValueError:
        raise BenchmarkConfigurationError("cluster expansion configuration is invalid") from None
    if os.path.lexists(config.output):
        raise BenchmarkConfigurationError("output path already exists")
    if not config.generations_json.is_file():
        raise BenchmarkConfigurationError("generation contract is invalid")


def _load_face_embedding_generations(path: Path) -> tuple[Mapping[str, object], ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not payload:
            raise ValueError
        return tuple(_mapping(value) for value in payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise BenchmarkConfigurationError("generation contract is invalid") from None


def _load_benchmark_run(
    run_root: Path,
) -> _StrictClusterRunArtifact:
    from .analysis import BoundingBox, face_crop_path
    from .benchmark import BenchmarkFace, BenchmarkRun
    from .cluster_expansion import ClusterMember, FaceCluster
    from .index import SourceFaceRecord

    try:
        manifest_bytes, faces_bytes, manifest, source_records = _load_index_source_run(
            run_root, BoundingBox, SourceFaceRecord, face_crop_path
        )
        clusters = _load_benchmark_clusters(run_root / "clusters.json")
        source_faces = _benchmark_source_faces(faces_bytes, source_records)
        accepted_ids = {record.face_id for record in source_records if record.status == "ok"}
        memberships, typed_clusters = _validate_benchmark_clusters(
            clusters, source_faces, accepted_ids, ClusterMember, FaceCluster
        )
        faces = tuple(
            BenchmarkFace(
                face_id=face.face_id,
                filename=face.filename,
                crop_path=face.crop_path,
                crop_sha256=_query_crop_sha256(run_root, face.crop_path),
                cluster_id=memberships[face.face_id],
                status="ok",
                confidence=face.confidence,
                sharpness=face.sharpness,
                relative_area=face.relative_area,
            )
            for face in sorted(source_faces.values(), key=lambda item: item.face_id)
            if face.face_id in accepted_ids
        )
        duration_seconds = _finite_number(manifest["duration_seconds"])
        peak_memory_bytes = manifest["peak_memory_bytes"]
        if duration_seconds < 0 or not isinstance(peak_memory_bytes, int) or peak_memory_bytes < 1:
            raise BenchmarkConfigurationError("benchmark run resource evidence is invalid")
        return _StrictClusterRunArtifact(
            benchmark_run=BenchmarkRun(
                _sha256_bytes(manifest_bytes), _sha256_bytes(faces_bytes), faces
            ),
            source_faces=source_faces,
            source_manifest=manifest,
            clusters=typed_clusters,
            corpus_build_duration_ms=max(0, round(duration_seconds * 1_000)),
            corpus_build_peak_memory_bytes=peak_memory_bytes,
            corpus_evidence_sha256=_stable_corpus_evidence_sha256(
                manifest, _sha256_bytes(faces_bytes), typed_clusters
            ),
        )
    except (BuildIndexConfigurationError, OSError, TypeError, ValueError, KeyError):
        raise BenchmarkConfigurationError("benchmark run is invalid") from None


def _load_benchmark_clusters(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise BenchmarkConfigurationError("benchmark clusters are invalid") from None


def _benchmark_source_faces(
    payload: bytes, source_records: Sequence[Any]
) -> Mapping[str, _BenchmarkSourceFace]:
    faces_payload = _json_object(payload)
    images = faces_payload.get("images")
    if not isinstance(images, list):
        raise BenchmarkConfigurationError("benchmark faces are invalid")
    record_by_id = {record.face_id: record for record in source_records}
    parsed: dict[str, _BenchmarkSourceFace] = {}
    for image in images:
        image_mapping = _mapping(image)
        raw_faces = image_mapping.get("faces")
        if not isinstance(raw_faces, list):
            raise BenchmarkConfigurationError("benchmark faces are invalid")
        for raw_face in raw_faces:
            face = _mapping(raw_face)
            face_id = face.get("face_id")
            record = record_by_id.get(face_id) if isinstance(face_id, str) else None
            quality = _validate_face_quality(face.get("quality"))
            if not isinstance(face_id, str) or record is None or face_id in parsed:
                raise BenchmarkConfigurationError("benchmark faces are invalid")
            if record.status == "ok" and (quality.decision != "accepted" or quality.reasons):
                raise BenchmarkConfigurationError("benchmark face quality is inconsistent")
            parsed[face_id] = _BenchmarkSourceFace(
                face_id=record.face_id,
                filename=record.filename,
                face_index=record.face_index,
                crop_path=record.crop_path,
                x=record.bounding_box.x,
                y=record.bounding_box.y,
                width=record.bounding_box.width,
                height=record.bounding_box.height,
                confidence=_finite_number(face["confidence"]),
                minimum_side_px=quality.minimum_side_px,
                relative_area=quality.relative_area,
                sharpness=quality.sharpness,
            )
    if set(parsed) != set(record_by_id):
        raise BenchmarkConfigurationError("benchmark faces are incomplete")
    return parsed


def _validate_benchmark_clusters(
    payload: object,
    source_faces: Mapping[str, _BenchmarkSourceFace],
    accepted_ids: set[str],
    cluster_member_type: Any,
    face_cluster_type: Any,
) -> tuple[Mapping[str, str], tuple[Any, ...]]:
    root = _mapping(payload)
    clusters = root.get("clusters")
    if set(root) != {"clusters"} or not isinstance(clusters, list):
        raise BenchmarkConfigurationError("benchmark clusters are invalid")
    memberships: dict[str, str] = {}
    cluster_ids: set[str] = set()
    ordered_cluster_ids: list[str] = []
    typed_clusters: list[Any] = []
    for raw_cluster in clusters:
        cluster = _mapping(raw_cluster)
        if set(cluster) != {"cluster_id", "members", "representative_face_id"}:
            raise BenchmarkConfigurationError("benchmark clusters are invalid")
        cluster_id = cluster["cluster_id"]
        members = cluster["members"]
        representative = cluster["representative_face_id"]
        if (
            not isinstance(cluster_id, str)
            or not cluster_id
            or cluster_id in cluster_ids
            or not isinstance(representative, str)
            or not isinstance(members, list)
            or not members
        ):
            raise BenchmarkConfigurationError("benchmark clusters are invalid")
        cluster_ids.add(cluster_id)
        ordered_cluster_ids.append(cluster_id)
        member_ids: set[str] = set()
        typed_members: list[Any] = []
        for raw_member in members:
            member = _mapping(raw_member)
            if set(member) != {"distance_to_representative", "face_id", "face_index", "filename"}:
                raise BenchmarkConfigurationError("benchmark clusters are invalid")
            face_id = member["face_id"]
            source = source_faces.get(face_id) if isinstance(face_id, str) else None
            distance = _finite_number(member["distance_to_representative"])
            if (
                source is None
                or face_id not in accepted_ids
                or face_id in memberships
                or face_id in member_ids
                or member["filename"] != source.filename
                or member["face_index"] != source.face_index
                or distance < 0
                or (
                    face_id == representative
                    and not math.isclose(distance, 0.0, rel_tol=0.0, abs_tol=1e-12)
                )
                or (face_id != representative and distance == 0.0)
            ):
                raise BenchmarkConfigurationError("benchmark cluster membership is invalid")
            member_ids.add(face_id)
            memberships[face_id] = cluster_id
            typed_members.append(cluster_member_type(face_id, source.filename, float(distance)))
        if representative not in member_ids:
            raise BenchmarkConfigurationError("benchmark cluster representative is invalid")
        typed_clusters.append(face_cluster_type(cluster_id, representative, tuple(typed_members)))
    if ordered_cluster_ids != [f"person-{number:04d}" for number in range(1, len(cluster_ids) + 1)]:
        raise BenchmarkConfigurationError("benchmark cluster IDs are invalid")
    if set(memberships) != accepted_ids:
        raise BenchmarkConfigurationError("benchmark cluster membership is incomplete")
    return memberships, tuple(typed_clusters)


def _stable_corpus_evidence_sha256(
    manifest: Mapping[str, object], faces_sha256: str, clusters: Sequence[Any]
) -> str:
    """Hash only durable corpus inputs, excluding timestamps and measured resources."""
    membership = [
        {
            "cluster_id": cluster.cluster_id,
            "representative_face_id": cluster.representative_face_id,
            "members": [
                {
                    "face_id": member.face_id,
                    "filename": member.filename,
                    "distance_to_representative": member.distance_to_representative,
                }
                for member in cluster.members
            ],
        }
        for cluster in clusters
    ]
    normalized = {
        "schema_version": 1,
        "source_faces_sha256": faces_sha256,
        "model_hashes": dict(sorted(_mapping(manifest["model_hashes"]).items())),
        "parameters": dict(sorted(_mapping(manifest["parameters"]).items())),
        "dependency_versions": dict(sorted(_mapping(manifest["dependency_versions"]).items())),
        "membership_sha256": _sha256_bytes(_canonical_json(membership)),
    }
    return _sha256_bytes(_canonical_json(normalized))


def _validate_benchmark_index(
    run: Any,
    source_faces: Mapping[str, _BenchmarkSourceFace],
    source_manifest: Mapping[str, object],
    index: Any,
) -> None:
    if (
        index.manifest.source_run_manifest_sha256 != run.manifest_sha256
        or index.manifest.source_faces_sha256 != run.faces_sha256
    ):
        raise BenchmarkConfigurationError("benchmark index source does not match run")
    model_hashes = _mapping(source_manifest["model_hashes"])
    parameters = _index_parameters(source_manifest)
    yunet_model = _mapping(index.manifest.yunet_model)
    sface_model = _mapping(index.manifest.sface_model)
    source_parameters = _mapping(source_manifest["parameters"])
    if (
        yunet_model.get("sha256") != model_hashes["yunet"]
        or sface_model.get("sha256") != model_hashes["sface"]
        or yunet_model.get("basename") != source_parameters["yunet_model_filename"]
        or sface_model.get("basename") != source_parameters["sface_model_filename"]
        or dict(index.manifest.parameters) != dict(parameters)
    ):
        raise BenchmarkConfigurationError("benchmark index processing does not match run")
    expected_ids = {face.face_id for face in run.faces}
    entries = getattr(index, "entries", ())
    if {entry.face_id for entry in entries} != expected_ids:
        raise BenchmarkConfigurationError("benchmark index does not match run")
    for entry in entries:
        source = source_faces.get(entry.face_id)
        if (
            source is None
            or (
                entry.filename,
                entry.face_index,
                entry.crop_path,
                entry.bounding_box.x,
                entry.bounding_box.y,
                entry.bounding_box.width,
                entry.bounding_box.height,
            )
            != (
                source.filename,
                source.face_index,
                source.crop_path,
                source.x,
                source.y,
                source.width,
                source.height,
            )
            or (
                entry.quality.confidence,
                entry.quality.minimum_side_px,
                entry.quality.relative_area,
                entry.quality.sharpness,
                entry.quality.decision,
                entry.quality.reasons,
            )
            != (
                source.confidence,
                source.minimum_side_px,
                source.relative_area,
                source.sharpness,
                "accepted",
                (),
            )
        ):
            raise BenchmarkConfigurationError("benchmark index metadata does not match run")


def _validate_benchmark_media(run_root: Path, photos_root: Path, run: Any, inventory: Any) -> None:
    available = {photo.filename for photo in inventory.photos}
    if not photos_root.is_dir() or any(
        face.filename not in available
        or not (photos_root / face.filename).is_file()
        or not (run_root / face.crop_path).is_file()
        for face in run.faces
    ):
        raise BenchmarkConfigurationError("benchmark media is incomplete")


def _publish_benchmark_proposal(config: BuildBenchmarkConfig, proposal: Any, run: Any) -> None:
    from .benchmark_artifacts import BenchmarkProposalArtifactWriter
    from .benchmark_report import write_benchmark_report

    config.output.parent.mkdir(parents=True, exist_ok=True)
    outer = Path(
        tempfile.mkdtemp(prefix=f".{config.output.name}.benchmark.", dir=config.output.parent)
    )
    bundle = outer / "bundle"
    try:
        BenchmarkProposalArtifactWriter(bundle).finish(proposal)
        write_benchmark_report(bundle, proposal, run, config.run, config.photos)
        if os.path.lexists(config.output):
            raise FileExistsError(config.output)
        os.replace(bundle, config.output)
        outer.rmdir()
    except BaseException:
        shutil.rmtree(outer, ignore_errors=True)
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
    cluster.add_argument("--max-candidate-edges", type=int, default=100_000)
    cluster.add_argument("--image-limit", type=int)
    cluster.add_argument("--max-image-dimension", type=int, default=12000)
    cluster.add_argument("--max-image-pixels", type=int, default=100_000_000)
    cluster.add_argument("--severe-blur-threshold", type=float, default=25.0)
    cluster.add_argument("--borderline-blur-threshold", type=float, default=50.0)
    cluster.add_argument("--minimum-relative-area", type=float, default=0.0009)
    cluster.add_argument("--minimum-confidence", type=float, default=0.82)
    compare = commands.add_parser("compare")
    compare.add_argument("--run", type=Path, required=True)
    compare.add_argument("--peakshot-export", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    review = commands.add_parser("review")
    review.add_argument("--run", type=Path, required=True)
    review.add_argument("--comparison", type=Path, required=True)
    review.add_argument("--peakshot-export", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    build_index = commands.add_parser("build-index")
    build_index.add_argument("--run", type=Path, required=True)
    build_index.add_argument("--photos", type=Path, required=True)
    build_index.add_argument("--yunet-model", type=Path, required=True)
    build_index.add_argument("--sface-model", type=Path, required=True)
    build_index.add_argument("--output", type=Path, required=True)
    build_benchmark = commands.add_parser("build-benchmark")
    build_benchmark.add_argument("--run", type=Path, required=True)
    build_benchmark.add_argument("--index", type=Path, required=True)
    build_benchmark.add_argument("--photos", type=Path, required=True)
    build_benchmark.add_argument("--output", type=Path, required=True)
    build_benchmark.add_argument("--query-count", type=int, default=30)
    finalize_benchmark = commands.add_parser("finalize-benchmark")
    finalize_benchmark.add_argument("--proposal", type=Path, required=True)
    finalize_benchmark.add_argument("--annotations-csv", type=Path, required=True)
    finalize_benchmark.add_argument("--output", type=Path, required=True)
    evaluate_cluster_expansion = commands.add_parser("evaluate-cluster-expansion")
    evaluate_cluster_expansion.add_argument("--benchmark", type=Path, required=True)
    evaluate_cluster_expansion.add_argument("--index", type=Path, required=True)
    evaluate_cluster_expansion.add_argument("--cluster-run", type=Path, required=True)
    evaluate_cluster_expansion.add_argument("--output", type=Path, required=True)
    evaluate_cluster_expansion.add_argument("--direct-threshold", type=float, required=True)
    evaluate_cluster_expansion.add_argument("--anchor-threshold", type=float, required=True)
    evaluate_cluster_expansion.add_argument("--configuration-hash", required=True)
    evaluate_cluster_expansion.add_argument("--generations-json", type=Path, required=True)
    smoke_search = commands.add_parser("smoke-search")
    smoke_search.add_argument("--proposal", type=Path, required=True)
    smoke_search.add_argument("--index", type=Path, required=True)
    smoke_search.add_argument("--run", type=Path, required=True)
    smoke_search.add_argument("--photos", type=Path, required=True)
    smoke_search.add_argument("--yunet-model", type=Path, required=True)
    smoke_search.add_argument("--sface-model", type=Path, required=True)
    smoke_search.add_argument("--output", type=Path, required=True)
    smoke_search.add_argument("--query-count", type=int, default=5)
    smoke_search.add_argument("--limit", type=int, default=10)
    compare_quality = commands.add_parser("compare-quality")
    compare_quality.add_argument("--baseline-run", type=Path, required=True)
    compare_quality.add_argument("--candidate-run", type=Path, required=True)
    compare_quality.add_argument("--output", type=Path, required=True)
    compare_quality.add_argument("--minimum-face-px", type=int, default=32)
    compare_quality.add_argument("--severe-blur-threshold", type=float, default=25.0)
    compare_quality.add_argument("--borderline-blur-threshold", type=float, default=50.0)
    compare_quality.add_argument("--minimum-relative-area", type=float, default=0.0009)
    compare_quality.add_argument("--minimum-confidence", type=float, default=0.82)
    finalize_quality = commands.add_parser("finalize-quality-review")
    finalize_quality.add_argument("--comparison", type=Path, required=True)
    finalize_quality.add_argument("--labels-csv", type=Path, required=True)
    finalize_quality.add_argument("--search-comparison", type=Path, required=True)
    finalize_quality.add_argument("--baseline-run", type=Path, required=True)
    finalize_quality.add_argument("--candidate-run", type=Path, required=True)
    finalize_quality.add_argument("--benchmark", type=Path, required=True)
    finalize_quality.add_argument("--baseline-index", type=Path, required=True)
    finalize_quality.add_argument("--candidate-index", type=Path, required=True)
    finalize_quality.add_argument("--run", type=Path, required=True)
    finalize_quality.add_argument("--yunet-model", type=Path, required=True)
    finalize_quality.add_argument("--sface-model", type=Path, required=True)
    finalize_quality.add_argument("--reviewer", required=True)
    finalize_quality.add_argument("--reviewed-at", required=True)
    finalize_quality.add_argument("--output", type=Path, required=True)
    compare_search = commands.add_parser("compare-search")
    compare_search.add_argument("--benchmark", type=Path, required=True)
    compare_search.add_argument("--baseline-index", type=Path, required=True)
    compare_search.add_argument("--candidate-index", type=Path, required=True)
    compare_search.add_argument("--run", type=Path, required=True)
    compare_search.add_argument("--yunet-model", type=Path, required=True)
    compare_search.add_argument("--sface-model", type=Path, required=True)
    compare_search.add_argument("--quality-comparison", type=Path, required=True)
    compare_search.add_argument("--output", type=Path, required=True)
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
        elif arguments.command == "build-index":
            build_index_config = BuildIndexConfig(
                run=arguments.run,
                photos=arguments.photos,
                yunet_model=arguments.yunet_model,
                sface_model=arguments.sface_model,
                output=arguments.output,
            )
        elif arguments.command == "build-benchmark":
            build_benchmark_config = BuildBenchmarkConfig(
                run=arguments.run,
                index=arguments.index,
                photos=arguments.photos,
                output=arguments.output,
                query_count=arguments.query_count,
            )
        elif arguments.command == "finalize-benchmark":
            finalize_benchmark_config = FinalizeBenchmarkConfig(
                proposal=arguments.proposal,
                annotations_csv=arguments.annotations_csv,
                output=arguments.output,
            )
        elif arguments.command == "evaluate-cluster-expansion":
            evaluate_cluster_expansion_config = EvaluateClusterExpansionConfig(
                benchmark=arguments.benchmark,
                index=arguments.index,
                cluster_run=arguments.cluster_run,
                output=arguments.output,
                direct_threshold=arguments.direct_threshold,
                anchor_threshold=arguments.anchor_threshold,
                configuration_hash=arguments.configuration_hash,
                generations_json=arguments.generations_json,
            )
        elif arguments.command == "smoke-search":
            smoke_search_config = SmokeSearchConfig(
                proposal=arguments.proposal,
                index=arguments.index,
                run=arguments.run,
                photos=arguments.photos,
                yunet_model=arguments.yunet_model,
                sface_model=arguments.sface_model,
                output=arguments.output,
                query_count=arguments.query_count,
                limit=arguments.limit,
            )
        elif arguments.command == "compare-quality":
            compare_quality_config = CompareQualityConfig(
                baseline_run=arguments.baseline_run,
                candidate_run=arguments.candidate_run,
                output=arguments.output,
                minimum_face_px=arguments.minimum_face_px,
                severe_blur_threshold=arguments.severe_blur_threshold,
                borderline_blur_threshold=arguments.borderline_blur_threshold,
                minimum_relative_area=arguments.minimum_relative_area,
                minimum_confidence=arguments.minimum_confidence,
            )
        elif arguments.command == "finalize-quality-review":
            finalize_quality_config = FinalizeQualityReviewConfig(
                comparison=arguments.comparison,
                labels_csv=arguments.labels_csv,
                search_comparison=arguments.search_comparison,
                baseline_run=arguments.baseline_run,
                candidate_run=arguments.candidate_run,
                benchmark=arguments.benchmark,
                baseline_index=arguments.baseline_index,
                candidate_index=arguments.candidate_index,
                run=arguments.run,
                yunet_model=arguments.yunet_model,
                sface_model=arguments.sface_model,
                reviewer=arguments.reviewer,
                reviewed_at=arguments.reviewed_at,
                output=arguments.output,
            )
        elif arguments.command == "compare-search":
            compare_search_config = CompareSearchConfig(
                benchmark=arguments.benchmark,
                baseline_index=arguments.baseline_index,
                candidate_index=arguments.candidate_index,
                run=arguments.run,
                yunet_model=arguments.yunet_model,
                sface_model=arguments.sface_model,
                quality_comparison=arguments.quality_comparison,
                output=arguments.output,
            )
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
                max_candidate_edges=arguments.max_candidate_edges,
                image_limit=arguments.image_limit,
                max_image_dimension=arguments.max_image_dimension,
                max_image_pixels=arguments.max_image_pixels,
                severe_blur_threshold=arguments.severe_blur_threshold,
                borderline_blur_threshold=arguments.borderline_blur_threshold,
                minimum_relative_area=arguments.minimum_relative_area,
                minimum_confidence=arguments.minimum_confidence,
            )
            config.validate()
            if os.path.lexists(config.output):
                parser.error(f"output path already exists: {config.output}")
    except SystemExit as error:
        return error.code if isinstance(error.code, int) else 2
    except (
        BuildIndexConfigurationError,
        ClusterConfigurationError,
        ComparisonError,
        QualityComparisonConfigurationError,
        ValueError,
    ):
        return 2

    try:
        if arguments.command == "compare":
            run_comparison(comparison_config)
        elif arguments.command == "review":
            run_review(review_config)
        elif arguments.command == "build-index":
            try:
                run_build_index(build_index_config)
            except Exception:
                return 2
        elif arguments.command == "build-benchmark":
            try:
                run_build_benchmark(build_benchmark_config)
            except Exception:
                return 2
        elif arguments.command == "finalize-benchmark":
            try:
                run_finalize_benchmark(finalize_benchmark_config)
            except Exception:
                return 2
        elif arguments.command == "evaluate-cluster-expansion":
            try:
                run_evaluate_cluster_expansion(evaluate_cluster_expansion_config)
            except Exception:
                return 2
        elif arguments.command == "smoke-search":
            try:
                run_smoke_search_command(smoke_search_config)
            except Exception:
                return 2
        elif arguments.command == "compare-quality":
            try:
                run_compare_quality_command(compare_quality_config)
            except Exception:
                return 2
        elif arguments.command == "finalize-quality-review":
            try:
                run_finalize_quality_review_command(finalize_quality_config)
            except Exception:
                return 2
        elif arguments.command == "compare-search":
            try:
                run_compare_search_command(compare_search_config)
            except Exception:
                return 2
        else:
            run_cluster(config)
    except (
        BuildIndexConfigurationError,
        ClusterConfigurationError,
        ComparisonError,
        QualityComparisonConfigurationError,
        FileExistsError,
        OSError,
        ValueError,
    ):
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

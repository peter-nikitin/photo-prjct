from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil, floor
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from face_spike.cli import RunConfig
from face_spike.pipeline import FaceDetection, ImageAnalysis
from face_spike.retrieval import RetrievalMetrics

_DETECTION_HEADERS = (
    "filename",
    "participant_group",
    "face_expected",
    "detection_count",
    "primary_x",
    "primary_y",
    "primary_width",
    "primary_height",
    "primary_confidence",
    "crop_width",
    "crop_height",
    "status",
    "error_code",
)
_RETRIEVAL_HEADERS = (
    "query_filename",
    "candidate_filename",
    "rank",
    "cosine_distance",
    "same_group",
    "query_participant_group",
    "candidate_participant_group",
)
_PREVIEW_LIMIT = 1920


@dataclass(frozen=True)
class ExperimentResult:
    """Completed experiment data, deliberately excluding raw embedding serialization."""

    config: RunConfig
    analyses: Sequence[ImageAnalysis]
    retrieval: RetrievalMetrics
    started_at: datetime
    finished_at: datetime
    durations: Mapping[str, float]
    unexpected_labelled_image_failures: int
    dependency_versions: Mapping[str, str]


class RunArtifactWriter:
    """Write a run privately, then publish its complete directory atomically."""

    def __init__(self, output: Path) -> None:
        if os.path.lexists(output):
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.output = output
        self._staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
        try:
            self._annotated = self._staging / "annotated"
            self._crops = self._staging / "crops"
            self._annotated.mkdir()
            self._crops.mkdir()
        except Exception:
            self.abort()
            raise

    def write_diagnostics(self, analysis: ImageAnalysis) -> None:
        try:
            _write_diagnostic_images(self._annotated, self._crops, analysis)
        except Exception:
            self.abort()
            raise

    def finish(self, result: ExperimentResult) -> None:
        try:
            metrics = build_metrics(result)
            _write_json_atomic(self._staging / "manifest.json", _manifest(result, metrics))
            _write_csv_atomic(
                self._staging / "detections.csv",
                _DETECTION_HEADERS,
                _detection_rows(result.analyses),
            )
            _write_csv_atomic(
                self._staging / "retrieval.csv",
                _RETRIEVAL_HEADERS,
                _retrieval_rows(result.retrieval),
            )
            _write_json_atomic(self._staging / "metrics.json", metrics)
            from face_spike.report import render_report

            _write_text_atomic(self._staging / "report.html", render_report(result, metrics))
            if os.path.lexists(self.output):
                raise FileExistsError(self.output)
            os.replace(self._staging, self.output)
        except Exception:
            self.abort()
            raise

    def abort(self) -> None:
        if self._staging.exists():
            shutil.rmtree(self._staging)


def write_run(output: Path, result: ExperimentResult) -> None:
    """Publish one immutable run directory beneath a path that did not previously exist."""
    writer = RunArtifactWriter(output)
    try:
        for analysis in _sorted_analyses(result.analyses):
            writer.write_diagnostics(analysis)
        writer.finish(result)
    except Exception:
        writer.abort()
        raise


def build_metrics(result: ExperimentResult) -> dict[str, Any]:
    analyses = _sorted_analyses(result.analyses)
    expected_counts = Counter(analysis.image.item.face_expected.value for analysis in analyses)
    expected_yes = expected_counts["yes"]
    detected_expected_yes = sum(
        analysis.primary_detection is not None
        for analysis in analyses
        if analysis.image.item.face_expected.value == "yes"
    )
    false_detections = sum(
        analysis.primary_detection is not None
        for analysis in analyses
        if analysis.image.item.face_expected.value == "no"
    )
    errors = Counter(
        analysis.status for analysis in analyses if analysis.status not in {"ok", "no_detection"}
    )
    retrieval = result.retrieval
    return {
        "counts": {
            "dataset_images": len(analyses),
            "face_expected": {key: expected_counts[key] for key in ("yes", "no", "uncertain")},
            "accepted_detections": sum(len(analysis.detections) for analysis in analyses),
            "primary_detections": sum(
                analysis.primary_detection is not None for analysis in analyses
            ),
            "embedding_success": sum(
                analysis.status == "ok" and analysis.embedding is not None for analysis in analyses
            ),
            "unexpected_labelled_image_failures": result.unexpected_labelled_image_failures,
        },
        "detection": {
            "expected_face_coverage": (
                detected_expected_yes / expected_yes if expected_yes else None
            ),
            "expected_face_detected": detected_expected_yes,
            "expected_face_total": expected_yes,
            "false_detections": false_detections,
        },
        "retrieval": {
            "evaluable_queries": retrieval.evaluable_queries,
            "recall_at_1": retrieval.recall_at_1,
            "recall_at_5": retrieval.recall_at_5,
            "recall_at_10": retrieval.recall_at_10,
        },
        "latency_seconds": {key: result.durations[key] for key in sorted(result.durations)},
        "error_counts": {key: errors[key] for key in sorted(errors)},
        "face_size_buckets": _face_size_buckets(analyses),
    }


def _manifest(result: ExperimentResult, metrics: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "dependency_versions": {
            key: result.dependency_versions[key] for key in sorted(result.dependency_versions)
        },
        "duration_seconds": (result.finished_at - result.started_at).total_seconds(),
        "durations_seconds": {key: result.durations[key] for key in sorted(result.durations)},
        "finished_at": _timestamp(result.finished_at),
        "model_hashes": {
            "sface": _sha256(result.config.sface_model),
            "yunet": _sha256(result.config.yunet_model),
        },
        "parameters": {
            "detection_threshold": result.config.detection_threshold,
            "input_photos_basename": result.config.photos.name,
            "labels_filename": result.config.labels.name,
            "max_image_dimension": result.config.max_image_dimension,
            "max_image_pixels": result.config.max_image_pixels,
            "min_face_px": result.config.min_face_px,
            "sface_model_filename": result.config.sface_model.name,
            "top_k": result.config.top_k,
            "yunet_model_filename": result.config.yunet_model.name,
        },
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "started_at": _timestamp(result.started_at),
        "counts": metrics["counts"],
        "error_counts": metrics["error_counts"],
    }


def _timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _detection_rows(analyses: Sequence[ImageAnalysis]) -> list[dict[str, str | int | float]]:
    rows: list[dict[str, str | int | float]] = []
    for analysis in _sorted_analyses(analyses):
        primary = analysis.primary_detection
        box = primary.bounding_box if primary is not None else None
        crop_width, crop_height = _crop_dimensions(analysis)
        rows.append(
            {
                "filename": analysis.image.item.filename,
                "participant_group": analysis.image.item.participant_group or "",
                "face_expected": analysis.image.item.face_expected.value,
                "detection_count": len(analysis.detections),
                "primary_x": "" if box is None else _number(box.x),
                "primary_y": "" if box is None else _number(box.y),
                "primary_width": "" if box is None else _number(box.width),
                "primary_height": "" if box is None else _number(box.height),
                "primary_confidence": "" if primary is None else _number(primary.confidence),
                "crop_width": crop_width if crop_width is not None else "",
                "crop_height": crop_height if crop_height is not None else "",
                "status": analysis.status,
                "error_code": "" if analysis.status in {"ok", "no_detection"} else analysis.status,
            }
        )
    return rows


def _retrieval_rows(retrieval: RetrievalMetrics) -> list[dict[str, str | int | float]]:
    rows: list[dict[str, str | int | float]] = []
    for query in sorted(retrieval.queries, key=lambda entry: entry.filename):
        for rank, match in enumerate(query.matches, start=1):
            rows.append(
                {
                    "query_filename": query.filename,
                    "candidate_filename": match.filename,
                    "rank": rank,
                    "cosine_distance": _number(match.distance),
                    "same_group": str(match.same_group).lower(),
                    "query_participant_group": query.participant_group or "",
                    "candidate_participant_group": match.participant_group or "",
                }
            )
    return rows


def _face_size_buckets(analyses: Sequence[ImageAnalysis]) -> dict[str, int]:
    buckets = {"<32": 0, "32-63": 0, "64-127": 0, "128-255": 0, ">=256": 0}
    for analysis in analyses:
        for detection in analysis.detections:
            face_size = min(detection.bounding_box.width, detection.bounding_box.height)
            if face_size < 32:
                buckets["<32"] += 1
            elif face_size < 64:
                buckets["32-63"] += 1
            elif face_size < 128:
                buckets["64-127"] += 1
            elif face_size < 256:
                buckets["128-255"] += 1
            else:
                buckets[">=256"] += 1
    return buckets


def _write_diagnostic_images(annotated: Path, crops: Path, analysis: ImageAnalysis) -> None:
    preview_path = _asset_path(annotated, analysis.image.item.filename, ".jpg")
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    if analysis.image.rgb is None:
        raise ValueError("image pixels are required for diagnostic artifacts")
    image = Image.fromarray(analysis.image.rgb, mode="RGB")
    scale = min(1.0, _PREVIEW_LIMIT / max(image.width, image.height))
    if scale < 1.0:
        image = image.resize(
            (
                max(1, round(image.width * scale)),
                max(1, round(image.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
    draw = ImageDraw.Draw(image)
    for detection in analysis.detections:
        _draw_detection(draw, detection, scale)
    _save_image_atomic(preview_path, image, "JPEG", quality=90)

    if analysis.primary_detection is None:
        return
    crop = _crop_image(analysis)
    if crop is None:
        return
    crop_path = _asset_path(crops, analysis.image.item.filename, ".png")
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    _save_image_atomic(crop_path, crop, "PNG")


def _draw_detection(draw: ImageDraw.ImageDraw, detection: FaceDetection, scale: float) -> None:
    box = detection.bounding_box
    coordinates = (
        box.x * scale,
        box.y * scale,
        (box.x + box.width) * scale,
        (box.y + box.height) * scale,
    )
    color = "lime" if detection.primary else "orange"
    width = max(1, round(3 * scale))
    draw.rectangle(coordinates, outline=color, width=width)
    label = f"{detection.confidence:.3f}" + (" PRIMARY" if detection.primary else "")
    draw.text(
        (coordinates[0], max(0, coordinates[1] - 14)),
        label,
        fill=color,
        stroke_width=1,
        stroke_fill="black",
    )


def _crop_dimensions(analysis: ImageAnalysis) -> tuple[int | None, int | None]:
    bounds = _crop_bounds(analysis)
    if bounds is None:
        return (None, None)
    left, top, right, bottom = bounds
    return (right - left, bottom - top)


def _crop_image(analysis: ImageAnalysis) -> Image.Image | None:
    bounds = _crop_bounds(analysis)
    if bounds is None:
        return None
    if analysis.image.rgb is None:
        raise ValueError("image pixels are required for crop artifacts")
    left, top, right, bottom = bounds
    return Image.fromarray(analysis.image.rgb, mode="RGB").crop((left, top, right, bottom))


def _crop_bounds(analysis: ImageAnalysis) -> tuple[int, int, int, int] | None:
    if analysis.primary_detection is None:
        return None
    box = analysis.primary_detection.bounding_box
    left = max(0, floor(box.x))
    top = max(0, floor(box.y))
    right = min(analysis.image.width, ceil(box.x + box.width))
    bottom = min(analysis.image.height, ceil(box.y + box.height))
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _asset_path(root: Path, filename: str, suffix: str) -> Path:
    return root / asset_relative_path(filename, suffix)


def asset_relative_path(filename: str, suffix: str) -> Path:
    """Return a stable flat image filename that cannot escape its artifact directory."""
    if not suffix.startswith("."):
        raise ValueError("asset suffix must start with a dot")
    digest = hashlib.sha256(filename.encode("utf-8")).hexdigest()
    return Path(f"{digest}{suffix}")


def _save_image_atomic(path: Path, image: Image.Image, format_name: str, **options: Any) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            image.save(stream, format=format_name, **options)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_csv_atomic(
    path: Path, headers: Sequence[str], rows: Sequence[Mapping[str, str | int | float]]
) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\r\n")
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_text_atomic(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _sorted_analyses(analyses: Sequence[ImageAnalysis]) -> tuple[ImageAnalysis, ...]:
    return tuple(sorted(analyses, key=lambda analysis: analysis.image.item.filename))


def _number(value: float) -> str:
    return format(value, ".12g")

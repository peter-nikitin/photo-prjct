from __future__ import annotations

import csv
import errno
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

from .analysis import (
    DecodedImage,
    EventPhotoAnalysis,
    FaceInstance,
    face_crop_path,
)
from .clustering import FaceCluster, ordered_face_clusters
from .inventory import EventPhoto

_FACE_HEADERS = (
    "face_id",
    "filename",
    "face_index",
    "x",
    "y",
    "width",
    "height",
    "confidence",
    "status",
    "error_code",
    "crop_path",
)
_CLUSTER_HEADERS = (
    "cluster_id",
    "representative_face_id",
    "face_id",
    "filename",
    "face_index",
    "distance_to_representative",
)
_PREVIEW_LIMIT = 1920
_HARD_LINK_FALLBACK_ERRNOS = frozenset(
    error_number
    for name in ("EXDEV", "ENOTSUP", "EOPNOTSUPP", "ENOSYS", "EMLINK")
    if (error_number := getattr(errno, name, None)) is not None
)


@dataclass(frozen=True)
class ClusterRunResult:
    photos: Path
    yunet_model: Path
    sface_model: Path
    parameters: Mapping[str, str | int | float | None]
    analyses: Sequence[EventPhotoAnalysis]
    clusters: Sequence[FaceCluster]
    started_at: datetime
    finished_at: datetime
    durations: Mapping[str, float]
    dependency_versions: Mapping[str, str]


class ClusterArtifactWriter:
    """Stream diagnostics to hidden staging, then atomically publish a complete run."""

    def __init__(self, output: Path, photos: Path) -> None:
        if os.path.lexists(output):
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.output = output
        self.photos = photos.resolve()
        self._staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
        self._annotated = self._staging / "annotated"
        self._all_faces = self._staging / "faces"
        try:
            self._annotated.mkdir()
            self._all_faces.mkdir()
        except BaseException as failure:
            abort_preserving_exception(self, failure)
            raise

    def write_diagnostics(
        self,
        photo: EventPhoto,
        decoded: DecodedImage,
        analysis: EventPhotoAnalysis,
    ) -> None:
        try:
            if photo.filename != analysis.filename:
                raise ValueError("diagnostic photo and analysis filenames differ")
            _validate_analysis_contract((analysis,), self.photos, self._staging)
            image = Image.fromarray(decoded.rgb)
            preview = image.copy()
            scale = min(1.0, _PREVIEW_LIMIT / max(preview.width, preview.height))
            if scale < 1.0:
                preview = preview.resize(
                    (
                        max(1, round(preview.width * scale)),
                        max(1, round(preview.height * scale)),
                    ),
                    Image.Resampling.LANCZOS,
                )
            draw = ImageDraw.Draw(preview)
            for face in sorted(analysis.faces, key=lambda item: item.face_index):
                box = face.detection.bounding_box
                coordinates = (
                    box.x * scale,
                    box.y * scale,
                    (box.x + box.width) * scale,
                    (box.y + box.height) * scale,
                )
                draw.rectangle(coordinates, outline="lime", width=max(1, round(3 * scale)))
                draw.text(
                    (coordinates[0], max(0, coordinates[1] - 14)),
                    f"face-{face.face_index:03d} {face.detection.confidence:.3f}",
                    fill="lime",
                    stroke_width=1,
                    stroke_fill="black",
                )
            _save_image_atomic(
                self._annotated / annotated_asset_name(photo.filename),
                preview,
                "JPEG",
                quality=90,
            )
            for face in sorted(analysis.faces, key=lambda item: item.face_index):
                bounds = _crop_bounds(face, decoded.width, decoded.height)
                if bounds is None:
                    raise ValueError(f"face crop is empty: {face.face_id}")
                _save_image_atomic(
                    self._staging / face.crop_path,
                    image.crop(bounds),
                    "PNG",
                )
        except BaseException as failure:
            abort_preserving_exception(self, failure)
            raise

    def finish(self, result: ClusterRunResult) -> None:
        try:
            if result.photos.resolve() != self.photos:
                raise ValueError("result photo root differs from artifact writer photo root")
            analyses = tuple(sorted(result.analyses, key=lambda item: item.filename))
            try:
                clusters = ordered_face_clusters(result.clusters)
            except ValueError:
                raise ValueError(
                    "cluster IDs violate artifact contract: invalid person-NNNN ID"
                ) from None
            _validate_artifact_contract(analyses, clusters, self.photos, self._staging)
            face_by_id = {face.face_id: face for analysis in analyses for face in analysis.faces}
            _validate_cluster_membership(face_by_id, clusters)
            materialization = self._materialize_people(face_by_id, clusters)
            metrics = _build_metrics(analyses, clusters)
            _write_csv_atomic(self._staging / "faces.csv", _FACE_HEADERS, _face_rows(analyses))
            _write_json_atomic(self._staging / "faces.json", _faces_json(analyses))
            _write_csv_atomic(
                self._staging / "clusters.csv",
                _CLUSTER_HEADERS,
                _cluster_rows(face_by_id, clusters),
            )
            _write_json_atomic(
                self._staging / "clusters.json", _clusters_json(face_by_id, clusters)
            )
            _write_json_atomic(self._staging / "metrics.json", metrics)
            _write_json_atomic(
                self._staging / "manifest.json",
                _manifest(result, metrics, materialization),
            )
            from .cluster_report import render_cluster_report

            _write_text_atomic(
                self._staging / "report.html",
                render_cluster_report(analyses, clusters, metrics),
            )
            if os.path.lexists(self.output):
                raise FileExistsError(self.output)
            os.replace(self._staging, self.output)
        except BaseException as failure:
            abort_preserving_exception(self, failure)
            raise

    def abort(self) -> None:
        if self._staging.exists():
            shutil.rmtree(self._staging)

    def _materialize_people(
        self,
        face_by_id: Mapping[str, FaceInstance],
        clusters: Sequence[FaceCluster],
    ) -> dict[str, int]:
        counts = {"copy": 0, "hard_link": 0}
        people = self._staging / "people"
        people.mkdir()
        for cluster in clusters:
            face_dir = people / cluster.cluster_id / "faces"
            photo_dir = people / cluster.cluster_id / "photos"
            face_dir.mkdir(parents=True)
            photo_dir.mkdir()
            filenames: set[str] = set()
            for member in sorted(cluster.members, key=lambda item: item.face_id):
                face = face_by_id[member.face_id]
                filenames.add(face.filename)
                shutil.copyfile(
                    self._staging / face.crop_path,
                    face_dir / Path(face.crop_path).name,
                )
            for filename in sorted(filenames):
                source = self.photos / filename
                destination = photo_dir / filename
                try:
                    os.link(source, destination)
                    counts["hard_link"] += 1
                except OSError as error:
                    if error.errno not in _HARD_LINK_FALLBACK_ERRNOS:
                        raise
                    shutil.copyfile(source, destination)
                    counts["copy"] += 1
        return counts


def abort_preserving_exception(
    writer: ClusterArtifactWriter,
    failure: BaseException,
) -> None:
    """Abort staging without replacing the failure that triggered cleanup."""
    try:
        writer.abort()
    except BaseException as cleanup_error:
        note = (
            "cluster artifact staging cleanup failed: "
            f"{type(cleanup_error).__name__}: {cleanup_error}"
        )
        if note not in getattr(failure, "__notes__", ()):
            failure.add_note(note)


def face_asset_name(face_id: str) -> str:
    return Path(face_crop_path(face_id)).name


def annotated_asset_name(filename: str) -> str:
    return f"{hashlib.sha256(filename.encode('utf-8')).hexdigest()}.jpg"


def _validate_artifact_contract(
    analyses: Sequence[EventPhotoAnalysis],
    clusters: Sequence[FaceCluster],
    photo_root: Path,
    artifact_root: Path,
) -> None:
    _validate_analysis_contract(analyses, photo_root, artifact_root)
    expected_cluster_ids = tuple(f"person-{number:04d}" for number in range(1, len(clusters) + 1))
    actual_cluster_ids = tuple(cluster.cluster_id for cluster in clusters)
    if actual_cluster_ids != expected_cluster_ids:
        raise ValueError(
            "cluster IDs violate artifact contract: expected contiguous person-NNNN IDs"
        )


def _validate_analysis_contract(
    analyses: Sequence[EventPhotoAnalysis],
    photo_root: Path,
    artifact_root: Path,
) -> None:
    filenames = [analysis.filename for analysis in analyses]
    if len(set(filenames)) != len(filenames):
        raise ValueError("image filenames violate artifact contract: duplicates")
    face_ids: list[str] = []
    for analysis in analyses:
        if not _is_safe_direct_basename(analysis.filename, photo_root):
            raise ValueError("image filename violates artifact contract: unsafe basename")
        expected_indices = tuple(range(1, len(analysis.faces) + 1))
        actual_indices = tuple(face.face_index for face in analysis.faces)
        if actual_indices != expected_indices:
            raise ValueError("face indices violate artifact contract: expected one-based order")
        for face in analysis.faces:
            expected_face_id = f"{analysis.filename}#face-{face.face_index:03d}"
            if (
                face.filename != analysis.filename
                or not _is_safe_direct_basename(face.filename, photo_root)
                or face.face_id != expected_face_id
                or face.crop_path != face_crop_path(face.face_id)
                or not _is_contained_crop_path(face.crop_path, artifact_root)
            ):
                raise ValueError("face filename, ID, or crop path violates artifact contract")
            face_ids.append(face.face_id)
    if len(set(face_ids)) != len(face_ids):
        raise ValueError("face IDs violate artifact contract: duplicates")


def _is_safe_direct_basename(filename: str, root: Path) -> bool:
    if not filename or "\x00" in filename or filename in {".", ".."}:
        return False
    supplied = Path(filename)
    if supplied.is_absolute() or supplied.name != filename:
        return False
    try:
        return (root / supplied).parent.resolve() == root.resolve()
    except OSError:
        return False


def _is_contained_crop_path(crop_path: str, artifact_root: Path) -> bool:
    if not crop_path or "\x00" in crop_path:
        return False
    supplied = Path(crop_path)
    if supplied.is_absolute() or supplied.parts[:1] != ("faces",) or len(supplied.parts) != 2:
        return False
    try:
        return (artifact_root / supplied).parent.resolve() == (artifact_root / "faces").resolve()
    except OSError:
        return False


def _validate_cluster_membership(
    face_by_id: Mapping[str, FaceInstance],
    clusters: Sequence[FaceCluster],
) -> None:
    successful = {
        face_id
        for face_id, face in face_by_id.items()
        if face.status == "ok" and face.embedding is not None
    }
    member_ids = [member.face_id for cluster in clusters for member in cluster.members]
    if len(set(member_ids)) != len(member_ids) or set(member_ids) != successful:
        raise ValueError("clusters must contain every successful face exactly once")
    if len({cluster.cluster_id for cluster in clusters}) != len(clusters):
        raise ValueError("cluster IDs must be unique")
    for cluster in clusters:
        if cluster.representative_face_id not in {member.face_id for member in cluster.members}:
            raise ValueError("cluster representative must be a member")


def _face_rows(analyses: Sequence[EventPhotoAnalysis]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for analysis in analyses:
        for face in sorted(analysis.faces, key=lambda item: item.face_index):
            box = face.detection.bounding_box
            rows.append(
                {
                    "face_id": face.face_id,
                    "filename": face.filename,
                    "face_index": face.face_index,
                    "x": _number(box.x),
                    "y": _number(box.y),
                    "width": _number(box.width),
                    "height": _number(box.height),
                    "confidence": _number(face.detection.confidence),
                    "status": face.status,
                    "error_code": "" if face.status == "ok" else face.status,
                    "crop_path": face.crop_path,
                }
            )
    return rows


def _face_record(face: FaceInstance) -> dict[str, Any]:
    box = face.detection.bounding_box
    landmarks = face.detection.landmarks
    return {
        "confidence": float(face.detection.confidence),
        "crop_path": face.crop_path,
        "error_code": "" if face.status == "ok" else face.status,
        "face_id": face.face_id,
        "face_index": face.face_index,
        "filename": face.filename,
        "height": float(box.height),
        "landmarks": {
            "left_eye": list(landmarks.left_eye),
            "left_mouth_corner": list(landmarks.left_mouth_corner),
            "nose": list(landmarks.nose),
            "right_eye": list(landmarks.right_eye),
            "right_mouth_corner": list(landmarks.right_mouth_corner),
        },
        "status": face.status,
        "width": float(box.width),
        "x": float(box.x),
        "y": float(box.y),
    }


def _faces_json(analyses: Sequence[EventPhotoAnalysis]) -> dict[str, Any]:
    return {
        "images": [
            {
                "faces": [
                    _face_record(face)
                    for face in sorted(analysis.faces, key=lambda item: item.face_index)
                ],
                "filename": analysis.filename,
                "height": analysis.height,
                "status": analysis.status,
                "width": analysis.width,
            }
            for analysis in analyses
        ]
    }


def _cluster_rows(
    face_by_id: Mapping[str, FaceInstance],
    clusters: Sequence[FaceCluster],
) -> list[dict[str, Any]]:
    return [
        {
            "cluster_id": cluster.cluster_id,
            "representative_face_id": cluster.representative_face_id,
            "face_id": member.face_id,
            "filename": face_by_id[member.face_id].filename,
            "face_index": face_by_id[member.face_id].face_index,
            "distance_to_representative": _number(member.distance_to_representative),
        }
        for cluster in clusters
        for member in sorted(cluster.members, key=lambda item: item.face_id)
    ]


def _clusters_json(
    face_by_id: Mapping[str, FaceInstance],
    clusters: Sequence[FaceCluster],
) -> dict[str, Any]:
    return {
        "clusters": [
            {
                "cluster_id": cluster.cluster_id,
                "members": [
                    {
                        "distance_to_representative": float(member.distance_to_representative),
                        "face_id": member.face_id,
                        "face_index": face_by_id[member.face_id].face_index,
                        "filename": face_by_id[member.face_id].filename,
                    }
                    for member in sorted(cluster.members, key=lambda item: item.face_id)
                ],
                "representative_face_id": cluster.representative_face_id,
            }
            for cluster in clusters
        ]
    }


def _build_metrics(
    analyses: Sequence[EventPhotoAnalysis], clusters: Sequence[FaceCluster]
) -> dict[str, Any]:
    faces = [face for analysis in analyses for face in analysis.faces]
    face_errors = Counter(face.status for face in faces if face.status != "ok")
    image_errors = Counter(
        analysis.status for analysis in analyses if analysis.status not in {"ok", "no_detection"}
    )
    sizes = Counter(len(cluster.members) for cluster in clusters)
    return {
        "cluster_size_distribution": {str(size): sizes[size] for size in sorted(sizes)},
        "counts": {
            "clusters": len(clusters),
            "embedding_success": sum(
                face.status == "ok" and face.embedding is not None for face in faces
            ),
            "face_instances": len(faces),
            "images": len(analyses),
            "singleton_clusters": sizes[1],
        },
        "face_error_counts": {status: face_errors[status] for status in sorted(face_errors)},
        "image_error_counts": {status: image_errors[status] for status in sorted(image_errors)},
    }


def _manifest(
    result: ClusterRunResult,
    metrics: Mapping[str, Any],
    materialization: Mapping[str, int],
) -> dict[str, Any]:
    parameters = {key: result.parameters[key] for key in sorted(result.parameters)}
    parameters.update(
        {
            "input_photos_basename": result.photos.name,
            "sface_model_filename": result.sface_model.name,
            "yunet_model_filename": result.yunet_model.name,
        }
    )
    return {
        "counts": metrics["counts"],
        "dependency_versions": {
            key: result.dependency_versions[key] for key in sorted(result.dependency_versions)
        },
        "duration_seconds": (result.finished_at - result.started_at).total_seconds(),
        "durations_seconds": {key: result.durations[key] for key in sorted(result.durations)},
        "finished_at": _timestamp(result.finished_at),
        "model_hashes": {
            "sface": _sha256(result.sface_model),
            "yunet": _sha256(result.yunet_model),
        },
        "parameters": parameters,
        "photo_materialization": {
            "copy": materialization["copy"],
            "hard_link": materialization["hard_link"],
        },
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "started_at": _timestamp(result.started_at),
    }


def _crop_bounds(
    face: FaceInstance, image_width: int, image_height: int
) -> tuple[int, int, int, int] | None:
    box = face.detection.bounding_box
    left = max(0, floor(box.x))
    top = max(0, floor(box.y))
    right = min(image_width, ceil(box.x + box.width))
    bottom = min(image_height, ceil(box.y + box.height))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


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
    path: Path, headers: Sequence[str], rows: Sequence[Mapping[str, Any]]
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _number(value: float) -> str:
    return format(value, ".12g")

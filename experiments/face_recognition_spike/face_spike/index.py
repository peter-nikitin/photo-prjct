from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from numpy.typing import NDArray

from .analysis import (
    BoundingBox,
    FaceDetector,
    FaceRecognizer,
    ImageDecoder,
    analyze_decoded_event_photo,
)
from .inventory import EventPhotoInventory
from .quality import FaceQuality, FaceQualityThresholds

if TYPE_CHECKING:
    from .index_artifacts import FaceIndexManifest


@dataclass(frozen=True)
class SourceFaceRecord:
    face_id: str
    filename: str
    face_index: int
    bounding_box: BoundingBox
    crop_path: str
    status: str


@dataclass(frozen=True)
class FaceIndexEntry:
    face_id: str
    filename: str
    face_index: int
    bounding_box: BoundingBox
    crop_path: str
    quality: FaceQuality


@dataclass(frozen=True, eq=False)
class FaceIndex:
    entries: tuple[FaceIndexEntry, ...]
    embeddings: NDArray[np.float32]
    manifest: FaceIndexManifest

    def __post_init__(self) -> None:
        _validate_entries(self.entries)
        embeddings = _normalized_float32_matrix(self.embeddings)
        if embeddings.shape[0] != len(self.entries):
            raise ValueError("entry count does not match embedding rows")
        object.__setattr__(self, "embeddings", embeddings)
        object.__setattr__(self, "manifest", self.manifest.copy())


def build_face_index(
    source_faces: tuple[SourceFaceRecord, ...],
    inventory: EventPhotoInventory,
    decoder: ImageDecoder,
    detector: FaceDetector,
    recognizer: FaceRecognizer,
    *,
    quality_thresholds: FaceQualityThresholds,
    manifest: FaceIndexManifest,
) -> FaceIndex:
    """Reprocess a source run and bind accepted source faces to compact vectors."""
    quality_thresholds.validate()
    required = _required_source_faces(source_faces)
    inventory_names = [photo.filename for photo in inventory.photos]
    if len(inventory_names) != len(set(inventory_names)):
        raise ValueError("inventory filenames must be unique")

    entries: list[FaceIndexEntry] = []
    vectors: list[NDArray[np.float32]] = []
    matched: set[tuple[str, int]] = set()
    for photo in inventory.photos:
        try:
            decoded = decoder.decode(photo)
        except Exception:
            continue
        try:
            analysis = analyze_decoded_event_photo(
                photo,
                decoded,
                detector,
                recognizer,
                quality_thresholds=quality_thresholds,
            )
            for face in analysis.faces:
                key = (face.filename, face.face_index)
                source = required.get(key)
                if source is None:
                    if face.status == "ok" and face.embedding is not None:
                        raise ValueError("unexpected detected face")
                    continue
                _validate_reconciliation(
                    source, face.face_id, face.detection.bounding_box, face.crop_path
                )
                if face.status != "ok" or face.embedding is None:
                    raise ValueError("required source face failed during reprocessing")
                matched.add(key)
                entries.append(
                    FaceIndexEntry(
                        source.face_id,
                        source.filename,
                        source.face_index,
                        source.bounding_box,
                        source.crop_path,
                        face.quality,
                    )
                )
                vectors.append(face.embedding.vector)
        finally:
            del decoded

    missing = sorted(set(required) - matched)
    if missing:
        raise ValueError("missing required source face")
    order = np.argsort(np.asarray([entry.face_id for entry in entries], dtype=str), kind="stable")
    ordered_entries = tuple(entries[int(index)] for index in order)
    if vectors:
        matrix = np.ascontiguousarray(
            np.vstack([vectors[int(index)] for index in order]), dtype=np.float32
        )
    else:
        matrix = np.empty((0, 0), dtype=np.float32)
    return FaceIndex(ordered_entries, matrix, manifest)


def _required_source_faces(
    source_faces: Sequence[SourceFaceRecord],
) -> dict[tuple[str, int], SourceFaceRecord]:
    required_sources = tuple(source for source in source_faces if source.status == "ok")
    identifiers = [source.face_id for source in required_sources]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate source face ID")
    required: dict[tuple[str, int], SourceFaceRecord] = {}
    for source in required_sources:
        _validate_source_face(source)
        key = (source.filename, source.face_index)
        if key in required:
            raise ValueError("duplicate required source face")
        required[key] = source
    return required


def _validate_source_face(source: SourceFaceRecord) -> None:
    expected_id = f"{source.filename}#face-{source.face_index:03d}"
    if (
        not source.filename
        or source.face_index < 1
        or source.face_id != expected_id
        or not _is_relative_path(source.crop_path)
    ):
        raise ValueError("invalid source face record")


def _validate_reconciliation(
    source: SourceFaceRecord,
    face_id: str,
    bounding_box: BoundingBox,
    crop_path: str,
) -> None:
    if face_id != source.face_id or crop_path != source.crop_path:
        raise ValueError("source face identity differs")
    if bounding_box != source.bounding_box:
        raise ValueError("source geometry differs")


def _validate_entries(entries: Sequence[FaceIndexEntry]) -> None:
    face_ids = [entry.face_id for entry in entries]
    if len(face_ids) != len(set(face_ids)):
        raise ValueError("face IDs must be unique")
    if face_ids != sorted(face_ids):
        raise ValueError("face IDs must be ordered")
    for entry in entries:
        expected_id = f"{entry.filename}#face-{entry.face_index:03d}"
        if (
            not entry.filename
            or entry.face_index < 1
            or entry.face_id != expected_id
            or not _is_relative_path(entry.crop_path)
            or entry.quality.decision != "accepted"
        ):
            raise ValueError("invalid index entry")


def _is_relative_path(value: str) -> bool:
    path = Path(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\x00" not in value


def _normalized_float32_matrix(value: NDArray[np.float32]) -> NDArray[np.float32]:
    array = np.asarray(value)
    if array.dtype == object:
        raise ValueError("object embedding arrays are not supported")
    if array.ndim != 2:
        raise ValueError("embeddings must be a two-dimensional matrix")
    if array.shape[0] and array.shape[1] == 0:
        raise ValueError("embeddings must be nonempty")
    if not np.issubdtype(array.dtype, np.floating):
        raise ValueError("embeddings must be floating-point")
    matrix = np.ascontiguousarray(array, dtype=np.float32)
    if not np.isfinite(matrix).all():
        raise ValueError("embeddings must be finite")
    if matrix.shape[0]:
        norms = np.linalg.norm(matrix, axis=1)
        if not np.all(np.isfinite(norms)) or not np.allclose(norms, 1.0, rtol=1e-5, atol=1e-6):
            raise ValueError("embeddings must be normalized")
    return matrix

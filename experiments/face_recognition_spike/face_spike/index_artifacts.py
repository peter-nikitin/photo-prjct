from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING

import numpy as np

from .analysis import BoundingBox
from .quality import FaceQuality

if TYPE_CHECKING:
    from .index import FaceIndex, FaceIndexEntry

_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MODEL_KEYS = frozenset({"basename", "size", "sha256"})


@dataclass(frozen=True)
class FaceIndexManifest:
    source_run_manifest_sha256: str
    source_faces_sha256: str
    yunet_model: Mapping[str, object]
    sface_model: Mapping[str, object]
    parameters: Mapping[str, object]
    dependency_versions: Mapping[str, str]
    entry_count: int
    embedding_dimension: int
    created_at: str

    def __post_init__(self) -> None:
        _validate_manifest(self)

    def copy(self) -> FaceIndexManifest:
        return FaceIndexManifest.from_dict(self.to_dict())

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "source_run_manifest_sha256": self.source_run_manifest_sha256,
            "source_faces_sha256": self.source_faces_sha256,
            "yunet_model": dict(self.yunet_model),
            "sface_model": dict(self.sface_model),
            "parameters": _json_copy(self.parameters),
            "dependency_versions": dict(self.dependency_versions),
            "entry_count": self.entry_count,
            "embedding_dimension": self.embedding_dimension,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> FaceIndexManifest:
        expected = {
            "schema_version",
            "source_run_manifest_sha256",
            "source_faces_sha256",
            "yunet_model",
            "sface_model",
            "parameters",
            "dependency_versions",
            "entry_count",
            "embedding_dimension",
            "created_at",
        }
        if set(value) != expected or value.get("schema_version") != _SCHEMA_VERSION:
            raise ValueError("invalid index manifest schema")
        try:
            return cls(
                source_run_manifest_sha256=_string(value["source_run_manifest_sha256"]),
                source_faces_sha256=_string(value["source_faces_sha256"]),
                yunet_model=_mapping(value["yunet_model"]),
                sface_model=_mapping(value["sface_model"]),
                parameters=_mapping(value["parameters"]),
                dependency_versions=_string_mapping(value["dependency_versions"]),
                entry_count=_integer(value["entry_count"]),
                embedding_dimension=_integer(value["embedding_dimension"]),
                created_at=_string(value["created_at"]),
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid index manifest schema") from None


class FaceIndexArtifactWriter:
    """Publish one complete private index through a hidden sibling staging directory."""

    def __init__(self, output: Path) -> None:
        if os.path.lexists(output):
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.output = output
        self._staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))

    def finish(self, index: FaceIndex) -> None:
        try:
            from .index import FaceIndex

            if not isinstance(index, FaceIndex):
                raise TypeError("index must be a FaceIndex")
            _validate_index(index)
            _write_json_atomic(
                self._staging / "manifest.json", _manifest_for_index(index).to_dict()
            )
            _write_json_atomic(
                self._staging / "faces.json",
                [_entry_to_dict(entry) for entry in index.entries],
            )
            _write_embeddings_atomic(self._staging / "embeddings.npz", index.embeddings)
            if os.path.lexists(self.output):
                raise FileExistsError(self.output)
            os.replace(self._staging, self.output)
        except BaseException as failure:
            _abort_preserving_exception(self, failure)
            raise

    def abort(self) -> None:
        if self._staging.exists():
            shutil.rmtree(self._staging)


def load_face_index(path: Path) -> FaceIndex:
    """Load a complete private index only after strict cross-file validation."""
    from .index import FaceIndex

    root = path.resolve()
    if not root.is_dir():
        raise ValueError("index artifact is not a directory")
    expected = {"manifest.json", "faces.json", "embeddings.npz"}
    if {child.name for child in root.iterdir()} != expected:
        raise ValueError("index artifact files do not match schema")
    manifest = FaceIndexManifest.from_dict(_load_json_object(root / "manifest.json"))
    raw_entries = _load_json_array(root / "faces.json")
    entries = tuple(_entry_from_dict(item) for item in raw_entries)
    embeddings = _load_embeddings(root / "embeddings.npz")
    if len(entries) != manifest.entry_count or embeddings.shape[0] != manifest.entry_count:
        raise ValueError("entry count does not reconcile")
    dimension = embeddings.shape[1] if embeddings.ndim == 2 else -1
    if dimension != manifest.embedding_dimension:
        raise ValueError("embedding dimension does not reconcile")
    return FaceIndex(entries, embeddings, manifest)


def _validate_manifest(manifest: FaceIndexManifest) -> None:
    if not _SHA256.fullmatch(manifest.source_run_manifest_sha256) or not _SHA256.fullmatch(
        manifest.source_faces_sha256
    ):
        raise ValueError("manifest hashes must be lowercase SHA-256")
    _validate_model(manifest.yunet_model)
    _validate_model(manifest.sface_model)
    _json_copy(manifest.parameters)
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in manifest.dependency_versions.items()
    ):
        raise ValueError("dependency versions must be string values")
    if isinstance(manifest.entry_count, bool) or manifest.entry_count < 0:
        raise ValueError("entry count must be nonnegative")
    if isinstance(manifest.embedding_dimension, bool) or manifest.embedding_dimension < 0:
        raise ValueError("embedding dimension must be nonnegative")
    if manifest.entry_count == 0 and manifest.embedding_dimension != 0:
        raise ValueError("empty index must have zero embedding dimension")
    if manifest.entry_count > 0 and manifest.embedding_dimension <= 0:
        raise ValueError("nonempty index requires embedding dimension")
    try:
        parsed = datetime.fromisoformat(manifest.created_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("created_at must be UTC ISO-8601") from None
    if (
        not manifest.created_at.endswith("Z")
        or parsed.tzinfo is None
        or parsed.utcoffset() != UTC.utcoffset(parsed)
    ):
        raise ValueError("created_at must be UTC ISO-8601")


def _validate_model(model: Mapping[str, object]) -> None:
    if set(model) != _MODEL_KEYS:
        raise ValueError("model schema is invalid")
    basename = model["basename"]
    if (
        not isinstance(basename, str)
        or not basename
        or Path(basename).name != basename
        or PureWindowsPath(basename).drive
        or "/" in basename
        or "\\" in basename
        or basename in {".", ".."}
        or isinstance(model["size"], bool)
        or not isinstance(model["size"], int)
        or model["size"] < 0
        or not isinstance(model["sha256"], str)
        or not _SHA256.fullmatch(model["sha256"])
    ):
        raise ValueError("model schema is invalid")


def _manifest_for_index(index: FaceIndex) -> FaceIndexManifest:
    dimension = index.embeddings.shape[1] if index.entries else 0
    values = index.manifest.to_dict()
    values["entry_count"] = len(index.entries)
    values["embedding_dimension"] = dimension
    return FaceIndexManifest.from_dict(values)


def _validate_index(index: FaceIndex) -> None:
    if index.embeddings.dtype != np.float32 or not index.embeddings.flags.c_contiguous:
        raise ValueError("embeddings must be C-contiguous float32")
    if index.embeddings.dtype == object:
        raise ValueError("object embedding arrays are not supported")
    if not np.isfinite(index.embeddings).all():
        raise ValueError("embeddings must be finite")


def _entry_to_dict(entry: FaceIndexEntry) -> dict[str, object]:
    return {
        "face_id": entry.face_id,
        "filename": entry.filename,
        "face_index": entry.face_index,
        "bounding_box": {
            "x": entry.bounding_box.x,
            "y": entry.bounding_box.y,
            "width": entry.bounding_box.width,
            "height": entry.bounding_box.height,
        },
        "crop_path": entry.crop_path,
        "quality": {
            "confidence": entry.quality.confidence,
            "minimum_side_px": entry.quality.minimum_side_px,
            "relative_area": entry.quality.relative_area,
            "sharpness": entry.quality.sharpness,
            "decision": entry.quality.decision,
            "reasons": list(entry.quality.reasons),
        },
    }


def _entry_from_dict(value: object) -> FaceIndexEntry:
    from .index import FaceIndexEntry

    item = _mapping(value)
    expected = {"face_id", "filename", "face_index", "bounding_box", "crop_path", "quality"}
    if set(item) != expected:
        raise ValueError("face entry schema is invalid")
    box = _mapping(item["bounding_box"])
    quality = _mapping(item["quality"])
    if set(box) != {"x", "y", "width", "height"} or set(quality) != {
        "confidence",
        "minimum_side_px",
        "relative_area",
        "sharpness",
        "decision",
        "reasons",
    }:
        raise ValueError("face entry schema is invalid")
    try:
        decision = _string(quality["decision"])
        reasons = _sequence(quality["reasons"])
        if decision != "accepted" or reasons:
            raise ValueError("index face quality must be accepted")
        return FaceIndexEntry(
            _string(item["face_id"]),
            _string(item["filename"]),
            _integer(item["face_index"]),
            BoundingBox(*(_number(box[name]) for name in ("x", "y", "width", "height"))),
            _string(item["crop_path"]),
            FaceQuality(
                _number(quality["confidence"]),
                _number(quality["minimum_side_px"]),
                _number(quality["relative_area"]),
                _number(quality["sharpness"]),
                "accepted",
                (),
            ),
        )
    except (TypeError, ValueError):
        raise ValueError("face entry schema is invalid") from None


def _load_embeddings(path: Path) -> np.ndarray:
    try:
        with np.load(path, allow_pickle=False) as archive:
            if archive.files != ["embeddings"]:
                raise ValueError("archive must contain exactly embeddings")
            try:
                embeddings = archive["embeddings"]
            except ValueError as error:
                if "Object arrays" in str(error):
                    raise ValueError("object embedding arrays are not supported") from None
                raise
    except ValueError:
        raise
    except Exception:
        raise ValueError("invalid embeddings archive") from None
    if embeddings.dtype == object:
        raise ValueError("object embedding arrays are not supported")
    if embeddings.dtype != np.float32 or embeddings.ndim != 2 or not embeddings.flags.c_contiguous:
        raise ValueError("embeddings must be C-contiguous float32")
    if not np.isfinite(embeddings).all():
        raise ValueError("embeddings must be finite")
    if embeddings.shape[0] and embeddings.shape[1] == 0:
        raise ValueError("embeddings must be nonempty")
    if embeddings.shape[0] and not np.allclose(
        np.linalg.norm(embeddings, axis=1), 1.0, rtol=1e-5, atol=1e-6
    ):
        raise ValueError("embeddings must be normalized")
    return embeddings


def _write_embeddings_atomic(path: Path, embeddings: np.ndarray) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            np.savez(stream, embeddings=embeddings)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _write_json_atomic(path: Path, value: object) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _abort_preserving_exception(writer: FaceIndexArtifactWriter, failure: BaseException) -> None:
    try:
        writer.abort()
    except BaseException as cleanup_error:
        note = "index artifact staging cleanup failed: "
        note += f"{type(cleanup_error).__name__}: {cleanup_error}"
        failure.add_note(note)


def _load_json_object(path: Path) -> Mapping[str, object]:
    return _mapping(_load_json(path))


def _load_json_array(path: Path) -> list[object]:
    value = _load_json(path)
    if not isinstance(value, list):
        raise ValueError("faces schema is invalid")
    return value


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("invalid index JSON") from None


def _json_copy(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("parameters must be a JSON object")
    try:
        copied = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        raise ValueError("parameters must be JSON-safe") from None
    if not isinstance(copied, dict):
        raise ValueError("parameters must be a JSON object")
    return copied


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("expected JSON object")
    return value


def _string_mapping(value: object) -> Mapping[str, str]:
    mapping = _mapping(value)
    if any(not isinstance(item, str) for item in mapping.values()):
        raise ValueError("expected string mapping")
    return {key: item for key, item in mapping.items() if isinstance(item, str)}


def _sequence(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected JSON array")
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected integer")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError("expected finite number")
    return float(value)

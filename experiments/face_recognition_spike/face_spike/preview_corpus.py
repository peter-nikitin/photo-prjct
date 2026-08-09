"""Materialize a verified local preview-small-v1 corpus without application state."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from photo_worker.contracts import (
    MAX_PREVIEW_PIXELS_CAP,
    PREVIEW_CONTRACT_VERSION,
    PROCESSOR_TYPE_GENERATE_PREVIEW,
    PROCESSOR_VERSION_GENERATE_PREVIEW,
    V2_GENERATE_PREVIEW_CONFIGURATION,
    OutputSlot,
)
from photo_worker.preview import PreviewError, generate_preview
from PIL import Image, UnidentifiedImageError

_PHOTO_ID = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SCHEMA_VERSION = 1
_FINALIZATION_ID = "<finalization>"
_PREVIEW_CONFIGURATION = V2_GENERATE_PREVIEW_CONFIGURATION["generate_preview"]
_WORKER_CONFIGURATION = V2_GENERATE_PREVIEW_CONFIGURATION["worker"]


class PreviewCorpusError(ValueError):
    """The source or published corpus cannot safely support local profiling."""


@dataclass(frozen=True)
class PreviewCorpusPhoto:
    photo_id: str
    source_filename: str
    source_sha256: str
    source_byte_size: int
    preview_filename: str
    byte_size: int
    width: int
    height: int
    oriented_source_width: int
    oriented_source_height: int
    sha256: str
    warnings: tuple[str, ...]

    def as_payload(self) -> dict[str, object]:
        return {
            "photo_id": self.photo_id,
            "source_filename": self.source_filename,
            "source_sha256": self.source_sha256,
            "source_byte_size": self.source_byte_size,
            "preview_filename": self.preview_filename,
            "byte_size": self.byte_size,
            "width": self.width,
            "height": self.height,
            "oriented_source_width": self.oriented_source_width,
            "oriented_source_height": self.oriented_source_height,
            "sha256": self.sha256,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PreviewCorpusManifest:
    source_manifest_sha256: str
    event: Mapping[str, str]
    preview_contract: Mapping[str, object]
    production_contract_sha256: str
    photos: tuple[PreviewCorpusPhoto, ...]
    unresolved: tuple[Mapping[str, str], ...]
    complete: bool
    manifest_sha256: str
    generated: int = 0
    reused: int = 0

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "artifact_type": "preview-corpus",
            "source_manifest_sha256": self.source_manifest_sha256,
            "event": dict(self.event),
            "preview_contract": dict(self.preview_contract),
            "production_contract_sha256": self.production_contract_sha256,
            "photos": [photo.as_payload() for photo in self.photos],
            "unresolved": [dict(item) for item in self.unresolved],
            "complete": self.complete,
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True)
class _SourcePhoto:
    photo_id: str
    filename: str
    sha256: str
    byte_size: int
    path: Path


@dataclass(frozen=True)
class _SourceCorpus:
    manifest_sha256: str
    event: Mapping[str, str]
    photos: tuple[_SourcePhoto, ...]


def materialize_preview_corpus(
    source_manifest: Path, originals: Path, output: Path, *, workers: int
) -> PreviewCorpusManifest:
    """Generate a complete corpus, or leave only an explicitly incomplete failure record."""
    if workers < 1:
        raise PreviewCorpusError("workers must be positive")
    source = _load_source(source_manifest, originals)
    contract = _preview_contract()
    existing_photos: tuple[PreviewCorpusPhoto, ...] = ()
    if os.path.lexists(output):
        if (output / "manifest.json").exists():
            if (output / "incomplete-manifest.json").exists():
                existing = _load_complete_with_stale_incomplete(output)
                _load_incomplete_preview_corpus(
                    output, source, contract, complete_manifest=existing
                )
                try:
                    (output / "incomplete-manifest.json").unlink()
                except OSError as error:
                    raise PreviewCorpusError("output corpus recovery failed") from error
            else:
                existing = load_verified_preview_corpus(output)
            if not _same_identity(existing, source, contract):
                raise PreviewCorpusError("output corpus identity does not match source corpus")
            return PreviewCorpusManifest(
                **{**existing.__dict__, "generated": 0, "reused": len(existing.photos)}
            )
        incomplete = _load_incomplete_preview_corpus(output, source, contract)
        existing_photos = incomplete.photos
    else:
        try:
            output.mkdir(parents=True, exist_ok=False)
        except OSError as error:
            raise PreviewCorpusError("output corpus cannot be created") from error

    completed_ids = {photo.photo_id for photo in existing_photos}
    pending = tuple(photo for photo in source.photos if photo.photo_id not in completed_ids)
    generated: list[PreviewCorpusPhoto] = []
    unresolved: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_generate_one, source_photo, output): source_photo.photo_id
            for source_photo in pending
        }
        for future in as_completed(futures):
            photo_id = futures[future]
            try:
                generated.append(future.result())
            except PreviewError as error:
                unresolved.append({"photo_id": photo_id, "error": error.code})
            except Exception:
                unresolved.append({"photo_id": photo_id, "error": "preview_generation_failed"})

    if unresolved:
        _write_incomplete(
            output,
            source,
            contract,
            tuple(sorted((*existing_photos, *generated), key=lambda photo: photo.photo_id)),
            unresolved,
        )
        raise PreviewCorpusError("preview generation failed; inspect failed-attempt evidence")

    photos = tuple(sorted((*existing_photos, *generated), key=lambda photo: photo.photo_id))
    manifest = _build_manifest(
        source,
        contract,
        photos,
        (),
        generated=len(generated),
        reused=len(existing_photos),
    )
    try:
        _verify_manifest_files(output, manifest)
        _write_json_atomic(output / "manifest.json", manifest.payload())
        (output / "incomplete-manifest.json").unlink(missing_ok=True)
    except Exception as error:
        if not (output / "manifest.json").exists():
            _write_terminal_incomplete(output, source, contract, photos)
        raise PreviewCorpusError("output corpus verification failed") from error
    return manifest


def load_verified_preview_corpus(output: Path) -> PreviewCorpusManifest:
    """Load a completed corpus only after replaying all published file evidence."""
    try:
        if output.is_symlink() or not output.is_dir():
            raise PreviewCorpusError("output corpus is invalid")
        manifest_path = output / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise PreviewCorpusError("output corpus is invalid")
        payload = _json_object(manifest_path.read_bytes(), "output corpus")
        manifest = _manifest_from_payload(payload)
        if not manifest.complete:
            raise PreviewCorpusError("output corpus manifest is incomplete")
        _verify_manifest_files(output, manifest)
        expected_names = {"manifest.json", *(photo.preview_filename for photo in manifest.photos)}
        actual_names = {entry.name for entry in output.iterdir()}
        if actual_names != expected_names:
            raise PreviewCorpusError("output corpus contains unexpected files")
        return manifest
    except PreviewCorpusError:
        raise
    except OSError as error:
        raise PreviewCorpusError("output corpus is invalid") from error


def _load_complete_with_stale_incomplete(output: Path) -> PreviewCorpusManifest:
    try:
        if output.is_symlink() or not output.is_dir():
            raise PreviewCorpusError("output corpus is invalid")
        path = output / "manifest.json"
        if path.is_symlink() or not path.is_file():
            raise PreviewCorpusError("output corpus is invalid")
        manifest = _manifest_from_payload(_json_object(path.read_bytes(), "output corpus"))
        if not manifest.complete:
            raise PreviewCorpusError("output corpus manifest is incomplete")
        _verify_manifest_files(output, manifest)
        expected_names = {
            "manifest.json",
            "incomplete-manifest.json",
            *(photo.preview_filename for photo in manifest.photos),
        }
        if {entry.name for entry in output.iterdir()} != expected_names:
            raise PreviewCorpusError("output corpus contains unexpected files")
        return manifest
    except PreviewCorpusError:
        raise
    except OSError as error:
        raise PreviewCorpusError("output corpus is invalid") from error


def _load_incomplete_preview_corpus(
    output: Path,
    source: _SourceCorpus,
    contract: Mapping[str, object],
    *,
    complete_manifest: PreviewCorpusManifest | None = None,
) -> PreviewCorpusManifest:
    try:
        if output.is_symlink() or not output.is_dir():
            raise PreviewCorpusError("incomplete corpus is invalid")
        path = output / "incomplete-manifest.json"
        if (
            path.is_symlink()
            or not path.is_file()
            or (complete_manifest is None and (output / "manifest.json").exists())
        ):
            raise PreviewCorpusError("incomplete corpus is invalid")
        manifest = _manifest_from_payload(_json_object(path.read_bytes(), "incomplete corpus"))
        if manifest.complete or not manifest.unresolved:
            raise PreviewCorpusError("incomplete corpus is invalid")
        if not _same_identity_subset(manifest, source, contract):
            raise PreviewCorpusError("incomplete corpus identity does not match source corpus")
        _verify_manifest_files(output, manifest)
        successful_ids = {photo.photo_id for photo in manifest.photos}
        unresolved_ids: set[str] = set()
        terminal = manifest.unresolved == (
            {"photo_id": _FINALIZATION_ID, "error": "final_verification_failed"},
        )
        for item in manifest.unresolved:
            photo_id = item.get("photo_id")
            error = item.get("error")
            if terminal:
                break
            if (
                not isinstance(photo_id, str)
                or photo_id not in {photo.photo_id for photo in source.photos}
                or photo_id in successful_ids
                or photo_id in unresolved_ids
                or not isinstance(error, str)
                or not error
            ):
                raise PreviewCorpusError("incomplete corpus is invalid")
            unresolved_ids.add(photo_id)
        source_ids = {photo.photo_id for photo in source.photos}
        if terminal:
            if successful_ids != source_ids:
                raise PreviewCorpusError("incomplete corpus is invalid")
        elif successful_ids | unresolved_ids != source_ids:
            raise PreviewCorpusError("incomplete corpus is invalid")
        expected_names = {
            "incomplete-manifest.json",
            *(photo.preview_filename for photo in manifest.photos),
        }
        if complete_manifest is not None:
            expected_names |= {
                "manifest.json",
                *(photo.preview_filename for photo in complete_manifest.photos),
            }
        if {entry.name for entry in output.iterdir()} != expected_names:
            raise PreviewCorpusError("incomplete corpus contains unexpected files")
        return manifest
    except PreviewCorpusError:
        raise
    except OSError as error:
        raise PreviewCorpusError("incomplete corpus is invalid") from error


def _load_source(source_manifest: Path, originals: Path) -> _SourceCorpus:
    try:
        if source_manifest.is_symlink() or not source_manifest.is_file():
            raise PreviewCorpusError("source corpus manifest is invalid")
        source_bytes = source_manifest.read_bytes()
        document = _json_object(source_bytes, "source corpus")
        if set(document) != {
            "version",
            "complete",
            "event",
            "files",
            "inventory_hash",
            "manifest_hash",
            "unresolved_count",
        }:
            raise PreviewCorpusError("source corpus manifest is invalid")
        event_value = _mapping(document["event"], "source corpus")
        if set(event_value) != {"id", "slug"} or not isinstance(event_value["id"], str):
            raise PreviewCorpusError("source corpus manifest is invalid")
        if (
            not event_value["id"]
            or not isinstance(event_value["slug"], str)
            or not event_value["slug"]
        ):
            raise PreviewCorpusError("source corpus manifest is invalid")
        if document["version"] != 1 or document["complete"] is not True:
            raise PreviewCorpusError("source corpus manifest is incomplete")
        if document["unresolved_count"] != 0 or not _valid_digest(document["inventory_hash"]):
            raise PreviewCorpusError("source corpus manifest is invalid")
        if not _valid_digest(document["manifest_hash"]):
            raise PreviewCorpusError("source corpus manifest is invalid")
        root = _direct_directory(originals, "source corpus")
        rows = document["files"]
        if not isinstance(rows, list) or not rows:
            raise PreviewCorpusError("source corpus manifest is invalid")
        photos: list[_SourcePhoto] = []
        for row in rows:
            values = _mapping(row, "source corpus")
            if set(values) != {
                "photo_id",
                "filename",
                "key",
                "content_type",
                "etag",
                "size",
                "sha256",
            }:
                raise PreviewCorpusError("source corpus manifest is invalid")
            photo_id = values["photo_id"]
            filename = values["filename"]
            if not isinstance(photo_id, str) or _PHOTO_ID.fullmatch(photo_id) is None:
                raise PreviewCorpusError("source corpus manifest is invalid")
            if filename != f"photo-{photo_id}.jpg" or not isinstance(values["key"], str):
                raise PreviewCorpusError("source corpus manifest is invalid")
            if values["key"] != f"originals/{photo_id}" or values["content_type"] != "image/jpeg":
                raise PreviewCorpusError("source corpus manifest is invalid")
            if not isinstance(values["etag"], str) or not values["etag"]:
                raise PreviewCorpusError("source corpus manifest is invalid")
            if (
                not isinstance(values["size"], int)
                or values["size"] < 1
                or not _valid_digest(values["sha256"])
            ):
                raise PreviewCorpusError("source corpus manifest is invalid")
            path = root / filename
            photos.append(_SourcePhoto(photo_id, filename, values["sha256"], values["size"], path))
        if len({photo.photo_id for photo in photos}) != len(photos):
            raise PreviewCorpusError("source corpus manifest has duplicate photo IDs")
        if len({photo.filename for photo in photos}) != len(photos):
            raise PreviewCorpusError("source corpus manifest is invalid")
        if document["inventory_hash"] != _inventory_hash(rows):
            raise PreviewCorpusError("source corpus manifest is invalid")
        if document["manifest_hash"] != _manifest_hash(document):
            raise PreviewCorpusError("source corpus manifest is invalid")
        declared = {photo.filename for photo in photos}
        actual = _direct_regular_file_names(root, "source corpus")
        if actual != declared:
            raise PreviewCorpusError("source corpus files do not match manifest")
        for photo in photos:
            if photo.path.is_symlink() or photo.path.stat().st_size != photo.byte_size:
                raise PreviewCorpusError("source corpus files do not match manifest")
            if _sha256(photo.path) != photo.sha256:
                raise PreviewCorpusError("source corpus files do not match manifest")
        return _SourceCorpus(
            hashlib.sha256(source_bytes).hexdigest(),
            {"id": str(event_value["id"]), "slug": event_value["slug"]},
            tuple(sorted(photos, key=lambda photo: photo.photo_id)),
        )
    except PreviewCorpusError:
        raise
    except (OSError, UnicodeDecodeError):
        raise PreviewCorpusError("source corpus is invalid") from None


def _generate_one(source: _SourcePhoto, output: Path) -> PreviewCorpusPhoto:
    destination = output / f"{source.photo_id}.jpg"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{source.photo_id}.", suffix=".partial", dir=output
    )
    partial = Path(temporary_name)
    try:
        os.close(descriptor)
        result = generate_preview(
            source.path,
            partial,
            max_input_bytes=_WORKER_CONFIGURATION["max_input_bytes"],
            max_pixels=MAX_PREVIEW_PIXELS_CAP,
            slot=_output_slot(source.photo_id),
        )
        if destination.exists() or destination.is_symlink():
            raise PreviewError("output_contract_violation")
        os.replace(partial, destination)
        return PreviewCorpusPhoto(
            source.photo_id,
            source.filename,
            source.sha256,
            source.byte_size,
            destination.name,
            result.byte_size,
            result.width,
            result.height,
            result.oriented_source_width,
            result.oriented_source_height,
            result.sha256,
            result.warnings,
        )
    finally:
        partial.unlink(missing_ok=True)


def _output_slot(photo_id: str) -> OutputSlot:
    return OutputSlot(
        variant="preview-small-v1",
        upload_url="https://local.invalid/preview",
        upload_expires_at="2099-01-01T00:00:00Z",
        content_type="image/jpeg",
        staging_key=f"processing-staging/previews/{photo_id}/preview-small-v1.jpg",
        max_bytes=_PREVIEW_CONFIGURATION["max_output_bytes"],
        max_width=_PREVIEW_CONFIGURATION["max_output_width"],
        max_height=_PREVIEW_CONFIGURATION["max_output_height"],
        checksum_algorithm=_PREVIEW_CONFIGURATION["checksum_algorithm"],
    )


def _preview_contract() -> dict[str, object]:
    return {
        "contract_version": PREVIEW_CONTRACT_VERSION,
        "processor_type": PROCESSOR_TYPE_GENERATE_PREVIEW,
        "processor_version": PROCESSOR_VERSION_GENERATE_PREVIEW,
        **dict(_PREVIEW_CONFIGURATION),
        "max_input_bytes": _WORKER_CONFIGURATION["max_input_bytes"],
        "max_pixels": MAX_PREVIEW_PIXELS_CAP,
    }


def _build_manifest(
    source: _SourceCorpus,
    contract: Mapping[str, object],
    photos: tuple[PreviewCorpusPhoto, ...],
    unresolved: tuple[Mapping[str, str], ...],
    *,
    generated: int,
    reused: int,
) -> PreviewCorpusManifest:
    contract_sha256 = _canonical_sha256(contract)
    frozen = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_type": "preview-corpus",
        "source_manifest_sha256": source.manifest_sha256,
        "event": dict(source.event),
        "preview_contract": dict(contract),
        "production_contract_sha256": contract_sha256,
        "photos": [photo.as_payload() for photo in photos],
        "unresolved": [dict(item) for item in unresolved],
        "complete": not unresolved,
    }
    return PreviewCorpusManifest(
        source.manifest_sha256,
        dict(source.event),
        dict(contract),
        contract_sha256,
        photos,
        unresolved,
        not unresolved,
        _canonical_sha256(frozen),
        generated,
        reused,
    )


def _write_incomplete(
    output: Path,
    source: _SourceCorpus,
    contract: Mapping[str, object],
    photos: tuple[PreviewCorpusPhoto, ...],
    unresolved: list[dict[str, str]],
) -> None:
    manifest = _build_manifest(
        source,
        contract,
        photos,
        tuple(sorted(unresolved, key=lambda item: item["photo_id"])),
        generated=0,
        reused=len(photos),
    )
    _write_json_atomic(output / "incomplete-manifest.json", manifest.payload())


def _write_terminal_incomplete(
    output: Path,
    source: _SourceCorpus,
    contract: Mapping[str, object],
    photos: tuple[PreviewCorpusPhoto, ...],
) -> None:
    verified: list[PreviewCorpusPhoto] = []
    unresolved: list[dict[str, str]] = []
    for photo in photos:
        try:
            _verify_preview_file(output, photo)
        except PreviewCorpusError:
            _discard_invalid_preview(output, photo)
            unresolved.append(
                {"photo_id": photo.photo_id, "error": "final_file_verification_failed"}
            )
        else:
            verified.append(photo)
    if not unresolved:
        unresolved.append({"photo_id": _FINALIZATION_ID, "error": "final_verification_failed"})
    _write_incomplete(output, source, contract, tuple(verified), unresolved)


def _discard_invalid_preview(output: Path, photo: PreviewCorpusPhoto) -> None:
    path = output / photo.preview_filename
    try:
        if not path.exists() and not path.is_symlink():
            return
        if path.is_symlink() or not path.is_file():
            raise PreviewCorpusError("invalid preview cleanup is unsafe")
        path.unlink()
    except PreviewCorpusError:
        raise
    except OSError as error:
        raise PreviewCorpusError("invalid preview cleanup failed") from error


def _same_identity(
    manifest: PreviewCorpusManifest, source: _SourceCorpus, contract: Mapping[str, object]
) -> bool:
    return (
        manifest.source_manifest_sha256 == source.manifest_sha256
        and dict(manifest.event) == dict(source.event)
        and dict(manifest.preview_contract) == dict(contract)
        and manifest.production_contract_sha256 == _canonical_sha256(contract)
        and [
            (photo.photo_id, photo.source_filename, photo.source_sha256, photo.source_byte_size)
            for photo in manifest.photos
        ]
        == [
            (photo.photo_id, photo.filename, photo.sha256, photo.byte_size)
            for photo in source.photos
        ]
    )


def _same_identity_subset(
    manifest: PreviewCorpusManifest, source: _SourceCorpus, contract: Mapping[str, object]
) -> bool:
    source_by_id = {photo.photo_id: photo for photo in source.photos}
    return (
        manifest.source_manifest_sha256 == source.manifest_sha256
        and dict(manifest.event) == dict(source.event)
        and dict(manifest.preview_contract) == dict(contract)
        and manifest.production_contract_sha256 == _canonical_sha256(contract)
        and all(
            (source_photo := source_by_id.get(photo.photo_id)) is not None
            and (
                photo.source_filename,
                photo.source_sha256,
                photo.source_byte_size,
            )
            == (source_photo.filename, source_photo.sha256, source_photo.byte_size)
            for photo in manifest.photos
        )
    )


def _manifest_from_payload(value: Mapping[str, object]) -> PreviewCorpusManifest:
    required = {
        "schema_version",
        "artifact_type",
        "source_manifest_sha256",
        "event",
        "preview_contract",
        "production_contract_sha256",
        "photos",
        "unresolved",
        "complete",
        "manifest_sha256",
    }
    if set(value) != required or value["schema_version"] != _SCHEMA_VERSION:
        raise PreviewCorpusError("output corpus manifest is invalid")
    if value["artifact_type"] != "preview-corpus" or not isinstance(value["complete"], bool):
        raise PreviewCorpusError("output corpus manifest is invalid")
    if not isinstance(value["unresolved"], list) or not _valid_digest(
        value["source_manifest_sha256"]
    ):
        raise PreviewCorpusError("output corpus manifest is invalid")
    if not (
        _valid_digest(value["production_contract_sha256"])
        and _valid_digest(value["manifest_sha256"])
    ):
        raise PreviewCorpusError("output corpus manifest is invalid")
    event = _mapping(value["event"], "output corpus")
    contract = _mapping(value["preview_contract"], "output corpus")
    if set(event) != {"id", "slug"} or not all(
        isinstance(item, str) and item for item in event.values()
    ):
        raise PreviewCorpusError("output corpus manifest is invalid")
    if dict(contract) != _preview_contract():
        raise PreviewCorpusError("output corpus manifest is invalid")
    if _canonical_sha256(contract) != value["production_contract_sha256"]:
        raise PreviewCorpusError("output corpus manifest is invalid")
    rows = value["photos"]
    if not isinstance(rows, list) or (value["complete"] and not rows):
        raise PreviewCorpusError("output corpus manifest is invalid")
    photos = tuple(_photo_from_payload(_mapping(row, "output corpus")) for row in rows)
    if [photo.photo_id for photo in photos] != sorted(photo.photo_id for photo in photos):
        raise PreviewCorpusError("output corpus manifest is invalid")
    if len({photo.photo_id for photo in photos}) != len(photos):
        raise PreviewCorpusError("output corpus manifest is invalid")
    frozen = {key: item for key, item in value.items() if key != "manifest_sha256"}
    if _canonical_sha256(frozen) != value["manifest_sha256"]:
        raise PreviewCorpusError("output corpus manifest is invalid")
    unresolved = tuple(_unresolved_from_payload(item) for item in value["unresolved"])
    if value["complete"] != (not unresolved):
        raise PreviewCorpusError("output corpus manifest is invalid")
    return PreviewCorpusManifest(
        value["source_manifest_sha256"],
        {"id": event["id"], "slug": event["slug"]},
        dict(contract),
        value["production_contract_sha256"],
        photos,
        unresolved,
        value["complete"],
        value["manifest_sha256"],
    )


def _unresolved_from_payload(value: object) -> Mapping[str, str]:
    item = _mapping(value, "output corpus")
    if set(item) != {"photo_id", "error"} or not all(
        isinstance(field, str) and field for field in item.values()
    ):
        raise PreviewCorpusError("output corpus manifest is invalid")
    return {"photo_id": item["photo_id"], "error": item["error"]}


def _photo_from_payload(value: Mapping[str, object]) -> PreviewCorpusPhoto:
    expected = {
        "photo_id",
        "source_filename",
        "source_sha256",
        "source_byte_size",
        "preview_filename",
        "byte_size",
        "width",
        "height",
        "oriented_source_width",
        "oriented_source_height",
        "sha256",
        "warnings",
    }
    if set(value) != expected:
        raise PreviewCorpusError("output corpus manifest is invalid")
    photo_id = value["photo_id"]
    if not isinstance(photo_id, str) or _PHOTO_ID.fullmatch(photo_id) is None:
        raise PreviewCorpusError("output corpus manifest is invalid")
    if (
        value["source_filename"] != f"photo-{photo_id}.jpg"
        or value["preview_filename"] != f"{photo_id}.jpg"
    ):
        raise PreviewCorpusError("output corpus manifest is invalid")
    positive_fields = (
        "source_byte_size",
        "byte_size",
        "width",
        "height",
        "oriented_source_width",
        "oriented_source_height",
    )
    if any(not isinstance(value[field], int) or value[field] < 1 for field in positive_fields):
        raise PreviewCorpusError("output corpus manifest is invalid")
    if not _valid_digest(value["source_sha256"]) or not _valid_digest(value["sha256"]):
        raise PreviewCorpusError("output corpus manifest is invalid")
    if not isinstance(value["warnings"], list) or any(
        not isinstance(item, str) for item in value["warnings"]
    ):
        raise PreviewCorpusError("output corpus manifest is invalid")
    return PreviewCorpusPhoto(
        photo_id,
        value["source_filename"],
        value["source_sha256"],
        value["source_byte_size"],
        value["preview_filename"],
        value["byte_size"],
        value["width"],
        value["height"],
        value["oriented_source_width"],
        value["oriented_source_height"],
        value["sha256"],
        tuple(value["warnings"]),
    )


def _verify_manifest_files(output: Path, manifest: PreviewCorpusManifest) -> None:
    for photo in manifest.photos:
        _verify_preview_file(output, photo)


def _verify_preview_file(output: Path, photo: PreviewCorpusPhoto) -> None:
    path = output / photo.preview_filename
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size != photo.byte_size:
            raise PreviewCorpusError("output corpus file evidence is invalid")
        if _sha256(path) != photo.sha256:
            raise PreviewCorpusError("output corpus file evidence is invalid")
        with Image.open(path) as image:
            if (
                image.format != "JPEG"
                or image.width != photo.width
                or image.height != photo.height
                or image.width > _PREVIEW_CONFIGURATION["max_output_width"]
                or image.height > _PREVIEW_CONFIGURATION["max_output_height"]
            ):
                raise PreviewCorpusError("output corpus file evidence is invalid")
    except UnidentifiedImageError as error:
        raise PreviewCorpusError("output corpus file evidence is invalid") from error
    except OSError as error:
        raise PreviewCorpusError("output corpus file evidence is invalid") from error


def _direct_directory(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise PreviewCorpusError(f"{label} originals directory is invalid")
    return path


def _direct_regular_file_names(root: Path, label: str) -> set[str]:
    try:
        names: set[str] = set()
        for entry in root.iterdir():
            if entry.is_symlink() or not entry.is_file():
                raise PreviewCorpusError(f"{label} files do not match manifest")
            names.add(entry.name)
        return names
    except OSError as error:
        raise PreviewCorpusError(f"{label} files do not match manifest") from error


def _write_json_atomic(path: Path, value: Mapping[str, object]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                value,
                stream,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json_object(raw: bytes, label: str) -> Mapping[str, object]:
    try:
        value: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PreviewCorpusError(f"{label} manifest is invalid") from None
    return _mapping(value, label)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PreviewCorpusError(f"{label} manifest is invalid")
    return value


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _inventory_hash(files: object) -> str:
    if not isinstance(files, list):
        raise PreviewCorpusError("source corpus manifest is invalid")
    inventory: list[dict[str, object]] = []
    for value in files:
        row = _mapping(value, "source corpus")
        inventory.append(
            {
                "photo_id": row["photo_id"],
                "filename": row["filename"],
                "key": row["key"],
                "size": row["size"],
                "content_type": row["content_type"],
                "etag": row["etag"],
            }
        )
    return _canonical_sha256(inventory)


def _manifest_hash(manifest: Mapping[str, object]) -> str:
    return _canonical_sha256(
        {key: value for key, value in manifest.items() if key != "manifest_hash"}
    )


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(64 * 1024):
            digest.update(chunk)
    return digest.hexdigest()

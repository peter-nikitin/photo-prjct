"""Fail-closed, vector-free preview quality profile comparison bundles."""
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
from photo_worker.face_quality import evaluate_face_quality

from .analysis import FaceDetection
from .image_decoder import ImageLimits, PillowImageDecoder
from .inventory import EventPhoto
from .models import SFaceRecognizer, YuNetDetector
from .preview_corpus import PreviewCorpusError, PreviewCorpusManifest, load_verified_preview_corpus
from .preview_profile_report import render_profile_report, validate_report_links
from .quality_profiles import (
    DECISION_CONFIGURATION,
    QUALITY_PROFILES,
    decide_quality,
    profile_payloads,
)
from .quality_sample_artifacts import load_quality_sample_bundle

_PHOTO_ID = re.compile(r"^[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROOT_NAMES = frozenset({"manifest.json", "evidence.json", "report.html", "previews", "crops"})
_STATUSES = frozenset(
    {
        "ok",
        "no_detection",
        "image_decode_failed",
        "unsupported_image",
        "image_too_large",
        "detection_failed",
        "invalid_face_quality",
        "technical_failure",
    }
)


class ComparisonError(ValueError):
    """The requested private comparison cannot be safely published or trusted."""


@dataclass(frozen=True)
class ProfileComparison:
    output: Path
    manifest_sha256: str
    photo_count: int


@dataclass(frozen=True)
class _SampleIdentity:
    raw_sha256: str
    sample_sha256: str
    source_bundle_sha256: str
    photo_ids: tuple[str, ...]


def refuse_existing_output(output: Path) -> None:
    if os.path.lexists(output):
        raise ComparisonError("comparison output already exists")


def match_detections(
    baseline: Iterable[tuple[float, float, float, float]],
    candidate: Iterable[tuple[float, float, float, float]],
    *,
    minimum_iou: float = 0.5,
) -> tuple[tuple[int, int], ...]:
    left, right = tuple(baseline), tuple(candidate)
    scored = sorted(
        (_iou(first, second), first_index, second_index)
        for first_index, first in enumerate(left)
        for second_index, second in enumerate(right)
    )
    pairs: list[tuple[int, int]] = []
    used_left: set[int] = set()
    used_right: set[int] = set()
    for score, first_index, second_index in reversed(scored):
        if score < minimum_iou:
            break
        if first_index not in used_left and second_index not in used_right:
            pairs.append((first_index, second_index))
            used_left.add(first_index)
            used_right.add(second_index)
    return tuple(sorted(pairs))


def compare_preview_profiles(
    preview_corpus: Path,
    sample: Path,
    yunet_model: Path,
    sface_model: Path,
    output: Path,
    *,
    problem_photo_ids: tuple[str, ...],
) -> ProfileComparison:
    refuse_existing_output(output)
    if (
        not problem_photo_ids
        or len(set(problem_photo_ids)) != len(problem_photo_ids)
        or not all(_PHOTO_ID.fullmatch(item) for item in problem_photo_ids)
    ):
        raise ComparisonError("problem photo IDs are invalid")
    problem_photo_ids = tuple(sorted(problem_photo_ids))
    corpus = _load_corpus(preview_corpus)
    sample_identity = _load_sample_identity(sample)
    photo_by_id = {photo.photo_id: photo for photo in corpus.photos}
    photo_ids = tuple(sorted(set(problem_photo_ids) | set(sample_identity.photo_ids)))
    if not set(photo_ids) <= set(photo_by_id):
        raise ComparisonError("comparison photo is absent from preview corpus")
    model_hashes = {"sface": _sha256_file(sface_model), "yunet": _sha256_file(yunet_model)}
    try:
        SFaceRecognizer(sface_model)
    except Exception as error:
        raise ComparisonError("sface model cannot be loaded") from error

    evidence: list[dict[str, object]] = []
    decoder = PillowImageDecoder(ImageLimits(1600, 1600 * 1600))
    for threshold in DECISION_CONFIGURATION.detector_thresholds:
        detector = YuNetDetector(yunet_model, threshold=threshold)
        for photo_id in photo_ids:
            source = preview_corpus / photo_by_id[photo_id].preview_filename
            evidence.append(_analyze_one(decoder, detector, source, photo_id, threshold))
    _attach_threshold_deltas(evidence)
    return _publish(
        output,
        preview_corpus,
        corpus,
        photo_by_id,
        tuple(problem_photo_ids),
        sample_identity,
        model_hashes,
        evidence,
    )


def load_verified_profile_comparison(output: Path) -> Mapping[str, object]:
    try:
        root = output.resolve()
        if (
            output.is_symlink()
            or not root.is_dir()
            or {item.name for item in root.iterdir()} != _ROOT_NAMES
        ):
            raise ComparisonError("comparison bundle is invalid")
        if any((root / name).is_symlink() for name in _ROOT_NAMES):
            raise ComparisonError("comparison bundle contains symlink")
        manifest = _json_object(root / "manifest.json")
        required = {
            "artifact_type",
            "schema_version",
            "preview_manifest_sha256",
            "source_manifest_sha256",
            "model_hashes",
            "configuration",
            "profiles",
            "sample",
            "problem_photo_ids",
            "photo_ids",
            "files",
            "manifest_sha256",
        }
        if (
            set(manifest) != required
            or manifest["artifact_type"] != "preview-profile-comparison"
            or manifest["schema_version"] != 2
        ):
            raise ComparisonError("comparison manifest schema is invalid")
        if (
            manifest["configuration"] != DECISION_CONFIGURATION.as_payload()
            or manifest["profiles"] != profile_payloads()
        ):
            raise ComparisonError("comparison decision configuration is invalid")
        if not _digest(manifest["preview_manifest_sha256"]) or not _digest(
            manifest["source_manifest_sha256"]
        ):
            raise ComparisonError("comparison corpus identity is invalid")
        if not _valid_models(manifest["model_hashes"]) or not _valid_sample(manifest["sample"]):
            raise ComparisonError("comparison input identity is invalid")
        problem_ids = _ids(manifest["problem_photo_ids"], allow_empty=False)
        photo_ids = _ids(manifest["photo_ids"], allow_empty=False)
        if not set(problem_ids) <= set(photo_ids):
            raise ComparisonError("comparison photo identity is invalid")
        frozen = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
        if manifest["manifest_sha256"] != _canonical_sha256(frozen):
            raise ComparisonError("comparison manifest hash is invalid")
        files = _file_inventory(root, manifest["files"])
        evidence = _json_object(root / "evidence.json")
        _validate_evidence(evidence, manifest, files)
        _validate_report(root, evidence, manifest)
        return manifest
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise ComparisonError("comparison bundle is invalid") from error


def _load_sample_identity(sample_path: Path) -> _SampleIdentity:
    if sample_path.name != "sample.json" or sample_path.is_symlink() or not sample_path.is_file():
        raise ComparisonError("quality sample must be a bundle sample.json")
    try:
        sample, _sample_bundle_sha256 = load_quality_sample_bundle(sample_path.parent)
        raw = sample_path.read_bytes()
    except (OSError, ValueError) as error:
        raise ComparisonError("quality sample is invalid") from error
    photo_ids: list[str] = []
    for rejection in sample.rejections:
        match = re.fullmatch(r"photo-([0-9a-f]{32})\.jpg", rejection.rejection.filename)
        if match is None:
            raise ComparisonError("quality sample rejection filename is invalid")
        photo_ids.append(match.group(1))
    return _SampleIdentity(
        hashlib.sha256(raw).hexdigest(),
        _digest_value(sample_path, "sample_sha256"),
        sample.source_bundle_sha256,
        tuple(sorted(set(photo_ids))),
    )


def _digest_value(sample_path: Path, key: str) -> str:
    try:
        value = json.loads(sample_path.read_text(encoding="utf-8"))[key]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ComparisonError("quality sample is invalid") from error
    if not _digest(value):
        raise ComparisonError("quality sample is invalid")
    return str(value)


def _analyze_one(
    decoder: PillowImageDecoder,
    detector: YuNetDetector,
    source: Path,
    photo_id: str,
    threshold: float,
) -> dict[str, object]:
    try:
        image = decoder.decode(EventPhoto(source.name, source))
        detections = detector.detect(image.bgr)
        faces = [_face_payload(image.bgr, image.width, image.height, item) for item in detections]
        return {
            "photo_id": photo_id,
            "threshold": threshold,
            "status": "ok" if faces else "no_detection",
            "detection_count": len(faces),
            "detections": faces,
        }
    except Exception as error:
        return {
            "photo_id": photo_id,
            "threshold": threshold,
            "status": _technical_code(error),
            "detection_count": 0,
            "detections": [],
        }
    finally:
        # DecodedImage owns both RGB and BGR; no image survives the threshold/photo scope.
        if "image" in locals():
            del image


def _face_payload(
    image: Any, width: int, height: int, detection: FaceDetection
) -> dict[str, object]:
    box = detection.bounding_box
    bbox = (box.x, box.y, box.width, box.height)
    quality = evaluate_face_quality(
        image,
        bbox=bbox,
        confidence=detection.confidence,
        thresholds=DECISION_CONFIGURATION.thresholds(),
    )
    decisions = {profile.name: decide_quality(profile, quality) for profile in QUALITY_PROFILES}
    return {
        "identity": _detection_identity(bbox, detection.confidence),
        "bbox": {"x": box.x, "y": box.y, "width": box.width, "height": box.height},
        "confidence": detection.confidence,
        "minimum_side_px": quality.minimum_side_px,
        "relative_area": quality.relative_area,
        "sharpness": quality.sharpness,
        "current": {"decision": quality.decision, "reasons": list(quality.reasons)},
        "profiles": {
            name: {"decision": item.decision, "reasons": list(item.reasons)}
            for name, item in decisions.items()
        },
        "crop_path": None,
    }


def _attach_threshold_deltas(records: list[dict[str, object]]) -> None:
    by_key = {(str(row["photo_id"]), float(row["threshold"])): row for row in records}
    for photo_id in {str(item["photo_id"]) for item in records}:
        baseline = by_key[(photo_id, 0.75)]
        for threshold in DECISION_CONFIGURATION.detector_thresholds:
            current = by_key[(photo_id, threshold)]
            current["threshold_delta"] = _expected_delta(baseline, current)


def _publish(
    output: Path,
    corpus_root: Path,
    corpus: PreviewCorpusManifest,
    photo_by_id: Mapping[str, Any],
    problem_ids: tuple[str, ...],
    sample: _SampleIdentity,
    model_hashes: Mapping[str, str],
    records: list[dict[str, object]],
) -> ProfileComparison:
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging.", dir=output.parent))
    try:
        (staging / "previews").mkdir()
        (staging / "crops").mkdir()
        changed = _changed_faces(records)
        preview_ids = set(problem_ids) | {photo_id for photo_id, _threshold, _index in changed}
        preview_paths = _copy_previews(staging, corpus_root, photo_by_id, preview_ids)
        _write_changed_crops(staging, corpus_root, photo_by_id, records, changed)
        for record in records:
            record["preview_path"] = preview_paths.get(str(record["photo_id"]))
        evidence = {
            "schema_version": 2,
            "configuration": DECISION_CONFIGURATION.as_payload(),
            "profiles": profile_payloads(),
            "records": records,
        }
        _write_json(staging / "evidence.json", evidence)
        problem_records = [
            _problem_report_record(photo_id, records, preview_paths[photo_id])
            for photo_id in problem_ids
        ]
        sampled_changed = _sample_changed_cards(records, set(problem_ids))
        _write_text(
            staging / "report.html", render_profile_report(problem_records, sampled_changed)
        )
        manifest: dict[str, object] = {
            "artifact_type": "preview-profile-comparison",
            "schema_version": 2,
            "preview_manifest_sha256": corpus.manifest_sha256,
            "source_manifest_sha256": corpus.source_manifest_sha256,
            "model_hashes": dict(sorted(model_hashes.items())),
            "configuration": DECISION_CONFIGURATION.as_payload(),
            "profiles": profile_payloads(),
            "sample": {
                "raw_sha256": sample.raw_sha256,
                "sample_sha256": sample.sample_sha256,
                "source_bundle_sha256": sample.source_bundle_sha256,
            },
            "problem_photo_ids": list(problem_ids),
            "photo_ids": sorted({str(row["photo_id"]) for row in records}),
            "files": _file_rows(staging),
        }
        manifest["manifest_sha256"] = _canonical_sha256(manifest)
        _write_json(staging / "manifest.json", manifest)
        load_verified_profile_comparison(staging)
        refuse_existing_output(output)
        os.replace(staging, output)
        return ProfileComparison(
            output, str(manifest["manifest_sha256"]), len(manifest["photo_ids"])
        )
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _copy_previews(
    staging: Path, corpus_root: Path, photo_by_id: Mapping[str, Any], ids: set[str]
) -> dict[str, str]:
    paths: dict[str, str] = {}
    for photo_id in sorted(ids):
        source = corpus_root / photo_by_id[photo_id].preview_filename
        destination = staging / "previews" / source.name
        shutil.copyfile(source, destination)
        paths[photo_id] = destination.relative_to(staging).as_posix()
    return paths


def _write_changed_crops(
    staging: Path,
    corpus_root: Path,
    photo_by_id: Mapping[str, Any],
    records: Sequence[dict[str, object]],
    changed: set[tuple[str, float, int]],
) -> None:
    decoder = PillowImageDecoder(ImageLimits(1600, 1600 * 1600))
    for photo_id, threshold, index in sorted(changed):
        record = next(
            row for row in records if row["photo_id"] == photo_id and row["threshold"] == threshold
        )
        image = decoder.decode(
            EventPhoto(
                photo_by_id[photo_id].preview_filename,
                corpus_root / photo_by_id[photo_id].preview_filename,
            )
        )
        try:
            face = record["detections"][index]  # type: ignore[index]
            x, y, width, height = (int(round(value)) for value in _bbox_tuple(face["bbox"]))
            crop = image.bgr[y : y + height, x : x + width]
            destination = staging / "crops" / f"{photo_id}-{threshold:.2f}-{face['identity']}.png"
            if crop.size == 0 or not cv2.imwrite(str(destination), crop):
                raise ComparisonError("changed-decision crop cannot be written")
            face["crop_path"] = destination.relative_to(staging).as_posix()
        finally:
            del image


def _changed_faces(records: Sequence[Mapping[str, object]]) -> set[tuple[str, float, int]]:
    return {
        (str(record["photo_id"]), float(record["threshold"]), index)
        for record in records
        for index, face in enumerate(record["detections"])  # type: ignore[index]
        if any(
            value["decision"] != face["current"]["decision"] for value in face["profiles"].values()
        )
    }


def _problem_report_record(
    photo_id: str, records: Sequence[Mapping[str, object]], preview_path: str
) -> dict[str, object]:
    matching = [row for row in records if row["photo_id"] == photo_id]
    return {
        "photo_id": photo_id,
        "preview_path": preview_path,
        "status": ",".join(str(row["status"]) for row in matching),
        "thresholds": matching,
    }


def _sample_changed_cards(
    records: Sequence[Mapping[str, object]], problem_ids: set[str]
) -> list[dict[str, object]]:
    return [
        {
            "photo_id": record["photo_id"],
            "threshold": record["threshold"],
            "face": face,
            "crop_path": face["crop_path"],
        }
        for record in records
        if record["photo_id"] not in problem_ids
        for face in record["detections"]  # type: ignore[index]
        if face["crop_path"]
    ]


def _validate_evidence(
    evidence: Mapping[str, object], manifest: Mapping[str, object], files: Mapping[str, str]
) -> None:
    if (
        set(evidence) != {"schema_version", "configuration", "profiles", "records"}
        or evidence["schema_version"] != 2
    ):
        raise ComparisonError("comparison evidence schema is invalid")
    if (
        evidence["configuration"] != manifest["configuration"]
        or evidence["profiles"] != manifest["profiles"]
    ):
        raise ComparisonError("comparison evidence identity is invalid")
    _reject_vectors(evidence)
    records = evidence["records"]
    if not isinstance(records, list) or len(records) != len(manifest["photo_ids"]) * len(
        DECISION_CONFIGURATION.detector_thresholds
    ):
        raise ComparisonError("comparison evidence coverage is invalid")
    expected = {
        (photo_id, threshold)
        for photo_id in manifest["photo_ids"]
        for threshold in DECISION_CONFIGURATION.detector_thresholds
    }
    observed: set[tuple[str, float]] = set()
    media = set(files)
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "photo_id",
            "threshold",
            "status",
            "detection_count",
            "detections",
            "threshold_delta",
            "preview_path",
        }:
            raise ComparisonError("comparison record schema is invalid")
        key = (record["photo_id"], record["threshold"])
        if (
            type(record["detection_count"]) is not int
            or record["detection_count"] < 0
            or not isinstance(record["detections"], list)
            or key in observed
            or key not in expected
            or record["detection_count"] != len(record["detections"])
        ):
            raise ComparisonError("comparison detection coverage is invalid")
        observed.add(key)
        if type(record["status"]) is not str or record["status"] not in _STATUSES:
            raise ComparisonError("comparison detection status is invalid")
        if record["status"] == "ok" and (
            record["detection_count"] <= 0 or not record["detections"]
        ):
            raise ComparisonError("comparison ok status is invalid")
        if record["status"] in {"ok", "no_detection"} and not isinstance(
            record["detections"], list
        ):
            raise ComparisonError("comparison detection status is invalid")
        if record["status"] == "no_detection" and (
            record["detection_count"] != 0 or record["detections"]
        ):
            raise ComparisonError("comparison no-detection status is invalid")
        if record["status"] not in {"ok", "no_detection"} and (
            record["detection_count"] != 0
            or record["detections"]
            or record["threshold_delta"] is not None
        ):
            raise ComparisonError("comparison technical status is invalid")
        preview = record["preview_path"]
        photo_id, _threshold = key
        if photo_id in manifest["problem_photo_ids"] and preview is None:
            raise ComparisonError("comparison problem preview is missing")
        if preview is not None and preview not in media:
            raise ComparisonError("comparison preview media is missing")
        for face in record["detections"]:
            changed = _validate_face(face, media)
            if changed and (preview is None or face["crop_path"] is None):
                raise ComparisonError("comparison changed decision media is missing")
    if observed != expected:
        raise ComparisonError("comparison threshold matrix is incomplete")
    _validate_deltas(records)
    referenced_previews = {str(row["preview_path"]) for row in records if row["preview_path"]}
    referenced_crops = {
        str(face["crop_path"]) for row in records for face in row["detections"] if face["crop_path"]
    }
    actual_previews = {path for path in media if path.startswith("previews/")}
    actual_crops = {path for path in media if path.startswith("crops/")}
    if actual_previews != referenced_previews or actual_crops != referenced_crops:
        raise ComparisonError("comparison media coverage is invalid")


def _validate_face(face: object, media: set[str]) -> bool:
    required = {
        "identity",
        "bbox",
        "confidence",
        "minimum_side_px",
        "relative_area",
        "sharpness",
        "current",
        "profiles",
        "crop_path",
    }
    if (
        not isinstance(face, dict)
        or set(face) != required
        or set(face["profiles"]) != {item.name for item in QUALITY_PROFILES}
    ):
        raise ComparisonError("comparison face schema is invalid")
    bbox = face["bbox"]
    if (
        not isinstance(bbox, dict)
        or set(bbox) != {"x", "y", "width", "height"}
        or not all(_finite(value) for value in bbox.values())
    ):
        raise ComparisonError("comparison bbox is invalid")
    x, y, width, height = _bbox_tuple(bbox)
    if x < 0 or y < 0 or width <= 0 or height <= 0 or not _probability(face["confidence"]):
        raise ComparisonError("comparison face bounds are invalid")
    minimum_side = face["minimum_side_px"]
    if not _finite(minimum_side) or float(minimum_side) != min(width, height):
        raise ComparisonError("comparison face minimum side is invalid")
    if not _probability(face["relative_area"]) or not _finite(face["sharpness"]):
        raise ComparisonError("comparison face evidence is invalid")
    if face["identity"] != _detection_identity((x, y, width, height), float(face["confidence"])):
        raise ComparisonError("comparison face identity is invalid")
    current = _recomputed_current(face)
    if (
        not isinstance(face["current"], dict)
        or set(face["current"]) != {"decision", "reasons"}
        or not isinstance(face["current"]["reasons"], list)
        or face["current"] != {"decision": current.decision, "reasons": list(current.reasons)}
    ):
        raise ComparisonError("comparison current decision is invalid")
    changed = False
    for profile in QUALITY_PROFILES:
        decision = decide_quality(profile, current)
        expected = {"decision": decision.decision, "reasons": list(decision.reasons)}
        if (
            not isinstance(face["profiles"][profile.name], dict)
            or not isinstance(face["profiles"][profile.name].get("reasons"), list)
            or face["profiles"][profile.name] != expected
        ):
            raise ComparisonError("comparison profile decision is invalid")
        changed |= decision.decision != current.decision
    crop = face["crop_path"]
    if crop is not None and crop not in media:
        raise ComparisonError("comparison crop media is missing")
    return changed


def _recomputed_current(face: Mapping[str, object]):
    """Recreate production decision from frozen scalar evidence without any embedding."""
    side = float(face["minimum_side_px"])
    sharpness = float(face["sharpness"])
    area = float(face["relative_area"])
    confidence = float(face["confidence"])
    reasons: list[str] = []
    if side < DECISION_CONFIGURATION.minimum_face_px:
        reasons.append("too_small")
    if sharpness < DECISION_CONFIGURATION.severe_blur_threshold:
        reasons.append("severe_blur")
    if not reasons and sharpness < DECISION_CONFIGURATION.borderline_blur_threshold:
        support: list[str] = []
        if area < DECISION_CONFIGURATION.minimum_relative_area:
            support.append("small_relative_area")
        if confidence < DECISION_CONFIGURATION.minimum_confidence:
            support.append("low_confidence")
        if support:
            reasons.extend(("borderline_blur", *support))
    from photo_worker.face_quality import FaceQualityEvidence

    return FaceQualityEvidence(
        DECISION_CONFIGURATION.algorithm_version,
        DECISION_CONFIGURATION.crop_size,
        confidence,
        side,
        area,
        sharpness,
        "quality_rejected" if reasons else "accepted",
        tuple(reasons),
    )


def _validate_deltas(records: Sequence[Mapping[str, object]]) -> None:
    by_key = {(str(row["photo_id"]), float(row["threshold"])): row for row in records}
    for photo_id in {str(row["photo_id"]) for row in records}:
        baseline = by_key[(photo_id, 0.75)]
        for threshold in DECISION_CONFIGURATION.detector_thresholds:
            row = by_key[(photo_id, threshold)]
            expected = _expected_delta(baseline, row)
            if row["threshold_delta"] != expected:
                raise ComparisonError("comparison threshold delta is invalid")


def _expected_delta(
    baseline: Mapping[str, object], current: Mapping[str, object]
) -> dict[str, object]:
    if baseline["status"] not in {"ok", "no_detection"} or current["status"] not in {
        "ok",
        "no_detection",
    }:
        return None
    before = [_bbox_tuple(item["bbox"]) for item in baseline["detections"]]  # type: ignore[index]
    after = [_bbox_tuple(item["bbox"]) for item in current["detections"]]  # type: ignore[index]
    pairs = match_detections(before, after)
    matched_before = {left for left, _right in pairs}
    matched_after = {right for _left, right in pairs}
    return {
        "misses": [
            baseline["detections"][index]["identity"]
            for index in range(len(before))
            if index not in matched_before
        ],  # type: ignore[index]
        "recoveries": [
            current["detections"][index]["identity"]
            for index in range(len(after))
            if index not in matched_after
        ],  # type: ignore[index]
        "technical": False,
    }


def _validate_report(
    root: Path, evidence: Mapping[str, object], manifest: Mapping[str, object]
) -> None:
    records = evidence["records"]
    problem_records = [
        _problem_report_record(photo_id, records, _problem_preview(records, photo_id))
        for photo_id in manifest["problem_photo_ids"]
    ]
    expected = render_profile_report(
        problem_records,
        _sample_changed_cards(records, set(manifest["problem_photo_ids"])),
    )
    if (root / "report.html").read_text(encoding="utf-8") != expected:
        raise ComparisonError("comparison report payload is invalid")
    validate_report_links(root, root / "report.html")


def _problem_preview(records: Sequence[Mapping[str, object]], photo_id: str) -> str:
    preview = next(row["preview_path"] for row in records if row["photo_id"] == photo_id)
    if not isinstance(preview, str):
        raise ComparisonError("comparison problem preview is missing")
    return preview


def _file_inventory(root: Path, rows: object) -> Mapping[str, str]:
    if not isinstance(rows, list):
        raise ComparisonError("comparison file inventory is invalid")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if any(path.is_symlink() for path in root.rglob("*")) or any(
        path.is_dir() and path.relative_to(root).parts not in {("previews",), ("crops",)}
        for path in root.rglob("*")
    ):
        raise ComparisonError("comparison bundle contains unexpected path")
    declared: dict[str, str] = {}
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"path", "sha256"}
            or not isinstance(row["path"], str)
            or not _digest(row["sha256"])
        ):
            raise ComparisonError("comparison file inventory is invalid")
        path = _contained(root, row["path"])
        if row["path"] in declared or _sha256_file(path) != row["sha256"]:
            raise ComparisonError("comparison file inventory is invalid")
        declared[row["path"]] = row["sha256"]
    if set(declared) != actual - {"manifest.json"}:
        raise ComparisonError("comparison bundle contains undeclared files")
    return declared


def _valid_models(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"yunet", "sface"}
        and all(_digest(item) for item in value.values())
    )


def _valid_sample(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"raw_sha256", "sample_sha256", "source_bundle_sha256"}
        and all(_digest(item) for item in value.values())
    )


def _ids(value: object, *, allow_empty: bool) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or value != sorted(value)
        or len(set(value)) != len(value)
        or not all(isinstance(item, str) and _PHOTO_ID.fullmatch(item) for item in value)
    ):
        raise ComparisonError("comparison photo IDs are invalid")
    return tuple(value)


def _reject_vectors(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if "vector" in key.lower() or "embedding" in key.lower():
                raise ComparisonError("comparison evidence must be vector-free")
            _reject_vectors(item)
    elif isinstance(value, list):
        for item in value:
            _reject_vectors(item)


def _load_corpus(path: Path) -> PreviewCorpusManifest:
    try:
        return load_verified_preview_corpus(path)
    except PreviewCorpusError as error:
        raise ComparisonError("preview corpus is invalid") from error


def _json_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ComparisonError("comparison bundle is invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ComparisonError("comparison JSON is invalid")
    return value


def _iou(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> float:
    x1, y1, w1, h1 = left
    x2, y2, w2, h2 = right
    overlap = max(0.0, min(x1 + w1, x2 + w2) - max(x1, x2)) * max(
        0.0, min(y1 + h1, y2 + h2) - max(y1, y2)
    )
    union = w1 * h1 + w2 * h2 - overlap
    return overlap / union if union else 0.0


def _detection_identity(bbox: tuple[float, float, float, float], confidence: float) -> str:
    return hashlib.sha256(
        ",".join(f"{value:.6f}" for value in (*bbox, confidence)).encode()
    ).hexdigest()[:16]


def _bbox_tuple(value: object) -> tuple[float, float, float, float]:
    if not isinstance(value, dict) or set(value) != {"x", "y", "width", "height"}:
        raise ComparisonError("comparison bbox is invalid")
    return (float(value["x"]), float(value["y"]), float(value["width"]), float(value["height"]))


def _technical_code(error: Exception) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and code in _STATUSES:
        return code
    if isinstance(error, RuntimeError) and str(error) == "detection_failed":
        return "detection_failed"
    if error.__class__.__name__ == "FaceQualityError":
        return "invalid_face_quality"
    return "technical_failure"


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ComparisonError("model or bundle file is invalid")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and float("-inf") < float(value) < float("inf")
    )


def _probability(value: object) -> bool:
    return _finite(value) and 0.0 <= float(value) <= 1.0


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _file_rows(root: Path) -> list[dict[str, str]]:
    return [
        {"path": path.relative_to(root).as_posix(), "sha256": _sha256_file(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _contained(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if (
        Path(relative).is_absolute()
        or root not in candidate.parents
        or candidate.is_symlink()
        or not candidate.is_file()
    ):
        raise ComparisonError("comparison bundle contains unsafe path")
    return candidate

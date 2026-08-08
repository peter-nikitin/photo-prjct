"""Immutable local bundles for quality comparison and manual review."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from .analysis import BoundingBox
from .quality_comparison import (
    FaceMatch,
    NewRejection,
    QualityComparison,
    QualityFace,
    QualityOutcome,
    QualityPhoto,
    QualityRun,
    TechnicalFailure,
    ThresholdSample,
)

if TYPE_CHECKING:
    from .smoke_search import SearchComparison

SCHEMA_VERSION = 1
LABEL_HEADERS = ("bundle_sha256", "face_id", "label")
ALLOWED_LABELS = frozenset({"clear", "blurred", "unusably_small", "uncertain"})
_BUNDLE_DIGEST_PLACEHOLDER = "0" * 64


def quality_comparison_sha256(comparison: QualityComparison) -> str:
    return hashlib.sha256(_canonical_json(_comparison_payload(comparison))).hexdigest()


def write_quality_comparison_bundle(
    output: Path,
    comparison: QualityComparison,
    candidate_run: Path,
) -> None:
    """Publish a complete vector-free review bundle through hidden staging."""
    if not isinstance(comparison, QualityComparison):
        raise TypeError("comparison must be a QualityComparison")
    if os.path.lexists(output):
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.quality.", dir=output.parent))
    try:
        review_crops = staging / "review-crops"
        threshold_crops = staging / "threshold-crops"
        review_crops.mkdir()
        threshold_crops.mkdir()
        crop_names: dict[str, str] = {}
        for rejection in comparison.new_rejections:
            source = _contained_crop(candidate_run, rejection.crop_path)
            if not source.is_file():
                raise ValueError("new-rejection crop is missing")
            crop_digest = hashlib.sha256(rejection.candidate_face_id.encode()).hexdigest()
            destination_name = f"{crop_digest}.png"
            shutil.copyfile(source, review_crops / destination_name)
            crop_names[rejection.candidate_face_id] = destination_name

        threshold_names: dict[str, str] = {}
        for samples in comparison.threshold_samples.values():
            for sample in samples:
                if sample.face_id in threshold_names:
                    continue
                source = _contained_crop(candidate_run, sample.crop_path)
                if source.is_symlink() or not source.is_file():
                    raise ValueError("threshold-sample crop is missing")
                destination_name = f"{hashlib.sha256(sample.face_id.encode()).hexdigest()}.png"
                shutil.copyfile(source, threshold_crops / destination_name)
                threshold_names[sample.face_id] = destination_name

        payload = _comparison_payload(comparison)
        digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
        _write_json(staging / "comparison.json", payload)
        _write_label_template(staging / "labels-template.csv", comparison)
        _write_text(
            staging / "report.html",
            _render_report(
                comparison,
                crop_names,
                threshold_names,
                _BUNDLE_DIGEST_PLACEHOLDER,
            ),
        )
        manifest_payload: dict[str, object] = {
            "artifact_type": "quality-comparison",
            "comparison_sha256": digest,
            "schema_version": SCHEMA_VERSION,
            "review_crops": [
                {
                    "face_id": face_id,
                    "path": f"review-crops/{name}",
                }
                for face_id, name in sorted(crop_names.items())
            ],
            "threshold_crops": [
                {
                    "face_id": face_id,
                    "metrics": sorted(
                        metric
                        for metric, samples in comparison.threshold_samples.items()
                        if any(sample.face_id == face_id for sample in samples)
                    ),
                    "path": f"threshold-crops/{name}",
                }
                for face_id, name in sorted(threshold_names.items())
            ],
        }
        identity_payload = {
            **manifest_payload,
            "files": _bundle_identity_file_rows(staging, _BUNDLE_DIGEST_PLACEHOLDER),
        }
        bundle_sha256 = hashlib.sha256(_canonical_json(identity_payload)).hexdigest()
        report_path = staging / "report.html"
        report = report_path.read_text(encoding="utf-8")
        if report.count(_BUNDLE_DIGEST_PLACEHOLDER) != 1:
            raise ValueError("quality report bundle placeholder is invalid")
        _write_text(
            report_path,
            report.replace(_BUNDLE_DIGEST_PLACEHOLDER, bundle_sha256),
        )
        manifest_payload["files"] = _bundle_file_rows(staging)
        manifest_payload["bundle_sha256"] = bundle_sha256
        _write_json(staging / "manifest.json", manifest_payload)
        if os.path.lexists(output):
            raise FileExistsError(output)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def load_quality_review_labels(
    path: Path, comparison: QualityComparison, bundle_sha256: str
) -> Mapping[str, str]:
    """Load one exact, complete annotation export without mutating draft state."""
    if not isinstance(bundle_sha256, str) or len(bundle_sha256) != 64:
        raise ValueError("quality bundle hash is invalid")
    expected_ids = {item.candidate_face_id for item in comparison.new_rejections}
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != LABEL_HEADERS:
                raise ValueError("quality label header is invalid")
            rows = list(reader)
    except OSError:
        raise ValueError("quality labels cannot be read") from None
    labels: dict[str, str] = {}
    for row in rows:
        if set(row) != set(LABEL_HEADERS) or None in row:
            raise ValueError("quality label row is invalid")
        face_id = row["face_id"]
        label = row["label"]
        if row["bundle_sha256"] != bundle_sha256:
            raise ValueError("quality label source hash differs")
        if face_id not in expected_ids:
            raise ValueError("quality label face is unknown")
        if face_id in labels:
            raise ValueError("duplicate quality label")
        if label not in ALLOWED_LABELS:
            raise ValueError("quality label is invalid")
        labels[face_id] = label
    if set(labels) != expected_ids:
        raise ValueError("every new rejection requires a label")
    return dict(sorted(labels.items()))


def load_quality_run(path: Path) -> tuple[QualityRun, Mapping[str, object]]:
    """Load one immutable cluster run with exact production quality evidence."""
    root = path.resolve()
    if path.is_symlink() or not root.is_dir():
        raise ValueError("quality run is invalid")
    manifest_path = root / "manifest.json"
    faces_path = root / "faces.json"
    if any(item.is_symlink() or not item.is_file() for item in (manifest_path, faces_path)):
        raise ValueError("quality run is incomplete")
    manifest = _load_json(manifest_path)
    payload = _load_json(faces_path)
    if set(payload) != {"images"}:
        raise ValueError("quality run faces schema is invalid")
    parameters = _mapping(manifest.get("parameters"))
    model_hashes = _require(manifest.get("model_hashes"), {"sface", "yunet"})
    source = _require(
        manifest.get("source"),
        {"faces_sha256", "generation_sha256", "inventory_sha256", "media_sha256"},
    )
    configuration = _quality_configuration_from_cluster_parameters(parameters)
    images = _list(payload["images"])
    photos: list[QualityPhoto] = []
    for raw_image in images:
        image = _require(raw_image, {"faces", "filename", "height", "status", "width"})
        filename = _string(image["filename"])
        status = _string(image["status"])
        raw_faces = _list(image["faces"])
        faces = tuple(_cluster_quality_face(raw_face, root) for raw_face in raw_faces)
        photos.append(
            QualityPhoto(
                filename,
                status,
                faces,
                None if status in {"ok", "no_detection"} else status,
            )
        )

    filenames = [photo.filename for photo in photos]
    media = tuple(
        (
            _string(item["filename"]),
            _string(item["sha256"]),
        )
        for raw in _list(source["media_sha256"])
        for item in (_require(raw, {"filename", "sha256"}),)
    )
    inventory_sha256 = _string(source["inventory_sha256"])
    generation_sha256 = _string(source["generation_sha256"])
    faces_sha256 = _string(source["faces_sha256"])
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
    generation_payload = {
        "model_hashes": dict(model_hashes),
        "parameters": generation_parameters,
    }
    if (
        filenames != sorted(filenames)
        or faces_sha256 != hashlib.sha256(_canonical_json(payload)).hexdigest()
        or inventory_sha256 != hashlib.sha256(_canonical_json(filenames)).hexdigest()
        or generation_sha256 != hashlib.sha256(_canonical_json(generation_payload)).hexdigest()
        or tuple(filename for filename, _digest in media) != tuple(filenames)
    ):
        raise ValueError("quality run source identity is invalid")
    run_sha256 = hashlib.sha256(
        _canonical_json(
            {
                "faces_sha256": _sha256_file(faces_path),
                "manifest_sha256": _sha256_file(manifest_path),
            }
        )
    ).hexdigest()
    return (
        QualityRun(
            run_sha256,
            inventory_sha256,
            media,
            generation_sha256,
            tuple(photos),
            tuple(
                sorted((_string(name), _string(digest)) for name, digest in model_hashes.items())
            ),
            {
                key: value
                for key, value in generation_parameters.items()
                if key
                not in {
                    "min_face_px",
                    "quality_algorithm_version",
                    "quality_crop_size",
                    "severe_blur_threshold",
                    "borderline_blur_threshold",
                    "minimum_confidence",
                    "minimum_relative_area",
                }
            },
            configuration,
        ),
        configuration,
    )


def load_quality_comparison_bundle(path: Path) -> tuple[QualityComparison, str]:
    root = path.resolve()
    expected = {
        "comparison.json",
        "labels-template.csv",
        "manifest.json",
        "report.html",
        "review-crops",
        "threshold-crops",
    }
    if (
        path.is_symlink()
        or not root.is_dir()
        or {child.name for child in root.iterdir()} != expected
    ):
        raise ValueError("quality comparison bundle is incomplete")
    if any(
        item.is_symlink() or not item.is_file()
        for item in (
            root / "comparison.json",
            root / "labels-template.csv",
            root / "manifest.json",
            root / "report.html",
        )
    ) or any(
        item.is_symlink() or not item.is_dir()
        for item in (root / "review-crops", root / "threshold-crops")
    ):
        raise ValueError("quality comparison bundle types are invalid")
    manifest = _load_json(root / "manifest.json")
    payload = _load_json(root / "comparison.json")
    manifest = _require(
        manifest,
        {
            "artifact_type",
            "bundle_sha256",
            "comparison_sha256",
            "files",
            "review_crops",
            "schema_version",
            "threshold_crops",
        },
    )
    bundle_sha256 = _string(manifest["bundle_sha256"])
    identity_manifest = {
        key: value for key, value in manifest.items() if key not in {"bundle_sha256", "files"}
    }
    identity_manifest["files"] = _bundle_identity_file_rows(root, bundle_sha256)
    if (
        manifest["artifact_type"] != "quality-comparison"
        or manifest["schema_version"] != SCHEMA_VERSION
        or manifest["comparison_sha256"] != hashlib.sha256(_canonical_json(payload)).hexdigest()
        or bundle_sha256 != hashlib.sha256(_canonical_json(identity_manifest)).hexdigest()
    ):
        raise ValueError("quality comparison manifest is invalid")
    _validate_bundle_files(root, manifest["files"])
    if (
        set(payload)
        != {
            "schema_version",
            "source",
            "counts",
            "quality_configuration",
            "outcomes",
            "matches",
            "new_rejections",
            "sampling_configuration",
            "threshold_samples",
            "metric_distributions",
            "rejection_reason_counts",
            "technical_reason_counts",
            "unresolved_photos",
            "technical_failures",
        }
        or payload["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError("quality comparison schema is invalid")
    source = _require(
        payload["source"],
        {
            "baseline_run_sha256",
            "candidate_run_sha256",
            "inventory_sha256",
            "media_sha256",
            "baseline_generation_sha256",
            "candidate_generation_sha256",
            "minimum_iou",
        },
    )
    counts = _mapping(payload.get("counts"))
    configuration = _quality_configuration(payload.get("quality_configuration"))
    matches = tuple(_face_match(value) for value in _list(payload.get("matches")))
    rejections = tuple(_new_rejection(value) for value in _list(payload.get("new_rejections")))
    outcomes = _require(payload["outcomes"], {"baseline", "candidate"})
    baseline_outcomes = tuple(_quality_outcome(value) for value in _list(outcomes["baseline"]))
    candidate_outcomes = tuple(_quality_outcome(value) for value in _list(outcomes["candidate"]))
    samples = _mapping(payload["threshold_samples"])
    sampling = _require(
        payload["sampling_configuration"],
        {"samples_per_metric", "threshold_band_fraction"},
    )
    sample_values = {
        name: tuple(_threshold_sample(name, item) for item in _list(values))
        for name, values in samples.items()
    }
    failures = tuple(
        TechnicalFailure(
            _string(item["cohort"]),
            _string(item["filename"]),
            _optional_string(item["face_id"]),
            _string(item["reason"]),
        )
        for raw in _list(payload["technical_failures"])
        for item in (_require(raw, {"cohort", "filename", "face_id", "reason"}),)
    )
    media_rows = _list(source["media_sha256"])
    media = tuple(
        (_string(item[0]), _string(item[1]))
        for item in media_rows
        if isinstance(item, list) and len(item) == 2
    )
    if len(media) != len(media_rows):
        raise ValueError("quality comparison media is invalid")
    comparison = QualityComparison(
        _string(source["baseline_run_sha256"]),
        _string(source["candidate_run_sha256"]),
        _string(source["inventory_sha256"]),
        media,
        _string(source["baseline_generation_sha256"]),
        _string(source["candidate_generation_sha256"]),
        _number(source["minimum_iou"]),
        configuration,
        {name: _integer(value) for name, value in counts.items()},
        baseline_outcomes,
        candidate_outcomes,
        matches,
        rejections,
        _number(sampling["threshold_band_fraction"]),
        _integer(sampling["samples_per_metric"]),
        sample_values,
        _nested_number_sequences(payload["metric_distributions"]),
        _nested_integer_mapping(payload["rejection_reason_counts"]),
        _nested_integer_mapping(payload["technical_reason_counts"]),
        tuple(_string(item) for item in _list(payload["unresolved_photos"])),
        failures,
    )
    if quality_comparison_sha256(comparison) != manifest["comparison_sha256"]:
        raise ValueError("quality comparison digest is invalid")
    _validate_bundle_crop_coverage(root, manifest, comparison)
    return comparison, bundle_sha256


def write_search_comparison(output: Path, result: object) -> None:
    from .smoke_search import SearchComparison

    if not isinstance(result, SearchComparison):
        raise TypeError("search comparison is invalid")
    if os.path.lexists(output):
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.search.", dir=output.parent))
    try:
        payload = _search_payload(result)
        digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
        _write_json(staging / "search-comparison.json", payload)
        _write_json(
            staging / "manifest.json",
            {
                "artifact_type": "quality-search-comparison",
                "schema_version": SCHEMA_VERSION,
                "search_comparison_sha256": digest,
            },
        )
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def load_search_comparison(path: Path):
    from .smoke_search import (
        SearchComparison,
        SearchComparisonPhoto,
        SearchComparisonQueryResult,
    )

    root = path.resolve()
    if not root.is_dir() or {child.name for child in root.iterdir()} != {
        "manifest.json",
        "search-comparison.json",
    }:
        raise ValueError("search comparison bundle is incomplete")
    payload = _load_json(root / "search-comparison.json")
    manifest = _load_json(root / "manifest.json")
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    if manifest != {
        "artifact_type": "quality-search-comparison",
        "schema_version": SCHEMA_VERSION,
        "search_comparison_sha256": digest,
    }:
        raise ValueError("search comparison manifest is invalid")
    if set(payload) != {
        "schema_version",
        "source",
        "direct_threshold",
        "queries",
        "aggregate",
        "approved",
    }:
        raise ValueError("search comparison schema is invalid")
    source = _require(
        payload["source"],
        {
            "quality_comparison_sha256",
            "benchmark_sha256",
            "baseline_index_sha256",
            "candidate_index_sha256",
        },
    )
    values = tuple(
        SearchComparisonQueryResult(
            _string(item["query_id"]),
            _string(item["source_run_sha256"]),
            _string(item["query_crop_sha256"]),
            tuple(
                SearchComparisonPhoto(_string(value["face_id"]), _string(value["filename"]))
                for raw_value in _list(item["baseline_results"])
                for value in (_require(raw_value, {"face_id", "filename"}),)
            ),
            tuple(
                SearchComparisonPhoto(_string(value["face_id"]), _string(value["filename"]))
                for raw_value in _list(item["candidate_results"])
                for value in (_require(raw_value, {"face_id", "filename"}),)
            ),
            tuple(_string(value) for value in _list(item["confirmed_relevant"])),
            tuple(_string(value) for value in _list(item["lost_confirmed_relevant"])),
            tuple(
                SearchComparisonPhoto(_string(value["face_id"]), _string(value["filename"]))
                for raw_value in _list(item["quality_rejected_supports"])
                for value in (_require(raw_value, {"face_id", "filename"}),)
            ),
        )
        for raw in _list(payload["queries"])
        for item in (
            _require(
                raw,
                {
                    "query_id",
                    "source_run_sha256",
                    "query_crop_sha256",
                    "baseline_results",
                    "candidate_results",
                    "confirmed_relevant",
                    "lost_confirmed_relevant",
                    "quality_rejected_supports",
                },
            ),
        )
    )
    result = SearchComparison(
        _string(source["quality_comparison_sha256"]),
        _string(source["benchmark_sha256"]),
        _string(source["baseline_index_sha256"]),
        _string(source["candidate_index_sha256"]),
        _number(payload["direct_threshold"]),
        values,
    )
    if result.aggregate != payload["aggregate"] or result.approved is not payload["approved"]:
        raise ValueError("search comparison aggregate is invalid")
    return result


def _comparison_payload(comparison: QualityComparison) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "baseline_run_sha256": comparison.baseline_run_sha256,
            "candidate_run_sha256": comparison.candidate_run_sha256,
            "inventory_sha256": comparison.inventory_sha256,
            "media_sha256": [list(item) for item in comparison.media_sha256],
            "baseline_generation_sha256": comparison.baseline_generation_sha256,
            "candidate_generation_sha256": comparison.candidate_generation_sha256,
            "minimum_iou": comparison.minimum_iou,
        },
        "counts": dict(comparison.counts),
        "outcomes": {
            "baseline": [_outcome_payload(item) for item in comparison.baseline_outcomes],
            "candidate": [_outcome_payload(item) for item in comparison.candidate_outcomes],
        },
        "quality_configuration": dict(comparison.quality_configuration),
        "matches": [
            {
                "filename": item.filename,
                "baseline_face_id": item.baseline_face_id,
                "candidate_face_id": item.candidate_face_id,
                "intersection_over_union": item.intersection_over_union,
            }
            for item in comparison.matches
        ],
        "new_rejections": [
            {
                "filename": item.filename,
                "baseline_face_id": item.baseline_face_id,
                "candidate_face_id": item.candidate_face_id,
                "crop_path": item.crop_path,
                "bounding_box": {
                    "x": float(item.bounding_box.x),
                    "y": float(item.bounding_box.y),
                    "width": float(item.bounding_box.width),
                    "height": float(item.bounding_box.height),
                },
                "reasons": list(item.reasons),
                "quality": {
                    "algorithm_version": "normalized-laplacian-v1",
                    "crop_size": 112,
                    "confidence": float(item.confidence),
                    "minimum_side_px": float(item.minimum_side_px),
                    "relative_area": float(item.relative_area),
                    "sharpness": float(item.sharpness),
                    "decision": "quality_rejected",
                },
            }
            for item in comparison.new_rejections
        ],
        "sampling_configuration": {
            "threshold_band_fraction": comparison.threshold_band_fraction,
            "samples_per_metric": comparison.samples_per_metric,
        },
        "threshold_samples": {
            name: [
                {
                    "face_id": sample.face_id,
                    "filename": sample.filename,
                    "crop_path": sample.crop_path,
                    "value": sample.value,
                    "threshold": sample.threshold,
                }
                for sample in samples
            ]
            for name, samples in comparison.threshold_samples.items()
        },
        "metric_distributions": {
            cohort: {metric: list(values) for metric, values in metrics.items()}
            for cohort, metrics in comparison.metric_distributions.items()
        },
        "rejection_reason_counts": {
            cohort: dict(values) for cohort, values in comparison.rejection_reason_counts.items()
        },
        "technical_reason_counts": {
            cohort: dict(values) for cohort, values in comparison.technical_reason_counts.items()
        },
        "unresolved_photos": list(comparison.unresolved_photos),
        "technical_failures": [
            {
                "cohort": item.cohort,
                "filename": item.filename,
                "face_id": item.face_id,
                "reason": item.reason,
            }
            for item in comparison.technical_failures
        ],
    }


def _outcome_payload(item: QualityOutcome) -> dict[str, object]:
    return {
        "cohort": item.cohort,
        "filename": item.filename,
        "face_id": item.face_id,
        "status": item.status,
        "embedded": item.embedded,
        "crop_path": item.crop_path,
        "confidence": float(item.confidence),
        "minimum_side_px": float(item.minimum_side_px),
        "relative_area": float(item.relative_area),
        "sharpness": float(item.sharpness),
        "reasons": list(item.reasons),
        "technical_failure": item.technical_failure,
    }


def _search_payload(result: SearchComparison) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "quality_comparison_sha256": result.quality_comparison_sha256,
            "benchmark_sha256": result.benchmark_sha256,
            "baseline_index_sha256": result.baseline_index_sha256,
            "candidate_index_sha256": result.candidate_index_sha256,
        },
        "direct_threshold": result.direct_threshold,
        "queries": [
            {
                "query_id": item.query_id,
                "source_run_sha256": item.source_run_sha256,
                "query_crop_sha256": item.query_crop_sha256,
                "baseline_results": [
                    {"face_id": photo.face_id, "filename": photo.filename}
                    for photo in item.baseline_results
                ],
                "candidate_results": [
                    {"face_id": photo.face_id, "filename": photo.filename}
                    for photo in item.candidate_results
                ],
                "confirmed_relevant": list(item.confirmed_relevant),
                "lost_confirmed_relevant": list(item.lost_confirmed_relevant),
                "quality_rejected_supports": [
                    {"face_id": photo.face_id, "filename": photo.filename}
                    for photo in item.quality_rejected_supports
                ],
            }
            for item in result.query_results
        ],
        "aggregate": result.aggregate,
        "approved": result.approved,
    }


def _cluster_quality_face(value: object, root: Path) -> QualityFace:
    from photo_worker.face_quality import FaceQualityEvidence

    item = _require(
        value,
        {
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
        },
    )
    raw_quality = _require(
        item["quality"],
        {
            "algorithm_version",
            "crop_size",
            "confidence",
            "minimum_side_px",
            "relative_area",
            "sharpness",
            "decision",
            "reasons",
        },
    )
    quality = FaceQualityEvidence(
        _string(raw_quality["algorithm_version"]),
        _integer(raw_quality["crop_size"]),
        _number(raw_quality["confidence"]),
        _number(raw_quality["minimum_side_px"]),
        _number(raw_quality["relative_area"]),
        _number(raw_quality["sharpness"]),
        _string(raw_quality["decision"]),
        tuple(_string(reason) for reason in _list(raw_quality["reasons"])),
    )
    status = _string(item["status"])
    error_code = _string(item["error_code"])
    if status == "ok":
        comparison_status = "accepted"
        technical_failure = None
    elif status == "quality_rejected":
        comparison_status = "quality_rejected"
        technical_failure = None
    elif status in {"alignment_failed", "embedding_failed", "invalid_embedding"}:
        comparison_status = "technical_failed"
        technical_failure = error_code
    else:
        raise ValueError("quality run face status is invalid")
    face_index = _integer(item["face_index"])
    landmarks = _require(
        item["landmarks"],
        {"left_eye", "left_mouth_corner", "nose", "right_eye", "right_mouth_corner"},
    )
    for point in landmarks.values():
        values = _list(point)
        if len(values) != 2:
            raise ValueError("quality run landmarks are invalid")
        tuple(_number(coordinate) for coordinate in values)
    crop_path = _string(item["crop_path"])
    crop = _contained_crop(root, crop_path)
    if (
        face_index < 1
        or _number(item["confidence"]) != quality.confidence
        or error_code != ("" if status == "ok" else status)
        or crop.is_symlink()
        or not crop.is_file()
    ):
        raise ValueError("quality run face is invalid")
    return QualityFace(
        _string(item["face_id"]),
        _string(item["filename"]),
        BoundingBox(
            _number(item["x"]),
            _number(item["y"]),
            _number(item["width"]),
            _number(item["height"]),
        ),
        crop_path,
        comparison_status,
        quality,
        technical_failure,
    )


def _quality_configuration_from_cluster_parameters(
    parameters: Mapping[str, object],
) -> Mapping[str, object]:
    return _quality_configuration(
        {
            "algorithm_version": parameters.get("quality_algorithm_version"),
            "crop_size": parameters.get("quality_crop_size"),
            "minimum_face_px": parameters.get("min_face_px"),
            "severe_blur_threshold": parameters.get("severe_blur_threshold"),
            "borderline_blur_threshold": parameters.get("borderline_blur_threshold"),
            "minimum_relative_area": parameters.get("minimum_relative_area"),
            "minimum_confidence": parameters.get("minimum_confidence"),
        }
    )


def _quality_configuration(value: object) -> Mapping[str, object]:
    item = _require(
        value,
        {
            "algorithm_version",
            "crop_size",
            "minimum_face_px",
            "severe_blur_threshold",
            "borderline_blur_threshold",
            "minimum_relative_area",
            "minimum_confidence",
        },
    )
    return {
        "algorithm_version": _string(item["algorithm_version"]),
        "crop_size": _integer(item["crop_size"]),
        "minimum_face_px": _integer(item["minimum_face_px"]),
        "severe_blur_threshold": _number(item["severe_blur_threshold"]),
        "borderline_blur_threshold": _number(item["borderline_blur_threshold"]),
        "minimum_relative_area": _number(item["minimum_relative_area"]),
        "minimum_confidence": _number(item["minimum_confidence"]),
    }


def _face_match(value: object) -> FaceMatch:
    item = _require(
        value,
        {"filename", "baseline_face_id", "candidate_face_id", "intersection_over_union"},
    )
    return FaceMatch(
        _string(item["filename"]),
        _string(item["baseline_face_id"]),
        _string(item["candidate_face_id"]),
        _number(item["intersection_over_union"]),
    )


def _new_rejection(value: object) -> NewRejection:
    item = _require(
        value,
        {
            "filename",
            "baseline_face_id",
            "candidate_face_id",
            "crop_path",
            "bounding_box",
            "reasons",
            "quality",
        },
    )
    quality = _require(
        item["quality"],
        {
            "algorithm_version",
            "crop_size",
            "confidence",
            "minimum_side_px",
            "relative_area",
            "sharpness",
            "decision",
        },
    )
    if (
        quality["algorithm_version"] != "normalized-laplacian-v1"
        or quality["crop_size"] != 112
        or quality["decision"] != "quality_rejected"
    ):
        raise ValueError("new rejection quality evidence is invalid")
    return NewRejection(
        _string(item["filename"]),
        _string(item["baseline_face_id"]),
        _string(item["candidate_face_id"]),
        _string(item["crop_path"]),
        _box(item["bounding_box"]),
        tuple(_string(reason) for reason in _list(item["reasons"])),
        _number(quality["confidence"]),
        _number(quality["minimum_side_px"]),
        _number(quality["relative_area"]),
        _number(quality["sharpness"]),
    )


def _quality_outcome(value: object) -> QualityOutcome:
    item = _require(
        value,
        {
            "cohort",
            "filename",
            "face_id",
            "status",
            "embedded",
            "crop_path",
            "confidence",
            "minimum_side_px",
            "relative_area",
            "sharpness",
            "reasons",
            "technical_failure",
        },
    )
    return QualityOutcome(
        _string(item["cohort"]),
        _string(item["filename"]),
        _string(item["face_id"]),
        _string(item["status"]),
        _boolean(item["embedded"]),
        _string(item["crop_path"]),
        _number(item["confidence"]),
        _number(item["minimum_side_px"]),
        _number(item["relative_area"]),
        _number(item["sharpness"]),
        tuple(_string(reason) for reason in _list(item["reasons"])),
        _optional_string(item["technical_failure"]),
    )


def _threshold_sample(metric: str, value: object) -> ThresholdSample:
    item = _require(value, {"face_id", "filename", "crop_path", "value", "threshold"})
    return ThresholdSample(
        metric,
        _string(item["face_id"]),
        _string(item["filename"]),
        _string(item["crop_path"]),
        _number(item["value"]),
        _number(item["threshold"]),
    )


def _nested_number_sequences(value: object) -> Mapping[str, Mapping[str, tuple[float, ...]]]:
    return {
        cohort: {
            metric: tuple(_number(item) for item in _list(values))
            for metric, values in _mapping(metrics).items()
        }
        for cohort, metrics in _mapping(value).items()
    }


def _nested_integer_mapping(value: object) -> Mapping[str, Mapping[str, int]]:
    return {
        cohort: {reason: _integer(count) for reason, count in _mapping(values).items()}
        for cohort, values in _mapping(value).items()
    }


def _box(value: object) -> BoundingBox:
    item = _require(value, {"x", "y", "width", "height"})
    return BoundingBox(*(_number(item[name]) for name in ("x", "y", "width", "height")))


def _load_json(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("quality JSON cannot be read") from None
    return _mapping(value)


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("expected JSON object")
    return value


def _require(value: object, keys: set[str]) -> Mapping[str, object]:
    item = _mapping(value)
    if set(item) != keys:
        raise ValueError("quality schema is invalid")
    return item


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("expected JSON list")
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise ValueError("expected optional string")


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("expected integer")
    return value


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("expected boolean")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected number")
    return float(value)


def _render_report(
    comparison: QualityComparison,
    crop_names: Mapping[str, str],
    threshold_names: Mapping[str, str],
    bundle_sha256: str,
) -> str:
    cards = "".join(
        (
            f'<article data-face-id="{html.escape(item.candidate_face_id, quote=True)}">'
            f"<h2>{html.escape(item.candidate_face_id)}</h2>"
            f'<img src="review-crops/{crop_names[item.candidate_face_id]}" '
            f'alt="Review crop for {html.escape(item.candidate_face_id, quote=True)}">'
            f"<p>{html.escape(', '.join(item.reasons))}</p>"
            '<select class="quality-label"><option value="">Select</option>'
            '<option value="clear">clear</option><option value="blurred">blurred</option>'
            '<option value="unusably_small">unusably_small</option>'
            '<option value="uncertain">uncertain</option></select></article>'
        )
        for item in comparison.new_rejections
    )
    threshold_cards = "".join(
        (
            f'<article data-threshold-face-id="{html.escape(face_id, quote=True)}">'
            f"<h2>Retained threshold sample: {html.escape(face_id)}</h2>"
            f'<img src="threshold-crops/{name}" '
            f'alt="Threshold sample for {html.escape(face_id, quote=True)}"></article>'
        )
        for face_id, name in sorted(threshold_names.items())
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>Quality review</title>'
        "<style>img{max-width:240px;height:auto}article{margin:1rem 0}</style></head><body>"
        "<h1>New quality-gate rejections</h1>"
        "<p>Every rejection requires one label. Retained threshold samples follow.</p>"
        f"{cards}<h1>Retained threshold samples</h1>{threshold_cards}"
        "<button id=export>Export complete CSV</button><script>"
        "const allowed=new Set(["
        '"clear","blurred","unusably_small","uncertain"]);'
        f"const source={json.dumps(bundle_sha256)};"
        "document.querySelector('#export').onclick=()=>{const rows=[];"
        "for(const card of document.querySelectorAll('[data-face-id]')){"
        "const label=card.querySelector('select').value;if(!allowed.has(label)){"
        "alert('Label every rejection before export');return;}"
        "rows.push([source,card.dataset.faceId,label]);}"
        "const quote=v=>'\"'+v.replaceAll('\"','\"\"')+'\"';"
        "const csv=['bundle_sha256,face_id,label',...rows.map(r=>r.map(quote).join(','))]"
        ".join('\\r\\n')+'\\r\\n';const link=document.createElement('a');"
        "link.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'}));"
        "link.download='quality-review-labels.csv';link.click();URL.revokeObjectURL(link.href);};"
        "</script></body></html>\n"
    )


def _contained_crop(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or path.parts[:1] != ("faces",) or len(path.parts) != 2:
        raise ValueError("unsafe crop path")
    candidate = root / path
    if candidate.parent.resolve() != (root / "faces").resolve():
        raise ValueError("unsafe crop path")
    return candidate


def _write_label_template(path: Path, comparison: QualityComparison) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\r\n")
            writer.writerow(LABEL_HEADERS)
            for item in comparison.new_rejections:
                writer.writerow(("", item.candidate_face_id, ""))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise ValueError("quality run cannot be read") from None
    return digest.hexdigest()


def _bundle_file_rows(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_file(path),
            "size": path.stat().st_size,
        }
        for path in sorted(
            item for item in root.rglob("*") if item.is_file() and item.name != "manifest.json"
        )
    ]


def _bundle_identity_file_rows(root: Path, embedded_digest: str) -> list[dict[str, object]]:
    rows = _bundle_file_rows(root)
    report_path = root / "report.html"
    report = report_path.read_text(encoding="utf-8")
    if report.count(embedded_digest) != 1:
        raise ValueError("quality report bundle digest is invalid")
    normalized = report.replace(embedded_digest, _BUNDLE_DIGEST_PLACEHOLDER).encode("utf-8")
    for row in rows:
        if row["path"] == "report.html":
            row["sha256"] = hashlib.sha256(normalized).hexdigest()
            row["size"] = len(normalized)
    return rows


def _validate_bundle_files(root: Path, value: object) -> None:
    rows = _list(value)
    declared: set[str] = set()
    for raw in rows:
        item = _require(raw, {"path", "sha256", "size"})
        relative = _string(item["path"])
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or relative in declared:
            raise ValueError("quality bundle file manifest is invalid")
        target = root / path
        if (
            target.is_symlink()
            or not target.is_file()
            or _integer(item["size"]) != target.stat().st_size
            or _string(item["sha256"]) != _sha256_file(target)
        ):
            raise ValueError("quality bundle file differs")
        declared.add(relative)
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if declared != actual or any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("quality bundle file coverage is invalid")


def _validate_bundle_crop_coverage(
    root: Path, manifest: Mapping[str, object], comparison: QualityComparison
) -> None:
    review_rows = [_require(item, {"face_id", "path"}) for item in _list(manifest["review_crops"])]
    review = {_string(item["face_id"]): _string(item["path"]) for item in review_rows}
    expected_review = {item.candidate_face_id for item in comparison.new_rejections}
    if len(review) != len(review_rows) or set(review) != expected_review:
        raise ValueError("review crop coverage is invalid")
    threshold_rows = [
        _require(item, {"face_id", "metrics", "path"})
        for item in _list(manifest["threshold_crops"])
    ]
    threshold = {_string(item["face_id"]): item for item in threshold_rows}
    expected_threshold = {
        sample.face_id for samples in comparison.threshold_samples.values() for sample in samples
    }
    if len(threshold) != len(threshold_rows) or set(threshold) != expected_threshold:
        raise ValueError("threshold crop coverage is invalid")
    for face_id, item in threshold.items():
        expected_metrics = sorted(
            metric
            for metric, samples in comparison.threshold_samples.items()
            if any(sample.face_id == face_id for sample in samples)
        )
        if [_string(value) for value in _list(item["metrics"])] != expected_metrics:
            raise ValueError("threshold crop metrics are invalid")
    declared_paths = {Path(value) for value in review.values()} | {
        Path(_string(item["path"])) for item in threshold.values()
    }
    actual_paths = {
        path.relative_to(root)
        for directory in (root / "review-crops", root / "threshold-crops")
        for path in directory.iterdir()
    }
    if declared_paths != actual_paths:
        raise ValueError("quality crop paths are invalid")


def _write_json(path: Path, value: object) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
    )


def _write_text(path: Path, value: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

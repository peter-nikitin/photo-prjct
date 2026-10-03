from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .artifacts import publish_immutable
from .normalize import normalize_image
from .review import ReviewRow
from .runner import (
    DETECTOR_COHORTS,
    VARIANTS,
    Detection,
    classify_detections,
    detection_payload,
    validate_detector_cohort,
)
from .snapshot import load_snapshot, snapshot_manifest_sha256

_REVISION = re.compile(r"[0-9a-f]{7,64}\Z")


class OpenCvYuNetDetector:
    """Thin, local wrapper around the exact YuNet model supplied by the operator."""

    def __init__(self, model: Path, threshold: float = 0.75) -> None:
        import cv2

        self._cv2 = cv2
        self._detector = cv2.FaceDetectorYN.create(str(model), "", (320, 320), threshold, 0.3, 5000)

    def detect(self, content: bytes) -> tuple[Any, tuple[Detection, ...]]:
        import numpy as np

        image = self._cv2.imdecode(np.frombuffer(content, dtype=np.uint8), self._cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("unsupported image")
        height, width = image.shape[:2]
        self._detector.setInputSize((width, height))
        _, faces = self._detector.detect(image)
        return image, tuple(
            Detection(
                float(face[0]),
                float(face[1]),
                float(face[2]),
                float(face[3]),
                float(face[14]),
                tuple(float(value) for value in face[4:14]),
            )
            for face in ([] if faces is None else faces)
        )


def run_offline(
    snapshot: Path, model: Path, output: Path, *, experiment_revision: str
) -> tuple[ReviewRow, ...]:
    """Evaluate all detector-cohort inputs locally and atomically publish only complete evidence."""
    validate_experiment_revision(experiment_revision)
    records = tuple(
        record
        for record in load_snapshot(snapshot, expected_count=40)
        if record["cohort"] in DETECTOR_COHORTS
    )
    validate_detector_cohort(records)
    detector = OpenCvYuNetDetector(model)
    model_sha256 = hashlib.sha256(model.read_bytes()).hexdigest()
    case_evidence: list[dict[str, object]] = []
    review_rows: list[ReviewRow] = []
    for record in records:
        content = (snapshot / "objects" / record["object_name"]).read_bytes()
        normalized = normalize_image(content)
        variants = (
            ("baseline-original", content, False),
            ("normalized-1600", normalized.content, False),
            ("normalized-1600-quality", normalized.content, True),
        )
        evidence: list[dict[str, object]] = []
        for variant, variant_content, quality_enabled in variants:
            started = time.perf_counter()
            image, detections = detector.detect(variant_content)
            accepted, qualities = (
                _quality_decisions(image, detections) if quality_enabled else (None, None)
            )
            outcome = classify_detections(
                detections, accepted=accepted, quality_enabled=quality_enabled
            )
            evidence.append(
                {
                    "variant": variant,
                    "input_geometry": {
                        "width": image.shape[1],
                        "height": image.shape[0],
                        "resize_scale": normalized.resize_scale
                        if variant != "baseline-original"
                        else 1.0,
                    },
                    "raw_detection_count": outcome.raw_detection_count,
                    "accepted_detection_count": outcome.accepted_detection_count,
                    "outcome": outcome.outcome,
                    "detections": [detection_payload(item) for item in detections],
                    "quality": qualities,
                    "runtime_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            review_rows.append(
                ReviewRow(str(record["case_id"]), variant, str(record["cohort"]), outcome.outcome)
            )
        case_evidence.append(
            {
                "case_id": record["case_id"],
                "cohort": record["cohort"],
                "original_geometry": {
                    "width": normalized.original_size[0],
                    "height": normalized.original_size[1],
                },
                "variants": evidence,
            }
        )

    def write(stage: Path) -> None:
        _write_json(stage / "evidence.json", {"cases": case_evidence})
        _write_json(stage / "review-rows.json", {"rows": [asdict(row) for row in review_rows]})
        _write_json(stage / "metrics.json", _run_metrics(review_rows))
        _write_visual_evidence(stage, snapshot, records, case_evidence, detector)
        (stage / "report.html").write_text(_run_report(case_evidence), encoding="utf-8")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": "detector-run",
            "snapshot_manifest_sha256": snapshot_manifest_sha256(snapshot),
            "model_sha256": model_sha256,
            "experiment_revision": experiment_revision,
            "variants": list(VARIANTS),
            "case_count": len(records),
        }
        manifest["run_identity"] = _run_identity(stage, manifest)
        _write_json(stage / "manifest.json", manifest)

    publish_immutable(output, write)
    return tuple(review_rows)


def load_review_rows(run: Path) -> tuple[ReviewRow, ...]:
    verify_run(run)
    try:
        payload = json.loads((run / "review-rows.json").read_text(encoding="utf-8"))
        rows = payload["rows"]
        if not isinstance(rows, list):
            raise ValueError
        return tuple(ReviewRow(**row) for row in rows)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("detector run review rows are invalid") from error


def verify_run(run: Path) -> str:
    """Verify the complete immutable run, including every manual-review visual."""
    try:
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("detector run manifest is invalid") from error
    required = {
        "schema_version",
        "artifact_type",
        "snapshot_manifest_sha256",
        "model_sha256",
        "experiment_revision",
        "variants",
        "case_count",
        "run_identity",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != required
        or manifest["schema_version"] != SCHEMA_VERSION
        or manifest["artifact_type"] != "detector-run"
        or not isinstance(manifest["run_identity"], str)
    ):
        raise ValueError("detector run manifest is invalid")
    identity = manifest.pop("run_identity")
    if not _REVISION.fullmatch(manifest["experiment_revision"]):
        raise ValueError("detector run manifest is invalid")
    if identity != _run_identity(run, manifest):
        raise ValueError("detector run evidence identity is invalid")
    return identity


def validate_experiment_revision(value: object) -> None:
    if not isinstance(value, str) or _REVISION.fullmatch(value) is None:
        raise ValueError("experiment revision must be a nonempty immutable revision")


def _quality_decisions(
    image: Any, detections: tuple[Detection, ...]
) -> tuple[tuple[bool, ...], list[dict[str, object]]]:
    from photo_worker.face_quality import FaceQualityThresholds, evaluate_face_quality

    thresholds = FaceQualityThresholds("normalized-laplacian-v1", 112, 32, 25, 50, 0.0009, 0.82)
    values = [
        evaluate_face_quality(
            image,
            bbox=(item.x, item.y, item.width, item.height),
            confidence=item.confidence,
            thresholds=thresholds,
        )
        for item in detections
    ]
    return tuple(value.decision == "accepted" for value in values), [
        value.as_payload() for value in values
    ]


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )


def _run_identity(root: Path, manifest: dict[str, object]) -> str:
    artifacts = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    payload = {"manifest": manifest, "artifacts": artifacts}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _run_metrics(rows: list[ReviewRow]) -> dict[str, object]:
    metrics: dict[str, dict[str, dict[str, int]]] = {}
    for row in rows:
        bucket = metrics.setdefault(row.variant, {}).setdefault(row.cohort, {})
        bucket[row.outcome] = bucket.get(row.outcome, 0) + 1
    return {"case_count": len(rows) // len(VARIANTS), "outcomes": metrics}


def _write_visual_evidence(
    stage: Path,
    snapshot: Path,
    records: tuple[dict[str, Any], ...],
    cases: list[dict[str, object]],
    detector: OpenCvYuNetDetector,
) -> None:
    originals, annotated, crops = (stage / "originals", stage / "annotated", stage / "crops")
    originals.mkdir()
    annotated.mkdir()
    crops.mkdir()
    for record, case in zip(records, cases, strict=True):
        case_id = str(record["case_id"])
        content = (snapshot / "objects" / str(record["object_name"])).read_bytes()
        for variant in case["variants"]:
            variant_name = str(variant["variant"])
            source = (
                content if variant_name == "baseline-original" else normalize_image(content).content
            )
            image, _ = detector.detect(source)
            visual = image.copy()
            for number, detection in enumerate(variant["detections"]):
                x, y = float(detection["x"]), float(detection["y"])
                width, height = float(detection["width"]), float(detection["height"])
                detector._cv2.rectangle(
                    visual,
                    (round(x), round(y)),
                    (round(x + width), round(y + height)),
                    (0, 255, 0),
                    3,
                )
                if number < 20:
                    left, top = max(0, int(x)), max(0, int(y))
                    right, bottom = (
                        min(image.shape[1], int(x + width)),
                        min(image.shape[0], int(y + height)),
                    )
                    if right > left and bottom > top:
                        crop = image[top:bottom, left:right]
                        crop = _bounded_visual(crop, detector, maximum=512)
                        detector._cv2.imwrite(
                            str(crops / f"{case_id}-{variant_name}-{number + 1:02d}.jpg"), crop
                        )
            visual = _bounded_visual(visual, detector)
            detector._cv2.imwrite(str(annotated / f"{case_id}-{variant_name}.jpg"), visual)
            if variant_name == "baseline-original":
                detector._cv2.imwrite(
                    str(originals / f"{case_id}.jpg"), _bounded_visual(image, detector)
                )


def _bounded_visual(image: Any, detector: OpenCvYuNetDetector, *, maximum: int = 1600) -> Any:
    if max(image.shape[:2]) <= maximum:
        return image
    scale = maximum / max(image.shape[:2])
    return detector._cv2.resize(
        image, (round(image.shape[1] * scale), round(image.shape[0] * scale))
    )


def _run_report(cases: list[dict[str, object]]) -> str:
    rows = "\n".join(
        "<tr><td>{case}</td><td><img src='originals/{case}.jpg'></td>{variants}</tr>".format(
            case=case["case_id"],
            variants="".join(
                "<td><img src='annotated/{case}-{variant}.jpg'><br>{outcome}</td>".format(
                    case=case["case_id"], variant=variant["variant"], outcome=variant["outcome"]
                )
                for variant in case["variants"]
            ),
        )
        for case in cases
    )
    return (
        "<!doctype html><meta charset=utf-8><title>Private detector run</title>"
        "<style>img{max-width:240px;max-height:180px}td{vertical-align:top}</style>"
        "<table><thead><tr><th>Case</th><th>Original</th><th>Baseline</th>"
        f"<th>Normalized</th><th>Normalized + quality</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
    )

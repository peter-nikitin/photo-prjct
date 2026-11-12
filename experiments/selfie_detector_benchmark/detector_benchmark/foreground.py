from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from . import SCHEMA_VERSION
from .artifacts import publish_immutable
from .offline import _REVISION, _run_identity, verify_run
from .review import (
    ReviewRow,
    build_identity_bound_review,
    finalize_identity_bound_review,
    load_identity_bound_labels,
    require_certain_identity_bound_review,
)
from .runner import VARIANTS, Detection, classify_detections, validate_detector_cohort

SOURCE_RUN_IDENTITY = "19f58e027c3aca32487d13ef3e420fca9ade15fc189c7bd7d70625b39cc101aa"
SOURCE_VARIANT = "normalized-1600"
QUALITY_VARIANT = "normalized-1600-quality"
FOREGROUND_VARIANT = "normalized-1600-foreground"
_EXPECTED_CASES = 36
_MAX_SECONDARY_RATIO = 0.25
_ALLOWED_SECONDARY_REASONS = frozenset({"severe_blur", "too_small"})


@dataclass(frozen=True)
class SecondaryDisposition:
    source_index: int
    area_ratio: float
    quality_decision: str
    reasons: tuple[str, ...]
    disposition: str


@dataclass(frozen=True)
class ForegroundOutcome:
    outcome: str
    selected_source_index: int | None
    raw_detection_count: int
    secondary: tuple[SecondaryDisposition, ...]


def classify_foreground(
    detections: Sequence[Detection], quality: Sequence[Mapping[str, object]]
) -> ForegroundOutcome:
    """Apply the frozen 4:1 foreground rule to normalized YuNet evidence."""
    if len(detections) != len(quality):
        raise ValueError("quality decisions must match detections")
    decisions = tuple(_quality_value(value) for value in quality)
    areas = tuple(_area(detection) for detection in detections)
    if not detections:
        return ForegroundOutcome("no_face", None, 0, ())
    if len(detections) == 1:
        return ForegroundOutcome("single_face", 0, 1, ())

    ordered = tuple(sorted(range(len(detections)), key=lambda index: (-areas[index], index)))
    primary = ordered[0]
    primary_area = areas[primary]
    secondary_indices = ordered[1:]
    can_ignore_secondary = all(
        areas[index] <= primary_area * _MAX_SECONDARY_RATIO
        and decisions[index][0] == "quality_rejected"
        and bool(set(decisions[index][1]) & _ALLOWED_SECONDARY_REASONS)
        for index in secondary_indices
    )
    selected = (
        primary
        if areas[primary] > areas[ordered[1]]
        and decisions[primary][0] == "accepted"
        and can_ignore_secondary
        else None
    )
    outcome = "single_face" if selected is not None else "multiple_faces"
    secondary = tuple(
        SecondaryDisposition(
            source_index=index,
            area_ratio=areas[index] / primary_area,
            quality_decision=decisions[index][0],
            reasons=decisions[index][1],
            disposition="ignored" if selected is not None else "retained",
        )
        for index in secondary_indices
    )
    return ForegroundOutcome(outcome, selected, len(detections), secondary)


def derive_foreground_run(
    source_run: Path, output: Path, *, experiment_revision: str
) -> tuple[ReviewRow, ...]:
    """Derive and atomically publish one immutable foreground variant from a verified source run."""
    if _REVISION.fullmatch(experiment_revision) is None:
        raise ValueError("experiment revision must be a nonempty immutable revision")
    source_identity = verify_run(source_run)
    if source_identity != SOURCE_RUN_IDENTITY:
        raise ValueError("source run identity is not the approved immutable source")
    source_cases = _load_source_cases(source_run)
    derived_cases: list[dict[str, object]] = []
    rows: list[ReviewRow] = []
    for source_case in source_cases:
        normalized = source_case["normalized"]
        outcome = classify_foreground(normalized["detections"], normalized["quality"])
        case_id, cohort = source_case["case_id"], source_case["cohort"]
        derived_cases.append(
            {
                "case_id": case_id,
                "cohort": cohort,
                "source_variant": SOURCE_VARIANT,
                "quality_variant": QUALITY_VARIANT,
                "source_detection_count": len(normalized["detections"]),
                "source_detections": [asdict(detection) for detection in normalized["detections"]],
                "source_quality": normalized["quality"],
                "foreground": _foreground_payload(outcome),
            }
        )
        rows.append(ReviewRow(case_id, FOREGROUND_VARIANT, cohort, outcome.outcome))

    def write(stage: Path) -> None:
        _write_json(stage / "rule.json", _rule_manifest(source_identity))
        _write_json(stage / "evidence.json", {"cases": derived_cases})
        _write_json(stage / "review-rows.json", {"rows": [asdict(row) for row in rows]})
        _write_visuals(stage, source_run, derived_cases)
        (stage / "report.html").write_text(_report_html(derived_cases), encoding="utf-8")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "artifact_type": "foreground-run",
            "source_run_identity": source_identity,
            "experiment_revision": experiment_revision,
            "source_variant": SOURCE_VARIANT,
            "variant": FOREGROUND_VARIANT,
            "case_count": _EXPECTED_CASES,
        }
        manifest["run_identity"] = _run_identity(stage, manifest)
        _write_json(stage / "manifest.json", manifest)

    publish_immutable(output, write)
    return tuple(rows)


def verify_foreground_run(run: Path) -> str:
    """Verify the complete immutable foreground evidence bundle and every displayed visual."""
    try:
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("foreground run manifest is invalid") from error
    required = {
        "schema_version",
        "artifact_type",
        "source_run_identity",
        "experiment_revision",
        "source_variant",
        "variant",
        "case_count",
        "run_identity",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != required
        or manifest["schema_version"] != SCHEMA_VERSION
        or manifest["artifact_type"] != "foreground-run"
        or manifest["source_run_identity"] != SOURCE_RUN_IDENTITY
        or manifest["source_variant"] != SOURCE_VARIANT
        or manifest["variant"] != FOREGROUND_VARIANT
        or manifest["case_count"] != _EXPECTED_CASES
        or not isinstance(manifest["run_identity"], str)
        or _REVISION.fullmatch(manifest["experiment_revision"]) is None
    ):
        raise ValueError("foreground run manifest is invalid")
    identity = manifest.pop("run_identity")
    if identity != _run_identity(run, manifest):
        raise ValueError("foreground run evidence identity is invalid")
    if _read_json(run / "rule.json") != _rule_manifest(str(manifest["source_run_identity"])):
        raise ValueError("foreground rule is invalid")
    cases = _load_derived_cases(run)
    rows = _load_rows(run)
    expected_rows = tuple(
        ReviewRow(
            str(case["case_id"]),
            FOREGROUND_VARIANT,
            str(case["cohort"]),
            str(case["foreground"]["outcome"]),
        )
        for case in cases
    )
    if rows != expected_rows:
        raise ValueError("foreground review rows are invalid")
    names = {path.name for path in (run / "annotated").glob("*.jpg")}
    expected_names = {f"{row.case_id}-{FOREGROUND_VARIANT}.jpg" for row in rows}
    if names != expected_names:
        raise ValueError("foreground review visuals are invalid")
    return identity


def load_foreground_review_rows(run: Path) -> tuple[ReviewRow, ...]:
    """Return foreground rows only after complete run identity verification."""
    verify_foreground_run(run)
    return _load_rows(run)


def build_foreground_review(run: Path, output: Path) -> None:
    """Build labels only after verifying the exact derived foreground run."""
    identity = verify_foreground_run(run)
    build_identity_bound_review(load_foreground_review_rows(run), identity, output)


def finalize_foreground_review(
    run: Path, labels_csv: Path, source_labels_csv: Path, output: Path
) -> dict[str, Any]:
    """Finalize only a complete, certain review of the exact derived foreground run."""
    identity = verify_foreground_run(run)
    rows = load_foreground_review_rows(run)
    require_certain_identity_bound_review(rows, identity, labels_csv)
    source_identity = _source_run_identity(run)
    cases = _load_derived_cases(run)
    source_rows, comparison_rows = _source_rows(cases)
    source_labels = load_identity_bound_labels(source_rows, source_identity, source_labels_csv)
    foreground_labels = load_identity_bound_labels(rows, identity, labels_csv)
    return finalize_identity_bound_review(
        rows,
        identity,
        labels_csv,
        output,
        additional={
            "source_run_identity": source_identity,
            "comparisons": _comparison_totals(
                comparison_rows, rows, foreground_labels, source_labels
            ),
        },
    )


def _source_run_identity(run: Path) -> str:
    try:
        value = _read_json(run / "manifest.json")["source_run_identity"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("foreground run manifest is invalid") from error
    if value != SOURCE_RUN_IDENTITY:
        raise ValueError("foreground run manifest is invalid")
    return value


def _source_rows(
    cases: Sequence[Mapping[str, object]],
) -> tuple[tuple[ReviewRow, ...], dict[str, tuple[ReviewRow, ...]]]:
    all_rows: list[ReviewRow] = []
    comparison_rows: dict[str, list[ReviewRow]] = {
        SOURCE_VARIANT: [],
        QUALITY_VARIANT: [],
    }
    for case in cases:
        case_id, cohort = str(case["case_id"]), str(case["cohort"])
        detections = tuple(_detection_from_payload(item) for item in case["source_detections"])
        quality = tuple(_quality_payload(item) for item in case["source_quality"])
        source_outcomes = {
            SOURCE_VARIANT: classify_detections(
                detections, accepted=None, quality_enabled=False
            ).outcome,
            QUALITY_VARIANT: classify_detections(
                detections,
                accepted=tuple(value["decision"] == "accepted" for value in quality),
                quality_enabled=True,
            ).outcome,
        }
        for variant in VARIANTS:
            row = ReviewRow(case_id, variant, cohort, source_outcomes.get(variant, ""))
            all_rows.append(row)
            if variant in comparison_rows:
                comparison_rows[variant].append(row)
    return (
        tuple(all_rows),
        {variant: tuple(rows) for variant, rows in comparison_rows.items()},
    )


def _comparison_totals(
    source_rows: Mapping[str, Sequence[ReviewRow]],
    foreground_rows: Sequence[ReviewRow],
    foreground_labels: Mapping[tuple[str, str], str],
    source_labels: Mapping[tuple[str, str], str],
) -> dict[str, dict[str, int]]:
    foreground_outcomes = {row.case_id: row.outcome for row in foreground_rows}
    foreground_by_case = {
        case_id: label
        for (case_id, variant), label in foreground_labels.items()
        if variant == FOREGROUND_VARIANT
    }
    totals: dict[str, dict[str, int]] = {}
    for variant in (SOURCE_VARIANT, QUALITY_VARIANT):
        values = {"changed": 0, "helped": 0, "harmed": 0, "neutral": 0}
        for source in source_rows[variant]:
            foreground_label = foreground_by_case[source.case_id]
            source_label = source_labels[(source.case_id, variant)]
            foreground_outcome = foreground_outcomes[source.case_id]
            if source.outcome == foreground_outcome:
                continue
            values["changed"] += 1
            if source_label == "incorrect" and foreground_label == "correct":
                values["helped"] += 1
            elif source_label == "correct" and foreground_label == "incorrect":
                values["harmed"] += 1
            else:
                values["neutral"] += 1
        totals[variant] = values
    return totals


def _load_source_cases(source_run: Path) -> tuple[dict[str, Any], ...]:
    try:
        manifest = _read_json(source_run / "manifest.json")
        evidence = _read_json(source_run / "evidence.json")["cases"]
        source_rows = _read_json(source_run / "review-rows.json")["rows"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("source run evidence is invalid") from error
    if not isinstance(manifest, dict) or manifest.get("case_count") != _EXPECTED_CASES:
        raise ValueError("source run evidence must contain exactly 36 cases")
    if not isinstance(evidence, list) or len(evidence) != _EXPECTED_CASES:
        raise ValueError("source run evidence must contain exactly 36 cases")
    if not isinstance(source_rows, list) or len(source_rows) < _EXPECTED_CASES:
        raise ValueError("source run review rows are invalid")
    values: list[dict[str, Any]] = []
    for case in evidence:
        if not isinstance(case, dict):
            raise ValueError("source run evidence is invalid")
        case_id, cohort, variants = case.get("case_id"), case.get("cohort"), case.get("variants")
        if (
            not isinstance(case_id, str)
            or not isinstance(cohort, str)
            or not isinstance(variants, list)
        ):
            raise ValueError("source run evidence is invalid")
        normalized = _variant(variants, SOURCE_VARIANT)
        quality_variant = _variant(variants, QUALITY_VARIANT)
        if (
            normalized is None
            or quality_variant is None
            or not isinstance(normalized.get("detections"), list)
            or not isinstance(quality_variant.get("detections"), list)
            or not isinstance(quality_variant.get("quality"), list)
        ):
            raise ValueError("source run normalized evidence is invalid")
        detections = tuple(_detection_from_payload(value) for value in normalized["detections"])
        quality_detections = tuple(
            _detection_from_payload(value) for value in quality_variant["detections"]
        )
        quality = tuple(_quality_payload(value) for value in quality_variant["quality"])
        if detections != quality_detections:
            raise ValueError("source run normalized detections do not match quality evidence")
        if len(detections) != len(quality):
            raise ValueError("source run quality must match detections")
        values.append(
            {
                "case_id": case_id,
                "cohort": cohort,
                "normalized": {"detections": detections, "quality": quality},
            }
        )
    if len({value["case_id"] for value in values}) != _EXPECTED_CASES:
        raise ValueError("source run cases are invalid")
    validate_detector_cohort(tuple({"cohort": value["cohort"]} for value in values))
    expected_source_rows = {
        (value["case_id"], variant, value["cohort"])
        for value in values
        for variant in (SOURCE_VARIANT, QUALITY_VARIANT)
    }
    available_source_rows = {
        (row.get("case_id"), row.get("variant"), row.get("cohort"))
        for row in source_rows
        if isinstance(row, dict)
    }
    if not expected_source_rows <= available_source_rows:
        raise ValueError("source run review rows are invalid")
    return tuple(sorted(values, key=lambda value: value["case_id"]))


def _load_derived_cases(run: Path) -> tuple[dict[str, Any], ...]:
    try:
        values = _read_json(run / "evidence.json")["cases"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("foreground evidence is invalid") from error
    if not isinstance(values, list) or len(values) != _EXPECTED_CASES:
        raise ValueError("foreground evidence is invalid")
    expected_fields = {
        "case_id",
        "cohort",
        "source_variant",
        "quality_variant",
        "source_detection_count",
        "source_detections",
        "source_quality",
        "foreground",
    }
    case_ids: set[str] = set()
    for value in values:
        if (
            not isinstance(value, dict)
            or set(value) != expected_fields
            or not isinstance(value["case_id"], str)
            or not isinstance(value["cohort"], str)
            or value["source_variant"] != SOURCE_VARIANT
            or value["quality_variant"] != QUALITY_VARIANT
            or isinstance(value["source_detection_count"], bool)
            or not isinstance(value["source_detection_count"], int)
            or not isinstance(value["source_detections"], list)
            or not isinstance(value["source_quality"], list)
            or not isinstance(value["foreground"], dict)
        ):
            raise ValueError("foreground evidence is invalid")
        detections = tuple(_detection_from_payload(item) for item in value["source_detections"])
        quality = tuple(_quality_payload(item) for item in value["source_quality"])
        if value["source_detection_count"] != len(detections) or len(detections) != len(quality):
            raise ValueError("foreground evidence is invalid")
        if value["foreground"] != _foreground_payload(classify_foreground(detections, quality)):
            raise ValueError("foreground evidence is invalid")
        case_ids.add(value["case_id"])
    if len(case_ids) != _EXPECTED_CASES:
        raise ValueError("foreground evidence is invalid")
    validate_detector_cohort(tuple({"cohort": value["cohort"]} for value in values))
    return tuple(sorted(values, key=lambda value: value["case_id"]))


def _load_rows(run: Path) -> tuple[ReviewRow, ...]:
    try:
        values = _read_json(run / "review-rows.json")["rows"]
        rows = tuple(ReviewRow(**value) for value in values)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("foreground review rows are invalid") from error
    if (
        len(rows) != _EXPECTED_CASES
        or len({(row.case_id, row.variant) for row in rows}) != _EXPECTED_CASES
    ):
        raise ValueError("foreground review rows are invalid")
    return tuple(sorted(rows, key=lambda row: row.case_id))


def _quality_value(value: Mapping[str, object]) -> tuple[str, tuple[str, ...]]:
    decision = value.get("decision")
    reasons = value.get("reasons")
    if (
        decision not in {"accepted", "quality_rejected"}
        or not isinstance(reasons, (list, tuple))
        or not all(isinstance(reason, str) for reason in reasons)
        or (decision == "accepted" and reasons)
        or (decision == "quality_rejected" and not reasons)
    ):
        raise ValueError("quality evidence is invalid")
    return str(decision), tuple(reasons)


def _variant(variants: Sequence[object], name: str) -> Mapping[str, object] | None:
    return next(
        (
            variant
            for variant in variants
            if isinstance(variant, dict) and variant.get("variant") == name
        ),
        None,
    )


def _quality_payload(value: object) -> Mapping[str, object]:
    required = {
        "algorithm_version",
        "crop_size",
        "confidence",
        "minimum_side_px",
        "relative_area",
        "sharpness",
        "decision",
        "reasons",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("source run quality is invalid")
    if (
        not isinstance(value["algorithm_version"], str)
        or isinstance(value["crop_size"], bool)
        or not isinstance(value["crop_size"], int)
        or any(
            isinstance(value[field], bool)
            or not isinstance(value[field], (int, float))
            or not math.isfinite(float(value[field]))
            for field in ("confidence", "minimum_side_px", "relative_area", "sharpness")
        )
    ):
        raise ValueError("source run quality is invalid")
    _quality_value(value)
    return value


def _detection_from_payload(value: object) -> Detection:
    required = {"x", "y", "width", "height", "confidence", "landmarks"}
    try:
        if (
            not isinstance(value, dict)
            or set(value) != required
            or not isinstance(value["landmarks"], list)
            or len(value["landmarks"]) != 10
            or any(
                isinstance(value[field], bool) or not isinstance(value[field], (int, float))
                for field in ("x", "y", "width", "height", "confidence")
            )
            or any(
                isinstance(item, bool) or not isinstance(item, (int, float))
                for item in value["landmarks"]
            )
        ):
            raise ValueError
        detection = Detection(
            float(value["x"]),
            float(value["y"]),
            float(value["width"]),
            float(value["height"]),
            float(value["confidence"]),
            tuple(float(item) for item in value["landmarks"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("source run detections are invalid") from error
    _area(detection)
    if not all(math.isfinite(value) for value in detection.landmarks):
        raise ValueError("source run detections are invalid")
    return detection


def _foreground_payload(outcome: ForegroundOutcome) -> dict[str, object]:
    return {
        "outcome": outcome.outcome,
        "selected_source_index": outcome.selected_source_index,
        "raw_detection_count": outcome.raw_detection_count,
        "secondary": [
            {
                "source_index": secondary.source_index,
                "area_ratio": secondary.area_ratio,
                "quality_decision": secondary.quality_decision,
                "reasons": list(secondary.reasons),
                "disposition": secondary.disposition,
            }
            for secondary in outcome.secondary
        ],
    }


def _area(detection: Detection) -> float:
    area = detection.width * detection.height
    if (
        not all(
            math.isfinite(value)
            for value in (
                detection.x,
                detection.y,
                detection.width,
                detection.height,
                detection.confidence,
            )
        )
        or area <= 0
    ):
        raise ValueError("detection area is invalid")
    return area


def _rule_manifest(source_identity: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_run_identity": source_identity,
        "source_variant": SOURCE_VARIANT,
        "quality_variant": QUALITY_VARIANT,
        "variant": FOREGROUND_VARIANT,
        "largest_area_must_be_strict": True,
        "maximum_secondary_area_ratio": _MAX_SECONDARY_RATIO,
        "secondary_rejection_reasons": sorted(_ALLOWED_SECONDARY_REASONS),
        "quality_configuration": {
            "name": "normalized-laplacian-v1",
            "crop_size": 112,
            "minimum_face_side": 32,
            "severe_blur_threshold": 25,
            "borderline_blur_threshold": 50,
            "minimum_relative_area": 0.0009,
            "minimum_confidence": 0.82,
        },
    }


def _write_visuals(stage: Path, source_run: Path, cases: Sequence[Mapping[str, object]]) -> None:
    annotated = stage / "annotated"
    annotated.mkdir()
    for case in cases:
        case_id = str(case["case_id"])
        source = source_run / "annotated" / f"{case_id}-{SOURCE_VARIANT}.jpg"
        try:
            with Image.open(source) as opened:
                image = opened.convert("RGB")
        except OSError as error:
            raise ValueError("source run review visual is invalid") from error
        draw = ImageDraw.Draw(image)
        for index, detection in enumerate(case["source_detections"]):
            color = (
                "royalblue" if index == case["foreground"]["selected_source_index"] else "orange"
            )
            x, y = float(detection["x"]), float(detection["y"])
            width, height = float(detection["width"]), float(detection["height"])
            draw.rectangle(
                (
                    round(x),
                    round(y),
                    round(x + width),
                    round(y + height),
                ),
                outline=color,
                width=4,
            )
            draw.text((round(x), max(0, round(y) - 14)), str(index), fill=color)
        image.thumbnail((1600, 1600))
        image.save(annotated / f"{case_id}-{FOREGROUND_VARIANT}.jpg", format="JPEG")


def _report_html(cases: Sequence[Mapping[str, object]]) -> str:
    rows = "\n".join(
        "<tr><td>{case}</td><td>{cohort}</td><td>{outcome}</td><td>{selected}</td>"
        "<td><img src='annotated/{case}-{variant}.jpg'></td></tr>".format(
            case=case["case_id"],
            cohort=case["cohort"],
            outcome=case["foreground"]["outcome"],
            selected=case["foreground"]["selected_source_index"],
            variant=FOREGROUND_VARIANT,
        )
        for case in cases
    )
    return (
        "<!doctype html><meta charset=utf-8><title>Private foreground review</title>"
        "<style>img{max-width:320px;max-height:240px}td{vertical-align:top}</style>"
        "<table><thead><tr><th>Case</th><th>Cohort</th><th>Outcome</th>"
        "<th>Selected source index</th>"
        "<th>Normalized evidence</th></tr></thead><tbody>"
        f"{rows}</tbody></table>"
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("artifact is invalid") from error


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )

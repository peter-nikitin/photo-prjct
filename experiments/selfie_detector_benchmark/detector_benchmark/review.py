from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .artifacts import publish_immutable

_LABELS = frozenset({"correct", "incorrect", "uncertain"})
_CSV_HEADERS = ("review_rows_sha256", "case_id", "variant", "label")


@dataclass(frozen=True)
class ReviewRow:
    case_id: str
    variant: str
    cohort: str
    outcome: str = ""


def build_review_bundle(rows: Iterable[ReviewRow], output: Path) -> None:
    """Create a separate editable-label bundle while retaining immutable detector evidence."""
    values = _validate_rows(rows)
    identity = _rows_sha256(values)

    def write(stage: Path) -> None:
        (stage / "review.csv").write_text(
            ",".join(_CSV_HEADERS)
            + "\n"
            + "".join(f"{identity},{row.case_id},{row.variant},\n" for row in values),
            encoding="utf-8",
        )
        (stage / "report.html").write_text(_report_html(values, identity), encoding="utf-8")
        _write_json(
            stage / "manifest.json",
            {
                "schema_version": SCHEMA_VERSION,
                "artifact_type": "detector-review",
                "review_rows_sha256": identity,
                "row_count": len(values),
            },
        )

    publish_immutable(output, write)


def build_review_from_run(run: Path, output: Path) -> None:
    """Create labels only after checking the exact run manifest, evidence, and review images."""
    from .offline import load_review_rows, verify_run

    identity = verify_run(run)
    rows = _validate_rows(load_review_rows(run))

    def write(stage: Path) -> None:
        (stage / "review.csv").write_text(
            ",".join(_CSV_HEADERS)
            + "\n"
            + "".join(f"{identity},{row.case_id},{row.variant},\n" for row in rows),
            encoding="utf-8",
        )
        _write_json(
            stage / "manifest.json",
            {
                "schema_version": SCHEMA_VERSION,
                "artifact_type": "detector-review",
                "run_identity": identity,
                "row_count": len(rows),
            },
        )

    publish_immutable(output, write)


def finalize_review(rows: Iterable[ReviewRow], labels_csv: Path, output: Path) -> dict[str, Any]:
    """Publish analysis only after an exact, one-label-per-case/variant review."""
    values = _validate_rows(rows)
    evidence_hash = _rows_sha256(values)
    labels = _load_labels(labels_csv, evidence_hash)
    expected = {(row.case_id, row.variant) for row in values}
    if set(labels) != expected:
        raise ValueError("review is incomplete or does not match this run")
    result = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "detector-analysis",
        "review_rows_sha256": evidence_hash,
        "reviewed_rows": len(values),
        "metrics": _metrics(values, labels),
        "acceptance": {
            variant: acceptance_metrics(
                tuple(row for row in values if row.variant == variant), labels
            )
            for variant in sorted({row.variant for row in values})
        },
    }

    def write(stage: Path) -> None:
        _write_json(stage / "analysis.json", result)

    publish_immutable(output, write)
    return result


def finalize_run_review(run: Path, labels_csv: Path, output: Path) -> dict[str, Any]:
    """Finalize labels only when the complete exact visual/evidence run still verifies."""
    from .offline import load_review_rows, verify_run

    identity = verify_run(run)
    rows = _validate_rows(load_review_rows(run))
    labels = _load_labels(labels_csv, identity)
    expected = {(row.case_id, row.variant) for row in rows}
    if set(labels) != expected:
        raise ValueError("review is incomplete or does not match this run")
    result = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "detector-analysis",
        "run_identity": identity,
        "reviewed_rows": len(rows),
        "metrics": _metrics(rows, labels),
        "acceptance": {
            variant: acceptance_metrics(
                tuple(row for row in rows if row.variant == variant), labels
            )
            for variant in sorted({row.variant for row in rows})
        },
    }
    publish_immutable(output, lambda stage: _write_json(stage / "analysis.json", result))
    return result


def _validate_rows(rows: Iterable[ReviewRow]) -> tuple[ReviewRow, ...]:
    values = tuple(sorted(rows, key=lambda row: (row.case_id, row.variant)))
    if not values or any(not row.case_id or not row.variant or not row.cohort for row in values):
        raise ValueError("review rows are invalid")
    if len({(row.case_id, row.variant) for row in values}) != len(values):
        raise ValueError("review rows are duplicated")
    return values


def _load_labels(path: Path, expected_identity: str) -> dict[tuple[str, str], str]:
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != _CSV_HEADERS:
                raise ValueError("review labels have an invalid header")
            rows = list(reader)
    except OSError as error:
        raise ValueError("review labels cannot be read") from error
    labels: dict[tuple[str, str], str] = {}
    for row in rows:
        if set(row) != set(_CSV_HEADERS) or None in row:
            raise ValueError("review labels are invalid")
        key = (row["case_id"], row["variant"])
        if row["review_rows_sha256"] != expected_identity:
            raise ValueError("review labels have a different evidence identity")
        if not key[0] or not key[1] or row["label"] not in _LABELS or key in labels:
            raise ValueError("review labels are invalid")
        labels[key] = row["label"]
    return labels


def _metrics(rows: tuple[ReviewRow, ...], labels: dict[tuple[str, str], str]) -> dict[str, object]:
    metrics: dict[str, dict[str, dict[str, int]]] = {}
    for row in rows:
        bucket = metrics.setdefault(row.variant, {}).setdefault(
            row.cohort, {"correct": 0, "incorrect": 0, "uncertain": 0, "total": 0}
        )
        bucket[labels[(row.case_id, row.variant)]] += 1
        bucket["total"] += 1
    return metrics


def acceptance_metrics(
    rows: Iterable[ReviewRow], labels: dict[tuple[str, str], str]
) -> dict[str, object]:
    """Calculate the frozen recovery and guardrail gates from manual and detector evidence."""
    values = tuple(rows)
    recovery = [
        row
        for row in values
        if row.cohort == "no_face"
        and row.outcome == "single_face"
        and labels[(row.case_id, row.variant)] == "correct"
    ]
    controls = [row for row in values if row.cohort == "successful_control"]
    preserved = [
        row
        for row in controls
        if row.outcome == "single_face" and labels[(row.case_id, row.variant)] == "correct"
    ]
    guardrails = [row for row in values if row.cohort == "multiple_faces"]
    uncertain = sum(labels[(row.case_id, row.variant)] == "uncertain" for row in values)
    violation = sum(row.outcome == "single_face" for row in guardrails)
    result = {
        "recovered_no_face": {"correct": len(recovery), "total": 17},
        "successful_controls": {
            "preserved": len(preserved),
            "total": 16,
            "uncertain": sum(labels[(row.case_id, row.variant)] == "uncertain" for row in controls),
        },
        "multiple_face_accepted_single": {"violations": violation, "total": 3},
        "uncertain_rows": uncertain,
    }
    result["promising"] = (
        result["recovered_no_face"]["correct"] >= 5
        and result["successful_controls"]["preserved"] == 16
        and result["multiple_face_accepted_single"]["violations"] == 0
    )
    return result


def _rows_sha256(rows: tuple[ReviewRow, ...]) -> str:
    return hashlib.sha256(
        json.dumps([asdict(row) for row in rows], sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _report_html(rows: tuple[ReviewRow, ...], identity: str) -> str:
    table = "\n".join(
        f"<tr><td>{row.case_id}</td><td>{row.variant}</td><td>{row.cohort}</td>"
        f"<td>{row.outcome}</td></tr>"
        for row in rows
    )
    return (
        "<!doctype html><meta charset=utf-8><title>Private detector review</title>"
        f"<h1>Private detector review</h1><p>Evidence identity: {identity}</p>"
        "<p>Label every CSV row exactly once as correct, incorrect, or uncertain.</p>"
        "<table><thead><tr><th>Case</th><th>Variant</th><th>Cohort</th><th>Outcome</th>"
        f"</tr></thead><tbody>{table}</tbody></table>"
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

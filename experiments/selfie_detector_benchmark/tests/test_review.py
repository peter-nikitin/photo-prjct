from __future__ import annotations

from pathlib import Path

import pytest
from detector_benchmark.review import (
    ReviewRow,
    acceptance_metrics,
    build_review_bundle,
    finalize_review,
)


def _rows() -> tuple[ReviewRow, ...]:
    return (
        ReviewRow("case-001", "baseline-original", "no_face"),
        ReviewRow("case-001", "normalized-1600", "no_face"),
        ReviewRow("case-001", "normalized-1600-quality", "no_face"),
    )


def test_finalization_requires_one_valid_label_for_every_case_variant(tmp_path: Path) -> None:
    """Missing review rows would turn a partial manual review into an authoritative conclusion."""
    labels = tmp_path / "labels.csv"
    bundle = tmp_path / "bundle"
    build_review_bundle(_rows(), bundle)
    rows = (bundle / "review.csv").read_text(encoding="utf-8").splitlines()
    labels.write_text("\n".join((rows[0], f"{rows[1]}correct")) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="incomplete"):
        finalize_review(_rows(), labels, tmp_path / "analysis")
    assert not (tmp_path / "analysis").exists()

    labels.write_text(
        "\n".join(
            f"{line}{label}"
            for line, label in zip(rows[1:], ("correct", "uncertain", "incorrect"), strict=True)
        ).join((rows[0] + "\n", "\n")),
        encoding="utf-8",
    )
    result = finalize_review(_rows(), labels, tmp_path / "analysis")

    assert result["reviewed_rows"] == 3
    assert (tmp_path / "analysis" / "analysis.json").is_file()


def test_finalization_rejects_labels_from_a_different_immutable_review(tmp_path: Path) -> None:
    """A complete CSV from a different detector run must not be accepted by matching case IDs."""
    bundle = tmp_path / "bundle"
    build_review_bundle(_rows(), bundle)
    labels = bundle / "review.csv"
    identity = labels.read_text(encoding="utf-8").splitlines()[1].split(",", 1)[0]
    labels.write_text(
        labels.read_text(encoding="utf-8").replace(identity, "f" * 64),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="identity"):
        finalize_review(_rows(), labels, tmp_path / "analysis")


def test_acceptance_metrics_enforce_recovery_regression_guardrail_and_uncertainty() -> None:
    """Four recoveries, a lost control, or an accepted multiple face must not look promising."""
    rows = (
        tuple(
            ReviewRow(f"case-{number:03d}", "normalized-1600", "no_face", "single_face")
            for number in range(1, 18)
        )
        + tuple(
            ReviewRow(f"case-{number:03d}", "normalized-1600", "successful_control", "single_face")
            for number in range(18, 34)
        )
        + tuple(
            ReviewRow(f"case-{number:03d}", "normalized-1600", "multiple_faces", "multiple_faces")
            for number in range(34, 37)
        )
    )
    labels = {(row.case_id, row.variant): "correct" for row in rows}

    assert acceptance_metrics(rows, labels)["promising"] is True
    for index in range(5, 18):
        labels[(f"case-{index:03d}", "normalized-1600")] = "incorrect"
    metrics = acceptance_metrics(rows, labels)
    assert metrics["recovered_no_face"] == {"correct": 4, "total": 17}
    assert metrics["promising"] is False

    labels[("case-018", "normalized-1600")] = "uncertain"
    guardrail_rows = (
        *rows[:-1],
        ReviewRow("case-036", "normalized-1600", "multiple_faces", "single_face"),
    )
    metrics = acceptance_metrics(guardrail_rows, labels)
    assert metrics["successful_controls"]["uncertain"] == 1
    assert metrics["multiple_face_accepted_single"] == {"violations": 1, "total": 3}
    assert metrics["promising"] is False


def test_acceptance_uncertainty_is_scoped_to_its_single_variant() -> None:
    """One variant's uncertainty must not contaminate another variant's final gates."""
    rows = tuple(
        ReviewRow("case-001", variant, "no_face", "single_face")
        for variant in ("baseline-original", "normalized-1600", "normalized-1600-quality")
    )
    labels = {
        ("case-001", "baseline-original"): "uncertain",
        ("case-001", "normalized-1600"): "correct",
        ("case-001", "normalized-1600-quality"): "incorrect",
    }

    assert acceptance_metrics((rows[0],), labels)["uncertain_rows"] == 1
    assert acceptance_metrics((rows[1],), labels)["uncertain_rows"] == 0
    assert acceptance_metrics((rows[2],), labels)["uncertain_rows"] == 0

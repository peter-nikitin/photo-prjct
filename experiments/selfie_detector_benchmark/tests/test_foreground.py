from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from detector_benchmark.offline import _run_identity
from detector_benchmark.review import ReviewRow
from detector_benchmark.runner import Detection
from PIL import Image


def _detection(width: float, height: float, *, x: float = 0, y: float = 0) -> Detection:
    return Detection(x=x, y=y, width=width, height=height, confidence=0.95, landmarks=())


def _quality(decision: str, *reasons: str) -> dict[str, object]:
    return {"decision": decision, "reasons": list(reasons)}


def _source_quality(decision: str, *reasons: str) -> dict[str, object]:
    return {
        "algorithm_version": "normalized-laplacian-v1",
        "crop_size": 112,
        "confidence": 0.95,
        "minimum_side_px": 40.0,
        "relative_area": 0.2,
        "sharpness": 100.0,
        "decision": decision,
        "reasons": list(reasons),
    }


def _payload(
    *, x: float, y: float, width: float, height: float, confidence: float
) -> dict[str, object]:
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "confidence": confidence,
        "landmarks": [0.0] * 10,
    }


def test_zero_and_one_detection_preserve_unambiguous_cardinality() -> None:
    """Applying quality to an unambiguous face would silently change the frozen cardinality rule."""
    from detector_benchmark.foreground import classify_foreground

    assert classify_foreground((), ()).outcome == "no_face"
    outcome = classify_foreground(
        (_detection(10, 10),), (_quality("quality_rejected", "too_small"),)
    )
    assert outcome.outcome == "single_face"
    assert outcome.selected_source_index == 0
    assert outcome.raw_detection_count == 1


def test_foreground_requires_a_strictly_larger_accepted_primary() -> None:
    """Tied areas or a rejected largest face must retain the genuine multiple-face guardrail."""
    from detector_benchmark.foreground import classify_foreground

    tied = classify_foreground(
        (_detection(40, 25), _detection(20, 50)),
        (_quality("accepted"), _quality("quality_rejected", "too_small")),
    )
    rejected = classify_foreground(
        (_detection(100, 100), _detection(20, 20)),
        (_quality("quality_rejected", "severe_blur"), _quality("quality_rejected", "too_small")),
    )

    assert tied.outcome == rejected.outcome == "multiple_faces"
    assert tied.selected_source_index is None
    assert rejected.selected_source_index is None


def test_four_to_one_boundary_and_allowed_secondary_reasons_are_frozen() -> None:
    """A 25-percent severe-blur secondary is ignorable; a larger or other rejection is not."""
    from detector_benchmark.foreground import classify_foreground

    accepted = classify_foreground(
        (_detection(100, 100), _detection(50, 50)),
        (_quality("accepted"), _quality("quality_rejected", "severe_blur", "too_small")),
    )
    too_large = classify_foreground(
        (_detection(100, 100), _detection(51, 50)),
        (_quality("accepted"), _quality("quality_rejected", "severe_blur")),
    )
    wrong_reason = classify_foreground(
        (_detection(100, 100), _detection(50, 50)),
        (_quality("accepted"), _quality("quality_rejected", "borderline_blur", "low_confidence")),
    )

    assert accepted.outcome == "single_face"
    assert accepted.selected_source_index == 0
    assert accepted.secondary[0].area_ratio == 0.25
    assert accepted.secondary[0].disposition == "ignored"
    assert too_large.outcome == wrong_reason.outcome == "multiple_faces"


def test_usable_secondary_preserves_multiple_faces_and_pairing_must_be_complete() -> None:
    """An accepted smaller person or malformed quality must never become background."""
    from detector_benchmark.foreground import classify_foreground

    outcome = classify_foreground(
        (_detection(100, 100), _detection(40, 40)),
        (_quality("accepted"), _quality("accepted")),
    )

    assert outcome.outcome == "multiple_faces"
    with pytest.raises(ValueError, match="quality"):
        classify_foreground((_detection(10, 10), _detection(5, 5)), (_quality("accepted"),))


def _write_source_run(root: Path, *, case_count: int = 36) -> str:
    (root / "annotated").mkdir(parents=True)
    cases = []
    rows = []
    for number in range(1, case_count + 1):
        case_id = f"case-{number:03d}"
        cohort = (
            "no_face"
            if number <= 17
            else "successful_control"
            if number <= 33
            else "multiple_faces"
        )
        cases.append(
            {
                "case_id": case_id,
                "cohort": cohort,
                "variants": [
                    {
                        "variant": "normalized-1600",
                        "raw_detection_count": 2,
                        "outcome": "multiple_faces",
                        "detections": [
                            _payload(x=5, y=5, width=40, height=40, confidence=0.95),
                            _payload(x=60, y=5, width=20, height=20, confidence=0.8),
                        ],
                        "quality": None,
                    },
                    {
                        "variant": "normalized-1600-quality",
                        "raw_detection_count": 2,
                        "outcome": "single_face",
                        "detections": [
                            _payload(x=5, y=5, width=40, height=40, confidence=0.95),
                            _payload(x=60, y=5, width=20, height=20, confidence=0.8),
                        ],
                        "quality": [
                            _source_quality("accepted"),
                            _source_quality("quality_rejected", "too_small"),
                        ],
                    },
                ],
            }
        )
        rows.append(
            {
                "case_id": case_id,
                "variant": "normalized-1600",
                "cohort": cohort,
                "outcome": "multiple_faces",
            }
        )
        rows.append(
            {
                "case_id": case_id,
                "variant": "normalized-1600-quality",
                "cohort": cohort,
                "outcome": "single_face",
            }
        )
        Image.new("RGB", (100, 80), "white").save(
            root / "annotated" / f"{case_id}-normalized-1600.jpg", format="JPEG"
        )
    (root / "evidence.json").write_text(json.dumps({"cases": cases}), encoding="utf-8")
    (root / "review-rows.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    (root / "report.html").write_text("source report", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "artifact_type": "detector-run",
        "snapshot_manifest_sha256": "a" * 64,
        "model_sha256": "b" * 64,
        "experiment_revision": "c" * 40,
        "variants": ["normalized-1600"],
        "case_count": case_count,
    }
    manifest["run_identity"] = _run_identity(root, manifest)
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return str(manifest["run_identity"])


def test_derivation_binds_exact_source_rows_and_every_published_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed rule, evidence, row, report, or visual invalidates derived review evidence."""
    from detector_benchmark import foreground

    source = tmp_path / "source"
    source_identity = _write_source_run(source)
    monkeypatch.setattr(foreground, "SOURCE_RUN_IDENTITY", source_identity)
    derived = tmp_path / "derived"
    rows = foreground.derive_foreground_run(source, derived, experiment_revision="d" * 40)

    assert len(rows) == 36
    assert all(row.variant == "normalized-1600-foreground" for row in rows)
    assert foreground.verify_foreground_run(derived)
    with pytest.raises(FileExistsError):
        foreground.derive_foreground_run(source, derived, experiment_revision="d" * 40)

    for target in (
        derived / "rule.json",
        derived / "evidence.json",
        derived / "review-rows.json",
        derived / "report.html",
        derived / "annotated" / "case-001-normalized-1600-foreground.jpg",
    ):
        original = target.read_bytes()
        target.write_bytes(original + b"x")
        with pytest.raises(ValueError, match="identity|invalid"):
            foreground.verify_foreground_run(derived)
        target.write_bytes(original)


def test_verifier_rejects_a_rehashed_foreground_outcome_that_breaks_the_frozen_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new aggregate hash cannot make a forged rule outcome authoritative review evidence."""
    from detector_benchmark import foreground

    source = tmp_path / "source"
    source_identity = _write_source_run(source)
    monkeypatch.setattr(foreground, "SOURCE_RUN_IDENTITY", source_identity)
    derived = tmp_path / "derived"
    foreground.derive_foreground_run(source, derived, experiment_revision="d" * 40)

    evidence_path = derived / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["cases"][0]["foreground"] = {
        "outcome": "multiple_faces",
        "selected_source_index": None,
        "raw_detection_count": 2,
        "secondary": [
            {
                "source_index": 1,
                "area_ratio": 0.25,
                "quality_decision": "quality_rejected",
                "reasons": ["too_small"],
                "disposition": "retained",
            }
        ],
    }
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    rows_path = derived / "review-rows.json"
    rows = json.loads(rows_path.read_text(encoding="utf-8"))
    rows["rows"][0]["outcome"] = "multiple_faces"
    rows_path.write_text(json.dumps(rows), encoding="utf-8")
    report_path = derived / "report.html"
    report_path.write_text(
        report_path.read_text(encoding="utf-8").replace("single_face", "multiple_faces", 1),
        encoding="utf-8",
    )
    _rehash_foreground_run(derived)

    with pytest.raises(ValueError, match="foreground evidence|review rows"):
        foreground.verify_foreground_run(derived)


def test_verifier_rejects_rehashed_malformed_derived_evidence_as_a_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed evidence must be rejected through the verifier's documented failure boundary."""
    from detector_benchmark import foreground

    source = tmp_path / "source"
    source_identity = _write_source_run(source)
    monkeypatch.setattr(foreground, "SOURCE_RUN_IDENTITY", source_identity)
    derived = tmp_path / "derived"
    foreground.derive_foreground_run(source, derived, experiment_revision="d" * 40)

    evidence_path = derived / "evidence.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["cases"][0].pop("case_id")
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    _rehash_foreground_run(derived)

    with pytest.raises(ValueError, match="foreground evidence"):
        foreground.verify_foreground_run(derived)


def _rehash_foreground_run(run: Path) -> None:
    manifest_path = run / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("run_identity")
    manifest["run_identity"] = _run_identity(run, manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_derivation_rejects_a_verified_but_incomplete_source_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source with fewer than the frozen 36 rows would weaken every benchmark conclusion."""
    from detector_benchmark import foreground

    source = tmp_path / "source"
    source_identity = _write_source_run(source, case_count=35)
    monkeypatch.setattr(foreground, "SOURCE_RUN_IDENTITY", source_identity)

    with pytest.raises(ValueError, match="36|source"):
        foreground.derive_foreground_run(source, tmp_path / "derived", experiment_revision="d" * 40)


def test_identity_bound_foreground_review_rejects_incomplete_uncertain_duplicate_and_foreign_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only one complete, certain label set bound to this derived identity is authoritative."""
    from detector_benchmark import foreground

    source = tmp_path / "source"
    source_identity = _write_source_run(source)
    monkeypatch.setattr(foreground, "SOURCE_RUN_IDENTITY", source_identity)
    run = tmp_path / "derived"
    foreground.derive_foreground_run(source, run, experiment_revision="d" * 40)
    bundle = tmp_path / "bundle"
    foreground.build_foreground_review(run, bundle)
    identity = foreground.verify_foreground_run(run)
    lines = (bundle / "review.csv").read_text(encoding="utf-8").splitlines()
    header, first = lines[:2]
    labels = tmp_path / "labels.csv"

    for contents, message in (
        ("\n".join((header, f"{first}correct")), "incomplete"),
        (
            "\n".join([header, f"{first}uncertain", *(f"{line}correct" for line in lines[2:])]),
            "uncertain",
        ),
        (
            "\n".join(
                [
                    header,
                    f"{first}correct",
                    f"{first}incorrect",
                    *(f"{line}correct" for line in lines[2:]),
                ]
            ),
            "invalid",
        ),
        (
            "\n".join(
                [
                    header,
                    f"{first.replace(identity, 'a' * 64)}correct",
                    *(f"{line}correct" for line in lines[2:]),
                ]
            ),
            "identity",
        ),
    ):
        labels.write_text(contents + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            foreground.finalize_foreground_review(run, labels, tmp_path / "analysis")
        assert not (tmp_path / "analysis").exists()

    labels.write_text(
        "\n".join([header, *(f"{line}correct" for line in lines[1:])]) + "\n",
        encoding="utf-8",
    )
    result = foreground.finalize_foreground_review(run, labels, tmp_path / "analysis")
    assert result["run_identity"] == identity


def test_identity_bound_helper_keeps_detector_uncertainty_as_review_evidence(
    tmp_path: Path,
) -> None:
    """The existing detector workflow must retain an uncertain manual label as recorded evidence."""
    from detector_benchmark.review import (
        build_identity_bound_review,
        finalize_identity_bound_review,
    )

    rows = (
        ReviewRow("case-001", "baseline-original", "no_face"),
        ReviewRow("case-001", "normalized-1600", "no_face"),
    )
    identity = "a" * 64
    bundle = tmp_path / "bundle"
    build_identity_bound_review(rows, identity, bundle)
    labels = bundle / "review.csv"
    header, *values = labels.read_text(encoding="utf-8").splitlines()
    labels.write_text(
        "\n".join([header, f"{values[0]}uncertain", f"{values[1]}correct"]) + "\n",
        encoding="utf-8",
    )

    result = finalize_identity_bound_review(rows, identity, labels, tmp_path / "analysis")

    assert result["metrics"]["baseline-original"]["no_face"]["uncertain"] == 1


def test_cli_verify_run_outputs_the_verified_source_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The documented source-verification command must return its checked immutable identity."""
    from detector_benchmark.cli import main

    source = tmp_path / "source"
    identity = _write_source_run(source)
    monkeypatch.setattr(sys, "argv", ["detector-benchmark", "verify-run", "--run", str(source)])

    assert main() == 0
    assert json.loads(capsys.readouterr().out) == {"run_identity": identity}

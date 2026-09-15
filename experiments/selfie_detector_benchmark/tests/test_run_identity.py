from __future__ import annotations

import json
from pathlib import Path

import pytest
from detector_benchmark.offline import _run_identity, verify_run


def test_run_identity_rejects_changed_manifest_evidence_and_review_visual(tmp_path: Path) -> None:
    """Manual labels must never survive a change to the run identity or displayed evidence."""
    (tmp_path / "annotated").mkdir()
    (tmp_path / "evidence.json").write_text('{"cases": []}', encoding="utf-8")
    (tmp_path / "review-rows.json").write_text('{"rows": []}', encoding="utf-8")
    (tmp_path / "report.html").write_text("report", encoding="utf-8")
    (tmp_path / "annotated" / "case-001-baseline-original.jpg").write_bytes(b"image")
    manifest = {
        "schema_version": 1,
        "artifact_type": "detector-run",
        "snapshot_manifest_sha256": "a" * 64,
        "model_sha256": "b" * 64,
        "experiment_revision": "c" * 40,
        "variants": ["baseline-original"],
        "case_count": 1,
    }
    manifest["run_identity"] = _run_identity(tmp_path, manifest)
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert verify_run(tmp_path) == manifest["run_identity"]
    for target in (
        tmp_path / "manifest.json",
        tmp_path / "evidence.json",
        tmp_path / "annotated" / "case-001-baseline-original.jpg",
    ):
        original = target.read_bytes()
        target.write_bytes(original + b"x")
        with pytest.raises(ValueError, match="identity|manifest"):
            verify_run(tmp_path)
        target.write_bytes(original)

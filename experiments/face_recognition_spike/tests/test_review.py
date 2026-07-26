from __future__ import annotations

import json
from pathlib import Path

import pytest
from face_spike.comparison import ComparisonConfig, run_comparison
from face_spike.review import ReviewConfig, ReviewError, run_review
from test_comparison import _reference, _run


def _completed_inputs(tmp_path: Path) -> tuple[Path, Path]:
    run = _run(
        tmp_path / "run",
        [
            ("person-0001", ["a.jpg", "b.jpg"]),
            ("person-0002", ["c.jpg"]),
            ("person-0003", ["d.jpg"]),
        ],
    )
    reference = _reference(
        tmp_path / "reference",
        {"10": ["a.jpg", "b.jpg", "c.jpg"], "20": ["d.jpg"]},
    )
    comparison = tmp_path / "comparison"
    run_comparison(ComparisonConfig(run, reference, comparison))
    return run, comparison


def test_review_publishes_bounded_index_detail_pages_and_all_fragmentation_pairs(
    tmp_path: Path,
) -> None:
    run, comparison = _completed_inputs(tmp_path)
    output = tmp_path / "review"

    run_review(ReviewConfig(run, comparison, output))

    index = (output / "report.html").read_text(encoding="utf-8")
    detail = (output / "people" / "person-0001" / "index.html").read_text(encoding="utf-8")
    data = json.loads((output / "fragmentation-data.json").read_text(encoding="utf-8"))
    assert index.count("<img ") == 3
    assert 'src="../run/people/person-0001/photos/a.jpg"' not in index
    assert 'href="people/person-0001/index.html"' in index
    assert 'loading="lazy"' in index
    assert 'src="../../../run/people/person-0001/photos/a.jpg"' in detail
    assert 'loading="lazy"' in detail
    assert data["pairs"] == [
        {
            "cluster_ids": ["person-0001", "person-0002"],
            "key": "10|person-0001|person-0002",
            "peakshot_person_id": "10",
        }
    ]


def test_review_ui_uses_versioned_bundle_scoped_storage_and_strict_atomic_csv_contract(
    tmp_path: Path,
) -> None:
    run, comparison = _completed_inputs(tmp_path)
    output = tmp_path / "review"

    run_review(ReviewConfig(run, comparison, output))

    page = (output / "fragmentation-review.html").read_text(encoding="utf-8")
    assert "face-spike-fragmentation-v1:" in page
    assert '"same", "different", "uncertain"' in page
    assert "duplicate pair key" in page
    assert "bundle identity mismatch" in page
    assert "localStorage.setItem" in page


@pytest.mark.parametrize("existing", ["directory", "symlink"])
def test_review_rejects_existing_output_without_mutating_inputs(
    tmp_path: Path, existing: str
) -> None:
    run, comparison = _completed_inputs(tmp_path)
    output = tmp_path / "review"
    if existing == "directory":
        output.mkdir()
    else:
        output.symlink_to(tmp_path / "missing")
    run_manifest = (run / "manifest.json").read_bytes()
    comparison_manifest = (comparison / "manifest.json").read_bytes()

    with pytest.raises(ReviewError, match="output path already exists"):
        run_review(ReviewConfig(run, comparison, output))

    assert (run / "manifest.json").read_bytes() == run_manifest
    assert (comparison / "manifest.json").read_bytes() == comparison_manifest
    assert not list(tmp_path.glob(".review.*"))


def test_review_rejects_comparison_created_for_a_different_run(tmp_path: Path) -> None:
    run, comparison = _completed_inputs(tmp_path)
    manifest_path = comparison / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["run_basename"] = "other-run"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReviewError, match="different run"):
        run_review(ReviewConfig(run, comparison, tmp_path / "review"))

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from detector_benchmark.snapshot import SnapshotRecord, export_snapshot


def _record(number: int, content: bytes = b"image") -> SnapshotRecord:
    return SnapshotRecord(
        source_id=f"feedback-{number}",
        cohort="no_face",
        source_status="no_face",
        source_counts={"matched_photo_count": 0, "visible_result_count": 0},
        diagnostics={
            "configuration_hash": "a" * 64,
            "eligible_face_count": 0,
            "failure_code": "no_face",
        },
        result_label=None,
        direct_cosine_distance=None,
        media_type="image/jpeg",
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
    )


def test_snapshot_redacts_source_identifiers_and_rejects_incomplete_records(tmp_path: Path) -> None:
    """A leaked contact/token or missing object must prevent publication."""
    records = (_record(1), _record(2))

    with pytest.raises(ValueError, match="expected 3 records"):
        export_snapshot(records, tmp_path / "snapshot", expected_count=3)
    assert not (tmp_path / "snapshot").exists()

    output = tmp_path / "snapshot"
    export_snapshot(records, output, expected_count=2)

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert [record["case_id"] for record in manifest["records"]] == ["case-001", "case-002"]
    assert "feedback-1" not in (output / "manifest.json").read_text(encoding="utf-8")
    assert "contact" not in (output / "manifest.json").read_text(encoding="utf-8").lower()
    assert sorted(path.name for path in (output / "objects").iterdir()) == [
        "case-001.jpg",
        "case-002.jpg",
    ]


def test_snapshot_rejects_forbidden_metadata_and_checksum_mismatch(tmp_path: Path) -> None:
    """A source payload containing private credentials cannot become benchmark evidence."""
    private = _record(1)
    object.__setattr__(private, "diagnostics", {"bearer_token": "secret"})
    with pytest.raises(ValueError, match="invalid"):
        export_snapshot((private,), tmp_path / "snapshot", expected_count=1)

    mismatch = _record(1)
    object.__setattr__(mismatch, "sha256", "0" * 64)
    with pytest.raises(ValueError, match="checksum"):
        export_snapshot((mismatch,), tmp_path / "snapshot", expected_count=1)


def test_snapshot_rejects_secret_like_value_under_an_allowlisted_key(tmp_path: Path) -> None:
    """An arbitrary value cannot be smuggled into a harmless-looking manifest field."""
    private = _record(1)
    object.__setattr__(
        private,
        "diagnostics",
        {
            "configuration_hash": "https://private.example/secret",
            "eligible_face_count": 0,
            "failure_code": "",
        },
    )

    with pytest.raises(ValueError, match="invalid"):
        export_snapshot((private,), tmp_path / "snapshot", expected_count=1)


def test_snapshot_accepts_the_exact_four_frozen_production_cohorts(tmp_path: Path) -> None:
    """The exporter must accept the recorded production status/failure-code pairs, not stand-ins."""
    records = (
        _record_for("no_face", "no_face", "no_face"),
        _record_for("multiple_faces", "multiple_faces", "multiple_faces"),
        _record_for("successful_control", "ready", ""),
        _record_for("ready_zero_visible_results", "ready", ""),
    )

    export_snapshot(records, tmp_path / "snapshot", expected_count=4)

    assert (tmp_path / "snapshot" / "manifest.json").is_file()


@pytest.mark.parametrize(
    ("cohort", "status", "failure_code"),
    (
        ("no_face", "ready", None),
        ("no_face", "no_face", None),
        ("no_face", "no_face", "multiple_faces"),
    ),
)
def test_snapshot_rejects_unmapped_or_non_string_failure_code(
    tmp_path: Path, cohort: str, status: str, failure_code: str | None
) -> None:
    """Only the explicit cohort/status/failure-code triples belong to the frozen schema."""
    record = _record_for(cohort, status, failure_code)

    with pytest.raises(ValueError, match="invalid"):
        export_snapshot((record,), tmp_path / "snapshot", expected_count=1)


def _record_for(cohort: str, status: str, failure_code: str | None) -> SnapshotRecord:
    record = _record(len(cohort), content=cohort.encode())
    object.__setattr__(record, "cohort", cohort)
    object.__setattr__(record, "source_status", status)
    object.__setattr__(
        record,
        "diagnostics",
        {
            "configuration_hash": "a" * 64,
            "eligible_face_count": 0,
            "failure_code": failure_code,
        },
    )
    object.__setattr__(record, "sha256", hashlib.sha256(record.content).hexdigest())
    return record

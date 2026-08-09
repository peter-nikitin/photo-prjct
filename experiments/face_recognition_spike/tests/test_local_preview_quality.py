from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from .fixtures import make_jpeg, write_json

# ruff: noqa: E501


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fixture_manifest(tmp_path: Path) -> tuple[Path, Path, Path]:
    originals = tmp_path / "originals"
    source = make_jpeg(originals / "photo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg")
    files = [
        {
            "photo_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "filename": source.name,
            "key": "originals/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "content_type": "image/jpeg",
            "etag": '"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"',
            "size": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
    ]
    inventory = [
        {key: item[key] for key in ("photo_id", "filename", "key", "size", "content_type", "etag")}
        for item in files
    ]
    payload: dict[str, object] = {
        "version": 1,
        "complete": True,
        "event": {"id": "9", "slug": "test-event"},
        "files": files,
        "inventory_hash": _canonical_sha256(inventory),
        "unresolved_count": 0,
    }
    payload["manifest_hash"] = _canonical_sha256(payload)
    manifest = write_json(tmp_path / "source.json", payload)
    return manifest, originals, tmp_path / "output"


def test_materialize_cli_requires_positive_workers(tmp_path: Path) -> None:
    """Zero workers would promise bounded parallel generation while doing none."""
    manifest, originals, output = _fixture_manifest(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "face_spike.local_preview_quality",
            "materialize",
            "--source-manifest",
            str(manifest),
            "--originals",
            str(originals),
            "--output",
            str(output),
            "--workers",
            "0",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "workers must be a positive integer" in result.stderr


def test_materialize_cli_reports_generated_and_reused_counts(tmp_path: Path) -> None:
    """Operators need an observable distinction between a new run and verified reuse."""
    manifest, originals, output = _fixture_manifest(tmp_path)
    command = [
        sys.executable,
        "-m",
        "face_spike.local_preview_quality",
        "materialize",
        "--source-manifest",
        str(manifest),
        "--originals",
        str(originals),
        "--output",
        str(output),
        "--workers",
        "1",
    ]

    first = subprocess.run(command, text=True, capture_output=True, check=False)
    second = subprocess.run(command, text=True, capture_output=True, check=False)

    assert first.returncode == 0, first.stderr
    assert "generated=1 reused=0" in first.stdout
    assert second.returncode == 0, second.stderr
    assert "generated=0 reused=1" in second.stdout


def test_materialize_cli_returns_nonzero_for_source_failure(tmp_path: Path) -> None:
    """Automation must stop rather than consume a corpus whose source is invalid."""
    manifest, originals, output = _fixture_manifest(tmp_path)
    (originals / "photo-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpg").unlink()

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "face_spike.local_preview_quality",
            "materialize",
            "--source-manifest",
            str(manifest),
            "--originals",
            str(originals),
            "--output",
            str(output),
            "--workers",
            "1",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "source corpus" in result.stderr


def test_compare_cli_requires_exactly_seven_unique_lowerhex_problem_ids(tmp_path: Path) -> None:
    from face_spike.local_preview_quality import main

    assert (
        main(
            [
                "compare",
                "--preview-corpus",
                str(tmp_path),
                "--sample",
                str(tmp_path / "sample.json"),
                "--yunet-model",
                str(tmp_path / "yunet.onnx"),
                "--sface-model",
                str(tmp_path / "sface.onnx"),
                "--output",
                str(tmp_path / "out"),
                "--problem-photo-id",
                "A" * 32,
            ]
        )
        == 1
    )


def test_s_unsorted_valid_seven_id_compare_cli_reaches_publisher(
    tmp_path: Path, monkeypatch: __import__("pytest").MonkeyPatch
) -> None:
    """S: parser accepts arbitrary valid order and dispatches the publisher contract."""
    from face_spike import local_preview_quality as command
    from face_spike.preview_profile_comparison import ProfileComparison

    received: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        command,
        "compare_preview_profiles",
        lambda *_args, problem_photo_ids: (
            received.append(problem_photo_ids) or ProfileComparison(tmp_path / "out", "a" * 64, 7)
        ),
    )
    ids = [f"{value:032x}" for value in reversed(range(1, 8))]
    argv = [
        "compare",
        "--preview-corpus",
        str(tmp_path),
        "--sample",
        str(tmp_path / "sample.json"),
        "--yunet-model",
        str(tmp_path / "yunet.onnx"),
        "--sface-model",
        str(tmp_path / "sface.onnx"),
        "--output",
        str(tmp_path / "out"),
    ]
    for identifier in ids:
        argv.extend(("--problem-photo-id", identifier))
    assert command.main(argv) == 0
    assert received == [tuple(ids)]

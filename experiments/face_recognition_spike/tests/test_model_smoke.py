from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from face_spike import cli


@pytest.mark.face_models
def test_cluster_command_publishes_a_complete_run_with_real_models(tmp_path: Path) -> None:
    """Catch a broken YuNet/SFace integration in the new all-face command."""
    yunet_model = _required_path("FACE_SPIKE_YUNET_MODEL")
    sface_model = _required_path("FACE_SPIKE_SFACE_MODEL")
    source_photos = _required_path("FACE_SPIKE_SMOKE_PHOTOS")
    photos = tmp_path / "photos"
    photos.mkdir()
    _copy_one_jpeg(source_photos, photos)
    output = tmp_path / "model-smoke-run"

    exit_code = cli.main(
        [
            "cluster",
            "--photos",
            str(photos),
            "--yunet-model",
            str(yunet_model),
            "--sface-model",
            str(sface_model),
            "--output",
            str(output),
            "--image-limit",
            "1",
        ]
    )

    assert exit_code == 0
    assert {
        "annotated",
        "clusters.csv",
        "clusters.json",
        "faces",
        "faces.csv",
        "faces.json",
        "manifest.json",
        "metrics.json",
        "people",
        "report.html",
    } <= {path.name for path in output.iterdir()}


def _required_path(variable: str) -> Path:
    value = os.environ.get(variable)
    if not value:
        pytest.skip(f"{variable} is not configured")
    path = Path(value)
    if not path.exists():
        pytest.skip(f"{variable} does not exist: {path}")
    return path


def _copy_one_jpeg(source_photos: Path, destination: Path) -> None:
    source = next(
        (
            path
            for path in sorted(source_photos.iterdir())
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}
        ),
        None,
    )
    if source is None:
        pytest.skip("FACE_SPIKE_SMOKE_PHOTOS must contain a JPEG")
    shutil.copyfile(source, destination / source.name)

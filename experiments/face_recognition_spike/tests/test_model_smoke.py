from __future__ import annotations

import csv
import os
import shutil
from pathlib import Path

import pytest
from face_spike.cli import RunConfig, run_experiment

_ENVIRONMENT_VARIABLES = (
    "FACE_SPIKE_YUNET_MODEL",
    "FACE_SPIKE_SFACE_MODEL",
    "FACE_SPIKE_SMOKE_PHOTOS",
)


@pytest.mark.face_models
@pytest.mark.skipif(
    not all(os.environ.get(name) for name in _ENVIRONMENT_VARIABLES),
    reason="set FACE_SPIKE_YUNET_MODEL, FACE_SPIKE_SFACE_MODEL, and FACE_SPIKE_SMOKE_PHOTOS",
)
def test_real_models_initialize_and_write_complete_run(tmp_path: Path) -> None:
    yunet_model = Path(os.environ["FACE_SPIKE_YUNET_MODEL"])
    sface_model = Path(os.environ["FACE_SPIKE_SFACE_MODEL"])
    source_photos = Path(os.environ["FACE_SPIKE_SMOKE_PHOTOS"])
    inputs = tmp_path / "photos"
    inputs.mkdir()
    selected = sorted(
        path
        for path in source_photos.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg"}
    )[:2]
    if len(selected) != 2:
        pytest.skip("FACE_SPIKE_SMOKE_PHOTOS must contain at least two JPEGs")
    for index, source in enumerate(selected):
        shutil.copyfile(source, inputs / f"smoke-{index}.jpg")

    labels = tmp_path / "labels.csv"
    with labels.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("filename", "participant_group", "face_expected"))
        writer.writerows((("smoke-0.jpg", "smoke", "yes"), ("smoke-1.jpg", "smoke", "yes")))
    output = tmp_path / "run"

    run_experiment(
        RunConfig(
            photos=inputs,
            labels=labels,
            yunet_model=yunet_model,
            sface_model=sface_model,
            output=output,
            detection_threshold=0.75,
            min_face_px=32,
            top_k=10,
            max_image_dimension=12_000,
            max_image_pixels=100_000_000,
        )
    )

    assert (output / "manifest.json").is_file()
    assert (output / "detections.csv").is_file()
    assert (output / "retrieval.csv").is_file()
    assert (output / "metrics.json").is_file()
    assert (output / "report.html").is_file()
    assert (output / "annotated").is_dir()
    assert (output / "crops").is_dir()

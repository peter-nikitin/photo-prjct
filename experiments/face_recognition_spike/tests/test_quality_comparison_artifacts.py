from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest
import test_cluster_artifacts as cluster_artifacts
from PIL import Image
from test_quality_comparison import _face, _photo, _quality, _run, _thresholds


def _comparison():
    return _comparison_evidence()[0]


def _comparison_evidence():
    from face_spike.quality_comparison import compare_quality_runs

    baseline = _run(
        _photo("photo.jpg", _face("baseline", "photo.jpg", (0, 0, 20, 20))),
        _photo("unresolved.jpg"),
    )
    candidate = _run(
        _photo(
            "photo.jpg",
            _face(
                "candidate",
                "photo.jpg",
                (0, 0, 20, 20),
                status="quality_rejected",
                quality=_quality(
                    decision="quality_rejected", reasons=("severe_blur",), sharpness=5
                ),
            ),
        ),
        _photo("unresolved.jpg"),
    )
    return compare_quality_runs(baseline, candidate, thresholds=_thresholds()), baseline, candidate


def test_writer_atomically_publishes_every_new_rejection_and_no_raw_embedding(
    tmp_path: Path,
) -> None:
    from face_spike.quality_comparison_artifacts import (
        load_quality_comparison_bundle,
        write_quality_comparison_bundle,
    )

    comparison = _comparison()
    candidate_run = tmp_path / "candidate"
    crop = candidate_run / "faces" / "candidate.png"
    crop.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "gray").save(crop)
    output = tmp_path / "comparison"

    write_quality_comparison_bundle(output, comparison, candidate_run)

    assert {path.name for path in output.iterdir()} == {
        "comparison.json",
        "labels-template.csv",
        "manifest.json",
        "report.html",
        "review-crops",
        "threshold-crops",
    }
    payload = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert [item["candidate_face_id"] for item in payload["new_rejections"]] == ["candidate"]
    assert len(tuple((output / "review-crops").iterdir())) == 1
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in output.rglob("*")
        if path.is_file()
    ).lower()
    assert '"embedding"' not in text
    assert '"vector"' not in text
    assert "candidate" in (output / "report.html").read_text(encoding="utf-8")
    assert all(
        label in (output / "report.html").read_text()
        for label in ("clear", "blurred", "unusably_small", "uncertain")
    )
    loaded, bundle_sha256 = load_quality_comparison_bundle(output)
    assert loaded == comparison
    assert len(bundle_sha256) == 64
    report = (output / "report.html").read_text(encoding="utf-8")
    assert "fetch(" not in report
    assert f'const source="{bundle_sha256}"' in report
    assert "bundle_sha256,face_id,label" in report

    with pytest.raises(FileExistsError):
        write_quality_comparison_bundle(output, comparison, candidate_run)


def test_label_loader_requires_exact_source_complete_unique_known_labels(tmp_path: Path) -> None:
    from face_spike.quality_comparison_artifacts import (
        LABEL_HEADERS,
        load_quality_review_labels,
    )

    comparison = _comparison()
    digest = "a" * 64
    path = tmp_path / "labels.csv"

    def write(rows: list[tuple[str, str, str]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(LABEL_HEADERS)
            writer.writerows(rows)

    write([(digest, "candidate", "blurred")])
    assert load_quality_review_labels(path, comparison, digest) == {"candidate": "blurred"}

    invalid_rows = (
        [],
        [(digest, "candidate", "blurred"), (digest, "candidate", "blurred")],
        [(digest, "unknown", "blurred")],
        [(digest, "candidate", "usable")],
        [("f" * 64, "candidate", "blurred")],
    )
    for rows in invalid_rows:
        write(rows)
        with pytest.raises(ValueError):
            load_quality_review_labels(path, comparison, digest)


def test_bundle_rejects_rehashed_internal_inconsistency_and_asset_tampering(
    tmp_path: Path,
) -> None:
    from face_spike import quality_comparison_artifacts as artifacts
    from face_spike.quality_comparison_artifacts import (
        load_quality_comparison_bundle,
        write_quality_comparison_bundle,
    )

    comparison = _comparison()
    candidate_run = tmp_path / "candidate"
    crop = candidate_run / "faces" / "candidate.png"
    crop.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "gray").save(crop)
    output = tmp_path / "comparison"
    write_quality_comparison_bundle(output, comparison, candidate_run)

    payload_path = output / "comparison.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["counts"]["newly_rejected"] = 0
    payload_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["comparison_sha256"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    old_digest = manifest["bundle_sha256"]
    identity = {
        key: value for key, value in manifest.items() if key not in {"bundle_sha256", "files"}
    }
    identity["files"] = artifacts._bundle_identity_file_rows(output, old_digest)
    new_digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    report_path = output / "report.html"
    report_path.write_text(
        report_path.read_text(encoding="utf-8").replace(old_digest, new_digest),
        encoding="utf-8",
    )
    manifest["bundle_sha256"] = new_digest
    manifest["files"] = artifacts._bundle_file_rows(output)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="counts do not reconcile"):
        load_quality_comparison_bundle(output)


def test_cluster_run_producer_loads_and_compares_without_an_intermediate_schema(
    tmp_path: Path,
) -> None:
    from face_spike.cluster_artifacts import ClusterArtifactWriter
    from face_spike.clustering import ClusterMember, FaceCluster
    from face_spike.quality import FaceQuality
    from face_spike.quality_comparison import compare_quality_runs
    from face_spike.quality_comparison_artifacts import load_quality_run
    from fixtures import make_jpeg

    runs: list[Path] = []
    for name, rejected in (("baseline", False), ("candidate", True)):
        root = tmp_path / name
        photos = root / "photos"
        make_jpeg(photos / "photo.jpg", size=(40, 24))
        face = cluster_artifacts._face("photo.jpg", 1, 2)
        if rejected:
            face = replace(
                face,
                status="quality_rejected",
                embedding=None,
                quality=FaceQuality(
                    "normalized-laplacian-v1",
                    112,
                    0.875,
                    8.0,
                    1 / 12,
                    5.0,
                    "quality_rejected",
                    ("severe_blur",),
                ),
            )
        analysis = cluster_artifacts._analysis("photo.jpg", face)
        output = root / "run"
        writer = ClusterArtifactWriter(output, photos)
        cluster_artifacts._write_diagnostic(writer, photos, analysis)
        clusters = (
            ()
            if rejected
            else (
                FaceCluster(
                    "person-0001",
                    face.face_id,
                    (ClusterMember(face.face_id, 0.0),),
                ),
            )
        )
        result = cluster_artifacts._run(root, (analysis,), clusters)
        if not rejected:
            result = replace(
                result,
                parameters={
                    **result.parameters,
                    "severe_blur_threshold": 0.0,
                    "borderline_blur_threshold": 1.0,
                    "minimum_confidence": 0.0,
                    "minimum_relative_area": 0.0,
                },
            )
        writer.finish(result)
        runs.append(output)

    baseline, baseline_configuration = load_quality_run(runs[0])
    candidate, candidate_configuration = load_quality_run(runs[1])
    comparison = compare_quality_runs(
        baseline,
        candidate,
        thresholds=replace(_thresholds(), minimum_relative_area=0.0009),
    )

    assert baseline.inventory_sha256 == candidate.inventory_sha256
    assert baseline.media_sha256 == candidate.media_sha256
    assert baseline.generation_sha256 != candidate.generation_sha256
    assert baseline_configuration["severe_blur_threshold"] == 0.0
    assert candidate_configuration == {
        "algorithm_version": "normalized-laplacian-v1",
        "crop_size": 112,
        "minimum_face_px": 32,
        "severe_blur_threshold": 25.0,
        "borderline_blur_threshold": 50.0,
        "minimum_relative_area": 0.0009,
        "minimum_confidence": 0.82,
    }
    assert comparison.counts["newly_rejected"] == 1

    faces_path = runs[1] / "faces.json"
    faces_payload = json.loads(faces_path.read_text(encoding="utf-8"))
    faces_payload["images"][0]["faces"][0]["x"] = 3.0
    faces_path.write_text(json.dumps(faces_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="source identity"):
        load_quality_run(runs[1])

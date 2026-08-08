from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from face_cluster_contract import ClusterFaceValue, build_face_cluster_kernel
from face_spike import cli
from face_spike.analysis import (
    BoundingBox,
    FaceDetection,
    FaceEmbedding,
    FaceLandmarks,
    analyze_decoded_event_photo,
    face_crop_path,
)
from face_spike.image_decoder import ImageLimits, PillowImageDecoder
from face_spike.index_artifacts import load_face_index
from face_spike.inventory import EventPhoto
from face_spike.quality import default_face_quality_thresholds
from face_spike.smoke_search import SearchComparison
from fixtures import make_jpeg
from PIL import Image


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _refresh_run_source(run: Path) -> None:
    manifest_path = run / "manifest.json"
    faces_path = run / "faces.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    faces = json.loads(faces_path.read_text(encoding="utf-8"))
    filenames = [image["filename"] for image in faces["images"]]
    previous_media = {
        item["filename"]: item["sha256"]
        for item in manifest.get("source", {}).get("media_sha256", [])
    }
    generation_parameters = {
        key: value
        for key, value in manifest["parameters"].items()
        if key
        not in {
            "input_photos_basename",
            "sface_model_filename",
            "yunet_model_filename",
        }
    }
    manifest["source"] = {
        "faces_sha256": _canonical_sha256(faces),
        "generation_sha256": _canonical_sha256(
            {
                "model_hashes": manifest["model_hashes"],
                "parameters": generation_parameters,
            }
        ),
        "inventory_sha256": _canonical_sha256(filenames),
        "media_sha256": [
            {
                "filename": filename,
                "sha256": previous_media.get(filename, "f" * 64),
            }
            for filename in filenames
        ],
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")


def _write_run(run: Path, yunet: Path, sface: Path) -> None:
    run.mkdir()
    parameters = {
        "cluster_threshold": 0.363,
        "detection_threshold": 0.0,
        "distance_block_size": 512,
        "max_candidate_edges": 100_000,
        "image_limit": None,
        "input_photos_basename": "photos",
        "max_image_dimension": 12000,
        "max_image_pixels": 100_000_000,
        "min_face_px": 1,
        "quality_algorithm_version": "normalized-laplacian-v1",
        "quality_crop_size": 112,
        "severe_blur_threshold": 0.0,
        "borderline_blur_threshold": 1.0,
        "minimum_confidence": 0.0,
        "minimum_relative_area": 0.0,
        "representative_threshold": 0.363,
        "sface_model_filename": "sface.onnx",
        "yunet_model_filename": "yunet.onnx",
    }
    manifest = {
        "counts": {"images": 1},
        "dependency_versions": {"numpy": "test", "opencv": "test", "pillow": "test"},
        "duration_seconds": 1.0,
        "peak_memory_bytes": 123,
        "durations_seconds": {"clustering": 0.5, "decode_detection_embedding": 0.5},
        "finished_at": "2026-07-28T10:00:00Z",
        "model_hashes": {"sface": _sha256(sface), "yunet": _sha256(yunet)},
        "parameters": parameters,
        "photo_materialization": {"copy": 0, "hard_link": 1},
        "platform": "test",
        "python_version": "test",
        "started_at": "2026-07-28T09:59:59Z",
    }
    faces = {
        "images": [
            {
                "faces": [
                    {
                        "confidence": 0.9,
                        "crop_path": face_crop_path("photo.jpg#face-001"),
                        "error_code": "",
                        "face_id": "photo.jpg#face-001",
                        "face_index": 1,
                        "filename": "photo.jpg",
                        "height": 12.0,
                        "landmarks": {
                            "left_eye": [4.0, 5.0],
                            "left_mouth_corner": [4.0, 12.0],
                            "nose": [8.0, 8.0],
                            "right_eye": [12.0, 5.0],
                            "right_mouth_corner": [12.0, 12.0],
                        },
                        "quality": {
                            "algorithm_version": "normalized-laplacian-v1",
                            "confidence": 0.9,
                            "crop_size": 112,
                            "decision": "accepted",
                            "minimum_side_px": 10.0,
                            "reasons": [],
                            "relative_area": 0.1,
                            "sharpness": 20.0,
                        },
                        "status": "ok",
                        "width": 10.0,
                        "x": 3.0,
                        "y": 2.0,
                    }
                ],
                "filename": "photo.jpg",
                "height": 30,
                "status": "ok",
                "width": 40,
            }
        ]
    }
    (run / "faces.json").write_text(json.dumps(faces, sort_keys=True) + "\n", encoding="utf-8")
    photos = run.parent / "photos"
    manifest["source"] = {
        "faces_sha256": _canonical_sha256(faces),
        "generation_sha256": "0" * 64,
        "inventory_sha256": _canonical_sha256(["photo.jpg"]),
        "media_sha256": [{"filename": "photo.jpg", "sha256": _sha256(photos / "photo.jpg")}],
    }
    (run / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    _refresh_run_source(run)


def _write_singleton_clusters(run: Path) -> None:
    (run / "clusters.json").write_text(
        json.dumps(
            {
                "clusters": [
                    {
                        "cluster_id": "person-0001",
                        "representative_face_id": "photo.jpg#face-001",
                        "members": [
                            {
                                "distance_to_representative": 0.0,
                                "face_id": "photo.jpg#face-001",
                                "face_index": 1,
                                "filename": "photo.jpg",
                            }
                        ],
                    }
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _arguments(run: Path, photos: Path, yunet: Path, sface: Path, output: Path) -> list[str]:
    return [
        "build-index",
        "--run",
        str(run),
        "--photos",
        str(photos),
        "--yunet-model",
        str(yunet),
        "--sface-model",
        str(sface),
        "--output",
        str(output),
    ]


def _ready_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    photos = tmp_path / "photos"
    make_jpeg(photos / "photo.jpg", size=(40, 30))
    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"yunet")
    sface.write_bytes(b"sface")
    run = tmp_path / "run"
    _write_run(run, yunet, sface)
    make_jpeg(run / face_crop_path("photo.jpg#face-001"), size=(20, 20))
    return run, photos, yunet, sface, tmp_path / "index"


def _install_working_models(monkeypatch: pytest.MonkeyPatch, yunet: Path, sface: Path) -> None:
    class Detector:
        def __init__(self, model_path: Path, *, threshold: float) -> None:
            assert model_path == yunet
            assert threshold == 0.0

        def detect(self, bgr: np.ndarray) -> tuple[FaceDetection, ...]:
            return (
                FaceDetection(
                    BoundingBox(3.0, 2.0, 10.0, 12.0),
                    FaceLandmarks((4, 5), (12, 5), (8, 8), (4, 12), (12, 12)),
                    0.9,
                ),
            )

    class Recognizer:
        def __init__(self, model_path: Path) -> None:
            assert model_path == sface

        def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
            return FaceEmbedding(np.asarray([1.0, 0.0], dtype=np.float32))

    monkeypatch.setattr(cli, "YuNetDetector", Detector)
    monkeypatch.setattr(cli, "SFaceRecognizer", Recognizer)


def test_build_index_publishes_reconciled_private_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run, photos, yunet, sface, output = _ready_inputs(tmp_path)
    _install_working_models(monkeypatch, yunet, sface)

    assert cli.main(_arguments(run, photos, yunet, sface, output)) == 0

    index = load_face_index(output)
    assert [entry.face_id for entry in index.entries] == ["photo.jpg#face-001"]
    assert index.manifest.parameters == {
        "detection_threshold": 0.0,
        "image_limit": None,
        "max_image_dimension": 12000,
        "max_image_pixels": 100_000_000,
        "min_face_px": 1,
        "quality_algorithm_version": "normalized-laplacian-v1",
        "quality_crop_size": 112,
        "severe_blur_threshold": 0.0,
        "borderline_blur_threshold": 1.0,
        "minimum_confidence": 0.0,
        "minimum_relative_area": 0.0,
    }
    assert index.manifest.yunet_model == {
        "basename": "yunet.onnx",
        "size": 5,
        "sha256": _sha256(yunet),
    }
    assert index.manifest.source_run_manifest_sha256 == _sha256(run / "manifest.json")
    assert index.manifest.source_faces_sha256 == _sha256(run / "faces.json")


def test_build_index_config_is_immutable_and_limited_to_public_paths() -> None:
    assert [field.name for field in fields(cli.BuildIndexConfig)] == [
        "run",
        "photos",
        "yunet_model",
        "sface_model",
        "output",
    ]
    config = cli.BuildIndexConfig(
        *(Path(name) for name in ("run", "photos", "yunet", "sface", "out"))
    )
    with pytest.raises(FrozenInstanceError):
        config.output = Path("other")


def test_build_benchmark_and_finalize_benchmark_configs_are_frozen_and_expose_public_paths() -> (
    None
):
    assert [field.name for field in fields(cli.BuildBenchmarkConfig)] == [
        "run",
        "index",
        "photos",
        "output",
        "query_count",
    ]
    assert [field.name for field in fields(cli.FinalizeBenchmarkConfig)] == [
        "proposal",
        "annotations_csv",
        "output",
    ]
    build = cli.BuildBenchmarkConfig(
        *(Path(name) for name in ("run", "index", "photos", "out")), 30
    )
    finalize = cli.FinalizeBenchmarkConfig(
        *(Path(name) for name in ("proposal", "annotations", "out"))
    )
    with pytest.raises(FrozenInstanceError):
        build.output = Path("other")
    with pytest.raises(FrozenInstanceError):
        finalize.output = Path("other")

    parser = cli.build_parser()
    build_args = parser.parse_args(
        [
            "build-benchmark",
            "--run",
            "run",
            "--index",
            "index",
            "--photos",
            "photos",
            "--output",
            "out",
            "--query-count",
            "3",
        ]
    )
    finalize_args = parser.parse_args(
        [
            "finalize-benchmark",
            "--proposal",
            "proposal",
            "--annotations-csv",
            "annotations.csv",
            "--output",
            "out",
        ]
    )
    assert (build_args.command, build_args.query_count) == ("build-benchmark", 3)
    assert finalize_args.command == "finalize-benchmark"


def test_evaluate_cluster_expansion_requires_every_threshold_and_dispatches_immutable_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    arguments = [
        "evaluate-cluster-expansion",
        "--benchmark",
        str(tmp_path / "benchmark"),
        "--index",
        str(tmp_path / "index"),
        "--cluster-run",
        str(tmp_path / "cluster-run"),
        "--output",
        str(tmp_path / "report.json"),
        "--direct-threshold",
        "0.1",
        "--anchor-threshold",
        "0.05",
        "--configuration-hash",
        "a" * 64,
        "--generations-json",
        str(tmp_path / "generations.json"),
    ]
    parsed = cli.build_parser().parse_args(arguments)
    assert parsed.command == "evaluate-cluster-expansion"
    assert [field.name for field in fields(cli.EvaluateClusterExpansionConfig)] == [
        "benchmark",
        "index",
        "cluster_run",
        "output",
        "direct_threshold",
        "anchor_threshold",
        "configuration_hash",
        "generations_json",
    ]
    called: list[cli.EvaluateClusterExpansionConfig] = []
    monkeypatch.setattr(cli, "run_evaluate_cluster_expansion", called.append)

    assert cli.main(arguments) == 0
    assert called == [
        cli.EvaluateClusterExpansionConfig(
            tmp_path / "benchmark",
            tmp_path / "index",
            tmp_path / "cluster-run",
            tmp_path / "report.json",
            0.1,
            0.05,
            "a" * 64,
            tmp_path / "generations.json",
        )
    ]


def _smoke_search_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path, Path, Path]:
    from face_spike.benchmark import BenchmarkRun, build_benchmark_proposal
    from face_spike.index import FaceIndex
    from face_spike.index_artifacts import FaceIndexArtifactWriter, FaceIndexManifest
    from test_smoke_search import _proposal_and_index

    original_proposal, original_index = _proposal_and_index()
    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"yunet")
    sface.write_bytes(b"sface")
    manifest = FaceIndexManifest(
        original_index.manifest.source_run_manifest_sha256,
        original_index.manifest.source_faces_sha256,
        {"basename": yunet.name, "size": yunet.stat().st_size, "sha256": _sha256(yunet)},
        {"basename": sface.name, "size": sface.stat().st_size, "sha256": _sha256(sface)},
        original_index.manifest.parameters,
        original_index.manifest.dependency_versions,
        len(original_index.entries),
        original_index.embeddings.shape[1],
        original_index.manifest.created_at,
    )
    index = FaceIndex(original_index.entries, original_index.embeddings, manifest)
    benchmark_run = BenchmarkRun(
        manifest.source_run_manifest_sha256,
        manifest.source_faces_sha256,
        original_proposal.faces,
    )
    proposal = build_benchmark_proposal(benchmark_run, index, query_count=5)
    proposal_path = tmp_path / "proposal"
    index_path = tmp_path / "index"
    cli._publish_benchmark_proposal(
        cli.BuildBenchmarkConfig(
            tmp_path / "source-run", index_path, tmp_path / "photos", proposal_path
        ),
        proposal,
        benchmark_run,
    )
    FaceIndexArtifactWriter(index_path).finish(index)
    run = tmp_path / "run"
    photos = tmp_path / "photos"
    for query in proposal.queries:
        make_jpeg(run / query.query_crop_path, size=(20, 20))
    for entry in index.entries:
        make_jpeg(photos / entry.filename, size=(20, 20))
    return proposal_path, index_path, run, photos, yunet, sface, tmp_path / "output"


def _smoke_search_arguments(
    proposal: Path,
    index: Path,
    run: Path,
    photos: Path,
    yunet: Path,
    sface: Path,
    output: Path,
) -> list[str]:
    return [
        "smoke-search",
        "--proposal",
        str(proposal),
        "--index",
        str(index),
        "--run",
        str(run),
        "--photos",
        str(photos),
        "--yunet-model",
        str(yunet),
        "--sface-model",
        str(sface),
        "--output",
        str(output),
    ]


def test_smoke_search_config_parser_defaults_and_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parsed = cli.build_parser().parse_args(
        _smoke_search_arguments(
            tmp_path / "proposal",
            tmp_path / "index",
            tmp_path / "run",
            tmp_path / "photos",
            tmp_path / "yunet",
            tmp_path / "sface",
            tmp_path / "output",
        )
    )
    assert (parsed.command, parsed.query_count, parsed.limit) == ("smoke-search", 5, 10)
    assert [field.name for field in fields(cli.SmokeSearchConfig)] == [
        "proposal",
        "index",
        "run",
        "photos",
        "yunet_model",
        "sface_model",
        "output",
        "query_count",
        "limit",
    ]
    called: list[cli.SmokeSearchConfig] = []
    monkeypatch.setattr(cli, "run_smoke_search_command", called.append)

    assert (
        cli.main(
            _smoke_search_arguments(
                tmp_path / "proposal",
                tmp_path / "index",
                tmp_path / "run",
                tmp_path / "photos",
                tmp_path / "yunet",
                tmp_path / "sface",
                tmp_path / "output",
            )
        )
        == 0
    )
    assert called == [
        cli.SmokeSearchConfig(
            tmp_path / "proposal",
            tmp_path / "index",
            tmp_path / "run",
            tmp_path / "photos",
            tmp_path / "yunet",
            tmp_path / "sface",
            tmp_path / "output",
        )
    ]


def test_smoke_search_processes_compatible_inputs_and_prevents_partial_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proposal, index, run, photos, yunet, sface, output = _smoke_search_inputs(tmp_path)
    arguments = _smoke_search_arguments(proposal, index, run, photos, yunet, sface, output)

    class Detector:
        def __init__(self, path: Path, *, threshold: float) -> None:
            assert path == yunet
            assert threshold == 0.0

    class Recognizer:
        def __init__(self, path: Path) -> None:
            assert path == sface

    monkeypatch.setattr(cli, "YuNetDetector", Detector)
    monkeypatch.setattr(cli, "SFaceRecognizer", Recognizer)
    monkeypatch.setattr(
        cli,
        "_process_smoke_query",
        lambda *args: np.asarray([1.0, 0.0], dtype=np.float32),
    )

    assert cli.main(arguments) == 0
    assert {path.name for path in output.iterdir()} == {"results.json", "report.html"}

    existing_output = tmp_path / "existing-output"
    existing_output.mkdir()
    sentinel = existing_output / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")
    assert cli.main(arguments[:-1] + [str(existing_output)]) == 2
    assert sentinel.read_text(encoding="utf-8") == "preserve"

    failed_output = tmp_path / "failed-output"

    def fail_query(*args: object) -> np.ndarray:
        raise ValueError("query cannot be processed")

    monkeypatch.setattr(cli, "_process_smoke_query", fail_query)
    assert cli.main(arguments[:-1] + [str(failed_output)]) == 2
    assert not failed_output.exists()


def test_process_smoke_query_decodes_png_crop_through_real_face_analysis(tmp_path: Path) -> None:
    crop = tmp_path / "run" / "faces" / "query.png"
    crop.parent.mkdir(parents=True)
    Image.new("RGB", (20, 20), (20, 30, 40)).save(crop, "PNG")

    class Detector:
        def detect(self, bgr: np.ndarray) -> tuple[FaceDetection, ...]:
            assert bgr.shape == (20, 20, 3)
            return (
                FaceDetection(
                    BoundingBox(2.0, 2.0, 10.0, 10.0),
                    FaceLandmarks((4, 4), (10, 4), (7, 7), (4, 10), (10, 10)),
                    0.9,
                ),
            )

    class Recognizer:
        def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
            assert detection.bounding_box == BoundingBox(2.0, 2.0, 10.0, 10.0)
            return FaceEmbedding(np.asarray([1.0, 0.0], dtype=np.float32))

    vector = cli._process_smoke_query(
        SimpleNamespace(query_crop_path="faces/query.png"),
        tmp_path / "run",
        PillowImageDecoder(ImageLimits(100, 10_000)),
        Detector(),
        Recognizer(),
        default_face_quality_thresholds(),
        EventPhoto,
        analyze_decoded_event_photo,
    )

    np.testing.assert_array_equal(vector, np.asarray([1.0, 0.0], dtype=np.float32))


def test_build_benchmark_accepts_recoverable_zero_dimension_image_evidence(tmp_path: Path) -> None:
    run, _, _, _, _ = _ready_inputs(tmp_path)
    _write_singleton_clusters(run)
    payload = json.loads((run / "faces.json").read_text(encoding="utf-8"))
    payload["images"].append(
        {
            "faces": [],
            "filename": "unreadable.jpg",
            "height": 0,
            "status": "image_decode_failed",
            "width": 0,
        }
    )
    (run / "faces.json").write_text(json.dumps(payload), encoding="utf-8")
    _refresh_run_source(run)

    benchmark_run = cli._load_benchmark_run(run).benchmark_run

    assert [face.face_id for face in benchmark_run.faces] == ["photo.jpg#face-001"]


def test_strict_cluster_run_loader_returns_validated_membership_and_resource_evidence(
    tmp_path: Path,
) -> None:
    run, _, _, _, _ = _ready_inputs(tmp_path)
    _write_singleton_clusters(run)

    artifact = cli._load_benchmark_run(run)

    assert [cluster.cluster_id for cluster in artifact.clusters] == ["person-0001"]
    assert artifact.clusters[0].members[0].face_id == "photo.jpg#face-001"
    assert artifact.corpus_build_duration_ms == 1000
    assert artifact.corpus_build_peak_memory_bytes == 123
    assert len(artifact.corpus_evidence_sha256) == 64
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(
        {
            "duration_seconds": 9.0,
            "peak_memory_bytes": 999,
            "started_at": "2026-08-06T00:00:00Z",
            "finished_at": "2026-08-06T00:00:09Z",
        }
    )
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    independently_measured = cli._load_benchmark_run(run)

    assert independently_measured.corpus_evidence_sha256 == artifact.corpus_evidence_sha256


def test_build_benchmark_rejects_index_with_incompatible_models_or_processing_metadata(
    tmp_path: Path,
) -> None:
    from face_spike.index import FaceIndex, FaceIndexEntry
    from face_spike.index_artifacts import FaceIndexManifest
    from face_spike.quality import FaceQuality

    run_root, _, yunet, sface, _ = _ready_inputs(tmp_path)
    _write_singleton_clusters(run_root)
    artifact = cli._load_benchmark_run(run_root)
    benchmark_run = artifact.benchmark_run
    source_faces = artifact.source_faces
    source_manifest = artifact.source_manifest
    source = source_faces["photo.jpg#face-001"]
    index = FaceIndex(
        (
            FaceIndexEntry(
                source.face_id,
                source.filename,
                source.face_index,
                BoundingBox(source.x, source.y, source.width, source.height),
                source.crop_path,
                FaceQuality(
                    "normalized-laplacian-v1",
                    112,
                    source.confidence,
                    source.minimum_side_px,
                    source.relative_area,
                    source.sharpness,
                    "accepted",
                    (),
                ),
            ),
        ),
        np.asarray([[1.0, 0.0]], dtype=np.float32),
        FaceIndexManifest(
            benchmark_run.manifest_sha256,
            benchmark_run.faces_sha256,
            {"basename": yunet.name, "size": yunet.stat().st_size, "sha256": "f" * 64},
            {"basename": sface.name, "size": sface.stat().st_size, "sha256": _sha256(sface)},
            {"minimum_face_px": 999},
            {"numpy": "test"},
            1,
            2,
            "2026-07-28T10:00:00Z",
        ),
    )

    with pytest.raises(cli.BenchmarkConfigurationError):
        cli._validate_benchmark_index(benchmark_run, source_faces, source_manifest, index)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda cluster: cluster.__setitem__("cluster_id", "invalid"),
        lambda cluster: cluster.__setitem__("representative_face_id", "missing#face-001"),
        lambda cluster: cluster["members"][0].__setitem__("distance_to_representative", 1.0),
    ],
)
def test_build_benchmark_rejects_noncanonical_cluster_representative_contract(
    tmp_path: Path, mutation: object
) -> None:
    run, _, _, _, _ = _ready_inputs(tmp_path)
    _write_singleton_clusters(run)
    clusters_path = run / "clusters.json"
    payload = json.loads(clusters_path.read_text(encoding="utf-8"))
    mutation(payload["clusters"][0])
    clusters_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(cli.BenchmarkConfigurationError):
        cli._load_benchmark_run(run)


def test_build_benchmark_accepts_representative_distance_roundoff(tmp_path: Path) -> None:
    run, _, _, _, _ = _ready_inputs(tmp_path)
    _write_singleton_clusters(run)
    clusters_path = run / "clusters.json"
    payload = json.loads(clusters_path.read_text(encoding="utf-8"))
    payload["clusters"][0]["members"][0]["distance_to_representative"] = 1.1102230246251565e-16
    clusters_path.write_text(json.dumps(payload), encoding="utf-8")

    benchmark_run = cli._load_benchmark_run(run).benchmark_run

    assert benchmark_run.faces[0].cluster_id == "person-0001"


def _write_duplicate_embedding_cluster(run: Path) -> Path:
    faces_path = run / "faces.json"
    faces = json.loads(faces_path.read_text(encoding="utf-8"))
    duplicate = dict(faces["images"][0]["faces"][0])
    duplicate.update(
        {
            "crop_path": face_crop_path("photo.jpg#face-002"),
            "face_id": "photo.jpg#face-002",
            "face_index": 2,
        }
    )
    faces["images"][0]["faces"].append(duplicate)
    faces_path.write_text(json.dumps(faces, sort_keys=True) + "\n", encoding="utf-8")
    make_jpeg(run / duplicate["crop_path"], size=(20, 20))
    _refresh_run_source(run)

    built = build_face_cluster_kernel(
        (
            ClusterFaceValue("photo.jpg#face-001", (1.0, 0.0)),
            ClusterFaceValue("photo.jpg#face-002", (1.0, 0.0)),
        ),
        edge_threshold=0.363,
        representative_threshold=0.363,
        distance_block_size=1,
        max_candidate_edges=10,
    )
    cluster = built[0]
    assert cluster.members[1].distance_to_representative == 0.0
    clusters_path = run / "clusters.json"
    clusters_path.write_text(
        json.dumps(
            {
                "clusters": [
                    {
                        "cluster_id": "person-0001",
                        "representative_face_id": cluster.representative_face_id,
                        "members": [
                            {
                                "distance_to_representative": member.distance_to_representative,
                                "face_id": member.face_id,
                                "face_index": index,
                                "filename": "photo.jpg",
                            }
                            for index, member in enumerate(cluster.members, start=1)
                        ],
                    }
                ]
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return clusters_path


def test_build_benchmark_accepts_zero_distance_for_duplicate_embedding_member(
    tmp_path: Path,
) -> None:
    run, _, _, _, _ = _ready_inputs(tmp_path)
    _write_duplicate_embedding_cluster(run)

    benchmark_run = cli._load_benchmark_run(run).benchmark_run

    assert [face.cluster_id for face in benchmark_run.faces] == ["person-0001", "person-0001"]


def test_build_benchmark_rejects_distance_above_cosine_bound(tmp_path: Path) -> None:
    run, _, _, _, _ = _ready_inputs(tmp_path)
    clusters_path = _write_duplicate_embedding_cluster(run)
    payload = json.loads(clusters_path.read_text(encoding="utf-8"))
    payload["clusters"][0]["members"][1]["distance_to_representative"] = 2.1
    clusters_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(cli.BenchmarkConfigurationError):
        cli._load_benchmark_run(run)


@pytest.mark.parametrize("query_count", [0, -1, True])
def test_build_benchmark_rejects_nonpositive_or_boolean_query_count_before_inputs(
    tmp_path: Path, query_count: object
) -> None:
    config = cli.BuildBenchmarkConfig(
        tmp_path / "missing-run",
        tmp_path / "missing-index",
        tmp_path / "missing-photos",
        tmp_path / "proposal",
        query_count,  # type: ignore[arg-type]
    )

    with pytest.raises(cli.BenchmarkConfigurationError):
        cli.run_build_benchmark(config)

    assert not config.output.exists()


def test_build_benchmark_and_finalize_benchmark_sanitize_runtime_errors_in_main(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_build(config: cli.BuildBenchmarkConfig) -> None:
        raise RuntimeError("private build failure")

    def fail_finalize(config: cli.FinalizeBenchmarkConfig) -> None:
        raise RuntimeError("private finalization failure")

    monkeypatch.setattr(cli, "run_build_benchmark", fail_build)
    monkeypatch.setattr(cli, "run_finalize_benchmark", fail_finalize)

    assert (
        cli.main(
            [
                "build-benchmark",
                "--run",
                str(tmp_path / "run"),
                "--index",
                str(tmp_path / "index"),
                "--photos",
                str(tmp_path / "photos"),
                "--output",
                str(tmp_path / "proposal"),
            ]
        )
        == 2
    )
    assert (
        cli.main(
            [
                "finalize-benchmark",
                "--proposal",
                str(tmp_path / "proposal"),
                "--annotations-csv",
                str(tmp_path / "annotations.csv"),
                "--output",
                str(tmp_path / "final"),
            ]
        )
        == 2
    )


def test_cluster_runtime_errors_are_not_swallowed_by_benchmark_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_cluster(config: cli.ClusterConfig) -> None:
        raise RuntimeError("must remain visible")

    monkeypatch.setattr(cli, "run_cluster", fail_cluster)
    with pytest.raises(RuntimeError, match="must remain visible"):
        cli.main(
            [
                "cluster",
                "--photos",
                str(tmp_path / "photos"),
                "--yunet-model",
                str(tmp_path / "yunet"),
                "--sface-model",
                str(tmp_path / "sface"),
                "--output",
                str(tmp_path / "output"),
            ]
        )


def test_build_benchmark_publishes_report_bundle_that_finalize_loader_reconciles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_benchmark
    from face_spike.benchmark import build_benchmark_proposal
    from face_spike.benchmark_artifacts import load_benchmark_proposal
    from test_benchmark import _cluster, _index, _run

    monkeypatch.setattr(
        test_benchmark,
        "_quality",
        lambda *, confidence=0.95, sharpness=120.0: __import__(
            "photo_worker.face_quality", fromlist=["FaceQualityEvidence"]
        ).FaceQualityEvidence(
            "normalized-laplacian-v1",
            112,
            confidence,
            24.0,
            0.1,
            sharpness,
            "accepted",
            (),
        ),
    )

    run = _run(_cluster("person-0001"))
    proposal = build_benchmark_proposal(run, _index(run), query_count=1)
    output = tmp_path / "proposal"
    config = cli.BuildBenchmarkConfig(
        tmp_path / "run", tmp_path / "index", tmp_path / "photos", output, 1
    )

    cli._publish_benchmark_proposal(config, proposal, run)

    assert {path.name for path in output.iterdir()} == {
        "manifest.json",
        "proposal.json",
        "report.html",
        "queries",
    }
    assert cli._load_finalizable_proposal(output, load_benchmark_proposal) == proposal


def test_finalize_benchmark_publishes_final_artifact_from_exact_reviewed_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_benchmark
    from face_spike.benchmark import build_benchmark_proposal
    from test_benchmark import _cluster, _index, _run, _valid_annotations
    from test_benchmark_artifacts import _row, _write_csv

    monkeypatch.setattr(
        test_benchmark,
        "_quality",
        lambda *, confidence=0.95, sharpness=120.0: __import__(
            "photo_worker.face_quality", fromlist=["FaceQualityEvidence"]
        ).FaceQualityEvidence(
            "normalized-laplacian-v1",
            112,
            confidence,
            24.0,
            0.1,
            sharpness,
            "accepted",
            (),
        ),
    )

    run = _run(*(_cluster(f"person-{number:04d}") for number in range(30)))
    proposal = build_benchmark_proposal(run, _index(run))
    bundle = tmp_path / "proposal"
    cli._publish_benchmark_proposal(
        cli.BuildBenchmarkConfig(tmp_path / "run", tmp_path / "index", tmp_path / "photos", bundle),
        proposal,
        run,
    )
    annotations_csv = tmp_path / "annotations.csv"
    _write_csv(annotations_csv, [_row(proposal, item) for item in _valid_annotations(proposal)])
    output = tmp_path / "final"

    cli.run_finalize_benchmark(cli.FinalizeBenchmarkConfig(bundle, annotations_csv, output))

    assert {path.name for path in output.iterdir()} == {"manifest.json", "benchmark.json"}


@pytest.mark.parametrize(
    "missing_argument", ["--run", "--photos", "--yunet-model", "--sface-model", "--output"]
)
def test_build_index_requires_each_public_path_argument(
    tmp_path: Path, missing_argument: str
) -> None:
    run, photos, yunet, sface, output = _ready_inputs(tmp_path)
    arguments = _arguments(run, photos, yunet, sface, output)
    index = arguments.index(missing_argument)
    del arguments[index : index + 2]

    assert cli.main(arguments) == 2


@pytest.mark.parametrize(
    "broken", ["missing_manifest", "malformed_faces", "incompatible_parameters", "missing_peak"]
)
def test_build_index_rejects_missing_or_incompatible_source_run(
    tmp_path: Path, broken: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run, photos, yunet, sface, output = _ready_inputs(tmp_path)
    if broken == "missing_manifest":
        (run / "manifest.json").unlink()
    elif broken == "malformed_faces":
        (run / "faces.json").write_text("{", encoding="utf-8")
    elif broken == "incompatible_parameters":
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        del manifest["parameters"]["min_face_px"]
        (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    else:
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        del manifest["peak_memory_bytes"]
        (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert cli.main(_arguments(run, photos, yunet, sface, output)) == 2
    assert not output.exists()
    assert capsys.readouterr().err == ""


def test_build_index_rejects_model_hash_mismatch_without_output(tmp_path: Path) -> None:
    run, photos, yunet, sface, output = _ready_inputs(tmp_path)
    yunet.write_bytes(b"different-model")

    assert cli.main(_arguments(run, photos, yunet, sface, output)) == 2
    assert not output.exists()


@pytest.mark.parametrize("broken", ["missing_models", "missing_photos", "invalid_inventory"])
def test_build_index_rejects_model_and_inventory_inputs(tmp_path: Path, broken: str) -> None:
    run, photos, yunet, sface, output = _ready_inputs(tmp_path)
    if broken == "missing_models":
        yunet.unlink()
    elif broken == "missing_photos":
        photos = tmp_path / "missing-photos"
    else:
        (photos / "nested").mkdir()

    assert cli.main(_arguments(run, photos, yunet, sface, output)) == 2
    assert not output.exists()


def test_build_index_preserves_existing_output(tmp_path: Path) -> None:
    run, photos, yunet, sface, output = _ready_inputs(tmp_path)
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")

    assert cli.main(_arguments(run, photos, yunet, sface, output)) == 2
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_build_index_sanitizes_model_initialization_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run, photos, yunet, sface, output = _ready_inputs(tmp_path)

    def fail_model(*args: object, **kwargs: object) -> object:
        raise RuntimeError("private model failure")

    monkeypatch.setattr(cli, "YuNetDetector", fail_model)

    assert cli.main(_arguments(run, photos, yunet, sface, output)) == 2
    assert not output.exists()
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("failure", ["builder", "publication", "builder_runtime", "writer_runtime"])
def test_build_index_sanitizes_build_and_publication_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run, photos, yunet, sface, output = _ready_inputs(tmp_path)
    _install_working_models(monkeypatch, yunet, sface)
    if failure in {"builder", "builder_runtime"}:
        import face_spike.index as index

        def fail_builder(*args: object, **kwargs: object) -> object:
            if failure == "builder_runtime":
                raise RuntimeError("private builder failure")
            raise ValueError("private reconciliation failure")

        monkeypatch.setattr(index, "build_face_index", fail_builder)
    elif failure == "publication":
        import face_spike.index_artifacts as index_artifacts

        real_replace = index_artifacts.os.replace

        def fail_publication(source: Path, destination: Path) -> None:
            if destination == output:
                raise OSError("private publication failure")
            real_replace(source, destination)

        monkeypatch.setattr(index_artifacts.os, "replace", fail_publication)
    else:
        import face_spike.index_artifacts as index_artifacts

        def fail_writer(*args: object, **kwargs: object) -> None:
            raise RuntimeError("private writer failure")

        monkeypatch.setattr(index_artifacts.FaceIndexArtifactWriter, "finish", fail_writer)

    assert cli.main(_arguments(run, photos, yunet, sface, output)) == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".index.*"))
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image_status", "unknown_image_status"),
        ("empty_image_status", "unknown_image_status"),
        ("face_status", "unknown_face_status"),
        ("face_index", 0),
        ("face_index", True),
        ("face_index", 2),
        ("face_id", "other.jpg#face-001"),
        ("face_id", "photo.jpg#face-002"),
        ("crop_path", "faces/wrong.png"),
        ("crop_path", "/absolute/face.png"),
        ("crop_path", "../traversing.png"),
    ],
)
def test_build_index_rejects_malformed_source_face_contract_before_building(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    run, photos, yunet, sface, output = _ready_inputs(tmp_path)
    payload = json.loads((run / "faces.json").read_text(encoding="utf-8"))
    image = payload["images"][0]
    face = image["faces"][0]
    if field == "image_status":
        image["status"] = value
    elif field == "empty_image_status":
        image["faces"] = []
        image["status"] = value
    else:
        face[field] = value
    (run / "faces.json").write_text(json.dumps(payload), encoding="utf-8")
    _install_working_models(monkeypatch, yunet, sface)

    import face_spike.index as index

    def builder_must_not_run(*args: object, **kwargs: object) -> object:
        pytest.fail("malformed source run reached index construction")

    monkeypatch.setattr(index, "build_face_index", builder_must_not_run)

    assert cli.main(_arguments(run, photos, yunet, sface, output)) == 2
    assert not output.exists()
    assert not list(tmp_path.glob(".index.*"))


def test_quality_comparison_command_parsers_and_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    compare_arguments = [
        "compare-quality",
        "--baseline-run",
        str(tmp_path / "baseline"),
        "--candidate-run",
        str(tmp_path / "candidate"),
        "--output",
        str(tmp_path / "comparison"),
    ]
    finalize_arguments = [
        "finalize-quality-review",
        "--comparison",
        str(tmp_path / "comparison"),
        "--labels-csv",
        str(tmp_path / "labels.csv"),
        "--search-comparison",
        str(tmp_path / "search-comparison"),
        "--baseline-run",
        str(tmp_path / "baseline"),
        "--candidate-run",
        str(tmp_path / "candidate"),
        "--benchmark",
        str(tmp_path / "benchmark"),
        "--baseline-index",
        str(tmp_path / "baseline-index"),
        "--candidate-index",
        str(tmp_path / "candidate-index"),
        "--run",
        str(tmp_path / "run"),
        "--yunet-model",
        str(tmp_path / "yunet.onnx"),
        "--sface-model",
        str(tmp_path / "sface.onnx"),
        "--reviewer",
        "reviewer",
        "--reviewed-at",
        "2026-08-08T00:00:00Z",
        "--output",
        str(tmp_path / "approval"),
    ]
    search_arguments = [
        "compare-search",
        "--benchmark",
        str(tmp_path / "benchmark"),
        "--baseline-index",
        str(tmp_path / "baseline-index"),
        "--candidate-index",
        str(tmp_path / "candidate-index"),
        "--run",
        str(tmp_path / "run"),
        "--yunet-model",
        str(tmp_path / "yunet.onnx"),
        "--sface-model",
        str(tmp_path / "sface.onnx"),
        "--quality-comparison",
        str(tmp_path / "comparison"),
        "--output",
        str(tmp_path / "search-comparison"),
    ]
    parsed = cli.build_parser().parse_args(compare_arguments)
    assert (
        parsed.minimum_face_px,
        parsed.severe_blur_threshold,
        parsed.borderline_blur_threshold,
        parsed.minimum_relative_area,
        parsed.minimum_confidence,
    ) == (32, 25.0, 50.0, 0.0009, 0.82)

    quality_calls: list[cli.CompareQualityConfig] = []
    finalize_calls: list[cli.FinalizeQualityReviewConfig] = []
    search_calls: list[cli.CompareSearchConfig] = []
    monkeypatch.setattr(cli, "run_compare_quality_command", quality_calls.append)
    monkeypatch.setattr(cli, "run_finalize_quality_review_command", finalize_calls.append)
    monkeypatch.setattr(cli, "run_compare_search_command", search_calls.append)

    assert cli.main(compare_arguments) == 0
    assert cli.main(finalize_arguments) == 0
    assert cli.main(search_arguments) == 0
    assert len(quality_calls) == len(finalize_calls) == len(search_calls) == 1
    assert quality_calls[0].baseline_run == tmp_path / "baseline"
    assert finalize_calls[0].reviewer == "reviewer"
    assert search_calls[0].candidate_index == tmp_path / "candidate-index"


def _prepare_quality_finalization_orchestration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[
    cli.FinalizeQualityReviewConfig,
    SearchComparison,
]:
    from face_spike import benchmark_artifacts, quality_comparison_artifacts
    from face_spike.quality_comparison_artifacts import write_search_comparison
    from test_quality_comparison_artifacts import _comparison_evidence
    from test_quality_comparison_report import _benchmark, _search

    comparison, baseline_run, candidate_run = _comparison_evidence()
    benchmark = _benchmark()
    reviewed_search = _search(comparison, benchmark)
    reviewed_search_path = tmp_path / "reviewed-search"
    write_search_comparison(reviewed_search_path, reviewed_search)
    comparison_path = tmp_path / "comparison"
    baseline_path = tmp_path / "baseline"
    candidate_path = tmp_path / "candidate"
    config = cli.FinalizeQualityReviewConfig(
        comparison=comparison_path,
        labels_csv=tmp_path / "labels.csv",
        search_comparison=reviewed_search_path,
        baseline_run=baseline_path,
        candidate_run=candidate_path,
        benchmark=tmp_path / "benchmark",
        baseline_index=tmp_path / "baseline-index",
        candidate_index=tmp_path / "candidate-index",
        run=tmp_path / "query-run",
        yunet_model=tmp_path / "yunet.onnx",
        sface_model=tmp_path / "sface.onnx",
        reviewer="reviewer",
        reviewed_at="2026-08-08T00:00:00Z",
        output=tmp_path / "approval",
    )

    monkeypatch.setattr(
        quality_comparison_artifacts,
        "load_quality_comparison_bundle",
        lambda path: (comparison, "f" * 64),
    )
    monkeypatch.setattr(
        quality_comparison_artifacts,
        "load_quality_review_labels",
        lambda path, source, digest: {"candidate": "blurred"},
    )

    def load_quality_run(path: Path) -> tuple[object, object]:
        run = {baseline_path: baseline_run, candidate_path: candidate_run}[path]
        return run, dict(run.quality_configuration)

    monkeypatch.setattr(quality_comparison_artifacts, "load_quality_run", load_quality_run)
    monkeypatch.setattr(benchmark_artifacts, "load_final_benchmark", lambda path: benchmark)
    return config, reviewed_search


def test_quality_finalization_recomputes_from_exact_sources_in_private_temp_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from face_spike.quality_comparison_artifacts import write_search_comparison

    config, reviewed_search = _prepare_quality_finalization_orchestration(monkeypatch, tmp_path)
    recompute_calls: list[cli.CompareSearchConfig] = []
    temporary_roots: list[Path] = []
    temporary_payloads: list[str] = []

    def recompute(recompute_config: cli.CompareSearchConfig) -> None:
        recompute_calls.append(recompute_config)
        temporary_roots.append(recompute_config.output.parent)
        write_search_comparison(recompute_config.output, reviewed_search)
        temporary_payloads.append(
            "\n".join(
                path.read_text(encoding="utf-8")
                for path in recompute_config.output.iterdir()
                if path.is_file()
            ).lower()
        )

    monkeypatch.setattr(cli, "run_compare_search_command", recompute)

    cli.run_finalize_quality_review_command(config)

    assert recompute_calls == [
        cli.CompareSearchConfig(
            benchmark=config.benchmark,
            baseline_index=config.baseline_index,
            candidate_index=config.candidate_index,
            run=config.run,
            yunet_model=config.yunet_model,
            sface_model=config.sface_model,
            quality_comparison=config.comparison,
            output=recompute_calls[0].output,
        )
    ]
    approval = (config.output / "approval.json").read_text(encoding="utf-8").lower()
    assert '"approved": true' in approval
    assert '"embedding"' not in approval
    assert '"vector"' not in approval
    assert all(not path.exists() for path in temporary_roots)
    assert len(temporary_payloads) == 1
    assert '"embedding"' not in temporary_payloads[0]
    assert '"vector"' not in temporary_payloads[0]


@pytest.mark.parametrize("failure", ["mismatch", "recomputation"])
def test_quality_finalization_failure_never_publishes_or_leaks_temp_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    from face_spike.quality_comparison_artifacts import write_search_comparison

    config, reviewed_search = _prepare_quality_finalization_orchestration(monkeypatch, tmp_path)
    temporary_roots: list[Path] = []

    def recompute(recompute_config: cli.CompareSearchConfig) -> None:
        temporary_roots.append(recompute_config.output.parent)
        if failure == "recomputation":
            raise ValueError("synthetic recomputation failure")
        mismatched = replace(reviewed_search, candidate_index_sha256="d" * 64)
        write_search_comparison(recompute_config.output, mismatched)

    monkeypatch.setattr(cli, "run_compare_search_command", recompute)

    with pytest.raises(cli.QualityComparisonConfigurationError):
        cli.run_finalize_quality_review_command(config)

    assert not config.output.exists()
    assert temporary_roots
    assert all(not path.exists() for path in temporary_roots)


def test_search_comparison_requires_indexes_from_the_exact_compared_runs_and_configuration() -> (
    None
):
    parameters = {
        "detection_threshold": 0.0,
        "image_limit": None,
        "max_image_dimension": 12000,
        "max_image_pixels": 100_000_000,
        "min_face_px": 32,
        "quality_algorithm_version": "normalized-laplacian-v1",
        "quality_crop_size": 112,
        "severe_blur_threshold": 25.0,
        "borderline_blur_threshold": 50.0,
        "minimum_confidence": 0.82,
        "minimum_relative_area": 0.0009,
    }

    def index(run_manifest: str, faces: str, **changes: object) -> SimpleNamespace:
        return SimpleNamespace(
            manifest=SimpleNamespace(
                source_run_manifest_sha256=run_manifest,
                source_faces_sha256=faces,
                parameters={**parameters, **changes},
            )
        )

    baseline = index("a" * 64, "b" * 64)
    candidate = index("c" * 64, "d" * 64)
    comparison = SimpleNamespace(
        baseline_run_sha256=_canonical_sha256(
            {"faces_sha256": "b" * 64, "manifest_sha256": "a" * 64}
        ),
        candidate_run_sha256=_canonical_sha256(
            {"faces_sha256": "d" * 64, "manifest_sha256": "c" * 64}
        ),
        quality_configuration={
            "algorithm_version": "normalized-laplacian-v1",
            "crop_size": 112,
            "minimum_face_px": 32,
            "severe_blur_threshold": 25.0,
            "borderline_blur_threshold": 50.0,
            "minimum_relative_area": 0.0009,
            "minimum_confidence": 0.82,
        },
    )

    cli._validate_quality_comparison_indexes(comparison, baseline, candidate)
    with pytest.raises(ValueError, match="source runs"):
        cli._validate_quality_comparison_indexes(comparison, baseline, index("e" * 64, "d" * 64))
    with pytest.raises(ValueError, match="configuration"):
        cli._validate_quality_comparison_indexes(
            comparison,
            baseline,
            index("c" * 64, "d" * 64, minimum_confidence=0.81),
        )

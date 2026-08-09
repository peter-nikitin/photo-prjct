import hashlib
import json
import weakref
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from .test_preview_corpus import _setup

# ruff: noqa: E501, E701, E702, I001


def _rehash_bundle(output: Path) -> None:
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files.append(
                {
                    "path": path.relative_to(output).as_posix(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
    manifest["files"] = files
    frozen = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    manifest["manifest_sha256"] = hashlib.sha256(
        json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def _write_sample(path: Path, photo_id: str, corpus_hash: str) -> None:
    path.write_text(
        json.dumps(
            {
                "source_bundle_sha256": corpus_hash,
                "rejections": [{"filename": f"photo-{photo_id}.jpg"}],
            }
        ),
        encoding="utf-8",
    )


def _valid_sample_bundle(tmp_path: Path) -> tuple[Path, str]:
    """Build a canonical upstream sample with Task 2's required photo-ID filename."""
    from PIL import Image
    from face_spike.quality_comparison import compare_quality_runs
    from face_spike.quality_comparison_artifacts import write_quality_comparison_bundle
    from face_spike.quality_sample import build_quality_sample
    from face_spike.quality_sample_artifacts import write_quality_sample_bundle
    from .test_quality_comparison import _face, _photo, _quality, _run, _thresholds

    photo_id = "1" * 32
    filename = f"photo-{photo_id}.jpg"
    unresolved = f"photo-{'2' * 32}.jpg"
    baseline = _run(
        _photo(filename, _face("baseline", filename, (0, 0, 20, 20))), _photo(unresolved)
    )
    candidate = _run(
        _photo(
            filename,
            _face(
                "candidate",
                filename,
                (0, 0, 20, 20),
                status="quality_rejected",
                quality=_quality(
                    decision="quality_rejected", reasons=("severe_blur",), sharpness=5
                ),
            ),
        ),
        _photo(unresolved),
    )
    comparison = compare_quality_runs(baseline, candidate, thresholds=_thresholds())
    candidate_run = tmp_path / "candidate-run"
    crop = candidate_run / "faces" / "candidate.png"
    crop.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "gray").save(crop)
    source = tmp_path / "source"
    write_quality_comparison_bundle(source, comparison, candidate_run)
    digest = json.loads((source / "manifest.json").read_text())["bundle_sha256"]
    sample_dir = tmp_path / "sample"
    write_quality_sample_bundle(sample_dir, source, build_quality_sample(comparison, digest, 1))
    return sample_dir / "sample.json", photo_id


def test_bbox_matching_classifies_misses_and_recoveries() -> None:
    from face_spike.preview_profile_comparison import match_detections

    baseline = ((1.0, 1.0, 10.0, 10.0),)
    candidate = ((1.2, 1.2, 10.0, 10.0), (30.0, 30.0, 5.0, 5.0))
    assert match_detections(baseline, candidate) == ((0, 0),)


def test_refuses_output_overwrite(tmp_path: Path) -> None:
    from face_spike.preview_profile_comparison import ComparisonError, refuse_existing_output

    output = tmp_path / "bundle"
    output.mkdir()
    with pytest.raises(ComparisonError, match="exists"):
        refuse_existing_output(output)


def test_comparison_publishes_verified_atomic_bundle_from_same_preview_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from face_spike import preview_profile_comparison as comparison
    from face_spike.preview_corpus import materialize_preview_corpus

    source, originals, corpus = _setup(tmp_path)
    corpus_manifest = materialize_preview_corpus(source, originals, corpus, workers=1)
    photo_id = corpus_manifest.photos[0].photo_id
    sample = tmp_path / "sample.json"
    _write_sample(sample, photo_id, corpus_manifest.source_manifest_sha256)
    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"yunet")
    sface.write_bytes(b"sface")

    class Detector:
        def __init__(self, _model: Path, *, threshold: float) -> None:
            self.threshold = threshold

        def detect(self, _image: object) -> tuple[object, ...]:
            return ()

    monkeypatch.setattr(comparison, "YuNetDetector", Detector)
    monkeypatch.setattr(comparison, "SFaceRecognizer", lambda _model: object())
    monkeypatch.setattr(
        comparison,
        "_load_sample_identity",
        lambda _sample: comparison._SampleIdentity("a" * 64, "b" * 64, "c" * 64, (photo_id,)),
    )
    output = tmp_path / "comparison"
    result = comparison.compare_preview_profiles(
        corpus, sample, yunet, sface, output, problem_photo_ids=(photo_id,)
    )

    assert result.photo_count == 1
    assert (
        comparison.load_verified_profile_comparison(output)["manifest_sha256"]
        == result.manifest_sha256
    )

    (output / "unexpected.txt").write_text("no", encoding="utf-8")
    with pytest.raises(comparison.ComparisonError, match="invalid|unexpected|undeclared"):
        comparison.load_verified_profile_comparison(output)


def test_verifier_rejects_rehashed_empty_report_and_nested_embedding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A self-consistent digest inventory cannot bless contradictory human/biometric evidence."""
    from face_spike import preview_profile_comparison as comparison
    from face_spike.preview_corpus import materialize_preview_corpus

    source, originals, corpus = _setup(tmp_path)
    corpus_manifest = materialize_preview_corpus(source, originals, corpus, workers=1)
    photo_id = corpus_manifest.photos[0].photo_id
    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"yunet")
    sface.write_bytes(b"sface")
    monkeypatch.setattr(comparison, "SFaceRecognizer", lambda _model: object())
    monkeypatch.setattr(
        comparison,
        "YuNetDetector",
        lambda *_args, **_kwargs: type("D", (), {"detect": lambda _self, _bgr: ()})(),
    )
    monkeypatch.setattr(
        comparison,
        "_load_sample_identity",
        lambda _sample: comparison._SampleIdentity("a" * 64, "b" * 64, "c" * 64, (photo_id,)),
    )
    sample = tmp_path / "sample.json"
    sample.write_text("{}", encoding="utf-8")
    output = tmp_path / "comparison"
    comparison.compare_preview_profiles(
        corpus, sample, yunet, sface, output, problem_photo_ids=(photo_id,)
    )
    (output / "report.html").write_text("<!doctype html><body></body>", encoding="utf-8")
    _rehash_bundle(output)
    with pytest.raises(comparison.ComparisonError, match="report"):
        comparison.load_verified_profile_comparison(output)

    # Rebuild then mutate a nested evidence key, updating all hashes again.
    output = tmp_path / "comparison-vector"
    comparison.compare_preview_profiles(
        corpus, sample, yunet, sface, output, problem_photo_ids=(photo_id,)
    )
    evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
    evidence["records"][0]["nested"] = {"embedding_vector": [0.1]}
    (output / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    _rehash_bundle(output)
    with pytest.raises(comparison.ComparisonError, match="vector-free|record schema"):
        comparison.load_verified_profile_comparison(output)


def test_api_normalizes_unsorted_valid_problem_ids_before_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from face_spike import preview_profile_comparison as comparison
    from face_spike.preview_corpus import materialize_preview_corpus

    source, originals, corpus = _setup(tmp_path)
    manifest = materialize_preview_corpus(source, originals, corpus, workers=1)
    identifiers = tuple(item.photo_id for item in manifest.photos)
    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"yunet")
    sface.write_bytes(b"sface")
    monkeypatch.setattr(comparison, "SFaceRecognizer", lambda _model: object())
    monkeypatch.setattr(
        comparison,
        "YuNetDetector",
        lambda *_args, **_kwargs: type("D", (), {"detect": lambda _self, _bgr: ()})(),
    )
    monkeypatch.setattr(
        comparison,
        "_load_sample_identity",
        lambda _sample: comparison._SampleIdentity("a" * 64, "b" * 64, "c" * 64, identifiers),
    )
    sample = tmp_path / "sample.json"
    sample.write_text("{}", encoding="utf-8")
    output = tmp_path / "comparison"
    comparison.compare_preview_profiles(
        corpus, sample, yunet, sface, output, problem_photo_ids=tuple(reversed(identifiers))
    )
    assert json.loads((output / "manifest.json").read_text())["problem_photo_ids"] == sorted(
        identifiers
    )


def test_one_detection_call_and_no_detection_status_per_photo_threshold() -> None:
    from face_spike.preview_profile_comparison import _analyze_one

    calls = 0

    class Detector:
        def detect(self, _bgr: object) -> tuple[object, ...]:
            nonlocal calls
            calls += 1
            return ()

    decoded = SimpleNamespace(
        bgr=np.zeros((8, 8, 3), dtype=np.uint8),
        rgb=np.zeros((8, 8, 3), dtype=np.uint8),
        width=8,
        height=8,
    )
    result = _analyze_one(
        SimpleNamespace(decode=lambda _photo: decoded),
        Detector(),
        Path("photo.jpg"),
        "a" * 32,
        0.75,
    )

    assert calls == 1
    assert result["status"] == "no_detection"
    assert result["detection_count"] == 0


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (RuntimeError("detection_failed"), "detection_failed"),
        (ValueError("x"), "technical_failure"),
    ],
)
def test_technical_status_is_finite_and_isolated(error: Exception, expected: str) -> None:
    from face_spike.preview_profile_comparison import _technical_code

    assert _technical_code(error) == expected


def test_real_complete_synthetic_quality_sample_bundle_loads(tmp_path: Path) -> None:
    """A: comparison input is the existing complete quality-sample artifact, not a fake JSON."""
    from face_spike.quality_sample_artifacts import (
        load_quality_sample_bundle,
        write_quality_sample_bundle,
    )
    from .test_quality_sample_artifacts import _source_bundle

    source, sample = _source_bundle(tmp_path)
    output = tmp_path / "sample"
    write_quality_sample_bundle(output, source, sample)
    assert load_quality_sample_bundle(output)[0] == sample


def test_active_task2_sample_identity_accepts_valid_canonical_bundle(tmp_path: Path) -> None:
    from face_spike.preview_profile_comparison import _load_sample_identity

    sample, photo_id = _valid_sample_bundle(tmp_path)
    identity = _load_sample_identity(sample)
    assert identity.photo_ids == (photo_id,)
    assert (
        len(identity.raw_sha256)
        == len(identity.sample_sha256)
        == len(identity.source_bundle_sha256)
        == 64
    )


def test_active_task2_sample_identity_rejects_canonical_legacy_filename(tmp_path: Path) -> None:
    from face_spike.preview_profile_comparison import ComparisonError, _load_sample_identity
    from face_spike.quality_sample_artifacts import write_quality_sample_bundle
    from .test_quality_sample_artifacts import _source_bundle

    source, sample = _source_bundle(tmp_path)
    bundle = tmp_path / "legacy-sample"
    write_quality_sample_bundle(bundle, source, sample)
    with pytest.raises(ComparisonError, match="rejection filename"):
        _load_sample_identity(bundle / "sample.json")


@pytest.mark.parametrize("target", ["sample_sha256", "source_bundle_sha256", "duplicate"])
def test_active_task2_sample_identity_rejects_tampered_bundle(tmp_path: Path, target: str) -> None:
    """B-D: malformed canonical artifacts are rejected by the active comparison input seam."""
    from face_spike.preview_profile_comparison import ComparisonError, _load_sample_identity

    sample, _photo_id = _valid_sample_bundle(tmp_path)
    if target == "source_bundle_sha256":
        path = sample.parent / "manifest.json"
        payload = json.loads(path.read_text())
        payload[target] = "0" * 64
    else:
        path = sample
        payload = json.loads(path.read_text())
        if target == "duplicate":
            payload["rejections"].append(payload["rejections"][0])
        else:
            payload[target] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ComparisonError):
        _load_sample_identity(sample)


def test_sample_digest_tamper_rejects(tmp_path: Path) -> None:
    """B: canonical sample identity is fail-closed."""
    from face_spike.quality_sample_artifacts import (
        load_quality_sample_bundle,
        write_quality_sample_bundle,
    )
    from .test_quality_sample_artifacts import _source_bundle

    source, sample = _source_bundle(tmp_path)
    output = tmp_path / "sample"
    write_quality_sample_bundle(output, source, sample)
    payload = json.loads((output / "sample.json").read_text())
    payload["sample_sha256"] = "0" * 64
    (output / "sample.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_quality_sample_bundle(output)


def test_sample_source_bundle_mismatch_rejects(tmp_path: Path) -> None:
    """C: sample bundle source binding is validated by the existing loader."""
    from face_spike.quality_sample_artifacts import (
        load_quality_sample_bundle,
        write_quality_sample_bundle,
    )
    from .test_quality_sample_artifacts import _source_bundle

    source, sample = _source_bundle(tmp_path)
    output = tmp_path / "sample"
    write_quality_sample_bundle(output, source, sample)
    payload = json.loads((output / "manifest.json").read_text())
    payload["source_bundle_sha256"] = "0" * 64
    (output / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_quality_sample_bundle(output)


def test_sample_duplicate_rejection_evidence_rejects(tmp_path: Path) -> None:
    """D: duplicate rejected-face evidence is rejected before profiling."""
    from face_spike.quality_sample_artifacts import (
        load_quality_sample_bundle,
        write_quality_sample_bundle,
    )
    from .test_quality_sample_artifacts import _source_bundle

    source, sample = _source_bundle(tmp_path)
    output = tmp_path / "sample"
    write_quality_sample_bundle(output, source, sample)
    payload = json.loads((output / "sample.json").read_text())
    payload["rejections"].append(payload["rejections"][0])
    (output / "sample.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_quality_sample_bundle(output)


def _nonzero_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, wide: bool = False, miss: bool = False
) -> tuple[object, Path, str, list[float]]:
    """Build a real published comparison with threshold-varying face detections."""
    from face_spike import preview_profile_comparison as comparison
    from face_spike.analysis import BoundingBox, FaceDetection, FaceLandmarks
    from face_spike.preview_corpus import materialize_preview_corpus

    source, originals, corpus = _setup(tmp_path)
    corpus_manifest = materialize_preview_corpus(source, originals, corpus, workers=1)
    photo_id = corpus_manifest.photos[0].photo_id
    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"yunet")
    sface.write_bytes(b"sface")
    calls: list[float] = []
    landmarks = FaceLandmarks((1, 1), (2, 1), (1, 2), (1, 3), (2, 3))

    class Detector:
        def __init__(self, _model: Path, *, threshold: float) -> None:
            self.threshold = threshold

        def detect(self, _bgr: object) -> tuple[object, ...]:
            calls.append(self.threshold)
            count = ({0.75: 2, 0.70: 1, 0.65: 1} if miss else {0.75: 1, 0.70: 2, 0.65: 1})[
                self.threshold
            ]
            side = 35 if wide else 4
            return tuple(
                FaceDetection(BoundingBox(1 + index * 5, 1, side, side), landmarks, self.threshold)
                for index in range(count)
            )

    monkeypatch.setattr(comparison, "YuNetDetector", Detector)
    monkeypatch.setattr(comparison, "SFaceRecognizer", lambda _model: object())
    monkeypatch.setattr(
        comparison,
        "_load_sample_identity",
        lambda _sample: comparison._SampleIdentity("a" * 64, "b" * 64, "c" * 64, (photo_id,)),
    )
    sample = tmp_path / "sample.json"
    sample.write_text("{}", encoding="utf-8")
    output = tmp_path / "comparison"
    comparison.compare_preview_profiles(
        corpus, sample, yunet, sface, output, problem_photo_ids=(photo_id,)
    )
    return comparison, output, photo_id, calls


def test_e_serialized_matrix_and_boundary_decisions_are_exact() -> None:
    """E: the frozen configuration carries all production decision boundaries."""
    from face_spike.quality_profiles import DECISION_CONFIGURATION, profile_payloads

    assert DECISION_CONFIGURATION.as_payload() == {
        "algorithm_version": "normalized-laplacian-v1",
        "crop_size": 112,
        "minimum_face_px": 32,
        "severe_blur_threshold": 25,
        "borderline_blur_threshold": 50,
        "minimum_relative_area": 0.0009,
        "minimum_confidence": 0.82,
        "detector_thresholds": [0.75, 0.70, 0.65],
    }
    assert [item["name"] for item in profile_payloads()] == [
        "current-v3",
        "small-floor-40",
        "background-blur-75",
        "combined-40-75",
    ]


def test_f_nonzero_threshold_detections_have_one_call_per_threshold_photo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F: output preserves .75/.70/.65 separation and exactly one detector pass each."""
    _comparison, output, _photo_id, calls = _nonzero_comparison(tmp_path, monkeypatch)
    records = json.loads((output / "evidence.json").read_text())["records"]
    assert calls == [0.75, 0.70, 0.65]
    assert [row["detection_count"] for row in records] == [1, 2, 1]


def test_g_one_measurement_feeds_all_four_profile_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G: profile decisions share the persisted production measurements."""
    _comparison, output, _photo_id, _calls = _nonzero_comparison(tmp_path, monkeypatch)
    face = json.loads((output / "evidence.json").read_text())["records"][0]["detections"][0]
    assert set(face["profiles"]) == {
        "current-v3",
        "small-floor-40",
        "background-blur-75",
        "combined-40-75",
    }
    assert face["minimum_side_px"] == 4


def test_g_measurement_called_once_per_detection_then_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G: one production measurement is shared by all four pure decisions."""
    from face_spike import preview_profile_comparison as comparison
    from face_spike.analysis import BoundingBox, FaceDetection, FaceLandmarks
    from photo_worker.face_quality import FaceQualityEvidence

    calls = 0

    def measure(*_args: object, **_kwargs: object) -> FaceQualityEvidence:
        nonlocal calls
        calls += 1
        return FaceQualityEvidence(
            "normalized-laplacian-v1", 112, 0.2, 35, 0.01, 100, "accepted", ()
        )

    monkeypatch.setattr(comparison, "evaluate_face_quality", measure)
    detection = FaceDetection(
        BoundingBox(1, 1, 4, 4), FaceLandmarks((1, 1), (2, 1), (1, 2), (1, 3), (2, 3)), 0.2
    )
    decoded = SimpleNamespace(
        bgr=np.zeros((8, 8, 3), dtype=np.uint8),
        rgb=np.zeros((8, 8, 3), dtype=np.uint8),
        width=8,
        height=8,
    )
    record = comparison._analyze_one(
        SimpleNamespace(decode=lambda _photo: decoded),
        SimpleNamespace(detect=lambda _bgr: (detection,)),
        Path("p.jpg"),
        "a" * 32,
        0.75,
    )
    assert calls == 1 and record["detections"][0]["minimum_side_px"] == 35
    assert record["detections"][0]["profiles"]["small-floor-40"]["decision"] == "quality_rejected"


@pytest.mark.parametrize("error", [RuntimeError("detection_failed"), ValueError("unexpected")])
def test_analyze_one_technical_failure_record_is_exact(error: Exception) -> None:
    """Technical detector failures stay isolated as zero-detection records."""
    from face_spike.preview_profile_comparison import _analyze_one

    decoder = SimpleNamespace(
        decode=lambda _photo: SimpleNamespace(
            bgr=np.zeros((8, 8, 3), dtype=np.uint8),
            rgb=np.zeros((8, 8, 3), dtype=np.uint8),
            width=8,
            height=8,
        )
    )
    record = _analyze_one(
        decoder,
        SimpleNamespace(detect=lambda _bgr: (_ for _ in ()).throw(error)),
        Path("p.jpg"),
        "a" * 32,
        0.75,
    )
    assert record["status"] in {"detection_failed", "technical_failure"}
    assert record["detection_count"] == 0 and record["detections"] == []


def test_h_output_deltas_and_rehashed_ghost_delta_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H: matched lower-threshold recovery is emitted and forged identity is rejected."""
    comparison, output, _photo_id, _calls = _nonzero_comparison(tmp_path, monkeypatch)
    evidence = json.loads((output / "evidence.json").read_text())
    assert evidence["records"][1]["threshold_delta"]["recoveries"]
    assert evidence["records"][2]["threshold_delta"]["misses"] == []
    evidence["records"][1]["threshold_delta"]["recoveries"] = ["ghost"]
    (output / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    _rehash_bundle(output)
    with pytest.raises(comparison.ComparisonError, match="delta"):
        comparison.load_verified_profile_comparison(output)


def test_published_lower_threshold_miss_is_verified_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    comparison, output, _photo_id, _calls = _nonzero_comparison(tmp_path, monkeypatch, miss=True)
    assert comparison.load_verified_profile_comparison(output)
    records = json.loads((output / "evidence.json").read_text())["records"]
    baseline_id = records[0]["detections"][1]["identity"]
    assert records[1]["threshold_delta"]["misses"] == [baseline_id]


def test_i_rehashed_contradictory_statuses_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I: ok/no_detection/technical are mutually exclusive persisted shapes."""
    comparison, output, _photo_id, _calls = _nonzero_comparison(tmp_path, monkeypatch)
    evidence = json.loads((output / "evidence.json").read_text())
    evidence["records"][0]["status"] = "ok"
    evidence["records"][0]["detections"] = []
    evidence["records"][0]["detection_count"] = 0
    (output / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    _rehash_bundle(output)
    with pytest.raises(comparison.ComparisonError, match="ok status"):
        comparison.load_verified_profile_comparison(output)


def test_k_rehashed_missing_problem_preview_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K: a problem preview is mandatory even if hashes are recomputed."""
    comparison, output, _photo_id, _calls = _nonzero_comparison(tmp_path, monkeypatch)
    evidence = json.loads((output / "evidence.json").read_text())
    for record in evidence["records"]:
        record["preview_path"] = None
    (output / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    _rehash_bundle(output)
    with pytest.raises(comparison.ComparisonError, match="problem preview"):
        comparison.load_verified_profile_comparison(output)


def test_l_bundle_verifier_rejects_symlink_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L: bundle verification, not only HTML rendering, rejects symlinked media."""
    comparison, output, _photo_id, _calls = _nonzero_comparison(tmp_path, monkeypatch)
    preview = next((output / "previews").iterdir())
    target = tmp_path / "outside.jpg"
    target.write_bytes(preview.read_bytes())
    preview.unlink()
    preview.symlink_to(target)
    with pytest.raises(comparison.ComparisonError, match="symlink|invalid|unexpected"):
        comparison.load_verified_profile_comparison(output)


def test_m_forced_staged_verifier_failure_cleans_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M: failed final validation removes sibling staging and never publishes output."""
    comparison, _output, photo_id, _calls = _nonzero_comparison(tmp_path, monkeypatch)
    # A new destination is forced to fail during its staging verification.
    monkeypatch.setattr(
        comparison,
        "load_verified_profile_comparison",
        lambda _path: (_ for _ in ()).throw(comparison.ComparisonError("forced")),
    )
    source, originals, corpus = _setup(tmp_path / "retry")
    from face_spike.preview_corpus import materialize_preview_corpus

    manifest = materialize_preview_corpus(source, originals, corpus, workers=1)
    photo_id = manifest.photos[0].photo_id
    yunet = tmp_path / "retry-y.onnx"
    sface = tmp_path / "retry-s.onnx"
    yunet.write_bytes(b"y")
    sface.write_bytes(b"s")
    monkeypatch.setattr(
        comparison,
        "YuNetDetector",
        lambda *_args, **_kwargs: type("D", (), {"detect": lambda _self, _bgr: ()})(),
    )
    monkeypatch.setattr(comparison, "SFaceRecognizer", lambda _model: object())
    monkeypatch.setattr(
        comparison,
        "_load_sample_identity",
        lambda _sample: comparison._SampleIdentity("a" * 64, "b" * 64, "c" * 64, (photo_id,)),
    )
    output = tmp_path / "failed-output"
    sample = tmp_path / "retry-sample.json"
    sample.write_text("{}")
    with pytest.raises(comparison.ComparisonError, match="forced"):
        comparison.compare_preview_profiles(
            corpus, sample, yunet, sface, output, problem_photo_ids=(photo_id,)
        )
    assert not output.exists() and not list(tmp_path.glob(".failed-output.staging.*"))


def test_k2_rehashed_missing_changed_crop_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K2: changed decision media cannot be removed behind recomputed hashes."""
    from photo_worker.face_quality import FaceQualityEvidence
    from face_spike import preview_profile_comparison as comparison

    monkeypatch.setattr(
        comparison,
        "evaluate_face_quality",
        lambda *_args, **_kwargs: FaceQualityEvidence(
            "normalized-laplacian-v1", 112, 0.9, 35, 0.01, 100, "accepted", ()
        ),
    )
    comparison, output, _photo_id, _calls = _nonzero_comparison(tmp_path, monkeypatch, wide=True)
    assert comparison.load_verified_profile_comparison(output)
    crop = next((output / "crops").iterdir())
    crop.unlink()
    # Keep all serialized evidence intact: only mandatory media coverage is removed.
    _rehash_bundle(output)
    with pytest.raises(
        comparison.ComparisonError, match="crop media|changed decision media|media coverage"
    ):
        comparison.load_verified_profile_comparison(output)


def test_o_rehashed_current_and_candidate_reason_tamper_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O: both production and candidate reason payloads are recomputed by the loader."""
    comparison, output, _photo_id, _calls = _nonzero_comparison(tmp_path, monkeypatch)
    evidence = json.loads((output / "evidence.json").read_text())
    face = evidence["records"][0]["detections"][0]
    face["current"]["reasons"] = []
    face["profiles"]["current-v3"]["reasons"] = ["invented"]
    (output / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    _rehash_bundle(output)
    with pytest.raises(comparison.ComparisonError, match="current decision|profile decision"):
        comparison.load_verified_profile_comparison(output)


@pytest.mark.parametrize("key", ["vector", "nested_embedding"])
def test_p_rehashed_recursive_biometric_key_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str
) -> None:
    """P: no nested vector or embedding evidence survives immutable verification."""
    comparison, output, _photo_id, _calls = _nonzero_comparison(tmp_path, monkeypatch)
    evidence = json.loads((output / "evidence.json").read_text())
    evidence["records"][0]["nested"] = {key: [0.0]}
    (output / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    _rehash_bundle(output)
    with pytest.raises(comparison.ComparisonError, match="vector-free|record schema"):
        comparison.load_verified_profile_comparison(output)


def test_q_decoded_owner_is_released_after_analyze_call() -> None:
    """Q: `_analyze_one` does not retain RGB/BGR frame ownership."""
    from face_spike.preview_profile_comparison import _analyze_one

    held: list[weakref.ReferenceType[object]] = []

    class Image:
        def __init__(self) -> None:
            self.bgr = np.zeros((8, 8, 3), dtype=np.uint8)
            self.rgb = np.zeros((8, 8, 3), dtype=np.uint8)
            self.width = 8
            self.height = 8

    class Decoder:
        def decode(self, _photo: object) -> object:
            image = Image()
            held.append(weakref.ref(image))
            return image

    _analyze_one(Decoder(), SimpleNamespace(detect=lambda _bgr: ()), Path("p.jpg"), "a" * 32, 0.75)
    assert held[0]() is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("detection_count", True),
        ("detections", {}),
        ("threshold_delta", {"misses": {}}),
        ("confidence", True),
        ("bbox_width", True),
        ("reasons", {}),
    ],
)
def test_rehashed_json_type_forgery_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    """Exact JSON scalar/container types are validated after report and inventory rehashing."""
    comparison, output, _photo_id, _calls = _nonzero_comparison(tmp_path, monkeypatch)
    evidence = json.loads((output / "evidence.json").read_text())
    record = evidence["records"][0]
    if field == "confidence":
        record["detections"][0]["confidence"] = value
    elif field == "bbox_width":
        record["detections"][0]["bbox"][2] = value
    elif field == "reasons":
        record["detections"][0]["current"]["reasons"] = value
    else:
        record[field] = value
    (output / "evidence.json").write_text(json.dumps(evidence), encoding="utf-8")
    _rehash_bundle(output)
    with pytest.raises(comparison.ComparisonError):
        comparison.load_verified_profile_comparison(output)

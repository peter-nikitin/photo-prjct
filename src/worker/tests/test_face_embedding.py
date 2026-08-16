from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import cv2
import numpy as np
import photo_worker.face_embedding as face_embedding
import pytest
from photo_worker.adaface import (
    ADAFACE_EMBEDDING_DIMENSIONS,
    ADAFACE_MODEL_NAME,
    AdaFaceError,
    AdaFaceRuntime,
    align_face,
    normalize_embedding,
    prepare_input,
    verify_file_digest,
)
from photo_worker.contracts import FaceEmbeddingFace
from photo_worker.face_embedding import (
    FaceEmbeddingError,
    extract_face_embeddings,
    extract_selfie_embedding,
)
from photo_worker.face_quality import FaceQualityError, FaceQualityEvidence, FaceQualityThresholds
from photo_worker.scrfd import DetectedFace
from PIL import Image, ImageCms


class FakeNumpy:
    def __init__(self, shape: tuple[int, int, int]) -> None:
        self.shape = shape


class DummyImage:
    def __init__(self, width: int, height: int) -> None:
        self.shape = (height, width, 3)


class _FakeDetector:
    def __init__(self, detections: list[dict[str, object]]) -> None:
        self._detections = detections
        self.calls: list[float] = []

    def detect(self, image: object, *, threshold: float) -> tuple[DetectedFace, ...]:
        del image
        self.calls.append(threshold)
        return tuple(
            DetectedFace(
                bbox=(
                    float(detection["bbox"][0]),
                    float(detection["bbox"][1]),
                    float(detection["bbox"][0]) + float(detection["bbox"][2]),
                    float(detection["bbox"][1]) + float(detection["bbox"][3]),
                ),
                confidence=float(detection["confidence"]),
                landmarks=detection["landmarks"],
            )
            for detection in self._detections
        )


def write_jpeg(path: Path) -> None:
    image = Image.new("RGB", (32, 32), "white")
    image.save(path, "JPEG")
    image.close()


def test_adaface_recovers_canonical_crop_from_displaced_scaled_rotated_landmarks() -> None:
    import cv2

    y, x = np.indices((112, 112), dtype=np.uint16)
    canonical = np.stack(
        (
            (x * 2) % 256,
            (y * 2) % 256,
            (x + y) % 256,
        ),
        axis=2,
    ).astype(np.uint8)
    canonical_landmarks = np.asarray(
        (
            (38.2946, 51.6963),
            (73.5318, 51.5014),
            (56.0252, 71.7366),
            (41.5493, 92.3655),
            (70.7299, 92.2041),
        ),
        dtype=np.float32,
    )
    angle = np.deg2rad(23.0)
    scale = 1.4
    canonical_to_source = np.asarray(
        (
            (scale * np.cos(angle), -scale * np.sin(angle), 72.0),
            (scale * np.sin(angle), scale * np.cos(angle), 28.0),
        ),
        dtype=np.float32,
    )
    image = cv2.warpAffine(canonical, canonical_to_source, (260, 240))
    landmarks_array = np.concatenate(
        (canonical_landmarks, np.ones((5, 1), dtype=np.float32)), axis=1
    )
    displaced_landmarks = landmarks_array @ canonical_to_source.T

    aligned = align_face(
        np,
        cv2,
        image,
        tuple(tuple(float(value) for value in point) for point in displaced_landmarks),
    )

    assert aligned.shape == (112, 112, 3)
    assert np.mean(np.abs(aligned[5:-5].astype(np.int16) - canonical[5:-5])) < 1.0
    assert aligned[52, 38].tolist() == pytest.approx(canonical[52, 38].tolist(), abs=2)
    assert aligned[92, 71].tolist() == pytest.approx(canonical[92, 71].tolist(), abs=2)


def test_adaface_converts_bgr_to_rgb_and_normalizes_each_channel() -> None:
    aligned = np.empty((112, 112, 3), dtype=np.uint8)
    aligned[:, :] = (0, 127, 255)

    prepared = prepare_input(np, aligned)

    assert prepared.shape == (1, 3, 112, 112)
    assert prepared.dtype == np.float32
    assert prepared[0, :, 0, 0].tolist() == pytest.approx([1.0, (127.0 / 255.0 - 0.5) / 0.5, -1.0])


def test_adaface_runtime_runs_preprocessed_input_and_normalizes_512_values() -> None:
    class Output:
        def detach(self) -> Output:
            return self

        def cpu(self) -> Output:
            return self

        def numpy(self) -> np.ndarray:
            return np.arange(1, ADAFACE_EMBEDDING_DIMENSIONS + 1, dtype=np.float32)[None, :]

    observed: list[np.ndarray] = []

    class Model:
        def __call__(self, value: np.ndarray) -> Output:
            observed.append(value)
            return Output()

    class Torch:
        @staticmethod
        def from_numpy(value: np.ndarray) -> np.ndarray:
            return value

        @staticmethod
        def inference_mode() -> object:
            return nullcontext()

    import cv2

    runtime = AdaFaceRuntime(model=Model(), torch=Torch())
    image = np.full((112, 112, 3), (0, 127, 255), dtype=np.uint8)
    landmarks = (
        (38.2946, 51.6963),
        (73.5318, 51.5014),
        (56.0252, 71.7366),
        (41.5493, 92.3655),
        (70.7299, 92.2041),
    )

    embedding = runtime.extract(np, cv2, image, landmarks)

    assert observed[0].shape == (1, 3, 112, 112)
    assert observed[0][0, :, 0, 0].tolist() == pytest.approx([1.0, -0.00392157, -1.0])
    assert len(embedding) == ADAFACE_EMBEDDING_DIMENSIONS
    assert sum(value * value for value in embedding) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "values",
    [
        np.ones(511, dtype=np.float32),
        np.full(512, np.nan, dtype=np.float32),
        np.zeros(512, dtype=np.float32),
    ],
)
def test_adaface_rejects_wrong_dimension_non_finite_and_zero_vectors(values: np.ndarray) -> None:
    with pytest.raises(AdaFaceError):
        normalize_embedding(np, values)


def test_adaface_rejects_an_artifact_whose_digest_does_not_match(tmp_path: Path) -> None:
    artifact = tmp_path / "model.safetensors"
    artifact.write_bytes(b"not-the-pinned-model")

    with pytest.raises(AdaFaceError):
        verify_file_digest(artifact, "0" * 64)


def test_detect_faces_adapts_scrfd_source_corners_to_sface_width_and_height() -> None:
    """Passing SCRFD corner coordinates to SFace unchanged would break alignment."""

    detector = _FakeDetector([_detection(size=10)])

    assert face_embedding._detect_faces(detector, object(), 0.5) == [
        {
            "bbox": (1.0, 2.0, 10.0, 10.0),
            "confidence": 0.99,
            "landmarks": ((1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0), (5.0, 5.0)),
            "score": 0.99,
        }
    ]


def test_scrfd_landmarks_drive_adaface_gallery_embedding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source)

    image = DummyImage(32, 32)
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_numpy",
        lambda: FakeNumpy((0, 0, 0)),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_cv2",
        lambda: object(),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._decode_image",
        lambda *_args, **_kwargs: image,
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._model_path",
        lambda *_args, **_kwargs: Path("/tmp/model.bin"),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_models",
        lambda *_args, **_kwargs: (_FakeDetector([_detection(size=10)]), None),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._extract_embedding",
        lambda *_args, **_kwargs: tuple(float(i) for i in range(512)),
    )

    result = extract_face_embeddings(source, max_bytes=1024, model=ADAFACE_MODEL_NAME)

    assert result.faces == (
        FaceEmbeddingFace(
            index=0,
            bbox=(1.0, 2.0, 10.0, 10.0),
            confidence=0.99,
            landmarks=((1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0), (5.0, 5.0)),
            embedding=tuple(float(i) for i in range(512)),
        ),
    )
    assert result.has_single_query_face_usable is True
    assert result.warnings == ()
    assert set(result.timings) == {
        "decode_ms",
        "model_load_ms",
        "detect_ms",
        "embed_ms",
        "total_ms",
    }


def test_extract_face_embeddings_reuses_models_across_image_sizes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source)
    first_image = DummyImage(32, 48)
    second_image = DummyImage(64, 96)

    detector = _FakeDetector([])
    creations = {"detector": 0, "recognizer": 0}
    monkeypatch.setattr("photo_worker.face_embedding._load_numpy", lambda: np)
    monkeypatch.setattr("photo_worker.face_embedding._load_cv2", lambda: object())
    images = [first_image, second_image]
    monkeypatch.setattr(
        "photo_worker.face_embedding._decode_image", lambda *_args, **_kwargs: images.pop(0)
    )
    model_paths = [tmp_path / "scrfd.onnx", tmp_path / "sface.onnx"]
    monkeypatch.setattr(
        "photo_worker.face_embedding._model_path", lambda *_args, **_kwargs: model_paths.pop(0)
    )
    monkeypatch.setattr(face_embedding, "_MODEL_RUNTIMES", {})

    def load_models(*_args: object, **_kwargs: object) -> tuple[_FakeDetector, object]:
        creations["detector"] += 1
        creations["recognizer"] += 1
        return detector, object()

    monkeypatch.setattr("photo_worker.face_embedding._load_models", load_models)

    extract_face_embeddings(source, max_bytes=1024)
    model_paths[:] = [tmp_path / "scrfd.onnx", tmp_path / "sface.onnx"]
    extract_face_embeddings(source, max_bytes=1024)

    assert creations == {"detector": 1, "recognizer": 1}
    assert detector.calls == [0.5, 0.5]


def test_extract_face_embeddings_no_faces_and_no_valid_faces_have_separate_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source)

    image = DummyImage(32, 32)
    detector = _FakeDetector([])
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_numpy",
        lambda: FakeNumpy((0, 0, 0)),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_cv2",
        lambda: object(),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._decode_image",
        lambda *_args, **_kwargs: image,
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._model_path",
        lambda *_args, **_kwargs: Path("/tmp/model.bin"),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_models",
        lambda *_args, **_kwargs: (detector, None),
    )
    monkeypatch.setattr(face_embedding, "_MODEL_RUNTIMES", {})

    result = extract_face_embeddings(source, max_bytes=1024)
    assert result.faces == ()
    assert result.warnings == ("no_faces_detected",)

    detector._detections[:] = [_detection(size=10)]
    monkeypatch.setattr(
        "photo_worker.face_embedding._extract_embedding",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            FaceEmbeddingError("model_inference_error")
        ),
    )

    invalid = extract_face_embeddings(source, max_bytes=1024)
    assert invalid.faces == ()
    assert invalid.warnings == ("face_embedding_failed", "no_valid_faces")


def test_extract_face_embeddings_decode_failure_is_mapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"not-an-image")
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_numpy",
        lambda: FakeNumpy((0, 0, 0)),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_cv2",
        lambda: object(),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._decode_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FaceEmbeddingError("decode_failed")),
    )

    with pytest.raises(FaceEmbeddingError) as raised:
        extract_face_embeddings(source, max_bytes=1024)

    assert raised.value.code == "decode_failed"


def test_extract_face_embeddings_rejects_size_mismatch_without_inflating_arrays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"x" * 2048)
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_numpy",
        lambda: FakeNumpy((0, 0, 0)),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_cv2",
        lambda: object(),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._decode_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FaceEmbeddingError("input_too_large")),
    )

    with pytest.raises(FaceEmbeddingError) as raised:
        extract_face_embeddings(source, max_bytes=1024)

    assert raised.value.code == "input_too_large"


def _selfie_model_mocks(
    monkeypatch: pytest.MonkeyPatch,
    image: DummyImage | None,
    detections: list[dict[str, object]],
) -> _FakeDetector:
    detector = _FakeDetector(detections)
    monkeypatch.setattr("photo_worker.face_embedding._load_numpy", lambda: FakeNumpy((0, 0, 0)))
    monkeypatch.setattr("photo_worker.face_embedding._load_cv2", lambda: object())
    if image is not None:
        monkeypatch.setattr(
            "photo_worker.face_embedding._decode_image", lambda *_args, **_kwargs: image
        )
        monkeypatch.setattr(
            "photo_worker.face_embedding._decode_selfie_image", lambda *_args, **_kwargs: image
        )
    monkeypatch.setattr(
        "photo_worker.face_embedding._model_path", lambda *_args, **_kwargs: Path("/tmp/model.bin")
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_models", lambda *_args, **_kwargs: (detector, None)
    )
    monkeypatch.setattr(face_embedding, "_MODEL_RUNTIMES", {})
    return detector


def _detection(*, size: float = 32.0) -> dict[str, object]:
    return {
        "bbox": (1.0, 2.0, size, size),
        "confidence": 0.99,
        "landmarks": ((1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0), (5.0, 5.0)),
        "score": 0.99,
    }


def _quality_thresholds() -> FaceQualityThresholds:
    return FaceQualityThresholds(
        algorithm_version="normalized-laplacian-v1",
        crop_size=112,
        minimum_face_px=20,
        severe_blur_threshold=10.0,
        borderline_blur_threshold=20.0,
        minimum_relative_area=0.1,
        minimum_confidence=0.8,
    )


def _quality(decision: str) -> FaceQualityEvidence:
    return FaceQualityEvidence(
        algorithm_version="normalized-laplacian-v1",
        crop_size=112,
        confidence=0.99,
        minimum_side_px=30.0,
        relative_area=0.2,
        sharpness=30.0,
        decision=decision,
        reasons=("severe_blur",) if decision == "quality_rejected" else (),
    )


def test_quality_rejection_skips_adaface_and_keeps_explicit_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rejected gallery detection must never reach AdaFace or carry a vector."""
    source = tmp_path / "photo.jpg"
    write_jpeg(source)
    _selfie_model_mocks(monkeypatch, DummyImage(100, 100), [_detection(size=30)])
    monkeypatch.setattr(
        "photo_worker.face_embedding.evaluate_face_quality",
        lambda *_args, **_kwargs: _quality("quality_rejected"),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._extract_embedding",
        lambda *_args, **_kwargs: pytest.fail("AdaFace must not run for quality rejection"),
    )

    result = extract_face_embeddings(
        source, max_bytes=1024, quality_thresholds=_quality_thresholds()
    )

    assert result.faces[0].status == "quality_rejected"
    assert result.faces[0].quality == _quality("quality_rejected")
    assert result.faces[0].embedding is None
    assert result.faces[0].error_code is None


def test_quality_gate_keeps_accepted_vectors_and_isolates_face_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One technical AdaFace failure must not discard a successful sibling face."""
    source = tmp_path / "photo.jpg"
    write_jpeg(source)
    _selfie_model_mocks(
        monkeypatch,
        DummyImage(100, 100),
        [
            _detection(size=30),
            {**_detection(size=31), "bbox": (50.0, 20.0, 31.0, 31.0)},
            {**_detection(size=32), "bbox": (20.0, 50.0, 32.0, 32.0)},
        ],
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding.evaluate_face_quality",
        lambda *_args, **_kwargs: _quality("accepted"),
    )
    calls = iter((FaceEmbeddingError("model_inference_error"), tuple(2.0 for _ in range(512))))

    def extract(*_args: object, **_kwargs: object) -> tuple[float, ...]:
        result = next(calls)
        if isinstance(result, FaceEmbeddingError):
            raise result
        return result

    monkeypatch.setattr("photo_worker.face_embedding._extract_embedding", extract)

    result = extract_face_embeddings(
        source, max_bytes=1024, max_faces=2, quality_thresholds=_quality_thresholds()
    )

    assert result.warnings == ("faces_truncated", "face_embedding_failed")
    assert len(result.faces) == 2
    assert result.faces[0].status == "technical_failed"
    assert result.faces[0].embedding is None
    assert result.faces[0].error_code == "model_inference_error"
    assert result.faces[1].status == "kept"
    assert result.faces[1].embedding == tuple(2.0 for _ in range(512))


def test_quality_measurement_failure_is_a_technical_record_without_a_vector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Converting an invalid calculation into a quality rejection would hide a processor error."""
    source = tmp_path / "photo.jpg"
    write_jpeg(source)
    _selfie_model_mocks(monkeypatch, DummyImage(100, 100), [_detection(size=30)])
    monkeypatch.setattr(
        "photo_worker.face_embedding.evaluate_face_quality",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FaceQualityError()),
    )

    result = extract_face_embeddings(
        source, max_bytes=1024, quality_thresholds=_quality_thresholds()
    )

    assert result.faces[0].status == "technical_failed"
    assert result.faces[0].error_code == "invalid_face_quality"
    assert result.as_payload()["faces"][0] == {
        "index": 0,
        "bbox": [1.0, 2.0, 30.0, 30.0],
        "confidence": 0.99,
        "landmarks": [[1.0, 1.0], [2.0, 2.0], [3.0, 3.0], [4.0, 4.0], [5.0, 5.0]],
        "status": "technical_failed",
        "error_code": "invalid_face_quality",
    }


def test_face_record_rejects_every_contradictory_v3_state() -> None:
    """A terminal record must not combine acceptance, rejection, vector, and error states."""
    accepted = _quality("accepted")
    rejected = _quality("quality_rejected")
    vector = tuple(2.0 for _ in range(512))
    shared = {
        "index": 0,
        "bbox": (1.0, 2.0, 30.0, 30.0),
        "confidence": 0.99,
        "landmarks": ((1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0), (5.0, 5.0)),
    }
    contradictory_states = (
        {"status": "quality_rejected", "quality": accepted, "embedding": None, "error_code": None},
        {"status": "kept", "quality": rejected, "embedding": vector, "error_code": None},
        {
            "status": "quality_rejected",
            "quality": rejected,
            "embedding": vector,
            "error_code": None,
        },
        {
            "status": "quality_rejected",
            "quality": rejected,
            "embedding": None,
            "error_code": "model_inference_error",
        },
        {
            "status": "technical_failed",
            "quality": accepted,
            "embedding": vector,
            "error_code": "model_inference_error",
        },
        {"status": "technical_failed", "quality": None, "embedding": None, "error_code": None},
        {"status": "kept", "quality": accepted, "embedding": None, "error_code": None},
    )

    for state in contradictory_states:
        with pytest.raises(ValueError):
            FaceEmbeddingFace(**shared, **state)


def test_face_record_retains_legacy_and_intended_terminal_forms() -> None:
    """State validation must retain the v1/v2 kept record and both v3 technical forms."""
    accepted = _quality("accepted")
    rejected = _quality("quality_rejected")
    vector = tuple(2.0 for _ in range(512))
    shared = {
        "index": 0,
        "bbox": (1.0, 2.0, 30.0, 30.0),
        "confidence": 0.99,
        "landmarks": ((1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0), (5.0, 5.0)),
    }

    records = (
        FaceEmbeddingFace(**shared, embedding=vector),
        FaceEmbeddingFace(**shared, status="kept", quality=accepted, embedding=vector),
        FaceEmbeddingFace(
            **shared,
            status="quality_rejected",
            quality=rejected,
            embedding=None,
        ),
        FaceEmbeddingFace(
            **shared,
            status="technical_failed",
            quality=accepted,
            embedding=None,
            error_code="model_inference_error",
        ),
        FaceEmbeddingFace(
            **shared,
            status="technical_failed",
            quality=None,
            embedding=None,
            error_code="invalid_face_quality",
        ),
    )

    assert len(records) == 5


def test_extract_selfie_embedding_requires_exactly_one_face_and_normalizes_vector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "selfie.png"
    write_jpeg(source)
    detector = _selfie_model_mocks(monkeypatch, DummyImage(64, 64), [_detection()])
    monkeypatch.setattr(
        "photo_worker.face_embedding._extract_embedding",
        lambda *_args, **_kwargs: tuple(2.0 for _ in range(128)),
    )

    result = extract_selfie_embedding(source, max_bytes=1024, content_type="image/png")

    assert len(result.embedding) == 128
    assert sum(value * value for value in result.embedding) == pytest.approx(1.0)
    assert result.model == "sface"
    assert detector.calls == [0.5]


def test_scrfd_landmarks_drive_transient_adaface_selfie_embedding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "selfie.png"
    write_jpeg(source)
    detector = _selfie_model_mocks(monkeypatch, DummyImage(64, 64), [_detection()])
    monkeypatch.setattr(
        "photo_worker.face_embedding._extract_embedding",
        lambda *_args, **_kwargs: tuple(2.0 for _ in range(512)),
    )

    result = extract_selfie_embedding(
        source,
        max_bytes=1024,
        content_type="image/png",
        model=ADAFACE_MODEL_NAME,
    )

    assert result.model == ADAFACE_MODEL_NAME
    assert len(result.embedding) == 512
    assert sum(value * value for value in result.embedding) == pytest.approx(1.0)
    assert detector.calls == [0.5]


@pytest.mark.parametrize(
    ("detections", "code"),
    [
        ([], "no_face_detected"),
        ([_detection(), _detection()], "multiple_faces_detected"),
        ([_detection(size=31)], "quality_rejected"),
    ],
)
def test_extract_selfie_embedding_maps_face_count_and_quality_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    detections: list[dict[str, object]],
    code: str,
) -> None:
    source = tmp_path / "selfie.jpg"
    write_jpeg(source)
    _selfie_model_mocks(monkeypatch, DummyImage(64, 64), detections)

    with pytest.raises(FaceEmbeddingError, match=code):
        extract_selfie_embedding(source, max_bytes=1024, content_type="image/jpeg")


def test_extract_selfie_embedding_rejects_non_finite_vector_and_releases_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "selfie.jpg"
    write_jpeg(source)
    released: list[bool] = []

    class ReleasableImage(DummyImage):
        def __del__(self) -> None:
            released.append(True)

    image_holder = [ReleasableImage(64, 64)]
    _selfie_model_mocks(monkeypatch, None, [_detection()])
    monkeypatch.setattr(
        "photo_worker.face_embedding._decode_selfie_image",
        lambda *_args, **_kwargs: image_holder.pop(),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._extract_embedding",
        lambda *_args, **_kwargs: (float("nan"),) * 512,
    )

    with pytest.raises(FaceEmbeddingError, match="quality_rejected"):
        extract_selfie_embedding(source, max_bytes=1024, content_type="image/jpeg")

    # The worker must not retain decoded pixels after the failed one-shot query.
    assert released == [True]


def test_decode_selfie_image_downscales_a_large_selfie_without_reencoding(tmp_path: Path) -> None:
    """Removing the 1600 px bound would pass a needlessly large selfie to SCRFD."""
    source = tmp_path / "large-selfie.jpg"
    image = Image.new("RGB", (2400, 1200), "white")
    image.save(source, "JPEG")
    image.close()

    decoded = face_embedding._decode_selfie_image(
        np,
        cv2,
        source,
        max_bytes=source.stat().st_size,
        max_pixels=2_880_000,
    )

    assert decoded.shape == (800, 1600, 3)


def test_decode_selfie_image_does_not_upscale_an_accepted_small_selfie(tmp_path: Path) -> None:
    """Changing normalization to enlarge an 800 px source would fail this test."""
    source = tmp_path / "small-selfie.png"
    image = Image.new("RGB", (800, 400), "white")
    image.save(source, "PNG")
    image.close()

    decoded = face_embedding._decode_selfie_image(
        np,
        cv2,
        source,
        max_bytes=source.stat().st_size,
        max_pixels=320_000,
    )

    assert decoded.shape == (400, 800, 3)


def test_decode_selfie_image_applies_exif_orientation_before_inference(tmp_path: Path) -> None:
    """Skipping EXIF transpose would leave SCRFD with the camera-file geometry."""
    source = tmp_path / "oriented-selfie.jpg"
    image = Image.new("RGB", (40, 20), "white")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, "JPEG", exif=exif)
    image.close()

    decoded = face_embedding._decode_selfie_image(
        np,
        cv2,
        source,
        max_bytes=source.stat().st_size,
        max_pixels=800,
    )

    assert decoded.shape == (40, 20, 3)


def test_decode_selfie_image_converts_an_embedded_non_srgb_profile_before_bgr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping ICC conversion would pass a tagged non-sRGB JPEG straight to OpenCV."""
    source = tmp_path / "profiled-selfie.jpg"
    image = Image.new("RGB", (20, 10), "white")
    lab_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
    image.save(source, "JPEG", icc_profile=lab_profile.tobytes())
    image.close()
    calls: list[tuple[object, object]] = []

    def convert(*args: object, **kwargs: object) -> Image.Image:
        calls.append((args[1], args[2]))
        return args[0].convert("RGB")

    monkeypatch.setattr("PIL.ImageCms.profileToProfile", convert)

    decoded = face_embedding._decode_selfie_image(
        np,
        cv2,
        source,
        max_bytes=source.stat().st_size,
        max_pixels=200,
    )

    assert decoded.shape == (10, 20, 3)
    assert len(calls) == 1

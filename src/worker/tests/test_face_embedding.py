from __future__ import annotations

from pathlib import Path

import numpy as np
import photo_worker.face_embedding as face_embedding
import pytest
from photo_worker.contracts import FaceEmbeddingFace
from photo_worker.face_embedding import (
    FaceEmbeddingError,
    extract_face_embeddings,
    extract_selfie_embedding,
)
from photo_worker.face_quality import FaceQualityError, FaceQualityEvidence, FaceQualityThresholds
from PIL import Image


class FakeNumpy:
    def __init__(self, shape: tuple[int, int, int]) -> None:
        self.shape = shape


class DummyImage:
    def __init__(self, width: int, height: int) -> None:
        self.shape = (height, width, 3)


def write_jpeg(path: Path) -> None:
    image = Image.new("RGB", (32, 32), "white")
    image.save(path, "JPEG")
    image.close()


def test_detect_faces_maps_opencv_no_face_tuple_to_empty() -> None:
    """OpenCV returns ``(retval, None)`` when YuNet finds no faces."""

    class NoFaceDetector:
        def setInputSize(self, _size: tuple[int, int]) -> None:  # noqa: N802
            return None

        def detect(self, _image: object) -> tuple[int, None]:
            return 1, None

    assert face_embedding._detect_faces(np, NoFaceDetector(), object(), 320, 320, 0.75) == []


def test_extract_face_embeddings_one_face_success(
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
        lambda *_args, **_kwargs: (None, None),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._detect_faces",
        lambda *_args, **_kwargs: [
            {
                "bbox": (1.0, 2.0, 10.0, 10.0),
                "confidence": 0.99,
                "landmarks": ((1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0), (5.0, 5.0)),
                "score": 0.99,
            }
        ],
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._extract_embedding",
        lambda *_args, **_kwargs: tuple(float(i) for i in range(128)),
    )

    result = extract_face_embeddings(source, max_bytes=1024)

    assert result.faces == (
        FaceEmbeddingFace(
            index=0,
            bbox=(1.0, 2.0, 10.0, 10.0),
            confidence=0.99,
            landmarks=((1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0), (5.0, 5.0)),
            embedding=tuple(float(i) for i in range(128)),
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


def test_extract_face_embeddings_reuses_models_but_sets_each_image_size(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "photo.jpg"
    write_jpeg(source)
    first_image = DummyImage(32, 48)
    second_image = DummyImage(64, 96)

    class Detector:
        def __init__(self) -> None:
            self.input_sizes: list[tuple[int, int]] = []

        def setInputSize(self, size: tuple[int, int]) -> None:  # noqa: N802
            self.input_sizes.append(size)

        def detect(self, _image: object) -> tuple[None, None]:
            return None, None

    detector = Detector()
    creations = {"detector": 0, "recognizer": 0}

    class FaceDetectorYN:
        @staticmethod
        def create(*_args: object) -> Detector:
            creations["detector"] += 1
            return detector

    class FaceRecognizerSF:
        @staticmethod
        def create(*_args: object) -> object:
            creations["recognizer"] += 1
            return object()

    FakeCv2 = type(
        "FakeCv2",
        (),
        {"FaceDetectorYN": FaceDetectorYN, "FaceRecognizerSF": FaceRecognizerSF},
    )

    monkeypatch.setattr("photo_worker.face_embedding._load_numpy", lambda: np)
    monkeypatch.setattr("photo_worker.face_embedding._load_cv2", lambda: FakeCv2())
    images = [first_image, second_image]
    monkeypatch.setattr(
        "photo_worker.face_embedding._decode_image", lambda *_args, **_kwargs: images.pop(0)
    )
    model_paths = [tmp_path / "yunet.onnx", tmp_path / "sface.onnx"]
    monkeypatch.setattr(
        "photo_worker.face_embedding._model_path", lambda *_args, **_kwargs: model_paths.pop(0)
    )
    monkeypatch.setattr(face_embedding, "_MODEL_RUNTIMES", {})

    extract_face_embeddings(source, max_bytes=1024)
    model_paths[:] = [tmp_path / "yunet.onnx", tmp_path / "sface.onnx"]
    extract_face_embeddings(source, max_bytes=1024)

    assert creations == {"detector": 1, "recognizer": 1}
    assert detector.input_sizes == [(32, 48), (64, 96)]


def test_extract_face_embeddings_no_faces_and_no_valid_faces_have_separate_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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
        lambda *_args, **_kwargs: (None, None),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._detect_faces",
        lambda *_args, **_kwargs: [],
    )

    result = extract_face_embeddings(source, max_bytes=1024)
    assert result.faces == ()
    assert result.warnings == ("no_faces_detected",)

    monkeypatch.setattr(
        "photo_worker.face_embedding._detect_faces",
        lambda *_args, **_kwargs: [
            {
                "bbox": (1.0, 2.0, 10.0, 10.0),
                "confidence": 0.99,
                "landmarks": ((1.0, 1.0), (2.0, 2.0), (3.0, 3.0), (4.0, 4.0), (5.0, 5.0)),
                "score": 0.99,
            }
        ],
    )
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
) -> None:
    monkeypatch.setattr("photo_worker.face_embedding._load_numpy", lambda: FakeNumpy((0, 0, 0)))
    monkeypatch.setattr("photo_worker.face_embedding._load_cv2", lambda: object())
    if image is not None:
        monkeypatch.setattr(
            "photo_worker.face_embedding._decode_image", lambda *_args, **_kwargs: image
        )
    monkeypatch.setattr(
        "photo_worker.face_embedding._model_path", lambda *_args, **_kwargs: Path("/tmp/model.bin")
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._load_models", lambda *_args, **_kwargs: (None, None)
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._detect_faces", lambda *_args, **_kwargs: detections
    )


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


def test_quality_rejection_skips_sface_and_keeps_explicit_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rejected gallery detection must never reach SFace or carry a vector."""
    source = tmp_path / "photo.jpg"
    write_jpeg(source)
    _selfie_model_mocks(monkeypatch, DummyImage(100, 100), [_detection(size=30)])
    monkeypatch.setattr(
        "photo_worker.face_embedding.evaluate_face_quality",
        lambda *_args, **_kwargs: _quality("quality_rejected"),
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._extract_embedding",
        lambda *_args, **_kwargs: pytest.fail("SFace must not run for quality rejection"),
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
    """One technical SFace failure must not discard a successful sibling face."""
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
    calls = iter((FaceEmbeddingError("model_inference_error"), tuple(2.0 for _ in range(128))))

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
    assert result.faces[1].embedding == tuple(2.0 for _ in range(128))


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
    vector = tuple(2.0 for _ in range(128))
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
    vector = tuple(2.0 for _ in range(128))
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
    _selfie_model_mocks(monkeypatch, DummyImage(64, 64), [_detection()])
    monkeypatch.setattr(
        "photo_worker.face_embedding._extract_embedding",
        lambda *_args, **_kwargs: tuple(2.0 for _ in range(128)),
    )

    result = extract_selfie_embedding(source, max_bytes=1024, content_type="image/png")

    assert len(result.embedding) == 128
    assert sum(value * value for value in result.embedding) == pytest.approx(1.0)
    assert result.model == "sface"


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
        "photo_worker.face_embedding._decode_image", lambda *_args, **_kwargs: image_holder.pop()
    )
    monkeypatch.setattr(
        "photo_worker.face_embedding._extract_embedding",
        lambda *_args, **_kwargs: (float("nan"),) * 128,
    )

    with pytest.raises(FaceEmbeddingError, match="quality_rejected"):
        extract_selfie_embedding(source, max_bytes=1024, content_type="image/jpeg")

    # The worker must not retain decoded pixels after the failed one-shot query.
    assert released == [True]

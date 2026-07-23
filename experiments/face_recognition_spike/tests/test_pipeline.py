from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from face_spike.domain import DatasetItem, FaceExpected, LoadedImage
from face_spike.opencv_models import ModelProcessingError, SFaceRecognizer, YuNetDetector
from face_spike.pipeline import (
    BoundingBox,
    FaceDetection,
    FaceEmbedding,
    FaceLandmarks,
    analyze_image,
)


def image(*, width: int = 100, height: int = 80) -> LoadedImage:
    pixels = np.zeros((height, width, 3), dtype=np.uint8)
    return LoadedImage(
        item=DatasetItem("frame.jpg", "rider-01", FaceExpected.YES),
        rgb=pixels[:, :, ::-1].copy(),
        bgr=pixels,
        width=width,
        height=height,
    )


def yunet_row(
    x: float = 10,
    y: float = 20,
    width: float = 30,
    height: float = 40,
    confidence: float = 0.9,
) -> list[float]:
    return [
        x,
        y,
        width,
        height,
        12,
        23,
        33,
        23,
        22,
        30,
        15,
        45,
        30,
        45,
        confidence,
    ]


class FakeYuNetModel:
    def __init__(self, faces: np.ndarray | None) -> None:
        self.faces = faces
        self.input_sizes: list[tuple[int, int]] = []

    def setInputSize(self, size: tuple[int, int]) -> None:  # noqa: N802
        self.input_sizes.append(size)

    def detect(self, bgr: np.ndarray) -> tuple[object, np.ndarray | None]:
        return object(), self.faces


def detector(
    rows: list[list[float]] | None, *, threshold: float = 0.75, min_face_px: int = 2
) -> YuNetDetector:
    model = FakeYuNetModel(None if rows is None else np.asarray(rows, dtype=np.float64))
    return YuNetDetector(
        Path("yunet.onnx"), threshold=threshold, min_face_px=min_face_px, model=model
    )


def test_yunet_returns_no_detections_when_opencv_returns_none() -> None:
    assert detector(None).detect(image().bgr) == ()


def test_yunet_clips_all_edges_and_retains_documented_landmark_order() -> None:
    row = yunet_row(-5, -4, 110, 100)
    row[4:14] = [-2, -3, 105, -2, -1, 81, 105, 82, 50, 90]

    detection = detector([row]).detect(image().bgr)[0]

    assert detection.bounding_box == BoundingBox(0.0, 0.0, 100.0, 80.0)
    assert detection.landmarks == FaceLandmarks(
        right_eye=(0.0, 0.0),
        left_eye=(100.0, 0.0),
        nose=(0.0, 80.0),
        right_mouth_corner=(100.0, 80.0),
        left_mouth_corner=(50.0, 80.0),
    )


@pytest.mark.parametrize(
    "row",
    [
        yunet_row(float("nan")),
        yunet_row(confidence=float("inf")),
        yunet_row()[:14],
    ],
)
def test_yunet_rejects_non_finite_or_wrong_length_rows(row: list[float]) -> None:
    assert detector([row]).detect(image().bgr) == ()


def test_yunet_rejects_non_finite_landmarks_and_below_threshold_boxes() -> None:
    invalid_landmarks = yunet_row()
    invalid_landmarks[5] = float("nan")

    assert detector([invalid_landmarks, yunet_row(confidence=0.74)]).detect(image().bgr) == ()


def test_yunet_rejects_rows_that_cannot_be_converted_to_finite_numbers() -> None:
    malformed = np.asarray([["not-a-number"] * 15], dtype=object)
    model = FakeYuNetModel(malformed)

    assert (
        YuNetDetector(Path("yunet.onnx"), threshold=0.75, min_face_px=2, model=model).detect(
            image().bgr
        )
        == ()
    )


def test_yunet_rejects_boxes_outside_image_after_clipping_and_below_minimum_size() -> None:
    accepted = detector(
        [yunet_row(120, 20, 10, 10), yunet_row(10, 20, 1, 40), yunet_row(10, 20, 30, 40)]
    ).detect(image().bgr)

    assert [entry.bounding_box for entry in accepted] == [BoundingBox(10.0, 20.0, 30.0, 40.0)]


def test_yunet_marks_one_largest_detection_primary_with_stable_tie_breakers() -> None:
    detections = detector(
        [
            yunet_row(20, 10, 20, 20, 0.8),
            yunet_row(10, 20, 20, 20, 0.9),
            yunet_row(5, 30, 20, 20, 0.9),
        ]
    ).detect(image().bgr)

    assert [entry.primary for entry in detections] == [False, False, True]


def test_yunet_sets_input_size_for_each_image() -> None:
    model = FakeYuNetModel(np.asarray([yunet_row()]))
    adapter = YuNetDetector(Path("yunet.onnx"), threshold=0.75, min_face_px=2, model=model)

    adapter.detect(image(width=100, height=80).bgr)
    adapter.detect(image(width=8, height=6).bgr)

    assert model.input_sizes == [(100, 80), (8, 6)]


def primary_detection() -> FaceDetection:
    return FaceDetection(
        bounding_box=BoundingBox(10.0, 20.0, 30.0, 40.0),
        landmarks=FaceLandmarks(
            right_eye=(12.0, 23.0),
            left_eye=(33.0, 23.0),
            nose=(22.0, 30.0),
            right_mouth_corner=(15.0, 45.0),
            left_mouth_corner=(30.0, 45.0),
        ),
        confidence=0.9,
        primary=True,
    )


class FakeSFaceModel:
    def __init__(self, feature: object) -> None:
        self.feature_result = feature
        self.aligned_rows: list[np.ndarray] = []

    def alignCrop(self, bgr: np.ndarray, row: np.ndarray) -> np.ndarray:  # noqa: N802
        self.aligned_rows.append(row)
        return bgr

    def feature(self, aligned: np.ndarray) -> object:
        return self.feature_result


def test_sface_reconstructs_float32_yunet_row_and_normalizes_flattened_feature() -> None:
    model = FakeSFaceModel(np.asarray([[3.0, 4.0]], dtype=np.float64))

    embedding = SFaceRecognizer(Path("sface.onnx"), model=model).extract(
        image().bgr, primary_detection()
    )

    assert model.aligned_rows[0].dtype == np.float32
    assert model.aligned_rows[0].shape == (1, 15)
    np.testing.assert_allclose(model.aligned_rows[0][0], yunet_row())
    assert embedding.vector.dtype == np.float32
    np.testing.assert_allclose(embedding.vector, np.asarray([0.6, 0.8], dtype=np.float32))


@pytest.mark.parametrize(
    "feature",
    [
        np.asarray([1e-30, 2e-30], dtype=np.float32),
        np.asarray([3e38, 3e38], dtype=np.float32),
    ],
)
def test_sface_normalizes_finite_float32_vectors_without_norm_overflow_or_underflow(
    feature: np.ndarray,
) -> None:
    embedding = SFaceRecognizer(Path("sface.onnx"), model=FakeSFaceModel(feature)).extract(
        image().bgr, primary_detection()
    )

    assert embedding.vector.dtype == np.float32
    assert np.isfinite(embedding.vector).all()
    assert np.isclose(np.linalg.norm(embedding.vector.astype(np.float64)), 1.0)


@pytest.mark.parametrize(
    "feature",
    [np.asarray([], dtype=np.float32), np.asarray([0.0, 0.0]), np.asarray([1.0, np.nan])],
)
def test_sface_rejects_empty_zero_or_non_finite_embeddings(feature: np.ndarray) -> None:
    recognizer = SFaceRecognizer(Path("sface.onnx"), model=FakeSFaceModel(feature))

    with pytest.raises(ModelProcessingError) as error:
        recognizer.extract(image().bgr, primary_detection())

    assert error.value.code == "invalid_embedding"


def test_sface_rejects_feature_conversion_overflow_as_invalid_embedding() -> None:
    recognizer = SFaceRecognizer(Path("sface.onnx"), model=FakeSFaceModel([10**400]))

    with pytest.raises(ModelProcessingError) as error:
        recognizer.extract(image().bgr, primary_detection())

    assert error.value.code == "invalid_embedding"


class FailingSFaceModel:
    def __init__(self, *, align_failure: bool = False, feature_failure: bool = False) -> None:
        self.align_failure = align_failure
        self.feature_failure = feature_failure

    def alignCrop(self, bgr: np.ndarray, row: np.ndarray) -> np.ndarray:  # noqa: N802
        if self.align_failure:
            raise RuntimeError("alignment third party details")
        return bgr

    def feature(self, aligned: np.ndarray) -> np.ndarray:
        if self.feature_failure:
            raise RuntimeError("feature third party details")
        return np.asarray([1.0], dtype=np.float32)


@pytest.mark.parametrize(
    ("model", "status"),
    [
        (FailingSFaceModel(align_failure=True), "alignment_failed"),
        (FailingSFaceModel(feature_failure=True), "embedding_failed"),
    ],
)
def test_sface_adapter_exceptions_become_documented_analysis_statuses(
    model: FailingSFaceModel, status: str
) -> None:
    analysis = analyze_image(
        image(),
        FakeDetector((primary_detection(),)),
        SFaceRecognizer(Path("sface.onnx"), model=model),
    )

    assert analysis.status == status
    assert "third party details" not in repr(analysis)


class FakeDetector:
    def __init__(
        self, result: tuple[FaceDetection, ...] = (), failure: Exception | None = None
    ) -> None:
        self.result = result
        self.failure = failure

    def detect(self, bgr: np.ndarray) -> tuple[FaceDetection, ...]:
        if self.failure is not None:
            raise self.failure
        return self.result


def test_model_processing_errors_reject_undocumented_statuses() -> None:
    with pytest.raises(ValueError, match="invalid model processing error code"):
        ModelProcessingError("third party details")


def test_pipeline_does_not_propagate_a_tampered_model_processing_error_code() -> None:
    tampered = ModelProcessingError.__new__(ModelProcessingError)
    tampered.code = "third party details"

    analysis = analyze_image(
        image(), FakeDetector((primary_detection(),)), FakeRecognizer(failure=tampered)
    )

    assert analysis.status == "embedding_failed"
    assert "third party details" not in repr(analysis)


class FakeRecognizer:
    def __init__(
        self, result: FaceEmbedding | None = None, failure: Exception | None = None
    ) -> None:
        self.result = result
        self.failure = failure

    def extract(self, bgr: np.ndarray, detection: FaceDetection) -> FaceEmbedding:
        if self.failure is not None:
            raise self.failure
        assert self.result is not None
        return self.result


def test_pipeline_records_no_detection_without_calling_recognizer() -> None:
    analysis = analyze_image(image(), FakeDetector(), FakeRecognizer())

    assert analysis.status == "no_detection"
    assert analysis.primary_detection is None
    assert analysis.embedding is None


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (RuntimeError("third party details"), "detection_failed"),
        (ModelProcessingError("alignment_failed"), "alignment_failed"),
        (ModelProcessingError("embedding_failed"), "embedding_failed"),
        (ModelProcessingError("invalid_embedding"), "invalid_embedding"),
    ],
)
def test_pipeline_uses_stable_failure_codes_without_exception_text(
    failure: Exception, status: str
) -> None:
    if status == "detection_failed":
        analysis = analyze_image(image(), FakeDetector(failure=failure), FakeRecognizer())
    else:
        analysis = analyze_image(
            image(), FakeDetector((primary_detection(),)), FakeRecognizer(failure=failure)
        )

    assert analysis.status == status
    assert analysis.embedding is None
    assert "third party details" not in repr(analysis)


def test_pipeline_returns_embedding_for_primary_detection() -> None:
    expected = FaceEmbedding(np.asarray([0.6, 0.8], dtype=np.float32))

    analysis = analyze_image(
        image(), FakeDetector((primary_detection(),)), FakeRecognizer(expected)
    )

    assert analysis.status == "ok"
    assert analysis.primary_detection == primary_detection()
    assert analysis.embedding == expected

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
from photo_worker.scrfd import SCRFDDetector, SCRFDError


@dataclass(frozen=True)
class _ValueInfo:
    name: str
    shape: tuple[int | str, ...]


class _FakeSession:
    def __init__(
        self,
        outputs: list[np.ndarray],
        *,
        input_shape: tuple[int | str, ...] = (1, 3, 640, 640),
        output_names: tuple[str, ...] | None = None,
        output_count: int = 9,
    ) -> None:
        self._outputs = outputs
        self._inputs = [_ValueInfo("input.1", input_shape)]
        names = output_names or tuple(f"output-{index}" for index in range(len(outputs)))
        self._output_info = [
            _ValueInfo(name, tuple(output.shape))
            for name, output in zip(names[:output_count], outputs[:output_count], strict=True)
        ]
        self.calls: list[tuple[list[str] | None, dict[str, np.ndarray]]] = []

    def get_inputs(self) -> list[_ValueInfo]:
        return self._inputs

    def get_outputs(self) -> list[_ValueInfo]:
        return self._output_info

    def run(
        self, output_names: list[str] | None, inputs: dict[str, np.ndarray]
    ) -> list[np.ndarray]:
        self.calls.append((output_names, inputs))
        return self._outputs


def _outputs() -> list[np.ndarray]:
    counts = (12800, 3200, 800)
    return [
        *(np.zeros((1, count, 1), dtype=np.float32) for count in counts),
        *(np.zeros((1, count, 4), dtype=np.float32) for count in counts),
        *(np.zeros((1, count, 10), dtype=np.float32) for count in counts),
    ]


def _official_det_10g_outputs() -> list[np.ndarray]:
    return [output[0] for output in _outputs()]


def _detector(outputs: list[np.ndarray]) -> tuple[SCRFDDetector, _FakeSession]:
    session = _FakeSession(outputs)
    return SCRFDDetector(Path("not-a-real-model.onnx"), session=session), session


def _set_detection(
    outputs: list[np.ndarray],
    *,
    stride_index: int,
    anchor_index: int,
    score: float,
    distances: tuple[float, float, float, float],
    landmarks: tuple[float, ...],
) -> None:
    outputs[stride_index][0, anchor_index, 0] = score
    outputs[3 + stride_index][0, anchor_index] = distances
    outputs[6 + stride_index][0, anchor_index] = landmarks


def test_detect_returns_no_faces_and_builds_the_fixed_normalized_rgb_blob() -> None:
    """Changing SCRFD's fixed RGB input contract must make this regression fail."""
    detector, session = _detector(_outputs())
    image = np.zeros((200, 400, 3), dtype=np.uint8)
    image[0, 0] = (255, 127, 0)

    assert detector.detect(image, threshold=0.5) == ()

    _output_names, inputs = session.calls[0]
    blob = inputs["input.1"]
    assert blob.shape == (1, 3, 640, 640)
    np.testing.assert_allclose(blob[0, :, 0, 0], (-127.5 / 128, -0.5 / 128, 127.5 / 128))
    np.testing.assert_allclose(blob[0, :, 500, 500], (-127.5 / 128,) * 3)


def test_detector_accepts_the_official_dynamic_input_and_unbatched_det_10g_outputs() -> None:
    """Rejecting the checksum-verified v0.7 graph blocks the worker image before inference."""
    session = _FakeSession(
        _official_det_10g_outputs(),
        input_shape=(1, 3, "?", "?"),
        output_names=("448", "471", "494", "451", "474", "497", "454", "477", "500"),
    )
    detector = SCRFDDetector(Path("det_10g.onnx"), session=session)

    assert detector.detect(np.zeros((320, 320, 3), dtype=np.uint8), threshold=0.5) == ()


def test_detect_decodes_one_face_at_the_inclusive_production_threshold() -> None:
    """Changing SCRFD stride decoding or the 0.5 score boundary must fail this test."""
    outputs = _outputs()
    _set_detection(
        outputs,
        stride_index=0,
        anchor_index=2 * (5 * 80 + 7),
        score=0.5,
        distances=(1.0, 2.0, 3.0, 4.0),
        landmarks=(1.0, 1.0, 2.0, 1.0, 1.5, 2.0, 1.0, 3.0, 2.0, 3.0),
    )
    detector, _session = _detector(outputs)

    faces = detector.detect(np.zeros((640, 640, 3), dtype=np.uint8), threshold=0.5)

    assert len(faces) == 1
    assert faces[0].bbox == (48.0, 24.0, 80.0, 72.0)
    assert faces[0].confidence == 0.5
    assert faces[0].landmarks == (
        (64.0, 48.0),
        (72.0, 48.0),
        (68.0, 56.0),
        (64.0, 64.0),
        (72.0, 64.0),
    )


def test_detect_keeps_the_stable_highest_scored_face_after_nms() -> None:
    """Removing deterministic 0.4 NMS or confidence ordering must fail this test."""
    outputs = _outputs()
    _set_detection(
        outputs,
        stride_index=0,
        anchor_index=0,
        score=0.9,
        distances=(1.0, 1.0, 4.0, 4.0),
        landmarks=(0.0,) * 10,
    )
    _set_detection(
        outputs,
        stride_index=0,
        anchor_index=1,
        score=0.8,
        distances=(1.0, 1.0, 4.0, 4.0),
        landmarks=(0.0,) * 10,
    )
    _set_detection(
        outputs,
        stride_index=0,
        anchor_index=10,
        score=0.7,
        distances=(1.0, 1.0, 2.0, 2.0),
        landmarks=(0.0,) * 10,
    )
    detector, _session = _detector(outputs)

    faces = detector.detect(np.zeros((640, 640, 3), dtype=np.uint8), threshold=0.5)

    assert [face.confidence for face in faces] == pytest.approx([0.9, 0.7])
    assert [face.bbox for face in faces] == [
        (0.0, 0.0, 32.0, 32.0),
        (32.0, 0.0, 56.0, 16.0),
    ]


def test_detect_maps_letterboxed_coordinates_back_to_the_source_and_clips_them() -> None:
    """Changing aspect-ratio mapping or source clipping must fail this test."""
    outputs = _outputs()
    _set_detection(
        outputs,
        stride_index=2,
        anchor_index=0,
        score=0.9,
        distances=(1.0, 1.0, 3.0, 4.0),
        landmarks=(-1.0, -1.0, 1.0, 1.0, 10.0, 10.0, 2.0, 3.0, 3.0, 4.0),
    )
    detector, _session = _detector(outputs)

    faces = detector.detect(np.zeros((200, 100, 3), dtype=np.uint8), threshold=0.5)

    assert len(faces) == 1
    assert faces[0].bbox == (0.0, 0.0, 30.0, 40.0)
    assert faces[0].landmarks == (
        (0.0, 0.0),
        (10.0, 10.0),
        (100.0, 100.0),
        (20.0, 30.0),
        (30.0, 40.0),
    )


def test_detect_maps_with_the_integer_resize_scale_used_by_letterbox() -> None:
    """Using the nominal 0.4 scale would misalign a 1600x64 source after rounding to 25px."""
    outputs = _outputs()
    _set_detection(
        outputs,
        stride_index=0,
        anchor_index=2 * (1 * 80 + 2),
        score=0.9,
        distances=(1.0, 0.5, 3.0, 1.0),
        landmarks=(0.0, 0.0, 1.0, 0.0, 2.0, 1.0, 1.0, 2.0, 0.0, 1.0),
    )
    detector, _session = _detector(outputs)

    faces = detector.detect(np.zeros((64, 1600, 3), dtype=np.uint8), threshold=0.5)

    assert len(faces) == 1
    assert faces[0].bbox == pytest.approx((20.48, 10.24, 102.4, 40.96))
    np.testing.assert_allclose(
        faces[0].landmarks,
        ((40.96, 20.48), (61.44, 20.48), (81.92, 40.96), (61.44, 61.44), (40.96, 40.96)),
    )


def test_detect_suppresses_the_pixel_inclusive_overlap_just_above_nms_threshold() -> None:
    """Continuous-coordinate IoU keeps these 0.4043-overlap SCRFD boxes incorrectly."""
    outputs = _outputs()
    _set_detection(
        outputs,
        stride_index=0,
        anchor_index=2 * (2 * 80 + 2),
        score=0.9,
        distances=(2.0, 2.0, 2.0, 2.0),
        landmarks=(0.0,) * 10,
    )
    _set_detection(
        outputs,
        stride_index=0,
        anchor_index=2 * (2 * 80 + 4),
        score=0.8,
        distances=(2.25, 2.0, 1.75, 2.0),
        landmarks=(0.0,) * 10,
    )
    detector, _session = _detector(outputs)

    faces = detector.detect(np.zeros((640, 640, 3), dtype=np.uint8), threshold=0.5)

    assert [face.confidence for face in faces] == pytest.approx([0.9])
    assert [face.bbox for face in faces] == [(0.0, 0.0, 32.0, 32.0)]


def test_detect_runs_nms_before_clipping_edge_crossing_boxes() -> None:
    """Clipping first turns two low-overlap source boxes into near-duplicates at the edge."""
    outputs = _outputs()
    _set_detection(
        outputs,
        stride_index=0,
        anchor_index=0,
        score=0.9,
        distances=(10.0, 0.0, 3.75, 4.0),
        landmarks=(-10.0, 0.0, 0.0, 0.0, 3.75, 4.0, 0.0, 4.0, -1.0, 1.0),
    )
    _set_detection(
        outputs,
        stride_index=0,
        anchor_index=1,
        score=0.8,
        distances=(0.0, 0.0, 2.5, 4.0),
        landmarks=(0.0,) * 10,
    )
    detector, _session = _detector(outputs)

    faces = detector.detect(np.zeros((640, 640, 3), dtype=np.uint8), threshold=0.5)

    assert [face.confidence for face in faces] == pytest.approx([0.9, 0.8])
    assert [face.bbox for face in faces] == [
        (0.0, 0.0, 30.0, 32.0),
        (0.0, 0.0, 20.0, 32.0),
    ]
    assert faces[0].landmarks[0] == (0.0, 0.0)


def test_detector_rejects_a_graph_without_the_scrfd_nine_output_contract() -> None:
    """Accepting a non-SCRFD graph would defer a deploy-time model error to inference."""
    session = _FakeSession(_outputs(), output_count=8)

    with pytest.raises(SCRFDError) as raised:
        SCRFDDetector(Path("secret-model-path.onnx"), session=session)

    assert "secret-model-path.onnx" not in str(raised.value)

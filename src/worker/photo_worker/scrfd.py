"""CPU SCRFD-10G_KPS face detection behind a small worker-owned interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

INPUT_SIZE = 640
INPUT_MEAN = 127.5
INPUT_SCALE = 1.0 / 128.0
STRIDES = (8, 16, 32)
ANCHORS_PER_LOCATION = 2
NMS_THRESHOLD = 0.4
OUTPUT_ROWS = (12800, 3200, 800)
OUTPUT_COLUMNS = (1, 1, 1, 4, 4, 4, 10, 10, 10)


class SCRFDError(ValueError):
    """Raised when the SCRFD model or inference output is unusable."""


@dataclass(frozen=True)
class DetectedFace:
    """A source-space SCRFD detection using ``(x1, y1, x2, y2)`` coordinates."""

    bbox: tuple[float, float, float, float]
    confidence: float
    landmarks: tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
    ]


class SCRFDDetector:
    """Run the fixed-shape SCRFD-10G_KPS graph on one BGR image at a time."""

    def __init__(self, model_path: Path, *, session: object | None = None) -> None:
        self._model_path = Path(model_path)
        try:
            self._session = session if session is not None else _load_session(self._model_path)
            inputs = self._session.get_inputs()
            outputs = self._session.get_outputs()
            if len(inputs) != 1 or len(outputs) != 9:
                raise SCRFDError("invalid_scrfd_model")
            if not _is_supported_input_shape(tuple(inputs[0].shape)):
                raise SCRFDError("invalid_scrfd_model")
            if not _has_supported_output_shapes(outputs):
                raise SCRFDError("invalid_scrfd_model")
            self._input_name = str(inputs[0].name)
        except SCRFDError:
            raise
        except Exception as error:
            raise SCRFDError("invalid_scrfd_model") from error

    def detect(self, image: np.ndarray, *, threshold: float) -> tuple[DetectedFace, ...]:
        """Return descending-confidence detections mapped from the fixed input to ``image``."""
        try:
            if not 0.0 <= threshold <= 1.0:
                raise ValueError("threshold")
            source, scale = _letterbox(image)
            blob = _blob(source)
            outputs = self._session.run(None, {self._input_name: blob})
            candidates = _decode_outputs(
                outputs,
                threshold=threshold,
                scale=scale,
            )
            return tuple(
                clipped
                for candidate in _nms(candidates)
                if (clipped := _clip_detected_face(candidate, image.shape)) is not None
            )
        except SCRFDError:
            raise
        except Exception as error:
            raise SCRFDError("scrfd_inference_failed") from error


def _load_session(model_path: Path) -> Any:
    try:
        import onnxruntime

        return onnxruntime.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
    except Exception as error:
        raise SCRFDError("invalid_scrfd_model") from error


def _letterbox(image: np.ndarray) -> tuple[np.ndarray, float]:
    source = np.asarray(image)
    if source.ndim != 3 or source.shape[2] != 3:
        raise ValueError("image")
    height, width = source.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("image")
    if not np.isfinite(source).all():
        raise ValueError("image")
    scale = min(INPUT_SIZE / width, INPUT_SIZE / height)
    resized_width = max(1, int(width * scale))
    resized_height = max(1, int(height * scale))
    resized = cv2.resize(source, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    letterboxed = np.zeros((INPUT_SIZE, INPUT_SIZE, 3), dtype=np.float32)
    letterboxed[:resized_height, :resized_width] = resized
    return letterboxed, resized_height / height


def _blob(letterboxed: np.ndarray) -> np.ndarray:
    rgb = letterboxed[:, :, ::-1]
    return (
        ((rgb - INPUT_MEAN) * INPUT_SCALE)
        .transpose(2, 0, 1)[np.newaxis, ...]
        .astype(np.float32, copy=False)
    )


def _decode_outputs(
    outputs: Any,
    *,
    threshold: float,
    scale: float,
) -> list[DetectedFace]:
    if not isinstance(outputs, (list, tuple)) or len(outputs) != 9:
        raise ValueError("outputs")
    decoded: list[DetectedFace] = []
    for index, stride in enumerate(STRIDES):
        rows = OUTPUT_ROWS[index]
        scores = _output_array(outputs[index], rows, 1)[:, 0]
        boxes = _output_array(outputs[index + len(STRIDES)], rows, 4) * stride
        keypoints = _output_array(outputs[index + len(STRIDES) * 2], rows, 10) * stride
        centers = _anchor_centers(stride)
        for row in np.flatnonzero(scores >= threshold):
            center_x, center_y = centers[row]
            left, top, right, bottom = boxes[row]
            x1 = (center_x - left) / scale
            y1 = (center_y - top) / scale
            x2 = (center_x + right) / scale
            y2 = (center_y + bottom) / scale
            if x2 <= x1 or y2 <= y1:
                continue
            points = keypoints[row].reshape(5, 2)
            landmarks = tuple(
                ((center_x + point_x) / scale, (center_y + point_y) / scale)
                for point_x, point_y in points
            )
            decoded.append(
                DetectedFace(
                    bbox=(float(x1), float(y1), float(x2), float(y2)),
                    confidence=float(scores[row]),
                    landmarks=(
                        landmarks[0],
                        landmarks[1],
                        landmarks[2],
                        landmarks[3],
                        landmarks[4],
                    ),
                )
            )
    return decoded


def _is_supported_input_shape(shape: tuple[Any, ...]) -> bool:
    return shape in {(1, 3, INPUT_SIZE, INPUT_SIZE), (1, 3, "?", "?")}


def _has_supported_output_shapes(outputs: Any) -> bool:
    try:
        return all(
            tuple(output.shape) in {(rows, columns), (1, rows, columns)}
            for output, rows, columns in zip(
                outputs, _output_rows_by_output(), OUTPUT_COLUMNS, strict=True
            )
        )
    except Exception:
        return False


def _output_rows_by_output() -> tuple[int, ...]:
    return OUTPUT_ROWS + OUTPUT_ROWS + OUTPUT_ROWS


def _output_array(output: Any, rows: int, columns: int) -> np.ndarray:
    array = np.asarray(output, dtype=np.float32)
    if array.shape == (1, rows, columns):
        array = array[0]
    if array.shape != (rows, columns) or not np.isfinite(array).all():
        raise ValueError("output")
    return array


def _anchor_centers(stride: int) -> np.ndarray:
    grid = INPUT_SIZE // stride
    centers = np.stack(np.mgrid[:grid, :grid][::-1], axis=-1).astype(np.float32) * stride
    return np.repeat(centers.reshape(-1, 2), ANCHORS_PER_LOCATION, axis=0)


def _clip_detected_face(face: DetectedFace, source_shape: tuple[int, ...]) -> DetectedFace | None:
    height, width = source_shape[:2]
    clipped_box = _clip_box(*face.bbox, width, height)
    if clipped_box is None:
        return None
    landmarks = tuple(_clip_point(*point, width, height) for point in face.landmarks)
    return DetectedFace(
        bbox=clipped_box,
        confidence=face.confidence,
        landmarks=(landmarks[0], landmarks[1], landmarks[2], landmarks[3], landmarks[4]),
    )


def _clip_box(
    x1: float, y1: float, x2: float, y2: float, width: int, height: int
) -> tuple[float, float, float, float] | None:
    clipped = (
        max(0.0, min(float(width), float(x1))),
        max(0.0, min(float(height), float(y1))),
        max(0.0, min(float(width), float(x2))),
        max(0.0, min(float(height), float(y2))),
    )
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        return None
    return clipped


def _clip_point(x: float, y: float, width: int, height: int) -> tuple[float, float]:
    return max(0.0, min(float(width), float(x))), max(0.0, min(float(height), float(y)))


def _nms(candidates: list[DetectedFace]) -> list[DetectedFace]:
    ordered = sorted(
        candidates,
        key=lambda face: (-face.confidence, *face.bbox),
    )
    kept: list[DetectedFace] = []
    for face in ordered:
        if all(_iou(face.bbox, selected.bbox) <= NMS_THRESHOLD for selected in kept):
            kept.append(face)
    return kept


def _iou(
    first: tuple[float, float, float, float], second: tuple[float, float, float, float]
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left + 1.0) * max(0.0, bottom - top + 1.0)
    if intersection <= 0.0:
        return 0.0
    first_area = (first[2] - first[0] + 1.0) * (first[3] - first[1] + 1.0)
    second_area = (second[2] - second[0] + 1.0) * (second[3] - second[1] + 1.0)
    return intersection / (first_area + second_area - intersection)

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from .analysis import (
    BoundingBox,
    FaceDetection,
    FaceEmbedding,
    FaceLandmarks,
    FaceProcessingError,
)


class YuNetDetector:
    def __init__(
        self,
        model_path: Path,
        *,
        threshold: float,
        model: Any | None = None,
    ) -> None:
        if not np.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be finite and between zero and one")
        self._threshold = threshold
        self._model = (
            model
            if model is not None
            else cv2.FaceDetectorYN.create(str(model_path), "", (320, 320), threshold, 0.3, 5000)
        )

    def detect(self, bgr: NDArray[np.uint8]) -> tuple[FaceDetection, ...]:
        height, width = bgr.shape[:2]
        try:
            self._model.setInputSize((width, height))
            _, rows = self._model.detect(bgr)
        except Exception:
            raise RuntimeError("detection_failed") from None
        if rows is None:
            return ()
        return tuple(
            detection
            for row in np.asarray(rows)
            if (detection := self._normalize_row(row, width, height)) is not None
        )

    def _normalize_row(
        self, raw_row: NDArray[np.generic], image_width: int, image_height: int
    ) -> FaceDetection | None:
        try:
            row = np.asarray(raw_row, dtype=np.float64).reshape(-1)
        except (TypeError, ValueError, OverflowError):
            return None
        if row.size != 15 or not np.isfinite(row).all() or row[14] < self._threshold:
            return None
        x, y, width, height = row[:4]
        x1 = float(np.clip(x, 0.0, image_width))
        y1 = float(np.clip(y, 0.0, image_height))
        x2 = float(np.clip(x + width, 0.0, image_width))
        y2 = float(np.clip(y + height, 0.0, image_height))
        if x2 <= x1 or y2 <= y1:
            return None
        landmarks = row[4:14].reshape(5, 2)
        clipped = tuple(
            (
                float(np.clip(x_coord, 0.0, image_width)),
                float(np.clip(y_coord, 0.0, image_height)),
            )
            for x_coord, y_coord in landmarks
        )
        return FaceDetection(
            BoundingBox(x1, y1, x2 - x1, y2 - y1),
            FaceLandmarks(*clipped),
            float(row[14]),
        )


class SFaceRecognizer:
    def __init__(self, model_path: Path, *, model: Any | None = None) -> None:
        self._model = (
            model if model is not None else cv2.FaceRecognizerSF.create(str(model_path), "")
        )

    def extract(self, bgr: NDArray[np.uint8], detection: FaceDetection) -> FaceEmbedding:
        box = detection.bounding_box
        landmarks = detection.landmarks
        row = np.asarray(
            [
                box.x,
                box.y,
                box.width,
                box.height,
                *landmarks.right_eye,
                *landmarks.left_eye,
                *landmarks.nose,
                *landmarks.right_mouth_corner,
                *landmarks.left_mouth_corner,
                detection.confidence,
            ],
            dtype=np.float32,
        ).reshape(1, 15)
        try:
            aligned = self._model.alignCrop(bgr, row)
        except Exception:
            raise FaceProcessingError("alignment_failed") from None
        try:
            feature = self._model.feature(aligned)
        except Exception:
            raise FaceProcessingError("embedding_failed") from None
        try:
            return FaceEmbedding(np.asarray(feature, dtype=np.float32).reshape(-1))
        except (TypeError, ValueError, OverflowError):
            raise FaceProcessingError("invalid_embedding") from None

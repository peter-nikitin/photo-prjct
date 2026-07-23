from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from face_spike.pipeline import (
    BoundingBox,
    FaceDetection,
    FaceEmbedding,
    FaceLandmarks,
    ModelProcessingError,
    _mark_primary,
)


class YuNetDetector:
    def __init__(
        self,
        model_path: Path,
        *,
        threshold: float,
        min_face_px: int,
        model: Any | None = None,
    ) -> None:
        if not np.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be finite and between zero and one")
        if min_face_px < 1:
            raise ValueError("min_face_px must be positive")
        self._threshold = threshold
        self._min_face_px = min_face_px
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
            raise ModelProcessingError("detection_failed") from None
        if rows is None:
            return ()

        detections = tuple(
            detection
            for row in np.asarray(rows)
            if (detection := self._normalize_row(row, width, height)) is not None
        )
        if not detections:
            return ()
        normalized, _ = _mark_primary(detections)
        return normalized

    def _normalize_row(
        self, raw_row: NDArray[np.generic], image_width: int, image_height: int
    ) -> FaceDetection | None:
        try:
            row = np.asarray(raw_row, dtype=np.float64).reshape(-1)
        except (TypeError, ValueError, OverflowError):
            return None
        if row.size != 15 or not np.isfinite(row).all():
            return None
        x, y, width, height = row[:4]
        x1 = float(np.clip(x, 0.0, image_width))
        y1 = float(np.clip(y, 0.0, image_height))
        x2 = float(np.clip(x + width, 0.0, image_width))
        y2 = float(np.clip(y + height, 0.0, image_height))
        box = BoundingBox(x1, y1, x2 - x1, y2 - y1)
        if (
            box.width < self._min_face_px
            or box.height < self._min_face_px
            or row[14] < self._threshold
        ):
            return None
        landmarks = row[4:14].reshape(5, 2)
        clipped = tuple(
            (float(np.clip(x_coord, 0.0, image_width)), float(np.clip(y_coord, 0.0, image_height)))
            for x_coord, y_coord in landmarks
        )
        return FaceDetection(
            bounding_box=box,
            landmarks=FaceLandmarks(*clipped),
            confidence=float(row[14]),
        )


class SFaceRecognizer:
    def __init__(self, model_path: Path, *, model: Any | None = None) -> None:
        self._model = (
            model if model is not None else cv2.FaceRecognizerSF.create(str(model_path), "")
        )

    def extract(self, bgr: NDArray[np.uint8], detection: FaceDetection) -> FaceEmbedding:
        row = detection.as_yunet_row()
        try:
            aligned = self._model.alignCrop(bgr, row)
        except Exception:
            raise ModelProcessingError("alignment_failed") from None
        try:
            feature = self._model.feature(aligned)
        except Exception:
            raise ModelProcessingError("embedding_failed") from None

        try:
            vector = np.asarray(feature, dtype=np.float32).reshape(-1)
        except (TypeError, ValueError, OverflowError):
            raise ModelProcessingError("invalid_embedding") from None
        if vector.size == 0 or not np.isfinite(vector).all():
            raise ModelProcessingError("invalid_embedding")
        vector64 = vector.astype(np.float64)
        norm = float(np.linalg.norm(vector64))
        if not np.isfinite(norm) or norm == 0.0:
            raise ModelProcessingError("invalid_embedding")
        return FaceEmbedding(np.asarray(vector64 / norm, dtype=np.float32))

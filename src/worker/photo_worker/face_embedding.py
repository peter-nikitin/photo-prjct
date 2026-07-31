from __future__ import annotations

import math
import os
from pathlib import Path
from time import monotonic
from typing import Any

from photo_worker.contracts import (
    MAX_FACE_EMBEDDING_DIMENSIONS,
    MAX_PIXELS_CAP,
    SELFIE_MAX_INPUT_BYTES,
    SELFIE_MAX_PIXELS,
    FaceEmbeddingFace,
    FaceEmbeddingResult,
    SelfieEmbeddingResult,
)


class FaceEmbeddingError(ValueError):
    """Domain error for face-embedding inference failures."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def extract_selfie_embedding(
    path: Path,
    *,
    max_bytes: int,
    content_type: str,
    max_pixels: int = SELFIE_MAX_PIXELS,
    detection_threshold: float = 0.75,
    minimum_face_px: int = 32,
    model: str = "sface",
    yunet_model_path: Path | None = None,
    sface_model_path: Path | None = None,
) -> SelfieEmbeddingResult:
    """Return one transient query embedding or a stable selfie-domain failure."""
    if (
        content_type not in {"image/jpeg", "image/png"}
        or not 0 < max_bytes <= SELFIE_MAX_INPUT_BYTES
        or not 0 < max_pixels <= SELFIE_MAX_PIXELS
        or minimum_face_px != 32
        or not 0.0 <= detection_threshold <= 1.0
        or model != "sface"
    ):
        raise FaceEmbeddingError("unsupported_input")

    np = _load_numpy()
    cv2 = _load_cv2()
    image: Any | None = None
    embedding: tuple[float, ...] | None = None
    started = monotonic()
    try:
        image = _decode_image(np, cv2, path, max_bytes=max_bytes, max_pixels=max_pixels)
        decode_ms = _elapsed_ms(started)
        width, height = image.shape[1], image.shape[0]
        model_started = monotonic()
        detector, recognizer = _load_models(
            cv2,
            width,
            height,
            _model_path(yunet_model_path, "PHOTO_WORKER_YUNET_MODEL_PATH"),
            _model_path(sface_model_path, "PHOTO_WORKER_SFACE_MODEL_PATH"),
            detection_threshold,
        )
        model_ms = _elapsed_ms(model_started)
        detect_started = monotonic()
        detections = _detect_faces(np, detector, image, width, height, detection_threshold)
        detect_ms = _elapsed_ms(detect_started)
        if not detections:
            raise FaceEmbeddingError("no_face_detected")
        if len(detections) != 1:
            raise FaceEmbeddingError("multiple_faces_detected")
        detection = detections[0]
        bbox = detection["bbox"]
        if min(float(bbox[2]), float(bbox[3])) < minimum_face_px:
            raise FaceEmbeddingError("quality_rejected")
        embed_started = monotonic()
        embedding = _extract_embedding(np, recognizer, image, detection)
        normalized = _normalized_selfie_vector(embedding)
        embed_ms = _elapsed_ms(embed_started)
        return SelfieEmbeddingResult(
            model=model,
            embedding=normalized,
            bbox=bbox,
            confidence=detection["confidence"],
            landmarks=detection["landmarks"],
            timings={
                "decode_ms": decode_ms,
                "model_load_ms": model_ms,
                "detect_ms": detect_ms,
                "embed_ms": embed_ms,
                "total_ms": decode_ms + model_ms + detect_ms + embed_ms,
            },
        )
    except FaceEmbeddingError:
        raise
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error
    finally:
        if embedding is not None:
            del embedding
        if image is not None:
            del image
        del np
        del cv2


def _normalized_selfie_vector(vector: tuple[float, ...]) -> tuple[float, ...]:
    if len(vector) != MAX_FACE_EMBEDDING_DIMENSIONS or not all(
        math.isfinite(value) for value in vector
    ):
        raise FaceEmbeddingError("quality_rejected")
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or norm <= 0.0:
        raise FaceEmbeddingError("quality_rejected")
    normalized = tuple(value / norm for value in vector)
    if not all(math.isfinite(value) for value in normalized):
        raise FaceEmbeddingError("quality_rejected")
    return normalized


def extract_face_embeddings(
    path: Path,
    *,
    max_bytes: int,
    max_pixels: int = MAX_PIXELS_CAP,
    max_faces: int = 1,
    detection_threshold: float = 0.75,
    model: str = "sface",
    yunet_model_path: Path | None = None,
    sface_model_path: Path | None = None,
) -> FaceEmbeddingResult:
    """Extract faces from a local JPEG file with YuNet + SFace."""
    if max_faces < 1:
        raise FaceEmbeddingError("unsupported_input")
    if max_bytes < 1:
        raise FaceEmbeddingError("input_too_large")
    if not (0.0 <= detection_threshold <= 1.0):
        raise FaceEmbeddingError("unsupported_input")
    if max_pixels <= 0 or max_pixels > MAX_PIXELS_CAP:
        raise FaceEmbeddingError("unsupported_input")

    np = _load_numpy()
    cv2 = _load_cv2()

    started = monotonic()
    try:
        image = _decode_image(np, cv2, path, max_bytes=max_bytes, max_pixels=max_pixels)
        decode_ms = _elapsed_ms(started)

        width, height = image.shape[1], image.shape[0]
        model_started = monotonic()
        yunet_model = _model_path(yunet_model_path, "PHOTO_WORKER_YUNET_MODEL_PATH")
        sface_model = _model_path(sface_model_path, "PHOTO_WORKER_SFACE_MODEL_PATH")
        detector, recognizer = _load_models(
            cv2, width, height, yunet_model, sface_model, detection_threshold
        )
        model_ms = _elapsed_ms(model_started)

        detect_started = monotonic()
        detections = _detect_faces(np, detector, image, width, height, detection_threshold)
        detect_ms = _elapsed_ms(detect_started)

        warnings: list[str] = []
        if not detections:
            warnings.append("no_faces_detected")
        if len(detections) > max_faces:
            warnings.append("faces_truncated")

        selected = detections[:max_faces]

        embed_started = monotonic()
        faces: list[FaceEmbeddingFace] = []
        for index, detection in enumerate(selected):
            try:
                embedding = _extract_embedding(np, recognizer, image, detection)
            except FaceEmbeddingError:
                warnings.append("face_embedding_failed")
                continue
            faces.append(
                FaceEmbeddingFace(
                    index=index,
                    bbox=detection["bbox"],
                    confidence=detection["confidence"],
                    landmarks=detection["landmarks"],
                    embedding=embedding,
                )
            )
            del embedding
        embed_ms = _elapsed_ms(embed_started)

        if not faces and detections:
            warnings.append("no_valid_faces")

        return FaceEmbeddingResult(
            model=model,
            faces=tuple(faces),
            has_single_query_face_usable=len(faces) == 1,
            warnings=_dedupe(warnings),
            timings={
                "decode_ms": decode_ms,
                "model_load_ms": model_ms,
                "detect_ms": detect_ms,
                "embed_ms": embed_ms,
                "total_ms": decode_ms + model_ms + detect_ms + embed_ms,
            },
        )
    except FaceEmbeddingError:
        raise
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error
    finally:
        del np
        del cv2
        try:
            del image
        except Exception:
            pass


def _decode_image(
    np: Any,
    cv2: Any,
    path: Path,
    *,
    max_bytes: int,
    max_pixels: int,
) -> Any:
    if max_pixels <= 0 or max_pixels > MAX_PIXELS_CAP:
        raise FaceEmbeddingError("unsupported_input")
    try:
        if path.stat().st_size > max_bytes:
            raise FaceEmbeddingError("input_too_large")
    except OSError as error:
        raise FaceEmbeddingError("decode_failed") from error

    try:
        raw = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    except OSError as error:
        raise FaceEmbeddingError("decode_failed") from error

    try:
        image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error

    if image is None:
        raise FaceEmbeddingError("decode_failed")

    try:
        height, width, channels = image.shape
    except Exception:
        raise FaceEmbeddingError("decode_failed") from None

    if not channels == 3 or height <= 0 or width <= 0:
        raise FaceEmbeddingError("decode_failed")
    if height * width > max_pixels:
        raise FaceEmbeddingError("input_too_large")
    return image


def _model_path(path: Path | None, env_var: str) -> Path:
    if path is not None:
        model = Path(path)
    else:
        candidate = os.environ.get(env_var)
        if not candidate:
            raise FaceEmbeddingError("model_inference_error")
        model = Path(candidate)

    if not model.is_file():
        raise FaceEmbeddingError("model_inference_error")

    return model


def _load_models(
    cv2: Any,
    width: int,
    height: int,
    yunet_model: Path,
    sface_model: Path,
    threshold: float,
) -> tuple[Any, Any]:
    try:
        detector = cv2.FaceDetectorYN.create(
            str(yunet_model),
            "",
            (width, height),
            threshold,
            0.3,
            5000,
        )
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error

    try:
        recognizer = cv2.FaceRecognizerSF.create(str(sface_model), "")
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error

    return detector, recognizer


def _detect_faces(
    np: Any,
    detector: Any,
    image: Any,
    width: int,
    height: int,
    threshold: float,
) -> list[dict[str, Any]]:
    try:
        detector.setInputSize((width, height))
        raw = detector.detect(image)
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error

    if raw is None:
        return []

    if isinstance(raw, tuple):
        if len(raw) == 2:
            raw = raw[1]
        else:
            raw = raw[0]

    if raw is None:
        return []

    try:
        rows = np.asarray(raw)
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error

    faces: list[dict[str, Any]] = []
    for row in rows:
        normalized = _normalize_detection(np, row, width, height, threshold)
        if normalized is not None:
            faces.append(normalized)

    faces.sort(key=lambda item: item["confidence"], reverse=True)
    return faces


def _normalize_detection(
    np: Any,
    row: Any,
    width: int,
    height: int,
    threshold: float,
) -> dict[str, Any] | None:
    try:
        values = np.asarray(row, dtype=np.float32).reshape(-1)
    except (TypeError, ValueError):
        return None

    if values.size != 15:
        return None

    if not np.isfinite(values).all():
        return None

    confidence = float(values[14])
    if not (0.0 <= confidence <= 1.0) or confidence < threshold:
        return None

    x, y, w, h = values[:4]
    if not all((w > 0.0, h > 0.0)):
        return None

    try:
        landmarks = values[4:14].reshape(5, 2)
    except Exception:
        return None

    return {
        "bbox": (
            max(0.0, float(x)),
            max(0.0, float(y)),
            max(0.0, min(float(w), float(width) - float(x))),
            max(0.0, min(float(h), float(height) - float(y))),
        ),
        "confidence": confidence,
        "landmarks": tuple(
            (
                max(0.0, min(float(point[0]), float(width))),
                max(0.0, min(float(point[1]), float(height))),
            )
            for point in landmarks
        ),
        "score": confidence,
    }


def _extract_embedding(
    np: Any, recognizer: Any, image: Any, detection: dict[str, Any]
) -> tuple[float, ...]:
    aligned_input = np.asarray(
        [
            *detection["bbox"],
            *[coord for point in detection["landmarks"] for coord in point],
            detection["confidence"],
        ],
        dtype=np.float32,
    ).reshape(1, 15)

    try:
        aligned = recognizer.alignCrop(image, aligned_input)
        vector = recognizer.feature(aligned)
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error

    try:
        values = np.asarray(vector, dtype=np.float32).reshape(-1)
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error

    if values.size == 0:
        raise FaceEmbeddingError("model_inference_error")
    if not np.isfinite(values).all():
        raise FaceEmbeddingError("model_inference_error")
    if values.ndim != 1:
        values = values.ravel()

    try:
        norm = float(np.linalg.norm(values))
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error

    if norm <= 0.0 or not np.isfinite(norm):
        raise FaceEmbeddingError("model_inference_error")

    normalized = values / norm
    if normalized.size < MAX_FACE_EMBEDDING_DIMENSIONS:
        raise FaceEmbeddingError("model_inference_error")

    normalized = normalized[:MAX_FACE_EMBEDDING_DIMENSIONS]
    payload = tuple(float(value) for value in normalized)
    try:
        del values
        del normalized
    except Exception:
        pass
    return payload


def _load_numpy() -> Any:
    try:
        import numpy as np
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error
    return np


def _load_cv2() -> Any:
    try:
        import cv2
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error
    return cv2


def _dedupe(values: list[str]) -> tuple[str, ...]:
    if not values:
        return ()
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return tuple(ordered)


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))

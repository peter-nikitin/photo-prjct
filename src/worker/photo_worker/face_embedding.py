from __future__ import annotations

import math
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from time import monotonic
from typing import Any, cast

from PIL import Image, ImageCms, ImageOps

from photo_worker.adaface import (
    ADAFACE_EMBEDDING_DIMENSIONS,
    ADAFACE_MODEL_NAME,
    AdaFaceError,
    load_adaface_runtime,
)
from photo_worker.contracts import (
    MAX_PIXELS_CAP,
    SELFIE_MAX_INPUT_BYTES,
    SELFIE_MAX_PIXELS,
    SFACE_EMBEDDING_DIMENSIONS,
    FaceEmbeddingFace,
    FaceEmbeddingResult,
    SelfieEmbeddingResult,
)
from photo_worker.face_quality import (
    FaceQualityError,
    FaceQualityThresholds,
    evaluate_face_quality,
)
from photo_worker.scrfd import SCRFDDetector


class FaceEmbeddingError(ValueError):
    """Domain error for face-embedding inference failures."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class _ModelRuntime:
    detector: Any
    recognizer: Any


_MODEL_RUNTIMES: dict[tuple[Path, Path, str], _ModelRuntime] = {}


def extract_selfie_embedding(
    path: Path,
    *,
    max_bytes: int,
    content_type: str,
    max_pixels: int = SELFIE_MAX_PIXELS,
    detection_threshold: float = 0.5,
    minimum_face_px: int = 32,
    model: str = "sface",
    scrfd_model_path: Path | None = None,
    sface_model_path: Path | None = None,
    adaface_model_path: Path | None = None,
) -> SelfieEmbeddingResult:
    """Return one transient query embedding or a stable selfie-domain failure."""
    if (
        content_type not in {"image/jpeg", "image/png"}
        or not 0 < max_bytes <= SELFIE_MAX_INPUT_BYTES
        or not 0 < max_pixels <= SELFIE_MAX_PIXELS
        or minimum_face_px != 32
        or not 0.0 <= detection_threshold <= 1.0
        or model not in {"sface", ADAFACE_MODEL_NAME}
    ):
        raise FaceEmbeddingError("unsupported_input")

    np = _load_numpy()
    cv2 = _load_cv2()
    image: Any | None = None
    embedding: tuple[float, ...] | None = None
    started = monotonic()
    try:
        image = _decode_selfie_image(np, cv2, path, max_bytes=max_bytes, max_pixels=max_pixels)
        decode_ms = _elapsed_ms(started)
        model_started = monotonic()
        runtime = _runtime_for_model(
            cv2,
            model=model,
            scrfd_model_path=scrfd_model_path,
            sface_model_path=sface_model_path,
            adaface_model_path=adaface_model_path,
        )
        model_ms = _elapsed_ms(model_started)
        detect_started = monotonic()
        detections = _detect_faces(runtime.detector, image, detection_threshold)
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
        embedding = _extract_embedding(np, runtime.recognizer, image, detection, model=model)
        normalized = _normalized_selfie_vector(embedding, dimensions=_embedding_dimensions(model))
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


def _normalized_selfie_vector(vector: tuple[float, ...], *, dimensions: int) -> tuple[float, ...]:
    if len(vector) != dimensions or not all(math.isfinite(value) for value in vector):
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
    detection_threshold: float = 0.5,
    model: str = "sface",
    scrfd_model_path: Path | None = None,
    sface_model_path: Path | None = None,
    adaface_model_path: Path | None = None,
    quality_thresholds: FaceQualityThresholds | None = None,
) -> FaceEmbeddingResult:
    """Extract faces with SCRFD and the explicitly selected pinned recognizer."""
    if max_faces < 1:
        raise FaceEmbeddingError("unsupported_input")
    if max_bytes < 1:
        raise FaceEmbeddingError("input_too_large")
    if not (0.0 <= detection_threshold <= 1.0):
        raise FaceEmbeddingError("unsupported_input")
    if max_pixels <= 0 or max_pixels > MAX_PIXELS_CAP:
        raise FaceEmbeddingError("unsupported_input")
    if model not in {"sface", ADAFACE_MODEL_NAME}:
        raise FaceEmbeddingError("unsupported_input")

    np = _load_numpy()
    cv2 = _load_cv2()

    started = monotonic()
    try:
        image = _decode_image(np, cv2, path, max_bytes=max_bytes, max_pixels=max_pixels)
        decode_ms = _elapsed_ms(started)

        model_started = monotonic()
        runtime = _runtime_for_model(
            cv2,
            model=model,
            scrfd_model_path=scrfd_model_path,
            sface_model_path=sface_model_path,
            adaface_model_path=adaface_model_path,
        )
        model_ms = _elapsed_ms(model_started)

        detect_started = monotonic()
        detections = _detect_faces(runtime.detector, image, detection_threshold)
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
            quality = None
            if quality_thresholds is not None:
                try:
                    quality = evaluate_face_quality(
                        image,
                        bbox=detection["bbox"],
                        confidence=detection["confidence"],
                        thresholds=quality_thresholds,
                    )
                except FaceQualityError:
                    warnings.append("face_quality_failed")
                    faces.append(
                        FaceEmbeddingFace(
                            index=index,
                            bbox=detection["bbox"],
                            confidence=detection["confidence"],
                            landmarks=detection["landmarks"],
                            embedding=None,
                            status="technical_failed",
                            error_code="invalid_face_quality",
                        )
                    )
                    continue
                if quality.decision == "quality_rejected":
                    faces.append(
                        FaceEmbeddingFace(
                            index=index,
                            bbox=detection["bbox"],
                            confidence=detection["confidence"],
                            landmarks=detection["landmarks"],
                            embedding=None,
                            status="quality_rejected",
                            quality=quality,
                        )
                    )
                    continue
            try:
                embedding = _extract_embedding(
                    np, runtime.recognizer, image, detection, model=model
                )
            except FaceEmbeddingError as error:
                warnings.append("face_embedding_failed")
                if quality is not None:
                    faces.append(
                        FaceEmbeddingFace(
                            index=index,
                            bbox=detection["bbox"],
                            confidence=detection["confidence"],
                            landmarks=detection["landmarks"],
                            embedding=None,
                            status="technical_failed",
                            quality=quality,
                            error_code=error.code,
                        )
                    )
                continue
            faces.append(
                FaceEmbeddingFace(
                    index=index,
                    bbox=detection["bbox"],
                    confidence=detection["confidence"],
                    landmarks=detection["landmarks"],
                    embedding=embedding,
                    quality=quality,
                )
            )
            del embedding
        embed_ms = _elapsed_ms(embed_started)

        kept_faces = tuple(face for face in faces if face.embedding is not None)
        if not kept_faces and detections:
            warnings.append("no_valid_faces")

        return FaceEmbeddingResult(
            model=model,
            faces=tuple(faces),
            has_single_query_face_usable=len(kept_faces) == 1,
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


def _decode_selfie_image(
    np: Any,
    cv2: Any,
    path: Path,
    *,
    max_bytes: int,
    max_pixels: int,
) -> Any:
    if max_pixels <= 0 or max_pixels > SELFIE_MAX_PIXELS:
        raise FaceEmbeddingError("unsupported_input")
    try:
        if path.stat().st_size > max_bytes:
            raise FaceEmbeddingError("input_too_large")
        with Image.open(path) as opened:
            width, height = opened.size
            if width <= 0 or height <= 0:
                raise FaceEmbeddingError("decode_failed")
            if width * height > max_pixels:
                raise FaceEmbeddingError("input_too_large")
            oriented = ImageOps.exif_transpose(opened)
            try:
                profile_bytes = oriented.info.get("icc_profile")
                if profile_bytes:
                    normalized = cast(
                        Image.Image,
                        ImageCms.profileToProfile(
                            oriented,
                            ImageCms.ImageCmsProfile(BytesIO(profile_bytes)),
                            ImageCms.createProfile("sRGB"),
                            outputMode="RGB",
                        ),
                    )
                else:
                    normalized = oriented.convert("RGB")
                try:
                    long_edge = max(normalized.size)
                    if long_edge > 1600:
                        scale = 1600 / long_edge
                        resized = normalized.resize(
                            (
                                max(1, round(normalized.width * scale)),
                                max(1, round(normalized.height * scale)),
                            ),
                            Image.Resampling.LANCZOS,
                        )
                        normalized.close()
                        normalized = resized
                    rgb = np.asarray(normalized, dtype=np.uint8)
                finally:
                    normalized.close()
            finally:
                oriented.close()
    except FaceEmbeddingError:
        raise
    except OSError as error:
        raise FaceEmbeddingError("decode_failed") from error
    except Exception as error:
        raise FaceEmbeddingError("decode_failed") from error

    try:
        image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise FaceEmbeddingError("decode_failed")
    return image


def _model_path(path: Path | None, env_var: str, *, directory: bool = False) -> Path:
    if path is not None:
        model = Path(path)
    else:
        candidate = os.environ.get(env_var)
        if not candidate:
            raise FaceEmbeddingError("model_inference_error")
        model = Path(candidate)

    if not (model.is_dir() if directory else model.is_file()):
        raise FaceEmbeddingError("model_inference_error")

    return model.resolve()


def _runtime_for_model(
    cv2: Any,
    *,
    model: str,
    scrfd_model_path: Path | None,
    sface_model_path: Path | None,
    adaface_model_path: Path | None,
) -> _ModelRuntime:
    scrfd_model = _model_path(scrfd_model_path, "PHOTO_WORKER_SCRFD_MODEL_PATH")
    if model == ADAFACE_MODEL_NAME:
        recognizer_model = _model_path(
            adaface_model_path,
            "PHOTO_WORKER_ADAFACE_MODEL_PATH",
            directory=True,
        )
    else:
        recognizer_model = _model_path(sface_model_path, "PHOTO_WORKER_SFACE_MODEL_PATH")
    return _get_model_runtime(cv2, scrfd_model, recognizer_model, model)


def _get_model_runtime(
    cv2: Any,
    scrfd_model: Path,
    recognizer_model: Path,
    model: str,
) -> _ModelRuntime:
    key = (scrfd_model.resolve(), recognizer_model.resolve(), model)
    runtime = _MODEL_RUNTIMES.get(key)
    if runtime is None:
        detector, recognizer = _load_models(cv2, scrfd_model, recognizer_model, model)
        runtime = _ModelRuntime(detector=detector, recognizer=recognizer)
        _MODEL_RUNTIMES[key] = runtime
    return runtime


def _load_models(
    cv2: Any,
    scrfd_model: Path,
    recognizer_model: Path,
    model: str,
) -> tuple[Any, Any]:
    try:
        detector = SCRFDDetector(scrfd_model)
        recognizer = (
            load_adaface_runtime(recognizer_model)
            if model == ADAFACE_MODEL_NAME
            else cv2.FaceRecognizerSF.create(str(recognizer_model), "")
        )
    except AdaFaceError as error:
        raise FaceEmbeddingError("model_inference_error") from error
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error

    return detector, recognizer


def _detect_faces(
    detector: Any,
    image: Any,
    threshold: float,
) -> list[dict[str, Any]]:
    try:
        raw = detector.detect(image, threshold=threshold)
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error
    faces = [
        {
            "bbox": (
                face.bbox[0],
                face.bbox[1],
                face.bbox[2] - face.bbox[0],
                face.bbox[3] - face.bbox[1],
            ),
            "confidence": face.confidence,
            "landmarks": face.landmarks,
            "score": face.confidence,
        }
        for face in raw
    ]
    return sorted(faces, key=lambda item: item["confidence"], reverse=True)


def _extract_embedding(
    np: Any,
    recognizer: Any,
    image: Any,
    detection: dict[str, Any],
    *,
    model: str,
) -> tuple[float, ...]:
    if model == ADAFACE_MODEL_NAME:
        try:
            return recognizer.extract(np, _load_cv2(), image, detection["landmarks"])
        except AdaFaceError as error:
            raise FaceEmbeddingError("model_inference_error") from error

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
        norm = float(np.linalg.norm(values))
    except Exception as error:
        raise FaceEmbeddingError("model_inference_error") from error
    if values.size < SFACE_EMBEDDING_DIMENSIONS or not np.isfinite(values).all():
        raise FaceEmbeddingError("model_inference_error")
    if norm <= 0.0 or not np.isfinite(norm):
        raise FaceEmbeddingError("model_inference_error")
    normalized = values / norm
    return tuple(float(value) for value in normalized[:SFACE_EMBEDDING_DIMENSIONS])


def _embedding_dimensions(model: str) -> int:
    return (
        ADAFACE_EMBEDDING_DIMENSIONS if model == ADAFACE_MODEL_NAME else SFACE_EMBEDDING_DIMENSIONS
    )


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

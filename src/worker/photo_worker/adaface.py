"""Pinned local AdaFace IR-18 inference adapter."""

from __future__ import annotations

import hashlib
import importlib.util
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

ADAFACE_MODEL_NAME = "adaface-ir18-webface4m"
ADAFACE_EMBEDDING_DIMENSIONS = 512
ADAFACE_INPUT_SIZE = 112
ADAFACE_MODEL_FILENAME = "model.safetensors"
ADAFACE_MODEL_SHA256 = "3a416518b11ece107b43385fc3678aad1d4f2405fde9f58f0be7f530230e368b"
ADAFACE_MODEL_SOURCE = Path("models/iresnet/model.py")
ADAFACE_MODEL_SOURCE_SHA256 = "e31be55c60b538c15151887e911b7535e2f0e1114a13427ba06483e1cd2a63f9"

_ADAFACE_REFERENCE_LANDMARKS = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)


class AdaFaceError(ValueError):
    """The pinned AdaFace model or an inference result is invalid."""


@dataclass(frozen=True)
class AdaFaceRuntime:
    """One reusable CPU runtime for the pinned AdaFace model."""

    model: Any
    torch: Any

    def extract(
        self,
        np: Any,
        cv2: Any,
        image: Any,
        landmarks: tuple[tuple[float, float], ...],
    ) -> tuple[float, ...]:
        aligned = align_face(np, cv2, image, landmarks)
        prepared = prepare_input(np, aligned)
        try:
            tensor = self.torch.from_numpy(prepared)
            with self.torch.inference_mode():
                output = self.model(tensor)
            values = output.detach().cpu().numpy()
        except Exception as error:
            raise AdaFaceError("adaface_inference_failed") from error
        return normalize_embedding(np, values)


def align_face(
    np: Any,
    cv2: Any,
    image: Any,
    landmarks: tuple[tuple[float, float], ...],
) -> Any:
    """Align YuNet's five landmarks to the canonical AdaFace crop."""
    try:
        source = np.asarray(landmarks, dtype=np.float32)
        target = np.asarray(_ADAFACE_REFERENCE_LANDMARKS, dtype=np.float32)
    except Exception as error:
        raise AdaFaceError("adaface_alignment_failed") from error
    if source.shape != (5, 2) or not np.isfinite(source).all():
        raise AdaFaceError("adaface_alignment_failed")
    try:
        transform, _inliers = cv2.estimateAffinePartial2D(source, target, method=cv2.LMEDS)
        if transform is None or not np.isfinite(transform).all():
            raise AdaFaceError("adaface_alignment_failed")
        aligned = cv2.warpAffine(
            image,
            transform,
            (ADAFACE_INPUT_SIZE, ADAFACE_INPUT_SIZE),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )
    except AdaFaceError:
        raise
    except Exception as error:
        raise AdaFaceError("adaface_alignment_failed") from error
    if getattr(aligned, "shape", None) != (ADAFACE_INPUT_SIZE, ADAFACE_INPUT_SIZE, 3):
        raise AdaFaceError("adaface_alignment_failed")
    return aligned


def prepare_input(np: Any, aligned_bgr: Any) -> Any:
    """Convert an aligned BGR uint8 crop to normalized NCHW RGB float32."""
    try:
        values = np.asarray(aligned_bgr)
    except Exception as error:
        raise AdaFaceError("adaface_preprocessing_failed") from error
    if values.shape != (ADAFACE_INPUT_SIZE, ADAFACE_INPUT_SIZE, 3):
        raise AdaFaceError("adaface_preprocessing_failed")
    try:
        rgb = values[:, :, ::-1].astype(np.float32) / 255.0
        normalized = (rgb - 0.5) / 0.5
        prepared = np.ascontiguousarray(normalized.transpose(2, 0, 1)[None, ...])
    except Exception as error:
        raise AdaFaceError("adaface_preprocessing_failed") from error
    if prepared.shape != (1, 3, ADAFACE_INPUT_SIZE, ADAFACE_INPUT_SIZE):
        raise AdaFaceError("adaface_preprocessing_failed")
    return prepared


def normalize_embedding(np: Any, vector: Any) -> tuple[float, ...]:
    """Validate and L2-normalize one AdaFace embedding."""
    try:
        values = np.asarray(vector, dtype=np.float32).reshape(-1)
    except Exception as error:
        raise AdaFaceError("adaface_invalid_embedding") from error
    if values.size != ADAFACE_EMBEDDING_DIMENSIONS or not np.isfinite(values).all():
        raise AdaFaceError("adaface_invalid_embedding")
    try:
        norm = float(np.linalg.norm(values))
    except Exception as error:
        raise AdaFaceError("adaface_invalid_embedding") from error
    if norm <= 0.0 or not np.isfinite(norm):
        raise AdaFaceError("adaface_invalid_embedding")
    normalized = values / norm
    if not np.isfinite(normalized).all():
        raise AdaFaceError("adaface_invalid_embedding")
    return tuple(float(value) for value in normalized)


def verify_file_digest(path: Path, expected_sha256: str) -> None:
    """Require an exact SHA-256 before importing or deserializing an artifact."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise AdaFaceError("adaface_model_load_failed") from error
    if digest.hexdigest() != expected_sha256:
        raise AdaFaceError("adaface_model_load_failed")


def load_adaface_runtime(model_directory: Path) -> AdaFaceRuntime:
    """Load the pinned safetensors artifact with pinned local architecture code only."""
    model_directory = model_directory.resolve()
    artifact = model_directory / ADAFACE_MODEL_FILENAME
    source = model_directory / ADAFACE_MODEL_SOURCE
    if not model_directory.is_dir() or not artifact.is_file() or not source.is_file():
        raise AdaFaceError("adaface_model_load_failed")
    verify_file_digest(artifact, ADAFACE_MODEL_SHA256)
    verify_file_digest(source, ADAFACE_MODEL_SOURCE_SHA256)

    try:
        import safetensors.torch
        import torch

        module = _load_model_module(source)
        model = module.IR_18(
            input_size=(ADAFACE_INPUT_SIZE, ADAFACE_INPUT_SIZE),
            output_dim=ADAFACE_EMBEDDING_DIMENSIONS,
        )
        state = safetensors.torch.load_file(str(artifact), device="cpu")
        prefix = "model.net."
        if not state or not all(key.startswith(prefix) for key in state):
            raise AdaFaceError("adaface_model_load_failed")
        model.load_state_dict({key.removeprefix(prefix): value for key, value in state.items()})
        model.eval()
    except AdaFaceError:
        raise
    except Exception as error:
        raise AdaFaceError("adaface_model_load_failed") from error
    return AdaFaceRuntime(model=model, torch=torch)


def _load_model_module(source: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("_photo_worker_pinned_adaface_ir18", source)
    if spec is None or spec.loader is None:
        raise AdaFaceError("adaface_model_load_failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

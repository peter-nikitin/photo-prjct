"""Build-time smoke for the SCRFD/SFace models shipped with the worker image."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from photo_worker.contracts import MAX_FACE_EMBEDDING_DIMENSIONS
from photo_worker.face_embedding import (
    FaceEmbeddingError,
    extract_face_embeddings,
    extract_selfie_embedding,
)


def main() -> None:
    """Load both model consumers with a no-face synthetic JPEG before publishing an image."""
    import cv2
    import numpy as np

    image = np.full((320, 320, 3), 127, dtype=np.uint8)
    encoded, jpeg = cv2.imencode(".jpg", image)
    if not encoded:
        raise RuntimeError("face_model_smoke_jpeg_encode_failed")

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "no-face.jpg"
        path.write_bytes(jpeg.tobytes())
        _assert_photo_embedding_no_face(path)
        _assert_selfie_query_no_face(path)
        decoded = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)

    if decoded is None:
        raise RuntimeError("face_model_smoke_decode_failed")
    _exercise_sface_feature(cv2, np, decoded)
    print("face-model-smoke-ok")


def _assert_photo_embedding_no_face(path: Path) -> None:
    result = extract_face_embeddings(path, max_bytes=path.stat().st_size)
    if result.model != "sface" or result.faces != () or result.warnings != ("no_faces_detected",):
        raise RuntimeError("face_model_smoke_unexpected_photo_result")


def _assert_selfie_query_no_face(path: Path) -> None:
    try:
        extract_selfie_embedding(
            path,
            max_bytes=path.stat().st_size,
            content_type="image/jpeg",
        )
    except FaceEmbeddingError as error:
        if error.code == "no_face_detected":
            return
        raise RuntimeError("face_model_smoke_unexpected_selfie_error") from error
    raise RuntimeError("face_model_smoke_expected_no_face")


def _exercise_sface_feature(cv2: Any, np: Any, image: Any) -> None:
    model_path = Path(os.environ["PHOTO_WORKER_SFACE_MODEL_PATH"])
    recognizer = cv2.FaceRecognizerSF.create(str(model_path), "")
    face = np.asarray(
        [64, 48, 192, 224, 120, 128, 200, 128, 160, 168, 128, 224, 192, 224, 1.0],
        dtype=np.float32,
    ).reshape(1, 15)
    aligned = recognizer.alignCrop(image, face)
    feature = recognizer.feature(aligned)
    if np.asarray(feature).reshape(-1).size != MAX_FACE_EMBEDDING_DIMENSIONS:
        raise RuntimeError("face_model_smoke_invalid_feature")


if __name__ == "__main__":
    main()

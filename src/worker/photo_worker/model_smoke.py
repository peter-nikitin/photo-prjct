"""Build-time inference smoke for the YuNet/SFace models shipped with the worker."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from photo_worker.face_embedding import extract_face_embeddings


def main() -> None:
    import cv2
    import numpy as np

    image = np.full((320, 320, 3), 127, dtype=np.uint8)
    encoded, jpeg = cv2.imencode(".jpg", image)
    if not encoded:
        raise RuntimeError("face_model_smoke_jpeg_encode_failed")

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "representative.jpg"
        path.write_bytes(jpeg.tobytes())
        result = extract_face_embeddings(path, max_bytes=path.stat().st_size)
        decoded = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)

    if result.model != "sface" or decoded is None:
        raise RuntimeError("face_model_smoke_invalid_result")
    _exercise_sface_feature(cv2, np, decoded)
    print("face-model-smoke-ok")


def _exercise_sface_feature(cv2: Any, np: Any, image: Any) -> None:
    model_path = Path(os.environ["PHOTO_WORKER_SFACE_MODEL_PATH"])
    recognizer = cv2.FaceRecognizerSF.create(str(model_path), "")
    face = np.asarray(
        [64, 48, 192, 224, 120, 128, 200, 128, 160, 168, 128, 224, 192, 224, 1.0],
        dtype=np.float32,
    ).reshape(1, 15)
    aligned = recognizer.alignCrop(image, face)
    feature = recognizer.feature(aligned)
    if np.asarray(feature).reshape(-1).size != 128:
        raise RuntimeError("face_model_smoke_invalid_feature")


if __name__ == "__main__":
    main()

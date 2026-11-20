"""CPU appearance descriptors for one clean preview."""

import hashlib
import importlib
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import cast

import cv2
import numpy as np
from PIL import Image

from photo_worker.contracts import APPEARANCE_PROFILE, PersonAppearanceError
from photo_worker.runtime_threads import CPU_THREADS
from photo_worker.scrfd import SCRFDDetector

_PROFILE = APPEARANCE_PROFILE
MODEL_HASHES = {
    "object_detection_yolox_2022nov.onnx": _PROFILE["detector_sha256"],
    "person-reidentification-retail-0286.xml": _PROFILE["encoder_xml_sha256"],
    "person-reidentification-retail-0286.bin": _PROFILE["encoder_sha256"],
}
MAX_DETECTIONS = _PROFILE["max_detections"]
MAX_DESCRIPTORS = _PROFILE["max_descriptors"]
DETECTOR_SIZE = 640
MIN_CONFIDENCE = 0.35
NMS_THRESHOLD = 0.50


def associate_heads(
    bodies: list[tuple[int, int, int, int]],
    faces: list[tuple[float, float, float, float]],
) -> dict[int, int]:
    owners = {}
    ambiguous = set()
    for face_index, (x, y, r, b) in enumerate(faces):
        matches = []
        for body_index, (left, top, right, bottom) in enumerate(bodies):
            width = max(0, min(r, right) - max(x, left))
            height = max(0, min(b, bottom) - max(y, top))
            if (
                left <= (x + r) / 2 <= right
                and top <= (y + b) / 2 <= (top + bottom) / 2
                and width * height >= 0.85 * (r - x) * (b - y)
            ):
                matches.append(body_index)
        if len(matches) == 1:
            body_index = matches[0]
            if body_index in owners:
                ambiguous.add(body_index)
            owners[body_index] = face_index
        elif matches:
            ambiguous.update(matches)
    return {body: face for body, face in owners.items() if body not in ambiguous}


def focus_quality(crop: Image.Image) -> tuple[str, tuple[float, float, float, float]]:
    width, height = crop.size
    scale = min(1.0, _PROFILE["focus_target_height"] / height)
    resized = crop.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS
    )
    gray = np.asarray(resized.convert("L"), dtype=np.float32)
    rh, rw = gray.shape

    def measure(region: np.ndarray) -> tuple[float, float]:
        if min(region.shape) < 3:
            return 0.0, 0.0
        laplacian = cv2.Laplacian(region, cv2.CV_32F)[1:-1, 1:-1]
        return round(float(np.mean(laplacian**2)), 6), round(float(region.std()), 6)

    center, center_std = measure(
        gray[round(rh / 6) : round(5 * rh / 6), round(0.2 * rw) : round(0.8 * rw)]
    )
    torso, torso_std = measure(
        gray[round(0.2 * rh) : round(0.55 * rh), round(0.25 * rw) : round(0.75 * rw)]
    )
    if (
        height < _PROFILE["focus_min_height"]
        or width < _PROFILE["focus_min_width"]
        or max(center_std, torso_std) < _PROFILE["focus_min_luminance_std"]
    ):
        reason = "lack_details"
    elif max(center, torso) < _PROFILE["focus_min_laplacian_energy"]:
        reason = "strong_blur"
    elif min(center, torso) < _PROFILE["focus_min_laplacian_energy"]:
        reason = "mixed_sharpness"
    else:
        reason = ""
    return reason, (center, torso, center_std, torso_std)


def _unit(vector: np.ndarray, dimensions: int) -> list[float]:
    norm = float(np.linalg.norm(vector))
    if vector.size != dimensions or not np.isfinite(norm) or norm <= 0:
        raise ValueError("invalid appearance descriptor")
    return [round(float(value), 6) for value in vector / norm]


def _colors(crop: Image.Image) -> list[float]:
    rgb = np.asarray(crop.resize((96, 192), Image.Resampling.BILINEAR))
    parts = []
    for region in (rgb[:43, 24:72], rgb[48:116, 19:77]):
        hsv = cv2.cvtColor(region, cv2.COLOR_RGB2HSV)
        hsv[:, :, 0][hsv[:, :, 1] < 32] = 0
        histogram = cv2.calcHist(
            [hsv], [0, 1, 2], None, [12, 4, 4], [0, 180, 0, 256, 0, 256]
        ).ravel()
        parts.append(np.sqrt(histogram / float(histogram.sum())))
    return _unit(np.concatenate(parts), 384)


def _helmet(
    image: Image.Image, body: tuple[int, int, int, int], face: tuple[float, float, float, float]
) -> list[float] | None:
    x, y, right, bottom = face
    w, h = right - x, bottom - y
    left, top, body_right, body_bottom = body
    body_width, body_height = body_right - left, body_bottom - top
    width = max(1.8 * w, 1.4 * h)
    center = x + w / 2
    raw = (
        max(left - 0.02 * body_width, center - width / 2),
        top - min(5, max(2, 0.01 * body_height)),
        min(body_right + 0.02 * body_width, center + width / 2),
        max(y + 0.25 * h, top + 0.1 * body_height),
    )
    box = (
        max(0, math.floor(raw[0])),
        max(0, math.floor(raw[1])),
        min(image.width, math.ceil(raw[2])),
        min(image.height, math.ceil(raw[3])),
    )
    bw, bh = box[2] - box[0], box[3] - box[1]
    area = max(1, (raw[2] - raw[0]) * (raw[3] - raw[1]))
    if min(bw, bh) < 20 or bw * bh / area < 0.8 or (top <= 1 and y - 1.05 * h < 0):
        return None
    gray = cv2.cvtColor(
        np.asarray(image.crop(box).resize((64, 64), Image.Resampling.BICUBIC)), cv2.COLOR_RGB2GRAY
    )
    vector = cv2.HOGDescriptor((64, 64), (16, 16), (8, 8), (8, 8), 9).compute(gray)
    descriptor = np.asarray(vector).ravel()
    return _unit(descriptor, 1764) if float(np.linalg.norm(vector)) > 0 else None


@lru_cache(maxsize=1)
def _models():
    root = Path(os.environ.get("PHOTO_WORKER_APPEARANCE_MODEL_DIR", "/worker/models/appearance"))
    for name, expected in MODEL_HASHES.items():
        with (root / name).open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != expected:
                raise ValueError("invalid appearance model artifact")
    ov = importlib.import_module("openvino")
    detector = cv2.dnn.readNet(str(root / "object_detection_yolox_2022nov.onnx"))
    encoder = ov.Core().compile_model(
        str(root / "person-reidentification-retail-0286.xml"),
        "CPU",
        {"INFERENCE_NUM_THREADS": CPU_THREADS},
    )
    heads_path = Path(os.environ["PHOTO_WORKER_SCRFD_MODEL_PATH"])
    with heads_path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != _PROFILE["head_detector_sha256"]:
            raise ValueError("invalid appearance head detector artifact")
    heads = SCRFDDetector(heads_path)
    return detector, encoder, heads


def _detect(
    image: Image.Image, model: cv2.dnn.Net
) -> list[tuple[tuple[int, int, int, int], float]]:
    width, height = image.size
    scale = DETECTOR_SIZE / max(width, height)
    resized_width, resized_height = max(1, int(width * scale)), max(1, int(height * scale))
    padded = np.full((DETECTOR_SIZE, DETECTOR_SIZE, 3), 114, dtype=np.float32)
    padded[:resized_height, :resized_width] = cv2.resize(
        np.asarray(image), (resized_width, resized_height), interpolation=cv2.INTER_LINEAR
    )
    model.setInput(cv2.dnn.blobFromImage(padded))
    predictions = model.forward()[0]

    # OpenCV Zoo YOLOX-S exports offsets on three grids. Class 0 is person.
    boxes_xywh: list[list[float]] = []
    scores = []
    offset = 0
    for stride in (8, 16, 32):
        grid_size = DETECTOR_SIZE // stride
        rows = predictions[offset : offset + grid_size**2]
        offset += len(rows)
        person_scores = rows[:, 4] * rows[:, 5]
        indices = np.flatnonzero(
            (person_scores >= MIN_CONFIDENCE) & (np.argmax(rows[:, 5:], axis=1) == 0)
        )
        grid = np.column_stack((indices % grid_size, indices // grid_size))
        centers = (rows[indices, :2] + grid) * stride
        sizes = np.exp(rows[indices, 2:4]) * stride
        boxes_xywh.extend(
            cast(list[list[float]], np.column_stack((centers - sizes / 2, sizes)).tolist())
        )
        scores.extend(person_scores[indices].tolist())

    people = []
    for index in cv2.dnn.NMSBoxes(boxes_xywh, scores, MIN_CONFIDENCE, NMS_THRESHOLD):
        x, y, box_width, box_height = boxes_xywh[index]
        x1, x2 = np.clip([x / scale, (x + box_width) / scale], 0, width)
        y1, y2 = np.clip([y / scale, (y + box_height) / scale], 0, height)
        box = (
            int(np.floor(x1)),
            int(np.floor(y1)),
            int(np.ceil(x2)),
            int(np.ceil(y2)),
        )
        if box[0] < box[2] and box[1] < box[3]:
            people.append((box, scores[index]))
    return people


def extract_person_appearance(
    path: Path, *, max_pixels: int, sha256: str, size: tuple[int | None, int | None]
) -> dict[str, object]:
    with path.open("rb") as handle:
        if hashlib.file_digest(handle, "sha256").hexdigest() != sha256:
            raise PersonAppearanceError("fingerprint_mismatch")
    try:
        with Image.open(path) as source:
            if source.format != "JPEG" or source.width * source.height > max_pixels:
                raise PersonAppearanceError("unsupported_input")
            if source.size != size:
                raise PersonAppearanceError("fingerprint_mismatch")
            image = source.convert("RGB")
    except OSError as error:
        raise PersonAppearanceError("decode_failed") from error
    try:
        return _extract(image)
    except Exception as error:
        raise PersonAppearanceError("model_inference_error") from error


def _encode(encoder, crop: Image.Image) -> list[float]:
    rgb = np.asarray(crop.resize((128, 256), Image.Resampling.BILINEAR))
    tensor = np.ascontiguousarray(rgb[:, :, ::-1].transpose(2, 0, 1)[None], dtype=np.float32)
    return _unit(encoder(tensor)[0].ravel(), 256)


def _extract(image: Image.Image) -> dict[str, object]:
    detector, encoder, heads = _models()
    people = sorted(_detect(image, detector), key=lambda item: (-item[1], item[0]))
    faces = heads.detect(
        cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR),
        threshold=_PROFILE["head_detection_threshold"],
    )
    face_boxes = [face.bbox for face in faces]
    associations = associate_heads([bbox for bbox, _ in people], face_boxes)
    rows = []
    count = 0
    for index, (bbox, score) in enumerate(people[:MAX_DETECTIONS]):
        crop = image.crop(bbox)
        reason, focus = focus_quality(crop)
        face = face_boxes[associations[index]] if index in associations else None
        row: dict[str, object] = {
            "bbox": list(bbox),
            "confidence": round(score, 6),
            "reason": reason,
            "focus": list(focus),
            "head": [round(value, 6) for value in face] if face is not None else None,
        }
        if not reason:
            if count == MAX_DESCRIPTORS:
                row["reason"] = "descriptor_limit"
            else:
                row.update(
                    reid=_encode(encoder, crop),
                    color=_colors(crop),
                    helmet=_helmet(image, bbox, face) if face is not None else None,
                )
                count += 1
        rows.append(row)
    return {
        "people": rows,
        "head_count": len(faces),
        "detections_truncated": len(people) > MAX_DETECTIONS,
        "descriptors_truncated": any(row["reason"] == "descriptor_limit" for row in rows),
    }

from __future__ import annotations

# ruff: noqa: E501 -- frozen artifact digests are intentionally kept as complete literals.
import hashlib
import json
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageDraw, ImageOps

from .bib_visual import (
    LLAMA_CPP_REVISION,
    MAX_TOKENS,
    MODEL_FILE_HASHES,
    MODEL_REVISION,
    PROMPT,
    SEED,
    TEMPERATURE,
    VisualReading,
)

TILE_SIZE = 1280
MAX_STRIDE = 960
OCR_MIN_CONFIDENCE = 0.5
ALPHANUMERIC_CONFLICT_MIN_IOU = 0.5
DISTANCE_SUFFIXES = ("m", "м", "km", "км", "k", "к")
MAX_CANDIDATES = 64
MAX_DIGITS = 16
MAX_RAW_OCR_BYTES = 256
OCR_ENGINE_PARAMETERS_SHA256 = "934591f8f3d92099ec6d4d76991b22250300e0cde9132c8e5e9e436f385ba68d"
RUNTIME_VERSIONS = {
    "numpy": "2.2.0",
    "onnxruntime": "1.29.0",
    "opencv-python": "4.12.0.88",
    "pillow": "12.0.0",
    "rapidocr": "3.9.2",
}

OCR_MODEL_HASHES = {  # noqa: E501 - frozen SHA-256 values must remain directly auditable
    "ch_PP-OCRv5_det_mobile.onnx": "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae",
    "ch_PP-OCRv5_rec_mobile.onnx": "5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    "dictionary.txt": "17665d27ed39f0deb82007859992d626d3105d0ee4578c120b7c72138dc04d05",
}
BIB_CONFIGURATION_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "tile_size": TILE_SIZE,
            "max_stride": MAX_STRIDE,
            "ocr_min_confidence": OCR_MIN_CONFIDENCE,
            "numeric_token_validation": {
                "rule": "reject-overlapping-distance-token-v1",
                "distance_suffixes": DISTANCE_SUFFIXES,
                "ambiguous_context_ocr": "one-unmarked-original-context-per-number",
                "min_iou": ALPHANUMERIC_CONFLICT_MIN_IOU,
                "min_confidence": OCR_MIN_CONFIDENCE,
            },
            "ocr_engine_parameters_sha256": OCR_ENGINE_PARAMETERS_SHA256,
            "ocr_models": OCR_MODEL_HASHES,
            "visual_model_revision": MODEL_REVISION,
            "visual_model_files": MODEL_FILE_HASHES,
            "prompt": PROMPT,
            "temperature": TEMPERATURE,
            "seed": SEED,
            "max_tokens": MAX_TOKENS,
            "context_crop": {
                "min_side": 384,
                "max_side": 1024,
                "candidate_scale": 4,
                "max_visual_side": 640,
                "resample": "pillow-thumbnail-default-bicubic",
                "polygon_mark": "red-2px-closed",
            },
            "bounds": {
                "candidates": MAX_CANDIDATES,
                "digits": MAX_DIGITS,
                "raw_ocr_bytes": MAX_RAW_OCR_BYTES,
                "visual_strings": 8,
                "visual_raw_bytes": 512,
            },
            "runtime_versions": RUNTIME_VERSIONS,
            "llama_cpp_revision": LLAMA_CPP_REVISION,
            "server": {
                "context_size": 4096,
                "threads": 2,
                "parallel": 1,
                "gpu_layers": 0,
                "jinja": True,
                "mmproj_offload": False,
                "chat_template": "gguf-tokenizer.chat_template",
                "response_format": "json_object",
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
).hexdigest()


def verify_runtime_versions() -> None:
    for package, expected in sorted(RUNTIME_VERSIONS.items()):
        try:
            actual = metadata.version(package)
        except metadata.PackageNotFoundError as error:
            raise RuntimeError(f"missing runtime package: {package}") from error
        if actual != expected:
            raise RuntimeError(f"runtime version mismatch: {package}")


class BibBoundsError(ValueError):
    pass


@dataclass(frozen=True)
class OCRRegion:
    polygon: tuple[tuple[float, float], ...]
    text: str
    confidence: float


@dataclass(frozen=True)
class OCRTileResult:
    regions: tuple[OCRRegion, ...]
    preparation_ms: float
    inference_ms: float


class OCREngine(Protocol):
    def infer(self, path: Path) -> OCRTileResult: ...


class VisualReader(Protocol):
    def read(self, *, image_id: str, image_path: Path) -> VisualReading: ...


@dataclass(frozen=True)
class BibCandidate:
    candidate_id: str
    number: str
    raw_text: str
    confidence: float
    polygon: tuple[tuple[float, float], ...]
    supporting_region_count: int
    crop: tuple[int, int, int, int]
    visual: VisualReading


@dataclass(frozen=True)
class BibRecognitionResult:
    source_sha256: str
    configuration_sha256: str
    width: int
    height: int
    candidates: tuple[BibCandidate, ...]
    tile_count: int
    ocr_preparation_ms: float
    ocr_inference_ms: float

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def tile_starts(length: int) -> tuple[int, ...]:
    end = max(0, length - TILE_SIZE)
    count = (end + MAX_STRIDE - 1) // MAX_STRIDE
    if count == 0:
        return (0,)
    return tuple(round(end * index / count) for index in range(count + 1))


def context_square(
    polygon: tuple[tuple[float, float], ...], width: int, height: int
) -> tuple[int, int, int, int]:
    if not polygon:
        raise ValueError("candidate polygon is empty")
    xs = [point[0] for point in polygon]
    ys = [point[1] for point in polygon]
    center_x = (min(xs) + max(xs)) / 2
    center_y = (min(ys) + max(ys)) / 2
    side = min(1024, max(384, math.ceil(4 * max(max(xs) - min(xs), max(ys) - min(ys)))))
    side = min(side, width, height)
    left = max(0, min(width - side, round(center_x - side / 2)))
    top = max(0, min(height - side, round(center_y - side / 2)))
    return left, top, left + side, top + side


def unambiguous_numeric_regions(number: str, regions: list[OCRRegion]) -> list[OCRRegion]:
    """Keep numeric regions unless overlapping OCR identifies that number with a distance unit."""
    conflicts = [region for region in regions if _is_distance_token(number, region.text)]
    return [
        region
        for region in regions
        if region.text.strip() == number
        and not any(
            _region_iou(region, conflict) >= ALPHANUMERIC_CONFLICT_MIN_IOU for conflict in conflicts
        )
    ]


def _is_distance_token(number: str, text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith(number) and stripped[len(number) :].casefold() in DISTANCE_SUFFIXES


def _region_iou(first: OCRRegion, second: OCRRegion) -> float:
    def bounds(region: OCRRegion) -> tuple[float, float, float, float]:
        xs, ys = zip(*region.polygon, strict=True)
        return min(xs), min(ys), max(xs), max(ys)

    left1, top1, right1, bottom1 = bounds(first)
    left2, top2, right2, bottom2 = bounds(second)
    intersection = max(0.0, min(right1, right2) - max(left1, left2)) * max(
        0.0, min(bottom1, bottom2) - max(top1, top2)
    )
    union = (right1 - left1) * (bottom1 - top1) + (right2 - left2) * (bottom2 - top2) - intersection
    return intersection / union if union > 0 else 0.0


def recognize_photo(
    source_path: Path,
    *,
    source_sha256: str,
    ocr: OCREngine,
    visual: VisualReader,
) -> BibRecognitionResult:
    with source_path.open("rb") as stream:
        actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual_hash != source_sha256:
        raise ValueError("source SHA-256 mismatch")
    grouped: dict[str, list[OCRRegion]] = {}
    preparation_ms = 0.0
    inference_ms = 0.0
    tile_count = 0
    with Image.open(source_path) as opened:
        if opened.width * opened.height > 100_000_000:
            raise BibBoundsError("image exceeds pixel bound")
        oriented = ImageOps.exif_transpose(opened).convert("RGB")
    try:
        width, height = oriented.size
        with tempfile.TemporaryDirectory(prefix="bib-recognition-") as temp:
            temp_path = Path(temp)
            for y in tile_starts(height):
                for x in tile_starts(width):
                    tile_path = temp_path / f"tile-{x}-{y}.png"
                    with oriented.crop(
                        (x, y, min(x + TILE_SIZE, width), min(y + TILE_SIZE, height))
                    ) as tile:
                        tile.save(tile_path, format="PNG")
                    output = ocr.infer(tile_path)
                    tile_count += 1
                    preparation_ms += output.preparation_ms
                    inference_ms += output.inference_ms
                    tile_path.unlink()
                    for region in output.regions:
                        if len(region.text.encode("utf-8")) > MAX_RAW_OCR_BYTES:
                            raise BibBoundsError("raw OCR text exceeds 256 UTF-8 bytes")
                        stripped = region.text.strip()
                        suffix = stripped.lstrip("0123456789")
                        number = stripped[: len(stripped) - len(suffix)]
                        if (
                            not number
                            or (suffix and not suffix.isalpha())
                            or region.confidence < OCR_MIN_CONFIDENCE
                        ):
                            continue
                        if len(number) > MAX_DIGITS:
                            raise BibBoundsError("digit string exceeds 16 digits")
                        mapped = OCRRegion(
                            polygon=tuple((px + x, py + y) for px, py in region.polygon),
                            text=region.text,
                            confidence=region.confidence,
                        )
                        grouped.setdefault(number, []).append(mapped)
                        if len(grouped) > MAX_CANDIDATES:
                            raise BibBoundsError("photo exceeds 64 unique OCR candidates")
            if len(grouped) > MAX_CANDIDATES:
                raise BibBoundsError("photo exceeds 64 unique OCR candidates")
            candidates: list[BibCandidate] = []
            for number, regions in sorted(grouped.items()):
                support = unambiguous_numeric_regions(number, regions)
                if not support:
                    continue
                representative = max(support, key=lambda region: region.confidence)
                # A suffix disagreement can be recognition noise (for example 259A).
                # Re-read the existing context once and require an actual distance unit.
                if any(
                    region.text.strip() != number
                    and _region_iou(representative, region) >= ALPHANUMERIC_CONFLICT_MIN_IOU
                    for region in regions
                ):
                    check_box = context_square(representative.polygon, width, height)
                    check_path = temp_path / "context-check.png"
                    with oriented.crop(check_box) as check:
                        check.save(check_path, format="PNG")
                    check_output = ocr.infer(check_path)
                    check_path.unlink()
                    preparation_ms += check_output.preparation_ms
                    inference_ms += check_output.inference_ms
                    for region in check_output.regions:
                        if len(region.text.encode("utf-8")) > MAX_RAW_OCR_BYTES:
                            raise BibBoundsError("raw OCR text exceeds 256 UTF-8 bytes")
                        if region.confidence >= OCR_MIN_CONFIDENCE and _is_distance_token(
                            number, region.text
                        ):
                            regions.append(
                                OCRRegion(
                                    polygon=tuple(
                                        (px + check_box[0], py + check_box[1])
                                        for px, py in region.polygon
                                    ),
                                    text=region.text,
                                    confidence=region.confidence,
                                )
                            )
                    support = unambiguous_numeric_regions(number, regions)
                    if not support:
                        continue
                    representative = max(support, key=lambda region: region.confidence)
                candidate_id = hashlib.sha256(f"{source_sha256}\0{number}".encode()).hexdigest()[
                    :24
                ]
                crop_box = context_square(representative.polygon, width, height)
                crop_path = temp_path / f"context-{candidate_id}.png"
                with oriented.crop(crop_box) as crop:
                    draw = ImageDraw.Draw(crop)
                    marked = [
                        (px - crop_box[0], py - crop_box[1]) for px, py in representative.polygon
                    ]
                    if marked:
                        draw.line(marked + [marked[0]], fill=(255, 0, 0), width=2)
                    crop.thumbnail((640, 640))
                    crop.save(crop_path, format="PNG")
                reading = visual.read(image_id=candidate_id, image_path=crop_path)
                crop_path.unlink()
                candidates.append(
                    BibCandidate(
                        candidate_id=candidate_id,
                        number=number,
                        raw_text=representative.text,
                        confidence=float(representative.confidence),
                        polygon=representative.polygon,
                        supporting_region_count=len(support),
                        crop=crop_box,
                        visual=reading,
                    )
                )
        return BibRecognitionResult(
            source_sha256=source_sha256,
            configuration_sha256=BIB_CONFIGURATION_SHA256,
            width=width,
            height=height,
            candidates=tuple(candidates),
            tile_count=tile_count,
            ocr_preparation_ms=preparation_ms,
            ocr_inference_ms=inference_ms,
        )
    finally:
        oriented.close()


class RapidOCREngine:
    """Frozen stage-by-stage RapidOCR adapter used by the measured experiment."""

    def __init__(self, config_path: Path) -> None:
        os.environ["ORT_DISABLE_TELEMETRY"] = "1"
        import onnxruntime
        from rapidocr import RapidOCR

        onnxruntime.disable_telemetry_events()
        self._ocr = RapidOCR(config_path=str(config_path))

    @classmethod
    def from_experiment_config(cls, config_path: Path, *, repository: Path) -> RapidOCREngine:
        import copy

        document = json.loads(config_path.read_text(encoding="utf-8"))
        parameters = copy.deepcopy(document["engine_parameters"])
        parameter_hash = hashlib.sha256(
            json.dumps(parameters, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if parameter_hash != OCR_ENGINE_PARAMETERS_SHA256:
            raise RuntimeError("OCR engine parameters do not match frozen configuration")
        parameters.pop("candidate_min_confidence", None)
        parameters.pop("disable_onnxruntime_telemetry", None)
        for section, key in (
            ("Det", "model_path"),
            ("Cls", "model_path"),
            ("Rec", "model_path"),
            ("Rec", "rec_keys_path"),
        ):
            value = parameters[section][key]
            path = Path(value)
            resolved = path if path.is_absolute() else repository / path.name
            expected = OCR_MODEL_HASHES[resolved.name]
            if not resolved.is_file():
                raise RuntimeError(f"missing OCR model file: {resolved.name}")
            if hashlib.sha256(resolved.read_bytes()).hexdigest() != expected:
                raise RuntimeError(f"OCR model hash mismatch: {resolved.name}")
            parameters[section][key] = str(resolved)
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as stream:
            json.dump(parameters, stream)
            stream.flush()
            return cls(Path(stream.name))

    def infer(self, path: Path) -> OCRTileResult:
        from rapidocr.ch_ppocr_rec import TextRecInput
        from rapidocr.utils.process_img import apply_vertical_padding, map_boxes_to_original

        started = time.perf_counter()
        original = self._ocr.load_img(path)
        image, operations = self._ocr.preprocess_img(original)
        if self._ocr.cfg.Global.use_vertical_padding:
            image, operations = apply_vertical_padding(
                image, operations, self._ocr.width_height_ratio, self._ocr.min_height
            )
        else:
            operations["padding_1"] = {"top": 0, "left": 0}
        preparation_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        detection = self._ocr.text_det(image)
        if detection.boxes is None:
            return OCRTileResult((), preparation_ms, (time.perf_counter() - started) * 1000)
        crops = self._ocr.crop_text_regions(image, detection.boxes)
        classified = self._ocr.text_cls(crops)
        if classified.img_list is None:
            raise RuntimeError("RapidOCR classifier returned no images")
        recognized = self._ocr.text_rec(
            TextRecInput(img=classified.img_list, return_word_box=False)
        )
        if recognized.txts is None or recognized.scores is None:
            raise RuntimeError("RapidOCR recognizer returned no result")
        if len(detection.boxes) != len(recognized.txts) or len(detection.boxes) != len(
            recognized.scores
        ):
            raise RuntimeError("RapidOCR stage result lengths differ")
        height, width = original.shape[:2]
        boxes = map_boxes_to_original(detection.boxes, operations, height, width)
        regions = tuple(
            OCRRegion(tuple((float(p[0]), float(p[1])) for p in box), str(text), float(score))
            for box, text, score in zip(boxes, recognized.txts, recognized.scores, strict=True)
        )
        return OCRTileResult(regions, preparation_ms, (time.perf_counter() - started) * 1000)

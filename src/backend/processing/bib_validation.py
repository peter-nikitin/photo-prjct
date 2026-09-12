from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Literal, cast

MAX_RESULT_BYTES = 120 * 1024
MAX_STORED_RESULT_BYTES = 128 * 1024
MAX_CANDIDATES = 64
MAX_RAW_TEXT_BYTES = 256
MAX_VISUAL_RESPONSE_BYTES = MAX_RESULT_BYTES
_SHA256 = re.compile(r"[0-9a-f]{64}")


class BibResultError(ValueError):
    pass


@dataclass(frozen=True)
class BibDecision:
    candidate_id: str
    number: str
    status: Literal["accepted", "rejected", "uncertain"]
    reason: str


@dataclass(frozen=True)
class ValidatedBibResult:
    result: dict[str, object]
    decisions: tuple[BibDecision, ...]
    validation_rule: str = "exact-worker-visual-v1"

    def as_stored_result(self) -> dict[str, object]:
        value: dict[str, object] = {
            "recognition": self.result,
            "validation": {
                "rule": self.validation_rule,
                "decisions": [
                    {
                        "candidate_id": decision.candidate_id,
                        "number": decision.number,
                        "status": decision.status,
                        "reason": decision.reason,
                    }
                    for decision in self.decisions
                ],
            },
        }
        if _json_size(value) > MAX_STORED_RESULT_BYTES:
            raise BibResultError("canonical bib result exceeds 128 KiB")
        return value


def validate_bib_result(
    value: object,
    *,
    expected_source_sha256: str,
    expected_configuration_sha256: str,
) -> ValidatedBibResult:
    if not isinstance(value, dict) or set(value) != {
        "source_sha256",
        "configuration_sha256",
        "width",
        "height",
        "candidates",
        "tile_count",
        "ocr_preparation_ms",
        "ocr_inference_ms",
    }:
        raise BibResultError("bib result fields do not match contract")
    if _json_size(value) > MAX_RESULT_BYTES:
        raise BibResultError("bib result exceeds 120 KiB")
    if (
        value["source_sha256"] != expected_source_sha256
        or not isinstance(value["source_sha256"], str)
        or _SHA256.fullmatch(value["source_sha256"]) is None
    ):
        raise BibResultError("source checksum mismatch")
    if value["configuration_sha256"] != expected_configuration_sha256:
        raise BibResultError("configuration identity mismatch")
    width, height = value["width"], value["height"]
    if not (_positive_int(width) and _positive_int(height) and _positive_int(value["tile_count"])):
        raise BibResultError("invalid image dimensions or tile count")
    if not all(_duration(value[name]) for name in ("ocr_preparation_ms", "ocr_inference_ms")):
        raise BibResultError("invalid OCR timing")
    candidates = value["candidates"]
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise BibResultError("invalid bib candidates")
    decisions: list[BibDecision] = []
    seen: set[str] = set()
    for candidate in candidates:
        decisions.append(
            _validate_candidate(
                candidate,
                expected_source_sha256=expected_source_sha256,
                width=width,
                height=height,
                seen=seen,
            )
        )
    return ValidatedBibResult(value, tuple(decisions))


def _validate_candidate(
    candidate: object,
    *,
    expected_source_sha256: str,
    width: int,
    height: int,
    seen: set[str],
) -> BibDecision:
    if not isinstance(candidate, dict) or set(candidate) != {
        "candidate_id",
        "number",
        "raw_text",
        "confidence",
        "polygon",
        "supporting_region_count",
        "crop",
        "visual",
    }:
        raise BibResultError("invalid candidate fields")
    candidate_id = candidate["candidate_id"]
    number = candidate["number"]
    if not isinstance(candidate_id, str) or len(candidate_id) != 24:
        raise BibResultError("invalid candidate id")
    if (
        not isinstance(number, str)
        or not number.isascii()
        or not number.isdigit()
        or not 1 <= len(number) <= 16
        or number in seen
    ):
        raise BibResultError("invalid or duplicate candidate number")
    seen.add(number)
    expected_candidate_id = hashlib.sha256(
        f"{expected_source_sha256}\0{number}".encode()
    ).hexdigest()[:24]
    if candidate_id != expected_candidate_id:
        raise BibResultError("candidate identity mismatch")
    raw_text = candidate["raw_text"]
    if not isinstance(raw_text, str) or len(raw_text.encode()) > MAX_RAW_TEXT_BYTES:
        raise BibResultError("invalid raw OCR evidence")
    if not _probability(candidate["confidence"]) or not _positive_int(
        candidate["supporting_region_count"]
    ):
        raise BibResultError("invalid OCR evidence")
    if not _polygon(candidate["polygon"], width=width, height=height):
        raise BibResultError("invalid candidate polygon")
    if not _crop(candidate["crop"], width=width, height=height):
        raise BibResultError("invalid candidate crop")
    visual = candidate["visual"]
    if not isinstance(visual, dict) or set(visual) != {
        "status",
        "raw_response",
        "numbers",
        "error_code",
        "inference_ms",
    }:
        raise BibResultError("invalid visual evidence")
    raw_response = visual["raw_response"]
    if (
        not isinstance(raw_response, str)
        or len(raw_response.encode()) > MAX_VISUAL_RESPONSE_BYTES
        or not _duration(visual["inference_ms"])
    ):
        raise BibResultError("invalid visual evidence bounds")
    if visual["status"] == "uncertain":
        if visual["numbers"] is not None or visual["error_code"] != "malformed_response":
            raise BibResultError("invalid uncertain visual evidence")
        return BibDecision(candidate_id, number, "uncertain", "visual_reading_failed")
    if visual["status"] != "complete":
        raise BibResultError("invalid visual status")
    numbers = visual["numbers"]
    if not isinstance(numbers, list) or len(numbers) > 8 or visual["error_code"] is not None:
        raise BibResultError("invalid complete visual evidence")
    if not all(
        isinstance(item, str) and item.isascii() and item.isdigit() and 1 <= len(item) <= 16
        for item in numbers
    ):
        raise BibResultError("invalid visual digit string")
    return BibDecision(
        candidate_id,
        number,
        "accepted" if number in numbers else "rejected",
        "exact_visual_match" if number in numbers else "exact_visual_mismatch",
    )


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _duration(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _probability(value: object) -> bool:
    return _duration(value) and cast(float, value) <= 1


def _coordinate(value: object, maximum: int) -> bool:
    return _duration(value) and cast(float, value) <= maximum


def _polygon(value: object, *, width: int, height: int) -> bool:
    return bool(
        isinstance(value, list)
        and len(value) == 4
        and all(
            isinstance(point, list)
            and len(point) == 2
            and _coordinate(point[0], width)
            and _coordinate(point[1], height)
            for point in value
        )
    )


def _crop(value: object, *, width: int, height: int) -> bool:
    return bool(
        isinstance(value, list)
        and len(value) == 4
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
        and 0 <= value[0] < value[2] <= width
        and 0 <= value[1] < value[3] <= height
    )

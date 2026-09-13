from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from photo_worker.bib_recognition import (
    BibBoundsError,
    OCRRegion,
    OCRTileResult,
    context_square,
    recognize_photo,
    tile_starts,
    verify_runtime_versions,
)
from photo_worker.bib_visual import (
    VisualBoundsError,
    VisualReading,
    parse_visual_response,
    verify_model_files,
)
from PIL import Image


class FakeOCR:
    def __init__(self, regions: tuple[OCRRegion, ...]) -> None:
        self.regions = regions
        self.sizes: list[tuple[int, int]] = []

    def infer(self, path: Path) -> OCRTileResult:
        with Image.open(path) as image:
            self.sizes.append(image.size)
        return OCRTileResult(regions=self.regions, preparation_ms=1.0, inference_ms=2.0)


class FakeVisual:
    def __init__(self, reading: VisualReading) -> None:
        self.reading = reading
        self.calls: list[tuple[str, tuple[int, int]]] = []

    def read(self, *, image_id: str, image_path: Path) -> VisualReading:
        with Image.open(image_path) as image:
            self.calls.append((image_id, image.size))
        return self.reading


def _jpeg(path: Path, size: tuple[int, int], *, orientation: int | None = None) -> str:
    image = Image.new("RGB", size, "white")
    exif = Image.Exif()
    if orientation is not None:
        exif[274] = orientation
    image.save(path, exif=exif)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_tile_starts_anchor_both_edges_with_bounded_stride() -> None:
    assert tile_starts(3500) == (0, 740, 1480, 2220)
    assert tile_starts(1806) == (0, 526)
    assert tile_starts(1280) == (0,)
    assert tile_starts(600) == (0,)


def test_recognition_orients_source_maps_tiles_and_keeps_visual_label_blind(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    digest = _jpeg(source, (1806, 3500), orientation=6)
    ocr = FakeOCR(
        (
            OCRRegion(
                polygon=((10.0, 20.0), (110.0, 20.0), (110.0, 70.0), (10.0, 70.0)),
                text="0012",
                confidence=0.9,
            ),
        )
    )
    visual = FakeVisual(
        VisualReading.complete(raw_response='{"numbers":["0012"]}', numbers=("0012",))
    )

    result = recognize_photo(source, source_sha256=digest, ocr=ocr, visual=visual)

    assert (result.width, result.height) == (3500, 1806)
    assert len(ocr.sizes) == 8
    assert set(ocr.sizes) == {(1280, 1280)}
    assert result.candidates[0].number == "0012"
    assert result.candidates[0].polygon == (
        (10.0, 20.0),
        (110.0, 20.0),
        (110.0, 70.0),
        (10.0, 70.0),
    )
    assert result.candidates[0].supporting_region_count == 8
    assert visual.calls == [(result.candidates[0].candidate_id, (400, 400))]
    assert "accept" not in str(result.as_dict()).lower()


def test_context_square_matches_measured_rule_and_clamps_edges() -> None:
    assert context_square(((100, 100), (200, 100), (200, 150), (100, 150)), 1000, 800) == (
        0,
        0,
        400,
        400,
    )
    assert context_square(((400, 300), (700, 300), (700, 500), (400, 500)), 1200, 900) == (
        100,
        0,
        1000,
        900,
    )


@pytest.mark.parametrize(
    ("raw", "numbers"),
    [
        ('{"numbers":["0012"]}', ("0012",)),
        ('```json\n{"numbers": []}\n```', ()),
    ],
)
def test_visual_parser_preserves_digit_strings(raw: str, numbers: tuple[str, ...]) -> None:
    assert parse_visual_response(raw) == numbers


def test_visual_bounds_are_terminal_not_malformed_uncertainty() -> None:
    with pytest.raises(VisualBoundsError, match="8 strings"):
        parse_visual_response('{"numbers":[' + ",".join('"1"' for _ in range(9)) + "]}")
    with pytest.raises(VisualBoundsError, match="16 digits"):
        parse_visual_response('{"numbers":["12345678901234567"]}')


def test_malformed_visual_response_is_preserved_as_uncertain(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    digest = _jpeg(source, (400, 400))
    ocr = FakeOCR(
        (OCRRegion(polygon=((5, 5), (20, 5), (20, 15), (5, 15)), text="42", confidence=0.8),)
    )
    visual = FakeVisual(
        VisualReading.malformed(raw_response="not json", error_code="malformed_response")
    )

    candidate = recognize_photo(source, source_sha256=digest, ocr=ocr, visual=visual).candidates[0]

    assert candidate.visual.status == "uncertain"
    assert candidate.visual.raw_response == "not json"
    assert candidate.visual.error_code == "malformed_response"


def test_bounds_fail_instead_of_truncating(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    digest = _jpeg(source, (100, 100))
    too_long = "1" * 17
    ocr = FakeOCR(
        (OCRRegion(polygon=((0, 0), (10, 0), (10, 10), (0, 10)), text=too_long, confidence=1.0),)
    )

    with pytest.raises(BibBoundsError, match="digit string"):
        recognize_photo(
            source,
            source_sha256=digest,
            ocr=ocr,
            visual=FakeVisual(VisualReading.complete("{}", ())),
        )


def test_model_preflight_requires_exact_offline_files(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    weight = model / "weights.bin"
    weight.write_bytes(b"known")
    expected = {"weights.bin": hashlib.sha256(b"known").hexdigest(), "config.json": "0" * 64}

    with pytest.raises(RuntimeError, match="missing model file: config.json"):
        verify_model_files(model, expected)
    (model / "config.json").write_bytes(b"wrong")
    with pytest.raises(RuntimeError, match="model hash mismatch: config.json"):
        verify_model_files(model, expected)


def test_runtime_preflight_rejects_a_changed_installed_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from photo_worker.bib_recognition import RUNTIME_VERSIONS

    monkeypatch.setattr(
        "photo_worker.bib_recognition.metadata.version",
        lambda name: "unexpected" if name == "rapidocr" else RUNTIME_VERSIONS[name],
    )
    with pytest.raises(RuntimeError, match="runtime version mismatch: rapidocr"):
        verify_runtime_versions()


def test_rapidocr_preflight_rejects_changed_engine_parameters(tmp_path: Path) -> None:
    from photo_worker.bib_recognition import RapidOCREngine

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"engine_parameters": {"Det": {"thresh": 0.31}}}))
    with pytest.raises(RuntimeError, match="parameters do not match"):
        RapidOCREngine.from_experiment_config(config, repository=tmp_path)


@pytest.mark.parametrize(
    ("number", "extended_text"), [("500", "500N"), ("500", "500м"), ("0042", "0042km")]
)
def test_recognition_does_not_promote_numeric_part_of_overlapping_alphanumeric_text(
    tmp_path: Path,
    number: str,
    extended_text: str,
) -> None:
    source = tmp_path / "photo.jpg"
    digest = _jpeg(source, (400, 400))
    # Measured overlapping reads of the distance label on 00001_Vlad.jpg.
    numeric = OCRRegion(
        polygon=((137, 178), (181, 131), (203, 151), (158, 198)),
        text=number,
        confidence=0.99069,
    )
    extended = OCRRegion(
        polygon=((131, 182), (182, 125), (207, 147), (156, 204)),
        text=extended_text,
        confidence=0.80878,
    )
    visual = FakeVisual(VisualReading.complete(json.dumps({"numbers": [number]}), (number,)))

    class ContextOCR(FakeOCR):
        def infer(self, path: Path) -> OCRTileResult:
            output = super().infer(path)
            if path.name == "context-check.png":
                return OCRTileResult((OCRRegion(extended.polygon, number + "m", 0.9107),), 1.0, 2.0)
            return output

    result = recognize_photo(
        source, source_sha256=digest, ocr=ContextOCR((numeric, extended)), visual=visual
    )

    assert result.candidates == ()
    assert visual.calls == []


def test_alphanumeric_conflict_does_not_suppress_a_separate_bib_with_same_number(
    tmp_path: Path,
) -> None:
    source = tmp_path / "photo.jpg"
    digest = _jpeg(source, (400, 400))
    regions = (
        OCRRegion(((10, 10), (40, 10), (40, 30), (10, 30)), "42", 0.99),
        OCRRegion(((10, 10), (45, 10), (45, 30), (10, 30)), "42km", 0.9),
        OCRRegion(((210, 210), (240, 210), (240, 230), (210, 230)), "42", 0.98),
    )
    result = recognize_photo(
        source,
        source_sha256=digest,
        ocr=FakeOCR(regions),
        visual=FakeVisual(VisualReading.complete('{"numbers":["42"]}', ("42",))),
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].polygon == regions[2].polygon
    assert result.candidates[0].supporting_region_count == 1


def test_overlapping_non_distance_suffix_noise_keeps_confirmed_bib(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    digest = _jpeg(source, (400, 400))
    regions = (
        OCRRegion(((14, 128), (148, 133), (146, 195), (12, 190)), "259", 0.99551),
        OCRRegion(((23, 128), (148, 135), (144, 197), (20, 190)), "259", 0.99718),
        OCRRegion(((21, 127), (184, 134), (181, 198), (18, 191)), "259A", 0.79368),
    )
    result = recognize_photo(
        source,
        source_sha256=digest,
        ocr=FakeOCR(regions),
        visual=FakeVisual(VisualReading.complete('{"numbers":["259"]}', ("259",))),
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].polygon == regions[1].polygon
    assert result.candidates[0].supporting_region_count == 2


@pytest.mark.parametrize(
    "evidence",
    json.loads(Path(__file__).with_name("fixtures").joinpath("bib-distance-ocr.json").read_text()),
    ids=lambda evidence: evidence["name"],
)
def test_measured_corpus_preserves_confirmed_bibs_and_rejects_distance(
    tmp_path: Path,
    evidence: dict,
) -> None:
    class RecordedOCR:
        def infer(self, path: Path) -> OCRTileResult:
            if path.name == "context-check.png":
                regions = evidence["context_regions"]
                x, y = 0, 0
            else:
                x, y = map(int, path.stem.split("-")[1:])
                regions = [r for r in evidence["regions"] if r["tile"] == [x, y]]
            return OCRTileResult(
                tuple(
                    OCRRegion(
                        tuple((px - x, py - y) for px, py in r["polygon"]),
                        r["text"],
                        r["confidence"],
                    )
                    for r in regions
                ),
                1.0,
                2.0,
            )

    source = tmp_path / "photo.jpg"
    digest = _jpeg(source, tuple(evidence["size"]))
    result = recognize_photo(
        source,
        source_sha256=digest,
        ocr=RecordedOCR(),
        visual=FakeVisual(VisualReading.complete('{"numbers":[]}', ())),
    )
    actual = [
        {
            "number": c.number,
            "polygon": [list(point) for point in c.polygon],
            "supporting_region_count": c.supporting_region_count,
        }
        for c in result.candidates
    ]
    assert actual == evidence["expected"]

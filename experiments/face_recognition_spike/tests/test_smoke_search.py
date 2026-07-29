from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlsplit

import numpy as np
import pytest
from face_spike.analysis import BoundingBox
from face_spike.benchmark import BenchmarkFace, BenchmarkRun, build_benchmark_proposal
from face_spike.index import FaceIndex, FaceIndexEntry
from face_spike.index_artifacts import FaceIndexManifest
from face_spike.quality import FaceQuality
from face_spike.smoke_search import (
    rank_unique_photos,
    run_smoke_search,
    write_smoke_search_output,
)


def _manifest(entry_count: int) -> FaceIndexManifest:
    return FaceIndexManifest(
        "a" * 64,
        "b" * 64,
        {"basename": "yunet.onnx", "size": 5, "sha256": "c" * 64},
        {"basename": "sface.onnx", "size": 5, "sha256": "d" * 64},
        {
            "detection_threshold": 0.0,
            "image_limit": None,
            "max_image_dimension": 12000,
            "max_image_pixels": 100_000_000,
            "min_face_px": 1,
            "minimum_face_sharpness": 0.0,
            "minimum_quality_confidence": 0.0,
            "minimum_relative_face_area": 0.0,
        },
        {"numpy": "test"},
        entry_count,
        2,
        "2026-07-28T10:00:00Z",
    )


def _index(entries: list[tuple[str, str, tuple[float, float]]]) -> FaceIndex:
    ordered = sorted(entries, key=lambda entry: entry[0])
    indexed_entries = tuple(
        FaceIndexEntry(
            face_id,
            filename,
            int(face_id.rsplit("-", maxsplit=1)[1]),
            BoundingBox(1.0, 2.0, 3.0, 4.0),
            f"faces/{face_id}.png",
            FaceQuality(1.0, 3.0, 0.1, 20.0, "accepted", ()),
        )
        for face_id, filename, _ in ordered
    )
    vectors = np.asarray([vector for _, _, vector in ordered], dtype=np.float32)
    return FaceIndex(indexed_entries, vectors, _manifest(len(ordered)))


def _proposal_and_index() -> tuple[object, FaceIndex]:
    faces: list[BenchmarkFace] = []
    entries: list[tuple[str, str, tuple[float, float]]] = []
    for person in range(1, 6):
        for photo in range(1, 5):
            filename = f"person-{person:02d}-photo-{photo:02d}.jpg"
            face_id = f"{filename}#face-001"
            faces.append(
                BenchmarkFace(
                    face_id,
                    filename,
                    f"faces/{face_id}.png",
                    f"person-{person:04d}",
                    "ok",
                    0.9,
                    20.0,
                    0.1,
                )
            )
            entries.append((face_id, filename, (1.0, 0.0)))
    run = BenchmarkRun("a" * 64, "b" * 64, tuple(faces))
    return build_benchmark_proposal(run, _index(entries), query_count=5), _index(entries)


def test_rank_unique_photos_excludes_the_whole_source_photo_and_orders_stably() -> None:
    index = _index(
        [
            ("held-out.jpg#face-001", "held-out.jpg", (1.0, 0.0)),
            ("held-out.jpg#face-002", "held-out.jpg", (0.8, 0.6)),
            ("a.jpg#face-002", "a.jpg", (0.8, 0.6)),
            ("a.jpg#face-001", "a.jpg", (0.8, 0.6)),
            ("b.jpg#face-001", "b.jpg", (0.8, 0.6)),
            ("later.jpg#face-001", "later.jpg", (0.0, 1.0)),
        ]
    )

    matches = rank_unique_photos(
        np.asarray([1.0, 0.0], dtype=np.float32), index, "held-out.jpg", limit=2
    )

    assert [(match.rank, match.filename, match.face_id) for match in matches] == [
        (1, "a.jpg", "a.jpg#face-001"),
        (2, "b.jpg", "b.jpg#face-001"),
    ]
    assert all(match.filename != "held-out.jpg" for match in matches)
    assert all(match.cosine_distance == pytest.approx(0.2) for match in matches)


def test_run_smoke_search_selects_bounded_queries_and_results() -> None:
    proposal, index = _proposal_and_index()

    result = run_smoke_search(
        proposal,
        index,
        lambda query: np.asarray([1.0, 0.0], dtype=np.float32),
        query_count=5,
        limit=2,
    )

    assert [query.query_id for query in result.queries] == [
        query.query_id for query in proposal.queries[:5]
    ]
    assert all(len(query.results) == 2 for query in result.queries)
    with pytest.raises(ValueError, match="query count"):
        run_smoke_search(
            proposal, index, lambda query: np.asarray([1.0, 0.0]), query_count=4, limit=2
        )
    with pytest.raises(ValueError, match="result limit"):
        run_smoke_search(
            proposal, index, lambda query: np.asarray([1.0, 0.0]), query_count=5, limit=0
        )


def test_write_smoke_search_output_is_deterministic_bounded_and_vector_free(tmp_path: Path) -> None:
    proposal, index = _proposal_and_index()
    result = run_smoke_search(
        proposal,
        index,
        lambda query: np.asarray([1.0, 0.0], dtype=np.float32),
        query_count=5,
        limit=1,
    )
    run_root = tmp_path / "run"
    photos_root = tmp_path / "photos"
    run_root.mkdir()
    photos_root.mkdir()
    first_output = tmp_path / "output-a"
    second_output = tmp_path / "output-b"

    write_smoke_search_output(first_output, result, run_root, photos_root)
    write_smoke_search_output(second_output, result, run_root, photos_root)

    payload = json.loads((first_output / "results.json").read_text(encoding="utf-8"))
    assert len(payload["queries"]) == 5
    assert all(len(query["results"]) == 1 for query in payload["queries"])
    assert (first_output / "results.json").read_bytes() == (
        second_output / "results.json"
    ).read_bytes()
    assert (first_output / "report.html").read_bytes() == (
        second_output / "report.html"
    ).read_bytes()
    report = (first_output / "report.html").read_text(encoding="utf-8")
    assert "Query crop" in report
    assert report.count('class="query-crop"') == 5
    assert report.count('class="result-photo"') == 5
    assert report.count("<img") == 10
    assert report.count('class="face-box-overlay"') == 5
    assert 'data-bounding-box="1,2,3,4"' in report
    assert '"embedding":' not in (first_output / "results.json").read_text(encoding="utf-8").lower()
    assert "vector" not in (first_output / "report.html").read_text(encoding="utf-8").lower()


def test_report_media_hrefs_percent_encode_reserved_crop_and_photo_characters(
    tmp_path: Path,
) -> None:
    proposal, index = _proposal_and_index()
    result = run_smoke_search(
        proposal,
        index,
        lambda query: np.asarray([1.0, 0.0], dtype=np.float32),
        query_count=5,
        limit=1,
    )
    run_root = tmp_path / "run"
    photos_root = tmp_path / "photos"
    crop_path = "faces/proxy #?.png"
    photo_name = "result #?.jpg"
    crop = run_root / crop_path
    photo = photos_root / photo_name
    crop.parent.mkdir(parents=True)
    photos_root.mkdir()
    crop.touch()
    photo.touch()
    first_query = replace(
        result.queries[0],
        query_crop_path=crop_path,
        results=(replace(result.queries[0].results[0], filename=photo_name),),
    )
    output = tmp_path / "output"

    write_smoke_search_output(
        output, replace(result, queries=(first_query,)), run_root, photos_root
    )

    urls = re.findall(r'(?:src|href)="([^"]+)"', (output / "report.html").read_text())
    resolved_paths: list[Path] = []
    for url in urls:
        parsed = urlsplit(url)
        assert parsed.query == ""
        assert parsed.fragment == ""
        resolved_paths.append((output / unquote(parsed.path)).resolve())
    assert crop.resolve() in resolved_paths
    assert photo.resolve() in resolved_paths

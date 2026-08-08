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
    SearchComparisonQuery,
    compare_search_indexes,
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
            "quality_algorithm_version": "normalized-laplacian-v1",
            "quality_crop_size": 112,
            "severe_blur_threshold": 0.0,
            "borderline_blur_threshold": 1.0,
            "minimum_confidence": 0.0,
            "minimum_relative_area": 0.0,
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
            FaceQuality(
                "normalized-laplacian-v1",
                112,
                1.0,
                3.0,
                0.1,
                20.0,
                "accepted",
                (),
            ),
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
                    "f" * 64,
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


def test_compare_search_reuses_exact_cosine_with_full_photo_holdout_and_rank_deltas() -> None:
    baseline = _index(
        [
            ("held-out.jpg#face-001", "held-out.jpg", (1.0, 0.0)),
            ("held-out.jpg#face-002", "held-out.jpg", (1.0, 0.0)),
            ("relevant-a.jpg#face-001", "relevant-a.jpg", (0.99, 0.141067)),
            ("relevant-b.jpg#face-001", "relevant-b.jpg", (0.98, 0.198997)),
            ("other.jpg#face-001", "other.jpg", (0.97, 0.243105)),
        ]
    )
    candidate = _index(
        [
            ("held-out.jpg#face-001", "held-out.jpg", (1.0, 0.0)),
            ("other.jpg#face-001", "other.jpg", (0.97, 0.243105)),
            ("relevant-a.jpg#face-001", "relevant-a.jpg", (0.99, 0.141067)),
        ]
    )
    query = SearchComparisonQuery(
        "query-1",
        "held-out.jpg",
        "e" * 64,
        "f" * 64,
        np.asarray([1.0, 0.0], dtype=np.float32),
        ("relevant-a.jpg", "relevant-b.jpg"),
    )

    result = compare_search_indexes(
        (query,),
        baseline,
        candidate,
        quality_rejected_baseline_face_ids=("relevant-b.jpg#face-001",),
        quality_comparison_sha256="a" * 64,
        benchmark_sha256="b" * 64,
        baseline_index_sha256="c" * 64,
        candidate_index_sha256="d" * 64,
    )

    query_result = result.query_results[0]
    assert "held-out.jpg" not in query_result.baseline_top
    assert "held-out.jpg" not in query_result.candidate_top
    assert query_result.lost_confirmed_relevant == ("relevant-b.jpg",)
    assert query_result.lost_due_to_quality_rejection == ("relevant-b.jpg",)
    assert result.aggregate == {
        "queries": 1,
        "baseline_unique_results": 3,
        "candidate_unique_results": 2,
        "unique_photo_delta": -1,
        "baseline_top_1_relevant": 1,
        "candidate_top_1_relevant": 1,
        "baseline_top_5_relevant": 2,
        "candidate_top_5_relevant": 1,
        "baseline_top_10_relevant": 2,
        "candidate_top_10_relevant": 1,
        "lost_confirmed_relevant": 1,
    }
    assert result.approved is False


def test_search_comparison_excludes_results_outside_the_production_direct_threshold() -> None:
    index = _index(
        [
            ("held-out.jpg#face-001", "held-out.jpg", (1.0, 0.0)),
            ("within.jpg#face-001", "within.jpg", (0.8, 0.6)),
            ("outside.jpg#face-001", "outside.jpg", (0.6, 0.8)),
        ]
    )

    result = compare_search_indexes(
        (
            SearchComparisonQuery(
                "query-1",
                "held-out.jpg",
                "e" * 64,
                "f" * 64,
                np.asarray([1.0, 0.0], dtype=np.float32),
                ("within.jpg",),
            ),
        ),
        index,
        index,
        quality_rejected_baseline_face_ids=(),
        quality_comparison_sha256="a" * 64,
        benchmark_sha256="b" * 64,
        baseline_index_sha256="c" * 64,
        candidate_index_sha256="d" * 64,
    )

    assert result.direct_threshold == 0.363
    assert result.query_results[0].baseline_top == ("within.jpg",)
    assert result.approved is True


def test_search_comparison_detects_confirmed_loss_beyond_top_ten_by_face_id() -> None:
    common = [
        (f"result-{index:02d}.jpg#face-001", f"result-{index:02d}.jpg", (1.0, 0.0))
        for index in range(10)
    ]
    relevant = ("zz-relevant.jpg#face-001", "zz-relevant.jpg", (1.0, 0.0))
    held_out = ("held-out.jpg#face-001", "held-out.jpg", (1.0, 0.0))
    query = SearchComparisonQuery(
        "query-1",
        "held-out.jpg",
        "e" * 64,
        "f" * 64,
        np.asarray([1.0, 0.0], dtype=np.float32),
        ("zz-relevant.jpg",),
    )

    result = compare_search_indexes(
        (query,),
        _index([held_out, *common, relevant]),
        _index([held_out, *common]),
        quality_rejected_baseline_face_ids=("zz-relevant.jpg#face-001",),
        quality_comparison_sha256="a" * 64,
        benchmark_sha256="b" * 64,
        baseline_index_sha256="c" * 64,
        candidate_index_sha256="d" * 64,
    )

    query_result = result.query_results[0]
    assert len(query_result.baseline_results) == 11
    assert query_result.baseline_top[-1] == "zz-relevant.jpg"
    assert query_result.lost_confirmed_relevant == ("zz-relevant.jpg",)
    assert query_result.quality_rejected_supports[0].face_id == "zz-relevant.jpg#face-001"


def test_search_loss_causation_uses_matched_baseline_support_id() -> None:
    baseline = _index(
        [
            ("held-out.jpg#face-001", "held-out.jpg", (1.0, 0.0)),
            ("lost.jpg#face-001", "lost.jpg", (1.0, 0.0)),
        ]
    )
    candidate = _index(
        [
            ("held-out.jpg#face-001", "held-out.jpg", (1.0, 0.0)),
            ("outside.jpg#face-001", "outside.jpg", (0.0, 1.0)),
        ]
    )
    query = SearchComparisonQuery(
        "query-1",
        "held-out.jpg",
        "e" * 64,
        "f" * 64,
        np.asarray([1.0, 0.0], dtype=np.float32),
        ("lost.jpg",),
    )

    result = compare_search_indexes(
        (query,),
        baseline,
        candidate,
        quality_rejected_baseline_face_ids=("lost.jpg#face-001",),
        quality_comparison_sha256="a" * 64,
        benchmark_sha256="b" * 64,
        baseline_index_sha256="c" * 64,
        candidate_index_sha256="d" * 64,
    )

    assert result.query_results[0].quality_rejected_supports == (
        result.query_results[0].baseline_results[0],
    )


def test_search_comparison_artifact_is_immutable_and_query_vector_free(tmp_path: Path) -> None:
    from face_spike.quality_comparison_artifacts import (
        load_search_comparison,
        write_search_comparison,
    )

    index = _index(
        [
            ("held-out.jpg#face-001", "held-out.jpg", (1.0, 0.0)),
            ("relevant.jpg#face-001", "relevant.jpg", (0.8, 0.6)),
        ]
    )
    result = compare_search_indexes(
        (
            SearchComparisonQuery(
                "query-1",
                "held-out.jpg",
                "e" * 64,
                "f" * 64,
                np.asarray([1.0, 0.0], dtype=np.float32),
                ("relevant.jpg",),
            ),
        ),
        index,
        index,
        quality_rejected_baseline_face_ids=(),
        quality_comparison_sha256="a" * 64,
        benchmark_sha256="b" * 64,
        baseline_index_sha256="c" * 64,
        candidate_index_sha256="d" * 64,
    )
    output = tmp_path / "search-comparison"

    write_search_comparison(output, result)

    assert load_search_comparison(output) == result
    serialized = "\n".join(
        path.read_text(encoding="utf-8") for path in output.iterdir() if path.is_file()
    ).lower()
    assert '"embedding"' not in serialized
    assert '"vector"' not in serialized
    with pytest.raises(FileExistsError):
        write_search_comparison(output, result)


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

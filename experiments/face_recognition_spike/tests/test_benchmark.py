from __future__ import annotations

import hashlib

import numpy as np
import pytest
from face_spike.analysis import BoundingBox
from face_spike.benchmark import (
    NEAREST_CROSS_CLUSTER_COUNT,
    Annotation,
    BenchmarkFace,
    BenchmarkRun,
    build_benchmark_proposal,
    finalize_benchmark,
)
from face_spike.index import FaceIndex, FaceIndexEntry
from face_spike.index_artifacts import FaceIndexManifest
from face_spike.quality import FaceQuality


def _quality(*, confidence: float = 0.95, sharpness: float = 120.0) -> FaceQuality:
    return FaceQuality(
        "normalized-laplacian-v1",
        112,
        confidence,
        24.0,
        0.1,
        sharpness,
        "accepted",
        (),
    )


def _manifest() -> FaceIndexManifest:
    return FaceIndexManifest(
        source_run_manifest_sha256="a" * 64,
        source_faces_sha256="b" * 64,
        yunet_model={"basename": "yunet.onnx", "size": 1, "sha256": "c" * 64},
        sface_model={"basename": "sface.onnx", "size": 2, "sha256": "d" * 64},
        parameters={"minimum_face_px": 24},
        dependency_versions={"numpy": "test"},
        entry_count=0,
        embedding_dimension=0,
        created_at="2026-07-28T10:00:00Z",
    )


def _face(
    cluster_id: str,
    number: int,
    *,
    filename: str | None = None,
    confidence: float = 0.95,
    sharpness: float = 120.0,
    status: str = "ok",
) -> BenchmarkFace:
    filename = f"{cluster_id}-{number}.jpg" if filename is None else filename
    return BenchmarkFace(
        face_id=f"{filename}#face-001",
        filename=filename,
        crop_path=f"faces/{hashlib.sha256(filename.encode()).hexdigest()}.png",
        crop_sha256="f" * 64,
        cluster_id=cluster_id,
        status=status,
        confidence=confidence,
        sharpness=sharpness,
        relative_area=0.1,
    )


def _run(*clusters: tuple[BenchmarkFace, ...]) -> BenchmarkRun:
    return BenchmarkRun("a" * 64, "b" * 64, tuple(face for cluster in clusters for face in cluster))


def _index(run: BenchmarkRun, vectors: dict[str, tuple[float, float]] | None = None) -> FaceIndex:
    vectors = vectors or {}
    entries = tuple(
        FaceIndexEntry(
            face.face_id,
            face.filename,
            1,
            BoundingBox(1, 2, 24, 24),
            face.crop_path,
            _quality(confidence=face.confidence, sharpness=face.sharpness),
        )
        for face in sorted(run.faces, key=lambda item: item.face_id)
        if face.status == "ok"
    )
    matrix = np.asarray(
        [vectors.get(entry.face_id, (1.0, 0.0)) for entry in entries], dtype=np.float32
    )
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    return FaceIndex(entries, matrix, _manifest())


def _cluster(
    cluster_id: str, *, confidence: float = 0.95, sharpness: float = 120.0
) -> tuple[BenchmarkFace, ...]:
    return tuple(
        _face(cluster_id, number, confidence=confidence, sharpness=sharpness)
        for number in range(1, 5)
    )


def _sized_cluster(
    cluster_id: str,
    size: int,
    *,
    confidence: float = 0.95,
    sharpness: float = 120.0,
) -> tuple[BenchmarkFace, ...]:
    return tuple(
        _face(cluster_id, number, confidence=confidence, sharpness=sharpness)
        for number in range(1, size + 1)
    )


def _valid_annotations(proposal: object) -> tuple[Annotation, ...]:
    annotations: list[Annotation] = []
    for query in proposal.queries:
        entries = proposal.face_by_id
        relevant = [
            candidate_id
            for candidate_id in query.candidate_face_ids
            if entries[candidate_id].cluster_id == query.proposed_cluster_id
        ][:3]
        annotations.extend(Annotation(query.query_id, face_id, "relevant") for face_id in relevant)
    return tuple(annotations)


def test_build_selects_eligible_people_in_deterministic_quality_and_size_coverage_order() -> None:
    small_high = _cluster("person-small-high", confidence=0.99, sharpness=200)
    medium_low = tuple(
        _face("person-medium-low", number, confidence=0.84, sharpness=30) for number in range(1, 7)
    )
    large_medium = tuple(
        _face("person-large-medium", number, confidence=0.91, sharpness=80)
        for number in range(1, 11)
    )
    run = _run(small_high, medium_low, large_medium)

    proposal = build_benchmark_proposal(run, _index(run), query_count=3)

    assert [query.proposed_cluster_id for query in proposal.queries] == [
        "person-small-high",
        "person-medium-low",
        "person-large-medium",
    ]
    assert [query.query_face_id for query in proposal.queries] == [
        "person-small-high-1.jpg#face-001",
        "person-medium-low-1.jpg#face-001",
        "person-large-medium-1.jpg#face-001",
    ]
    assert proposal == build_benchmark_proposal(run, _index(run), query_count=3)


def test_build_requires_four_distinct_source_photos_and_fatally_rejects_short_supply() -> None:
    ineligible = tuple(_face("too-small", number) for number in range(1, 4))
    run = _run(ineligible)

    with pytest.raises(ValueError, match="eligible"):
        build_benchmark_proposal(run, _index(run), query_count=1)


def test_build_proposes_only_one_person_per_cluster_and_keeps_deterministic_replacements() -> None:
    clusters = tuple(_cluster(f"person-{number:02d}") for number in range(31))
    run = _run(*clusters)

    proposal = build_benchmark_proposal(run, _index(run))

    assert len(proposal.queries) == 30
    assert len({query.proposed_cluster_id for query in proposal.queries}) == 30
    assert [query.proposed_cluster_id for query in proposal.replacement_queries] == ["person-30"]


def test_pool_has_same_cluster_nearest_and_distant_faces_without_holdout() -> None:
    selected = _cluster("selected")
    cross = tuple((_face(f"cross-{number}", 1),) for number in range(12))
    run = _run(selected, *cross)
    vectors = {face.face_id: (1.0, 0.0) for face in selected}
    vectors.update(
        {
            face.face_id: value
            for face, value in zip(
                (face for cluster in cross for face in cluster),
                (
                    (0.99, 0.1),
                    (0.98, 0.2),
                    (0.97, 0.3),
                    (0.96, 0.4),
                    (0.95, 0.5),
                    (0.94, 0.6),
                    (-0.1, 1.0),
                    (-0.2, 1.0),
                    (-0.3, 1.0),
                    (-0.4, 1.0),
                    (-0.5, 1.0),
                    (-1.0, 0.0),
                ),
                strict=True,
            )
        }
    )

    query = build_benchmark_proposal(run, _index(run, vectors), query_count=1).queries[0]

    assert query.query_filename not in {
        run.face_by_id[face_id].filename for face_id in query.candidate_face_ids
    }
    assert len(query.candidate_face_ids) == len(set(query.candidate_face_ids))
    assert query.candidate_face_ids[:3] == tuple(face.face_id for face in selected[1:])
    assert query.candidate_face_ids[3 : 3 + NEAREST_CROSS_CLUSTER_COUNT] == (
        "cross-0-1.jpg#face-001",
        "cross-1-1.jpg#face-001",
        "cross-2-1.jpg#face-001",
        "cross-3-1.jpg#face-001",
        "cross-4-1.jpg#face-001",
        "cross-5-1.jpg#face-001",
    )
    assert query.candidate_face_ids[3 + NEAREST_CROSS_CLUSTER_COUNT :] == (
        "cross-11-1.jpg#face-001",
        "cross-10-1.jpg#face-001",
        "cross-9-1.jpg#face-001",
        "cross-8-1.jpg#face-001",
    )
    assert query == build_benchmark_proposal(run, _index(run, vectors), query_count=1).queries[0]


def test_pool_uses_face_id_ties_for_equal_nearest_and_distant_cosine_distances() -> None:
    selected = _cluster("selected")
    cross = tuple(
        (_face(cluster_id, 1),)
        for cluster_id in (
            "cross-far-z",
            "cross-far-a",
            "cross-far-4",
            "cross-far-3",
            "cross-far-2",
            "cross-far-1",
            "cross-near-4",
            "cross-near-3",
            "cross-near-2",
            "cross-near-1",
            "cross-near-z",
            "cross-near-a",
        )
    )
    run = _run(selected, *cross)
    vectors = {face.face_id: (1.0, 0.0) for face in selected}
    vectors.update(
        {
            "cross-near-a-1.jpg#face-001": (1.0, 0.0),
            "cross-near-z-1.jpg#face-001": (1.0, 0.0),
            "cross-near-1-1.jpg#face-001": (0.99, 0.1),
            "cross-near-2-1.jpg#face-001": (0.98, 0.2),
            "cross-near-3-1.jpg#face-001": (0.97, 0.3),
            "cross-near-4-1.jpg#face-001": (0.96, 0.4),
            "cross-far-a-1.jpg#face-001": (-1.0, 0.0),
            "cross-far-z-1.jpg#face-001": (-1.0, 0.0),
            "cross-far-1-1.jpg#face-001": (-0.8, 0.6),
            "cross-far-2-1.jpg#face-001": (-0.7, 0.7),
            "cross-far-3-1.jpg#face-001": (-0.6, 0.8),
            "cross-far-4-1.jpg#face-001": (-0.5, 0.866),
        }
    )

    query = build_benchmark_proposal(run, _index(run, vectors), query_count=1).queries[0]

    assert query.candidate_face_ids[:3] == tuple(face.face_id for face in selected[1:])
    assert query.candidate_face_ids[3 : 3 + NEAREST_CROSS_CLUSTER_COUNT][:2] == (
        "cross-near-a-1.jpg#face-001",
        "cross-near-z-1.jpg#face-001",
    )
    assert query.candidate_face_ids[3 + NEAREST_CROSS_CLUSTER_COUNT :][:2] == (
        "cross-far-a-1.jpg#face-001",
        "cross-far-z-1.jpg#face-001",
    )
    assert len(query.candidate_face_ids) == len(set(query.candidate_face_ids))
    assert all(
        run.face_by_id[candidate_id].filename != query.query_filename
        for candidate_id in query.candidate_face_ids
    )


def test_finalization_replaces_invalid_query_deterministically() -> None:
    clusters = tuple(_cluster(f"person-{number:02d}") for number in range(31))
    run = _run(*clusters)
    proposal = build_benchmark_proposal(run, _index(run))
    all_queries = proposal.queries + proposal.replacement_queries
    annotations: list[Annotation] = []
    for query in all_queries:
        same_cluster = [
            face_id
            for face_id in query.candidate_face_ids
            if proposal.face_by_id[face_id].cluster_id == query.proposed_cluster_id
        ]
        if query.proposed_cluster_id != "person-00":
            annotations.extend(
                Annotation(query.query_id, face_id, "relevant") for face_id in same_cluster[:3]
            )

    final = finalize_benchmark(proposal, tuple(annotations))

    assert len(final.queries) == 30
    assert "person-00" not in {query.proposed_cluster_id for query in final.queries}
    assert "person-30" in {query.proposed_cluster_id for query in final.queries}


@pytest.mark.parametrize("label", ["same", "", "Relevant"])
def test_finalization_rejects_unknown_label_and_does_not_turn_unreviewed_candidates_into_negatives(
    label: str,
) -> None:
    clusters = tuple(_cluster(f"person-{number:02d}") for number in range(30))
    run = _run(*clusters)
    proposal = build_benchmark_proposal(run, _index(run))
    annotations = list(_valid_annotations(proposal))
    annotations[0] = Annotation(annotations[0].query_id, annotations[0].candidate_face_id, label)

    with pytest.raises(ValueError, match="label"):
        finalize_benchmark(proposal, tuple(annotations))


def test_finalization_rejects_unknown_duplicate_wrong_owner_and_held_out_annotations() -> None:
    clusters = tuple(_cluster(f"person-{number:02d}") for number in range(30))
    run = _run(*clusters)
    proposal = build_benchmark_proposal(run, _index(run))
    annotations = _valid_annotations(proposal)
    first = proposal.queries[0]
    other_query = proposal.queries[1]
    foreign_candidate = next(
        face_id
        for face_id in first.candidate_face_ids
        if face_id not in other_query.candidate_face_ids
    )
    cases = (
        annotations + (Annotation(first.query_id, "unknown#face-001", "different"),),
        annotations + (annotations[0],),
        annotations + (Annotation(other_query.query_id, foreign_candidate, "different"),),
        annotations + (Annotation(first.query_id, first.query_face_id, "relevant"),),
    )
    for case in cases:
        with pytest.raises(ValueError):
            finalize_benchmark(proposal, case)


def test_finalization_requires_three_relevant_distinct_non_query_photos() -> None:
    clusters = tuple(_cluster(f"person-{number:02d}") for number in range(30))
    run = _run(*clusters)
    proposal = build_benchmark_proposal(run, _index(run))
    annotations = list(_valid_annotations(proposal))
    first = proposal.queries[0]
    annotations = [
        annotation for annotation in annotations if annotation.query_id != first.query_id
    ]
    annotations.extend(
        Annotation(first.query_id, candidate, "relevant")
        for candidate in first.candidate_face_ids[:2]
    )

    with pytest.raises(ValueError, match="30 valid"):
        finalize_benchmark(proposal, tuple(annotations))


def test_split_is_stored_stable_stratified_and_keeps_each_person_in_one_half() -> None:
    clusters = tuple(
        _cluster(f"person-{number:02d}", confidence=(0.99 if number % 2 else 0.85))
        for number in range(30)
    )
    run = _run(*clusters)
    proposal = build_benchmark_proposal(run, _index(run))

    assert [query.split for query in proposal.queries].count("calibration") == 15
    assert [query.split for query in proposal.queries].count("evaluation") == 15
    assert {
        query.proposed_cluster_id for query in proposal.queries if query.split == "calibration"
    }.isdisjoint(
        {query.proposed_cluster_id for query in proposal.queries if query.split == "evaluation"}
    )
    assert proposal == build_benchmark_proposal(run, _index(run))


@pytest.mark.parametrize("stratum_counts", [(15, 15), (11, 9, 10)])
def test_split_balances_each_accepted_size_and_quality_stratum(
    stratum_counts: tuple[int, ...],
) -> None:
    qualities = ((0.99, 200.0), (0.91, 80.0), (0.84, 30.0))
    clusters = tuple(
        _sized_cluster(
            f"person-{group}-{number:02d}",
            4 if group == 0 else 6,
            confidence=confidence,
            sharpness=sharpness,
        )
        for group, (count, (confidence, sharpness)) in enumerate(
            zip(stratum_counts, qualities, strict=False)
        )
        for number in range(count)
    )
    run = _run(*clusters)
    proposal = build_benchmark_proposal(run, _index(run))
    splits_by_stratum: dict[tuple[int, int], list[str]] = {}
    for query in proposal.queries:
        face = proposal.face_by_id[query.query_face_id]
        size = sum(
            candidate.status == "ok" and candidate.cluster_id == query.proposed_cluster_id
            for candidate in run.faces
        )
        key = (
            0 if size <= 5 else 1 if size <= 9 else 2,
            0 if face.confidence >= 0.94 else 1 if face.confidence >= 0.88 else 2,
        )
        splits_by_stratum.setdefault(key, []).append(query.split)

    assert sum(query.split == "calibration" for query in proposal.queries) == 15
    assert all(
        abs(values.count("calibration") - values.count("evaluation")) <= 1
        for values in splits_by_stratum.values()
    )


def test_split_size_band_ignores_rejected_source_faces() -> None:
    rejected = _sized_cluster("person-00-rejected", 4) + tuple(
        _face("person-00-rejected", number, status="quality_rejected") for number in range(5, 11)
    )
    clusters = (rejected,) + tuple(_cluster(f"person-{number:02d}") for number in range(1, 30))
    run = _run(*clusters)
    proposal = build_benchmark_proposal(run, _index(run))
    rejected_query = next(
        query for query in proposal.queries if query.proposed_cluster_id == "person-00-rejected"
    )

    assert rejected_query.split == "calibration"


def test_finalization_rejects_manual_identity_duplicate_across_splits() -> None:
    run = _run(*(_cluster(f"person-{number:02d}") for number in range(30)))
    proposal = build_benchmark_proposal(run, _index(run))
    source = next(query for query in proposal.queries if query.split == "calibration")
    duplicate = next(
        query
        for query in proposal.queries
        if query.split == "evaluation" and source.query_face_id in query.candidate_face_ids
    )
    annotations = list(_valid_annotations(proposal))
    annotations.append(Annotation(duplicate.query_id, source.query_face_id, "relevant"))

    with pytest.raises(ValueError, match="30 valid"):
        finalize_benchmark(proposal, annotations)


def test_finalization_replaces_later_manual_identity_duplicate_with_its_slot_split() -> None:
    run = _run(*(_cluster(f"person-{number:02d}") for number in range(31)))
    proposal = build_benchmark_proposal(run, _index(run))
    source = proposal.queries[0]
    duplicate = next(
        query for query in proposal.queries[1:] if source.query_face_id in query.candidate_face_ids
    )
    replacement = proposal.replacement_queries[0]
    annotations = list(_valid_annotations(proposal))
    annotations.extend(
        Annotation(replacement.query_id, candidate_id, "relevant")
        for candidate_id in replacement.candidate_face_ids
        if proposal.face_by_id[candidate_id].cluster_id == replacement.proposed_cluster_id
    )
    annotations.append(Annotation(duplicate.query_id, source.query_face_id, "relevant"))

    final = finalize_benchmark(proposal, annotations)

    replacement_final = next(
        query for query in final.queries if query.query_id == replacement.query_id
    )
    assert duplicate.query_id not in {query.query_id for query in final.queries}
    assert replacement_final.split == duplicate.split


@pytest.mark.parametrize(
    "filename",
    [
        "/absolute.jpg",
        "../traversal.jpg",
        "",
        ".",
        "nested/../escape.jpg",
        "C:\\event.jpg",
        "\\\\server\\share.jpg",
        "back\\slash.jpg",
    ],
)
def test_benchmark_face_rejects_nonportable_or_absolute_source_filenames(filename: str) -> None:
    with pytest.raises(ValueError, match="benchmark face"):
        _face("person", 1, filename=filename)

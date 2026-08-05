from __future__ import annotations

import hashlib
import json
from math import sqrt

import numpy as np
import pytest
from face_spike.analysis import BoundingBox
from face_spike.benchmark import (
    Annotation,
    BenchmarkFace,
    BenchmarkQuery,
    BenchmarkSource,
    FinalBenchmark,
)
from face_spike.cluster_expansion import (
    ClusterExpansionConfiguration,
    ClusterMember,
    FaceCluster,
    _is_false_cluster_merge,
    _photo_label,
    evaluate_cluster_expansion,
    production_corpus_configuration_hash,
    rank_cluster_expansion,
    stable_evaluation_source,
)
from face_spike.index import FaceIndex, FaceIndexEntry
from face_spike.index_artifacts import FaceIndexManifest
from face_spike.quality import FaceQuality


def _benchmark() -> tuple[FinalBenchmark, FaceIndex, tuple[FaceCluster, ...]]:
    faces: list[BenchmarkFace] = []
    entries: list[FaceIndexEntry] = []
    vectors: list[list[float]] = []
    queries: list[BenchmarkQuery] = []
    annotations: list[Annotation] = []
    dimension = 60
    for number in range(30):
        prefix = f"person-{number:02d}"
        filenames = [
            f"{prefix}-query.jpg",
            f"{prefix}-direct.jpg",
            f"{prefix}-one.jpg",
            f"{prefix}-two.jpg",
        ]
        face_ids = [f"{filename}#face-001" for filename in filenames]
        basis = np.zeros(dimension, dtype=np.float32)
        basis[number] = 1.0
        if number == 0:
            direct = np.zeros(dimension, dtype=np.float32)
            direct[0] = 0.98
            direct[30] = sqrt(1 - 0.98**2)
            one = np.zeros(dimension, dtype=np.float32)
            one[0] = 0.5
            one[31] = sqrt(1 - 0.5**2)
            two = np.zeros(dimension, dtype=np.float32)
            two[0] = 0.4
            two[32] = sqrt(1 - 0.4**2)
            person_vectors = (basis, direct, one, two)
        else:
            person_vectors = (basis, basis, basis, basis)
        for face_id, filename, vector in zip(face_ids, filenames, person_vectors, strict=True):
            faces.append(
                BenchmarkFace(
                    face_id,
                    filename,
                    f"faces/{face_id}.png",
                    prefix,
                    "ok",
                    0.9,
                    100.0,
                    0.1,
                )
            )
            entries.append(
                FaceIndexEntry(
                    face_id,
                    filename,
                    1,
                    BoundingBox(0, 0, 10, 10),
                    f"faces/{face_id}.png",
                    FaceQuality(0.9, 10.0, 0.1, 100.0, "accepted", ()),
                )
            )
            vectors.append(vector.tolist())
        queries.append(
            BenchmarkQuery(
                f"query-{number:02d}",
                face_ids[0],
                filenames[0],
                f"faces/{face_ids[0]}.png",
                prefix,
                tuple(face_ids[1:]),
                "calibration" if number < 15 else "evaluation",
            )
        )
        annotations.extend(
            Annotation(f"query-{number:02d}", face_id, "relevant") for face_id in face_ids[1:]
        )
    manifest = FaceIndexManifest(
        "a" * 64,
        "b" * 64,
        {"basename": "yunet.onnx", "size": 1, "sha256": "e" * 64},
        {"basename": "sface.onnx", "size": 1, "sha256": "f" * 64},
        {},
        {"numpy": "test"},
        len(entries),
        dimension,
        "2026-08-05T00:00:00Z",
    )
    ordered = sorted(zip(entries, vectors, strict=True), key=lambda item: item[0].face_id)
    index = FaceIndex(
        tuple(item[0] for item in ordered),
        np.asarray([item[1] for item in ordered], dtype=np.float32),
        manifest,
    )
    index_manifest_sha256 = hashlib.sha256(
        json.dumps(manifest.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    source = BenchmarkSource("a" * 64, "b" * 64, index_manifest_sha256, "d" * 64)
    benchmark = FinalBenchmark(tuple(queries), tuple(annotations), tuple(faces), source)
    clusters = tuple(
        FaceCluster(
            f"person-{number:02d}",
            f"person-{number:02d}-query.jpg#face-001",
            tuple(
                ClusterMember(
                    f"person-{number:02d}-{suffix}.jpg#face-001",
                    f"person-{number:02d}-{suffix}.jpg",
                    distance,
                )
                for suffix, distance in (
                    ("query", 0.0),
                    ("direct", 0.01),
                    ("one", 0.1),
                    ("two", 0.2),
                )
            ),
        )
        for number in range(30)
    )
    return benchmark, index, clusters


def _configuration() -> ClusterExpansionConfiguration:
    return ClusterExpansionConfiguration(
        direct_threshold=0.1,
        anchor_threshold=0.05,
        configuration_hash="1" * 64,
    )


def _cluster_parameters() -> dict[str, object]:
    return {
        "cluster_threshold": 0.363,
        "representative_threshold": 0.363,
        "distance_block_size": 8,
        "max_candidate_edges": 10_000,
    }


def test_ranking_keeps_direct_photo_first_and_appends_unique_cluster_photos() -> None:
    benchmark, index, clusters = _benchmark()
    query = benchmark.queries[0]

    result = rank_cluster_expansion(query, benchmark, index, clusters, _configuration())

    assert [(item.filename, item.source) for item in result.photos] == [
        ("person-00-direct.jpg", "direct"),
        ("person-00-one.jpg", "face_cluster_expansion"),
        ("person-00-two.jpg", "face_cluster_expansion"),
    ]
    assert result.direct_photo_count + result.expanded_photo_count == result.final_photo_count


def test_held_out_corpus_removes_every_face_from_the_query_photo_before_clustering() -> None:
    """Keeping the query face can change a component medoid and leak evaluation evidence."""
    from face_spike.cluster_expansion import build_held_out_clusters

    benchmark, index, _clusters = _benchmark()
    query = benchmark.queries[0]

    retained = build_held_out_clusters(
        index,
        query_filename="not-a-source-photo.jpg",
        edge_threshold=0.6,
        representative_threshold=0.6,
        distance_block_size=8,
        max_candidate_edges=10_000,
    )
    held_out = build_held_out_clusters(
        index,
        query_filename=query.query_filename,
        edge_threshold=0.6,
        representative_threshold=0.6,
        distance_block_size=8,
        max_candidate_edges=10_000,
    )

    retained_cluster = next(
        cluster for cluster in retained if cluster.representative_face_id == query.query_face_id
    )
    held_out_person_clusters = [
        cluster
        for cluster in held_out
        if any(member.filename.startswith("person-00-") for member in cluster.members)
    ]
    assert len(retained_cluster.members) == 4
    assert [cluster.representative_face_id for cluster in held_out_person_clusters] == [
        "person-00-direct.jpg#face-001",
        "person-00-two.jpg#face-001",
    ]
    assert all(
        member.filename != query.query_filename
        for cluster in held_out
        for member in cluster.members
    )


def test_production_corpus_hash_binds_parameters_and_actual_generation_contract() -> None:
    from face_cluster_contract import corpus_configuration_hash

    benchmark, index, _clusters = _benchmark()
    parameters = {
        "cluster_threshold": 0.1,
        "representative_threshold": 0.1,
        "distance_block_size": 8,
        "max_candidate_edges": 100,
    }
    generations = (
        {
            "contract_version": 1,
            "processor_type": "face_embedding",
            "processor_version": 1,
            "configuration": {"model": "sface"},
            "configuration_hash": "a" * 64,
            "model": "sface",
        },
    )
    expected = production_corpus_configuration_hash(
        index,
        source_parameters=parameters,
        generations=generations,
    )

    assert expected == corpus_configuration_hash(
        algorithm_version="guarded-graph-v1",
        generations=generations,
        dimensions=index.manifest.embedding_dimension,
        edge_threshold=parameters["cluster_threshold"],
        representative_threshold=parameters["representative_threshold"],
        distance_block_size=parameters["distance_block_size"],
        max_candidate_edges=parameters["max_candidate_edges"],
    )

    assert expected != production_corpus_configuration_hash(
        index,
        source_parameters={**parameters, "cluster_threshold": 0.11},
        generations=generations,
    )
    assert expected != production_corpus_configuration_hash(
        index,
        source_parameters=parameters,
        generations=({**generations[0], "processor_version": 2},),
    )


def test_corpus_configuration_hash_excludes_input_identity_but_binds_generation_contract() -> None:
    from face_cluster_contract import corpus_configuration_hash

    generations = (
        {
            "contract_version": 1,
            "processor_type": "face_embedding",
            "processor_version": 1,
            "configuration": {"model": "sface"},
            "configuration_hash": "a" * 64,
            "model": "sface",
        },
    )
    expected = corpus_configuration_hash(
        algorithm_version="guarded-graph-v1",
        generations=generations,
        dimensions=128,
        edge_threshold=0.2,
        representative_threshold=0.1,
        distance_block_size=32,
        max_candidate_edges=100,
    )

    assert expected == corpus_configuration_hash(
        algorithm_version="guarded-graph-v1",
        generations=generations,
        dimensions=128,
        edge_threshold=0.2,
        representative_threshold=0.1,
        distance_block_size=32,
        max_candidate_edges=100,
    )
    assert expected != corpus_configuration_hash(
        algorithm_version="guarded-graph-v1",
        generations=({**generations[0], "processor_version": 2},),
        dimensions=128,
        edge_threshold=0.2,
        representative_threshold=0.1,
        distance_block_size=32,
        max_candidate_edges=100,
    )


def test_evaluation_reports_held_out_fragmentation_not_global_query_membership() -> None:
    benchmark, index, clusters = _benchmark()

    report = evaluate_cluster_expansion(
        benchmark,
        index,
        clusters,
        _configuration(),
        cluster_parameters={
            "cluster_threshold": 0.6,
            "representative_threshold": 0.6,
            "distance_block_size": 8,
            "max_candidate_edges": 10_000,
        },
    )

    assert "held_out" in report.cluster_metrics
    assert report.cluster_metrics["held_out"]["calibration"]["fragmentation"]["2"] >= 1


def test_evaluation_separates_calibration_from_held_out_metrics_and_preserves_identities() -> None:
    benchmark, index, clusters = _benchmark()

    report = evaluate_cluster_expansion(
        benchmark, index, clusters, _configuration(), cluster_parameters=_cluster_parameters()
    )
    payload = report.payload()

    assert set(payload["splits"]) == {"calibration", "evaluation"}
    assert payload["splits"]["calibration"]["search_count"] == 15
    assert payload["splits"]["evaluation"]["search_count"] == 15
    assert payload["splits"]["evaluation"]["counts"]["final_photos"] == (
        payload["splits"]["evaluation"]["counts"]["direct_photos"]
        + payload["splits"]["evaluation"]["counts"]["expanded_photos"]
    )
    incremental = payload["splits"]["evaluation"]["incremental"]
    expanded_precision = payload["splits"]["evaluation"]["precision"]["face_cluster_expansion"]
    assert (
        incremental["correct_photos"] + incremental["incorrect_photos"]
        == expanded_precision["labelled_photos"]
    )
    assert payload["report_sha256"] == report.report_sha256
    assert payload["corpus_configuration_hash"] == "1" * 64
    assert payload["thresholds"] == {"direct": 0.1, "anchor": 0.05}
    assert payload["resources"]["search_peak_memory_bytes"] is not None


def test_report_identity_is_deterministic_and_does_not_include_query_or_photo_artifacts() -> None:
    benchmark, index, clusters = _benchmark()

    first = evaluate_cluster_expansion(
        benchmark, index, clusters, _configuration(), cluster_parameters=_cluster_parameters()
    )
    second = evaluate_cluster_expansion(
        benchmark, index, clusters, _configuration(), cluster_parameters=_cluster_parameters()
    )
    encoded = first.to_json()

    assert first.report_sha256 == second.report_sha256
    assert hashlib.sha256(first.canonical_bytes()).hexdigest() == first.report_sha256
    assert "person-00" not in encoded
    assert "query.jpg" not in encoded

    changed_anchor = evaluate_cluster_expansion(
        benchmark,
        index,
        clusters,
        ClusterExpansionConfiguration(0.1, 0.04, "1" * 64),
        cluster_parameters=_cluster_parameters(),
    )
    assert first.evaluation_configuration_hash != changed_anchor.evaluation_configuration_hash
    assert first.report_sha256 != changed_anchor.report_sha256


def test_report_binds_resource_evidence_but_not_nondeterministic_measurements() -> None:
    benchmark, index, clusters = _benchmark()

    first = evaluate_cluster_expansion(
        benchmark,
        index,
        clusters,
        _configuration(),
        corpus_build_duration_ms=100,
        corpus_build_peak_memory_bytes=1000,
        cluster_parameters=_cluster_parameters(),
    )
    second = evaluate_cluster_expansion(
        benchmark,
        index,
        clusters,
        _configuration(),
        corpus_build_duration_ms=200,
        corpus_build_peak_memory_bytes=2000,
        cluster_parameters=_cluster_parameters(),
    )
    missing = evaluate_cluster_expansion(
        benchmark, index, clusters, _configuration(), cluster_parameters=_cluster_parameters()
    )

    assert first.report_sha256 == second.report_sha256
    assert first.payload()["resource_evidence"] == {
        "corpus_build": {"identity": "cluster-run-manifest-v1", "measured": True}
    }
    assert first.report_sha256 != missing.report_sha256


def test_report_identity_excludes_volatile_manifest_hashes_and_binds_stable_corpus_inputs() -> None:
    benchmark, index, clusters = _benchmark()
    alternate_manifest = FaceIndexManifest(
        "9" * 64,
        "b" * 64,
        index.manifest.yunet_model,
        index.manifest.sface_model,
        index.manifest.parameters,
        index.manifest.dependency_versions,
        index.manifest.entry_count,
        index.manifest.embedding_dimension,
        "2026-08-06T00:00:00Z",
    )
    alternate_index = FaceIndex(index.entries, index.embeddings, alternate_manifest)
    alternate_source = BenchmarkSource(
        "9" * 64,
        "b" * 64,
        hashlib.sha256(
            json.dumps(alternate_manifest.to_dict(), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "7" * 64,
    )
    alternate_benchmark = FinalBenchmark(
        benchmark.queries, benchmark.annotations, benchmark.faces, alternate_source
    )
    first = evaluate_cluster_expansion(
        benchmark,
        index,
        clusters,
        _configuration(),
        corpus_build_duration_ms=10,
        corpus_build_peak_memory_bytes=100,
        cluster_parameters=_cluster_parameters(),
    )
    second = evaluate_cluster_expansion(
        alternate_benchmark,
        alternate_index,
        clusters,
        _configuration(),
        corpus_build_duration_ms=20,
        corpus_build_peak_memory_bytes=200,
        cluster_parameters=_cluster_parameters(),
    )
    changed_membership = (
        FaceCluster(
            clusters[0].cluster_id,
            clusters[0].representative_face_id,
            (
                clusters[0].members[0],
                ClusterMember(
                    clusters[0].members[1].face_id,
                    clusters[0].members[1].filename,
                    0.02,
                ),
                *clusters[0].members[2:],
            ),
        ),
        *clusters[1:],
    )
    changed_model_manifest = FaceIndexManifest(
        index.manifest.source_run_manifest_sha256,
        index.manifest.source_faces_sha256,
        index.manifest.yunet_model,
        {**index.manifest.sface_model, "sha256": "c" * 64},
        index.manifest.parameters,
        index.manifest.dependency_versions,
        index.manifest.entry_count,
        index.manifest.embedding_dimension,
        index.manifest.created_at,
    )
    changed_model_index = FaceIndex(index.entries, index.embeddings, changed_model_manifest)
    changed_model_source = BenchmarkSource(
        benchmark.source.run_manifest_sha256,
        benchmark.source.faces_sha256,
        hashlib.sha256(
            json.dumps(
                changed_model_manifest.to_dict(), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        benchmark.source.proposal_sha256,
    )
    changed_model_benchmark = FinalBenchmark(
        benchmark.queries, benchmark.annotations, benchmark.faces, changed_model_source
    )

    assert first.report_sha256 == second.report_sha256
    assert (
        first.report_sha256
        != evaluate_cluster_expansion(
            benchmark,
            index,
            changed_membership,
            _configuration(),
            cluster_parameters=_cluster_parameters(),
        ).report_sha256
    )
    assert (
        first.report_sha256
        != evaluate_cluster_expansion(
            changed_model_benchmark,
            changed_model_index,
            clusters,
            _configuration(),
            cluster_parameters=_cluster_parameters(),
        ).report_sha256
    )


def test_report_identity_binds_metric_preserving_normalized_embedding_matrix() -> None:
    benchmark, index, clusters = _benchmark()
    embeddings = index.embeddings.copy()
    positions = [
        position
        for position, entry in enumerate(index.entries)
        if entry.filename.startswith("person-01-")
    ]
    for position in positions:
        embeddings[position, 1] = 0.99
        embeddings[position, 59] = sqrt(1 - 0.99**2)
    perturbed_index = FaceIndex(index.entries, embeddings, index.manifest)
    first = evaluate_cluster_expansion(
        benchmark, index, clusters, _configuration(), cluster_parameters=_cluster_parameters()
    )
    perturbed = evaluate_cluster_expansion(
        benchmark,
        perturbed_index,
        clusters,
        _configuration(),
        cluster_parameters=_cluster_parameters(),
    )

    assert first.splits == perturbed.splits
    assert (
        stable_evaluation_source(benchmark, index, clusters, {})["index_content_sha256"]
        != stable_evaluation_source(benchmark, perturbed_index, clusters, {})[
            "index_content_sha256"
        ]
    )
    assert first.evaluation_configuration_hash != perturbed.evaluation_configuration_hash
    assert first.report_sha256 != perturbed.report_sha256


def test_configuration_requires_explicit_distinct_strong_anchor_and_lowercase_hash() -> None:
    with pytest.raises(ValueError):
        ClusterExpansionConfiguration(0.1, 0.1, "1" * 64)
    with pytest.raises(ValueError):
        ClusterExpansionConfiguration(0.1, 0.05, "A" * 64)


def test_cluster_artifact_member_filename_must_match_the_reconciled_index() -> None:
    benchmark, index, clusters = _benchmark()
    first = clusters[0]
    broken = FaceCluster(
        first.cluster_id,
        first.representative_face_id,
        (
            ClusterMember(
                first.members[0].face_id,
                "wrong-photo.jpg",
                first.members[0].distance_to_representative,
            ),
            *first.members[1:],
        ),
    )

    with pytest.raises(ValueError):
        rank_cluster_expansion(
            benchmark.queries[0],
            benchmark,
            index,
            (broken, *clusters[1:]),
            _configuration(),
        )


def test_photo_label_requires_every_nonrelevant_face_to_be_explicitly_different() -> None:
    assert _photo_label({"relevant", "different"}) == "relevant"
    assert _photo_label({"different", "uncertain"}) == "uncertain"
    assert _photo_label({"different"}) == "different"


def test_false_cluster_merge_uses_face_annotations_not_aggregate_photo_labels() -> None:
    members = (
        ClusterMember("relevant-face", "group.jpg", 0.0),
        ClusterMember("different-face", "group.jpg", 0.1),
    )

    assert _is_false_cluster_merge(
        members, {"relevant-face": "relevant", "different-face": "different"}
    )
    assert not _is_false_cluster_merge(members, {"relevant-face": "relevant"})
    assert not _is_false_cluster_merge((members[0],), {"relevant-face": "relevant"})

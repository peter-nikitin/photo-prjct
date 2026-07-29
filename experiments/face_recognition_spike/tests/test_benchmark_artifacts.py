from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from face_spike.benchmark import Annotation, build_benchmark_proposal, finalize_benchmark
from face_spike.benchmark_artifacts import (
    ANNOTATION_CSV_HEADERS,
    BenchmarkFinalArtifactWriter,
    BenchmarkProposalArtifactWriter,
    load_annotations_csv,
    load_benchmark_proposal,
    load_final_benchmark,
)
from test_benchmark import _cluster, _index, _run, _valid_annotations


def _proposal() -> object:
    run = _run(*(_cluster(f"person-{number:02d}") for number in range(30)))
    return build_benchmark_proposal(run, _index(run))


def _row(proposal: object, annotation: Annotation) -> dict[str, str | int]:
    query = next(query for query in proposal.queries if query.query_id == annotation.query_id)
    face = proposal.face_by_id[annotation.candidate_face_id]
    return {
        "schema_version": 1,
        "source_run_manifest_sha256": proposal.source.run_manifest_sha256,
        "source_faces_sha256": proposal.source.faces_sha256,
        "index_manifest_sha256": proposal.source.index_manifest_sha256,
        "proposal_sha256": proposal.source.proposal_sha256,
        "query_id": query.query_id,
        "query_face_id": query.query_face_id,
        "query_filename": query.query_filename,
        "candidate_face_id": annotation.candidate_face_id,
        "candidate_filename": face.filename,
        "label": annotation.label,
        "note": annotation.note or "",
    }


def _write_csv(
    path: Path, rows: list[dict[str, str | int]], headers: tuple[str, ...] = ANNOTATION_CSV_HEADERS
) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def test_artifact_writers_publish_schema_bound_json_without_vectors_and_load_parity(
    tmp_path: Path,
) -> None:
    proposal = _proposal()
    proposal_path = tmp_path / "proposal"
    BenchmarkProposalArtifactWriter(proposal_path).finish(proposal)
    loaded = load_benchmark_proposal(proposal_path)

    assert loaded == proposal
    manifest = json.loads((proposal_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["source"]["proposal_sha256"] == proposal.source.proposal_sha256
    assert "embedding" not in (proposal_path / "proposal.json").read_text(encoding="utf-8").lower()
    assert not list(tmp_path.glob(".proposal.*"))

    final = finalize_benchmark(proposal, _valid_annotations(proposal))
    final_path = tmp_path / "final"
    BenchmarkFinalArtifactWriter(final_path).finish(final)
    assert load_final_benchmark(final_path) == final


def test_final_loader_rejects_a_structurally_valid_bundle_without_required_relevant_photos(
    tmp_path: Path,
) -> None:
    proposal = _proposal()
    final = finalize_benchmark(proposal, _valid_annotations(proposal))
    final_path = tmp_path / "final"
    BenchmarkFinalArtifactWriter(final_path).finish(final)
    payload_path = final_path / "benchmark.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["annotations"] = []
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="final benchmark schema"):
        load_final_benchmark(final_path)


def test_artifact_writers_preserve_existing_destination_and_clean_staging_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proposal = _proposal()
    output = tmp_path / "proposal"
    output.mkdir()
    sentinel = output / "sentinel"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        BenchmarkProposalArtifactWriter(output)
    assert sentinel.read_text(encoding="utf-8") == "preserve"

    output = tmp_path / "failing"
    writer = BenchmarkProposalArtifactWriter(output)
    from face_spike import benchmark_artifacts

    real_replace = benchmark_artifacts.os.replace

    def fail_publish(source: Path, destination: Path) -> None:
        if destination == output:
            raise OSError("injected publication failure")
        real_replace(source, destination)

    monkeypatch.setattr(benchmark_artifacts.os, "replace", fail_publish)
    with pytest.raises(OSError, match="injected publication failure"):
        writer.finish(proposal)
    assert not output.exists()
    assert not list(tmp_path.glob(".failing.*"))


def test_annotation_import_requires_exact_header_bundle_identity_and_single_line_notes(
    tmp_path: Path,
) -> None:
    proposal = _proposal()
    annotation = _valid_annotations(proposal)[0]
    path = tmp_path / "annotations.csv"
    _write_csv(path, [_row(proposal, annotation)])
    current: dict[tuple[str, str], Annotation] = {}

    imported = load_annotations_csv(path, proposal, current)

    assert imported == (annotation,)
    assert current == {(annotation.query_id, annotation.candidate_face_id): annotation}
    assert list(ANNOTATION_CSV_HEADERS) == list(
        csv.DictReader(path.open(encoding="utf-8")).fieldnames or []
    )

    bad_header = tmp_path / "bad-header.csv"
    _write_csv(bad_header, [_row(proposal, annotation)], ANNOTATION_CSV_HEADERS[:-1])
    wrong_source = _row(proposal, annotation)
    wrong_source["proposal_sha256"] = "f" * 64
    wrong_source_path = tmp_path / "wrong-source.csv"
    _write_csv(wrong_source_path, [wrong_source])
    multiline = _row(proposal, annotation)
    multiline["note"] = "not\nallowed"
    multiline_path = tmp_path / "multiline.csv"
    _write_csv(multiline_path, [multiline])
    for invalid in (bad_header, wrong_source_path, multiline_path):
        with pytest.raises(ValueError):
            load_annotations_csv(invalid, proposal, current)
        assert current == {(annotation.query_id, annotation.candidate_face_id): annotation}


def test_annotation_import_rejects_unknown_duplicate_and_wrong_query_ownership_atomically(
    tmp_path: Path,
) -> None:
    proposal = _proposal()
    first = _valid_annotations(proposal)[0]
    first_query, other_query = proposal.queries[:2]
    current = {(first.query_id, first.candidate_face_id): first}
    rows = [_row(proposal, first), _row(proposal, first)]
    duplicate = tmp_path / "duplicate.csv"
    _write_csv(duplicate, rows)
    unknown = _row(proposal, first)
    unknown["candidate_face_id"] = "unknown.jpg#face-001"
    unknown_path = tmp_path / "unknown.csv"
    _write_csv(unknown_path, [unknown])
    wrong = _row(proposal, first)
    foreign_candidate = next(
        face_id
        for face_id in first_query.candidate_face_ids
        if face_id not in other_query.candidate_face_ids
    )
    wrong["query_id"] = other_query.query_id
    wrong["candidate_face_id"] = foreign_candidate
    wrong["candidate_filename"] = proposal.face_by_id[foreign_candidate].filename
    wrong_path = tmp_path / "wrong.csv"
    _write_csv(wrong_path, [wrong])
    for invalid in (duplicate, unknown_path, wrong_path):
        with pytest.raises(ValueError):
            load_annotations_csv(invalid, proposal, current)
        assert current == {(first.query_id, first.candidate_face_id): first}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest, payload: manifest["source"].__setitem__("run_manifest_sha256", "f" * 64),
        lambda manifest, payload: manifest["source"].__setitem__("faces_sha256", "f" * 64),
        lambda manifest, payload: manifest["source"].__setitem__("index_manifest_sha256", "f" * 64),
        lambda manifest, payload: manifest["source"].__setitem__("proposal_sha256", "f" * 64),
        lambda manifest, payload: manifest.__setitem__("schema_version", 2),
        lambda manifest, payload: payload.__setitem__("vectors", []),
        lambda manifest, payload: payload["faces"][0].__setitem__("filename", "/absolute.jpg"),
        lambda manifest, payload: payload["faces"][0].__setitem__("filename", "../escape.jpg"),
        lambda manifest, payload: payload["queries"].append(payload["queries"][0]),
        lambda manifest, payload: payload["queries"].reverse(),
    ],
)
def test_proposal_loader_rejects_strict_schema_source_and_path_mutations(
    tmp_path: Path, mutation: object
) -> None:
    proposal = _proposal()
    output = tmp_path / "proposal"
    BenchmarkProposalArtifactWriter(output).finish(proposal)
    manifest_path = output / "manifest.json"
    payload_path = output / "proposal.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    mutation(manifest, payload)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_benchmark_proposal(output)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda manifest, payload: manifest["source"].__setitem__("run_manifest_sha256", "f" * 64),
        lambda manifest, payload: payload["annotations"].reverse(),
        lambda manifest, payload: payload.__setitem__("vectors", []),
    ],
)
def test_final_loader_rejects_source_annotation_order_and_vector_mutations(
    tmp_path: Path, mutate: object
) -> None:
    proposal = _proposal()
    final = finalize_benchmark(proposal, _valid_annotations(proposal))
    output = tmp_path / "final"
    BenchmarkFinalArtifactWriter(output).finish(final)
    manifest_path = output / "manifest.json"
    payload_path = output / "benchmark.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    mutate(manifest, payload)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_final_benchmark(output)


def test_final_loader_rejects_out_of_order_annotation_rows(tmp_path: Path) -> None:
    proposal = _proposal()
    final = finalize_benchmark(proposal, _valid_annotations(proposal))
    output = tmp_path / "final"
    BenchmarkFinalArtifactWriter(output).finish(final)
    payload_path = output / "benchmark.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["annotations"].reverse()
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="final benchmark schema"):
        load_final_benchmark(output)

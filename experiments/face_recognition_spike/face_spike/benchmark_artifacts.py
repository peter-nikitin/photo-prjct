from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, MutableMapping
from dataclasses import asdict
from pathlib import Path
from typing import cast

from .benchmark import (
    SCHEMA_VERSION,
    Annotation,
    BenchmarkFace,
    BenchmarkProposal,
    BenchmarkQuery,
    BenchmarkSource,
    FinalBenchmark,
    Split,
    validate_annotations,
)

ANNOTATION_CSV_HEADERS = (
    "schema_version",
    "source_run_manifest_sha256",
    "source_faces_sha256",
    "index_manifest_sha256",
    "proposal_sha256",
    "query_id",
    "query_face_id",
    "query_filename",
    "candidate_face_id",
    "candidate_filename",
    "label",
    "note",
)


def final_benchmark_sha256(benchmark: FinalBenchmark) -> str:
    """Hash the finalized queries, annotations, faces, and source identity."""
    if not isinstance(benchmark, FinalBenchmark):
        raise TypeError("final benchmark is required")
    return hashlib.sha256(
        json.dumps(
            _final_payload(benchmark),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


class BenchmarkProposalArtifactWriter:
    def __init__(self, output: Path) -> None:
        self._writer = _ArtifactWriter(output)

    def finish(self, proposal: BenchmarkProposal) -> None:
        if not isinstance(proposal, BenchmarkProposal):
            raise TypeError("proposal must be a BenchmarkProposal")
        self._writer.finish(
            "benchmark-proposal",
            proposal.source,
            "proposal.json",
            _proposal_payload(proposal),
        )

    def abort(self) -> None:
        self._writer.abort()


class BenchmarkFinalArtifactWriter:
    def __init__(self, output: Path) -> None:
        self._writer = _ArtifactWriter(output)

    def finish(self, benchmark: FinalBenchmark) -> None:
        if not isinstance(benchmark, FinalBenchmark):
            raise TypeError("benchmark must be a FinalBenchmark")
        self._writer.finish(
            "final-benchmark",
            benchmark.source,
            "benchmark.json",
            _final_payload(benchmark),
        )

    def abort(self) -> None:
        self._writer.abort()


class _ArtifactWriter:
    """Publish a complete immutable bundle through a hidden sibling directory."""

    def __init__(self, output: Path) -> None:
        if os.path.lexists(output):
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        self.output = output
        self.staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))

    def finish(
        self,
        artifact_type: str,
        source: BenchmarkSource,
        payload_name: str,
        payload: Mapping[str, object],
    ) -> None:
        try:
            _write_json_atomic(
                self.staging / "manifest.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "artifact_type": artifact_type,
                    "source": _source_payload(source),
                },
            )
            _write_json_atomic(self.staging / payload_name, payload)
            if os.path.lexists(self.output):
                raise FileExistsError(self.output)
            os.replace(self.staging, self.output)
        except BaseException:
            self.abort()
            raise

    def abort(self) -> None:
        if self.staging.exists():
            shutil.rmtree(self.staging)


def load_benchmark_proposal(path: Path) -> BenchmarkProposal:
    root = _artifact_root(path, "benchmark-proposal", "proposal.json")
    manifest = _load_json_object(root / "manifest.json")
    source = _source_from_payload(_mapping(manifest["source"]))
    payload = _load_json_object(root / "proposal.json")
    if set(payload) != {"schema_version", "queries", "replacement_queries", "faces"}:
        raise ValueError("proposal schema is invalid")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("proposal schema is invalid")
    try:
        return BenchmarkProposal(
            _queries_from_payload(payload["queries"]),
            _queries_from_payload(payload["replacement_queries"]),
            _faces_from_payload(payload["faces"]),
            source,
        )
    except (TypeError, ValueError):
        raise ValueError("proposal schema is invalid") from None


def load_final_benchmark(path: Path) -> FinalBenchmark:
    root = _artifact_root(path, "final-benchmark", "benchmark.json")
    manifest = _load_json_object(root / "manifest.json")
    source = _source_from_payload(_mapping(manifest["source"]))
    payload = _load_json_object(root / "benchmark.json")
    if set(payload) != {"schema_version", "queries", "annotations", "faces", "source"}:
        raise ValueError("final benchmark schema is invalid")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("final benchmark schema is invalid")
    if _source_from_payload(_mapping(payload["source"])) != source:
        raise ValueError("final benchmark source does not match manifest")
    try:
        return FinalBenchmark(
            _queries_from_payload(payload["queries"]),
            _annotations_from_payload(payload["annotations"]),
            _faces_from_payload(payload["faces"]),
            source,
        )
    except (TypeError, ValueError):
        raise ValueError("final benchmark schema is invalid") from None


def load_annotations_csv(
    path: Path,
    proposal: BenchmarkProposal,
    current: MutableMapping[tuple[str, str], Annotation],
) -> tuple[Annotation, ...]:
    """Replace the draft only after the whole CSV validates against this exact proposal."""
    if not isinstance(proposal, BenchmarkProposal):
        raise TypeError("proposal must be a BenchmarkProposal")
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != ANNOTATION_CSV_HEADERS:
                raise ValueError("annotation CSV header is invalid")
            rows = list(reader)
    except OSError:
        raise ValueError("annotation CSV cannot be read") from None
    annotations: list[Annotation] = []
    query_by_id = {query.query_id: query for query in proposal.all_queries}
    faces = proposal.face_by_id
    for row in rows:
        if set(row) != set(ANNOTATION_CSV_HEADERS) or None in row:
            raise ValueError("annotation CSV row is invalid")
        _validate_csv_source(row, proposal.source)
        query = query_by_id.get(row["query_id"])
        candidate = faces.get(row["candidate_face_id"])
        if query is None or candidate is None:
            raise ValueError("annotation CSV references unknown IDs")
        if (
            row["query_face_id"] != query.query_face_id
            or row["query_filename"] != query.query_filename
        ):
            raise ValueError("annotation CSV query identity is invalid")
        if row["candidate_filename"] != candidate.filename:
            raise ValueError("annotation CSV candidate identity is invalid")
        annotations.append(
            Annotation(
                row["query_id"],
                row["candidate_face_id"],
                row["label"],
                row["note"] or None,
            )
        )
    values = tuple(annotations)
    validate_annotations(proposal, values)
    replacement = {(item.query_id, item.candidate_face_id): item for item in values}
    current.clear()
    current.update(replacement)
    return values


def _artifact_root(path: Path, expected_type: str, payload_name: str) -> Path:
    root = path.resolve()
    if not root.is_dir() or {child.name for child in root.iterdir()} != {
        "manifest.json",
        payload_name,
    }:
        raise ValueError("benchmark artifact files do not match schema")
    manifest = _load_json_object(root / "manifest.json")
    if set(manifest) != {"schema_version", "artifact_type", "source"}:
        raise ValueError("benchmark manifest schema is invalid")
    if manifest["schema_version"] != SCHEMA_VERSION or manifest["artifact_type"] != expected_type:
        raise ValueError("benchmark manifest schema is invalid")
    _source_from_payload(_mapping(manifest["source"]))
    return root


def _proposal_payload(proposal: BenchmarkProposal) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "queries": [_query_payload(query) for query in proposal.queries],
        "replacement_queries": [_query_payload(query) for query in proposal.replacement_queries],
        "faces": [_face_payload(face) for face in proposal.faces],
    }


def _final_payload(benchmark: FinalBenchmark) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "queries": [_query_payload(query) for query in benchmark.queries],
        "annotations": [_annotation_payload(annotation) for annotation in benchmark.annotations],
        "faces": [_face_payload(face) for face in benchmark.faces],
        "source": _source_payload(benchmark.source),
    }


def _source_payload(source: BenchmarkSource) -> dict[str, str]:
    return {
        "run_manifest_sha256": source.run_manifest_sha256,
        "faces_sha256": source.faces_sha256,
        "index_manifest_sha256": source.index_manifest_sha256,
        "proposal_sha256": source.proposal_sha256,
    }


def _face_payload(face: BenchmarkFace) -> dict[str, object]:
    return asdict(face)


def _query_payload(query: BenchmarkQuery) -> dict[str, object]:
    return {
        "query_id": query.query_id,
        "query_face_id": query.query_face_id,
        "query_filename": query.query_filename,
        "query_crop_path": query.query_crop_path,
        "proposed_cluster_id": query.proposed_cluster_id,
        "candidate_face_ids": list(query.candidate_face_ids),
        "split": query.split,
    }


def _annotation_payload(annotation: Annotation) -> dict[str, object]:
    return asdict(annotation)


def _source_from_payload(value: Mapping[str, object]) -> BenchmarkSource:
    if set(value) != {
        "run_manifest_sha256",
        "faces_sha256",
        "index_manifest_sha256",
        "proposal_sha256",
    }:
        raise ValueError("benchmark source schema is invalid")
    return BenchmarkSource(
        _string(value["run_manifest_sha256"]),
        _string(value["faces_sha256"]),
        _string(value["index_manifest_sha256"]),
        _string(value["proposal_sha256"]),
    )


def _faces_from_payload(value: object) -> tuple[BenchmarkFace, ...]:
    if not isinstance(value, list):
        raise ValueError("faces schema is invalid")
    expected = {
        "face_id",
        "filename",
        "crop_path",
        "crop_sha256",
        "cluster_id",
        "status",
        "confidence",
        "sharpness",
        "relative_area",
    }
    return tuple(
        BenchmarkFace(
            _string(item["face_id"]),
            _string(item["filename"]),
            _string(item["crop_path"]),
            _string(item["crop_sha256"]),
            _string(item["cluster_id"]),
            _string(item["status"]),
            _number(item["confidence"]),
            _number(item["sharpness"]),
            _number(item["relative_area"]),
        )
        for raw in value
        for item in (_require_schema(raw, expected),)
    )


def _queries_from_payload(value: object) -> tuple[BenchmarkQuery, ...]:
    if not isinstance(value, list):
        raise ValueError("queries schema is invalid")
    expected = {
        "query_id",
        "query_face_id",
        "query_filename",
        "query_crop_path",
        "proposed_cluster_id",
        "candidate_face_ids",
        "split",
    }
    values: list[BenchmarkQuery] = []
    for raw in value:
        item = _require_schema(raw, expected)
        candidates = item["candidate_face_ids"]
        if not isinstance(candidates, list) or any(
            not isinstance(face_id, str) for face_id in candidates
        ):
            raise ValueError("queries schema is invalid")
        split = _string(item["split"])
        if split not in {"calibration", "evaluation"}:
            raise ValueError("queries schema is invalid")
        values.append(
            BenchmarkQuery(
                _string(item["query_id"]),
                _string(item["query_face_id"]),
                _string(item["query_filename"]),
                _string(item["query_crop_path"]),
                _string(item["proposed_cluster_id"]),
                tuple(candidates),
                cast(Split, split),
            )
        )
    return tuple(values)


def _annotations_from_payload(value: object) -> tuple[Annotation, ...]:
    if not isinstance(value, list):
        raise ValueError("annotations schema is invalid")
    expected = {"query_id", "candidate_face_id", "label", "note"}
    values: list[Annotation] = []
    for raw in value:
        item = _require_schema(raw, expected)
        note = item["note"]
        if note is not None and not isinstance(note, str):
            raise ValueError("annotations schema is invalid")
        values.append(
            Annotation(
                _string(item["query_id"]),
                _string(item["candidate_face_id"]),
                _string(item["label"]),
                note,
            )
        )
    return tuple(values)


def _validate_csv_source(row: Mapping[str, str | None], source: BenchmarkSource) -> None:
    expected = {
        "schema_version": str(SCHEMA_VERSION),
        "source_run_manifest_sha256": source.run_manifest_sha256,
        "source_faces_sha256": source.faces_sha256,
        "index_manifest_sha256": source.index_manifest_sha256,
        "proposal_sha256": source.proposal_sha256,
    }
    if any(row[key] != value for key, value in expected.items()):
        raise ValueError("annotation CSV source identity is invalid")


def _write_json_atomic(path: Path, value: object) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _load_json_object(path: Path) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("benchmark JSON is invalid") from None
    return _mapping(value)


def _require_schema(value: object, expected: set[str]) -> Mapping[str, object]:
    item = _mapping(value)
    if set(item) != expected:
        raise ValueError("benchmark row schema is invalid")
    return item


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("expected JSON object")
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("expected number")
    return float(value)

from __future__ import annotations

import csv
import json
import math
import os
import shutil
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import quote, urlparse

from .comparison_report import render_people_comparison

_CSV_HEADERS = (
    "peakshot_person_id",
    "peakshot_photo_count",
    "matched_cluster_ids",
    "our_photo_count",
    "intersection_count",
    "missing_count",
    "extra_count",
    "precision",
    "recall",
)
_REFERENCE_HEADERS = ("person_id", "piece_id", "filename")
_FACE_HEADERS = (
    "face_id",
    "filename",
    "face_index",
    "x",
    "y",
    "width",
    "height",
    "confidence",
    "status",
    "error_code",
    "crop_path",
)
_CLUSTER_HEADERS = (
    "cluster_id",
    "representative_face_id",
    "face_id",
    "filename",
    "face_index",
    "distance_to_representative",
)
_RUN_FILES = (
    "manifest.json",
    "metrics.json",
    "faces.csv",
    "faces.json",
    "clusters.csv",
    "clusters.json",
    "report.html",
)
_RUN_DIRECTORIES = ("annotated", "people", "faces")
_MANIFEST_FIELDS = {
    "counts",
    "dependency_versions",
    "duration_seconds",
    "durations_seconds",
    "finished_at",
    "model_hashes",
    "parameters",
    "photo_materialization",
    "platform",
    "python_version",
    "started_at",
}
_METRICS_FIELDS = {
    "cluster_size_distribution",
    "counts",
    "face_error_counts",
    "image_error_counts",
}
_PARAMETER_FIELDS = {
    "cluster_threshold",
    "detection_threshold",
    "distance_block_size",
    "image_limit",
    "input_photos_basename",
    "max_image_dimension",
    "max_image_pixels",
    "min_face_px",
    "representative_threshold",
    "sface_model_filename",
    "yunet_model_filename",
}
_PEAKSHOT_METADATA_FIELDS = {
    "assignment_count",
    "captured_at",
    "event_url",
    "originals_directory",
    "people_count",
    "photo_count",
}


class ComparisonError(ValueError):
    """A fatal comparison input or publication error."""


@dataclass(frozen=True)
class ComparisonConfig:
    run: Path
    peakshot_export: Path
    output: Path

    def validate(self) -> None:
        output = self.output.resolve(strict=False)
        run = self.run.resolve(strict=False)
        reference = self.peakshot_export.resolve(strict=False)
        if _paths_intersect(output, run) or _paths_intersect(output, reference):
            raise ComparisonError("output path intersects input")
        if os.path.lexists(self.output):
            raise ComparisonError("output path already exists")


@dataclass(frozen=True)
class _Cluster:
    cluster_id: str
    filenames: tuple[str, ...]
    member_count: int


@dataclass(frozen=True)
class _Reference:
    people: Mapping[str, frozenset[str]]
    photos: Mapping[str, frozenset[str]]


class _Overlap(TypedDict):
    intersection_count: int
    jaccard: float
    peakshot_person_id: str


def run_comparison(config: ComparisonConfig) -> dict[str, Any]:
    """Evaluate a completed run without changing either source directory."""
    config.validate()
    clusters = _load_run(config.run)
    reference = _load_reference(config.peakshot_export)
    comparison, metrics, rows = _evaluate(clusters, reference)
    _publish(config, comparison, metrics, rows)
    return comparison


def _load_run(run: Path) -> tuple[_Cluster, ...]:
    if not run.is_dir():
        raise ComparisonError("completed run directory does not exist")
    if not all((run / name).is_file() for name in _RUN_FILES) or not all(
        (run / name).is_dir() for name in _RUN_DIRECTORIES
    ):
        raise ComparisonError("completed run is missing required artifacts")
    manifest = _load_json(run / "manifest.json", "run manifest")
    metrics = _load_json(run / "metrics.json", "run metrics")
    payload = _load_json(run / "clusters.json", "clusters")
    records = payload.get("clusters") if isinstance(payload, Mapping) else None
    if not isinstance(records, list):
        raise ComparisonError("clusters artifact is malformed")
    face_rows = _load_csv(run / "faces.csv", "faces", _FACE_HEADERS)
    faces, image_statuses = _load_faces(run, face_rows, _load_json(run / "faces.json", "faces"))
    clusters: list[_Cluster] = []
    seen_cluster_ids: set[str] = set()
    seen_member_ids: set[str] = set()
    cluster_members: dict[str, set[str]] = {}
    cluster_filenames: dict[str, set[str]] = {}
    expected_cluster_rows: set[tuple[str, str, str, str, int, str]] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise ComparisonError("cluster record is malformed")
        cluster_id = record.get("cluster_id")
        representative_face_id = record.get("representative_face_id")
        members = record.get("members")
        if not isinstance(cluster_id, str) or not _valid_cluster_id(cluster_id):
            raise ComparisonError("cluster ID is malformed")
        if (
            cluster_id in seen_cluster_ids
            or not isinstance(representative_face_id, str)
            or not isinstance(members, list)
            or not members
        ):
            raise ComparisonError("cluster membership is malformed")
        filenames: set[str] = set()
        member_ids: set[str] = set()
        for member in members:
            if not isinstance(member, Mapping):
                raise ComparisonError("cluster member is malformed")
            face_id = member.get("face_id")
            filename = member.get("filename")
            face_index = _positive_integer(member.get("face_index"))
            distance = _finite_number(member.get("distance_to_representative"))
            if (
                not isinstance(face_id, str)
                or not _valid_filename(filename)
                or face_index is None
                or distance is None
                or face_id not in faces
                or face_id in member_ids
                or face_id in seen_member_ids
            ):
                raise ComparisonError("cluster member is malformed")
            face = faces[face_id]
            if (
                face["filename"] != filename
                or face["face_index"] != face_index
                or face["status"] != "ok"
            ):
                raise ComparisonError("cluster member disagrees with faces artifact")
            member_ids.add(face_id)
            seen_member_ids.add(face_id)
            filenames.add(filename)
            expected_cluster_rows.add(
                (
                    cluster_id,
                    representative_face_id,
                    face_id,
                    filename,
                    face_index,
                    _task3_number(distance),
                )
            )
        if representative_face_id not in member_ids:
            raise ComparisonError("cluster representative is not a member")
        seen_cluster_ids.add(cluster_id)
        cluster_members[cluster_id] = member_ids
        cluster_filenames[cluster_id] = filenames
        clusters.append(_Cluster(cluster_id, tuple(sorted(filenames)), len(members)))
    expected_ids = {f"person-{index:04d}" for index in range(1, len(clusters) + 1)}
    if seen_cluster_ids != expected_ids or seen_member_ids != {
        face_id for face_id, face in faces.items() if face["status"] == "ok"
    }:
        raise ComparisonError("clusters do not reconcile with successful faces")
    _validate_cluster_csv(run / "clusters.csv", expected_cluster_rows)
    _validate_people_and_report(run, clusters, faces, cluster_members, cluster_filenames)
    _validate_run_counts(manifest, metrics, clusters, faces, face_rows, image_statuses)
    return tuple(sorted(clusters, key=lambda cluster: _cluster_sort_key(cluster.cluster_id)))


def _load_faces(
    run: Path, rows: Sequence[Mapping[str, str]], faces_payload: Any
) -> tuple[dict[str, dict[str, Any]], tuple[str, ...]]:
    faces: dict[str, dict[str, Any]] = {}
    for row in rows:
        face_id = row["face_id"]
        filename = row["filename"]
        face_index = _positive_integer(row["face_index"])
        crop_path = row["crop_path"]
        status = row["status"]
        if (
            not face_id
            or not _valid_filename(filename)
            or face_index is None
            or status not in {"ok", "alignment_failed", "embedding_failed", "invalid_embedding"}
            or not _valid_crop_path(crop_path)
            or face_id in faces
            or not (run / crop_path).is_file()
            or face_id != f"{filename}#face-{face_index:03d}"
        ):
            raise ComparisonError("faces artifact is malformed")
        faces[face_id] = {
            "filename": filename,
            "face_index": face_index,
            "status": status,
            "crop_path": crop_path,
        }
    images = faces_payload.get("images") if isinstance(faces_payload, Mapping) else None
    if not isinstance(images, list):
        raise ComparisonError("faces artifact is malformed")
    json_faces: dict[str, tuple[str, int, str, str]] = {}
    image_filenames: set[str] = set()
    image_statuses: list[str] = []
    for image in images:
        if not isinstance(image, Mapping) or not _valid_filename(image.get("filename")):
            raise ComparisonError("faces artifact is malformed")
        image_filename = str(image["filename"])
        if image_filename in image_filenames:
            raise ComparisonError("faces artifact is malformed")
        image_filenames.add(image_filename)
        image_status = image.get("status")
        if image_status not in {
            "ok",
            "no_detection",
            "image_decode_failed",
            "unsupported_image",
            "image_too_large",
            "detection_failed",
        }:
            raise ComparisonError("faces artifact is malformed")
        image_statuses.append(image_status)
        image_faces = image.get("faces")
        if not isinstance(image_faces, list):
            raise ComparisonError("faces artifact is malformed")
        for face in image_faces:
            if not isinstance(face, Mapping):
                raise ComparisonError("faces artifact is malformed")
            json_face_id = face.get("face_id")
            json_filename = face.get("filename")
            json_face_index = _positive_integer(face.get("face_index"))
            json_status = face.get("status")
            json_crop_path = face.get("crop_path")
            if (
                not isinstance(json_face_id, str)
                or not _valid_filename(json_filename)
                or json_face_index is None
                or not isinstance(json_status, str)
                or not isinstance(json_crop_path, str)
                or json_face_id in json_faces
                or json_filename != image_filename
            ):
                raise ComparisonError("faces artifact is malformed")
            json_faces[json_face_id] = (
                json_filename,
                json_face_index,
                json_status,
                json_crop_path,
            )
    expected_json_faces = {
        face_id: (face["filename"], face["face_index"], face["status"], face["crop_path"])
        for face_id, face in faces.items()
    }
    if json_faces != expected_json_faces:
        raise ComparisonError("faces CSV and JSON disagree")
    return faces, tuple(image_statuses)


def _validate_cluster_csv(
    path: Path, expected_rows: set[tuple[str, str, str, str, int, str]]
) -> None:
    rows = _load_csv(path, "clusters", _CLUSTER_HEADERS)
    actual_rows: set[tuple[str, str, str, str, int, str]] = set()
    for row in rows:
        face_index = _positive_integer(row["face_index"])
        distance = _finite_number(row["distance_to_representative"])
        if face_index is None or distance is None:
            raise ComparisonError("clusters artifact is malformed")
        item = (
            row["cluster_id"],
            row["representative_face_id"],
            row["face_id"],
            row["filename"],
            face_index,
            row["distance_to_representative"],
        )
        if item in actual_rows:
            raise ComparisonError("clusters artifact has duplicate members")
        actual_rows.add(item)
    if actual_rows != expected_rows:
        raise ComparisonError("clusters CSV and JSON disagree")


def _validate_people_and_report(
    run: Path,
    clusters: Sequence[_Cluster],
    faces: Mapping[str, Mapping[str, Any]],
    cluster_members: Mapping[str, set[str]],
    cluster_filenames: Mapping[str, set[str]],
) -> None:
    report = (run / "report.html").read_text(encoding="utf-8")
    for cluster in clusters:
        if f'id="{cluster.cluster_id}"' not in report:
            raise ComparisonError("cluster report is missing a stable anchor")
        face_dir = run / "people" / cluster.cluster_id / "faces"
        photo_dir = run / "people" / cluster.cluster_id / "photos"
        if not face_dir.is_dir() or not photo_dir.is_dir():
            raise ComparisonError("cluster review artifacts are incomplete")
        if any(
            not (face_dir / Path(str(faces[face_id]["crop_path"])).name).is_file()
            for face_id in cluster_members[cluster.cluster_id]
        ) or any(
            not (photo_dir / filename).is_file()
            for filename in cluster_filenames[cluster.cluster_id]
        ):
            raise ComparisonError("cluster review artifacts are incomplete")
    for face in faces.values():
        if not (run / face["crop_path"]).is_file():
            raise ComparisonError("face crop artifact is missing")


def _validate_run_counts(
    manifest: Any,
    metrics: Any,
    clusters: Sequence[_Cluster],
    faces: Mapping[str, Mapping[str, Any]],
    face_rows: Sequence[Mapping[str, str]],
    image_statuses: Sequence[str],
) -> None:
    if not isinstance(manifest, Mapping) or not isinstance(metrics, Mapping):
        raise ComparisonError("run manifest or metrics is malformed")
    if set(manifest) != _MANIFEST_FIELDS or set(metrics) != _METRICS_FIELDS:
        raise ComparisonError("run manifest or metrics has unexpected schema")
    finished_at = manifest.get("finished_at")
    started_at = manifest.get("started_at")
    model_hashes = manifest.get("model_hashes")
    if (
        not isinstance(finished_at, str)
        or not _valid_timestamp(finished_at)
        or not isinstance(started_at, str)
        or not _valid_timestamp(started_at)
    ):
        raise ComparisonError("run manifest completion timestamp is malformed")
    if (
        not isinstance(model_hashes, Mapping)
        or set(model_hashes) != {"sface", "yunet"}
        or any(
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
            for value in model_hashes.values()
        )
    ):
        raise ComparisonError("run manifest model hashes are malformed")
    if (
        not _nonnegative_number(manifest.get("duration_seconds"))
        or not _string_mapping(manifest.get("dependency_versions"))
        or not _number_mapping(manifest.get("durations_seconds"))
        or not isinstance(manifest.get("parameters"), Mapping)
        or not _exact_nonnegative_counts(
            manifest.get("photo_materialization"), {"copy", "hard_link"}
        )
        or not isinstance(manifest.get("platform"), str)
        or not manifest["platform"]
        or not isinstance(manifest.get("python_version"), str)
        or not manifest["python_version"]
        or not all(
            isinstance(value, (str, int, float, type(None)))
            for value in manifest["parameters"].values()
        )
        or not all(
            isinstance(value, Mapping)
            for value in (
                metrics["cluster_size_distribution"],
                metrics["face_error_counts"],
                metrics["image_error_counts"],
            )
        )
    ):
        raise ComparisonError("run manifest or metrics is malformed")
    expected = {
        "clusters": len(clusters),
        "embedding_success": sum(face["status"] == "ok" for face in faces.values()),
        "face_instances": len(face_rows),
        "images": len(image_statuses),
        "singleton_clusters": sum(cluster.member_count == 1 for cluster in clusters),
    }
    if manifest.get("counts") != expected or metrics.get("counts") != expected:
        raise ComparisonError("run manifest counts do not reconcile")
    if metrics["cluster_size_distribution"] != {
        str(size): count
        for size, count in sorted(Counter(cluster.member_count for cluster in clusters).items())
    }:
        raise ComparisonError("run metrics cluster size distribution does not reconcile")
    if metrics["face_error_counts"] != {
        status: count
        for status, count in sorted(
            Counter(face["status"] for face in faces.values() if face["status"] != "ok").items()
        )
    }:
        raise ComparisonError("run metrics face errors do not reconcile")
    if metrics["image_error_counts"] != {
        status: count
        for status, count in sorted(
            Counter(
                status for status in image_statuses if status not in {"ok", "no_detection"}
            ).items()
        )
    }:
        raise ComparisonError("run metrics image errors do not reconcile")
    _validate_manifest_parameters(manifest["parameters"])


def _load_reference(export: Path) -> _Reference:
    if not export.is_dir():
        raise ComparisonError("Peakshot export directory does not exist")
    csv_path = export / "peakshot-person-photo-map.csv"
    people_path = export / "peakshot-people.json"
    photos_path = export / "peakshot-photos.json"
    metadata_path = export / "metadata.json"
    if not (
        csv_path.is_file()
        and people_path.is_file()
        and photos_path.is_file()
        and metadata_path.is_file()
    ):
        raise ComparisonError("Peakshot export is missing required artifacts")
    try:
        with csv_path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != _REFERENCE_HEADERS:
                raise ComparisonError("Peakshot assignment CSV has unexpected headers")
            rows = list(reader)
    except (OSError, csv.Error) as error:
        raise ComparisonError("Peakshot assignment CSV is malformed") from error
    if not rows:
        raise ComparisonError("Peakshot assignment CSV is malformed")
    people: dict[str, set[str]] = {}
    assignments: set[tuple[str, str]] = set()
    for row in rows:
        person_id = row.get("person_id")
        piece_id = row.get("piece_id")
        filename = row.get("filename")
        if (
            not isinstance(person_id, str)
            or not _valid_peakshot_id(person_id)
            or not isinstance(piece_id, str)
            or not _valid_peakshot_id(piece_id)
        ):
            raise ComparisonError("Peakshot assignment row is malformed")
        if not isinstance(filename, str) or not _valid_filename(filename):
            raise ComparisonError("Peakshot assignment row is malformed")
        if (person_id, filename) in assignments:
            raise ComparisonError("Peakshot assignment row is malformed")
        assignments.add((person_id, filename))
        people.setdefault(person_id, set()).add(filename)
    photos_payload = _load_json(photos_path, "Peakshot photos")
    if not isinstance(photos_payload, Mapping):
        raise ComparisonError("Peakshot photos artifact is malformed")
    photos: dict[str, frozenset[str]] = {}
    for filename, record in photos_payload.items():
        if not _valid_filename(filename) or not isinstance(record, Mapping):
            raise ComparisonError("Peakshot photos artifact is malformed")
        photo_piece_id = record.get("piece_id")
        person_ids = record.get("person_ids")
        if (
            not isinstance(photo_piece_id, str)
            or not _valid_peakshot_id(photo_piece_id)
            or not isinstance(person_ids, list)
            or any(not isinstance(item, str) or not _valid_peakshot_id(item) for item in person_ids)
            or person_ids != sorted(person_ids, key=int)
        ):
            raise ComparisonError("Peakshot photos artifact is malformed")
        if len(set(person_ids)) != len(person_ids):
            raise ComparisonError("Peakshot photos artifact is malformed")
        photos[str(filename)] = frozenset(person_ids)
    if set(filename for _, filename in assignments) - set(photos):
        raise ComparisonError("Peakshot assignments reference unknown photos")
    if len({record.get("piece_id") for record in photos_payload.values()}) != len(photos):
        raise ComparisonError("Peakshot photo piece IDs are not unique")
    for row in rows:
        filename = row["filename"]
        if row["piece_id"] != photos_payload[filename].get("piece_id"):
            raise ComparisonError("Peakshot assignment piece ID disagrees with photos artifact")
    photo_assignments = {
        (person_id, filename) for filename, person_ids in photos.items() for person_id in person_ids
    }
    if photo_assignments != assignments:
        raise ComparisonError("Peakshot assignments disagree with photos artifact")
    if any(
        people[person_id] != {name for name, ids in photos.items() if person_id in ids}
        for person_id in people
    ):
        raise ComparisonError("Peakshot assignments disagree with photos artifact")
    people_payload = _load_json(people_path, "Peakshot people")
    if not isinstance(people_payload, Mapping):
        raise ComparisonError("Peakshot people artifact is malformed")
    exported_people: dict[str, frozenset[str]] = {}
    for person_id, filenames in people_payload.items():
        if (
            not isinstance(person_id, str)
            or not _valid_peakshot_id(person_id)
            or not isinstance(filenames, list)
            or not filenames
            or filenames != sorted(filenames)
            or len(set(filenames)) != len(filenames)
            or any(not _valid_filename(filename) for filename in filenames)
        ):
            raise ComparisonError("Peakshot people artifact is malformed")
        exported_people[person_id] = frozenset(filenames)
    expected_people = {person_id: frozenset(filenames) for person_id, filenames in people.items()}
    if exported_people != expected_people:
        raise ComparisonError("Peakshot people artifact disagrees with assignments")
    metadata = _load_json(metadata_path, "Peakshot metadata")
    expected_counts = {
        "assignment_count": len(assignments),
        "people_count": len(people),
        "photo_count": len(photos),
    }
    if (
        not isinstance(metadata, Mapping)
        or set(metadata) != _PEAKSHOT_METADATA_FIELDS
        or any(metadata.get(key) != value for key, value in expected_counts.items())
        or not _valid_aware_timestamp(metadata.get("captured_at"))
        or not _valid_peakshot_event_url(metadata.get("event_url"))
        or not isinstance(metadata.get("originals_directory"), str)
        or not Path(metadata["originals_directory"]).is_absolute()
    ):
        raise ComparisonError("Peakshot metadata counts are malformed")
    return _Reference(
        people={person_id: frozenset(people[person_id]) for person_id in sorted(people)},
        photos={filename: photos[filename] for filename in sorted(photos)},
    )


def _evaluate(
    clusters: Sequence[_Cluster], reference: _Reference
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    assignments: list[dict[str, Any]] = []
    primary_clusters: dict[str, list[_Cluster]] = {person_id: [] for person_id in reference.people}
    unmatched_clusters: list[dict[str, Any]] = []
    merged_clusters: list[dict[str, Any]] = []
    purity_total = 0
    run_filenames: set[str] = set()
    for cluster in sorted(clusters, key=lambda item: _cluster_sort_key(item.cluster_id)):
        cluster_filenames = frozenset(cluster.filenames)
        run_filenames.update(cluster_filenames)
        overlaps: list[_Overlap] = [
            {
                "intersection_count": len(cluster_filenames & filenames),
                "jaccard": _ratio(
                    len(cluster_filenames & filenames), len(cluster_filenames | filenames)
                ),
                "peakshot_person_id": person_id,
            }
            for person_id, filenames in reference.people.items()
            if cluster_filenames & filenames
        ]
        overlaps.sort(
            key=lambda item: (
                -item["intersection_count"],
                -item["jaccard"],
                item["peakshot_person_id"],
            )
        )
        primary_person_id = overlaps[0]["peakshot_person_id"] if overlaps else None
        if primary_person_id is None:
            unmatched_clusters.append(
                {"cluster_id": cluster.cluster_id, "photo_count": len(cluster_filenames)}
            )
        else:
            primary_clusters[primary_person_id].append(cluster)
            purity_total += int(overlaps[0]["intersection_count"])
        if len(overlaps) > 1:
            merged_clusters.append(
                {
                    "cluster_id": cluster.cluster_id,
                    "overlapping_person_ids": [item["peakshot_person_id"] for item in overlaps],
                }
            )
        assignments.append(
            {
                "cluster_id": cluster.cluster_id,
                "member_count": cluster.member_count,
                "overlaps": overlaps,
                "photo_count": len(cluster_filenames),
                "primary_person_id": primary_person_id,
            }
        )
    rows: list[dict[str, Any]] = []
    fragmented_people: list[dict[str, Any]] = []
    relationship_intersection = relationship_expected = relationship_actual = 0
    for person_id in sorted(reference.people):
        expected = reference.people[person_id]
        matched = sorted(
            primary_clusters[person_id], key=lambda item: _cluster_sort_key(item.cluster_id)
        )
        actual = frozenset(filename for cluster in matched for filename in cluster.filenames)
        intersection = len(expected & actual)
        relationship_intersection += intersection
        relationship_expected += len(expected)
        relationship_actual += len(actual)
        if len(matched) > 1:
            fragmented_people.append(
                {
                    "cluster_ids": [cluster.cluster_id for cluster in matched],
                    "peakshot_person_id": person_id,
                }
            )
        rows.append(
            {
                "peakshot_person_id": person_id,
                "peakshot_photo_count": len(expected),
                "matched_cluster_ids": ";".join(cluster.cluster_id for cluster in matched),
                "our_photo_count": len(actual),
                "intersection_count": intersection,
                "missing_count": len(expected - actual),
                "extra_count": len(actual - expected),
                "precision": _ratio(intersection, len(actual)) if actual else None,
                "recall": _ratio(intersection, len(expected)),
            }
        )
    singleton_clusters = [cluster for cluster in clusters if cluster.member_count == 1]
    unmatched_ids = {item["cluster_id"] for item in unmatched_clusters}
    group_photos = [
        filename for filename, person_ids in reference.photos.items() if len(person_ids) > 1
    ]
    precision = (
        _ratio(relationship_intersection, relationship_actual) if relationship_actual else None
    )
    recall = (
        _ratio(relationship_intersection, relationship_expected) if relationship_expected else None
    )
    metrics = {
        "cluster_purity": _ratio(purity_total, sum(len(cluster.filenames) for cluster in clusters))
        if clusters
        else None,
        "fragmented_people": fragmented_people,
        "group_photos": {
            "count": len(group_photos),
            "in_run": sum(name in run_filenames for name in group_photos),
        },
        "merged_clusters": merged_clusters,
        "people": {
            "matched": sum(bool(primary_clusters[person_id]) for person_id in reference.people),
            "missed": sum(not primary_clusters[person_id] for person_id in reference.people),
            "total": len(reference.people),
        },
        "relationship_metrics": {
            "f1": _f1_counts(relationship_intersection, relationship_actual, relationship_expected),
            "precision": precision,
            "recall": recall,
        },
        "singleton_clusters": {
            "count": len(singleton_clusters),
            "matched": sum(
                cluster.cluster_id not in unmatched_ids for cluster in singleton_clusters
            ),
            "unmatched": sum(cluster.cluster_id in unmatched_ids for cluster in singleton_clusters),
        },
        "clusters": {
            "matched": len(clusters) - len(unmatched_clusters),
            "unmatched": len(unmatched_clusters),
            "total": len(clusters),
        },
    }
    comparison = {
        "cluster_alignment": assignments,
        "inventory": {
            "peakshot_only": sorted(set(reference.photos) - run_filenames),
            "run_only": sorted(run_filenames - set(reference.photos)),
        },
        "people": rows,
        "unmatched_clusters": unmatched_clusters,
    }
    return comparison, metrics, rows


def _publish(
    config: ComparisonConfig,
    comparison: Mapping[str, Any],
    metrics: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    config.output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{config.output.name}.", dir=config.output.parent))
    try:
        _write_json(staging / "comparison.json", comparison)
        _write_json(staging / "metrics.json", metrics)
        _write_csv(staging / "people-comparison.csv", rows)
        href = _cluster_href(config.run, config.output)
        (staging / "people-comparison.html").write_text(
            render_people_comparison(rows, comparison["unmatched_clusters"], href), encoding="utf-8"
        )
        _write_json(
            staging / "manifest.json",
            {
                "artifacts": [
                    "comparison.json",
                    "metrics.json",
                    "people-comparison.csv",
                    "people-comparison.html",
                ],
                "counts": {"clusters": metrics["clusters"]["total"], "peakshot_people": len(rows)},
                "peakshot_export_basename": config.peakshot_export.name,
                "run_basename": config.run.name,
            },
        )
        if os.path.lexists(config.output):
            raise ComparisonError("output path already exists")
        os.replace(staging, config.output)
    except BaseException as error:
        try:
            shutil.rmtree(staging)
        except BaseException as cleanup_error:
            error.add_note(f"could not remove comparison staging directory: {cleanup_error}")
        raise


def _cluster_href(run: Path, output: Path) -> Any:
    relative = os.path.relpath(run / "report.html", output)
    encoded = quote(relative, safe="/-._~")
    return lambda cluster_id: f"{encoded}#{quote(cluster_id, safe='-._~')}"


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=_CSV_HEADERS, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(
            {header: _csv_value(row[header]) for header in _CSV_HEADERS} for row in rows
        )


def _load_json(path: Path, name: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ComparisonError(f"{name} artifact is malformed") from error


def _load_csv(path: Path, name: str, headers: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) != headers:
                raise ComparisonError(f"{name} artifact has unexpected headers")
            rows = list(reader)
    except (OSError, csv.Error) as error:
        raise ComparisonError(f"{name} artifact is malformed") from error
    if any(
        set(row) != set(headers) or any(value is None for value in row.values()) for row in rows
    ):
        raise ComparisonError(f"{name} artifact is malformed")
    return rows


def _valid_filename(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and "\x00" not in value
        and not Path(value).is_absolute()
        and Path(value).name == value
        and value not in {".", ".."}
    )


def _valid_crop_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    path = Path(value)
    return not path.is_absolute() and path.parts[:1] == ("faces",) and len(path.parts) == 2


def _valid_cluster_id(value: str) -> bool:
    if not value.startswith("person-"):
        return False
    suffix = value[7:]
    return (
        len(suffix) >= 4
        and suffix.isdigit()
        and int(suffix) >= 1
        and suffix == f"{int(suffix):04d}"
    )


def _cluster_sort_key(cluster_id: str) -> int:
    if not _valid_cluster_id(cluster_id):
        raise ComparisonError("cluster ID is malformed")
    return int(cluster_id[7:])


def _valid_peakshot_id(value: str) -> bool:
    return value.isdigit() and int(value) >= 1 and value == str(int(value))


def _positive_integer(value: object) -> int | None:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return None
    return number if number > 0 and str(value) == str(number) else None


def _finite_number(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _nonnegative_number(value: object) -> bool:
    number = _finite_number(value)
    return number is not None and number >= 0


def _string_mapping(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and bool(value)
        and all(
            isinstance(key, str) and key and isinstance(item, str) and item
            for key, item in value.items()
        )
    )


def _number_mapping(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and bool(value)
        and all(
            isinstance(key, str) and key and _nonnegative_number(item)
            for key, item in value.items()
        )
    )


def _exact_nonnegative_counts(value: object, keys: set[str]) -> bool:
    return (
        isinstance(value, Mapping)
        and set(value) == keys
        and all(isinstance(item, int) and item >= 0 for item in value.values())
    )


def _validate_manifest_parameters(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != _PARAMETER_FIELDS:
        raise ComparisonError("run manifest parameters are malformed")
    detection_threshold = _finite_number(value.get("detection_threshold"))
    cluster_threshold = _finite_number(value.get("cluster_threshold"))
    representative_threshold = _finite_number(value.get("representative_threshold"))
    image_limit = value.get("image_limit")
    if (
        detection_threshold is None
        or not 0 <= detection_threshold <= 1
        or cluster_threshold is None
        or not 0 <= cluster_threshold <= 2
        or representative_threshold is None
        or not 0 <= representative_threshold <= 2
        or not _positive_integer(value.get("distance_block_size"))
        or not _positive_integer(value.get("max_image_dimension"))
        or not _positive_integer(value.get("max_image_pixels"))
        or not _positive_integer(value.get("min_face_px"))
        or (image_limit is not None and not _positive_integer(image_limit))
        or not _valid_filename(value.get("input_photos_basename"))
        or not _valid_filename(value.get("sface_model_filename"))
        or not _valid_filename(value.get("yunet_model_filename"))
    ):
        raise ComparisonError("run manifest parameters are malformed")


def _valid_timestamp(value: str) -> bool:
    if not value.endswith("Z"):
        return False
    return _valid_aware_timestamp(value)


def _valid_aware_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _valid_peakshot_event_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc) and bool(parsed.path)


def _paths_intersect(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator


def _f1_counts(intersection: int, actual: int, expected: int) -> float | None:
    if actual + expected == 0:
        return None
    return 2 * intersection / (actual + expected)


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, float):
        return format(value, ".12g")
    return value


def _task3_number(value: float) -> str:
    return format(value, ".12g")

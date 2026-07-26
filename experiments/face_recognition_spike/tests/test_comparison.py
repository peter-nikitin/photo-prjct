from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
from face_spike import comparison as comparison_module
from face_spike.comparison import ComparisonConfig, ComparisonError, run_comparison


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _task3_csv_distance(value: object) -> str:
    return format(float(value), ".12g")


def _run(path: Path, clusters: list[tuple[str, list[str]]]) -> Path:
    path.mkdir()
    face_index_by_filename: dict[str, int] = {}
    cluster_records: list[dict[str, object]] = []
    face_records: list[dict[str, object]] = []
    cluster_rows: list[dict[str, object]] = []
    for cluster_id, filenames in clusters:
        members: list[dict[str, object]] = []
        for filename in filenames:
            face_index = face_index_by_filename.get(filename, 0) + 1
            face_index_by_filename[filename] = face_index
            face_id = f"{filename}#face-{face_index:03d}"
            crop_path = f"faces/{hashlib.sha256(face_id.encode()).hexdigest()}.png"
            members.append(
                {
                    "face_id": face_id,
                    "filename": filename,
                    "face_index": face_index,
                    "distance_to_representative": 0.0,
                }
            )
            face_records.append(
                {
                    "face_id": face_id,
                    "filename": filename,
                    "face_index": face_index,
                    "status": "ok",
                    "crop_path": crop_path,
                }
            )
        representative_face_id = str(members[0]["face_id"])
        cluster_records.append(
            {
                "cluster_id": cluster_id,
                "representative_face_id": representative_face_id,
                "members": members,
            }
        )
        cluster_rows.extend(
            {
                "cluster_id": cluster_id,
                "representative_face_id": representative_face_id,
                **member,
            }
            for member in members
        )
    _write_json(
        path / "clusters.json",
        {"clusters": cluster_records},
    )
    face_headers = (
        "face_id,filename,face_index,x,y,width,height,confidence,minimum_side_px,"
        "relative_area,sharpness,quality_decision,quality_reasons,status,error_code,crop_path\n"
    )
    (path / "faces.csv").write_text(
        face_headers
        + "".join(
            f"{face['face_id']},{face['filename']},{face['face_index']},"
            f"0,0,1,1,1,1,0.01,100,accepted,,ok,,{face['crop_path']}\n"
            for face in face_records
        ),
        encoding="utf-8",
    )
    _write_json(
        path / "faces.json",
        {
            "images": [
                {
                    "filename": filename,
                    "status": "ok",
                    "faces": [face for face in face_records if face["filename"] == filename],
                }
                for filename in sorted(face_index_by_filename)
            ]
        },
    )
    (path / "clusters.csv").write_text(
        "cluster_id,representative_face_id,face_id,filename,face_index,distance_to_representative\n"
        + "".join(
            f"{row['cluster_id']},{row['representative_face_id']},{row['face_id']},"
            f"{row['filename']},{row['face_index']},"
            f"{_task3_csv_distance(row['distance_to_representative'])}\n"
            for row in cluster_rows
        ),
        encoding="utf-8",
    )
    counts = {
        "clusters": len(clusters),
        "embedding_success": len(face_records),
        "face_instances": len(face_records),
        "images": len(face_index_by_filename),
        "quality_accepted": len(face_records),
        "quality_rejected": 0,
        "singleton_clusters": sum(len(filenames) == 1 for _, filenames in clusters),
    }
    _write_json(
        path / "metrics.json",
        {
            "cluster_size_distribution": {
                str(size): sum(len(filenames) == size for _, filenames in clusters)
                for size in sorted({len(filenames) for _, filenames in clusters})
            },
            "counts": counts,
            "face_error_counts": {},
            "image_error_counts": {},
            "quality_rejection_reasons": {},
        },
    )
    _write_json(
        path / "manifest.json",
        {
            "counts": counts,
            "dependency_versions": {"numpy": "1", "opencv": "1", "pillow": "1"},
            "duration_seconds": 1.0,
            "durations_seconds": {"clustering": 0.5, "decode_detection_embedding": 0.5},
            "finished_at": "2026-07-26T12:00:00Z",
            "model_hashes": {"sface": "a" * 64, "yunet": "b" * 64},
            "parameters": {
                "cluster_threshold": 0.363,
                "detection_threshold": 0.75,
                "distance_block_size": 512,
                "image_limit": None,
                "input_photos_basename": "photos",
                "max_image_dimension": 12000,
                "max_image_pixels": 100_000_000,
                "min_face_px": 32,
                "minimum_face_sharpness": 50.0,
                "minimum_quality_confidence": 0.82,
                "minimum_relative_face_area": 0.0009,
                "representative_threshold": 0.363,
                "sface_model_filename": "sface.onnx",
                "yunet_model_filename": "yunet.onnx",
            },
            "photo_materialization": {"copy": 0, "hard_link": len(face_records)},
            "platform": "macOS",
            "python_version": "3.12",
            "started_at": "2026-07-26T11:59:59Z",
        },
    )
    (path / "annotated").mkdir()
    (path / "faces").mkdir()
    people = path / "people"
    people.mkdir()
    for face in face_records:
        (path / str(face["crop_path"])).write_bytes(b"crop")
    for cluster in cluster_records:
        cluster_id = str(cluster["cluster_id"])
        face_dir = people / cluster_id / "faces"
        photo_dir = people / cluster_id / "photos"
        face_dir.mkdir(parents=True)
        photo_dir.mkdir()
        for member in cluster["members"]:
            face = next(item for item in face_records if item["face_id"] == member["face_id"])
            (face_dir / Path(str(face["crop_path"])).name).write_bytes(b"crop")
            (photo_dir / str(member["filename"])).write_bytes(b"photo")
    (path / "report.html").write_text(
        "".join(f'<section id="{cluster_id}"></section>' for cluster_id, _ in clusters),
        encoding="utf-8",
    )
    return path


def _reference(path: Path, people: dict[str, list[str]], photos: set[str] | None = None) -> Path:
    path.mkdir()
    photos = set(photos or set().union(*map(set, people.values())))
    piece_ids = {filename: str(index) for index, filename in enumerate(sorted(photos), start=1)}
    with (path / "peakshot-person-photo-map.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(("person_id", "piece_id", "filename"))
        for person_id, filenames in people.items():
            for filename in filenames:
                writer.writerow((person_id, piece_ids[filename], filename))
    _write_json(
        path / "peakshot-photos.json",
        {
            filename: {
                "piece_id": piece_ids[filename],
                "person_ids": sorted(
                    person_id for person_id, filenames in people.items() if filename in filenames
                ),
            }
            for filename in sorted(photos)
        },
    )
    _write_json(path / "peakshot-people.json", people)
    _write_json(
        path / "metadata.json",
        {
            "people_count": len(people),
            "photo_count": len(photos),
            "assignment_count": sum(len(filenames) for filenames in people.values()),
            "captured_at": "2026-07-26T12:00:00+00:00",
            "event_url": "https://peakshot.example/event",
            "originals_directory": "/photo-refs/all",
        },
    )
    return path


def test_comparison_aligns_deterministically_and_reports_relationship_evidence(
    tmp_path: Path,
) -> None:
    run = _run(
        tmp_path / "run",
        [
            ("person-0002", ["a.jpg"]),
            ("person-0001", ["b.jpg", "c.jpg"]),
            ("person-0003", ["z.jpg"]),
        ],
    )
    reference = _reference(
        tmp_path / "reference",
        {"10": ["a.jpg", "b.jpg"], "20": ["b.jpg", "c.jpg"], "30": ["missing.jpg"]},
        {"a.jpg", "b.jpg", "c.jpg", "missing.jpg", "unassigned.jpg"},
    )
    output = tmp_path / "comparison"

    run_comparison(ComparisonConfig(run, reference, output))

    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    rows = list(csv.DictReader((output / "people-comparison.csv").open(encoding="utf-8")))
    assert [*rows[0]][:4] == [
        "peakshot_person_id",
        "peakshot_photo_count",
        "matched_cluster_ids",
        "our_photo_count",
    ]
    assert rows == [
        {
            "peakshot_person_id": "10",
            "peakshot_photo_count": "2",
            "matched_cluster_ids": "person-0002",
            "our_photo_count": "1",
            "intersection_count": "1",
            "missing_count": "1",
            "extra_count": "0",
            "precision": "1",
            "recall": "0.5",
        },
        {
            "peakshot_person_id": "20",
            "peakshot_photo_count": "2",
            "matched_cluster_ids": "person-0001",
            "our_photo_count": "2",
            "intersection_count": "2",
            "missing_count": "0",
            "extra_count": "0",
            "precision": "1",
            "recall": "1",
        },
        {
            "peakshot_person_id": "30",
            "peakshot_photo_count": "1",
            "matched_cluster_ids": "",
            "our_photo_count": "0",
            "intersection_count": "0",
            "missing_count": "1",
            "extra_count": "0",
            "precision": "",
            "recall": "0",
        },
    ]
    assert comparison["inventory"] == {
        "peakshot_only": ["missing.jpg", "unassigned.jpg"],
        "run_only": ["z.jpg"],
    }
    assert comparison["unmatched_clusters"] == [{"cluster_id": "person-0003", "photo_count": 1}]
    assert metrics["fragmented_people"] == []
    assert metrics["merged_clusters"] == [
        {"cluster_id": "person-0001", "overlapping_person_ids": ["20", "10"]}
    ]
    assert metrics["singleton_clusters"] == {"count": 2, "matched": 1, "unmatched": 1}
    assert metrics["group_photos"] == {"count": 1, "in_run": 1}
    assert metrics["relationship_metrics"] == {
        "f1": 0.75,
        "precision": 1.0,
        "recall": 0.6,
    }
    html = (output / "people-comparison.html").read_text(encoding="utf-8")
    assert 'href="../run/report.html#person-0001"' in html
    assert "unmatched clusters" in html
    assert str(tmp_path) not in "\n".join(
        path.read_text(encoding="utf-8") for path in output.iterdir()
    )


def test_comparison_reports_fragmentation_and_tie_breaks_person_id(tmp_path: Path) -> None:
    run = _run(
        tmp_path / "run",
        [
            ("person-0003", ["a.jpg"]),
            ("person-0002", ["b.jpg"]),
            ("person-0001", ["b.jpg"]),
        ],
    )
    reference = _reference(
        tmp_path / "reference",
        {"20": ["a.jpg", "b.jpg"], "10": ["a.jpg", "c.jpg"]},
    )
    output = tmp_path / "comparison"

    run_comparison(ComparisonConfig(run, reference, output))

    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert comparison["cluster_alignment"][2]["primary_person_id"] == "10"
    assert metrics["fragmented_people"] == [
        {"cluster_ids": ["person-0001", "person-0002"], "peakshot_person_id": "20"}
    ]
    person_20 = next(row for row in comparison["people"] if row["peakshot_person_id"] == "20")
    assert person_20["our_photo_count"] == 1


def test_comparison_keeps_case_sensitive_inventory_mismatches_visible(tmp_path: Path) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["face.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["FACE.jpg"]})
    output = tmp_path / "comparison"

    run_comparison(ComparisonConfig(run, reference, output))

    comparison = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert comparison["inventory"] == {
        "peakshot_only": ["FACE.jpg"],
        "run_only": ["face.jpg"],
    }


def test_comparison_accepts_task3_dot_12g_cluster_distance_serialization(tmp_path: Path) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["a.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg"]})
    distance = 0.12345678901234567
    clusters = json.loads((run / "clusters.json").read_text(encoding="utf-8"))
    clusters["clusters"][0]["members"][0]["distance_to_representative"] = distance
    _write_json(run / "clusters.json", clusters)
    with (run / "clusters.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    rows[0]["distance_to_representative"] = format(distance, ".12g")
    with (run / "clusters.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    run_comparison(ComparisonConfig(run, reference, tmp_path / "comparison"))


def test_comparison_uses_numeric_cluster_order_across_five_digit_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = tmp_path / "run"
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg", "b.jpg"]})
    monkeypatch.setattr(
        comparison_module,
        "_load_run",
        lambda _run: (
            comparison_module._Cluster("person-10000", ("b.jpg",), 1),
            comparison_module._Cluster("person-9999", ("a.jpg",), 1),
        ),
    )

    run_comparison(ComparisonConfig(run, reference, tmp_path / "comparison"))

    payload = json.loads((tmp_path / "comparison" / "comparison.json").read_text(encoding="utf-8"))
    assert comparison_module._valid_cluster_id("person-10000")
    assert not comparison_module._valid_cluster_id("person-999")
    assert [row["cluster_id"] for row in payload["cluster_alignment"]] == [
        "person-9999",
        "person-10000",
    ]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda run, reference: (run / "clusters.json").write_text("{}", encoding="utf-8"),
        lambda run, reference: (reference / "metadata.json").write_text("{}", encoding="utf-8"),
    ],
)
def test_comparison_rejects_malformed_inputs_without_publication(
    tmp_path: Path, mutate: object
) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["a.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg"]})
    mutate(run, reference)
    output = tmp_path / "comparison"

    with pytest.raises(ComparisonError):
        run_comparison(ComparisonConfig(run, reference, output))

    assert not output.exists()
    assert not list(tmp_path.glob(".comparison.*"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda run: (run / "faces.csv").unlink(),
        lambda run: _write_json(
            run / "manifest.json",
            {"counts": {"clusters": 99}, "finished_at": "not-a-timestamp", "model_hashes": {}},
        ),
        lambda run: (run / "clusters.csv").write_text("cluster_id\n", encoding="utf-8"),
    ],
)
def test_comparison_rejects_incomplete_or_corrupt_completed_run(
    tmp_path: Path, mutate: object
) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["a.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg"]})
    mutate(run)
    output = tmp_path / "comparison"

    with pytest.raises(ComparisonError, match="completed run|manifest|clusters"):
        run_comparison(ComparisonConfig(run, reference, output))

    assert not output.exists()


@pytest.mark.parametrize(
    "corruption", ["parameters", "distribution", "face_errors", "image_errors"]
)
def test_comparison_reconciles_task3_manifest_parameters_and_nested_metrics(
    tmp_path: Path, corruption: str
) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["a.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg"]})
    if corruption == "parameters":
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        del manifest["parameters"]["cluster_threshold"]
        _write_json(run / "manifest.json", manifest)
    else:
        metrics = json.loads((run / "metrics.json").read_text(encoding="utf-8"))
        metrics[
            {
                "distribution": "cluster_size_distribution",
                "face_errors": "face_error_counts",
                "image_errors": "image_error_counts",
            }[corruption]
        ] = {"wrong": 1}
        _write_json(run / "metrics.json", metrics)

    with pytest.raises(ComparisonError, match="manifest|metrics"):
        run_comparison(ComparisonConfig(run, reference, tmp_path / "comparison"))


@pytest.mark.parametrize("field", ["captured_at", "event_url", "originals_directory"])
def test_comparison_rejects_peakshot_metadata_missing_exporter_contract_fields(
    tmp_path: Path, field: str
) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["a.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg"]})
    metadata = json.loads((reference / "metadata.json").read_text(encoding="utf-8"))
    del metadata[field]
    _write_json(reference / "metadata.json", metadata)

    with pytest.raises(ComparisonError, match="Peakshot metadata"):
        run_comparison(ComparisonConfig(run, reference, tmp_path / "comparison"))


@pytest.mark.parametrize("corruption", ["missing", "wrong_filename", "extra_person"])
def test_comparison_requires_and_reconciles_peakshot_people_export(
    tmp_path: Path, corruption: str
) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["a.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg"]})
    people_path = reference / "peakshot-people.json"
    if corruption == "missing":
        people_path.unlink()
    else:
        people = json.loads(people_path.read_text(encoding="utf-8"))
        if corruption == "wrong_filename":
            people["10"] = ["other.jpg"]
        else:
            people["11"] = ["a.jpg"]
        _write_json(people_path, people)

    with pytest.raises(ComparisonError, match="Peakshot"):
        run_comparison(ComparisonConfig(run, reference, tmp_path / "comparison"))


@pytest.mark.parametrize("corruption", ["piece", "extra_person"])
def test_comparison_rejects_peakshot_piece_and_person_integrity_mismatches(
    tmp_path: Path, corruption: str
) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["a.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg"]})
    if corruption == "piece":
        with (reference / "peakshot-person-photo-map.csv").open(
            newline="", encoding="utf-8"
        ) as stream:
            rows = list(csv.reader(stream))
        rows[1][1] = "wrong-piece"
        with (reference / "peakshot-person-photo-map.csv").open(
            "w", newline="", encoding="utf-8"
        ) as stream:
            csv.writer(stream).writerows(rows)
    else:
        photos = json.loads((reference / "peakshot-photos.json").read_text(encoding="utf-8"))
        photos["a.jpg"]["person_ids"].append("orphan")
        _write_json(reference / "peakshot-photos.json", photos)
    output = tmp_path / "comparison"

    with pytest.raises(ComparisonError, match="Peakshot"):
        run_comparison(ComparisonConfig(run, reference, output))

    assert not output.exists()


@pytest.mark.parametrize(
    ("input_name", "nested"),
    [("run", True), ("reference", True), ("run", False), ("reference", False)],
)
def test_comparison_rejects_output_inside_an_input_before_writing(
    tmp_path: Path, input_name: str, nested: bool
) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["a.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg"]})
    input_path = run if input_name == "run" else reference
    output = input_path / "comparison" if nested else input_path
    before = {
        path.relative_to(input_path): path.read_bytes()
        for path in input_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ComparisonError, match="output path intersects input"):
        run_comparison(ComparisonConfig(run, reference, output))

    after = {
        path.relative_to(input_path): path.read_bytes()
        for path in input_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    if nested:
        assert not output.exists()
        assert not list(output.parent.glob(".comparison.*"))


def test_comparison_rejects_existing_output_and_cleans_staging_on_publication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["a.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg"]})
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(ComparisonError, match="output path already exists"):
        run_comparison(ComparisonConfig(run, reference, existing))

    output = tmp_path / "comparison"
    monkeypatch.setattr(
        "face_spike.comparison.os.replace", lambda *_: (_ for _ in ()).throw(OSError("boom"))
    )
    with pytest.raises(OSError, match="boom"):
        run_comparison(ComparisonConfig(run, reference, output))
    assert not output.exists()
    assert not list(tmp_path.glob(".comparison.*"))


def test_comparison_reports_staging_cleanup_failure_without_hiding_publication_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["a.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg"]})
    output = tmp_path / "comparison"
    monkeypatch.setattr(
        "face_spike.comparison.os.replace", lambda *_: (_ for _ in ()).throw(OSError("publish"))
    )
    monkeypatch.setattr(
        "face_spike.comparison.shutil.rmtree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cleanup")),
    )

    with pytest.raises(OSError, match="publish") as error:
        run_comparison(ComparisonConfig(run, reference, output))

    assert any("cleanup" in note for note in error.value.__notes__)


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_comparison_cleans_staging_after_keyboard_interrupt_and_preserves_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup_fails: bool
) -> None:
    run = _run(tmp_path / "run", [("person-0001", ["a.jpg"])])
    reference = _reference(tmp_path / "reference", {"10": ["a.jpg"]})
    output = tmp_path / "comparison"
    monkeypatch.setattr(
        comparison_module,
        "render_people_comparison",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt("stop")),
    )
    if cleanup_fails:
        monkeypatch.setattr(
            comparison_module.shutil,
            "rmtree",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("cleanup")),
        )

    with pytest.raises(KeyboardInterrupt, match="stop") as error:
        run_comparison(ComparisonConfig(run, reference, output))

    assert not output.exists()
    if cleanup_fails:
        assert any("cleanup" in note for note in error.value.__notes__)
    else:
        assert not list(tmp_path.glob(".comparison.*"))

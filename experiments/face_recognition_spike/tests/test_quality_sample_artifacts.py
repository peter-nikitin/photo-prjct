from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

# The stateful Node/vm harness intentionally embeds compact JavaScript.
# ruff: noqa: E501

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.path.insert(0, str(Path(__file__).parent))


def _source_bundle(tmp_path: Path):
    from face_spike.quality_comparison_artifacts import write_quality_comparison_bundle
    from face_spike.quality_sample import build_quality_sample
    from test_quality_comparison_artifacts import _comparison

    comparison = _comparison()
    candidate_run = tmp_path / "candidate-run"
    crop = candidate_run / "faces" / "candidate.png"
    crop.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "gray").save(crop)
    source = tmp_path / "comparison"
    write_quality_comparison_bundle(source, comparison, candidate_run)
    source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    source_digest = source_manifest["bundle_sha256"]
    return source, build_quality_sample(comparison, source_digest, sample_size=1)


def _comparison_with_exact_controls():
    from face_spike.quality_comparison import compare_quality_runs
    from test_quality_comparison import _face, _photo, _quality, _run, _thresholds

    retained = tuple(
        _face(
            f"retained-{index:02d}",
            "photo.jpg",
            (index * 2, 0, 32, 32),
            quality=_quality(
                confidence=0.82,
                minimum_side_px=32,
                relative_area=0.05,
                sharpness=25 if index < 20 else 50,
            ),
        )
        for index in range(40)
    )
    baseline_rejected = _face("baseline-rejected", "photo.jpg", (50, 0, 20, 20))
    baseline = _run(_photo("photo.jpg", *retained, baseline_rejected))
    rejected = _face(
        "rejected",
        "photo.jpg",
        (50, 0, 20, 20),
        status="quality_rejected",
        quality=_quality(decision="quality_rejected", reasons=("severe_blur",), sharpness=5),
    )
    candidate = _run(_photo("photo.jpg", *retained, rejected))
    return compare_quality_runs(baseline, candidate, thresholds=_thresholds())


def test_exact_logical_threshold_controls_keep_metrics_and_deduplicate_crops() -> None:
    comparison = _comparison_with_exact_controls()

    assert set(comparison.threshold_samples) == {
        "minimum_face_px",
        "severe_blur_threshold",
        "borderline_blur_threshold",
        "minimum_relative_area",
        "minimum_confidence",
    }
    assert sum(len(items) for items in comparison.threshold_samples.values()) == 100
    assert {item.face_id for items in comparison.threshold_samples.values() for item in items} == {
        f"retained-{index:02d}" for index in range(40)
    }


def test_bundle_keeps_100_logical_controls_and_deduplicates_their_crops(tmp_path: Path) -> None:
    from face_spike.quality_comparison_artifacts import write_quality_comparison_bundle
    from face_spike.quality_sample import build_quality_sample
    from face_spike.quality_sample_artifacts import write_quality_sample_bundle

    comparison = _comparison_with_exact_controls()
    candidate = tmp_path / "candidate"
    faces = candidate / "faces"
    faces.mkdir(parents=True)
    for face_id in [*(f"retained-{index:02d}" for index in range(40)), "rejected"]:
        Image.new("RGB", (8, 8), "gray").save(faces / f"{face_id}.png")
    source = tmp_path / "comparison"
    write_quality_comparison_bundle(source, comparison, candidate)
    digest = json.loads((source / "manifest.json").read_text())["bundle_sha256"]
    output = tmp_path / "sample"
    write_quality_sample_bundle(output, source, build_quality_sample(comparison, digest, 1))

    manifest = json.loads((output / "manifest.json").read_text())
    controls = manifest["retained_controls"]
    expected = [
        (metric, item.face_id, item.value, item.threshold)
        for metric, items in sorted(comparison.threshold_samples.items())
        for item in items
    ]
    assert [
        (item["metric"], item["face_id"], item["value"], item["threshold"]) for item in controls
    ] == expected
    assert len(controls) == 100
    assert (
        len({item["path"] for item in controls})
        == len(tuple((output / "retained-controls").iterdir()))
        == 40
    )
    report = (output / "report.html").read_text()
    assert sum(report.count(metric) for metric in comparison.threshold_samples) == 100
    assert "minimum_face_px | value=32.0 | threshold=32.0" in report
    assert "retained-controls/" in report


def test_finalization_publishes_strata_controls_and_bounded_crop_gallery(tmp_path: Path) -> None:
    from face_spike.quality_sample import QualitySampleLabel
    from face_spike.quality_sample_artifacts import write_quality_sample_analysis

    source, sample = _source_bundle(tmp_path)
    bundle = tmp_path / "sample"
    from face_spike.quality_sample_artifacts import write_quality_sample_bundle

    write_quality_sample_bundle(bundle, source, sample)
    output = tmp_path / "analysis"
    write_quality_sample_analysis(
        output,
        sample,
        (QualitySampleLabel(sample.rejections[0].face_id, "clear"),),
        reviewer="reviewer-7",
        reviewed_at="2026-08-08T12:00:00Z",
        sample_bundle=bundle,
    )
    payload = json.loads((output / "analysis.json").read_text())
    assert set(payload["by_reason_stratum"]) == {"severe_blur"}
    assert "threshold_vicinity_controls" in payload
    assert (output / "review-gallery" / "clear").is_dir()
    assert len(tuple((output / "review-gallery" / "clear").iterdir())) == 1
    report = (output / "report.html").read_text().lower()
    assert '<img loading="lazy"' in report
    assert "pass" not in report and "embedding" not in report and str(tmp_path) not in report


def test_analysis_html_renders_every_logical_threshold_control(tmp_path: Path) -> None:
    from face_spike.quality_comparison_artifacts import write_quality_comparison_bundle
    from face_spike.quality_sample import QualitySampleLabel, build_quality_sample
    from face_spike.quality_sample_artifacts import (
        write_quality_sample_analysis,
        write_quality_sample_bundle,
    )

    comparison = _comparison_with_exact_controls()
    candidate = tmp_path / "candidate"
    faces = candidate / "faces"
    faces.mkdir(parents=True)
    for face_id in [*(f"retained-{index:02d}" for index in range(40)), "rejected"]:
        Image.new("RGB", (8, 8), "gray").save(faces / f"{face_id}.png")
    source = tmp_path / "comparison"
    write_quality_comparison_bundle(source, comparison, candidate)
    digest = json.loads((source / "manifest.json").read_text())["bundle_sha256"]
    sample = build_quality_sample(comparison, digest, 1)
    bundle = tmp_path / "sample"
    write_quality_sample_bundle(bundle, source, sample)
    output = tmp_path / "analysis"
    write_quality_sample_analysis(
        output,
        sample,
        (QualitySampleLabel("rejected", "clear"),),
        reviewer="r",
        reviewed_at="2026-08-08T12:00:00Z",
        sample_bundle=bundle,
    )
    report = (output / "report.html").read_text()
    assert report.count("data-threshold-control") == 100
    assert "minimum_face_px: value=32.0; threshold=32.0" in report


def test_writer_publishes_bound_vector_free_sample_and_strict_loader_rejects_tampering(
    tmp_path: Path,
) -> None:
    from face_spike.quality_sample_artifacts import (
        load_quality_sample_bundle,
        write_quality_sample_bundle,
    )

    source, sample = _source_bundle(tmp_path)
    output = tmp_path / "sample"

    write_quality_sample_bundle(output, source, sample)

    loaded, bundle_digest = load_quality_sample_bundle(output)
    assert loaded == sample
    assert len(bundle_digest) == 64
    assert len(tuple((output / "sampled-crops").iterdir())) == 1
    assert (output / "retained-controls").is_dir()
    source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    assert len(tuple((output / "retained-controls").iterdir())) == len(
        source_manifest["threshold_crops"]
    )
    assert {path.name for path in output.iterdir()} == {
        "labels-template.csv",
        "manifest.json",
        "report.html",
        "sample.json",
        "sampled-crops",
        "retained-controls",
    }
    text = "\n".join(
        item.read_text(encoding="utf-8", errors="ignore")
        for item in output.rglob("*")
        if item.is_file()
    ).lower()
    assert '"embedding"' not in text
    assert '"vector"' not in text
    assert str(tmp_path) not in text

    sampled_crop = next((output / "sampled-crops").iterdir())
    sampled_crop.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="identity|differs"):
        load_quality_sample_bundle(output)

    with pytest.raises(FileExistsError):
        write_quality_sample_bundle(output, source, sample)


def test_writer_rejects_changed_source_and_symlinked_bundle_without_publication(
    tmp_path: Path,
) -> None:
    from face_spike.quality_sample_artifacts import write_quality_sample_bundle

    source, sample = _source_bundle(tmp_path)
    changed_sample = type(sample)(
        "b" * 64, sample.population_count, sample.strata, sample.rejections
    )
    with pytest.raises(ValueError, match="source"):
        write_quality_sample_bundle(tmp_path / "wrong-source", source, changed_sample)
    assert not (tmp_path / "wrong-source").exists()

    linked = tmp_path / "linked-source"
    os.symlink(source, linked)
    with pytest.raises(ValueError, match="source"):
        write_quality_sample_bundle(tmp_path / "linked-output", linked, sample)
    assert not (tmp_path / "linked-output").exists()


def test_writer_rejects_forged_non_identifier_sample_metadata(tmp_path: Path) -> None:
    from face_spike.quality_sample_artifacts import write_quality_sample_bundle

    source, sample = _source_bundle(tmp_path)
    forged_rejection = replace(sample.rejections[0].rejection, filename="forged.jpg")
    forged = replace(
        sample,
        rejections=(replace(sample.rejections[0], rejection=forged_rejection),),
    )

    with pytest.raises(ValueError, match="validated source"):
        write_quality_sample_bundle(tmp_path / "forged", source, forged)


def test_file_only_reviewer_is_paginated_resumable_and_refuses_incomplete_export(
    tmp_path: Path,
) -> None:
    from face_spike.quality_sample_artifacts import write_quality_sample_bundle

    source, sample = _source_bundle(tmp_path)
    output = tmp_path / "sample"
    write_quality_sample_bundle(output, source, sample)
    report = (output / "report.html").read_text(encoding="utf-8")

    assert report.count("<script>") == 1
    assert "at most 250 rejected faces" in report
    assert 'loading="lazy"' in report
    assert all(label in report for label in ("clear", "blurred", "unusably_small", "uncertain"))
    assert "keydown" in report
    assert "event.key >= '1' && event.key <= '4'" in report
    assert "localStorage" in report
    assert "Import CSV" in report
    assert "every sampled rejection" in report
    assert "Retained controls" in report
    assert "not included in estimates" in report


def test_reviewer_import_parser_accepts_the_quoted_csv_its_exporter_produces(
    tmp_path: Path,
) -> None:
    from face_spike.quality_sample_artifacts import write_quality_sample_bundle

    source, sample = _source_bundle(tmp_path)
    output = tmp_path / "sample"
    write_quality_sample_bundle(output, source, sample)
    report = (output / "report.html").read_text(encoding="utf-8")
    function = _javascript_function(report, "parseCsvRows")
    quoted_csv = (
        'sample_sha256,face_id,label\r\n"sample-identity","face, with ""quote""","clear"\r\n'
    )
    process = subprocess.run(
        [
            "node",
            "-e",
            f"{function};process.stdout.write(JSON.stringify(parseCsvRows({json.dumps(quoted_csv)})));",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(process.stdout) == [
        ["sample_sha256", "face_id", "label"],
        ["sample-identity", 'face, with "quote"', "clear"],
    ]


def test_generated_reviewer_script_is_valid_javascript(tmp_path: Path) -> None:
    from face_spike.quality_sample_artifacts import write_quality_sample_bundle

    source, sample = _source_bundle(tmp_path)
    output = tmp_path / "sample"
    write_quality_sample_bundle(output, source, sample)
    report = (output / "report.html").read_text(encoding="utf-8")
    script = report.split("<script>", 1)[1].split("</script>", 1)[0]
    checked = subprocess.run(["node", "--check", "-"], input=script, capture_output=True, text=True)

    assert checked.returncode == 0, checked.stderr


def test_reviewer_stateful_keyboard_draft_and_complete_export_contract(tmp_path: Path) -> None:
    from face_spike.quality_sample_artifacts import write_quality_sample_bundle

    source, sample = _source_bundle(tmp_path)
    output = tmp_path / "sample"
    write_quality_sample_bundle(output, source, sample)
    script = (
        (output / "report.html")
        .read_text(encoding="utf-8")
        .split("<script>", 1)[1]
        .split("</script>", 1)[0]
    )
    harness = r"""
const vm=require('vm'),fs=require('fs'); const storage=new Map(), listeners={};
function node(tag='div'){return {tag,children:[],value:'',classList:{toggle(){}},append(...x){this.children.push(...x)},replaceChildren(...x){this.children=x},dispatchEvent(event){if(event.type==='change'&&this.onchange)this.onchange(event)},click(){if(this.onclick)this.onclick()},addEventListener(type,fn){this.listeners=this.listeners||{};this.listeners[type]=fn}}}
const nodes={'#cards':node(),'#progress':node(),'#control-note':node(),'#previous':node(),'#next':node(),'#controls':node(),'#import':node(),'#export':node()};
global.document={querySelector:q=>nodes[q],querySelectorAll:q=>q==='select'?nodes['#cards'].children.flatMap(card=>card.children.filter(child=>child.tag==='select')):[],createElement:node,addEventListener:(type,fn)=>listeners[type]=fn};
global.localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)};global.Event=class{constructor(type){this.type=type}};global.FileReader=class{};global.Blob=class{};global.URL={createObjectURL:()=>'',revokeObjectURL(){}};global.alert=()=>{};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8')); listeners.keydown({key:'1'}); const api=global.__qualitySampleReview; console.log(JSON.stringify({state:api.state(),rows:api.exportRows(),progress:nodes['#progress'].textContent}));
"""
    path = tmp_path / "reviewer.js"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run(["node", "-e", harness, str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)

    assert value["state"]["draft"] == {sample.rejections[0].face_id: "clear"}
    assert value["rows"][0][2] == "clear"
    assert "labels 1/1" in value["progress"]


def test_reviewer_keyboard_labels_global_item_after_next_page(tmp_path: Path) -> None:
    from face_spike.quality_sample_artifacts import _render_reviewer

    rows = tuple(
        SimpleNamespace(face_id=f"face-{index}", reasons=("severe_blur",)) for index in range(251)
    )
    report = _render_reviewer(
        SimpleNamespace(rejections=rows),
        "a" * 64,
        "b" * 64,
        {row.face_id: "sampled-crops/x.png" for row in rows},
        {},
    )
    script = report.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = r"""const vm=require('vm'),fs=require('fs'),storage=new Map(),listeners={};function n(t='div'){return {tag:t,children:[],value:'',classList:{toggle(){}},append(...x){this.children.push(...x)},replaceChildren(...x){this.children=x},dispatchEvent(e){if(e.type==='change'&&this.onchange)this.onchange(e)},click(){this.onclick&&this.onclick()}}}const e={'#cards':n(),'#progress':n(),'#control-note':n(),'#previous':n(),'#next':n(),'#controls':n(),'#import':n(),'#export':n()};global.document={querySelector:q=>e[q],querySelectorAll:q=>q==='select'?e['#cards'].children.flatMap(x=>x.children.filter(y=>y.tag==='select')):[],createElement:n,addEventListener:(t,f)=>listeners[t]=f};global.localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)};global.Event=class{constructor(type){this.type=type}};global.FileReader=class{};global.Blob=class{};global.URL={createObjectURL:()=>'',revokeObjectURL(){}};global.alert=()=>{};vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));e['#next'].click();listeners.keydown({key:'1'});e['#controls'].click();e['#controls'].click();listeners.keydown({key:'1'});console.log(JSON.stringify({state:global.__qualitySampleReview.state(),progress:e['#progress'].textContent}));"""
    path = tmp_path / "reviewer.js"
    path.write_text(script)
    result = json.loads(
        subprocess.run(
            ["node", "-e", harness, str(path)], check=True, capture_output=True, text=True
        ).stdout
    )
    state = result["state"]

    assert state["page"] == 0
    assert state["activeIndex"] == 1
    assert state["draft"] == {"face-250": "clear", "face-0": "clear"}
    assert "labels 2/251" in result["progress"]


def _javascript_function(report: str, name: str) -> str:
    start = report.index(f"function {name}")
    opening = report.index("{", start)
    depth = 0
    for index in range(opening, len(report)):
        if report[index] == "{":
            depth += 1
        elif report[index] == "}":
            depth -= 1
            if depth == 0:
                return report[start : index + 1]
    raise AssertionError(f"{name} is not closed")


def test_label_loader_requires_exact_sample_identity_and_complete_valid_rows(
    tmp_path: Path,
) -> None:
    from face_spike.quality_sample_artifacts import (
        LABEL_HEADERS,
        load_quality_sample_labels,
        quality_sample_sha256,
    )

    _source, sample = _source_bundle(tmp_path)
    labels = tmp_path / "labels.csv"

    def write(rows: list[tuple[str, str, str]], headers=LABEL_HEADERS) -> None:
        with labels.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows)

    identity = quality_sample_sha256(sample)
    write([(identity, sample.rejections[0].face_id, "blurred")])
    loaded = load_quality_sample_labels(labels, sample)
    assert tuple((item.face_id, item.label) for item in loaded) == (
        (sample.rejections[0].face_id, "blurred"),
    )

    for rows in (
        [],
        [(identity, sample.rejections[0].face_id, "blurred")] * 2,
        [(identity, "unknown", "blurred")],
        [(identity, sample.rejections[0].face_id, "wrong")],
        [("f" * 64, sample.rejections[0].face_id, "blurred")],
    ):
        write(rows)
        with pytest.raises(ValueError):
            load_quality_sample_labels(labels, sample)
    write([(identity, sample.rejections[0].face_id, "blurred")], ("wrong", "headers", "only"))
    with pytest.raises(ValueError, match="headers"):
        load_quality_sample_labels(labels, sample)


def test_analysis_publication_is_immutable_weighted_and_has_all_clear_uncertain_items(
    tmp_path: Path,
) -> None:
    from face_spike.quality_sample import QualitySampleLabel
    from face_spike.quality_sample_artifacts import (
        write_quality_sample_analysis,
        write_quality_sample_bundle,
    )

    source, sample = _source_bundle(tmp_path)
    bundle = tmp_path / "sample"
    write_quality_sample_bundle(bundle, source, sample)
    output = tmp_path / "analysis"
    labels = (QualitySampleLabel(sample.rejections[0].face_id, "uncertain"),)

    write_quality_sample_analysis(
        output,
        sample,
        labels,
        reviewer="reviewer-7",
        reviewed_at="2026-08-08T12:00:00Z",
        sample_bundle=bundle,
    )

    payload = json.loads((output / "analysis.json").read_text(encoding="utf-8"))
    assert payload["weighted_proportions"]["uncertain"] == 1.0
    assert len(payload["review_gallery"]["uncertain"]) == 1
    assert payload["review_gallery"]["clear"] == []
    report = (output / "report.html").read_text(encoding="utf-8")
    assert '<img loading="lazy"' in report
    assert "automatic" not in report.lower()
    assert "pass" not in report.lower()
    with pytest.raises(FileExistsError):
        write_quality_sample_analysis(
            output,
            sample,
            labels,
            reviewer="reviewer-7",
            reviewed_at="2026-08-08T12:00:00Z",
            sample_bundle=bundle,
        )
    with pytest.raises(ValueError, match="reviewer"):
        write_quality_sample_analysis(
            tmp_path / "bad-reviewer",
            sample,
            labels,
            reviewer="\n",
            reviewed_at="2026-08-08T12:00:00Z",
            sample_bundle=bundle,
        )
    with pytest.raises(ValueError, match="timestamp"):
        write_quality_sample_analysis(
            tmp_path / "bad-time",
            sample,
            labels,
            reviewer="reviewer-7",
            reviewed_at="not-a-time",
            sample_bundle=bundle,
        )

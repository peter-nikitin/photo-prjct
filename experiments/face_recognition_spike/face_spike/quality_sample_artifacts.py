"""Immutable private artifacts for sampled face-quality review."""

# The self-contained file:// reviewer is emitted as one minified literal so its
# immutable hash is independent of an external asset pipeline.
# ruff: noqa: E501

from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from .analysis import BoundingBox
from .quality_comparison import NewRejection
from .quality_comparison_artifacts import load_quality_comparison_bundle
from .quality_sample import (
    QualitySample,
    QualitySampleLabel,
    QualitySampleStratum,
    SampledRejection,
    analyze_quality_sample,
    build_quality_sample,
)

SCHEMA_VERSION = 1
LABEL_HEADERS = ("sample_sha256", "face_id", "label")
_LABELS = frozenset({"clear", "blurred", "unusably_small", "uncertain"})
_DIGEST_PLACEHOLDER = "0" * 64


def quality_sample_sha256(sample: QualitySample) -> str:
    """Return the stable identity used to bind exported labels to one sample."""
    if not isinstance(sample, QualitySample):
        raise TypeError("quality sample is required")
    return hashlib.sha256(_canonical_json(_sample_identity_payload(sample))).hexdigest()


def write_quality_sample_bundle(output: Path, source_bundle: Path, sample: QualitySample) -> None:
    """Publish a bounded, vector-free sampled review bundle without overwriting evidence."""
    if not isinstance(sample, QualitySample):
        raise TypeError("quality sample is required")
    if os.path.lexists(output):
        raise FileExistsError(output)
    comparison, source_sha256 = _load_source_bundle(source_bundle)
    if sample.source_bundle_sha256 != source_sha256:
        raise ValueError("quality sample source bundle does not match")
    expected_sample = build_quality_sample(comparison, source_sha256, sample.sample_count)
    if sample != expected_sample:
        raise ValueError("quality sample does not match validated source")

    source_manifest = _load_json(source_bundle / "manifest.json")
    source_review = _crop_paths(source_manifest.get("review_crops"), "review-crops")
    source_controls = _crop_paths(source_manifest.get("threshold_crops"), "threshold-crops")
    sample_ids = {item.face_id for item in sample.rejections}
    comparison_ids = {item.candidate_face_id for item in comparison.new_rejections}
    if sample_ids - comparison_ids or set(source_review) != comparison_ids:
        raise ValueError("quality sample source crop coverage is invalid")

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.sample.", dir=output.parent))
    try:
        sampled_crops = staging / "sampled-crops"
        retained_controls = staging / "retained-controls"
        sampled_crops.mkdir()
        retained_controls.mkdir()
        sampled_names = _copy_crops(
            source_bundle, source_review, sample_ids, sampled_crops, "sampled-crops"
        )
        control_names = _copy_crops(
            source_bundle,
            source_controls,
            set(source_controls),
            retained_controls,
            "retained-controls",
        )
        logical_controls = [
            {
                "face_id": control.face_id,
                "metric": metric,
                "path": control_names[control.face_id],
                "threshold": control.threshold,
                "value": control.value,
            }
            for metric, controls in sorted(comparison.threshold_samples.items())
            for control in controls
        ]
        sample_sha256 = quality_sample_sha256(sample)
        _write_json(staging / "sample.json", _sample_payload(sample, sample_sha256))
        _write_labels_template(staging / "labels-template.csv", sample, sample_sha256)
        _write_text(
            staging / "report.html",
            _render_reviewer(
                sample, sample_sha256, _DIGEST_PLACEHOLDER, sampled_names, logical_controls
            ),
        )
        manifest: dict[str, object] = {
            "artifact_type": "quality-sample",
            "bundle_sha256": "",
            "files": [],
            "retained_controls": logical_controls,
            "sample_sha256": sample_sha256,
            "sampled_crops": _crop_rows(sampled_names, "sampled-crops"),
            "schema_version": SCHEMA_VERSION,
            "source_bundle_sha256": source_sha256,
        }
        identity = {
            key: value for key, value in manifest.items() if key not in {"bundle_sha256", "files"}
        }
        identity["files"] = _bundle_identity_file_rows(staging, _DIGEST_PLACEHOLDER)
        bundle_sha256 = hashlib.sha256(_canonical_json(identity)).hexdigest()
        report = (staging / "report.html").read_text(encoding="utf-8")
        if report.count(_DIGEST_PLACEHOLDER) != 1:
            raise ValueError("sample report digest placeholder is invalid")
        _write_text(staging / "report.html", report.replace(_DIGEST_PLACEHOLDER, bundle_sha256))
        manifest["bundle_sha256"] = bundle_sha256
        manifest["files"] = _bundle_file_rows(staging)
        _write_json(staging / "manifest.json", manifest)
        if os.path.lexists(output):
            raise FileExistsError(output)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def load_quality_sample_bundle(path: Path) -> tuple[QualitySample, str]:
    """Strictly reload a complete sample artifact without consulting its source path."""
    root = path.resolve()
    expected = {
        "labels-template.csv",
        "manifest.json",
        "report.html",
        "sample.json",
        "sampled-crops",
        "retained-controls",
    }
    if path.is_symlink() or not root.is_dir() or {item.name for item in root.iterdir()} != expected:
        raise ValueError("quality sample bundle is incomplete")
    if any(
        item.is_symlink() or not item.is_file()
        for item in (
            root / "labels-template.csv",
            root / "manifest.json",
            root / "report.html",
            root / "sample.json",
        )
    ) or any(
        item.is_symlink() or not item.is_dir()
        for item in (root / "sampled-crops", root / "retained-controls")
    ):
        raise ValueError("quality sample bundle types are invalid")
    manifest = _require(
        _load_json(root / "manifest.json"),
        {
            "artifact_type",
            "bundle_sha256",
            "files",
            "retained_controls",
            "sample_sha256",
            "sampled_crops",
            "schema_version",
            "source_bundle_sha256",
        },
    )
    bundle_sha256 = _digest(manifest["bundle_sha256"], "bundle")
    sample = _sample_from_payload(_load_json(root / "sample.json"))
    sample_sha256 = quality_sample_sha256(sample)
    if (
        manifest["artifact_type"] != "quality-sample"
        or manifest["schema_version"] != SCHEMA_VERSION
        or _digest(manifest["source_bundle_sha256"], "source") != sample.source_bundle_sha256
        or _digest(manifest["sample_sha256"], "sample") != sample_sha256
    ):
        raise ValueError("quality sample manifest is invalid")
    identity = {
        key: value for key, value in manifest.items() if key not in {"bundle_sha256", "files"}
    }
    identity["files"] = _bundle_identity_file_rows(root, bundle_sha256)
    if bundle_sha256 != hashlib.sha256(_canonical_json(identity)).hexdigest():
        raise ValueError("quality sample bundle identity is invalid")
    _validate_bundle_files(root, manifest["files"])
    sampled = _crop_paths(manifest["sampled_crops"], "sampled-crops")
    controls = _crop_paths(manifest["retained_controls"], "retained-controls")
    if set(sampled) != {item.face_id for item in sample.rejections}:
        raise ValueError("quality sample crop coverage is invalid")
    _validate_crop_paths(root, sampled, "sampled-crops")
    _validate_crop_paths(root, controls, "retained-controls")
    _validate_label_template(root / "labels-template.csv", sample, sample_sha256)
    if bundle_sha256 not in (root / "report.html").read_text(encoding="utf-8"):
        raise ValueError("quality sample report binding is invalid")
    return sample, bundle_sha256


def load_quality_sample_labels(path: Path, sample: QualitySample) -> tuple[QualitySampleLabel, ...]:
    """Load one complete, unique, sample-bound CSV label export."""
    if not isinstance(sample, QualitySample):
        raise TypeError("quality sample is required")
    identity = quality_sample_sha256(sample)
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
    except (OSError, UnicodeDecodeError, csv.Error):
        raise ValueError("quality sample labels cannot be read") from None
    if not rows or tuple(rows[0]) != LABEL_HEADERS:
        raise ValueError("quality sample label headers are invalid")
    labels: list[QualitySampleLabel] = []
    seen: set[str] = set()
    for row in rows[1:]:
        if len(row) != len(LABEL_HEADERS):
            raise ValueError("quality sample label row is invalid")
        row_identity, face_id, label = row
        if row_identity != identity:
            raise ValueError("quality sample label identity is invalid")
        if face_id in seen:
            raise ValueError("quality sample labels are duplicated")
        if label not in _LABELS:
            raise ValueError("quality sample label is invalid")
        seen.add(face_id)
        labels.append(QualitySampleLabel(face_id, label))
    expected = {item.face_id for item in sample.rejections}
    if seen != expected:
        raise ValueError("quality sample labels are incomplete or unknown")
    return tuple(labels)


def write_quality_sample_analysis(
    output: Path,
    sample: QualitySample,
    labels: Sequence[QualitySampleLabel],
    reviewer: str,
    reviewed_at: str,
    *,
    sample_bundle: Path,
) -> None:
    """Publish immutable weighted evidence; it intentionally makes no decision."""
    if os.path.lexists(output):
        raise FileExistsError(output)
    _validate_reviewer(reviewer)
    _validate_timestamp(reviewed_at)
    loaded_sample, _bundle_sha256 = load_quality_sample_bundle(sample_bundle)
    if loaded_sample != sample:
        raise ValueError("quality sample analysis bundle does not match")
    analysis = analyze_quality_sample(sample, labels)
    labels_by_id = {item.face_id: item.label for item in labels}
    by_stratum = {}
    for stratum in sample.strata:
        selected = [item for item in sample.rejections if item.reasons == stratum.reasons]
        raw = {label: 0 for label in _LABELS}
        weighted = {label: 0.0 for label in _LABELS}
        for item in selected:
            label = labels_by_id[item.face_id]
            raw[label] += 1
            weighted[label] += item.inclusion_weight
        by_stratum[",".join(stratum.reasons)] = {
            "population_count": stratum.population_count,
            "sample_count": stratum.sample_count,
            "raw_counts": raw,
            "weighted_counts": weighted,
            "weighted_proportions": {
                label: weighted[label] / stratum.population_count for label in _LABELS
            },
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.analysis.", dir=output.parent))
    try:
        source_manifest = _load_json(sample_bundle / "manifest.json")
        crops = _crop_paths(source_manifest["sampled_crops"], "sampled-crops")
        gallery = {
            "clear": analysis.clear_rejections,
            "uncertain": analysis.uncertain_rejections,
        }
        gallery_root = staging / "review-gallery"
        gallery_paths: dict[str, list[str]] = {"clear": [], "uncertain": []}
        for label, rejections in gallery.items():
            destination = gallery_root / label
            destination.mkdir(parents=True)
            for rejection in rejections:
                name = Path(crops[rejection.face_id]).name
                shutil.copyfile(sample_bundle / crops[rejection.face_id], destination / name)
                gallery_paths[label].append(f"review-gallery/{label}/{name}")
        payload = {
            "analysis_type": "quality-sample-analysis",
            "clear_wilson_interval": list(analysis.clear_wilson_interval),
            "kish_effective_sample_size": analysis.kish_effective_sample_size,
            "raw_counts": dict(analysis.raw_counts),
            "by_reason_stratum": by_stratum,
            "review_gallery": gallery_paths,
            "threshold_vicinity_controls": source_manifest["retained_controls"],
            "reviewed_at": reviewed_at,
            "reviewer": reviewer,
            "sample_sha256": quality_sample_sha256(sample),
            "source_bundle_sha256": sample.source_bundle_sha256,
            "weighted_counts": dict(analysis.weighted_counts),
            "weighted_proportions": dict(analysis.weighted_proportions),
        }
        _write_json(staging / "analysis.json", payload)
        _write_text(staging / "report.html", _render_analysis(payload))
        manifest = {
            "artifact_type": "quality-sample-analysis",
            "files": _bundle_file_rows(staging),
            "schema_version": SCHEMA_VERSION,
            "sha256": hashlib.sha256(_canonical_json(payload)).hexdigest(),
        }
        _write_json(staging / "manifest.json", manifest)
        if os.path.lexists(output):
            raise FileExistsError(output)
        os.replace(staging, output)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _load_source_bundle(source_bundle: Path):
    if source_bundle.is_symlink():
        raise ValueError("quality sample source bundle is unsafe")
    try:
        return load_quality_comparison_bundle(source_bundle)
    except (OSError, ValueError):
        raise ValueError("quality sample source bundle is invalid") from None


def _sample_identity_payload(sample: QualitySample) -> dict[str, object]:
    return {
        "population_count": sample.population_count,
        "rejections": [_sampled_rejection_payload(item) for item in sample.rejections],
        "source_bundle_sha256": sample.source_bundle_sha256,
        "strata": [
            {
                "population_count": item.population_count,
                "reasons": list(item.reasons),
                "sample_count": item.sample_count,
            }
            for item in sample.strata
        ],
    }


def _sample_payload(sample: QualitySample, sample_sha256: str) -> dict[str, object]:
    return {
        "sample_sha256": sample_sha256,
        **_sample_identity_payload(sample),
        "schema_version": SCHEMA_VERSION,
    }


def _sampled_rejection_payload(item: SampledRejection) -> dict[str, object]:
    rejection = item.rejection
    return {
        "baseline_face_id": rejection.baseline_face_id,
        "bounding_box": {
            "height": rejection.bounding_box.height,
            "width": rejection.bounding_box.width,
            "x": rejection.bounding_box.x,
            "y": rejection.bounding_box.y,
        },
        "candidate_face_id": rejection.candidate_face_id,
        "confidence": rejection.confidence,
        "crop_path": rejection.crop_path,
        "filename": rejection.filename,
        "minimum_side_px": rejection.minimum_side_px,
        "population_count": item.population_count,
        "reasons": list(rejection.reasons),
        "relative_area": rejection.relative_area,
        "sample_count": item.sample_count,
        "sharpness": rejection.sharpness,
    }


def _sample_from_payload(value: Mapping[str, object]) -> QualitySample:
    payload = _require(
        value,
        {
            "population_count",
            "rejections",
            "sample_sha256",
            "schema_version",
            "source_bundle_sha256",
            "strata",
        },
    )
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("quality sample schema is invalid")
    strata = tuple(
        QualitySampleStratum(
            tuple(
                _string(reason)
                for reason in _list(
                    _require(item, {"population_count", "reasons", "sample_count"})["reasons"]
                )
            ),
            _integer(
                _require(item, {"population_count", "reasons", "sample_count"})["population_count"]
            ),
            _integer(
                _require(item, {"population_count", "reasons", "sample_count"})["sample_count"]
            ),
        )
        for item in _list(payload["strata"])
    )
    rejections = tuple(_sampled_rejection(item) for item in _list(payload["rejections"]))
    sample = QualitySample(
        _digest(payload["source_bundle_sha256"], "source"),
        _integer(payload["population_count"]),
        strata,
        rejections,
    )
    if _digest(payload["sample_sha256"], "sample") != quality_sample_sha256(sample):
        raise ValueError("quality sample digest is invalid")
    return sample


def _sampled_rejection(value: object) -> SampledRejection:
    item = _require(
        value,
        {
            "baseline_face_id",
            "bounding_box",
            "candidate_face_id",
            "confidence",
            "crop_path",
            "filename",
            "minimum_side_px",
            "population_count",
            "reasons",
            "relative_area",
            "sample_count",
            "sharpness",
        },
    )
    box = _require(item["bounding_box"], {"height", "width", "x", "y"})
    rejection = NewRejection(
        _string(item["filename"]),
        _string(item["baseline_face_id"]),
        _string(item["candidate_face_id"]),
        _string(item["crop_path"]),
        BoundingBox(
            _number(box["x"]), _number(box["y"]), _number(box["width"]), _number(box["height"])
        ),
        tuple(_string(reason) for reason in _list(item["reasons"])),
        _number(item["confidence"]),
        _number(item["minimum_side_px"]),
        _number(item["relative_area"]),
        _number(item["sharpness"]),
    )
    return SampledRejection(
        rejection, _integer(item["population_count"]), _integer(item["sample_count"])
    )


def _crop_paths(value: object, directory: str) -> dict[str, str]:
    rows = _list(value)
    result: dict[str, str] = {}
    for raw in rows:
        item = _mapping(raw)
        allowed = (
            {"face_id", "metric", "path", "threshold", "value"}
            if directory == "retained-controls"
            else {"face_id", "metrics", "path"}
            if directory == "threshold-crops"
            else {"face_id", "path"}
        )
        if set(item) != allowed:
            raise ValueError("quality sample crop schema is invalid")
        face_id = _string(item["face_id"])
        relative = _safe_crop_path(_string(item["path"]), directory)
        key = face_id if directory != "retained-controls" else f"{face_id}\0{len(result)}"
        if key in result:
            raise ValueError("quality sample crops are duplicated")
        result[key] = relative
    return result


def _copy_crops(
    root: Path, source: Mapping[str, str], ids: set[str], destination: Path, directory: str
) -> dict[str, str]:
    result: dict[str, str] = {}
    for face_id in sorted(ids):
        if face_id not in source:
            raise ValueError("quality sample source crop is missing")
        relative = _safe_crop_path(
            source[face_id], "review-crops" if directory == "sampled-crops" else "threshold-crops"
        )
        original = root / relative
        if original.is_symlink() or not original.is_file():
            raise ValueError("quality sample source crop is invalid")
        name = f"{hashlib.sha256(face_id.encode()).hexdigest()}.png"
        shutil.copyfile(original, destination / name)
        result[face_id] = f"{directory}/{name}"
    return result


def _crop_rows(names: Mapping[str, str], directory: str) -> list[dict[str, str]]:
    return [
        {"face_id": face_id, "path": _safe_crop_path(path, directory)}
        for face_id, path in sorted(names.items())
    ]


def _validate_crop_paths(root: Path, crops: Mapping[str, str], directory: str) -> None:
    declared = {_safe_crop_path(path, directory) for path in crops.values()}
    actual = {item.relative_to(root).as_posix() for item in (root / directory).iterdir()}
    if declared != actual or any(
        (root / path).is_symlink() or not (root / path).is_file() for path in declared
    ):
        raise ValueError("quality sample crop paths are invalid")


def _render_reviewer_legacy(
    sample: QualitySample,
    sample_sha256: str,
    bundle_sha256: str,
    crops: Mapping[str, str],
    controls: Mapping[str, str],
) -> str:
    payload = {
        "controls": [
            {"face_id": face_id, "path": path} for face_id, path in sorted(controls.items())
        ],
        "rejections": [
            {"face_id": item.face_id, "path": crops[item.face_id], "reasons": list(item.reasons)}
            for item in sample.rejections
        ],
    }
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Sampled quality review</title><style>body{{font:16px sans-serif;margin:2rem}}article{{margin:1rem 0}}img{{max-width:240px;height:auto}}.hidden{{display:none}}</style></head><body><h1>Sampled rejected faces</h1><p><b>clear</b>: usable face; <b>blurred</b>: real but unsuitable; <b>unusably_small</b>: too small; <b>uncertain</b>: no confident judgement.</p><p>Each logical page contains at most 250 rejected faces.</p><p id=progress></p><button id=previous>Previous</button><button id=next>Next</button><button id=controls>Retained controls</button><label>Import CSV <input id=import type=file accept=.csv,text/csv></label><button id=export>Export complete CSV</button><main id=cards></main><p id=control-note class=hidden>Retained controls are a separate audit view and are not included in estimates.</p><script>const bundle={json.dumps(bundle_sha256)};const sample={json.dumps(sample_sha256)};const data={json.dumps(payload, separators=(",", ":"))};const labels=['clear','blurred','unusably_small','uncertain'];const key='findme-quality-sample:'+bundle;const pageSize=250;let page=0,showControls=false;let draft=loadDraft();function loadDraft(){{try{{const v=JSON.parse(localStorage.getItem(key)||'{{}}');return v&&typeof v==='object'?v:{{}}}}catch(_){{return {{}}}}}}function saveDraft(){{localStorage.setItem(key,JSON.stringify(draft))}}function visible(){{return showControls?data.controls:data.rejections}}function render(){{const rows=visible(),start=page*pageSize,items=rows.slice(start,start+pageSize),cards=document.querySelector('#cards');cards.replaceChildren(...items.map(item=>{{const card=document.createElement('article'),title=document.createElement('h2'),image=document.createElement('img');title.textContent=item.face_id;image.src=item.path;image.loading="lazy";image.alt='Review crop';card.append(title,image);if(!showControls){{const reasons=document.createElement('p');reasons.textContent=item.reasons.join(', ');const select=document.createElement('select');select.innerHTML='<option value="">Select label</option>'+labels.map(label=>'<option>'+label+'</option>').join('');select.value=draft[item.face_id]||'';select.onchange=()=>{{draft[item.face_id]=select.value;saveDraft();render()}};card.append(reasons,select)}}return card}}));document.querySelector('#progress').textContent=`${{showControls?'Controls':'Rejected faces'}} ${{start+1}}-${{Math.min(start+items.length,rows.length)}} of ${{rows.length}}; labels ${{Object.keys(draft).filter(id=>labels.includes(draft[id])).length}}/${{data.rejections.length}}`;document.querySelector('#control-note').classList.toggle('hidden',!showControls)}}document.querySelector('#previous').onclick=()=>{{page=Math.max(0,page-1);render()}};document.querySelector('#next').onclick=()=>{{page=Math.min(Math.ceil(visible().length/pageSize)-1,page+1);render()}};document.querySelector('#controls').onclick=()=>{{showControls=!showControls;page=0;render()}};document.addEventListener('keydown',event=>{{if(!showControls&&event.key >= '1' && event.key <= '4'){{const first=document.querySelector('select');if(first){{first.value=labels[Number(event.key)-1];first.dispatchEvent(new Event('change'))}}}}}});document.querySelector('#import').onchange=event=>{{const file=event.target.files[0];if(!file)return;const reader=new FileReader();reader.onload=()=>{{const rows=String(reader.result).trim().split(/\r?\n/).map(row=>row.split(','));if(rows.shift().join(',')!=='sample_sha256,face_id,label'||rows.length!==data.rejections.length||rows.some(row=>row.length!==3||row[0]!==sample||!labels.includes(row[2]))||new Set(rows.map(row=>row[1])).size!==data.rejections.length||new Set(rows.map(row=>row[1])).size!==new Set(data.rejections.map(row=>row.face_id)).size){{alert('Import must contain exactly one valid label for every sampled rejection');return}}draft=Object.fromEntries(rows.map(row=>[row[1],row[2]]));saveDraft();render()}};reader.readAsText(file)}};document.querySelector('#export').onclick=()=>{{const rows=data.rejections.map(row=>[sample,row.face_id,draft[row.face_id]]);if(rows.some(row=>!labels.includes(row[2]))){{alert('Label every sampled rejection before export');return}}const quote=v=>'"'+v.replaceAll('"','""')+'"';const csv=['sample_sha256,face_id,label',...rows.map(row=>row.map(quote).join(','))].join('\r\n')+'\r\n';const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([csv],{{type:'text/csv'}}));link.download='quality-sample-labels.csv';link.click();URL.revokeObjectURL(link.href)}};render();</script></body></html>\n"""


def _render_reviewer(
    sample: QualitySample,
    sample_sha256: str,
    bundle_sha256: str,
    crops: Mapping[str, str],
    controls: object,
) -> str:
    if isinstance(controls, list):
        controls = {
            f"{item['face_id']} | {item['metric']} | value={item['value']} | threshold={item['threshold']}": item[
                "path"
            ]
            for item in controls
            if isinstance(item, Mapping)
        }
    if not isinstance(controls, Mapping):
        raise TypeError("quality sample controls are invalid")
    report = _render_reviewer_legacy(sample, sample_sha256, bundle_sha256, crops, controls)
    parser = (
        "function parseCsvRows(text){const rows=[];let row=[],value='',quoted=false;"
        "for(let index=0;index<text.length;index++){const character=text[index];"
        "if(character==='\"'){if(quoted&&text[index+1]==='\"'){value+='\"';index++}"
        "else{quoted=!quoted}}else if(character===','&&!quoted){row.push(value);value=''}"
        "else if(character==='\\n'&&!quoted){row.push(value);rows.push(row);row=[];value=''}"
        "else if(character!=='\\r'){value+=character}}if(quoted)return null;"
        "if(value||row.length){row.push(value);rows.push(row)}return rows}"
    )
    report = report.replace("function render()", f"{parser}function render()", 1)
    old_parser = "String(reader.result).trim().split(/\r?\n/).map(row=>row.split(','))"
    if old_parser not in report:
        raise ValueError("quality sample reviewer import parser is missing")
    report = report.replace(old_parser, "parseCsvRows(String(reader.result))", 1)
    report = report.replace(
        "const rows=parseCsvRows(String(reader.result));if(rows.shift()",
        "const rows=parseCsvRows(String(reader.result));if(!rows||!rows.length||rows.shift()",
        1,
    )
    report = report.replace(
        "row.length!==3||row[0]!==sample||!labels.includes(row[2]))",
        "row.length!==3||row[0]!==sample||!labels.includes(row[2])||"
        "!data.rejections.some(item=>item.face_id===row[1]))",
        1,
    )
    invalid_line_endings = "join('\r\n')+'\r\n'"
    if invalid_line_endings not in report:
        raise ValueError("quality sample reviewer export is missing")
    report = report.replace(invalid_line_endings, "join('\\r\\n')+'\\r\\n'", 1)
    report = report.replace(
        "let page=0,showControls=false;",
        "let page=0,showControls=false,activeIndex=0;",
        1,
    ).replace("items.map(item=>", "items.map((item,index)=>", 1)
    report = report.replace(
        "select.onchange=()=>{draft[item.face_id]=select.value;saveDraft();render()}",
        "select.onfocus=()=>{activeIndex=start+index};select.onchange=()=>{draft[item.face_id]=select.value;saveDraft();render()}",
        1,
    )
    report = report.replace(
        "const first=document.querySelector('select');if(first){first.value=labels[Number(event.key)-1];first.dispatchEvent(new Event('change'))}",
        "const current=document.querySelectorAll('select')[activeIndex-page*pageSize];if(current){current.value=labels[Number(event.key)-1];current.dispatchEvent(new Event('change'));activeIndex=Math.min(data.rejections.length-1,activeIndex+1);render()}",
        1,
    )
    report = report.replace(
        "page=Math.max(0,page-1);render()",
        "page=Math.max(0,page-1);activeIndex=page*pageSize;render()",
        1,
    ).replace(
        "page=Math.min(Math.ceil(visible().length/pageSize)-1,page+1);render()",
        "page=Math.min(Math.ceil(visible().length/pageSize)-1,page+1);activeIndex=page*pageSize;render()",
        1,
    )
    report = report.replace(
        "showControls=!showControls;page=0;render()",
        "showControls=!showControls;page=0;activeIndex=0;render()",
        1,
    )
    return report.replace(
        "render();</script>",
        "globalThis.__qualitySampleReview={storageKey:key,state:()=>({page,activeIndex,draft:{...draft},total:data.rejections.length}),exportRows:()=>data.rejections.map(row=>[sample,row.face_id,draft[row.face_id]])};render();</script>",
        1,
    )


def _render_analysis(payload: Mapping[str, object]) -> str:
    gallery = _mapping(payload["review_gallery"])
    items = "".join(
        f'<li><img loading="lazy" src="{html.escape(path, quote=True)}" alt="Review crop"></li>'
        for label in ("clear", "uncertain")
        for path in _list(gallery[label])
    )
    proportions = _mapping(payload["weighted_proportions"])
    rates = "".join(
        f"<li>{html.escape(label)}: {proportions[label]}</li>" for label in sorted(proportions)
    )
    strata = _mapping(payload["by_reason_stratum"])
    stratum_rows = "".join(
        f"<li>{html.escape(name)}: {html.escape(json.dumps(value, sort_keys=True))}</li>"
        for name, value in sorted(strata.items())
    )
    controls = _list(payload["threshold_vicinity_controls"])
    control_rows = "".join(
        f"<li data-threshold-control>{html.escape(_string(_mapping(item)['metric']))}: value={_mapping(item)['value']}; threshold={_mapping(item)['threshold']}</li>"
        for item in controls
    )
    return f'<!doctype html><html><head><meta charset="utf-8"><title>Quality sample analysis</title></head><body><h1>Sampled quality evidence</h1><p>Reviewer: {html.escape(_string(payload["reviewer"]))}</p><ul>{rates}</ul><h2>By rejection-reason stratum</h2><ul>{stratum_rows}</ul><h2>Threshold-vicinity controls: {len(controls)}</h2><ul>{control_rows}</ul><h2>Clear and uncertain review gallery</h2><ul>{items}</ul></body></html>\n'


def _write_labels_template(path: Path, sample: QualitySample, identity: str) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\r\n")
        writer.writerow(LABEL_HEADERS)
        writer.writerows((identity, item.face_id, "") for item in sample.rejections)


def _validate_label_template(path: Path, sample: QualitySample, identity: str) -> None:
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.reader(stream))
    except (OSError, UnicodeDecodeError, csv.Error):
        raise ValueError("quality sample template is invalid") from None
    expected = [[*LABEL_HEADERS], *[[identity, item.face_id, ""] for item in sample.rejections]]
    if rows != expected:
        raise ValueError("quality sample template is invalid")


def _validate_reviewer(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 128
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError("quality sample reviewer is invalid")


def _validate_timestamp(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("quality sample timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("quality sample timestamp is invalid") from None
    if parsed.tzinfo is None:
        raise ValueError("quality sample timestamp is invalid")


def _safe_crop_path(value: str, directory: str) -> str:
    path = Path(value)
    if (
        path.is_absolute()
        or path.parts[:1] != (directory,)
        or len(path.parts) != 2
        or ".." in path.parts
    ):
        raise ValueError("quality sample crop path is unsafe")
    return path.as_posix()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
    )


def _write_text(path: Path, value: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise ValueError("quality sample file cannot be read") from None
    return digest.hexdigest()


def _bundle_file_rows(root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": item.relative_to(root).as_posix(),
            "sha256": _sha256_file(item),
            "size": item.stat().st_size,
        }
        for item in sorted(
            path for path in root.rglob("*") if path.is_file() and path.name != "manifest.json"
        )
    ]


def _bundle_identity_file_rows(root: Path, embedded_digest: str) -> list[dict[str, object]]:
    rows = _bundle_file_rows(root)
    report = (root / "report.html").read_text(encoding="utf-8")
    if report.count(embedded_digest) != 1:
        raise ValueError("quality sample report digest is invalid")
    normalized = report.replace(embedded_digest, _DIGEST_PLACEHOLDER).encode("utf-8")
    for row in rows:
        if row["path"] == "report.html":
            row["sha256"] = hashlib.sha256(normalized).hexdigest()
            row["size"] = len(normalized)
    return rows


def _validate_bundle_files(root: Path, value: object) -> None:
    rows = _list(value)
    declared: set[str] = set()
    for raw in rows:
        item = _require(raw, {"path", "sha256", "size"})
        relative = _string(item["path"])
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or relative in declared:
            raise ValueError("quality sample file manifest is invalid")
        target = root / path
        if (
            target.is_symlink()
            or not target.is_file()
            or _integer(item["size"]) != target.stat().st_size
            or _digest(item["sha256"], "file") != _sha256_file(target)
        ):
            raise ValueError("quality sample file differs")
        declared.add(relative)
    actual = {
        item.relative_to(root).as_posix()
        for item in root.rglob("*")
        if item.is_file() and item.name != "manifest.json"
    }
    if declared != actual or any(item.is_symlink() for item in root.rglob("*")):
        raise ValueError("quality sample file coverage is invalid")


def _load_json(path: Path) -> Mapping[str, object]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("quality sample JSON cannot be read") from None


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("quality sample JSON object is invalid")
    return value


def _require(value: object, keys: set[str]) -> Mapping[str, object]:
    item = _mapping(value)
    if set(item) != keys:
        raise ValueError("quality sample schema is invalid")
    return item


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("quality sample list is invalid")
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("quality sample string is invalid")
    return value


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("quality sample integer is invalid")
    return value


def _number(value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("quality sample number is invalid")
    return value


def _digest(value: object, kind: str) -> str:
    text = _string(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"quality sample {kind} digest is invalid")
    return text

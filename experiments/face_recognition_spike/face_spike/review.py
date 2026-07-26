from __future__ import annotations

# ruff: noqa: E501, I001 -- static HTML, CSS, and browser-only JavaScript stay readable in one template.

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .cluster_report import render_review_detail_page, render_review_index
from .comparison import ComparisonError, _load_reference, _load_run

_COMPARISON_FILES = (
    "comparison.json",
    "metrics.json",
    "people-comparison.csv",
    "people-comparison.html",
    "manifest.json",
)
_DECISIONS = ("same", "different", "uncertain")


class ReviewError(ValueError):
    """A fatal review input or immutable publication error."""


@dataclass(frozen=True)
class ReviewConfig:
    run: Path
    comparison: Path
    peakshot_export: Path
    output: Path

    def validate(self) -> None:
        output = self.output.resolve(strict=False)
        if os.path.lexists(self.output):
            raise ReviewError("output path already exists")
        if (
            _intersects(output, self.run.resolve(strict=False))
            or _intersects(output, self.comparison.resolve(strict=False))
            or _intersects(output, self.peakshot_export.resolve(strict=False))
        ):
            raise ReviewError("output path intersects input")


def run_review(config: ReviewConfig) -> dict[str, Any]:
    """Publish a separate immutable local review bundle without copying media."""
    config.validate()
    clusters = _load_clusters(config.run)
    comparison = _load_comparison(config.comparison, config.run)
    reference = _load_peakshot_reference(config.peakshot_export, config.comparison)
    original_metrics, original_metrics_bytes = _load_metrics(config.comparison)
    pairs = _fragmentation_pairs(comparison, clusters)
    identity = _bundle_identity(config.run, config.comparison)
    data = {
        "baseline_filtered_relationship_metrics": _relationship_metrics(
            clusters, comparison, reference.people, frozenset()
        ),
        "bundle_id": identity,
        "clusters": clusters,
        "comparison": comparison,
        "original_metrics": original_metrics,
        "pairs": pairs,
        "reference_people": {
            person_id: sorted(filenames) for person_id, filenames in reference.people.items()
        },
    }
    _publish(config, data, original_metrics_bytes)
    return data


def _load_clusters(run: Path) -> list[dict[str, Any]]:
    try:
        _load_run(run)
    except ComparisonError as error:
        raise ReviewError(f"completed run is invalid: {error}") from None
    payload = _json(run / "clusters.json", "clusters")
    records = payload.get("clusters") if isinstance(payload, Mapping) else None
    faces_payload = _json(run / "faces.json", "faces")
    images = faces_payload.get("images") if isinstance(faces_payload, Mapping) else None
    if not isinstance(records, list) or not isinstance(images, list):
        raise ReviewError("completed run review data is malformed")
    assets = {
        str(face.get("face_id")): Path(str(face.get("crop_path"))).name
        for image in images
        if isinstance(image, Mapping)
        for face in image.get("faces", [])
        if isinstance(face, Mapping) and isinstance(face.get("face_id"), str)
    }
    clusters: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, Mapping):
            raise ReviewError("completed run review data is malformed")
        cluster_id = record.get("cluster_id")
        representative = record.get("representative_face_id")
        members = record.get("members")
        if (
            not isinstance(cluster_id, str)
            or not isinstance(representative, str)
            or not isinstance(members, list)
        ):
            raise ReviewError("completed run review data is malformed")
        member_data: list[dict[str, str]] = []
        filenames: set[str] = set()
        for member in members:
            if not isinstance(member, Mapping):
                raise ReviewError("completed run review data is malformed")
            face_id, filename = member.get("face_id"), member.get("filename")
            if (
                not isinstance(face_id, str)
                or not isinstance(filename, str)
                or face_id not in assets
            ):
                raise ReviewError("completed run review data is malformed")
            member_data.append({"asset": assets[face_id], "face_id": face_id})
            filenames.add(filename)
        if representative not in assets or not member_data:
            raise ReviewError("completed run review data is malformed")
        clusters.append(
            {
                "cluster_id": cluster_id,
                "face_count": len(member_data),
                "filenames": sorted(filenames),
                "members": sorted(member_data, key=lambda item: item["face_id"]),
                "photo_count": len(filenames),
                "representative_asset": assets[representative],
            }
        )
    return sorted(clusters, key=_cluster_sort_key)


def _load_comparison(comparison: Path, run: Path) -> Mapping[str, Any]:
    if not comparison.is_dir() or not all(
        (comparison / name).is_file() for name in _COMPARISON_FILES
    ):
        raise ReviewError("completed comparison is missing required artifacts")
    manifest = _json(comparison / "manifest.json", "comparison manifest")
    payload = _json(comparison / "comparison.json", "comparison")
    if not isinstance(manifest, Mapping) or not isinstance(payload, Mapping):
        raise ReviewError("completed comparison is malformed")
    required = {"artifacts", "counts", "peakshot_export_basename", "run_basename"}
    if set(manifest) != required or set(manifest.get("artifacts", [])) != set(
        _COMPARISON_FILES[:-1]
    ):
        raise ReviewError("completed comparison is malformed")
    if manifest.get("run_basename") != run.name:
        raise ReviewError("completed comparison belongs to a different run")
    people = payload.get("people")
    if not isinstance(people, list):
        raise ReviewError("completed comparison is malformed")
    return payload


def _load_peakshot_reference(peakshot_export: Path, comparison: Path) -> Any:
    try:
        reference = _load_reference(peakshot_export)
    except ComparisonError as error:
        raise ReviewError(f"Peakshot export is invalid: {error}") from None
    manifest = _json(comparison / "manifest.json", "comparison manifest")
    if (
        not isinstance(manifest, Mapping)
        or manifest.get("peakshot_export_basename") != peakshot_export.name
    ):
        raise ReviewError("Peakshot export does not match completed comparison")
    return reference


def _load_metrics(comparison: Path) -> tuple[Mapping[str, Any], bytes]:
    try:
        raw = (comparison / "metrics.json").read_bytes()
        metrics = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReviewError("comparison metrics artifact is malformed") from error
    if not isinstance(metrics, Mapping):
        raise ReviewError("completed comparison metrics are malformed")
    return metrics, raw


def _relationship_metrics(
    clusters: Sequence[Mapping[str, Any]],
    comparison: Mapping[str, Any],
    reference_people: Mapping[str, frozenset[str]],
    excluded: frozenset[str],
) -> dict[str, float | None]:
    cluster_filenames = {
        str(cluster["cluster_id"]): frozenset(str(name) for name in cluster["filenames"])
        for cluster in clusters
    }
    intersection_total = actual_total = expected_total = 0
    people = comparison.get("people")
    if not isinstance(people, list):
        raise ReviewError("completed comparison is malformed")
    for row in people:
        if not isinstance(row, Mapping):
            raise ReviewError("completed comparison is malformed")
        person_id = row.get("peakshot_person_id")
        matched_ids = row.get("matched_cluster_ids")
        if (
            not isinstance(person_id, str)
            or not isinstance(matched_ids, str)
            or person_id not in reference_people
        ):
            raise ReviewError("completed comparison is malformed")
        actual = frozenset(
            filename
            for cluster_id in matched_ids.split(";")
            if cluster_id and cluster_id not in excluded
            for filename in cluster_filenames.get(cluster_id, ())
        )
        expected = reference_people[person_id]
        intersection_total += len(actual & expected)
        actual_total += len(actual)
        expected_total += len(expected)
    precision = _ratio(intersection_total, actual_total)
    recall = _ratio(intersection_total, expected_total)
    return {
        "f1": _ratio(2 * intersection_total, actual_total + expected_total),
        "precision": precision,
        "recall": recall,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _fragmentation_pairs(
    comparison: Mapping[str, Any], clusters: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    known = {str(cluster["cluster_id"]) for cluster in clusters}
    pairs: list[dict[str, Any]] = []
    people = comparison["people"]
    assert isinstance(people, list)
    for row in people:
        if not isinstance(row, Mapping):
            raise ReviewError("completed comparison is malformed")
        person_id = row.get("peakshot_person_id")
        raw_ids = row.get("matched_cluster_ids")
        if not isinstance(person_id, str) or not isinstance(raw_ids, str):
            raise ReviewError("completed comparison is malformed")
        ids = sorted({item for item in raw_ids.split(";") if item}, key=_cluster_id_key)
        if any(item not in known for item in ids):
            raise ReviewError("comparison references an unknown cluster")
        for left_index, left in enumerate(ids):
            for right in ids[left_index + 1 :]:
                pairs.append(
                    {
                        "cluster_ids": [left, right],
                        "key": f"{person_id}|{left}|{right}",
                        "peakshot_person_id": person_id,
                    }
                )
    return sorted(
        pairs,
        key=lambda pair: (
            str(pair["peakshot_person_id"]),
            *_cluster_id_key(str(pair["cluster_ids"][0])),
            *_cluster_id_key(str(pair["cluster_ids"][1])),
        ),
    )


def _publish(config: ReviewConfig, data: Mapping[str, Any], original_metrics_bytes: bytes) -> None:
    config.output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{config.output.name}.", dir=config.output.parent))
    try:
        root_prefix = _relative_prefix(config.run, staging)
        _write(
            staging / "report.html", render_review_index(data["clusters"], media_prefix=root_prefix)
        )
        for cluster in data["clusters"]:
            cluster_id = str(cluster["cluster_id"])
            page_prefix = _relative_prefix(config.run, staging / "people" / cluster_id)
            target = staging / "people" / cluster_id / "index.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            _write(target, render_review_detail_page(cluster, media_prefix=page_prefix))
        _write_json(staging / "fragmentation-data.json", data)
        (staging / "original-metrics.json").write_bytes(original_metrics_bytes)
        _write(staging / "fragmentation-review.html", _render_fragmentation_page(data, root_prefix))
        _write_json(
            staging / "manifest.json",
            {
                "artifacts": [
                    "fragmentation-data.json",
                    "fragmentation-review.html",
                    "original-metrics.json",
                    "report.html",
                ],
                "bundle_id": data["bundle_id"],
                "comparison_basename": config.comparison.name,
                "counts": {"clusters": len(data["clusters"]), "pairs": len(data["pairs"])},
                "run_basename": config.run.name,
            },
        )
        if os.path.lexists(config.output):
            raise ReviewError("output path already exists")
        os.replace(staging, config.output)
    except BaseException as error:
        try:
            shutil.rmtree(staging)
        except BaseException as cleanup_error:
            error.add_note(f"could not remove review staging directory: {cleanup_error}")
        raise


def _render_fragmentation_page(data: Mapping[str, Any], root_prefix: str) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Fragmentation review</title>
<style>body{{font:16px system-ui;margin:2rem;max-width:1100px}}.pair,.metric{{border:1px solid #bbb;padding:1rem;margin:1rem 0}}.clusters,.metrics{{display:flex;gap:1rem;flex-wrap:wrap}}.cluster{{max-width:180px}}figure{{margin:0;max-width:180px}}img{{max-width:100%;max-height:180px;object-fit:contain;display:block}}button[aria-pressed=true]{{font-weight:700;background:#cfe}}.not-applicable{{color:#944;font-weight:700}}.muted{{color:#555}}table{{border-collapse:collapse}}th,td{{border:1px solid #bbb;padding:.3rem;text-align:left}}</style></head><body>
<p><a href="report.html">Cluster index</a></p><h1>Fragmentation review</h1>
<p>Photo-level overlap is context, not proof of identity. Inspect face crops before choosing a decision. Quality labels and provisional values are manual review aids; they never alter the completed run or comparison.</p>
<section class="metrics"><div class="metric"><h2>Immutable original metrics</h2><p><a href="original-metrics.json">Byte-preserved source artifact</a></p><pre id="original-metrics"></pre></div><div class="metric"><h2>Provisional filtered metrics</h2><p class="muted">Excludes only clusters explicitly labelled not_face, low_quality, or mixed. Unreviewed clusters stay included.</p><pre id="filtered-metrics"></pre></div><div class="metric"><h2>Manual fragmentation</h2><p class="muted">Same decisions form virtual components only here; relationship metrics above stay unchanged.</p><pre id="fragmentation-summary"></pre></div></section>
<label>View <select id="view"><option value="pairs">fragmentation pairs</option><option value="clusters">cluster quality</option></select></label><label>Filter <select id="filter"><option value="all">all</option><option value="unreviewed">unreviewed</option><option value="same">identity same</option><option value="different">identity different</option><option value="uncertain">identity uncertain</option><option value="not_applicable">not applicable</option><option value="not_face">quality not_face</option><option value="low_quality">quality low_quality</option><option value="mixed">quality mixed</option><option value="usable">quality usable</option></select></label>
<button id="export">Export combined CSV</button><button id="export-clusters">Export cluster CSV</button><label>Import combined CSV <input id="import" type="file" accept=".csv,text/csv"></label><label>Import cluster CSV <input id="import-clusters" type="file" accept=".csv,text/csv"></label><p id="progress"></p><button id="previous">Previous</button><span id="page"></span><button id="next">Next</button><main id="pairs"></main>
<script id="review-data" type="application/json">{payload}</script><script>
(() => {{
const data=JSON.parse(document.getElementById('review-data').textContent), decisions=["same", "different", "uncertain"], qualities=["unreviewed", "usable", "not_face", "low_quality", "mixed"], evidence=["direct", "group_photo_ambiguous"], unusable=new Set(["not_face","low_quality","mixed"]), pairKey="face-spike-fragmentation-v2:pairs:"+data.bundle_id, qualityKey="face-spike-fragmentation-v2:quality:"+data.bundle_id, known=new Set(data.pairs.map(p=>p.key));
const clusters=new Map(data.clusters.map(c=>[c.cluster_id,c])), alignments=new Map(data.comparison.cluster_alignment.map(a=>[a.cluster_id,a])); let pairAnnotations=read(pairKey), qualityAnnotations=read(qualityKey), page=0; const pageSize=25;
function read(key){{try{{const x=JSON.parse(localStorage.getItem(key)||'{{}}');return x&&typeof x==='object'&&!Array.isArray(x)?x:{{}}}}catch(_e){{return {{}}}}}}
function save(){{localStorage.setItem(pairKey,JSON.stringify(pairAnnotations));localStorage.setItem(qualityKey,JSON.stringify(qualityAnnotations));render()}}
function quality(id){{return qualityAnnotations[id]||"unreviewed"}}
function pairAnnotation(key){{const x=pairAnnotations[key]||{{}};return {{decision:x.decision||"",evidence:x.evidence||"direct"}}}}
function rows(){{return data.pairs.map(p=>{{const annotation=pairAnnotation(p.key), qs=p.cluster_ids.map(quality);return {{...p,...annotation,qualities:qs,notApplicable:qs.some(q=>unusable.has(q))}}}})}}
function ratio(n,d){{return d? n/d:null}}function f1(i,a,e){{return a+e?2*i/(a+e):null}}function display(value){{return value===null?"n/a":Number(value).toFixed(4)}}
function filteredMetrics(){{const kept=data.clusters.filter(c=>!unusable.has(quality(c.cluster_id))), ids=new Set(kept.map(c=>c.cluster_id));let intersection=0,actual=0,purity=0;const people=new Map(data.comparison.people.map(p=>[p.peakshot_person_id,p])), referencePeople=new Map(Object.entries(data.reference_people).map(([id,files])=>[id,new Set(files)]));for(const person of people.values()){{const selected=person.matched_cluster_ids?person.matched_cluster_ids.split(';').filter(id=>ids.has(id)):[], files=new Set(selected.flatMap(id=>clusters.get(id).filenames)), expectedFiles=referencePeople.get(person.peakshot_person_id);actual+=files.size;intersection+=[...files].filter(filename=>expectedFiles.has(filename)).length}}for(const c of kept){{const a=alignments.get(c.cluster_id);purity+=a&&a.overlaps.length?Math.max(...a.overlaps.map(o=>o.intersection_count)):0}}const expected=[...referencePeople.values()].reduce((n,files)=>n+files.size,0), unmatched=kept.filter(c=>!alignments.get(c.cluster_id).primary_person_id), singleton=kept.filter(c=>c.face_count===1), fragmented=[...people.values()].filter(p=>p.matched_cluster_ids&&p.matched_cluster_ids.split(';').filter(id=>ids.has(id)).length>1);const reviewed=data.clusters.filter(c=>quality(c.cluster_id)!=="unreviewed").length;return {{review_coverage:`${{reviewed}} / ${{data.clusters.length}} clusters`,clusters:`${{kept.length}} included; ${{data.clusters.length-kept.length}} excluded`,precision:display(ratio(intersection,actual)),recall:display(ratio(intersection,expected)),f1:display(f1(intersection,actual,expected)),purity:display(ratio(purity,kept.reduce((n,c)=>n+c.photo_count,0))),unmatched_clusters:unmatched.length,singleton_clusters:`${{singleton.length}} total; ${{singleton.filter(c=>!unmatched.includes(c)).length}} matched; ${{singleton.filter(c=>unmatched.includes(c)).length}} unmatched`,fragmented_people:fragmented.length}}}}
function manualFragmentation(){{const kept=new Set(data.clusters.filter(c=>!unusable.has(quality(c.cluster_id))).map(c=>c.cluster_id));let original=0,components=0,unresolved=0;for(const person of data.comparison.people){{const ids=person.matched_cluster_ids?person.matched_cluster_ids.split(';').filter(id=>kept.has(id)):[];if(ids.length<2)continue;original+=ids.length;const parent=new Map(ids.map(id=>[id,id]));const find=id=>parent.get(id)===id?id:parent.set(id,find(parent.get(id))).get(id);for(const pair of rows().filter(p=>p.peakshot_person_id===person.peakshot_person_id&&p.decision==="same"&&!p.notApplicable)){{const [left,right]=pair.cluster_ids;if(kept.has(left)&&kept.has(right))parent.set(find(left),find(right))}}components+=new Set(ids.map(find)).size;unresolved+=rows().filter(p=>p.peakshot_person_id===person.peakshot_person_id&&!p.notApplicable&&!p.decision).length}}return {{original_algorithmic_clusters:original,confirmed_distinct_components:components,unresolved_pairs:unresolved}}}}
function renderMetrics(){{document.getElementById('original-metrics').textContent=JSON.stringify(data.original_metrics,null,2);document.getElementById('filtered-metrics').textContent=JSON.stringify(filteredMetrics(),null,2);document.getElementById('fragmentation-summary').textContent=JSON.stringify(manualFragmentation(),null,2)}}
function render(){{const filter=document.getElementById('filter').value, view=document.getElementById('view').value, pairRows=rows(), all=view==='pairs'?pairRows:data.clusters, filtered=view==='pairs'?all.filter(p=>filter==='all'||(filter==='unreviewed'?!p.decision:filter==='not_applicable'?p.notApplicable:qualities.includes(filter)?p.qualities.includes(filter):p.decision===filter)):all.filter(c=>filter==='all'||(qualities.includes(filter)&&quality(c.cluster_id)===filter)), last=Math.max(0,Math.ceil(filtered.length/pageSize)-1);page=Math.min(page,last);const visible=filtered.slice(page*pageSize,(page+1)*pageSize); const qualityReviewed=data.clusters.filter(c=>quality(c.cluster_id)!=='unreviewed').length;document.getElementById('progress').textContent=`${{pairRows.filter(p=>p.decision).length}} identity decisions / ${{pairRows.length}} pairs; ${{qualityReviewed}} / ${{data.clusters.length}} clusters quality-reviewed`;document.getElementById('page').textContent=`page ${{page+1}} / ${{last+1}}`;document.getElementById('previous').disabled=page===0;document.getElementById('next').disabled=page===last;document.getElementById('pairs').replaceChildren(...visible.map(view==='pairs'?card:clusterCard));renderMetrics()}}
function choiceButtons(section,values,current,onChoose){{for(const choice of values){{const button=document.createElement('button');button.textContent=choice;button.setAttribute('aria-pressed',String(current===choice));button.onclick=()=>{{onChoose(choice);save()}};section.append(button)}}}}
function card(pair){{const section=document.createElement('section');section.className='pair';const title=document.createElement('h2');title.textContent=`Peakshot ${{pair.peakshot_person_id}}: ${{pair.cluster_ids.join(' + ')}}`;section.append(title);if(pair.notApplicable){{const status=document.createElement('p');status.className='not-applicable';status.textContent='Identity analysis: not_applicable (one or both clusters are manually unusable). Stored identity decision is retained.';section.append(status)}}const wrap=document.createElement('div');wrap.className='clusters';for(const id of pair.cluster_ids){{const c=clusters.get(id),box=document.createElement('div');box.className='cluster';const a=document.createElement('a');a.href=`people/${{encodeURIComponent(id)}}/index.html`;const img=document.createElement('img');img.loading='lazy';img.src=`{root_prefix}people/${{encodeURIComponent(id)}}/faces/${{encodeURIComponent(c.representative_asset)}}`;img.alt=id;a.append(img,document.createTextNode(`${{id}} (${{c.face_count}} faces, ${{c.photo_count}} photos)`));box.append(a);const label=document.createElement('p');label.textContent='quality: ';box.append(label);choiceButtons(box,qualities,quality(id),choice=>{{if(choice==='unreviewed')delete qualityAnnotations[id];else qualityAnnotations[id]=choice}});for(const face of c.members.slice(0,3)){{const sample=document.createElement('img');sample.loading='lazy';sample.src=`{root_prefix}people/${{encodeURIComponent(id)}}/faces/${{encodeURIComponent(face.asset)}}`;sample.alt=face.face_id;box.append(sample)}}wrap.append(box)}}section.append(wrap);const identityLabel=document.createElement('p');identityLabel.textContent='identity decision: ';section.append(identityLabel);choiceButtons(section,decisions,pair.decision,choice=>{{pairAnnotations[pair.key]={{...pairAnnotations[pair.key],decision:choice}}}});const clear=document.createElement('button');clear.textContent='clear identity';clear.onclick=()=>{{const next={{...pairAnnotations[pair.key]}};delete next.decision;if(Object.keys(next).length)pairAnnotations[pair.key]=next;else delete pairAnnotations[pair.key];save()}};section.append(clear);const evidenceLabel=document.createElement('p');evidenceLabel.textContent='evidence quality: ';section.append(evidenceLabel);choiceButtons(section,evidence,pair.evidence,choice=>{{pairAnnotations[pair.key]={{...pairAnnotations[pair.key],evidence:choice}}}});return section}}
function clusterCard(c){{const section=document.createElement('section');section.className='pair';const title=document.createElement('h2'),a=document.createElement('a');a.href=`people/${{encodeURIComponent(c.cluster_id)}}/index.html`;a.textContent=`${{c.cluster_id}} (${{c.face_count}} faces, ${{c.photo_count}} photos)`;title.append(a);section.append(title);const image=document.createElement('img');image.loading='lazy';image.src=`{root_prefix}people/${{encodeURIComponent(c.cluster_id)}}/faces/${{encodeURIComponent(c.representative_asset)}}`;image.alt=c.cluster_id;section.append(image);const label=document.createElement('p');label.textContent='quality: ';section.append(label);choiceButtons(section,qualities,quality(c.cluster_id),choice=>{{if(choice==='unreviewed')delete qualityAnnotations[c.cluster_id];else qualityAnnotations[c.cluster_id]=choice}});return section}}
function combinedCsv(){{const lines=['bundle_id,peakshot_person_id,left_cluster_id,right_cluster_id,identity_decision,evidence_quality,left_cluster_quality,right_cluster_quality'];for(const p of rows())lines.push([data.bundle_id,p.peakshot_person_id,p.cluster_ids[0],p.cluster_ids[1],p.decision,p.evidence,p.qualities[0],p.qualities[1]].join(','));return lines.join('\\r\\n')+'\\r\\n'}}
function clusterCsv(){{const lines=['bundle_id,cluster_id,quality'];for(const c of data.clusters)lines.push([data.bundle_id,c.cluster_id,quality(c.cluster_id)].join(','));return lines.join('\\r\\n')+'\\r\\n'}}
function csvLines(text,header){{const lines=text.replace(/^\\uFEFF/,'').split(/\\r?\\n/).filter(Boolean);if(!lines.length||lines[0]!==header)throw Error('unexpected CSV headers');return lines.slice(1).map(line=>line.split(','))}}
function parseCombined(text){{const nextPairs={{}},nextQualities={{}},seenQualities={{}},seen=new Set;for(const row of csvLines(text,'bundle_id,peakshot_person_id,left_cluster_id,right_cluster_id,identity_decision,evidence_quality,left_cluster_quality,right_cluster_quality')){{if(row.length!==8)throw Error('malformed CSV row');const [bundle,person,left,right,decision,evidenceQuality,leftQuality,rightQuality]=row,pair=`${{person}}|${{left}}|${{right}}`;if(bundle!==data.bundle_id)throw Error('bundle identity mismatch');if(seen.has(pair))throw Error('duplicate pair key');if(!known.has(pair))throw Error('unknown pair key');if(decision&&!decisions.includes(decision))throw Error('invalid identity decision');if(!evidence.includes(evidenceQuality)||!qualities.includes(leftQuality)||!qualities.includes(rightQuality))throw Error('invalid annotation state');if(seenQualities[left]!==undefined&&seenQualities[left]!==leftQuality||seenQualities[right]!==undefined&&seenQualities[right]!==rightQuality)throw Error('inconsistent repeated cluster quality');seenQualities[left]=leftQuality;seenQualities[right]=rightQuality;seen.add(pair);if(decision||evidenceQuality!=="direct")nextPairs[pair]={{...(decision?{{decision}}:{{}}),...(evidenceQuality!=="direct"?{{evidence:evidenceQuality}}:{{}})}};if(leftQuality!=="unreviewed")nextQualities[left]=leftQuality;if(rightQuality!=="unreviewed")nextQualities[right]=rightQuality}}if(seen.size!==known.size)throw Error('combined CSV must contain every pair');return {{pairs:nextPairs,qualities:nextQualities}}}}
function parseClusters(text){{const next={{}},seen=new Set;for(const row of csvLines(text,'bundle_id,cluster_id,quality')){{if(row.length!==3)throw Error('malformed CSV row');const [bundle,id,state]=row;if(bundle!==data.bundle_id)throw Error('bundle identity mismatch');if(seen.has(id))throw Error('duplicate cluster id');if(!clusters.has(id))throw Error('unknown cluster id');if(!qualities.includes(state))throw Error('invalid quality state');seen.add(id);if(state!=="unreviewed")next[id]=state}}if(seen.size!==clusters.size)throw Error('cluster CSV must contain every cluster');return next}}
function download(text,name){{const url=URL.createObjectURL(new Blob([text],{{type:'text/csv'}})),a=document.createElement('a');a.href=url;a.download=name;a.click();URL.revokeObjectURL(url)}}
document.getElementById('view').onchange=()=>{{page=0;render()}};document.getElementById('filter').onchange=()=>{{page=0;render()}};document.getElementById('previous').onclick=()=>{{page--;render()}};document.getElementById('next').onclick=()=>{{page++;render()}};document.getElementById('export').onclick=()=>download(combinedCsv(),'fragmentation-annotations.csv');document.getElementById('export-clusters').onclick=()=>download(clusterCsv(),'cluster-quality.csv');document.getElementById('import').onchange=e=>{{const file=e.target.files[0];if(!file)return;file.text().then(text=>{{const next=parseCombined(text);pairAnnotations=next.pairs;qualityAnnotations=next.qualities;save()}}).catch(error=>alert(error.message));e.target.value=''}};document.getElementById('import-clusters').onchange=e=>{{const file=e.target.files[0];if(!file)return;file.text().then(text=>{{const next=parseClusters(text);qualityAnnotations=next;save()}}).catch(error=>alert(error.message));e.target.value=''}};render();
}})();</script></body></html>\n"""


def _bundle_identity(run: Path, comparison: Path) -> str:
    digest = hashlib.sha256()
    for path in (run / "manifest.json", comparison / "manifest.json"):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _json(path: Path, name: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewError(f"{name} artifact is malformed") from error


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _relative_prefix(target: Path, page_directory: Path) -> str:
    return quote(os.path.relpath(target, page_directory), safe="/-._~") + "/"


def _intersects(first: Path, second: Path) -> bool:
    try:
        first.relative_to(second)
        return True
    except ValueError:
        try:
            second.relative_to(first)
            return True
        except ValueError:
            return False


def _cluster_id_key(value: str) -> tuple[int, str]:
    try:
        return int(value.partition("-")[2]), value
    except ValueError as error:
        raise ReviewError("comparison references a malformed cluster") from error


def _cluster_sort_key(cluster: Mapping[str, Any]) -> tuple[int, str]:
    return _cluster_id_key(str(cluster["cluster_id"]))

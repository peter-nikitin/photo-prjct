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
from .comparison import ComparisonError, _load_run

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
    output: Path

    def validate(self) -> None:
        output = self.output.resolve(strict=False)
        if os.path.lexists(self.output):
            raise ReviewError("output path already exists")
        if _intersects(output, self.run.resolve(strict=False)) or _intersects(
            output, self.comparison.resolve(strict=False)
        ):
            raise ReviewError("output path intersects input")


def run_review(config: ReviewConfig) -> dict[str, Any]:
    """Publish a separate immutable local review bundle without copying media."""
    config.validate()
    clusters = _load_clusters(config.run)
    comparison = _load_comparison(config.comparison, config.run)
    pairs = _fragmentation_pairs(comparison, clusters)
    identity = _bundle_identity(config.run, config.comparison)
    data = {"bundle_id": identity, "clusters": clusters, "pairs": pairs}
    _publish(config, data)
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


def _publish(config: ReviewConfig, data: Mapping[str, Any]) -> None:
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
        _write(staging / "fragmentation-review.html", _render_fragmentation_page(data, root_prefix))
        _write_json(
            staging / "manifest.json",
            {
                "artifacts": [
                    "fragmentation-data.json",
                    "fragmentation-review.html",
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
<style>body{{font:16px system-ui;margin:2rem}}.pair{{border:1px solid #bbb;padding:1rem;margin:1rem 0}}.clusters{{display:flex;gap:1rem;flex-wrap:wrap}}figure{{margin:0;max-width:180px}}img{{max-width:100%;max-height:180px;object-fit:contain;display:block}}button[aria-pressed=true]{{font-weight:700;background:#cfe}}</style></head><body>
<p><a href="report.html">Cluster index</a></p><h1>Fragmentation review</h1>
<p>Photo-level overlap is context, not proof of identity. Inspect face crops before choosing a decision.</p>
<label>Filter <select id="filter"><option value="all">all</option><option value="unreviewed">unreviewed</option><option value="same">same</option><option value="different">different</option><option value="uncertain">uncertain</option></select></label>
<button id="export">Export CSV</button><label>Import CSV <input id="import" type="file" accept=".csv,text/csv"></label><p id="progress"></p><button id="previous">Previous</button><span id="page"></span><button id="next">Next</button><main id="pairs"></main>
<script id="review-data" type="application/json">{payload}</script><script>
(() => {{
const data=JSON.parse(document.getElementById('review-data').textContent), decisions=["same", "different", "uncertain"], key="face-spike-fragmentation-v1:"+data.bundle_id, known=new Set(data.pairs.map(p=>p.key));
const clusters=new Map(data.clusters.map(c=>[c.cluster_id,c])); let stored=read(), page=0; const pageSize=25;
function read(){{try{{const x=JSON.parse(localStorage.getItem(key)||'{{}}');return x&&typeof x==='object'&&!Array.isArray(x)?x:{{}}}}catch(_e){{return {{}}}}}}
function save(){{localStorage.setItem(key,JSON.stringify(stored));render()}}
function rows(){{return data.pairs.map(p=>({{...p,decision:stored[p.key]||''}}))}}
function render(){{const filter=document.getElementById('filter').value, all=rows(), filtered=all.filter(p=>filter==='all'||(filter==='unreviewed'?!p.decision:p.decision===filter)), last=Math.max(0,Math.ceil(filtered.length/pageSize)-1);page=Math.min(page,last);const visible=filtered.slice(page*pageSize,(page+1)*pageSize); document.getElementById('progress').textContent=`${{all.filter(p=>p.decision).length}} reviewed / ${{all.length}} total`;document.getElementById('page').textContent=`page ${{page+1}} / ${{last+1}}`;document.getElementById('previous').disabled=page===0;document.getElementById('next').disabled=page===last;document.getElementById('pairs').replaceChildren(...visible.map(card));}}
function card(pair){{const section=document.createElement('section');section.className='pair';const title=document.createElement('h2');title.textContent=`Peakshot ${{pair.peakshot_person_id}}: ${{pair.cluster_ids.join(' + ')}}`;section.append(title);const wrap=document.createElement('div');wrap.className='clusters';for(const id of pair.cluster_ids){{const c=clusters.get(id),box=document.createElement('div'),a=document.createElement('a');a.href=`people/${{encodeURIComponent(id)}}/index.html`;const img=document.createElement('img');img.loading='lazy';img.src=`{root_prefix}people/${{encodeURIComponent(id)}}/faces/${{encodeURIComponent(c.representative_asset)}}`;img.alt=id;a.append(img,document.createTextNode(`${{id}} (${{c.face_count}} faces, ${{c.photo_count}} photos)`));box.append(a);for(const face of c.members.slice(0,3)){{const sample=document.createElement('img');sample.loading='lazy';sample.src=`{root_prefix}people/${{encodeURIComponent(id)}}/faces/${{encodeURIComponent(face.asset)}}`;sample.alt=face.face_id;box.append(sample)}}wrap.append(box)}}section.append(wrap);for(const choice of decisions){{const button=document.createElement('button');button.textContent=choice;button.setAttribute('aria-pressed',String(pair.decision===choice));button.onclick=()=>{{stored[pair.key]=choice;save()}};section.append(button)}}const clear=document.createElement('button');clear.textContent='clear';clear.onclick=()=>{{delete stored[pair.key];save()}};section.append(clear);return section}}
function csv(){{const lines=['bundle_id,peakshot_person_id,left_cluster_id,right_cluster_id,decision'];for(const p of rows().filter(x=>x.decision)) lines.push([data.bundle_id,p.peakshot_person_id,p.cluster_ids[0],p.cluster_ids[1],p.decision].join(','));return lines.join('\\r\\n')+'\\r\\n'}}
function parse(text){{const lines=text.replace(/^\\uFEFF/,'').split(/\\r?\\n/).filter(Boolean), header='bundle_id,peakshot_person_id,left_cluster_id,right_cluster_id,decision';if(!lines.length||lines[0]!==header)throw Error('unexpected CSV headers');const next={{}},seen=new Set;for(const line of lines.slice(1)){{const row=line.split(',');if(row.length!==5||row.some(x=>!x))throw Error('malformed CSV row');const [bundle,person,left,right,decision]=row,pairKey=`${{person}}|${{left}}|${{right}}`;if(bundle!==data.bundle_id)throw Error('bundle identity mismatch');if(seen.has(pairKey))throw Error('duplicate pair key');if(!known.has(pairKey))throw Error('unknown pair key');if(!decisions.includes(decision))throw Error('invalid decision');seen.add(pairKey);next[pairKey]=decision}}return next}}
document.getElementById('filter').onchange=()=>{{page=0;render()}};document.getElementById('previous').onclick=()=>{{page--;render()}};document.getElementById('next').onclick=()=>{{page++;render()}};document.getElementById('export').onclick=()=>{{const url=URL.createObjectURL(new Blob([csv()],{{type:'text/csv'}})),a=document.createElement('a');a.href=url;a.download='fragmentation-decisions.csv';a.click();URL.revokeObjectURL(url)}};document.getElementById('import').onchange=e=>{{const file=e.target.files[0];if(!file)return;file.text().then(text=>{{const next=parse(text);stored=next;save()}}).catch(error=>alert(error.message));e.target.value=''}};render();
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

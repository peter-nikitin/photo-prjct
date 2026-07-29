from __future__ import annotations

# ruff: noqa: E501 -- the browser-only template is deliberately kept together.
import json
import os
from html import escape
from pathlib import Path, PurePath
from urllib.parse import quote

from .benchmark import (
    NEAREST_CROSS_CLUSTER_COUNT,
    BenchmarkProposal,
    BenchmarkQuery,
    BenchmarkRun,
)

PAGE_SIZE = 25
_LABELS = ("relevant", "different", "uncertain")


def write_benchmark_report(
    staging_root: Path,
    proposal: BenchmarkProposal,
    run: BenchmarkRun,
    run_root: Path,
    photos_root: Path,
) -> None:
    """Write a local, bounded, browser-only annotation report into caller-owned staging."""
    if not isinstance(proposal, BenchmarkProposal) or not isinstance(run, BenchmarkRun):
        raise TypeError("proposal and run must be benchmark values")
    if proposal.faces != run.faces:
        raise ValueError("proposal faces do not match benchmark run")
    root = Path(staging_root)
    root.mkdir(parents=True, exist_ok=True)
    run_media = Path(run_root).resolve(strict=False)
    photos = Path(photos_root).resolve(strict=False)
    queries = tuple(proposal.all_queries)
    _validate_query_ids(queries)
    (root / "queries").mkdir(exist_ok=True)
    root_cards: list[str] = []
    for query in queries:
        page = root / "queries" / query.query_id / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        crop_href = _media_href(run_media, query.query_crop_path, page.parent)
        root_cards.append(
            '<article class="query"><a href="queries/'
            f'{quote(query.query_id, safe="-._~")}/index.html"><img loading="lazy" src="{escape(_media_href(run_media, query.query_crop_path, root), quote=True)}" alt="Query face crop for {escape(query.query_id)}"></a>'
            f"<h2>{escape(query.query_id)}</h2>"
            f"<p>{escape(query.split)} · {escape(query.proposed_cluster_id)}</p></article>"
        )
        page.write_text(
            _detail_page(query, proposal, run_media, photos, page.parent, crop_href),
            encoding="utf-8",
        )
    (root / "report.html").write_text(_root_page("\n".join(root_cards)), encoding="utf-8")


def _validate_query_ids(queries: tuple[BenchmarkQuery, ...]) -> None:
    for query in queries:
        value = query.query_id
        if not value or PurePath(value).name != value or value in {".", ".."}:
            raise ValueError("benchmark query ID is not a safe path component")


def _media_href(photos_root: Path, relative: str, page_root: Path) -> str:
    return quote(os.path.relpath(photos_root / relative, page_root), safe="/-._~")


def _detail_page(
    query: BenchmarkQuery,
    proposal: BenchmarkProposal,
    run_root: Path,
    photos_root: Path,
    page_root: Path,
    crop_href: str,
) -> str:
    faces = proposal.face_by_id
    same_count = sum(
        faces[face_id].cluster_id == query.proposed_cluster_id
        for face_id in query.candidate_face_ids
    )
    nearest_end = same_count + min(
        NEAREST_CROSS_CLUSTER_COUNT, len(query.candidate_face_ids) - same_count
    )
    candidates = []
    for position, face_id in enumerate(query.candidate_face_ids):
        face = faces[face_id]
        provenance = (
            "same_cluster"
            if position < same_count
            else "nearest_cross_cluster"
            if position < nearest_end
            else "distant_cross_cluster"
        )
        candidates.append(
            {
                "candidate_face_id": face.face_id,
                "candidate_filename": face.filename,
                "crop_href": _media_href(run_root, face.crop_path, page_root),
                "provenance": provenance,
                "source_href": _media_href(photos_root, face.filename, page_root),
            }
        )
    data = {
        "annotation_candidates": _annotation_candidates(proposal),
        "candidates": candidates,
        "labels": list(_LABELS),
        "page_size": PAGE_SIZE,
        "query": {
            "query_face_id": query.query_face_id,
            "query_filename": query.query_filename,
            "query_id": query.query_id,
        },
        "source": {
            "faces_sha256": proposal.source.faces_sha256,
            "index_manifest_sha256": proposal.source.index_manifest_sha256,
            "proposal_sha256": proposal.source.proposal_sha256,
            "run_manifest_sha256": proposal.source.run_manifest_sha256,
        },
    }
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    title = escape(f"Annotation: {query.query_id}")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>{_CSS}</style></head><body>
<p><a href="../../report.html">All queries</a></p><h1>{title}</h1>
<section class="query-summary"><img loading="lazy" src="{escape(crop_href, quote=True)}" alt="Query face crop for {escape(query.query_id)}"><dl><dt>Held-out photo</dt><dd>{escape(query.query_filename)}</dd><dt>Split</dt><dd>{escape(query.split)}</dd><dt>Proposed cluster</dt><dd>{escape(query.proposed_cluster_id)}</dd></dl></section>
<p>Same-cluster candidates are proposals, not relevance labels. Review the face crop and source photo directly.</p>
<section aria-labelledby="provenance-title"><h2 id="provenance-title">Candidate provenance</h2><ul><li><code>same_cluster</code>: other accepted faces from the proposed cluster.</li><li><code>nearest_cross_cluster</code>: nearest accepted faces from other clusters.</li><li><code>distant_cross_cluster</code>: deterministic distant cross-cluster sample.</li></ul></section>
<p id="status" role="status" aria-live="polite">Unreviewed candidates are not treated as different.</p>
<p><button id="export" type="button">Export annotations CSV</button> <label for="import">Import annotations CSV</label><input id="import" type="file" accept=".csv,text/csv"></p>
<nav aria-label="Candidate pages"><button id="previous" type="button">Previous</button> <span id="page" aria-current="page"></span> <button id="next" type="button">Next</button></nav><main id="cards"></main>
<script id="benchmark-data" type="application/json">{data_json}</script><script>{_SCRIPT}</script></body></html>
"""


def _annotation_candidates(proposal: BenchmarkProposal) -> list[dict[str, str]]:
    faces = proposal.face_by_id
    return [
        {
            "candidate_face_id": candidate_id,
            "candidate_filename": faces[candidate_id].filename,
            "query_face_id": query.query_face_id,
            "query_filename": query.query_filename,
            "query_id": query.query_id,
        }
        for query in proposal.all_queries
        for candidate_id in query.candidate_face_ids
    ]


def _root_page(cards: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Benchmark annotation queries</title><style>{_CSS}</style></head><body><h1>Benchmark annotation queries</h1><p>Each link opens a bounded candidate review page.</p><main class="queries">{cards}</main></body></html>
"""


_CSS = """body{font:16px system-ui,sans-serif;line-height:1.45;margin:1.5rem;max-width:1200px;color:#202124}.queries,.cards{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}.query,.card{border:1px solid #c7c7c7;border-radius:.4rem;padding:.75rem}.query img,.query-summary img,.card img{display:block;max-width:100%;height:auto;max-height:260px;object-fit:contain}.query-summary{display:flex;gap:1rem;align-items:start}.query-summary img{max-width:300px}dt{font-weight:700}dd{margin:0 0 .5rem}button,input{font:inherit;margin:.2rem}button:focus-visible,input:focus-visible,a:focus-visible{outline:3px solid #1a73e8;outline-offset:2px}.choice{display:flex;flex-wrap:wrap;gap:.4rem}.note{width:100%;box-sizing:border-box}#status{min-height:1.5em}@media(max-width:600px){body{margin:1rem}.query-summary{display:block}.query-summary img{max-width:100%}}"""


_SCRIPT = r"""(()=>{
const data=JSON.parse(document.getElementById('benchmark-data').textContent),labels=data.labels,PAGE_SIZE=data.page_size;
const storageKey=`findme-photo:benchmark-annotations:v1:${data.source.proposal_sha256}`;
const header=['schema_version','source_run_manifest_sha256','source_faces_sha256','index_manifest_sha256','proposal_sha256','query_id','query_face_id','query_filename','candidate_face_id','candidate_filename','label','note'];
const candidateById=new Map(data.candidates.map(item=>[item.candidate_face_id,item])),knownByKey=new Map(data.annotation_candidates.map(item=>[`${item.query_id}\u0000${item.candidate_face_id}`,item]));
function read(){try{const value=JSON.parse(localStorage.getItem(storageKey)||'{}');return value&&typeof value==='object'&&!Array.isArray(value)?value:{}}catch(_error){return {}}}
let annotations=read(),page=0;
function key(id){return `${data.query.query_id}\u0000${id}`}
function show(message){document.getElementById('status').textContent=message}
function write(next){try{localStorage.setItem(storageKey,JSON.stringify(next))}catch(_error){show('Could not save annotations locally.');return false}annotations=next;return true}
function mutate(change,renderAfter=true){const next={...annotations};change(next);if(!write(next))return false;if(renderAfter)render();else status();return true}
function csvCell(value){const text=String(value);return /[",\r\n]/.test(text)?`"${text.replace(/"/g,'""')}"`:text}
function exportCsv(){const rows=[header];for(const [annotationKey,value] of Object.entries(annotations).sort(([left],[right])=>left.localeCompare(right))){const candidate=knownByKey.get(annotationKey);if(!candidate||!labels.includes(value.label)||typeof value.note!=='string')continue;rows.push([1,data.source.run_manifest_sha256,data.source.faces_sha256,data.source.index_manifest_sha256,data.source.proposal_sha256,candidate.query_id,candidate.query_face_id,candidate.query_filename,candidate.candidate_face_id,candidate.candidate_filename,value.label,value.note])}return rows.map(row=>row.map(csvCell).join(',')).join('\r\n')+'\r\n'}
function parseCsv(text){const rows=[],row=[],value=[];let quoted=false;for(let index=0;index<text.length;index++){const char=text[index],next=text[index+1];if(quoted){if(char==='"'&&next==='"'){value.push(char);index++}else if(char==='"'){quoted=false}else{value.push(char)}}else if(char==='"'){if(value.length)throw Error('malformed CSV');quoted=true}else if(char===','){row.push(value.join(''));value.length=0}else if(char==='\r'||char==='\n'){if(char==='\r'&&next==='\n')index++;row.push(value.join(''));value.length=0;rows.push(row.splice(0));}else{value.push(char)}}if(quoted)throw Error('malformed CSV');if(value.length||row.length){row.push(value.join(''));rows.push(row)}return rows}
function importCsv(text){const rows=parseCsv(text.replace(/^\uFEFF/,''));if(!rows.length||rows[0].length!==header.length||rows[0].some((value,index)=>value!==header[index]))throw Error('Unexpected CSV header.');const next={},seen=new Set;for(const row of rows.slice(1)){if(row.length!==header.length)throw Error('Malformed CSV row.');const [schema,runHash,facesHash,indexHash,proposalHash,queryId,queryFaceId,queryFilename,candidateId,candidateFilename,label,note]=row;if(schema!=='1'||runHash!==data.source.run_manifest_sha256||facesHash!==data.source.faces_sha256||indexHash!==data.source.index_manifest_sha256||proposalHash!==data.source.proposal_sha256)throw Error('CSV bundle identity does not match this report.');const annotationKey=`${queryId}\u0000${candidateId}`,candidate=knownByKey.get(annotationKey);if(!candidate||candidate.query_face_id!==queryFaceId||candidate.query_filename!==queryFilename||candidate.candidate_filename!==candidateFilename)throw Error('CSV references an unknown candidate.');if(seen.has(annotationKey))throw Error('CSV contains a duplicate annotation.');if(!labels.includes(label))throw Error('CSV label is invalid.');if(/\r|\n/.test(note))throw Error('CSV notes must be single-line.');seen.add(annotationKey);next[annotationKey]={label,note}}if(!write(next))return false;render();return true}
function setAnnotation(id,label,note=''){if(!candidateById.has(id)||!labels.includes(label)||/\r|\n/.test(note))throw Error('Invalid annotation.');mutate(next=>{next[key(id)]={label,note}})}
function clearAnnotation(id){mutate(next=>{delete next[key(id)]})}
function updateNote(id,note){if(/\r|\n/.test(note)){show('Notes must be single-line.');return}const annotation=annotations[key(id)];if(annotation)mutate(next=>{next[key(id)]={...annotation,note}},false)}
function element(name,text){const value=document.createElement(name);if(text!==undefined)value.textContent=text;return value}
function status(){show(`${Object.keys(annotations).filter(id=>id.startsWith(`${data.query.query_id}\u0000`)).length} reviewed on this query; all bundle annotations export together.`)}
function render(){const cards=document.getElementById('cards'),start=page*PAGE_SIZE,items=data.candidates.slice(start,start+PAGE_SIZE);cards.replaceChildren(...items.map(candidate=>{const card=element('section');card.className='card';const image=element('img');image.loading='lazy';image.src=candidate.crop_href;image.alt=`Candidate face crop ${candidate.candidate_face_id}`;card.append(image,element('p',candidate.candidate_face_id),element('p',`Provenance: ${candidate.provenance}`));const photo=element('a','Open source photo');photo.href=candidate.source_href;card.append(photo);const choices=element('div');choices.className='choice';const stored=annotations[key(candidate.candidate_face_id)]||{};for(const label of labels){const button=element('button',label);button.type='button';button.setAttribute('aria-pressed',String(stored.label===label));button.addEventListener('click',()=>setAnnotation(candidate.candidate_face_id,label,stored.note||''));choices.append(button)}const clear=element('button','Clear / unreviewed');clear.type='button';clear.addEventListener('click',()=>clearAnnotation(candidate.candidate_face_id));choices.append(clear);card.append(choices);const note=element('input');note.className='note';note.type='text';note.maxLength=240;note.value=stored.note||'';note.setAttribute('aria-label',`Single-line note for ${candidate.candidate_face_id}`);note.addEventListener('input',()=>updateNote(candidate.candidate_face_id,note.value));card.append(note);return card}));const count=Math.max(1,Math.ceil(data.candidates.length/PAGE_SIZE));document.getElementById('page').textContent=`Page ${page+1} of ${count}`;document.getElementById('previous').disabled=page===0;document.getElementById('next').disabled=page>=count-1;status()}
document.getElementById('previous').addEventListener('click',()=>{if(page){page--;render()}});document.getElementById('next').addEventListener('click',()=>{if((page+1)*PAGE_SIZE<data.candidates.length){page++;render()}});document.getElementById('export').addEventListener('click',()=>{const blob=new Blob([exportCsv()],{type:'text/csv'}),link=element('a');link.href=URL.createObjectURL(blob);link.download='benchmark-annotations.csv';link.click();URL.revokeObjectURL(link.href)});document.getElementById('import').addEventListener('change',event=>{const file=event.target.files&&event.target.files[0];if(!file)return;file.text().then(text=>{try{if(importCsv(text))show('Annotations imported.')}catch(error){show(error instanceof Error?error.message:'Import failed.')}});event.target.value=''});globalThis.__benchmarkReview={clearAnnotation,currentPageCount:()=>data.candidates.slice(page*PAGE_SIZE,page*PAGE_SIZE+PAGE_SIZE).length,exportCsv,importCsv,pageSize:PAGE_SIZE,setAnnotation,storageKey};render()})();"""

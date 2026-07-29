from __future__ import annotations

# ruff: noqa: E501 -- Node browser harness is intentionally a single script literal.
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

from face_spike.benchmark import build_benchmark_proposal
from face_spike.benchmark_artifacts import ANNOTATION_CSV_HEADERS
from face_spike.benchmark_report import PAGE_SIZE, write_benchmark_report
from test_benchmark import _cluster, _index, _run, _sized_cluster


def _proposal() -> object:
    clusters = (_sized_cluster("person-00", 28),) + tuple(
        _cluster(f"person-{number:02d}") for number in range(1, 30)
    )
    run = _run(*clusters)
    return run, build_benchmark_proposal(run, _index(run))


def _report(tmp_path: Path) -> tuple[object, object, Path, Path, Path]:
    run, proposal = _proposal()
    run_root = tmp_path / "run"
    photos = tmp_path / "photos"
    for face in run.faces:
        crop = run_root / face.crop_path
        original = photos / face.filename
        crop.parent.mkdir(parents=True, exist_ok=True)
        original.parent.mkdir(parents=True, exist_ok=True)
        crop.write_bytes(b"crop")
        original.write_bytes(b"original")
    output = tmp_path / "report"
    write_benchmark_report(output, proposal, run, run_root, photos)
    return run, proposal, output, run_root, photos


def _detail_data(path: Path) -> dict[str, object]:
    match = re.search(
        r'<script id="benchmark-data" type="application/json">(.*?)</script>',
        path.read_text(encoding="utf-8"),
        flags=re.DOTALL,
    )
    assert match
    return json.loads(match.group(1))


def _node_contract(page: Path) -> dict[str, object]:
    """Run the browser's real CSV/localStorage contract in Node with a minimal DOM."""
    data = _detail_data(page)
    script = re.findall(
        r"<script>(.*?)</script>", page.read_text(encoding="utf-8"), flags=re.DOTALL
    )[-1]
    harness = r"""
const fs=require('fs'),vm=require('vm');
const data=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const storage=new Map();
let throwWrite=false;
function node(){return {children:[],classList:{add(){}},append(...values){this.children.push(...values)},replaceChildren(...values){this.children=values},listeners:{},addEventListener(name,callback){this.listeners[name]=callback},click(){},setAttribute(){},value:'',textContent:'',checked:false,disabled:false};}
const elements=new Map();
for(const id of ['benchmark-data','cards','status','page','previous','next','export','import'])elements.set(id,node());
elements.get('benchmark-data').textContent=JSON.stringify(data);
global.document={getElementById:id=>elements.get(id)||null,createElement:node};
elements.get('cards').replaceChildren=function(...values){this.children=values;document.activeElement=null};
global.localStorage={getItem:key=>storage.get(key)||null,setItem:(key,value)=>{if(throwWrite)throw Error('storage unavailable');storage.set(key,value)}};
global.Blob=class { constructor(parts){this.parts=parts} };
global.URL={createObjectURL:()=>'',revokeObjectURL(){}};
global.FileReader=class {};
global.alert=()=>{};
global.window=global;
const foreign=data.annotation_candidates.find(item=>item.query_id!==data.query.query_id);
const initialKey=`findme-photo:benchmark-annotations:v1:${data.source.proposal_sha256}`;
storage.set(initialKey,JSON.stringify({[`${foreign.query_id}\u0000${foreign.candidate_face_id}`]:{label:'different',note:'other page'}}));
vm.runInThisContext(fs.readFileSync(process.argv[4],'utf8'));
const api=global.__benchmarkReview;
const first=data.candidates[0];
api.setAnnotation(first.candidate_face_id,'relevant','careful');
const noteInput=elements.get('cards').children[0].children.find(item=>item.type==='text');
noteInput.focus=()=>{document.activeElement=noteInput};
noteInput.focus();
for(const character of 'typed note'){noteInput.value+=character;noteInput.listeners.input();if(document.activeElement!==noteInput)throw Error('note input lost focus')}
vm.runInThisContext(fs.readFileSync(process.argv[4],'utf8'));
const refreshedInput=elements.get('cards').children[0].children.find(item=>item.type==='text');
const before=localStorage.getItem(api.storageKey);
const csv=api.exportCsv();
const invalids={
 header: csv.replace('schema_version','wrong_header'),
 hash: csv.replace(data.source.proposal_sha256,'f'.repeat(64)),
 unknown: csv.replace(first.candidate_face_id,'unknown#face'),
 duplicate: csv + csv.split('\r\n')[1]+'\r\n',
 label: csv.replace(',relevant,carefultyped note',',wrong,carefultyped note'),
 multiline: csv.replace('carefultyped note','"line1\nline2"'),
};
const unchanged={};
for(const [name,text] of Object.entries(invalids)){try{api.importCsv(text)}catch(error){} unchanged[name]=localStorage.getItem(api.storageKey)===before;}
api.clearAnnotation(first.candidate_face_id);
api.importCsv(csv);
const after=JSON.parse(localStorage.getItem(api.storageKey));
const exportBeforeFailure=api.exportCsv(),storageBeforeFailure=localStorage.getItem(api.storageKey);
const currentInput=elements.get('cards').children[0].children.find(item=>item.type==='text');
currentInput.focus=()=>{document.activeElement=currentInput};currentInput.focus();currentInput.value='must not persist';throwWrite=true;currentInput.listeners.input();throwWrite=false;
const importFailureCsv=csv.replace(',relevant,carefultyped note',',different,replacement');
throwWrite=true;api.importCsv(importFailureCsv);throwWrite=false;
console.log(JSON.stringify({header:csv.split('\r\n')[0],after,foreignKey:`${foreign.query_id}\u0000${foreign.candidate_face_id}`,refreshed:refreshedInput.value,typed:JSON.parse(before)[`${data.query.query_id}\u0000${first.candidate_face_id}`].note,unchanged,key:api.storageKey,pageSize:api.pageSize,shown:api.currentPageCount(),total:data.candidates.length,atomic:{exportUnchanged:api.exportCsv()===exportBeforeFailure,storageUnchanged:localStorage.getItem(api.storageKey)===storageBeforeFailure,focusUnchanged:document.activeElement===currentInput,status:elements.get('status').textContent}}));
"""
    fixture = page.parent / "node-data.json"
    fixture.write_text(json.dumps(data), encoding="utf-8")
    script_path = page.parent / "node-contract.js"
    script_path.write_text(harness, encoding="utf-8")
    browser_script = page.parent / "browser-script.js"
    browser_script.write_text(script, encoding="utf-8")
    result = subprocess.run(
        ["node", str(script_path), str(page), str(fixture), str(browser_script)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_report_writes_exactly_thirty_bounded_query_links_and_one_crop_each(tmp_path: Path) -> None:
    run, proposal, output, run_root, _ = _report(tmp_path)

    root = (output / "report.html").read_text(encoding="utf-8")
    links = re.findall(r'href="(queries/[^\"]+/index\.html)"', root)
    assert len(links) == len(set(links)) == 30
    assert root.count("<img ") == 30
    assert root.count('loading="lazy"') == 30
    assert "embedding" not in root.lower()
    assert "vector" not in root.lower()
    assert all((output / href).is_file() for href in links)
    assert len(tuple((output / "queries").glob("*/index.html"))) == 30
    for href in re.findall(r'<img loading="lazy" src="([^"]+)"', root):
        assert (output / unquote(href)).resolve().is_relative_to(run_root.resolve())
    assert run is not proposal


def test_query_page_exposes_provenance_and_defers_candidate_cards_to_pages(tmp_path: Path) -> None:
    _, proposal, output, run_root, photos_root = _report(tmp_path)
    query = next(item for item in proposal.queries if item.proposed_cluster_id == "person-00")
    page = output / "queries" / query.query_id / "index.html"
    report = page.read_text(encoding="utf-8")
    data = _detail_data(page)

    assert PAGE_SIZE == 25
    assert report.count("<img ") == 1
    assert "same_cluster" in report
    assert "nearest_cross_cluster" in report
    assert "distant_cross_cluster" in report
    assert "Same-cluster candidates are proposals, not relevance labels." in report
    assert query.query_filename in report
    assert query.split in report
    assert query.proposed_cluster_id in report
    assert set(data["labels"]) == {"relevant", "different", "uncertain"}
    assert len(data["candidates"]) > PAGE_SIZE
    assert all("source_href" in item and "crop_href" in item for item in data["candidates"])
    assert all(
        item["provenance"] in {"same_cluster", "nearest_cross_cluster", "distant_cross_cluster"}
        for item in data["candidates"]
    )
    assert "<img src=" not in report
    assert "embedding" not in report.lower()
    assert "vector" not in report.lower()
    for candidate in data["candidates"]:
        assert (
            (page.parent / unquote(candidate["crop_href"]))
            .resolve()
            .is_relative_to(run_root.resolve())
        )
        assert (
            (page.parent / unquote(candidate["source_href"]))
            .resolve()
            .is_relative_to(photos_root.resolve())
        )


def test_report_keeps_crops_and_originals_bound_to_separate_media_roots(tmp_path: Path) -> None:
    run, proposal, output, run_root, photos_root = _report(tmp_path)
    query = next(item for item in proposal.queries if item.proposed_cluster_id == "person-00")
    wrong = tmp_path / "wrong-report"

    write_benchmark_report(wrong, proposal, run, photos_root, run_root)
    wrong_page = wrong / "queries" / query.query_id / "index.html"
    wrong_data = _detail_data(wrong_page)

    assert all((run_root / face.crop_path).is_file() for face in run.faces)
    assert all((photos_root / face.filename).is_file() for face in run.faces)
    assert all(
        not (wrong_page.parent / unquote(item["crop_href"])).is_file()
        for item in wrong_data["candidates"]
    )
    assert all(
        not (wrong_page.parent / unquote(item["source_href"])).is_file()
        for item in wrong_data["candidates"]
    )


def test_browser_contract_exports_and_imports_bundle_scoped_annotations_atomically(
    tmp_path: Path,
) -> None:
    _, proposal, output, _, _ = _report(tmp_path)
    query = next(item for item in proposal.queries if item.proposed_cluster_id == "person-00")
    page = output / "queries" / query.query_id / "index.html"

    result = _node_contract(page)

    assert result["header"].split(",") == list(ANNOTATION_CSV_HEADERS)
    assert result["after"]
    assert result["foreignKey"] in result["after"]
    assert result["typed"] == "carefultyped note"
    assert result["refreshed"] == "carefultyped note"
    assert all(result["unchanged"].values())
    assert proposal.source.proposal_sha256 in result["key"]
    assert result["pageSize"] == 25
    assert result["shown"] == 25
    assert result["total"] > 25
    assert result["atomic"] == {
        "exportUnchanged": True,
        "storageUnchanged": True,
        "focusUnchanged": True,
        "status": "Could not save annotations locally.",
    }

"""Acceptance is fail-closed for quality, isolation and real resource evidence."""

from pathlib import Path

import pytest

from experiments.bib_search.worker_report import compare, write_immutable

MIB = 1024**2


def fixture():
    baseline = {
        "schema_version": 1,
        "corpus": {"photos": [{"relative_path": "a.jpg", "sha256": "a" * 64}]},
        "confirmed": [["a.jpg", "007"]],
        "rejected": [["a.jpg", "999"]],
        "excluded": [],
        "baseline_accepted": [["a.jpg", "007"]],
    }
    evidence = {
        "event_slug": "local-istra-bib",
        "rows": [
            {
                "relative_path": "a.jpg",
                "sha256": "a" * 64,
                "photo_id": "1",
                "event_slug": "local-istra-bib",
                "numbers": ["007"],
                "published": True,
                "terminal": True,
            }
        ],
        "search": {"007": ["1"], "7": [], "999": [], "0999": []},
        "isolation_passed": True,
        "metrics": {
            "peak_rss_bytes": 2000 * MIB,
            "memory_limit_bytes": 3072 * MIB,
            "worker_cpus": 2,
            "replicas": 1,
            "oom_kills": 0,
            "restarts": 0,
            "lease_expired": 0,
            "host_mem_available_min_bytes": 2000 * MIB,
            "swap_in_pages": 0,
            "swap_out_pages": 0,
            "cpu_saturation_seconds": 0,
            "iowait_saturation_seconds": 0,
            "health_failures": 0,
            "baseline_web_p95_ms": 100,
            "web_p95_ms": 120,
            "wall_seconds": 10,
            "peak_cpu_percent": 100,
            "peak_iowait_percent": 0,
            "disk_available_min_bytes": 10000 * MIB,
            "lease_max_seconds": 10,
            "sample_count": 5,
        },
        "stages": {
            k: {"count": 1, "p50": 1, "p95": 2}
            for k in [
                "download",
                "ocr",
                "visual",
                "validation",
                "total",
                "capture_metadata",
                "generate_preview",
                "face_embedding",
            ]
        },
    }
    return baseline, evidence


def test_good_evidence_selects_headroom_limit():
    baseline, evidence = fixture()
    result = compare(baseline, evidence)
    assert result["status"] == "GREEN"
    assert result["selected_memory_mib"] == 3072


@pytest.mark.parametrize(
    "failure",
    [
        "miss",
        "junk",
        "publication",
        "event",
        "zero",
        "missing_metrics",
        "nan",
        "oom",
        "rss",
        "host",
        "latency",
        "stage",
        "changed",
        "terminal",
    ],
)
def test_gate_rejects_regressions(failure):
    baseline, evidence = fixture()
    row = evidence["rows"][0]
    if failure == "miss":
        row["numbers"] = []
    elif failure == "junk":
        row["numbers"].append("999")
    elif failure == "publication":
        row["published"] = False
    elif failure == "event":
        row["event_slug"] = "other"
    elif failure == "zero":
        evidence["search"]["7"] = ["1"]
    elif failure == "missing_metrics":
        del evidence["metrics"]["peak_rss_bytes"]
    elif failure == "nan":
        evidence["metrics"]["web_p95_ms"] = float("nan")
    elif failure == "oom":
        evidence["metrics"]["oom_kills"] = 1
    elif failure == "rss":
        evidence["metrics"]["peak_rss_bytes"] = 5000 * MIB
    elif failure == "host":
        evidence["metrics"]["host_mem_available_min_bytes"] = 500 * MIB
    elif failure == "latency":
        evidence["metrics"]["web_p95_ms"] = 201
    elif failure == "stage":
        del evidence["stages"]["ocr"]
    elif failure == "changed":
        row["numbers"].append("123")
    elif failure == "terminal":
        row["terminal"] = False
    assert compare(baseline, evidence)["status"] != "GREEN"


def test_missing_excluded_pair_does_not_count_as_miss():
    baseline, evidence = fixture()
    baseline["confirmed"].append(["a.jpg", "67"])
    baseline["excluded"] = [{"relative_path": "a.jpg", "number": "67"}]
    assert compare(baseline, evidence)["status"] == "GREEN"


def test_immutable_output_refuses_overwrite(tmp_path: Path):
    path = tmp_path / "comparison.json"
    write_immutable(path, {"status": "RED"})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        write_immutable(path, {"status": "GREEN"})
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "metric,value",
    [
        ("oom_kills", 1),
        ("restarts", 1),
        ("lease_expired", 1),
        ("swap_in_pages", 1),
        ("swap_out_pages", 1),
        ("health_failures", 1),
        ("cpu_saturation_seconds", 301),
        ("iowait_saturation_seconds", 301),
        ("sample_count", 0),
        ("replicas", 2),
        ("worker_cpus", 1),
    ],
)
def test_each_capacity_stop_condition(metric, value):
    baseline, evidence = fixture()
    evidence["metrics"][metric] = value
    assert compare(baseline, evidence)["status"] == "RED"


def test_local_script_rejects_remote_event_before_docker(monkeypatch):
    from experiments.bib_search.worker_acceptance import run

    monkeypatch.setattr(
        "sys.argv",
        [
            "acceptance",
            "--event-slug",
            "production",
            "--source-root",
            ".",
            "--baseline",
            ".",
            "--output",
            ".",
        ],
    )
    with pytest.raises(SystemExit) as error:
        run.main()
    assert error.value.code == 2


def test_local_stack_keeps_production_sequential_contract():
    import yaml

    from tests.deployment.test_deployment_scripts import ROOT

    stack = yaml.safe_load((ROOT / "docker-compose.bib-local.yml").read_text())
    worker = stack["services"]["worker"]
    assert worker["cpus"] == "2.0"
    assert worker["mem_limit"] == "6g"
    assert worker["pids_limit"] == 64
    assert worker["restart"] == "no"
    assert worker["environment"]["PHOTO_WORKER_PROCESSOR_IDENTITIES"].endswith(
        "1/bib_recognition/1"
    )
    assert (
        worker["environment"]["PHOTO_WORKER_PROCESSOR_TYPES"]
        == "selfie_query,face_embedding,capture_metadata,generate_preview"
    )
    assert stack["services"]["web"]["ports"] == ["127.0.0.1:18210:8000"]


def test_metrics_observer_does_not_consume_worker_pid_quota():
    from tests.deployment.test_deployment_scripts import ROOT

    runner = " ".join(
        (ROOT / "experiments/bib_search/worker_acceptance/run.py").read_text().split()
    )
    sampler = (ROOT / "experiments/bib_search/worker_acceptance/metrics.py").read_text()
    assert '"--pid", f"container:{worker_id}"' in runner
    assert '"--cgroupns", "host"' in runner
    assert '"--network", "none"' in runner
    assert 'Path("/proc/1/cgroup")' in sampler
    assert "p.parent.name == str(os.getpid())" in sampler


@pytest.mark.parametrize("final_available", ["available", "timeout", "stopped"])
def test_runner_seals_samples_after_search(tmp_path, monkeypatch, final_available):
    import hashlib
    import json
    from types import SimpleNamespace

    from experiments.bib_search.worker_acceptance import run

    baseline, snapshot = fixture()
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.jpg").write_bytes(b"photo")
    digest = hashlib.sha256(b"photo").hexdigest()
    baseline["corpus"]["key"] = "istra"
    baseline["corpus"]["photos"][0]["sha256"] = digest
    snapshot["rows"][0]["sha256"] = digest
    snapshot.update(
        aggregate={"states": {"succeeded": 1}}, terminal=True, lease_expired=0, lease_max_seconds=1
    )
    del snapshot["metrics"]
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline))
    output = tmp_path / "var/bib-search/test"
    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "acceptance",
            "--event-slug",
            "local-test",
            "--source-root",
            str(source),
            "--baseline",
            str(baseline_path),
            "--output",
            str(output),
        ],
    )
    clock = [100.0]
    searched = [False]
    stopped = [False]
    emitted = [False]
    commands = []

    def append_sample(swap=0):
        sample = dict(
            at=clock[0],
            rss_bytes=2000 * MIB,
            cgroup_peak_bytes=2000 * MIB,
            memory_limit_bytes=3072 * MIB,
            oom_kills=0,
            worker_cpu_usec=clock[0] * 100,
            host_cpu_ticks=[clock[0], 0, 0, clock[0] * 10, 0],
            host_available_bytes=2000 * MIB,
            swap_in_pages=swap,
            swap_out_pages=0,
            disk_available_bytes=10000 * MIB,
        )
        with (output / "resource-stream.jsonl").open("a") as stream:
            stream.write(json.dumps(sample) + "\n")

    def sleep(seconds):
        clock[0] += seconds
        if searched[0] and final_available == "available" and not emitted[0]:
            append_sample(17)
            emitted[0] = True

    def command(parts, **kwargs):
        commands.append(parts)
        if final_available == "timeout" and parts[-1] == "stop":
            raise RuntimeError("cleanup failed")
        if parts[:2] == ["docker", "stop"]:
            stopped[0] = True
            if emitted[0]:
                clock[0] += 1
                append_sample(23)  # drain must include the last observer write too
        if parts[-1] == "snapshot":
            return SimpleNamespace(returncode=0, stdout=json.dumps(snapshot))
        if parts[-1] == "search":
            searched[0] = True
            clock[0] += 1
            if final_available == "stopped":
                stopped[0] = True
            return SimpleNamespace(returncode=0, stdout="{}")
        return SimpleNamespace(returncode=0, stdout="worker")

    class Process:
        returncode = 0

        def __init__(self, parts, **kwargs):
            self.observer = parts[:2] == ["docker", "run"]
            if self.observer:
                append_sample()
                clock[0] += 1
                append_sample()

        def poll(self):
            return None if self.observer and not stopped[0] else 0

        def wait(self, **kwargs):
            return 0

        def terminate(self):
            stopped[0] = True

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b""

    monkeypatch.setattr(run.time, "time", lambda: clock[0])
    monkeypatch.setattr(run.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(run.time, "sleep", sleep)
    monkeypatch.setattr(run.subprocess, "run", command)
    monkeypatch.setattr(run.subprocess, "Popen", Process)
    monkeypatch.setattr(
        run.subprocess,
        "check_output",
        lambda *a, **k: json.dumps(
            [
                {
                    "Image": "sha256:test",
                    "Config": {"Image": "test"},
                    "State": {"OOMKilled": False},
                    "RestartCount": 0,
                }
            ]
        ),
    )
    monkeypatch.setattr(run.urllib.request, "urlopen", lambda *a, **k: Response())
    assert run.main() == 1
    saved = json.loads((output / "resource-samples.json").read_text())
    raw = [json.loads(line) for line in (output / "resource-stream.jsonl").read_text().splitlines()]
    assert saved == raw
    assert stopped[0]
    assert any("--build" in parts for parts in commands)
    if final_available == "available":
        assert saved[-1]["swap_in_pages"] == 23
        evidence = json.loads((output / "evidence.json").read_text())
        assert evidence["metrics"]["swap_in_pages"] == 23
        assert evidence["metrics"]["sample_count"] == len(saved)
        assert json.loads((output / "comparison.json").read_text())["status"] == "RED"
    else:
        assert (
            "final resource sample" in json.loads((output / "failure.json").read_text())["reason"]
        )
        assert not (output / "comparison.json").exists()

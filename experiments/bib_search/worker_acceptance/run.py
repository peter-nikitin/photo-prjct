"""Run exactly one disposable event through Linux Docker and seal all evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import time
import urllib.request
from contextlib import suppress
from pathlib import Path

from experiments.bib_search.worker_report import compare, write_immutable

ROOT = Path(__file__).resolve().parents[3]


def p95(values):
    return sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("event-slug", "source-root", "baseline", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"local-[a-z0-9-]{1,60}", args.event_slug):
        parser.error("event slug must start local-")
    source = Path(args.source_root).resolve(strict=True)
    baseline_path = Path(args.baseline).resolve(strict=True)
    output = Path(args.output).resolve()
    if not output.is_relative_to(ROOT / "var/bib-search"):
        parser.error("output must be under this checkout var/bib-search")
    baseline = json.loads(baseline_path.read_text())
    photos = baseline["corpus"]["photos"]
    if not 1 <= len(photos) <= 100 or baseline["corpus"]["key"] != "istra":
        parser.error("requires one bounded Istra baseline")
    paths = {p["relative_path"] for p in photos}
    if paths != {
        p.name for p in source.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
    }:
        parser.error("source must contain exactly the baseline JPEG cohort")
    for p in photos:
        path = (source / p["relative_path"]).resolve(strict=True)
        if path.parent != source or hashlib.sha256(path.read_bytes()).hexdigest() != p["sha256"]:
            parser.error("source path/checksum mismatch")
    output.mkdir(parents=True, exist_ok=False)
    write_immutable(output / "baseline.json", baseline)
    env = {
        **os.environ,
        "BIB_EVENT_SLUG": args.event_slug,
        "BIB_SOURCE_ROOT": str(source),
        "BIB_BASELINE": str(baseline_path),
    }
    project = "bib-acceptance-" + hashlib.sha256(str(output).encode()).hexdigest()[:10]
    command = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "-p",
        project,
        "-f",
        str(ROOT / "docker-compose.bib-local.yml"),
    ]

    def compose(*parts, timeout=120):
        result = subprocess.run(
            [*command, *parts], env=env, cwd=ROOT, capture_output=True, text=True, timeout=timeout
        )
        if result.returncode:
            # Signed URLs and credentials never enter saved diagnostics.
            raise RuntimeError(f"compose {parts[0]} failed, exit {result.returncode}")
        return result.stdout

    def cohort(action):
        return json.loads(
            compose("exec", "-T", "web", "python", "/acceptance/cohort.py", action, timeout=600)
        )

    samples = []
    latencies = []
    baseline_latencies = []
    health_failures = 0
    snapshot = {}
    sampler = None
    observer_name = project + "-metrics"
    sample_file = None
    started = time.time()

    def health():
        tick = time.perf_counter()
        with urllib.request.urlopen(
            f"http://127.0.0.1:18210/events/{args.event_slug}/", timeout=10
        ) as response:
            if response.status != 200:
                raise RuntimeError("public health failed")
            response.read()
        return (time.perf_counter() - tick) * 1000

    try:
        compose("up", "-d", "--build", "--wait", "web", timeout=900)
        for _ in range(10):
            baseline_latencies.append(health())
        compose("up", "-d", "worker")
        worker_id = compose("ps", "-q", "worker").strip()
        image = json.loads(subprocess.check_output(["docker", "inspect", worker_id], text=True))[0]
        write_immutable(
            output / "runtime.json",
            {
                "project": project,
                "worker_image_id": image["Image"],
                "worker_configured_image": image["Config"]["Image"],
                "baseline_sha256": hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
                "started_at_unix": started,
                "worker_cpus": 2,
                "memory_limit_bytes": 6 * 1024**3,
            },
        )
        sample_file = (output / "resource-stream.jsonl").open("x")
        sampler = subprocess.Popen(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                observer_name,
                "--pid",
                f"container:{worker_id}",
                "--cgroupns",
                "host",
                "--network",
                "none",
                "--cpus",
                "0.1",
                "--memory",
                "64m",
                "--pids-limit",
                "16",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--mount",
                f"type=bind,src={ROOT / 'experiments/bib_search/worker_acceptance/metrics.py'},"
                "dst=/metrics.py,readonly",
                "--entrypoint",
                "python",
                image["Image"],
                "/metrics.py",
            ],
            env=env,
            cwd=ROOT,
            stdout=sample_file,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(2)
        upload = subprocess.Popen(
            [*command, "exec", "-T", "web", "python", "/acceptance/cohort.py", "upload"],
            env=env,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        last_snapshot = 0
        while time.time() - started < 4 * 3600:
            samples = [
                json.loads(line)
                for line in (output / "resource-stream.jsonl").read_text().splitlines()
                if line.endswith("}")
            ]
            if sampler.poll() is not None:
                raise RuntimeError("persistent resource sampler stopped")
            try:
                latencies.append(health())
            except Exception:
                health_failures += 1
            if time.time() - last_snapshot >= 20:
                snapshot = cohort("snapshot")
                last_snapshot = time.time()
                print(
                    f"cohort photos={len(snapshot['rows'])}/{len(photos)} "
                    f"bib={snapshot['aggregate']['states']} "
                    f"elapsed={round(time.time() - started)}s",
                    flush=True,
                )
                if upload.poll() is not None and upload.returncode != 0:
                    raise RuntimeError("ordinary HTTP upload failed")
                if snapshot["terminal"] and upload.poll() == 0:
                    break
            time.sleep(2)
        else:
            raise RuntimeError("four-hour cohort deadline exceeded")
        snapshot.update(cohort("search"))
        # Require a complete observation after all terminal/public-search work.
        # Then stop and drain Docker stdout before selecting the evidence window.
        cohort_end = time.time()
        final_sample_deadline = time.monotonic() + 10
        while True:
            if sampler.poll() is not None:
                raise RuntimeError("final resource sample unavailable: observer stopped")
            complete_samples = [
                json.loads(line)
                for line in (output / "resource-stream.jsonl").read_text().splitlines()
                if line.endswith("}")
            ]
            if complete_samples and complete_samples[-1]["at"] >= cohort_end:
                break
            if time.monotonic() >= final_sample_deadline:
                raise RuntimeError("final resource sample unavailable: deadline exceeded")
            time.sleep(0.1)
        stopped = subprocess.run(["docker", "stop", observer_name], capture_output=True, timeout=15)
        if stopped.returncode:
            raise RuntimeError("final resource sample unavailable: observer stop failed")
        sampler.wait(timeout=10)
        sampler = None
        sample_file.close()
        sample_file = None
        # Parse strictly after drain: a truncated/invalid final record stays RED.
        samples = [
            json.loads(line) for line in (output / "resource-stream.jsonl").read_text().splitlines()
        ]
        write_immutable(output / "resource-samples.json", samples)
        inspect = json.loads(subprocess.check_output(["docker", "inspect", worker_id], text=True))[
            0
        ]
        host_cpu = []
        iowait = []
        worker_cpu = []
        cpu_run = io_run = cpu_max = io_max = 0
        for a, b in zip(samples, samples[1:], strict=False):
            elapsed = b["at"] - a["at"]
            ticks = [y - x for x, y in zip(a["host_cpu_ticks"], b["host_cpu_ticks"], strict=True)]
            total = sum(ticks)
            cpu = 100 * (total - ticks[3] - ticks[4]) / total if total else 0
            io = 100 * ticks[4] / total if total else 0
            host_cpu.append(cpu)
            iowait.append(io)
            worker_cpu.append((b["worker_cpu_usec"] - a["worker_cpu_usec"]) / elapsed / 10000)
            cpu_run = cpu_run + elapsed if cpu > 85 else 0
            io_run = io_run + elapsed if io > 10 else 0
            cpu_max = max(cpu_max, cpu_run)
            io_max = max(io_max, io_run)
        # cgroup memory.peak conservatively includes model mappings/page cache and
        # guarantees no between-sample inference peak can escape the memory gate.
        metrics = {
            "peak_rss_bytes": max(max(s["rss_bytes"], s["cgroup_peak_bytes"]) for s in samples),
            "sampled_peak_rss_bytes": max(s["rss_bytes"] for s in samples),
            "memory_limit_bytes": samples[-1]["memory_limit_bytes"],
            "worker_cpus": 2,
            "replicas": 1,
            "oom_kills": max(s["oom_kills"] for s in samples) + int(inspect["State"]["OOMKilled"]),
            "restarts": inspect["RestartCount"],
            "lease_expired": snapshot["lease_expired"],
            "host_mem_available_min_bytes": min(s["host_available_bytes"] for s in samples),
            "swap_in_pages": samples[-1]["swap_in_pages"] - samples[0]["swap_in_pages"],
            "swap_out_pages": samples[-1]["swap_out_pages"] - samples[0]["swap_out_pages"],
            "cpu_saturation_seconds": cpu_max,
            "iowait_saturation_seconds": io_max,
            "health_failures": health_failures,
            "baseline_web_p95_ms": p95(baseline_latencies),
            "web_p95_ms": p95(latencies),
            "wall_seconds": time.time() - started,
            "peak_cpu_percent": max(worker_cpu),
            "peak_host_cpu_percent": max(host_cpu),
            "peak_iowait_percent": max(iowait),
            "disk_available_min_bytes": min(s["disk_available_bytes"] for s in samples),
            "lease_max_seconds": snapshot["lease_max_seconds"],
            "sample_count": len(samples),
        }
        snapshot["metrics"] = metrics
        result = compare(baseline, snapshot)
        write_immutable(output / "aggregate.json", snapshot["aggregate"])
        write_immutable(output / "evidence.json", snapshot)
        write_immutable(output / "comparison.json", result)
        print(json.dumps(result), flush=True)
        return 0 if result["status"] == "GREEN" else 1
    except Exception as error:
        write_immutable(
            output / "failure.json",
            {
                "status": "RED",
                "error_type": type(error).__name__,
                "reason": str(error)
                if isinstance(error, RuntimeError)
                else "local acceptance command failed",
                "snapshot": snapshot,
            },
        )
        print(f"Local acceptance blocked: {type(error).__name__}; evidence: {output}", flush=True)
        return 1
    finally:
        if sampler is not None:
            # Cleanup must not mask the original failure or overwrite sealed evidence.
            with suppress(OSError, subprocess.SubprocessError):
                subprocess.run(["docker", "stop", observer_name], capture_output=True, timeout=15)
            with suppress(OSError, subprocess.SubprocessError):
                sampler.terminate()
                sampler.wait(timeout=10)
        if sample_file is not None:
            sample_file.close()
        if not (output / "resource-samples.json").exists():
            stream = output / "resource-stream.jsonl"
            if stream.exists():
                with suppress(ValueError):
                    samples = [
                        json.loads(line)
                        for line in stream.read_text().splitlines()
                        if line.endswith("}")
                    ]
            write_immutable(output / "resource-samples.json", samples)
        write_immutable(
            output / "public-probes.json",
            {
                "baseline_ms": baseline_latencies,
                "cohort_ms": latencies,
                "failures": health_failures,
            },
        )
        try:
            compose("stop", timeout=60)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            if not (output / "failure.json").exists():
                raise


if __name__ == "__main__":
    raise SystemExit(main())

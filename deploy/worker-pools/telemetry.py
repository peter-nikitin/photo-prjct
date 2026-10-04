#!/usr/bin/env python3
"""One bounded host diagnostic snapshot; no processing or host-control authority."""

from __future__ import annotations

import json
import math
import os
import re
import selectors
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from uuid import UUID, uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import psutil
from host import SLOTS, active_slot  # noqa: E402
from prometheus_client.parser import text_string_to_metric_families

API = "https://findme-photo.ru:8443/internal/photo-processing/v1/members/telemetry"
RUNTIME_URL = "http://127.0.0.1:9101/metrics"
STATE_PATH = Path("/var/lib/findme-worker-telemetry/latest.json")
MAX_BODY = 16384
MAX_RUNTIME_BODY = 131072
MAX_NUMBER = 2**53
BUCKETS = ("1.0", "5.0", "15.0", "60.0", "300.0", "900.0", "1800.0", "+Inf")
KINDS = {
    "selfie": {"selfie_query"},
    "bulk": {
        "capture_metadata",
        "generate_preview",
        "generate_watermarked_preview",
        "face_embedding",
        "bib_recognition",
    },
}
OUTCOMES = {"callback_delivered", "execution_failed", "transport_failed", "lease_lost"}
INSPECT = (
    "[{{json .Id}},{{json .State.Running}},{{json .RestartCount}},"
    "{{json .State.OOMKilled}},{{json .State.ExitCode}},{{json .HostConfig.NanoCpus}},"
    "{{json .HostConfig.CpuQuota}},{{json .HostConfig.CpuPeriod}},{{json .HostConfig.Memory}}]"
)


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def number(value, *, integer=False):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= MAX_NUMBER
    ):
        raise ValueError("invalid diagnostic number")
    if integer and int(value) != value:
        raise ValueError("invalid diagnostic count")
    return int(value) if integer else value


def encoded(snapshot):
    raw = json.dumps(snapshot, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > MAX_BODY:
        raise ValueError("diagnostic snapshot too large")
    return raw


def bounded_command(args, *, max_output=32768, timeout=3):
    """Drain only capped stdout; discard errors rather than capturing arbitrary Docker text."""
    with subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "LC_ALL": "C"},
    ) as process:
        output = bytearray()
        deadline = time.monotonic() + timeout
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not selector.select(remaining):
                        raise TimeoutError("diagnostic command timeout")
                    chunk = os.read(
                        process.stdout.fileno(), min(4096, max_output + 1 - len(output))
                    )
                    if not chunk:
                        break
                    output.extend(chunk)
                    if len(output) > max_output:
                        raise ValueError("diagnostic command output too large")
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
            if process.returncode:
                raise subprocess.CalledProcessError(process.returncode, args)
            return output.decode("ascii")
        except BaseException:
            process.kill()
            process.wait()
            raise


def collect_host():
    host = {}
    for collect in (
        lambda: {"cpu_utilization": min(1, number(psutil.cpu_percent(interval=0.1)) / 100)},
        lambda: {
            "memory_available_bytes": number(psutil.virtual_memory().available, integer=True),
            "memory_total_bytes": number(psutil.virtual_memory().total, integer=True),
        },
        lambda: {
            "root_available_bytes": number(psutil.disk_usage("/").free, integer=True),
            "root_total_bytes": number(psutil.disk_usage("/").total, integer=True),
        },
    ):
        try:
            host.update(collect())
        except (OSError, ValueError):
            pass
    return host


def memory_bytes(text):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(B|[kKMGT]i?B)", text.strip())
    if match is None:
        raise ValueError("invalid Docker resource value")
    unit = match[2]
    exponent = {"B": 0, "k": 1, "K": 1, "M": 2, "G": 3, "T": 4}[unit[0]]
    return number(
        round(float(match[1]) * (1024 if "i" in unit else 1000) ** exponent), integer=True
    )


def collect_container(
    since,
    sampled_at,
    previous_id,
    *,
    command=bounded_command,
    container_name="findme-photo-worker-a",
):
    try:
        try:
            state = json.loads(command(["docker", "inspect", "--format", INSPECT, container_name]))
        except subprocess.CalledProcessError:
            # Empty exact-name listing distinguishes missing from a failed Docker connection.
            present = command(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    f"name=^/{container_name}$",
                    "--format",
                    "{{.ID}}",
                ]
            )
            if present.strip():
                return None
            return {"present": False, "running": False, "events_available": False}
        if (
            not isinstance(state, list)
            or len(state) != 9
            or re.fullmatch(r"[0-9a-f]{64}", state[0]) is None
            or type(state[1]) is not bool
            or type(state[3]) is not bool
        ):
            raise ValueError("invalid Docker state")
        container = {
            "present": True,
            "running": state[1],
            "container_id": state[0],
            "restart_count": number(state[2], integer=True),
            "oom_killed": state[3],
            "exit_code": number(state[4], integer=True),
            "events_available": False,
        }
        nano, quota, period, memory = (number(v, integer=True) if v >= 0 else 0 for v in state[5:])
        if nano:
            container["cpu_limit_cores"] = nano / 1_000_000_000
        elif quota and period:
            container["cpu_limit_cores"] = quota / period
        if memory:
            container["memory_limit_bytes"] = memory
        if state[1]:
            try:
                stats_id, cpu, usage = (
                    command(
                        [
                            "docker",
                            "stats",
                            "--no-stream",
                            "--no-trunc",
                            "--format",
                            "{{json .ID}}\t{{json .CPUPerc}}\t{{json .MemUsage}}",
                            container_name,
                        ]
                    )
                    .strip()
                    .split("\t")
                )
                if json.loads(stats_id) != state[0]:
                    raise ValueError("Docker container changed during statistics")
                percent = json.loads(cpu)
                if not isinstance(percent, str) or not percent.endswith("%"):
                    raise ValueError("invalid Docker CPU")
                container["cpu_usage_cores"] = number(float(percent[:-1])) / 100
                used, _limit = json.loads(usage).split(" / ")
                container["memory_usage_bytes"] = memory_bytes(used)
            except (OSError, ValueError, TypeError, subprocess.SubprocessError):
                pass
        # Docker's last-256 global ring cannot prove completeness after filtering by name.
        # Unknown coverage never yields zero counts. Recreated IDs invalidate prior windows.
        since = max(since, sampled_at - timedelta(seconds=60))
        try:
            events = command(
                [
                    "docker",
                    "events",
                    "--since",
                    since.isoformat(),
                    "--until",
                    sampled_at.isoformat(),
                    "--filter",
                    f"container={container_name}",
                    "--filter",
                    "event=oom",
                    "--filter",
                    "event=restart",
                    "--format",
                    "{{.Actor.ID}}\t{{.Action}}\t{{.Time}}",
                ]
            )
            counts = {"oom": 0, "restart": 0}
            for line in events.splitlines():
                cid, action, timestamp = line.split("\t")
                observed = datetime.fromtimestamp(int(timestamp), UTC)
                if (
                    re.fullmatch(r"[0-9a-f]{64}", cid) is None
                    or not since - timedelta(seconds=1) <= observed <= sampled_at
                ):
                    raise ValueError("invalid Docker event")
                if cid == state[0] and action in counts:
                    counts[action] += 1
            container["events_since"] = since.isoformat()
            if previous_id in {None, state[0]}:
                container.update({f"{key}_events": count for key, count in counts.items() if count})
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            pass
        return container
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        return None


def parse_runtime(raw, generation, pool, sampled_at):
    if str(UUID(generation)) != generation or len(raw) > MAX_RUNTIME_BODY:
        raise ValueError("invalid runtime identity")
    values = {}
    busy = None
    seen = set()
    prefix = "worker_runtime_execution_duration_seconds_"
    for family in text_string_to_metric_families(raw.decode("utf-8")):
        for sample in family.samples:
            key = (sample.name, tuple(sorted(sample.labels.items())))
            if key in seen or sample.timestamp is not None or sample.exemplar is not None:
                raise ValueError("invalid runtime sample")
            seen.add(key)
            if sample.name == "worker_runtime_busy" and not sample.labels:
                busy = number(sample.value, integer=True)
                if busy not in ({0, 1} if pool == "selfie" else {0, 1, 2}):
                    raise ValueError("invalid runtime busy")
                continue
            label_keys = (
                {"kind", "outcome", "le"}
                if sample.name == prefix + "bucket"
                else {"kind", "outcome"}
            )
            if (
                set(sample.labels) != label_keys
                or sample.labels["kind"] not in KINDS[pool]
                or sample.labels["outcome"] not in OUTCOMES
            ):
                raise ValueError("invalid runtime labels")
            pair = (sample.labels["kind"], sample.labels["outcome"])
            row = values.setdefault(pair, {})
            if sample.name == "worker_runtime_executions_total":
                row["executions"] = number(sample.value, integer=True)
            elif sample.name in {prefix + "count", prefix + "sum"}:
                suffix = sample.name.removeprefix(prefix)
                row[suffix] = number(sample.value, integer=suffix == "count")
            elif sample.name == prefix + "bucket" and sample.labels["le"] in BUCKETS:
                row.setdefault("buckets", {})[sample.labels["le"]] = number(
                    sample.value, integer=True
                )
            else:
                raise ValueError("invalid runtime metric")
    if busy is None:
        raise ValueError("missing runtime busy")
    aggregates = {}
    for (kind, outcome), row in values.items():
        if (
            set(row) != {"executions", "count", "sum", "buckets"}
            or set(row["buckets"]) != set(BUCKETS)
            or row["executions"] != row["count"]
        ):
            raise ValueError("incomplete runtime aggregate")
        buckets = [row["buckets"][le] for le in BUCKETS]
        if sorted(buckets) != buckets or buckets[-1] != row["count"]:
            raise ValueError("invalid runtime buckets")
        aggregates.setdefault(kind, {})[outcome] = [row["count"], row["sum"], buckets]
    return {
        "registration_generation": generation,
        "sampled_at": sampled_at.isoformat(),
        "busy": busy,
        "aggregates": aggregates,
    }


def scrape_runtime(pool, sampled_at, *, url=None):
    try:
        with build_opener(ProxyHandler({}), RejectRedirects()).open(
            url or RUNTIME_URL, timeout=3
        ) as response:
            raw = response.read(MAX_RUNTIME_BODY + 1)
            generation = response.headers.get("X-Worker-Registration-Generation", "")
        return parse_runtime(raw, generation, pool, sampled_at)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def submit(snapshot, token):
    if re.fullmatch(r"[A-Za-z0-9_-]+", token) is None:
        raise ValueError("invalid fleet credential")
    request = Request(
        API,
        data=encoded(snapshot),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    # System CA/hostname verification; existing /etc/hosts resolves the canonical private edge.
    with build_opener(ProxyHandler({}), RejectRedirects()).open(request, timeout=5) as response:
        raw = response.read(4097)
    if len(raw) > 4096:
        raise ValueError("invalid diagnostic response")
    reply = json.loads(raw)
    if (
        not isinstance(reply, dict)
        or set(reply) != {"accepted", "duplicate"}
        or reply["accepted"] is not True
        or type(reply["duplicate"]) is not bool
    ):
        raise ValueError("invalid diagnostic response")


def valid_identity(identity):
    if (
        set(identity) != {"pool", "instance_id", "boot_id", "worker_build", "zone_id"}
        or identity["pool"] not in KINDS
        or identity["zone_id"] not in {"ru-central1-a", "ru-central1-b", "ru-central1-d"}
        or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", identity["instance_id"]) is None
        or re.fullmatch(r"[0-9a-f]{40}", identity["worker_build"]) is None
        or str(UUID(identity["boot_id"])) != identity["boot_id"]
    ):
        raise ValueError("invalid host identity")


class Probe:
    def __init__(self, identity, path=STATE_PATH, *, now=lambda: datetime.now(UTC), slot="a"):
        valid_identity(identity)
        self.identity, self.path, self.now = identity, path, now
        self.slot = slot
        self.pending = None
        try:
            with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                raw = stream.read(MAX_BODY + 1)
            previous = json.loads(raw)
            if (
                len(raw) > MAX_BODY
                or any(previous[key] != value for key, value in identity.items())
                or str(UUID(previous["collector_epoch"])) != previous["collector_epoch"]
                or not 1 <= number(previous["sequence"], integer=True) < MAX_NUMBER
            ):
                raise ValueError("invalid diagnostic state")
            started = datetime.fromisoformat(previous["collector_started_at"])
            sample = datetime.fromisoformat(previous["sampled_at"])
            if started.tzinfo is None or sample.tzinfo is None or not started <= sample <= now():
                raise ValueError("invalid diagnostic state time")
            self.pending = previous
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.epoch = self.pending["collector_epoch"] if self.pending else str(uuid4())
        self.started = self.pending["collector_started_at"] if self.pending else now().isoformat()

    def collect(self):
        previous = self.pending or {}
        now = self.now()
        container = previous.get("container") or {}
        since = (
            datetime.fromisoformat(previous["sampled_at"])
            if previous
            else now - timedelta(seconds=30)
        )
        return {
            "host": collect_host(),
            "container": collect_container(
                since,
                now,
                container.get("container_id"),
                container_name=f"findme-photo-worker-{self.slot}",
            ),
            "runtime": scrape_runtime(
                self.identity["pool"],
                self.now(),
                url=f"http://127.0.0.1:{SLOTS[self.slot]}/metrics",
            ),
        }

    def save(self):
        raw = encoded(self.pending)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError("unexpected diagnostic state path")
        # One fixed scratch snapshot also bounds disk state after systemd kills a slow save.
        temporary = self.path.with_name(".pending-" + self.path.name)
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def retry_pending(self, send):
        if self.pending is None:
            return False
        age = self.now() - datetime.fromisoformat(self.pending["sampled_at"])
        if not timedelta(0) <= age <= timedelta(seconds=90):
            return False
        try:
            send(self.pending)
            return True
        except (OSError, ValueError, TypeError):
            return False

    def step(self, send):
        observations = self.collect()
        sampled_at = self.now().isoformat()
        if self.pending and sampled_at <= self.pending["sampled_at"]:
            return self.retry_pending(send)
        self.pending = (
            self.identity
            | {
                "collector_epoch": self.epoch,
                "collector_started_at": self.started,
                "sequence": self.pending["sequence"] + 1 if self.pending else 1,
                "sampled_at": sampled_at,
            }
            | observations
        )
        self.save()
        return self.retry_pending(send)


def own_identity(active):
    return {
        "pool": os.environ["PHOTO_WORKER_POOL"],
        "instance_id": Path("/etc/findme-worker/instance-id").read_text().strip(),
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "worker_build": active["build"],
        "zone_id": os.environ["PHOTO_WORKER_ZONE"],
    }


def main():
    try:
        active = active_slot()
        probe = Probe(own_identity(active), slot=active["slot"])

        def send(snapshot):
            if active_slot() != active:
                raise ValueError("worker changed during diagnostic snapshot")
            submit(snapshot, os.environ["PHOTO_PROCESSING_FLEET_TOKEN"])

        success = probe.step(send)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        success = False
    print("worker_telemetry_sent" if success else "worker_telemetry_unavailable")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Run one bounded appearance backfill on the deployed single VM."""

import argparse
import fcntl
import json
import os
import re
import select
import signal
import subprocess
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker",
    "compose",
    "--project-name",
    "photo-prjct",
    "--env-file",
    str(ROOT / ".env"),
    "-f",
    str(ROOT / "docker-compose.deployment.yml"),
    "--profile",
    "photo-worker",
]
MAX_LINE_BYTES = 384 * 1024
GATES_OFF = {"person-appearance-processing": "off", "apparel-search-expansion": "off"}
OPERATOR_ERRORS = {
    "appearance_gates_enabled",
    "insufficient_previews",
    "unknown_event",
    "worker_job_timeout",
    "worker_start_timeout",
}


class BatchError(RuntimeError):
    """Fixed operator error code, safe to save in the summary."""


class Peer:
    def __init__(self, container, *command, timeout):
        self.process = subprocess.Popen(
            ["docker", "exec", "-i", container, *command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.timeout = timeout
        self.buffer = bytearray()

    def send(self, payload):
        line = json.dumps(payload, separators=(",", ":")).encode() + b"\n"
        if len(line) > MAX_LINE_BYTES:
            raise BatchError("oversized_request")
        self.process.stdin.write(line)
        self.process.stdin.flush()

    def receive(self, *, timeout=None):
        deadline = monotonic() + (self.timeout if timeout is None else timeout)
        while (end := self.buffer.find(b"\n")) < 0:
            room = MAX_LINE_BYTES - len(self.buffer)
            if room <= 0:
                raise BatchError("oversized_reply")
            available, _, _ = select.select(
                [self.process.stdout], [], [], max(0, deadline - monotonic())
            )
            if not available:
                raise BatchError("peer_timeout")
            block = os.read(self.process.stdout.fileno(), min(room, 65_536))
            if not block:
                raise BatchError("peer_eof")
            self.buffer.extend(block)
        reply = json.loads(self.buffer[:end])
        del self.buffer[: end + 1]
        if not isinstance(reply, dict):
            raise BatchError("invalid_reply")
        return reply

    def ask(self, payload):
        self.send(payload)
        reply = self.receive()
        if "error" in reply and "attempt_id" not in reply:
            error = reply["error"]
            raise BatchError(
                error
                if isinstance(error, str) and error in OPERATOR_ERRORS
                else "peer_rejected_operation"
            )
        return reply

    def drain(self):
        """Wait for the remote worker to confirm it has joined its local children."""
        try:
            self.process.stdin.close()
            deadline = monotonic() + 100
            while monotonic() < deadline:
                if self.receive(timeout=deadline - monotonic()) == {"drained": True}:
                    return True
        except (BatchError, OSError, ValueError):
            pass
        return False

    def close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        finally:
            self.process.stdout.close()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_row(stream, value):
    stream.write(json.dumps(value, separators=(",", ":")) + "\n")
    stream.flush()


def timestamp():
    return datetime.now(UTC).isoformat()


def run_batch(backend, worker, *, directory, limit, processes, stopped):
    files = {"manifest": directory / "manifest.json", "runtime": directory / "runtime.json"}
    runtime_pids, used_pids = set(), set()
    manifest, attempts, inflight = {}, {}, {}
    outcomes = {}
    enrollment_seconds = None
    started = started_at = finished_at = recognition_seconds = None
    error = None
    final = {}
    enrolled = False
    with (
        (directory / "claims.jsonl").open("w") as claims,
        (directory / "results.jsonl").open("w") as results,
    ):

        def record(row, ack=None):
            job_id = row["job_id"]
            if row["photo_id"] != manifest[job_id] or job_id in outcomes:
                raise BatchError("unexpected_job_result")
            outcome = {
                key: row[key]
                for key in (
                    "photo_id",
                    "job_id",
                    "attempt_id",
                    "status",
                    "accepted",
                    "error_code",
                    "download_ms",
                    "compute_ms",
                    "total_ms",
                    "people",
                    "reid",
                    "color",
                    "helmet",
                    "reasons",
                    "claimed_at",
                    "terminal_at",
                )
                if key in row
            }
            if ack is not None:
                outcome["worker_pid"] = ack["pid"]
                outcome["worker_wall_ms"] = ack.get("wall_ms")
            write_row(results, outcome)
            outcomes[job_id] = outcome

        try:
            ready = backend.receive()
            if ready != {"ready": True}:
                if ready.get("error") == "appearance_gates_enabled":
                    raise BatchError("appearance_gates_enabled")
                raise BatchError("backend_not_ready")
            runtime = worker.receive()["runtime"]
            runtime_pids = {row["pid"] for row in runtime}
            if (
                len(runtime) != processes
                or len(runtime_pids) != processes
                or any(row["native_threads"] != 1 or row["opencv_threads"] != 1 for row in runtime)
            ):
                raise BatchError("invalid_pool_runtime")
            save(
                files["runtime"],
                [
                    {key: row[key] for key in ("pid", "native_threads", "opencv_threads")}
                    for row in runtime
                ],
            )
            if stopped.is_set():
                raise BatchError("interrupted")
            enrollment = backend.ask({"op": "enroll"})
            enrolled = True
            rows = enrollment["manifest"]
            manifest = {row["job_id"]: row["photo_id"] for row in rows}
            if (
                len(rows) != limit
                or len(manifest) != limit
                or len(set(manifest.values())) != limit
                or enrollment["flags"] != GATES_OFF
            ):
                raise BatchError("invalid_manifest")
            enrollment_seconds = enrollment["enrollment_seconds"]
            save(
                files["manifest"],
                {
                    "event_id": enrollment["event_id"],
                    "seed": enrollment["seed"],
                    "configuration_hash": enrollment["configuration_hash"],
                    "flags": enrollment["flags"],
                    "enrollment_seconds": enrollment_seconds,
                    "manifest": [
                        {"job_id": job_id, "photo_id": photo_id}
                        for job_id, photo_id in manifest.items()
                    ],
                },
            )
            started_at = timestamp()
            started = monotonic()
            while True:
                while not error and not stopped.is_set() and len(inflight) < processes:
                    if len(attempts) == limit:
                        break
                    claim = backend.ask({"op": "claim"})
                    if claim.get("empty"):
                        error = "missing_manifest_claim"
                        break
                    job = claim["job"]
                    job_id = job["id"]
                    attempt_id = job["attempt_id"]
                    if manifest.get(job_id) != job["photo_id"] or job_id in attempts:
                        raise BatchError("unexpected_claim")
                    attempts[job_id] = attempt_id
                    write_row(
                        claims,
                        {"photo_id": job["photo_id"], "job_id": job_id, "attempt_id": attempt_id},
                    )
                    if stopped.is_set():
                        break
                    worker.send({"op": "submit", "claim": claim})
                    inflight[attempt_id] = job_id

                if not inflight:
                    break
                ack = worker.ask({"op": "next"})
                attempt_id = ack["attempt_id"]
                job_id = inflight.pop(attempt_id)
                result = backend.ask({"op": "result", "attempt_id": attempt_id})
                if result["job_id"] != job_id or result["attempt_id"] != attempt_id:
                    raise BatchError("unexpected_attempt")
                if ack["pid"] not in runtime_pids:
                    raise BatchError("unexpected_worker_process")
                used_pids.add(ack["pid"])
                if result["status"] != "in_progress":
                    record(result, ack)
                if not result["accepted"] or "error" in ack:
                    error = error or "attempt_not_accepted"
                if len(outcomes) and len(outcomes) % 50 == 0:
                    print(json.dumps({"processed": len(outcomes), "of": limit}), flush=True)
            finished_at = timestamp()
            recognition_seconds = monotonic() - started
        except BatchError as failure:
            error = error or str(failure)
        except (KeyError, OSError, ValueError, TypeError):
            error = error or "batch_protocol_failed"
        finally:
            drained = worker.drain()
            if not drained:
                error = error or "pool_drain_unconfirmed"
            if enrolled:
                try:
                    final = backend.ask({"op": "finish", "release": drained})
                    for row in final["remaining"]:
                        record(row)
                except (BatchError, KeyError, OSError, ValueError, TypeError):
                    error = error or "database_finish_failed"
            for job_id in manifest.keys() - outcomes.keys():
                record(
                    {
                        "job_id": job_id,
                        "photo_id": manifest[job_id],
                        "attempt_id": attempts.get(job_id),
                        "status": "unverified",
                        "accepted": False,
                    }
                )

    accepted = sum(row["accepted"] is True for row in outcomes.values())
    if stopped.is_set():
        error = error or "interrupted"
    if (
        accepted != limit
        or final.get("flags") != GATES_OFF
        or final.get("jobs") != {"succeeded": limit}
        or final.get("pointers_preserved") is not True
        or len(used_pids) != min(limit, processes)
    ):
        error = error or "database_or_pool_verification_failed"
    return {
        "status": "incomplete" if error else "complete",
        "error": error,
        "requested": limit,
        "accepted": accepted,
        "processes": processes,
        "executed_pids": sorted(used_pids),
        "flags": final.get("flags"),
        "jobs": final.get("jobs"),
        "pointers_preserved": final.get("pointers_preserved"),
        "enrollment_seconds": enrollment_seconds,
        "recognition_started_at": started_at,
        "recognition_finished_at": finished_at,
        "recognition_wall_seconds": recognition_seconds,
    }


def docker_output(*command):
    return subprocess.check_output(
        command, stderr=subprocess.DEVNULL, text=True, timeout=15
    ).strip()


def running_containers():
    containers, images = {}, {}
    for service in ("web", "photo-worker"):
        container = docker_output(*COMPOSE, "ps", "--status", "running", "-q", service)
        if not re.fullmatch(r"[0-9a-f]{12,64}", container):
            raise BatchError("service_not_running")
        metadata = json.loads(
            docker_output(
                "docker",
                "inspect",
                "--format",
                '{"image":{{json .Image}},'
                '"revision":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}',
                container,
            )
        )
        image, revision = metadata["image"], metadata["revision"]
        containers[service] = container
        images[service] = {
            "id": image if re.fullmatch(r"sha256:[0-9a-f]{64}", image) else None,
            "revision": revision if re.fullmatch(r"[0-9a-f]{40}", revision) else None,
        }
    return containers, images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", required=True, type=int)
    parser.add_argument("--limit", required=True, type=int)
    parser.add_argument("--processes", default=6, type=int)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    if not (
        1 <= args.event_id <= 2**63 - 1
        and 1 <= args.limit <= 1000
        and 1 <= args.processes <= 6
        and 0 <= args.seed <= 2**63 - 1
    ):
        parser.error("event-id, limit, processes or seed outside allowed range")
    os.umask(0o077)
    try:
        args.output_dir.mkdir(mode=0o700, exist_ok=False)
    except OSError:
        parser.error("output-dir must be a new directory in an existing writable parent")
    stopped = Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    summary = {"status": "incomplete", "error": "batch_start_failed"}
    try:
        with ExitStack() as stack:
            lock = stack.enter_context((ROOT / ".deployment.lock").open("a"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            containers, images = running_containers()
            save(args.output_dir / "images.json", images)
            backend = Peer(
                containers["web"],
                "python",
                "manage.py",
                "run_person_appearance_backfill_session",
                "--event-id",
                str(args.event_id),
                "--limit",
                str(args.limit),
                "--processes",
                str(args.processes),
                "--seed",
                str(args.seed),
                timeout=180,
            )
            stack.callback(backend.close)
            worker = Peer(
                containers["photo-worker"],
                "python",
                "-m",
                "photo_worker.appearance_batch",
                "--processes",
                str(args.processes),
                timeout=405,
            )
            stack.callback(worker.close)
            summary = run_batch(
                backend,
                worker,
                directory=args.output_dir,
                limit=args.limit,
                processes=args.processes,
                stopped=stopped,
            )
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        pass
    except BatchError as error:
        summary["error"] = str(error)
    summary.update({"event_id": args.event_id, "seed": args.seed})
    save(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if summary["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())

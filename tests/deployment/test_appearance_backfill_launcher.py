import json
import os
import runpy
import signal
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.operational


def test_launcher_stops_new_claims_on_failed_callback_and_redacts_grants(tmp_path, monkeypatch):
    namespace = runpy.run_path(str(ROOT / "deploy/backfill-person-appearance.py"))
    main = namespace["main"]
    state = main.__globals__
    output = tmp_path / "artifacts"
    containers = {"web": "a" * 64, "photo-worker": "b" * 64}
    images = {
        container: {"image": f"sha256:{container}", "revision": container[:40]}
        for container in containers.values()
    }

    def docker_output(*command):
        if command[1] == "compose":
            return containers[command[-1]]
        assert command[1] == "inspect"
        return json.dumps(images[command[-1]])

    monkeypatch.setitem(state, "ROOT", tmp_path)
    monkeypatch.setitem(state, "docker_output", docker_output)
    monkeypatch.setattr(os, "umask", lambda mode: mode)
    monkeypatch.setattr(signal, "signal", lambda *_: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill-person-appearance.py",
            "--event-id",
            "13",
            "--limit",
            "4",
            "--processes",
            "2",
            "--seed",
            "7",
            "--output-dir",
            str(output),
        ],
    )
    flags = {"person-appearance-processing": "off", "apparel-search-expansion": "off"}
    manifest = [{"photo_id": f"photo-{i}", "job_id": f"job-{i}"} for i in range(1, 5)]
    sequence = []

    class Backend:
        def receive(self):
            return {"ready": True}

        def ask(self, payload):
            operation = payload["op"]
            if operation == "enroll":
                return {
                    "event_id": 13,
                    "seed": 7,
                    "configuration_hash": "a" * 64,
                    "manifest": manifest,
                    "flags": flags,
                    "enrollment_seconds": 0.5,
                }
            if operation == "claim":
                number = sequence.count("claim") + 1
                sequence.append("claim")
                return {
                    "job": {
                        "attempt_id": f"attempt-{number}",
                        "id": f"job-{number}",
                        "photo_id": f"photo-{number}",
                        "download_url": "https://example.test/?signature=grant-secret",
                        "input_fingerprint": "object-secret",
                    }
                }
            if operation == "result":
                number = payload["attempt_id"].split("-")[-1]
                sequence.append(f"check-{number}")
                return {
                    "attempt_id": f"attempt-{number}",
                    "job_id": f"job-{number}",
                    "photo_id": f"photo-{number}",
                    "status": "failed" if number == "2" else "succeeded",
                    "accepted": number != "2",
                }
            assert payload == {"op": "finish", "release": True}
            assert sequence[-1] == "drained"
            return {
                "jobs": {"succeeded": 1, "failed": 1, "queued": 2},
                "remaining": [
                    {**row, "status": "queued", "accepted": False} for row in manifest[2:]
                ],
                "pointers_preserved": True,
                "flags": flags,
            }

        def close(self):
            pass

    class Worker:
        def receive(self):
            return {
                "runtime": [
                    {"pid": pid, "native_threads": 1, "opencv_threads": 1} for pid in (101, 102)
                ]
            }

        def send(self, payload):
            assert payload["op"] == "submit"

        def ask(self, payload):
            assert payload["op"] == "next"
            if "check-2" not in sequence:
                return {"attempt_id": "attempt-2", "pid": 102, "wall_ms": 4}
            return {"attempt_id": "attempt-1", "pid": 101, "wall_ms": 5}

        def drain(self):
            sequence.append("drained")
            return True

        def close(self):
            pass

    peers = {containers["web"]: Backend(), containers["photo-worker"]: Worker()}
    monkeypatch.setitem(state, "Peer", lambda container, *_args, **_kwargs: peers[container])
    assert main() == 1
    summary = json.loads((output / "summary.json").read_text())
    assert sequence == ["claim", "claim", "check-2", "check-1", "drained"]
    assert summary["status"] == "incomplete"
    assert summary["accepted"] == 1
    assert summary["error"] == "attempt_not_accepted"
    assert summary["flags"] == flags
    results = [json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()]
    assert {row["job_id"]: row["status"] for row in results} == {
        "job-1": "succeeded",
        "job-2": "failed",
        "job-3": "queued",
        "job-4": "queued",
    }
    assert json.loads((output / "images.json").read_text()) == {
        service: {"id": images[container]["image"], "revision": images[container]["revision"]}
        for service, container in containers.items()
    }
    for path in output.iterdir():
        assert "grant-secret" not in path.read_text()
        assert "object-secret" not in path.read_text()

#!/usr/bin/env python3
"""Pull current worker, admit a warmed local candidate, drain its predecessor."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from pathlib import Path

from host import (
    SLOTS,
    active_slot,
    host_lock,
    private_json,
    read_json,
    save_active,
    status,
    validate_slot,
)

INSPECT = (
    '{"digest":{{json (index .RepoDigests 0)}},'
    '"build":{{json (index .Config.Labels "org.opencontainers.image.revision")}}}'
)
CONTAINER_INSPECT = (
    '{"id":{{json .Id}},"image":{{json .Config.Image}},'
    '"build":{{json (index .Config.Labels "org.opencontainers.image.revision")}},'
    '"running":{{json .State.Running}},"started_at":{{json .State.StartedAt}}}'
)


def update(image, *, root=Path("/"), run=subprocess.run, status=status, readiness_seconds=600):
    if (
        re.fullmatch(
            r"ghcr\.io/[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*-worker(?::latest|@sha256:[0-9a-f]{64})",
            image,
        )
        is None
    ):
        raise ValueError("current worker image or immutable recovery digest required")
    base = root / "etc/findme-worker"
    environment = {"PATH": "/usr/bin:/bin:/usr/local/bin", "DOCKER_CONFIG": str(base / "docker")}

    def invoke(args, *, timeout=30, capture=False):
        return run(
            args,
            check=True,
            timeout=timeout,
            env=environment,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

    def compose(slot, args, *, timeout=120):
        invoke(
            [
                "docker",
                "compose",
                "--project-name",
                f"findme-worker-{slot}",
                "--env-file",
                str(base / "runtime.env"),
                "--env-file",
                str(base / f"slot-{slot}.env"),
                "-f",
                str(root / "usr/local/lib/findme-worker/compose.yml"),
                *args,
            ],
            timeout=timeout,
        )

    def candidate_status(candidate):
        def inspect_running():
            observed = json.loads(
                invoke(
                    [
                        "docker",
                        "container",
                        "inspect",
                        "--format",
                        CONTAINER_INSPECT,
                        f"findme-photo-worker-{candidate['slot']}",
                    ],
                    capture=True,
                ).stdout
            )
            if (
                observed.get("image") != candidate["digest"]
                or observed.get("build") != candidate["build"]
                or observed.get("running") is not True
                or not observed.get("id")
                or not observed.get("started_at")
            ):
                raise ValueError("candidate image not running")
            return observed

        before = inspect_running()
        reply = status(SLOTS[candidate["slot"]])
        if reply.get("ready") is not True:
            raise ValueError("candidate not ready")
        validate_slot(candidate | {"registration_generation": reply["registration_generation"]})
        # Same immutable image, container and process start must bracket readiness.
        # A previous slot occupant or concurrent restart cannot authorize its handoff.
        if inspect_running() != before:
            raise ValueError("candidate process changed during readiness")
        return reply

    def finish(candidate, old, *, admitted=None):
        deadline = time.monotonic() + readiness_seconds
        while True:
            try:
                reply = admitted or candidate_status(candidate)
                candidate["registration_generation"] = reply["registration_generation"]
                validate_slot(candidate)
                break
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                if time.monotonic() >= deadline:
                    raise ValueError("candidate readiness timeout") from None
                time.sleep(2)
        # Admission has already atomically transferred claims. The journal survives every
        # process interruption until both local projection and predecessor drain complete.
        save_active(candidate, root=root)
        if old is not None and old["slot"] != candidate["slot"]:
            # Compose down is idempotent if interruption happens after container removal.
            compose(old["slot"], ["down", "--timeout", "930"], timeout=960)
        (base / "replacement.json").unlink()

    with host_lock(root=root):
        journal_path = base / "replacement.json"
        try:
            active = active_slot(root=root)
        except FileNotFoundError:
            active = None
        if journal_path.exists():
            journal = read_json(journal_path)
            candidate = validate_slot(journal["candidate"])
            old = validate_slot(journal["old"]) if journal["old"] is not None else None
            try:
                admitted = candidate_status(candidate)
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                admitted = None
            if admitted is not None:
                finish(candidate, old, admitted=admitted)
                return "recovered"
        else:
            journal = None
        invoke(["docker", "pull", image], timeout=900)
        resolved = json.loads(
            invoke(["docker", "image", "inspect", "--format", INSPECT, image], capture=True).stdout
        )
        if journal is not None and resolved["digest"] == candidate["digest"]:
            compose(candidate["slot"], ["up", "-d"])
            finish(candidate, old)
            return "recovered"
        if journal is None and active is not None and resolved["digest"] == active["digest"]:
            return "unchanged"
        if journal is not None:
            # A corrected latest/recovery image supersedes a stuck unready candidate in
            # the same slot. Its predecessor continues serving until new admission.
            slot, active = candidate["slot"], old
        else:
            slot = "b" if active is not None and active["slot"] == "a" else "a"
        candidate = validate_slot(resolved | {"slot": slot})
        if candidate["digest"].split("@", 1)[0] != image.split("@", 1)[0].removesuffix(":latest"):
            raise ValueError("resolved image belongs to another repository")
        slot_env = base / f"slot-{slot}.env"
        # Nonsecret slot configuration; private because Compose is invoked as host root.
        descriptor = os.open(slot_env, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(
                f"WORKER_IMAGE={candidate['digest']}\nPHOTO_WORKER_BUILD={candidate['build']}\nWORKER_SLOT={slot}\nWORKER_PORT={SLOTS[slot]}\n"
            )
        private_json(journal_path, {"candidate": candidate, "old": active})
        # A failed Docker submission may still have started/admitted the candidate.
        # Preserve its journal; a later timer reconciles rather than killing it blindly.
        compose(slot, ["up", "-d"])
        finish(candidate, active)
        return "updated"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/findme-worker/bootstrap.json"))
    parser.add_argument("--image", help="explicit compatible immutable digest for recovery")
    args = parser.parse_args()
    try:
        print("worker_image_" + update(args.image or read_json(args.config)["worker_image"]))
    except BlockingIOError:
        print("worker_image_host_busy")
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("worker_image_update_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

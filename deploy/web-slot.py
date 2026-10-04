"""Select the canonical Django upstream without replacing the HTTPS edge."""

import argparse
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

SLOTS = ("web", "web-next")
STATIC_ROOT = "/app/src/backend/staticfiles"
# Seed only immutable, versioned assets. Do not overwrite a candidate's manifest
# or mutable files, and never delete the previous release's hashed URLs.
STATIC_SEED = r"""
import pathlib, re, shutil, sys, tarfile
root = pathlib.Path('/app/src/backend/staticfiles')
with tarfile.open(fileobj=sys.stdin.buffer, mode='r|*') as archive:
    for item in archive:
        path = pathlib.PurePosixPath(item.name)
        if not item.isfile() or path.is_absolute() or '..' in path.parts:
            continue
        if not re.search(r'\.[0-9a-f]{12}\.', path.name):
            continue
        destination = root.joinpath(*path.parts)
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(item)
        with destination.open('xb') as output:
            shutil.copyfileobj(source, output)
"""


def compose(root: Path, *arguments: str) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        "photo-prjct",
        "--env-file",
        str(root / ".env"),
        "-f",
        str(root / "docker-compose.deployment.yml"),
        "-f",
        str(root / "docker-compose.https.yml"),
        *arguments,
    ]


def require_healthy(root: Path, slot: str) -> None:
    container = subprocess.check_output(compose(root, "ps", "-q", slot), text=True).strip()
    if not container:
        raise ValueError(f"Django slot {slot} is not running")
    health = subprocess.check_output(
        ["docker", "inspect", "--format", "{{.State.Health.Status}}", container], text=True
    ).strip()
    if health != "healthy":
        raise ValueError(f"Django slot {slot} is not healthy")


def installed(root: Path) -> str:
    # nginx -T validates upstream DNS, which disappears when the selected
    # backend exits. Read the installed file without requiring it to be live;
    # switch still validates the healthy replacement through nginx -t/reload.
    configuration = subprocess.check_output(
        compose(root, "exec", "-T", "nginx", "cat", "/etc/nginx/conf.d/default.conf"),
        text=True,
    )
    configuration = re.sub(r"#[^\n]*", "", configuration)
    declarations = re.findall(r"\bupstream\s+django_upstream\b", configuration)
    upstream = re.search(
        r"(?m)^\s*upstream\s+django_upstream\s*\{\s*server (web|web-next):8000;\s*\}",
        configuration,
    )
    if len(declarations) != 1 or upstream is None:
        raise ValueError("Nginx does not have one valid selected Django upstream")
    return upstream.group(1)


def selected(root: Path) -> str:
    marker = root / "deploy/nginx/selected-slot"
    installed_slot = installed(root)
    if marker.exists():
        slot = marker.read_text().strip()
        if slot not in SLOTS:
            raise ValueError("Invalid selected Django slot")
        if slot != installed_slot:
            raise ValueError(
                f"Django slot mismatch: installed={installed_slot} persisted={slot}; "
                "explicit switch required"
            )
        return slot
    # One-time migration from the deployed single-slot configuration. A missing
    # marker is not permission to assume web on subsequent deployments.
    if installed_slot != "web":
        raise ValueError("Missing selection does not match the single-slot Nginx configuration")
    require_healthy(root, "web")
    return "web"


def workers(root: Path) -> tuple[set[int], set[int]]:
    output = subprocess.check_output(
        compose(root, "exec", "-T", "nginx", "ps", "-o", "pid,args"),
        text=True,
        timeout=15,
    )
    active, draining = set(), set()
    for line in output.splitlines():
        match = re.fullmatch(r"\s*(\d+)\s+nginx: worker process( is shutting down)?\s*", line)
        if match:
            (draining if match[2] else active).add(int(match[1]))
    if not active:
        raise ValueError("Cannot prove a live Nginx worker generation")
    return active, draining


def drain(root: Path, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while workers(root)[1]:
        if time.monotonic() >= deadline:
            raise ValueError(
                "Predecessor still owns accepted requests; both slots must remain alive"
            )
        time.sleep(1)


def switch(root: Path, slot: str) -> None:
    if slot not in SLOTS:
        raise ValueError("Invalid Django slot")
    marker = root / "deploy/nginx/selected-slot"
    if marker.exists():
        if marker.read_text().strip() not in SLOTS:
            raise ValueError("Invalid selected Django slot")
        # Explicit reconciliation is allowed after a host process interruption
        # between successful reload and marker write; selected() fails closed.
    require_healthy(root, slot)
    previous_workers, _ = workers(root)
    subprocess.run(
        compose(
            root,
            "exec",
            "-T",
            "nginx",
            "/bin/sh",
            "/opt/nginx/reload-nginx.sh",
            "--apply",
            "--slot",
            slot,
        ),
        check=True,
    )
    # nginx -s reload only acknowledges signal delivery. Wait until the original
    # generation stops accepting connections before request verification can
    # prove the candidate route. Old workers may continue their accepted requests.
    deadline = time.monotonic() + 15
    while True:
        active, _ = workers(root)
        if active.isdisjoint(previous_workers):
            break
        if time.monotonic() >= deadline:
            raise ValueError("Nginx reload did not activate a new worker generation")
        time.sleep(0.1)
    if installed(root) != slot:
        raise ValueError("Nginx selected upstream changed during reload")
    with tempfile.NamedTemporaryFile(mode="w", dir=marker.parent, delete=False) as output:
        output.write(slot + "\n")
        output.flush()
        os.fsync(output.fileno())
        temporary = Path(output.name)
    try:
        os.replace(temporary, marker)
    finally:
        temporary.unlink(missing_ok=True)


def seed_static(root: Path, slot: str) -> None:
    # Forward recovery also seeds from a crashed, stopped candidate. Docker cp
    # can read its files without restarting it or accepting more work.
    container = subprocess.check_output(compose(root, "ps", "-a", "-q", slot), text=True).strip()
    if not container:
        raise ValueError("Cannot seed static assets from an absent slot")
    with tempfile.TemporaryFile() as archive:
        subprocess.run(
            ["docker", "cp", f"{container}:{STATIC_ROOT}/.", "-"], stdout=archive, check=True
        )
        archive.seek(0)
        subprocess.run(
            compose(
                root,
                "run",
                "--rm",
                "-T",
                "--no-deps",
                "--entrypoint",
                "python",
                slot,
                "-c",
                STATIC_SEED,
            ),
            stdin=archive,
            check=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/opt/photo-prjct"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("selected")
    commands.add_parser("installed")
    commands.add_parser("reconcile")
    drain_command = commands.add_parser("drain")
    drain_command.add_argument("--timeout", type=int, default=180)
    for name in ("switch", "seed-static"):
        command = commands.add_parser(name)
        command.add_argument("slot", choices=SLOTS)
    args = parser.parse_args()
    try:
        if args.command == "selected":
            print(selected(args.root))
        elif args.command == "installed":
            print(installed(args.root))
        elif args.command == "switch":
            switch(args.root, args.slot)
        elif args.command == "reconcile":
            slot = installed(args.root)
            marker = args.root / "deploy/nginx/selected-slot"
            # Agreement proves the reload generation was acknowledged before
            # the marker was written. Its backend may since have crashed: do
            # not require that failed backend to recover before preparing its
            # replacement. The caller still drains accepted work before reuse.
            # Disagreement remains ambiguous and requires a healthy explicit
            # reload; never guess which opposite slot is safe to replace.
            if not marker.exists() or marker.read_text().strip() != slot:
                switch(args.root, slot)
            print(selected(args.root))
        elif args.command == "drain":
            if args.timeout < 0 or args.timeout > 300:
                raise ValueError("Drain timeout must be between 0 and 300 seconds")
            drain(args.root, args.timeout)
        else:
            seed_static(args.root, args.slot)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Django slot operation failed: {error}\n")


if __name__ == "__main__":
    main()

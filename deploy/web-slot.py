"""Select the canonical Django upstream without replacing the HTTPS edge."""

import argparse
import os
import re
import subprocess
import tempfile
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


def selected(root: Path) -> str:
    marker = root / "deploy/nginx/selected-slot"
    configuration = subprocess.check_output(
        compose(root, "exec", "-T", "nginx", "nginx", "-T"),
        text=True,
    )
    upstream = re.search(
        r"upstream django_upstream\s*\{\s*server (web|web-next):8000;\s*\}", configuration
    )
    if upstream is None:
        raise ValueError("Nginx does not have one valid selected Django upstream")
    installed = upstream.group(1)
    if marker.exists():
        slot = marker.read_text().strip()
        if slot not in SLOTS:
            raise ValueError("Invalid selected Django slot")
        if slot != installed:
            raise ValueError(
                f"Django slot mismatch: installed={installed} persisted={slot}; "
                "explicit switch required"
            )
        return slot
    # One-time migration from the deployed single-slot configuration. A missing
    # marker is not permission to assume web on subsequent deployments.
    if installed != "web":
        raise ValueError("Missing selection does not match the single-slot Nginx configuration")
    require_healthy(root, "web")
    return "web"


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
    container = subprocess.check_output(compose(root, "ps", "-q", slot), text=True).strip()
    if not container:
        raise ValueError("Cannot seed static assets from a stopped slot")
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
    for name in ("switch", "seed-static"):
        command = commands.add_parser(name)
        command.add_argument("slot", choices=SLOTS)
    args = parser.parse_args()
    try:
        if args.command == "selected":
            print(selected(args.root))
        elif args.command == "switch":
            switch(args.root, args.slot)
        else:
            seed_static(args.root, args.slot)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Django slot operation failed: {error}\n")


if __name__ == "__main__":
    main()

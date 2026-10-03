#!/usr/bin/env python3
"""Reject a web image unable to read active native AdaFace evidence."""

import argparse
import json
import subprocess
from pathlib import Path


def verify(root, app_image, *, run=subprocess.run):
    root = Path(root)
    if not (root / ".env").is_file():
        return
    compose = [
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
    ]
    result = run(
        compose
        + [
            "exec",
            "-T",
            "db",
            "sh",
            "-c",
            "exec psql --no-psqlrc --no-password --tuples-only --no-align "
            '--username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --command "$1"',
            "native-generation-proof",
            "SELECT EXISTS (SELECT 1 FROM processing_eventfaceembeddingactivation a "
            "JOIN picflow_event e ON e.id = a.event_id "
            "WHERE e.face_search_generation = 'adaface_v5' "
            "AND a.generations @> "
            """'[{"configuration":{"embedding_storage":"vector_only"}}]'::jsonb)::text;""",
        ],
        check=True,
        text=True,
        capture_output=True,
        timeout=60,
    )
    native = json.loads(result.stdout)
    if type(native) is not bool:
        raise ValueError("invalid active native-generation proof")
    if not native:
        return
    run(["docker", "pull", app_image], check=True, capture_output=True, timeout=600)
    result = run(
        ["docker", "image", "inspect", app_image],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    rows = json.loads(result.stdout)
    if (
        len(rows) != 1
        or rows[0]
        .get("Config", {})
        .get("Labels", {})
        .get("ru.findme-photo.historical-adaface-contract")
        != "vector-only-v1"
    ):
        raise ValueError("web image is incompatible with active native AdaFace generations")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--app-image", required=True)
    args = parser.parse_args()
    verify(args.root, args.app_image)


if __name__ == "__main__":
    main()

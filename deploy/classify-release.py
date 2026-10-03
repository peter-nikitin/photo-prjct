#!/usr/bin/env python3
"""Select publication from effective Docker and canonical deployment inputs."""

import argparse
import subprocess


def classify(paths):
    web = worker = base = False
    for path in paths:
        if path.startswith(("docs/", "tests/", ".agents/", ".superpowers/")) or path.endswith(
            ".md"
        ):
            continue
        if path == ".dockerignore" or path == ".github/workflows/deploy.yml":
            web = worker = base = True
        elif path == "Dockerfile.worker-base" or path in {
            "src/worker/requirements.txt",
            "src/worker/requirements.cpu.txt",
        }:
            worker = base = True
        elif path == "Dockerfile.worker" or path.startswith("src/worker/photo_worker/"):
            worker = True
        elif (
            path in {"Dockerfile", "Dockerfile.import-worker"}
            or path.startswith(("src/backend/", "src/import_worker/", "deploy/"))
            or path.startswith("docker-compose")
            or path == ".env.example"
        ):
            web = True
        elif not path.startswith(("src/worker/tests/", ".github/")):
            # Unknown operational/configuration inputs must not silently look like documentation.
            web = True
    return {"web_changed": web, "worker_changed": worker, "worker_base_changed": base}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    if args.base == "0" * 40:
        # An initial push has no predecessor; publish both complete components explicitly.
        selected = dict.fromkeys(("web_changed", "worker_changed", "worker_base_changed"), True)
    else:
        paths = subprocess.run(
            ["git", "diff", "--name-only", args.base, args.head],
            check=True,
            text=True,
            capture_output=True,
        ).stdout.splitlines()
        selected = classify(paths)
    for key, value in selected.items():
        print(f"{key}={str(value).lower()}")


if __name__ == "__main__":
    main()

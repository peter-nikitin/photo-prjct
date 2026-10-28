"""Classify changed paths for the infrastructure release workflow."""

import sys


def classify(paths: list[str]) -> dict[str, bool]:
    terraform = any(
        path.startswith("infra/terraform/")
        or path.startswith("infra/ci/")
        or path == ".github/workflows/infrastructure.yml"
        for path in paths
    )
    ansible = any(path.startswith("infra/ansible/") for path in paths)
    runtime = any(
        path.startswith(("src/backend/", "src/worker/", "src/import_worker/", "deploy/"))
        or path.startswith("docker-compose.deployment.yml")
        or path
        in {"Dockerfile", "Dockerfile.worker", "Dockerfile.worker-base", "Dockerfile.import-worker"}
        for path in paths
    )
    return {"terraform": terraform, "ansible": ansible, "runtime": runtime}


def main() -> None:
    result = classify([line.strip() for line in sys.stdin if line.strip()])
    for key, value in result.items():
        print(f"{key}={str(value).lower()}")


if __name__ == "__main__":
    main()

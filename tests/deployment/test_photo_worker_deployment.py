import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from tests.deployment.compose_config import render_compose

ROOT = Path(__file__).resolve().parents[2]
WORKER_IMAGE = "ghcr.io/example/photo-worker@sha256:" + "a" * 64
ROLES = {
    "photo-worker": (
        10,
        (
            "1/capture_metadata/2",
            "2/generate_preview/1",
            "2/generate_watermarked_preview/1",
            "1/selfie_query/2",
        ),
    ),
    "photo-worker-face": (2, ("3/face_embedding/5",)),
    "photo-worker-appearance": (2, ("2/person_appearance/1",)),
    "photo-worker-bib": (1, ("1/bib_recognition/1",)),
}
DESIRED_TOKEN = "private-desired-worker-token"
RUNNING_TOKEN = "private-running-worker-token"


def test_deployment_partitions_upload_work_despite_stale_overrides(tmp_path):
    services = render_compose(
        tmp_path,
        "docker-compose.deployment.yml",
        profiles=("photo-worker",),
        environment={
            "PHOTO_WORKER_IMAGE": WORKER_IMAGE,
            "PHOTO_PROCESSING_WORKER_TOKEN": DESIRED_TOKEN,
            "PHOTO_WORKER_PROCESSOR_IDENTITIES": "1/selfie_query/2",
            "PHOTO_WORKER_PROCESSOR_TYPES": "selfie_query",
        },
    )["services"]
    assert {
        name for name, service in services.items() if "photo-worker" in service.get("profiles", [])
    } == set(ROLES)
    for name, (replicas, identities) in ROLES.items():
        worker = services[name]
        assert worker.get("scale", 1) == replicas
        assert worker["image"] == WORKER_IMAGE
        assert "volumes" not in worker and "ports" not in worker
        environment = worker["environment"]
        assert set(environment) == {
            "PHOTO_WORKER_API_URL",
            "PHOTO_WORKER_TOKEN",
            "PHOTO_WORKER_BUILD",
            "PHOTO_WORKER_LEASE_SECONDS",
            "PHOTO_WORKER_HTTP_TIMEOUT_SECONDS",
            "PHOTO_WORKER_PROCESSOR_IDENTITIES",
            "PHOTO_WORKER_PROCESSOR_TYPES",
        }
        assert environment["PHOTO_WORKER_API_URL"] == "http://web:8000/internal/photo-processing/v1"
        assert environment["PHOTO_WORKER_TOKEN"] == DESIRED_TOKEN
        assert set(environment["PHOTO_WORKER_PROCESSOR_IDENTITIES"].split(",")) == set(identities)
        assert set(environment["PHOTO_WORKER_PROCESSOR_TYPES"].split(",")) == {
            identity.split("/")[1] for identity in identities
        }
    assert services["web"]["environment"]["GUNICORN_WORKERS"] == "2"
    assert services["web"]["environment"]["GUNICORN_THREADS"] == "3"


@pytest.fixture
def release(tmp_path):
    (tmp_path / "deploy").symlink_to(ROOT / "deploy", target_is_directory=True)
    docker = tmp_path / "docker"
    docker.write_text(
        f"#!{sys.executable}\n"
        + r"""
import json, sys
from pathlib import Path

store = Path("worker.json")
state = json.loads(store.read_text())
containers = state["containers"]
desired = state["desired"]
args = sys.argv[1:]
with Path("commands").open("a") as commands:
    commands.write(" ".join(args) + "\n")
def managed(container, service):
    return (
        container["service"] == service
        and container.get("project", "photo-prjct") == "photo-prjct"
        and container.get("oneoff", "False") == "False"
    )

if args[0] == "ps":
    for cid, container in containers.items():
        labels = {
            "com.docker.compose.project": container.get("project", "photo-prjct"),
            "com.docker.compose.service": container["service"],
            "com.docker.compose.oneoff": container.get("oneoff", "False"),
        }
        filters = dict(
            arg.removeprefix("label=").split("=", 1) for arg in args if arg.startswith("label=")
        )
        if not filters.items() <= labels.items():
            continue
        if "-a" not in args and container["status"] != "running":
            continue
        print(cid if "-q" in args else container["service"])
elif args[0] == "inspect":
    print(json.dumps(containers[args[-1]]))
    print("private-running-worker-token", file=sys.stderr)
elif args[0] == "stop":
    for cid in args[3:]:
        containers[cid]["status"] = "exited"
elif args[0] == "rm":
    for cid in args[1:]:
        del containers[cid]
elif args[0] == "compose" and "--profile" in args:
    subcommand, *rest = args[args.index("--profile") + 2:]
    if subcommand == "config":
        if "--services" in rest:
            print("\n".join(["web", *desired]))
        else:
            print(json.dumps({"services": desired}))
    if subcommand == "up":
        for name in (s for s in rest if s in desired):
            for cid in list(containers):
                if managed(containers[cid], name):
                    del containers[cid]
            service = desired[name]
            for number in range(1, service.get("scale", 1) + 1):
                cid = f"{name}-{number}"
                if cid == state["missing"]:
                    continue
                containers[cid] = {
                    "service": name, "image": service["image"], "status": "running",
                    "environment": ["PATH=/usr/bin"] + [
                        f"{key}={value}" for key, value in service["environment"].items()
                    ],
                }
                if cid == "photo-worker-face-2":
                    containers[cid].update(state["corruption"])
store.write_text(json.dumps(state))
"""
    )
    docker.chmod(0o755)
    return tmp_path


def run_release(
    release, *, web=True, worker=False, active=True, search=False, corruption=None, missing=None
):
    desired = {
        name: {
            "profiles": ["photo-worker"],
            "scale": replicas,
            "image": WORKER_IMAGE,
            "environment": {
                "PHOTO_WORKER_PROCESSOR_IDENTITIES": ",".join(identities),
                "PHOTO_WORKER_PROCESSOR_TYPES": ",".join(i.split("/")[1] for i in identities),
                "PHOTO_WORKER_TOKEN": DESIRED_TOKEN,
            },
        }
        for name, (replicas, identities) in ROLES.items()
    }
    containers = {
        "legacy": {"service": "photo-worker", "status": "running" if active else "exited"}
    }
    if search:
        desired = {"photo-worker": desired["photo-worker"]}
        del desired["photo-worker"]["scale"]
        containers |= {
            "face-a": {"service": "photo-worker-face", "status": "running"},
            "face-b": {"service": "photo-worker-face", "status": "exited"},
            "other-project": {
                "service": "photo-worker-face",
                "status": "running",
                "project": "other",
            },
            "oneoff": {"service": "photo-worker-face", "status": "running", "oneoff": "True"},
            "run-command": {"service": "photo-worker", "status": "exited", "oneoff": "True"},
            "other-service": {"service": "import-worker", "status": "running"},
        }
    state = release / "worker.json"
    state.write_text(
        json.dumps(
            {
                "desired": desired,
                "containers": containers,
                "corruption": corruption or {},
                "missing": missing,
            }
        )
    )
    result = subprocess.run(
        ["sh", str(release / "deploy/apply-deployment.sh")],
        env={
            **os.environ,
            "PATH": f"{release}:{os.environ['PATH']}",
            "DEPLOY_ROOT": str(release),
            "DEPLOY_WEB": str(web).lower(),
            "DEPLOY_WORKER": str(worker).lower(),
            "DEPLOY_IMPORT": "false",
            "DEPLOY_COMMERCE": "false",
        },
        capture_output=True,
        text=True,
    )
    return result, (release / "commands").read_text(), json.loads(state.read_text())["containers"]


@pytest.mark.parametrize("worker", [True, False])
def test_active_release_reconciles_all_replicas_with_one_image(release, worker):
    result, calls, actual = run_release(release, web=not worker, worker=worker)
    assert result.returncode == 0, result.stderr
    assert set(actual) == {f"{name}-{i}" for name, (n, _) in ROLES.items() for i in range(1, n + 1)}
    assert all(c["image"] == WORKER_IMAGE for c in actual.values())
    assert calls.count("pull photo-worker") == int(worker)
    up = calls.index("up -d --no-deps --pull never --timeout 180 photo-worker")
    if worker:
        assert calls.index("pull photo-worker") < up
        assert "manage.py" not in calls and "up -d --no-deps import-worker" not in calls
    else:
        assert calls.index("up -d --wait web") < up
    assert result.stdout.index("PHOTO_WORKER_VERIFY=ok") < result.stdout.index(
        "DEPLOY_STAGE=image-prune"
    )


@pytest.mark.parametrize(("web", "worker"), [(True, False), (False, True)])
def test_recovery_release_does_not_activate_inactive_photo_profile(release, web, worker):
    result, calls, actual = run_release(release, web=web, worker=worker, active=False)
    assert result.returncode == 0, result.stderr
    assert actual == {"legacy": {"service": "photo-worker", "status": "exited"}}
    assert "pull photo-worker" not in calls
    assert "PHOTO_WORKER_VERIFY=" not in result.stdout


def test_search_release_drains_only_retired_project_workers(release):
    result, calls, actual = run_release(release, active=False, search=True)
    assert result.returncode == 0, result.stderr
    assert set(actual) == {
        "photo-worker-1",
        "oneoff",
        "other-service",
        "other-project",
        "run-command",
    }
    assert "stop --time 180 face-a face-b" in calls
    assert "PHOTO_WORKER_VERIFY=ok" in result.stdout


def test_standalone_package_runs_worker_verification_after_extraction(release):
    bundle = release / "deployment.tar"
    subprocess.run(["sh", ROOT / "deploy/package-deployment.sh", bundle], check=True)
    (release / "deploy").unlink()
    with tarfile.open(bundle) as package:
        package.extractall(release, filter="data")

    result, _, _ = run_release(release)
    assert result.returncode == 0, result.stderr
    assert "PHOTO_WORKER_VERIFY=ok" in result.stdout


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        (
            {"corruption": {"environment": ["PHOTO_WORKER_TOKEN=" + RUNNING_TOKEN]}},
            "environment-mismatch",
        ),
        ({"corruption": {"status": "exited"}}, "not-running"),
        ({"corruption": {"image": "ghcr.io/example/wrong-reference"}}, "image-mismatch"),
        ({"missing": "photo-worker-10"}, "container-mismatch"),
    ],
)
def test_release_detects_missing_or_broken_replica_without_disclosing_secrets(
    release, kwargs, status
):
    result, calls, _ = run_release(release, **kwargs)
    assert result.returncode != 0
    assert "image prune" not in calls
    output = result.stdout + result.stderr
    assert f"PHOTO_WORKER_VERIFY={status}" in result.stdout
    assert DESIRED_TOKEN not in output
    assert RUNNING_TOKEN not in output
    assert "ghcr.io/example/wrong-reference" not in output

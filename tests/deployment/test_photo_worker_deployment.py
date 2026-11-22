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
SEARCH_ROLES = {
    "photo-worker": (1, ("1/selfie_query/2",)),
    **{name: (0, identities) for name, (_, identities) in ROLES.items() if name != "photo-worker"},
}
VALIDATOR_IMAGE = "ghcr.io/example/bib-validator@sha256:" + "b" * 64
DESIRED_TOKEN = "private-desired-worker-token"
RUNNING_TOKEN = "private-running-worker-token"


def test_deployment_enables_only_selfie_processing_despite_stale_overrides(tmp_path):
    config = render_compose(
        tmp_path,
        "docker-compose.deployment.yml",
        profiles=("photo-worker", "bib-validation"),
        environment={
            "PHOTO_WORKER_IMAGE": WORKER_IMAGE,
            "BIB_VALIDATOR_IMAGE": VALIDATOR_IMAGE,
            "PHOTO_PROCESSING_WORKER_TOKEN": DESIRED_TOKEN,
            "PHOTO_WORKER_PROCESSOR_IDENTITIES": "3/face_embedding/5,1/bib_recognition/1",
            "PHOTO_WORKER_PROCESSOR_TYPES": "face_embedding,bib_recognition",
        },
    )
    services = config["services"]
    validator = services["bib-validator"]
    assert validator["image"] == VALIDATOR_IMAGE
    assert validator["profiles"] == ["bib-validation"]
    assert set(validator["networks"]) == {"bib-validation"}
    assert config["networks"]["bib-validation"]["internal"] is True
    assert validator["healthcheck"]["test"]
    assert not {"ports", "volumes", "environment", "env_file"} & validator.keys()
    assert set(services["photo-worker-bib"]["networks"]) == {"default", "bib-validation"}
    assert {"bib-validation"}.isdisjoint(services["web"]["networks"])
    assert {
        name for name, service in services.items() if "photo-worker" in service.get("profiles", [])
    } == set(SEARCH_ROLES)
    for name, (replicas, identities) in SEARCH_ROLES.items():
        worker = services[name]
        assert worker.get("scale", 1) == replicas
        assert worker["image"] == WORKER_IMAGE
        assert "volumes" not in worker and "ports" not in worker
        environment = worker["environment"]
        validator_keys = set()
        if name == "photo-worker-bib":
            validator_keys = {"PHOTO_WORKER_BIB_VALIDATOR_URL"}
            assert environment["PHOTO_WORKER_BIB_VALIDATOR_URL"] == "http://bib-validator:8180"
        assert set(environment) == validator_keys | {
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
    assert services["photo-worker"]["cpus"] == 2
    assert services["web"]["environment"]["GUNICORN_WORKERS"] == "4"
    assert services["web"]["environment"]["GUNICORN_THREADS"] == "2"


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
    if subcommand == "up" and rest[-1] == "bib-validator":
        if state["validator_unhealthy"]:
            sys.exit(1)
        containers["validator"] = {
            "service": "bib-validator", "status": "running", "image": state["validator_image"],
        }
    if subcommand == "stop" and rest[-1] == "bib-validator":
        containers["validator"]["status"] = "exited"
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
    release,
    *,
    web=True,
    worker=False,
    active=True,
    search=False,
    retire=False,
    corruption=None,
    missing=None,
    validator_unhealthy=False,
):
    roles = SEARCH_ROLES if search else ROLES
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
        for name, (replicas, identities) in roles.items()
    }
    containers = {
        "legacy": {"service": "photo-worker", "status": "running" if active else "exited"}
    }
    if search:
        containers["validator"] = {"service": "bib-validator", "status": "running"}
        if retire:
            desired = {"photo-worker": desired["photo-worker"]}
            del desired["photo-worker"]["scale"]
        containers |= {
            "face-a": {"service": "photo-worker-face", "status": "running"},
            "face-b": {"service": "photo-worker-face", "status": "exited"},
            "appearance-a": {"service": "photo-worker-appearance", "status": "running"},
            "bib-a": {"service": "photo-worker-bib", "status": "running"},
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
                "validator_unhealthy": validator_unhealthy,
                "validator_image": VALIDATOR_IMAGE,
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


def test_unhealthy_bib_validator_blocks_worker_rollout(release):
    result, calls, _ = run_release(release, worker=True, validator_unhealthy=True)

    assert result.returncode != 0
    assert "--wait-timeout 180 --no-deps --pull missing bib-validator" in calls
    assert "--no-deps --pull never --timeout 180 photo-worker" not in calls


@pytest.mark.parametrize("worker", [True, False])
def test_active_release_reconciles_all_replicas_with_one_image(release, worker):
    result, calls, actual = run_release(release, web=not worker, worker=worker)
    assert result.returncode == 0, result.stderr
    assert set(actual) == {"validator"} | {
        f"{name}-{i}" for name, (n, _) in ROLES.items() for i in range(1, n + 1)
    }
    assert calls.index("--wait-timeout 180 --no-deps --pull missing bib-validator") < calls.index(
        "--no-deps --pull never --timeout 180 photo-worker"
    )
    assert actual["validator"]["image"] == VALIDATOR_IMAGE
    assert all(c["image"] == WORKER_IMAGE for name, c in actual.items() if name != "validator")
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


@pytest.mark.parametrize("retire", [False, True])
def test_search_release_stops_upload_workers_and_keeps_selfie(release, retire):
    result, calls, actual = run_release(release, active=False, search=True, retire=retire)
    assert result.returncode == 0, result.stderr
    assert set(actual) == {
        "photo-worker-1",
        "oneoff",
        "other-service",
        "other-project",
        "run-command",
        "validator",
    }
    assert actual["validator"]["status"] == "exited"
    assert calls.index("--no-deps --pull never --timeout 180") < calls.index("stop bib-validator")
    assert "PHOTO_WORKER_PROCESSOR_TYPES=selfie_query" in actual["photo-worker-1"]["environment"]
    if retire:
        assert "stop --time 180 face-a face-b" in calls
    else:
        assert "up -d --no-deps --pull never --timeout 180 " + " ".join(SEARCH_ROLES) in calls
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

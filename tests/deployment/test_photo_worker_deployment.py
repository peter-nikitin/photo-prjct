import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKER_IMAGE = "ghcr.io/example/photo-worker@sha256:" + "a" * 64
IDENTITIES = (
    "1/capture_metadata/2,2/generate_preview/1,2/generate_watermarked_preview/1,"
    "3/face_embedding/5,1/bib_recognition/1,2/person_appearance/1,1/selfie_query/2"
)
PROCESSOR_TYPES = (
    "bib_recognition,face_embedding,capture_metadata,generate_preview,"
    "generate_watermarked_preview,person_appearance,selfie_query"
)
STALE_IDENTITIES = "1/selfie_query/2"
DESIRED_TOKEN = "private-desired-worker-token"
RUNNING_TOKEN = "private-running-worker-token"


def test_deployment_renders_upload_configuration_despite_stale_overrides():
    required = {
        key: "test"
        for key in (
            "ALLOWED_HOSTS",
            "DB_NAME",
            "DB_PASSWORD",
            "DB_USER",
            "DEBUG",
            "GALLERY_CDN_ORIGIN",
            "GUNICORN_MAX_REQUESTS",
            "GUNICORN_MAX_REQUESTS_JITTER",
            "GUNICORN_TIMEOUT",
            "PUBLIC_DOMAIN",
            "SECRET_KEY",
        )
    }
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-f",
            str(ROOT / "docker-compose.deployment.yml"),
            "--profile",
            "photo-worker",
            "config",
            "--format",
            "json",
            "photo-worker",
            "web",
        ],
        env={
            **os.environ,
            **required,
            "APP_IMAGE": WORKER_IMAGE,
            "PHOTO_WORKER_IMAGE": WORKER_IMAGE,
            "PHOTO_WORKER_PROCESSOR_IDENTITIES": STALE_IDENTITIES,
            "PHOTO_WORKER_PROCESSOR_TYPES": "selfie_query",
        },
        check=True,
        capture_output=True,
        text=True,
    )
    services = json.loads(result.stdout)["services"]
    worker = services["photo-worker"]
    assert worker["environment"]["PHOTO_WORKER_PROCESSOR_IDENTITIES"] == IDENTITIES
    assert worker["environment"]["PHOTO_WORKER_PROCESSOR_TYPES"] == PROCESSOR_TYPES
    assert "cpus" not in worker
    assert services["web"]["environment"]["GUNICORN_WORKERS"] == "2"
    assert services["web"]["environment"]["GUNICORN_THREADS"] == "3"


@pytest.fixture
def release(tmp_path):
    (tmp_path / "deploy").symlink_to(ROOT / "deploy", target_is_directory=True)
    docker = tmp_path / "docker"
    docker.write_text(
        f"""#!{sys.executable}
import json, sys
from pathlib import Path

state_file = Path("worker.json")
state = json.loads(state_file.read_text())
args = sys.argv[1:]
with Path("commands").open("a") as commands:
    commands.write(" ".join(args) + "\\n")
if args[0] == "inspect":
    actual = state["actual"]
    print(json.dumps(actual))
    print(state["desired"]["environment"]["PHOTO_WORKER_TOKEN"], file=sys.stderr)
elif args[-1] == "photo-worker":
    if "ps" in args:
        if state["actual"]["status"] == "running" or "--all" in args:
            print("worker-id")
    elif "config" in args:
        print(json.dumps({{"services": {{"photo-worker": state["desired"]}}}}))
    elif "up" in args:
        desired = state["desired"]
        state["actual"] = {{
            "image": desired["image"],
            "status": "running",
            "environment": ["PATH=/usr/bin"] + [
                f"{{key}}={{value}}" for key, value in desired["environment"].items()
            ],
        }} | state["corruption"]
        state_file.write_text(json.dumps(state))
"""
    )
    docker.chmod(0o755)
    return tmp_path


def run_release(release, *, web=True, worker=False, active=True, corruption=None):
    desired = {
        "image": WORKER_IMAGE,
        "environment": {
            "PHOTO_WORKER_PROCESSOR_IDENTITIES": IDENTITIES,
            "PHOTO_WORKER_PROCESSOR_TYPES": PROCESSOR_TYPES,
            "PHOTO_WORKER_TOKEN": DESIRED_TOKEN,
        },
    }
    state = release / "worker.json"
    state.write_text(
        json.dumps(
            {
                "desired": desired,
                "actual": {
                    "image": WORKER_IMAGE
                    if not worker
                    else WORKER_IMAGE.replace("a" * 64, "b" * 64),
                    "status": "running" if active else "exited",
                    "environment": [
                        "PHOTO_WORKER_PROCESSOR_IDENTITIES=" + STALE_IDENTITIES,
                        "PHOTO_WORKER_PROCESSOR_TYPES=selfie_query",
                    ],
                },
                "corruption": corruption or {},
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
    return result, (release / "commands").read_text(), json.loads(state.read_text())["actual"]


def test_web_only_release_restores_upload_processing_using_existing_image(release):
    result, calls, actual = run_release(release)
    assert result.returncode == 0, result.stderr
    assert "PHOTO_WORKER_PROCESSOR_IDENTITIES=" + IDENTITIES in actual["environment"]
    assert "PHOTO_WORKER_PROCESSOR_TYPES=" + PROCESSOR_TYPES in actual["environment"]
    assert actual["image"] == WORKER_IMAGE
    assert calls.index("up -d --wait web") < calls.index(
        "up -d --no-deps --pull never --timeout 180 photo-worker"
    )
    assert "pull photo-worker" not in calls
    assert "PHOTO_WORKER_VERIFY=ok" in result.stdout


def test_worker_only_release_pulls_new_image_and_updates_only_active_photo_worker(release):
    result, calls, actual = run_release(release, web=False, worker=True)
    assert result.returncode == 0, result.stderr
    assert actual["image"] == WORKER_IMAGE
    assert calls.index("pull photo-worker") < calls.index(
        "up -d --no-deps --pull never --timeout 180 photo-worker"
    )
    assert (
        "manage.py" not in calls and "import-worker" not in calls and "commerce-worker" not in calls
    )
    assert "PHOTO_WORKER_VERIFY=ok" in result.stdout
    assert calls.splitlines()[-1] == "image prune -a -f"
    assert result.stdout.index("PHOTO_WORKER_VERIFY=ok") < result.stdout.index(
        "DEPLOY_STAGE=image-prune"
    )


@pytest.mark.parametrize(("web", "worker"), [(True, False), (False, True)])
def test_recovery_release_does_not_activate_inactive_photo_profile(release, web, worker):
    result, calls, actual = run_release(release, web=web, worker=worker, active=False)
    assert result.returncode == 0, result.stderr
    assert actual["status"] == "exited"
    assert "pull photo-worker" not in calls
    assert not any(" up " in call and call.endswith(" photo-worker") for call in calls.splitlines())
    assert "PHOTO_WORKER_VERIFY=" not in result.stdout


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
    ("corruption", "status"),
    [
        (
            {
                "environment": [
                    "PHOTO_WORKER_PROCESSOR_IDENTITIES=" + IDENTITIES,
                    "PHOTO_WORKER_PROCESSOR_TYPES=" + PROCESSOR_TYPES,
                    "PHOTO_WORKER_TOKEN=" + RUNNING_TOKEN,
                ]
            },
            "environment-mismatch",
        ),
        ({"status": "exited"}, "not-running"),
        ({"image": "ghcr.io/example/wrong-reference"}, "image-mismatch"),
    ],
)
def test_release_fails_with_a_sanitized_status_when_worker_configuration_disagrees(
    release, corruption, status
):
    result, calls, _ = run_release(release, corruption=corruption)
    assert result.returncode != 0
    assert "image prune" not in calls
    output = result.stdout + result.stderr
    assert f"PHOTO_WORKER_VERIFY={status}" in result.stdout
    assert DESIRED_TOKEN not in output
    assert RUNNING_TOKEN not in output
    assert "ghcr.io/example/wrong-reference" not in output

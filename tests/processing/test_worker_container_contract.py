"""Repository contract for the separately packaged local worker."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory

import yaml
from django.test import Client, override_settings

ROOT = Path(__file__).resolve().parents[2]
OPENCV_ZOO_REVISION = "47534e27c9851bb1128ccc0102f1145e27f23f98"
FACE_MODEL_ARTIFACTS = (
    (
        "PHOTO_WORKER_YUNET_MODEL_PATH",
        "face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
        "models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    ),
    (
        "PHOTO_WORKER_SFACE_MODEL_PATH",
        "face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
        "models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    ),
)
FORBIDDEN_SETTINGS = {
    "DB_NAME",
    "DB_USER",
    "DB_PASSWORD",
    "DB_HOST",
    "DB_PORT",
    "SECRET_KEY",
    "MEDIA_S3_ACCESS_KEY_ID",
    "MEDIA_S3_SECRET_ACCESS_KEY",
    "PRIVATE_MEDIA_S3_ACCESS_KEY_ID",
    "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY",
}


def test_worker_container_is_minimal_and_starts_the_standalone_package() -> None:
    """A future image change must not accidentally package Django with the worker."""
    dockerfile = (ROOT / "Dockerfile.worker").read_text(encoding="utf-8")

    assert "COPY src/worker/requirements.txt" in dockerfile
    assert "COPY src/worker/photo_worker" in dockerfile
    assert 'CMD ["python", "-m", "photo_worker"]' in dockerfile
    assert "USER worker" in dockerfile
    assert "src/backend" not in dockerfile
    assert "manage.py" not in dockerfile
    assert "django" not in dockerfile.lower()


def test_worker_image_pins_shared_face_models_and_smokes_both_inference_paths() -> None:
    """Both photo and selfie inference must run from the same immutable worker image."""
    dockerfile = (ROOT / "Dockerfile.worker").read_text(encoding="utf-8")
    failures: list[str] = []

    for environment_name, filename, checksum, source_path in FACE_MODEL_ARTIFACTS:
        destination = f"/worker/models/{filename}"
        source = f"https://github.com/opencv/opencv_zoo/raw/{OPENCV_ZOO_REVISION}/{source_path}"
        instruction = f"ADD --checksum=sha256:{checksum} {source} {destination}"
        if instruction not in dockerfile:
            failures.append(f"missing immutable {filename} artifact")
        if f"{environment_name}={destination}" not in dockerfile:
            failures.append(f"missing {environment_name} container path")

    smoke_command = "RUN python -m photo_worker.model_smoke"
    if smoke_command not in dockerfile:
        failures.append("missing build-time face model smoke")
    else:
        if "RUN chown -R worker:worker /worker" not in dockerfile:
            failures.append("model artifacts are not readable by the worker user")
        if dockerfile.index("USER worker") > dockerfile.index(smoke_command):
            failures.append("face model smoke does not run as the worker user")

    smoke_path = ROOT / "src/worker/photo_worker/model_smoke.py"
    if not smoke_path.is_file():
        failures.append("missing shared face model smoke module")
    else:
        tree = ast.parse(smoke_path.read_text(encoding="utf-8"))
        called_names = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        missing_paths = {"extract_face_embeddings", "extract_selfie_embedding"} - called_names
        if missing_paths:
            failures.append(f"smoke misses shared inference path(s): {sorted(missing_paths)}")
        if "MAX_FACE_EMBEDDING_DIMENSIONS" not in smoke_path.read_text(encoding="utf-8"):
            failures.append("smoke does not validate the SFace feature dimension")

    assert failures == [], "\n".join(failures)


def test_worker_compose_profile_is_opt_in_and_receives_only_its_narrow_contract() -> None:
    """A compose edit must not hand the worker application or permanent-storage credentials."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]
    environment = worker["environment"]

    assert worker["profiles"] == ["worker"]
    assert worker["build"]["dockerfile"] == "Dockerfile.worker"
    assert worker.get("env_file") is None
    assert set(environment) == {
        "PHOTO_WORKER_API_URL",
        "PHOTO_WORKER_TOKEN",
        "PHOTO_WORKER_BUILD",
        "PHOTO_WORKER_LEASE_SECONDS",
        "PHOTO_WORKER_PROCESSOR_IDENTITIES",
        "PHOTO_WORKER_PROCESSOR_TYPES",
    }
    assert environment["PHOTO_WORKER_API_URL"].endswith("/internal/photo-processing/v1")
    assert "PHOTO_PROCESSING_WORKER_TOKEN" in environment["PHOTO_WORKER_TOKEN"]
    assert environment["PHOTO_WORKER_PROCESSOR_IDENTITIES"] == (
        "${PHOTO_WORKER_PROCESSOR_IDENTITIES:-1/capture_metadata/1,1/face_embedding/1,"
        "2/generate_preview/1,2/face_embedding/2}"
    )
    assert environment["PHOTO_WORKER_PROCESSOR_TYPES"] == (
        "${PHOTO_WORKER_PROCESSOR_TYPES:-selfie_query,face_embedding,capture_metadata,"
        "generate_preview}"
    )
    assert not (FORBIDDEN_SETTINGS & set(environment))
    assert "PHOTO_WORKER_CONCURRENCY" not in environment


def test_production_worker_profile_is_bounded_and_isolated_from_web_configuration() -> None:
    """The deployed worker has only its private API contract and declared resource bounds."""
    compose = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]

    assert worker["image"] == "${WORKER_IMAGE:-}"
    assert worker["profiles"] == ["worker"]
    assert worker.get("ports") is None
    assert worker.get("env_file") is None
    assert worker["depends_on"] == {"web": {"condition": "service_healthy"}}
    assert worker["restart"] == "unless-stopped"
    assert worker["cpus"] == "1.0"
    assert worker["mem_limit"] == "2g"
    assert worker["pids_limit"] == 64
    assert worker["environment"] == {
        "DEPLOYMENT_TARGET": "${DEPLOYMENT_TARGET:?DEPLOYMENT_TARGET must be set}",
        "PHOTO_WORKER_API_URL": "http://web:8000/internal/photo-processing/v1",
        "PHOTO_WORKER_TOKEN": "${PHOTO_PROCESSING_WORKER_TOKEN:-}",
        "PHOTO_WORKER_BUILD": "${PHOTO_WORKER_BUILD:-capture-metadata-v1}",
        "PHOTO_WORKER_LEASE_SECONDS": "${PHOTO_WORKER_LEASE_SECONDS:-120}",
        "PHOTO_WORKER_PROCESSOR_IDENTITIES": (
            "${PHOTO_WORKER_PROCESSOR_IDENTITIES:-1/capture_metadata/1,1/face_embedding/1,"
            "2/generate_preview/1,2/face_embedding/2}"
        ),
        "PHOTO_WORKER_PROCESSOR_TYPES": (
            "${PHOTO_WORKER_PROCESSOR_TYPES:-selfie_query,face_embedding,capture_metadata,"
            "generate_preview}"
        ),
    }
    assert not (FORBIDDEN_SETTINGS & set(worker["environment"]))


def test_default_compose_config_interpolates_example_without_enabling_the_worker() -> None:
    """An inactive profile must not make the normal local Compose configuration unusable."""
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        _copy_compose_inputs(directory)
        result = _compose_config(directory, "--env-file", ".env.example")

        assert result.returncode == 0, result.stderr
        compose = yaml.safe_load(result.stdout)
        assert "worker" not in compose["services"]

        worker_profile = _compose_config(
            directory, "--env-file", ".env.example", "--profile", "worker"
        )
        assert worker_profile.returncode == 0, worker_profile.stderr
        worker = yaml.safe_load(worker_profile.stdout)["services"]["worker"]
        assert worker["environment"]["PHOTO_WORKER_TOKEN"] == ""
        assert not (FORBIDDEN_SETTINGS & set(worker["environment"]))


def test_copied_and_edited_dotenv_remains_the_web_service_environment() -> None:
    """The copyable template must not redirect web away from a developer's edited `.env`."""
    with TemporaryDirectory() as temporary:
        directory = Path(temporary)
        _copy_compose_inputs(directory)
        dotenv = directory / ".env"
        dotenv.write_text(
            (directory / ".env.example")
            .read_text(encoding="utf-8")
            .replace("SECRET_KEY=change-me", "SECRET_KEY=edited-local-secret"),
            encoding="utf-8",
        )

        result = _compose_config(directory)

    assert result.returncode == 0, result.stderr
    assert yaml.safe_load(result.stdout)["services"]["web"]["environment"]["SECRET_KEY"] == (
        "edited-local-secret"
    )


def test_worker_compose_hostname_is_accepted_by_django_tracked_defaults() -> None:
    """The private Compose hostname must pass Django's host validation before API auth runs."""
    values = _dotenv_values(ROOT / ".env.example")
    allowed_hosts = values["ALLOWED_HOSTS"].split(",")

    assert "web" in allowed_hosts
    with override_settings(ALLOWED_HOSTS=allowed_hosts):
        response = Client().get("/health/", HTTP_HOST="web")
    assert response.status_code == 200


def _copy_compose_inputs(destination: Path) -> None:
    for name in ("docker-compose.yml", ".env.example"):
        copy2(ROOT / name, destination / name)


def _compose_config(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "compose", *arguments, "config"],
        cwd=directory,
        check=False,
        capture_output=True,
        text=True,
    )


def _dotenv_values(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", maxsplit=1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )

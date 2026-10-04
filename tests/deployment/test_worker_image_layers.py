"""Exercise the code-image Dockerfile against a tiny reusable base, without model downloads."""

import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.operational
ROOT = Path(__file__).resolve().parents[2]


def test_pinned_downloaded_model_is_readable_as_base_worker_user(tmp_path):
    source = (ROOT / "Dockerfile.worker-base").read_text()
    artifact = next(
        line
        for line in source.splitlines()
        if line.startswith("ADD ") and line.endswith("/model.py")
    )
    # Exercise the real pinned remote artifact and the base's actual final ownership
    # instructions, without downloading multi-GB models or compiling dependencies.
    ownership = source[source.index("RUN useradd --create-home") :]
    dockerfile = (
        "FROM ubuntu:24.04\n"
        + artifact
        + "\n"
        + ownership
        + '\nRUN test "$(id -u)" = 10001 '
        + "&& test -r /worker/models/adaface-ir18-webface4m/models/iresnet/model.py "
        + "&& head -c 1 /worker/models/adaface-ir18-webface4m/models/iresnet/model.py >/dev/null\n"
    )
    result = subprocess.run(
        ["docker", "build", "--quiet", "-"],
        input=dockerfile,
        capture_output=True,
        text=True,
        timeout=120,
        env=os.environ | {"BUILDX_CONFIG": str(tmp_path / "buildx")},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    subprocess.run(
        ["docker", "image", "rm", result.stdout.strip()],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )


def test_code_only_worker_builds_inherit_identical_base_layers(tmp_path):
    suffix = uuid4().hex
    base_tag = f"findme-layer-test-base:{suffix}"
    tags = [base_tag, f"findme-layer-test-code-a:{suffix}", f"findme-layer-test-code-b:{suffix}"]

    def docker(*args):
        result = subprocess.run(
            ["docker", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
            env=os.environ | {"BUILDX_CONFIG": str(tmp_path / "buildx")},
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    # Only test the image-layer boundary here. The published base's real model/runtime
    # contract is separately exercised by the offline smoke commands in the worker build.
    base = tmp_path / "base"
    base.mkdir()
    (base / "Dockerfile").write_text(
        "FROM nginx:1.27-alpine\n"
        "RUN adduser -D -u 10001 worker && mkdir -p /worker/models\n"
        "COPY model /worker/models/model\n"
        "COPY --chmod=755 python /usr/local/bin/python\nUSER worker\n"
    )
    (base / "model").write_bytes(b"pinned-test-model" * 16384)
    (base / "python").write_text("#!/bin/sh\nexit 0\n")
    code = tmp_path / "code"
    (code / "src/worker/photo_worker").mkdir(parents=True)
    (code / "Dockerfile").write_text((ROOT / "Dockerfile.worker").read_text())
    try:
        docker("build", "--quiet", "-t", base_tag, str(base))
        layers = json.loads(
            docker("image", "inspect", "--format", "{{json .RootFS.Layers}}", base_tag)
        )
        builds = []
        for index, tag in enumerate(tags[1:]):
            (code / "src/worker/photo_worker/code.py").write_text(f"revision = {index}\n")
            docker(
                "build",
                "--quiet",
                "--build-arg",
                f"WORKER_BASE_IMAGE={base_tag}",
                "--build-arg",
                "RELEASE_SHA=" + ("a" if index == 0 else "b") * 40,
                "-t",
                tag,
                str(code),
            )
            builds.append(
                json.loads(docker("image", "inspect", "--format", "{{json .RootFS.Layers}}", tag))
            )
        assert builds[0][: len(layers)] == layers == builds[1][: len(layers)]
        assert builds[0] != builds[1]
        assert len(builds[0]) == len(builds[1])
        assert sum(left != right for left, right in zip(*builds, strict=True)) == 1
    finally:
        subprocess.run(
            ["docker", "image", "rm", *tags],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )

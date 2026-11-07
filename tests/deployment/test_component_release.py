import importlib.util
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / f"{name}.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.mark.parametrize(
    ("path", "images", "services"),
    [
        ("docs/runbooks/deployment.md", (), ()),
        ("src/backend/processing/views.py", ("web",), ("web",)),
        ("src/backend/picflow/templates/picflow/gallery.html", ("web",), ("web",)),
        ("src/backend/picflow/static/ui/gallery.css", ("web",), ("web",)),
        ("src/backend/commerce/templates/commerce/order.html", ("web",), ("web",)),
        ("src/import_worker/import_worker/runtime.py", ("import",), ("import",)),
        ("src/backend/commerce/runtime.py", ("web",), ("web", "commerce")),
        ("src/worker/photo_worker/client.py", ("worker",), ("worker",)),
    ],
)
def test_independent_image_and_running_service_selection(path, images, services):
    selected = module("classify-release").classify([path])
    assert (
        tuple(name for name in ("web", "import", "worker") if selected[f"{name}_changed"]) == images
    )
    assert (
        tuple(
            name for name in ("web", "import", "commerce", "worker") if selected[f"{name}_changed"]
        )
        == services
    )


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["docs/runbooks/deployment.md", "README.md"], (False, False, False)),
        (["src/backend/processing/views.py"], (True, False, False)),
        (["src/import_worker/import_worker/runtime.py"], (False, False, False)),
        (["src/worker/photo_worker/client.py"], (False, True, False)),
        (["src/worker/requirements.cpu.txt"], (False, True, True)),
        (["Dockerfile.worker-base"], (False, True, True)),
        (["Dockerfile.worker"], (False, True, False)),
        ([".dockerignore"], (True, True, True)),
        (
            ["src/backend/processing/views.py", "src/worker/photo_worker/client.py"],
            (True, True, False),
        ),
        (["deploy/apply-deployment.sh"], (True, False, False)),
        ([".github/workflows/deploy.yml"], (False, False, False)),
        (["tests/deployment/test_component_release.py"], (False, False, False)),
        (["scripts/run-with-environment-secrets.py"], (True, False, False)),
        (["src/worker/tests/test_runtime.py"], (False, False, False)),
    ],
)
def test_only_effective_component_inputs_are_published(paths, expected):
    selected = module("classify-release").classify(paths)
    assert (
        selected["web_changed"],
        selected["worker_changed"],
        selected["worker_base_changed"],
    ) == expected


def test_failed_pointer_advance_restores_known_previous_target(tmp_path):
    docker = tmp_path / "docker"
    docker.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
        'case "$*" in *new-digest*) exit 1 ;; esac\n'
    )
    docker.chmod(0o755)
    result = subprocess.run(
        [
            "sh",
            ROOT / "deploy/image-pointer.sh",
            "advance",
            "ghcr.io/example/app:latest",
            "new-digest",
            "old-digest",
        ],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "DOCKER_LOG": str(tmp_path / "calls"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert (tmp_path / "calls").read_text().splitlines() == [
        "buildx imagetools create --tag ghcr.io/example/app:latest new-digest",
        "buildx imagetools create --tag ghcr.io/example/app:latest old-digest",
    ]


def test_native_collector_helper_installs_verified_owned_source_and_can_remove(tmp_path):
    package = tmp_path / "package"
    candidate = tmp_path / "candidate"
    package.mkdir()
    candidate.mkdir()
    for name in ("metrics.py", "metrics.service", "metrics.timer"):
        shutil.copy2(ROOT / "deploy/worker-pools" / name, package / name)
        shutil.copy2(package / name, candidate / name)
    binary = tmp_path / "bin"
    binary.mkdir()
    systemctl = binary / "systemctl"
    systemctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FINDME_SYSTEMCTL_LOG"\n')
    systemctl.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{binary}:{os.environ['PATH']}",
        "FINDME_METRICS_TEST_ROOT": str(tmp_path),
        "FINDME_SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
    }
    helper = ROOT / "deploy/worker-pools/metrics-root-helper.sh"
    result = subprocess.run(["sh", helper, "install"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "runtime/metrics.py").read_bytes() == (package / "metrics.py").read_bytes()
    assert (tmp_path / "config/metrics.json").is_file()
    assert (
        "enable --now findme-worker-pool-metrics.timer" in (tmp_path / "systemctl.log").read_text()
    )
    (candidate / "metrics.py").write_text("unreviewed source")
    result = subprocess.run(["sh", helper, "install"], env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert (tmp_path / "runtime/metrics.py").read_bytes() == (package / "metrics.py").read_bytes()
    result = subprocess.run(["sh", helper, "remove"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "runtime/metrics.py").exists()

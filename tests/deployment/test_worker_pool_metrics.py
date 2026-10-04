import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_metrics():
    path = ROOT / "deploy/worker-pools/metrics.py"
    spec = importlib.util.spec_from_file_location("worker_pool_metrics", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("selected", ["web", "web-next"])
def test_worker_pool_metrics_target_the_selected_django_slot(tmp_path, selected):
    metrics = _load_metrics()
    root = tmp_path / "deployment"
    (root / "deploy").mkdir(parents=True)
    cloud_path = root / "worker-pools-observation.json"
    cloud_path.write_text(
        json.dumps(
            {
                "folder_id": "worker-folder",
                "canonical_folder_id": "canonical-folder",
                "zone": "ru-central1-a",
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if any(argument.endswith("web-slot.py") for argument in command):
            return subprocess.CompletedProcess(command, 0, selected + "\n")
        return subprocess.CompletedProcess(command, 0)

    metrics.collect({"deploy_root": str(root), "cloud": str(cloud_path)}, run=run)

    assert calls[0][0][1:] == [
        str(root / "deploy/web-slot.py"),
        "--root",
        str(root),
        "selected",
    ]
    assert calls[1][0][calls[1][0].index("exec") + 2] == selected
    assert calls[2][0][calls[2][0].index("exec") + 2] == selected


def test_worker_pool_metrics_fail_closed_when_selected_slot_is_unavailable(tmp_path):
    metrics = _load_metrics()
    root = tmp_path / "deployment"
    (root / "deploy").mkdir(parents=True)
    cloud_path = root / "worker-pools-observation.json"
    cloud_path.write_text(
        json.dumps(
            {
                "folder_id": "worker-folder",
                "canonical_folder_id": "canonical-folder",
                "zone": "ru-central1-a",
            }
        ),
        encoding="utf-8",
    )

    def run(command, **kwargs):
        if any(argument.endswith("web-slot.py") for argument in command):
            raise subprocess.CalledProcessError(1, command)
        pytest.fail("Django command must not run without a selected slot")

    with pytest.raises(subprocess.CalledProcessError):
        metrics.collect({"deploy_root": str(root), "cloud": str(cloud_path)}, run=run)

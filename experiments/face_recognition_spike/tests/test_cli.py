from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from face_spike import cli


def valid_run_arguments(output: Path) -> list[str]:
    return [
        "run",
        "--photos",
        "/input/photos",
        "--labels",
        "/input/labels.csv",
        "--yunet-model",
        "/models/yunet.onnx",
        "--sface-model",
        "/models/sface.onnx",
        "--output",
        str(output),
    ]


@pytest.mark.parametrize("argv", [[], ["unknown"]])
def test_main_returns_invalid_invocation_exit_code(argv: Sequence[str]) -> None:
    assert cli.main(argv) == 2


@pytest.mark.parametrize(
    "missing_argument",
    ["--photos", "--labels", "--yunet-model", "--sface-model", "--output"],
)
def test_run_requires_each_required_argument(tmp_path: Path, missing_argument: str) -> None:
    arguments = valid_run_arguments(tmp_path / "run")
    argument_index = arguments.index(missing_argument)
    del arguments[argument_index : argument_index + 2]

    assert cli.main(arguments) == 2


def test_existing_output_is_rejected_before_experiment_initialization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "existing-run"
    output.mkdir()

    def initialization_must_not_run(config: object) -> object:
        pytest.fail(f"run_experiment was called with {config!r}")

    monkeypatch.setattr(cli, "run_experiment", initialization_must_not_run)

    assert cli.main(valid_run_arguments(output)) == 2


def test_run_uses_documented_default_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    received_config: list[object] = []

    def record_config(config: object) -> SimpleNamespace:
        received_config.append(config)
        return SimpleNamespace(unexpected_labelled_image_failures=0)

    monkeypatch.setattr(cli, "run_experiment", record_config)

    assert cli.main(valid_run_arguments(tmp_path / "new-run")) == 0

    config = received_config[0]
    assert config.detection_threshold == 0.75
    assert config.min_face_px == 32
    assert config.top_k == 10
    assert config.max_image_dimension == 12000
    assert config.max_image_pixels == 100000000


def test_run_returns_three_for_unexpected_labelled_image_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = SimpleNamespace(unexpected_labelled_image_failures=1)
    monkeypatch.setattr(cli, "run_experiment", lambda config: result)

    assert cli.main(valid_run_arguments(tmp_path / "new-run")) == 3


def test_run_returns_two_when_orchestration_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def raise_unavailable(config: object) -> object:
        raise cli.ExperimentUnavailableError

    monkeypatch.setattr(cli, "run_experiment", raise_unavailable)

    assert cli.main(valid_run_arguments(tmp_path / "new-run")) == 2

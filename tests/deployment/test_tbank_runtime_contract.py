"""Dark T-Bank deployment wiring and merchant configuration boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.deployment.test_deployment_scripts import _apply_env, _run

ROOT = Path(__file__).resolve().parents[2]
FIELDS = (
    "TERMINAL_KEY",
    "TERMINAL_PASSWORD",
    "API_ORIGIN",
    "RUB_ONLY",
    "PAY_TYPE",
    "RECEIPT_FFD",
    "RECEIPT_TAXATION",
    "RECEIPT_TAX",
    "RECEIPT_PAYMENT_METHOD",
    "RECEIPT_PAYMENT_OBJECT",
    "RECEIPT_MEASUREMENT_UNIT",
    "RECEIPT_CLOSING_REQUIRED",
)
VALUES = {
    "TBANK_TERMINAL_KEY": "test-terminal",
    "TBANK_TERMINAL_PASSWORD": "test-password-private-sentinel",
    "TBANK_API_ORIGIN": "https://rest-api-test.tinkoff.ru",
    "TBANK_RUB_ONLY": "True",
    "TBANK_PAY_TYPE": "O",
    "TBANK_RECEIPT_FFD": "1.2",
    "TBANK_RECEIPT_TAXATION": "osn",
    "TBANK_RECEIPT_TAX": "none",
    "TBANK_RECEIPT_PAYMENT_METHOD": "full_payment",
    "TBANK_RECEIPT_PAYMENT_OBJECT": "service",
    "TBANK_RECEIPT_MEASUREMENT_UNIT": "шт",
    "TBANK_RECEIPT_CLOSING_REQUIRED": "False",
}


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def test_compose_projects_identical_bank_settings_to_web_and_commerce_worker() -> None:
    for filename in ("docker-compose.yml", "docker-compose.deployment.yml"):
        services = yaml.safe_load((ROOT / filename).read_text())["services"]
        worker = services["commerce-worker"]["environment"]
        if filename == "docker-compose.yml":
            assert services["web"]["env_file"][0]["path"] == ".env"
            web = worker  # Both local processes read the same .env values.
        else:
            web = services["web"]["environment"]
        for suffix in FIELDS:
            name = f"TBANK_{suffix}"
            assert web[name] == worker[name] == f"${{{name}:-}}"


def test_optional_bank_credentials_use_canonical_deploy_secret_projection() -> None:
    manifest = json.loads((ROOT / "deploy/environment-secrets.json").read_text())
    entries = {entry["key"]: entry for entry in manifest["entries"]}
    for name in ("TBANK_TERMINAL_KEY", "TBANK_TERMINAL_PASSWORD"):
        assert entries[name] == {
            "key": name,
            "target": name,
            "type": "text",
            "required": False,
            "local": False,
        }
        assert name in manifest["consumers"]["deploy"]
        assert name not in manifest["consumers"]["local-web"]


@pytest.mark.parametrize("missing", VALUES)
def test_selected_bank_requires_every_merchant_value_before_deployment_mutation(
    tmp_path: Path, fake_bin: Path, missing: str
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(VALUES)
    env.update(
        {
            "COMMERCE_PUBLIC_ORIGIN": "https://findme-photo.ru",
            "COMMERCE_PAYMENT_GATEWAY_FACTORY": "commerce.tbank_gateway.tbank_gateway_factory",
        }
    )
    env[missing] = ""
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 2
    assert missing in result.stderr
    assert not (tmp_path / "apply.log").exists()


def test_selected_bank_settings_persist_without_printing_credentials(
    tmp_path: Path, fake_bin: Path
) -> None:
    env = _apply_env(tmp_path, fake_bin, scenario="private-media-no-photo")
    env.update(VALUES)
    env.update(
        {
            "COMMERCE_PUBLIC_ORIGIN": "https://findme-photo.ru",
            "COMMERCE_PAYMENT_GATEWAY_FACTORY": "commerce.tbank_gateway.tbank_gateway_factory",
        }
    )
    result = _run("deploy/apply-deployment.sh", env=env)
    assert result.returncode == 0, result.stderr
    persisted = (tmp_path / ".env").read_text()
    for name, value in VALUES.items():
        if name in {"TBANK_TERMINAL_KEY", "TBANK_TERMINAL_PASSWORD"}:
            assert f'{name}="{value}"' in persisted
        else:
            assert f"{name}={value}" in persisted
    assert VALUES["TBANK_TERMINAL_PASSWORD"] not in result.stdout + result.stderr
    assert VALUES["TBANK_TERMINAL_KEY"] not in result.stdout + result.stderr


def test_workflow_preserves_simulator_default_and_blank_merchant_values() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/deploy.yml").read_text())
    steps = workflow["jobs"]["deploy"]["steps"]
    deploy = next(step for step in steps if step.get("name") == "Run deployment")
    assert (
        "commerce.payment_simulator.payment_simulator_gateway_factory"
        in deploy["env"]["COMMERCE_PAYMENT_GATEWAY_FACTORY"]
    )
    for suffix in FIELDS:
        name = f"TBANK_{suffix}"
        if name not in {"TBANK_TERMINAL_KEY", "TBANK_TERMINAL_PASSWORD"}:
            assert deploy["env"][name] == f"${{{{ vars.{name} || '' }}}}"

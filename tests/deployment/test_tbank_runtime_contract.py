"""Merchant settings remain complete and private in the current one-VM runtime."""

from pathlib import Path

import pytest
import yaml

from tests.deployment.test_import_deployment import run_import_release

ROOT = Path(__file__).resolve().parents[2]
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


def bank_environment(values: dict[str, str]) -> str:
    settings = {
        "COMMERCE_PAYMENT_GATEWAY_FACTORY": "commerce.tbank_gateway.tbank_gateway_factory",
        "COMMERCE_PUBLIC_ORIGIN": "https://findme-photo.ru",
        **values,
    }
    return "".join(f"{name}={value}\n" for name, value in settings.items())


def test_compose_projects_same_bank_settings_to_web_and_commerce_worker() -> None:
    services = yaml.safe_load((ROOT / "docker-compose.deployment.yml").read_text())["services"]
    web = services["web"]["environment"]
    worker = services["commerce-worker"]["environment"]
    for name in VALUES:
        assert web[name] == worker[name] == f"${{{name}:-}}"
    assert "TBANK_TERMINAL_PASSWORD" not in services["photo-worker"]["environment"]


@pytest.mark.parametrize("missing", VALUES)
def test_selected_bank_rejects_each_missing_merchant_value_before_pull(
    tmp_path: Path, missing: str
) -> None:
    values = {**VALUES, missing: ""}
    original = bank_environment(values)
    result, commands, env_file = run_import_release(tmp_path, original)
    assert result.returncode == 2
    assert missing in result.stderr
    assert "pull import-worker" not in commands
    assert env_file.read_text() == original


def test_bank_credentials_survive_independent_import_release_without_logging(
    tmp_path: Path,
) -> None:
    result, commands, env_file = run_import_release(tmp_path, bank_environment(VALUES))
    assert result.returncode == 0, result.stderr
    assert "pull import-worker" in commands
    persisted = env_file.read_text()
    for name, value in VALUES.items():
        assert f"{name}={value}\n" in persisted
    for secret in (VALUES["TBANK_TERMINAL_KEY"], VALUES["TBANK_TERMINAL_PASSWORD"]):
        assert secret not in result.stdout + result.stderr
    assert env_file.stat().st_mode & 0o777 == 0o600

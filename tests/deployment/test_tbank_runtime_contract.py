"""Dark T-Bank deployment wiring and merchant configuration boundary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def test_compose_delivers_bank_values_to_both_processes(tmp_path) -> None:
    from tests.deployment.compose_config import render_compose

    for filename in ("docker-compose.yml", "docker-compose.deployment.yml"):
        services = render_compose(tmp_path, filename, profiles=("commerce",), environment=VALUES)[
            "services"
        ]
        for name, value in VALUES.items():
            assert services["web"]["environment"][name] == value
            assert services["commerce-worker"]["environment"][name] == value


def test_bank_credentials_are_optional_and_not_exposed_to_local_web() -> None:
    manifest = json.loads((ROOT / "deploy/environment-secrets.json").read_text())
    entries = {entry["key"]: entry for entry in manifest["entries"]}
    for name in ("TBANK_TERMINAL_KEY", "TBANK_TERMINAL_PASSWORD"):
        assert entries[name]["required"] is False
        assert name in manifest["consumers"]["deploy"]
        assert name not in manifest["consumers"]["local-web"]

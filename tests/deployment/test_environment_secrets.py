from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPOSITORY_ROOT / "deploy/environment-secrets.json"
RESOLVER_PATH = REPOSITORY_ROOT / "scripts/run-with-environment-secrets.py"
VERIFIER_PATH = REPOSITORY_ROOT / "scripts/verify-environment-secret-projection.py"

EXPECTED_SECRET_KEYS = {
    "SECRET_KEY",
    "DB_PASSWORD",
    "LETSENCRYPT_EMAIL",
    "COMMERCE_ORDER_ACCESS_SIGNING_SECRET",
    "COMMERCE_POSTBOX_API_KEY_ID",
    "COMMERCE_POSTBOX_API_KEY_SECRET",
    "MEDIA_S3_ACCESS_KEY_ID",
    "MEDIA_S3_SECRET_ACCESS_KEY",
    "PRIVATE_MEDIA_S3_ACCESS_KEY_ID",
    "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY",
    "PHOTO_PROCESSING_WORKER_TOKEN",
    "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID",
    "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY",
    "VM_SSH_KEY",
    "GHCR_READ_TOKEN",
    "YANDEX_MONITORING_API_KEY",
}
OPTIONAL_DARK_COMMERCE_SECRET_KEYS = {
    "COMMERCE_ORDER_ACCESS_SIGNING_SECRET",
    "COMMERCE_POSTBOX_API_KEY_ID",
    "COMMERCE_POSTBOX_API_KEY_SECRET",
}
REQUIRED_SECRET_KEYS = EXPECTED_SECRET_KEYS - OPTIONAL_DARK_COMMERCE_SECRET_KEYS
LOCAL_WEB_KEYS = {
    "SECRET_KEY",
    "MEDIA_S3_ACCESS_KEY_ID",
    "MEDIA_S3_SECRET_ACCESS_KEY",
    "PRIVATE_MEDIA_S3_ACCESS_KEY_ID",
    "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY",
    "PHOTO_PROCESSING_WORKER_TOKEN",
    "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID",
    "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY",
}
DEPLOY_KEYS = EXPECTED_SECRET_KEYS - {"YANDEX_MONITORING_API_KEY"}


@pytest.fixture(scope="module")
def resolver() -> ModuleType:
    assert RESOLVER_PATH.is_file(), "shared environment resolver is missing"
    spec = importlib.util.spec_from_file_location("findme_environment_secrets", RESOLVER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    assert MANIFEST_PATH.is_file(), "deployment environment manifest is missing"
    return json.loads(MANIFEST_PATH.read_text())


def _sentinel_values(manifest: dict[str, Any]) -> dict[str, str | bytes]:
    marker = uuid.uuid4().hex
    values: dict[str, str | bytes] = {}
    for entry in manifest["entries"]:
        key = entry["key"]
        if entry["type"] == "binary":
            values[key] = f"binary-{marker}-\x00\xff".encode()
        else:
            values[key] = f"text-{marker}-{key}"
    return values


def _payload(values: dict[str, str | bytes], *, version: str = "version-exact") -> dict[str, Any]:
    entries = []
    for key, value in values.items():
        if isinstance(value, bytes):
            entries.append({"key": key, "binaryValue": base64.b64encode(value).decode()})
        else:
            entries.append({"key": key, "textValue": value})
    return {"versionId": version, "entries": entries}


def _metadata(
    *,
    version: str | None = "version-exact",
    payload_entry_keys: set[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": "e6q85jjl76r45maigtfb",
        "folderId": "b1g2qttgfhb4gdunvlge",
        "status": "ACTIVE",
    }
    if version is not None:
        result["currentVersion"] = {
            "id": version,
            "secretId": "e6q85jjl76r45maigtfb",
            "status": "ACTIVE",
            "payloadEntryKeys": sorted(
                EXPECTED_SECRET_KEYS if payload_entry_keys is None else payload_entry_keys
            ),
        }
    result.update(overrides)
    return result


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _HttpBoundary:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[Any] = []

    def __call__(self, request: Any, *, timeout: float) -> _Response:
        assert timeout > 0
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return _Response(self.responses.pop(0))


def _fake_yc(tmp_path: Path, *, token: str = "iam-test-token", exit_code: int = 0) -> Path:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    executable = binary_dir / "yc"
    executable.write_text(
        "#!/bin/sh\n"
        '[ "$1 $2" = "iam create-token" ] || exit 93\n'
        f"printf '%s\\n' {token!r}\n"
        f"exit {exit_code}\n"
    )
    executable.chmod(0o700)
    return binary_dir


def _run_main(
    resolver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    http: _HttpBoundary,
    *,
    consumer: str = "local-web",
    command: list[str] | None = None,
) -> int:
    binary_dir = _fake_yc(tmp_path)
    monkeypatch.setenv("PATH", f"{binary_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(resolver, "urlopen", http)
    return resolver.main(
        [
            "--consumer",
            consumer,
            "--identity",
            "yc",
            "--",
            *(command or [sys.executable, "-c", "pass"]),
        ]
    )


def test_manifest_pins_the_reviewed_deployment_identity(manifest: dict[str, Any]) -> None:
    assert manifest["lockbox"] == {
        "secret_id": "e6q85jjl76r45maigtfb",
        "folder_id": "b1g2qttgfhb4gdunvlge",
    }
    assert manifest["github_oidc"] == {
        "issuer": "https://token.actions.githubusercontent.com",
        "audience": "https://github.com/peter-nikitin",
        "subject": "repo:peter-nikitin/photo-prjct:ref:refs/heads/main",
        "repository": "peter-nikitin/photo-prjct",
        "ref": "refs/heads/main",
        "service_account_id": "ajeaekiue94ogksguh0h",
        "federation_id": "ajeula3gd46omgf9jiko",
        "allowed_workflows": [
            "peter-nikitin/photo-prjct/.github/workflows/deploy.yml@refs/heads/main",
            "peter-nikitin/photo-prjct/.github/workflows/monitor-public-health.yml@refs/heads/main",
            "peter-nikitin/photo-prjct/.github/workflows/face-embedding-benchmark.yml@refs/heads/main",
        ],
    }


def test_manifest_declares_complete_schema_and_closed_projections(
    manifest: dict[str, Any],
) -> None:
    entries = {entry["key"]: entry for entry in manifest["entries"]}
    assert set(entries) == EXPECTED_SECRET_KEYS
    assert {
        key for key, entry in entries.items() if entry["required"] is True
    } == REQUIRED_SECRET_KEYS
    assert entries["VM_SSH_KEY"] == {
        "key": "VM_SSH_KEY",
        "target": "VM_SSH_KEY_FILE",
        "type": "binary",
        "required": True,
        "local": False,
    }
    for key in (
        "COMMERCE_ORDER_ACCESS_SIGNING_SECRET",
        "COMMERCE_POSTBOX_API_KEY_ID",
        "COMMERCE_POSTBOX_API_KEY_SECRET",
    ):
        assert entries[key] == {
            "key": key,
            "target": key,
            "type": "text",
            "required": False,
            "local": False,
        }
    assert {key for key, entry in entries.items() if entry["local"]} == LOCAL_WEB_KEYS
    assert {name: set(keys) for name, keys in manifest["consumers"].items()} == {
        "local-web": LOCAL_WEB_KEYS,
        "deploy": DEPLOY_KEYS,
        "remote-check": {"VM_SSH_KEY"},
        "public-monitor": {"YANDEX_MONITORING_API_KEY"},
    }


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        (
            [
                "--consumer",
                "arbitrary",
                "--identity",
                "yc",
                "--",
                "true",
            ],
            "unknown_consumer",
        ),
    ],
)
def test_unknown_consumer_fails_before_identity(
    resolver: ModuleType,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    reason: str,
) -> None:
    assert resolver.main(arguments) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"stage=manifest status=error code={reason}" in captured.err


def test_yc_identity_failure_is_sanitized_and_stops_before_http(
    resolver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    binary_dir = _fake_yc(tmp_path, token="identity-error-private-detail", exit_code=19)
    monkeypatch.setenv("PATH", f"{binary_dir}{os.pathsep}{os.environ['PATH']}")
    http = _HttpBoundary([])
    monkeypatch.setattr(resolver, "urlopen", http)

    exit_code = resolver.main(["--consumer", "local-web", "--identity", "yc", "--", "true"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert http.requests == []
    assert captured.out == ""
    assert "stage=identity status=error code=identity_failed" in captured.err
    assert "identity-error-private-detail" not in captured.err


@pytest.mark.parametrize(
    "metadata",
    [
        _metadata(id="other-secret"),
        _metadata(folderId="other-folder"),
        _metadata(status="INACTIVE"),
        _metadata(
            currentVersion={
                "id": "version-exact",
                "secretId": "other-secret",
                "status": "ACTIVE",
            }
        ),
    ],
)
def test_metadata_identity_mismatch_fails_before_payload_fetch(
    resolver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    metadata: dict[str, Any],
) -> None:
    http = _HttpBoundary([metadata])
    exit_code = _run_main(resolver, monkeypatch, tmp_path, http)

    assert exit_code == 2
    assert len(http.requests) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "stage=metadata status=error code=metadata_mismatch" in captured.err


@pytest.mark.parametrize(
    "metadata",
    [
        _metadata(version=None),
        _metadata(currentVersion={}),
        _metadata(
            currentVersion={
                "id": "version-exact",
                "secretId": "e6q85jjl76r45maigtfb",
                "status": "DESTROYED",
            }
        ),
    ],
)
def test_missing_active_version_fails_closed(
    resolver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    metadata: dict[str, Any],
) -> None:
    http = _HttpBoundary([metadata])
    exit_code = _run_main(resolver, monkeypatch, tmp_path, http)

    assert exit_code == 2
    assert len(http.requests) == 1
    assert "stage=metadata status=error code=missing_active_version" in capsys.readouterr().err


def test_payload_is_fetched_once_by_the_exact_active_version(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    values = _sentinel_values(manifest)
    http = _HttpBoundary(
        [_metadata(version="version-atomic"), _payload(values, version="version-atomic")]
    )

    assert _run_main(resolver, monkeypatch, tmp_path, http) == 0

    assert len(http.requests) == 2
    metadata_request, payload_request = http.requests
    assert metadata_request.full_url.endswith("/lockbox/v1/secrets/e6q85jjl76r45maigtfb")
    assert payload_request.full_url.endswith(
        "/lockbox/v1/secrets/e6q85jjl76r45maigtfb/payload?versionId=version-atomic"
    )
    assert metadata_request.get_header("Authorization") == "Bearer iam-test-token"
    assert payload_request.get_header("Authorization") == "Bearer iam-test-token"


def test_deploy_consumer_accepts_dark_payload_without_optional_commerce_entries(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    values = {
        key: value
        for key, value in _sentinel_values(manifest).items()
        if key not in OPTIONAL_DARK_COMMERCE_SECRET_KEYS
    }
    result_path = tmp_path / "keys.json"
    checker = textwrap.dedent(
        """
        import json, os, pathlib, sys
        data = pathlib.Path(os.environ["FINDME_ENV_FILE"]).read_text()
        keys = sorted(line.split("=", 1)[0] for line in data.splitlines() if "=" in line)
        pathlib.Path(sys.argv[1]).write_text(json.dumps(keys))
        """
    )
    http = _HttpBoundary(
        [
            _metadata(payload_entry_keys=REQUIRED_SECRET_KEYS),
            _payload(values),
        ]
    )

    assert (
        _run_main(
            resolver,
            monkeypatch,
            tmp_path,
            http,
            consumer="deploy",
            command=[sys.executable, "-c", checker, str(result_path)],
        )
        == 0
    )

    projected_keys = set(json.loads(result_path.read_text()))
    assert OPTIONAL_DARK_COMMERCE_SECRET_KEYS.isdisjoint(projected_keys)
    assert {"SECRET_KEY", "DB_PASSWORD", "VM_SSH_KEY_FILE", "GHCR_READ_TOKEN"} <= projected_keys


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda payload: payload["entries"].pop(), "missing_entry"),
        (lambda payload: payload["entries"].append(dict(payload["entries"][0])), "duplicate_entry"),
        (
            lambda payload: payload["entries"].append({"key": "UNREVIEWED", "textValue": "x"}),
            "unknown_entry",
        ),
        (
            lambda payload: payload["entries"].__setitem__(
                0, {"key": payload["entries"][0]["key"], "textValue": ""}
            ),
            "empty_entry",
        ),
        (
            lambda payload: payload["entries"].__setitem__(
                0,
                {
                    "key": payload["entries"][0]["key"],
                    "binaryValue": base64.b64encode(b"wrong").decode(),
                },
            ),
            "wrong_type",
        ),
        (lambda payload: payload.__setitem__("entries", "not-a-list"), "payload_malformed"),
        (
            lambda payload: payload.__setitem__("versionId", "different-version"),
            "payload_version_mismatch",
        ),
    ],
)
def test_invalid_payload_schema_fails_before_child(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutate: Any,
    reason: str,
) -> None:
    values = _sentinel_values(manifest)
    payload = _payload(values)
    mutate(payload)
    marker = tmp_path / "child-ran"
    http = _HttpBoundary([_metadata(), payload])

    exit_code = _run_main(
        resolver,
        monkeypatch,
        tmp_path,
        http,
        command=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path(__import__('sys').argv[1]).touch()",
            str(marker),
        ],
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert not marker.exists()
    assert captured.out == ""
    assert f"stage=payload status=error code={reason}" in captured.err
    assert not any(str(value) in captured.err for value in values.values())


def test_text_and_binary_values_preserve_boundaries_without_shell_evaluation(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    values = _sentinel_values(manifest)
    values.update(
        {
            "SECRET_KEY": " leading and trailing ",
            "DB_PASSWORD": "ends-with-backslash\\",
            "LETSENCRYPT_EMAIL": 'backslash-before-double-quote:\\"',
            "MEDIA_S3_ACCESS_KEY_ID": r"literal-\n-\t-\\-sequences",
            "MEDIA_S3_SECRET_ACCESS_KEY": "$HOME ${UNSET:-fallback} $$ $5",
            "PRIVATE_MEDIA_S3_ACCESS_KEY_ID": "single' and double\" quotes",
            "PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY": "line one\nline=two",
            "PHOTO_PROCESSING_WORKER_TOKEN": "equals=a=b=c",
            "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID": "# hash ; semicolon & amp | pipe < >",
            "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY": "tabs\tand newlines\n",
            "GHCR_READ_TOKEN": " $(touch nope) `id` * ? [ ] ! ",
            "VM_SSH_KEY": b"\x00binary\n'\"=$HOME;$(touch nope)\xff",
        }
    )
    compose_path = tmp_path / "compose.yml"
    projected_targets = [
        entry["target"]
        for entry in manifest["entries"]
        if entry["key"] in manifest["consumers"]["deploy"]
    ]
    compose_path.write_text(
        "services:\n"
        "  parser:\n"
        "    image: scratch\n"
        "    environment:\n"
        + "".join(
            f"      {target}: ${{{target}}}\n"
            for target in projected_targets
            if target != "VM_SSH_KEY_FILE"
        )
    )
    compose_result_path = tmp_path / "compose-result.json"
    compose_error_path = tmp_path / "compose-error.txt"
    binary_result_path = tmp_path / "binary-result.json"
    checker = textwrap.dedent(
        """
        import base64, json, os, pathlib, stat, subprocess, sys
        environment_path = pathlib.Path(os.environ["FINDME_ENV_FILE"])
        clean_environment = {
            name: os.environ[name]
            for name in ("HOME", "PATH", "TMPDIR")
            if name in os.environ
        }
        completed = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(environment_path),
                "-f",
                sys.argv[1],
                "config",
                "--format",
                "json",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=clean_environment,
        )
        pathlib.Path(sys.argv[2]).write_text(completed.stdout)
        pathlib.Path(sys.argv[3]).write_text(completed.stderr)
        if completed.returncode != 0:
            raise SystemExit(completed.returncode)
        environment = json.loads(completed.stdout)["services"]["parser"]["environment"]
        binary_path = next(environment_path.parent.glob("binary-*"))
        binary_result = {
            "value": base64.b64encode(binary_path.read_bytes()).decode(),
            "mode": stat.S_IMODE(binary_path.stat().st_mode),
            "environment_mode": stat.S_IMODE(environment_path.stat().st_mode),
        }
        pathlib.Path(sys.argv[4]).write_text(json.dumps(binary_result))
        previous = os.umask(0o077)
        os.umask(previous)
        assert previous == 0o077
        """
    )
    http = _HttpBoundary([_metadata(), _payload(values)])

    exit_code = _run_main(
        resolver,
        monkeypatch,
        tmp_path,
        http,
        consumer="deploy",
        command=[
            sys.executable,
            "-c",
            checker,
            str(compose_path),
            str(compose_result_path),
            str(compose_error_path),
            str(binary_result_path),
        ],
    )

    assert exit_code == 0
    compose_environment = json.loads(compose_result_path.read_text())["services"]["parser"][
        "environment"
    ]
    expected_text_values: dict[str, str] = {}
    for entry in manifest["entries"]:
        if entry["key"] in manifest["consumers"]["deploy"] and entry["type"] == "text":
            value = values[entry["key"]]
            assert isinstance(value, str)
            expected_text_values[entry["target"]] = value
    # Compose doubles literal dollar signs in its canonical config output.
    canonical_expected = {
        key: value.replace("$", "$$") for key, value in expected_text_values.items()
    }
    assert compose_environment == canonical_expected
    binary_result = json.loads(binary_result_path.read_text())
    binary_value = values["VM_SSH_KEY"]
    assert isinstance(binary_value, bytes)
    assert binary_result == {
        "value": base64.b64encode(binary_value).decode(),
        "mode": 0o600,
        "environment_mode": 0o600,
    }
    compose_result_path.unlink()
    compose_error_path.unlink()
    binary_result_path.unlink()
    assert not (tmp_path / "nope").exists()


def test_child_environment_cannot_bypass_projection_or_environment_files(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    values = _sentinel_values(manifest)
    entry_names = {entry["key"] for entry in manifest["entries"]}
    target_names = {entry["target"] for entry in manifest["entries"]}
    override_names = set(manifest["local_overrides"])
    scrubbed_names = entry_names | target_names | override_names
    for name in scrubbed_names:
        monkeypatch.setenv(name, f"stale-parent-{name}")
    monkeypatch.setenv("SAFE_PARENT_VALUE", "preserved-non-secret")
    names_path = tmp_path / "names.json"
    names_path.write_text(json.dumps(sorted(scrubbed_names)))
    result_path = tmp_path / "child-environment.json"
    checker = textwrap.dedent(
        """
        import json, os, pathlib, sys
        names = json.loads(pathlib.Path(sys.argv[1]).read_text())
        result = {
            "scrubbed": {name: os.environ.get(name) for name in names},
            "safe": os.environ.get("SAFE_PARENT_VALUE"),
            "environment_file": os.environ.get("FINDME_ENV_FILE"),
        }
        pathlib.Path(sys.argv[2]).write_text(json.dumps(result))
        """
    )
    http = _HttpBoundary([_metadata(), _payload(values)])

    assert (
        _run_main(
            resolver,
            monkeypatch,
            tmp_path,
            http,
            consumer="local-web",
            command=[sys.executable, "-c", checker, str(names_path), str(result_path)],
        )
        == 0
    )

    result = json.loads(result_path.read_text())
    assert result["scrubbed"] == {name: None for name in sorted(scrubbed_names)}
    assert result["safe"] == "preserved-non-secret"
    assert result["environment_file"]
    assert not Path(result["environment_file"]).exists()


@pytest.mark.parametrize(
    ("consumer", "expected"),
    [
        ("local-web", LOCAL_WEB_KEYS),
        ("deploy", (DEPLOY_KEYS - {"VM_SSH_KEY"}) | {"VM_SSH_KEY_FILE"}),
        ("remote-check", {"VM_SSH_KEY_FILE"}),
        ("public-monitor", {"YANDEX_MONITORING_API_KEY"}),
    ],
)
def test_each_consumer_receives_exactly_its_projection(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    consumer: str,
    expected: set[str],
) -> None:
    values = _sentinel_values(manifest)
    result_path = tmp_path / "keys.json"
    checker = textwrap.dedent(
        """
        import json, os, pathlib, sys
        data = pathlib.Path(os.environ["FINDME_ENV_FILE"]).read_text()
        keys = sorted(line.split("=", 1)[0] for line in data.splitlines() if "=" in line)
        pathlib.Path(sys.argv[1]).write_text(json.dumps(keys))
        """
    )
    http = _HttpBoundary([_metadata(), _payload(values)])

    assert (
        _run_main(
            resolver,
            monkeypatch,
            tmp_path,
            http,
            consumer=consumer,
            command=[sys.executable, "-c", checker, str(result_path)],
        )
        == 0
    )

    assert set(json.loads(result_path.read_text())) == expected
    if consumer == "local-web":
        assert {
            "DB_PASSWORD",
            "LETSENCRYPT_EMAIL",
            "VM_SSH_KEY_FILE",
            "GHCR_READ_TOKEN",
            "YANDEX_MONITORING_API_KEY",
        }.isdisjoint(expected)


@pytest.mark.parametrize(
    "consumer",
    ["local-web", "deploy", "remote-check", "public-monitor"],
)
def test_resolver_runs_the_projection_verifier_without_exposing_payload_values(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    consumer: str,
) -> None:
    values = _sentinel_values(manifest)
    http = _HttpBoundary([_metadata(), _payload(values)])

    assert (
        _run_main(
            resolver,
            monkeypatch,
            tmp_path,
            http,
            consumer=consumer,
            command=[sys.executable, str(VERIFIER_PATH), consumer],
        )
        == 0
    )

    captured = capfd.readouterr()
    assert captured.out == (
        f"[environment-secrets] stage=resolve status=ok "
        f"consumer={consumer} version_id=version-exact\n"
        f"[environment-secret-projection] consumer={consumer} status=ok\n"
    )
    assert captured.err == ""
    for value in values.values():
        if isinstance(value, str):
            assert value not in captured.out


@pytest.mark.parametrize(("child_exit", "expected_exit"), [(0, 0), (23, 23)])
def test_private_files_are_removed_after_child_exit(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    child_exit: int,
    expected_exit: int,
) -> None:
    values = _sentinel_values(manifest)
    result_path = tmp_path / "paths.json"
    checker = textwrap.dedent(
        f"""
        import json, os, pathlib, sys
        env_file = pathlib.Path(os.environ["FINDME_ENV_FILE"])
        binary_path = next(env_file.parent.glob("binary-*"))
        paths = [str(env_file), str(binary_path)]
        pathlib.Path(sys.argv[1]).write_text(json.dumps(paths))
        raise SystemExit({child_exit})
        """
    )
    http = _HttpBoundary([_metadata(), _payload(values)])

    exit_code = _run_main(
        resolver,
        monkeypatch,
        tmp_path,
        http,
        consumer="remote-check",
        command=[sys.executable, "-c", checker, str(result_path)],
    )

    assert exit_code == expected_exit
    assert all(not Path(path).exists() for path in json.loads(result_path.read_text()))


def test_private_files_are_removed_when_child_cannot_start(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    values = _sentinel_values(manifest)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    http = _HttpBoundary([_metadata(), _payload(values)])

    exit_code = _run_main(
        resolver,
        monkeypatch,
        tmp_path,
        http,
        consumer="remote-check",
        command=[str(tmp_path / "missing-command")],
    )

    assert exit_code == 2
    assert list(tmp_path.glob("findme-environment-*")) == []
    captured = capsys.readouterr()
    assert "stage=resolve status=ok" in captured.out
    assert "stage=child status=error code=child_start_failed" in captured.err
    assert not any(str(value) in captured.err for value in values.values())


def test_cleanup_failure_reports_the_exact_retained_private_path(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    values = _sentinel_values(manifest)
    retained_path: Path | None = None
    original_rmtree = resolver.shutil.rmtree

    def fail_cleanup(path: Path) -> None:
        nonlocal retained_path
        retained_path = Path(path)
        raise OSError("forced cleanup failure with private detail")

    monkeypatch.setattr(resolver.shutil, "rmtree", fail_cleanup)
    http = _HttpBoundary([_metadata(), _payload(values)])

    try:
        exit_code = _run_main(
            resolver,
            monkeypatch,
            tmp_path,
            http,
            consumer="remote-check",
        )

        captured = capsys.readouterr()
        assert exit_code == 2
        assert retained_path is not None and retained_path.is_dir()
        assert (
            f"stage=cleanup status=error code=cleanup_failed retained_path={retained_path}"
            in captured.err
        )
        assert "forced cleanup failure with private detail" not in captured.err
        assert not any(
            (value.decode(errors="ignore") if isinstance(value, bytes) else value) in captured.err
            for value in values.values()
        )
        assert all(path.name not in captured.err for path in retained_path.iterdir())
    finally:
        if retained_path is not None and retained_path.exists():
            original_rmtree(retained_path)


def _signal_wrapper(tmp_path: Path) -> Path:
    wrapper = tmp_path / "resolver-wrapper.py"
    wrapper.write_text(
        textwrap.dedent(
            f"""
            import importlib.util, json, os, sys
            spec = importlib.util.spec_from_file_location(
                "wrapped_resolver", {str(RESOLVER_PATH)!r}
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            metadata = json.loads(os.environ["TEST_METADATA"])
            payload = json.loads(os.environ["TEST_PAYLOAD"])
            class Response:
                def __init__(self, value): self.value = value
                def __enter__(self): return self
                def __exit__(self, *_args): return None
                def read(self): return json.dumps(self.value).encode()
            responses = iter((metadata, payload))
            module.urlopen = lambda _request, timeout: Response(next(responses))
            if "TEST_MATERIALIZED_READY" in os.environ:
                original_materialize = module._materialize
                def pause_after_materialize(*args):
                    environment_path = original_materialize(*args)
                    pathlib.Path(os.environ["TEST_MATERIALIZED_READY"]).write_text(
                        str(environment_path)
                    )
                    time.sleep(60)
                    return environment_path
                import pathlib, time
                module._materialize = pause_after_materialize
            raise SystemExit(module.main())
            """
        )
    )
    return wrapper


@pytest.mark.parametrize("signal_number", [signal.SIGHUP, signal.SIGINT, signal.SIGTERM])
def test_signal_is_forwarded_and_private_files_are_removed(
    manifest: dict[str, Any],
    tmp_path: Path,
    signal_number: signal.Signals,
) -> None:
    values = _sentinel_values(manifest)
    wrapper = _signal_wrapper(tmp_path)
    binary_dir = _fake_yc(tmp_path)
    ready_path = tmp_path / "ready.json"
    child = textwrap.dedent(
        """
        import json, os, pathlib, signal, sys, time
        signal.signal(signal.SIGHUP, lambda *_: raise_exit(129))
        signal.signal(signal.SIGINT, lambda *_: raise_exit(130))
        signal.signal(signal.SIGTERM, lambda *_: raise_exit(143))
        env_file = pathlib.Path(os.environ["FINDME_ENV_FILE"])
        binary_path = next(env_file.parent.glob("binary-*"))
        paths = [str(env_file), str(binary_path)]
        pathlib.Path(sys.argv[1]).write_text(json.dumps(paths))
        while True: time.sleep(1)
        """
    ).replace("import json,", "def raise_exit(code): raise SystemExit(code)\nimport json,")
    command = [
        sys.executable,
        str(wrapper),
        "--consumer",
        "remote-check",
        "--identity",
        "yc",
        "--",
        sys.executable,
        "-c",
        child,
        str(ready_path),
    ]
    environment = {
        **os.environ,
        "PATH": f"{binary_dir}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
        "TEST_METADATA": json.dumps(_metadata()),
        "TEST_PAYLOAD": json.dumps(_payload(values)),
    }
    process = subprocess.Popen(
        command, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    deadline = time.monotonic() + 10
    while not ready_path.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready_path.exists(), process.communicate(timeout=2)

    process_arguments = "\0".join(str(argument) for argument in process.args)
    process.send_signal(signal_number)
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 128 + signal_number
    assert all(not Path(path).exists() for path in json.loads(ready_path.read_text()))
    assert list(tmp_path.glob("findme-environment-*")) == []
    for value in values.values():
        sentinel = value.decode(errors="ignore") if isinstance(value, bytes) else value
        assert sentinel not in stdout
        assert sentinel not in stderr
        assert sentinel not in process_arguments
    assert "::" not in stdout + stderr


def test_signal_between_materialization_and_child_start_removes_private_files(
    manifest: dict[str, Any],
    tmp_path: Path,
) -> None:
    values = _sentinel_values(manifest)
    wrapper = _signal_wrapper(tmp_path)
    binary_dir = _fake_yc(tmp_path)
    ready_path = tmp_path / "materialized"
    command = [
        sys.executable,
        str(wrapper),
        "--consumer",
        "remote-check",
        "--identity",
        "yc",
        "--",
        sys.executable,
        "-c",
        "pass",
    ]
    environment = {
        **os.environ,
        "PATH": f"{binary_dir}{os.pathsep}{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
        "TEST_METADATA": json.dumps(_metadata()),
        "TEST_PAYLOAD": json.dumps(_payload(values)),
        "TEST_MATERIALIZED_READY": str(ready_path),
    }
    process = subprocess.Popen(
        command, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + 10
        while not ready_path.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready_path.exists(), process.communicate(timeout=2)
        environment_path = Path(ready_path.read_text())

        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)

        assert process.returncode == 128 + signal.SIGTERM
        assert not environment_path.exists()
        assert list(tmp_path.glob("findme-environment-*")) == []
        assert all(str(value) not in stdout + stderr for value in values.values())
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for retained in tmp_path.glob("findme-environment-*"):
            shutil.rmtree(retained)


@pytest.mark.parametrize(
    "request_url",
    [
        "https://attacker.invalid/oidc?x=1",
        "https://evilactions.githubusercontent.com/oidc?x=1",
        "https://actions.githubusercontent.com.attacker.invalid/oidc?x=1",
        "https://actions.githubusercontent.com/oidc?x=1",
        "https://pipelines.actions.githubusercontent.com:444/oidc?x=1",
        "https://github-user@pipelines.actions.githubusercontent.com/oidc?x=1",
        "https://pipelines.actions.githubusercontent.com",
        "https://pipelines.actions.githubusercontent.com/oidc?x=1#fragment",
    ],
    ids=[
        "unrelated-host",
        "lookalike-host",
        "suffix-attack",
        "apex",
        "non-default-port",
        "userinfo",
        "missing-path",
        "fragment",
    ],
)
def test_github_identity_rejects_an_untrusted_token_request_url(
    resolver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request_url: str,
) -> None:
    http = _HttpBoundary([])
    monkeypatch.setattr(resolver, "urlopen", http)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", request_url)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "github-request-token")

    exit_code = resolver.main(
        [
            "--consumer",
            "public-monitor",
            "--identity",
            "github-oidc",
            "--",
            "true",
        ]
    )

    assert exit_code == 2
    assert http.requests == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "stage=identity status=error code=identity_failed" in captured.err
    assert "github-request-token" not in captured.err


@pytest.mark.parametrize(
    "request_url",
    [
        "https://pipelines.actions.githubusercontent.com/id-token?x=1",
        "https://token.actions.githubusercontent.com/id-token?x=1",
        "https://run-actions-1-azure-eastus.actions.githubusercontent.com/id-token?x=1",
    ],
    ids=["pipelines", "token", "observed-regional-runner"],
)
def test_github_oidc_claims_and_token_exchange_are_closed(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    request_url: str,
) -> None:
    claims = {
        "iss": manifest["github_oidc"]["issuer"],
        "aud": manifest["github_oidc"]["audience"],
        "sub": manifest["github_oidc"]["subject"],
        "repository": manifest["github_oidc"]["repository"],
        "ref": manifest["github_oidc"]["ref"],
        "workflow_ref": manifest["github_oidc"]["allowed_workflows"][0],
    }
    encoded_claims = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    oidc_jwt = f"header.{encoded_claims}.signature"
    values = _sentinel_values(manifest)
    http = _HttpBoundary(
        [
            {"value": oidc_jwt},
            {"access_token": "github-iam-token", "token_type": "Bearer", "expires_in": 3600},
            _metadata(),
            _payload(values),
        ]
    )
    monkeypatch.setattr(resolver, "urlopen", http)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", request_url)
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "github-request-token")

    exit_code = resolver.main(
        [
            "--consumer",
            "public-monitor",
            "--identity",
            "github-oidc",
            "--",
            sys.executable,
            "-c",
            (
                "import os; "
                "assert 'ACTIONS_ID_TOKEN_REQUEST_TOKEN' not in os.environ; "
                "assert 'ACTIONS_ID_TOKEN_REQUEST_URL' not in os.environ"
            ),
        ]
    )

    assert exit_code == 0
    assert len(http.requests) == 4
    oidc_request, exchange_request, metadata_request, payload_request = http.requests
    assert urlsplit(oidc_request.full_url).hostname == urlsplit(request_url).hostname
    assert "audience=https%3A%2F%2Fgithub.com%2Fpeter-nikitin" in oidc_request.full_url
    assert oidc_request.get_header("Authorization") == "Bearer github-request-token"
    exchange_fields = exchange_request.data.decode()
    assert "audience=ajeaekiue94ogksguh0h" in exchange_fields
    assert "subject_token=header." in exchange_fields
    assert metadata_request.get_header("Authorization") == "Bearer github-iam-token"
    assert payload_request.get_header("Authorization") == "Bearer github-iam-token"


@pytest.mark.parametrize(
    "workflow_ref",
    [
        None,
        ".github/workflows/deploy.yml",
        "another-owner/photo-prjct/.github/workflows/deploy.yml@refs/heads/main",
        "peter-nikitin/photo-prjct/.github/workflows/other.yml@refs/heads/main",
        "peter-nikitin/photo-prjct/.github/workflows/deploy.yml@refs/heads/feature",
    ],
    ids=["missing", "path-only", "wrong-repository", "unknown-workflow", "wrong-ref"],
)
def test_github_oidc_rejects_a_workflow_ref_outside_the_exact_manifest_allowlist(
    resolver: ModuleType,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    workflow_ref: str | None,
) -> None:
    claims = {
        "iss": manifest["github_oidc"]["issuer"],
        "aud": manifest["github_oidc"]["audience"],
        "sub": manifest["github_oidc"]["subject"],
        "repository": manifest["github_oidc"]["repository"],
        "ref": manifest["github_oidc"]["ref"],
    }
    if workflow_ref is not None:
        claims["workflow_ref"] = workflow_ref
    encoded_claims = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    http = _HttpBoundary([{"value": f"header.{encoded_claims}.signature"}])
    monkeypatch.setattr(resolver, "urlopen", http)
    monkeypatch.setenv(
        "ACTIONS_ID_TOKEN_REQUEST_URL",
        "https://pipelines.actions.githubusercontent.com/id-token?x=1",
    )
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "github-request-token")

    exit_code = resolver.main(
        [
            "--consumer",
            "public-monitor",
            "--identity",
            "github-oidc",
            "--",
            "true",
        ]
    )

    assert exit_code == 2
    assert len(http.requests) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "stage=identity status=error code=identity_claims_mismatch" in captured.err
    assert "github-request-token" not in captured.err


def test_repository_does_not_retain_generated_sentinel(
    manifest: dict[str, Any],
    resolver: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    values = _sentinel_values(manifest)
    sentinel = f"repository-absence-{uuid.uuid4().hex}"
    values["SECRET_KEY"] = sentinel
    http = _HttpBoundary([_metadata(), _payload(values)])

    assert _run_main(resolver, monkeypatch, tmp_path, http) == 0

    captured = capsys.readouterr()
    assert sentinel not in captured.out
    assert sentinel not in captured.err
    assert "::" not in captured.out + captured.err
    for path in REPOSITORY_ROOT.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            try:
                assert sentinel.encode() not in path.read_bytes()
            except OSError:
                continue

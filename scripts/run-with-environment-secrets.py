#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATHS = {
    "staging": REPOSITORY_ROOT / "deploy/environment-secrets/staging.json",
}
LOCKBOX_API_BASE = "https://lockbox.api.cloud.yandex.net"
LOCKBOX_PAYLOAD_BASE = "https://payload.lockbox.api.cloud.yandex.net"
YANDEX_TOKEN_EXCHANGE_URL = "https://auth.yandex.cloud/oauth/token"
HTTP_TIMEOUT_SECONDS = 20.0
PRIVATE_MODE = 0o600
PRIVATE_UMASK = 0o077
ENVIRONMENT_NAME = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
GITHUB_TOKEN_REQUEST_DOMAIN = "actions.githubusercontent.com"
GITHUB_TOKEN_REQUEST_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
HANDLED_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)


@dataclass(frozen=True)
class ResolverError(Exception):
    stage: str
    code: str
    retained_path: str | None = None


@dataclass(frozen=True)
class SignalReceived(Exception):
    number: int


@dataclass(frozen=True)
class SecretValue:
    kind: str
    value: str | bytes


def _fail(stage: str, code: str) -> NoReturn:
    raise ResolverError(stage, code)


def _load_manifest(environment: str) -> dict[str, Any]:
    path = MANIFEST_PATHS.get(environment)
    if path is None:
        _fail("manifest", "unknown_environment")
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        _fail("manifest", "manifest_invalid")
    if not isinstance(manifest, dict) or manifest.get("environment") != environment:
        _fail("manifest", "manifest_invalid")
    return manifest


def _manifest_entries(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list):
        _fail("manifest", "manifest_invalid")
    entries: dict[str, Mapping[str, Any]] = {}
    targets: set[str] = set()
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            _fail("manifest", "manifest_invalid")
        key = raw_entry.get("key")
        target = raw_entry.get("target")
        kind = raw_entry.get("type")
        if (
            not isinstance(key, str)
            or key in entries
            or not ENVIRONMENT_NAME.fullmatch(key)
            or not isinstance(target, str)
            or target in targets
            or not ENVIRONMENT_NAME.fullmatch(target)
            or kind not in {"text", "binary"}
            or not isinstance(raw_entry.get("required"), bool)
            or not isinstance(raw_entry.get("local"), bool)
        ):
            _fail("manifest", "manifest_invalid")
        entries[key] = raw_entry
        targets.add(target)
    if not entries:
        _fail("manifest", "manifest_invalid")
    return entries


def _consumer_projection(
    manifest: Mapping[str, Any], consumer: str, entries: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    consumers = manifest.get("consumers")
    if not isinstance(consumers, dict):
        _fail("manifest", "manifest_invalid")
    if consumer not in consumers:
        _fail("manifest", "unknown_consumer")
    projection = consumers[consumer]
    if (
        not isinstance(projection, list)
        or not projection
        or any(not isinstance(key, str) for key in projection)
        or len(projection) != len(set(projection))
        or any(key not in entries for key in projection)
    ):
        _fail("manifest", "manifest_invalid")
    return projection


def _request_json(request: Request, *, stage: str, code: str) -> dict[str, Any]:
    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            value = json.loads(response.read())
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        _fail(stage, code)
    if not isinstance(value, dict):
        _fail(stage, code)
    return value


def _yc_identity() -> str:
    try:
        result = subprocess.run(
            ["yc", "iam", "create-token"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        _fail("identity", "identity_failed")
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        _fail("identity", "identity_failed")
    return token


def _append_query(url: str, name: str, value: str) -> str:
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.append((name, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _validate_github_token_request_url(url: str) -> None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        _fail("identity", "identity_failed")
    if (
        parts.scheme != "https"
        or not _is_github_token_request_host(parts.hostname)
        or port not in {None, 443}
        or parts.username is not None
        or parts.password is not None
        or not parts.path
        or parts.fragment
    ):
        _fail("identity", "identity_failed")


def _is_github_token_request_host(hostname: str | None) -> bool:
    suffix = f".{GITHUB_TOKEN_REQUEST_DOMAIN}"
    if hostname is None or not hostname.endswith(suffix):
        return False
    labels = hostname[: -len(suffix)].split(".")
    return bool(labels) and all(
        GITHUB_TOKEN_REQUEST_HOST_LABEL.fullmatch(label) for label in labels
    )


def _decode_jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        _fail("identity", "identity_claims_mismatch")
    try:
        encoded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(encoded))
    except (ValueError, binascii.Error, json.JSONDecodeError):
        _fail("identity", "identity_claims_mismatch")
    if not isinstance(claims, dict):
        _fail("identity", "identity_claims_mismatch")
    return claims


def _github_identity(manifest: Mapping[str, Any], environment: Mapping[str, str]) -> str:
    oidc = manifest.get("github_oidc")
    if not isinstance(oidc, dict):
        _fail("manifest", "manifest_invalid")
    request_url = environment.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = environment.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    expected_fields = {
        "issuer": oidc.get("issuer"),
        "audience": oidc.get("audience"),
        "subject": oidc.get("subject"),
        "repository": oidc.get("repository"),
        "environment": oidc.get("environment"),
        "service_account_id": oidc.get("service_account_id"),
    }
    if (
        not request_url
        or not request_token
        or any(not isinstance(value, str) or not value for value in expected_fields.values())
    ):
        _fail("identity", "identity_failed")
    _validate_github_token_request_url(request_url)

    request = Request(
        _append_query(request_url, "audience", expected_fields["audience"]),
        headers={"Authorization": f"Bearer {request_token}", "Accept": "application/json"},
    )
    token_response = _request_json(request, stage="identity", code="identity_failed")
    subject_token = token_response.get("value")
    if not isinstance(subject_token, str) or not subject_token:
        _fail("identity", "identity_failed")

    claims = _decode_jwt_claims(subject_token)
    expected_claims = {
        "iss": expected_fields["issuer"],
        "aud": expected_fields["audience"],
        "sub": expected_fields["subject"],
        "repository": expected_fields["repository"],
        "environment": expected_fields["environment"],
    }
    if any(claims.get(name) != value for name, value in expected_claims.items()):
        _fail("identity", "identity_claims_mismatch")
    allowed_workflows = oidc.get("allowed_workflows")
    workflow_ref = claims.get("workflow_ref")
    if (
        not isinstance(allowed_workflows, list)
        or not allowed_workflows
        or any(not isinstance(reference, str) or not reference for reference in allowed_workflows)
        or len(allowed_workflows) != len(set(allowed_workflows))
        or not isinstance(workflow_ref, str)
        or workflow_ref not in allowed_workflows
    ):
        _fail("identity", "identity_claims_mismatch")

    exchange_body = urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "audience": expected_fields["service_account_id"],
            "subject_token": subject_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
        }
    ).encode()
    exchange_request = Request(
        YANDEX_TOKEN_EXCHANGE_URL,
        data=exchange_body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    exchange_response = _request_json(
        exchange_request, stage="identity", code="identity_exchange_failed"
    )
    iam_token = exchange_response.get("access_token")
    if (
        not isinstance(iam_token, str)
        or not iam_token
        or exchange_response.get("token_type") != "Bearer"
    ):
        _fail("identity", "identity_exchange_failed")
    return iam_token


def _obtain_identity(
    identity: str, manifest: Mapping[str, Any], environment: Mapping[str, str]
) -> str:
    if identity == "yc":
        return _yc_identity()
    if identity == "github-oidc":
        return _github_identity(manifest, environment)
    _fail("identity", "unknown_identity")


def _authorized_request(url: str, iam_token: str) -> Request:
    return Request(
        url,
        headers={"Authorization": f"Bearer {iam_token}", "Accept": "application/json"},
    )


def _active_version(
    manifest: Mapping[str, Any], entries: Mapping[str, Mapping[str, Any]], iam_token: str
) -> str:
    lockbox = manifest.get("lockbox")
    if not isinstance(lockbox, dict):
        _fail("manifest", "manifest_invalid")
    secret_id = lockbox.get("secret_id")
    folder_id = lockbox.get("folder_id")
    if (
        not isinstance(secret_id, str)
        or not secret_id
        or not isinstance(folder_id, str)
        or not folder_id
    ):
        _fail("manifest", "manifest_invalid")
    request = _authorized_request(f"{LOCKBOX_API_BASE}/lockbox/v1/secrets/{secret_id}", iam_token)
    metadata = _request_json(request, stage="metadata", code="metadata_retrieval_failed")
    if (
        metadata.get("id") != secret_id
        or metadata.get("folderId") != folder_id
        or metadata.get("status") != "ACTIVE"
    ):
        _fail("metadata", "metadata_mismatch")
    version = metadata.get("currentVersion")
    if not isinstance(version, dict) or not version.get("id") or version.get("status") != "ACTIVE":
        _fail("metadata", "missing_active_version")
    if version.get("secretId") != secret_id:
        _fail("metadata", "metadata_mismatch")
    payload_keys = version.get("payloadEntryKeys")
    if (
        not isinstance(payload_keys, list)
        or any(not isinstance(key, str) for key in payload_keys)
        or len(payload_keys) != len(set(payload_keys))
        or set(payload_keys) != set(entries)
    ):
        _fail("metadata", "metadata_mismatch")
    version_id = version["id"]
    if not isinstance(version_id, str):
        _fail("metadata", "missing_active_version")
    return version_id


def _fetch_payload(manifest: Mapping[str, Any], version_id: str, iam_token: str) -> dict[str, Any]:
    lockbox = manifest["lockbox"]
    secret_id = lockbox["secret_id"]
    query = urlencode({"versionId": version_id})
    request = _authorized_request(
        f"{LOCKBOX_PAYLOAD_BASE}/lockbox/v1/secrets/{secret_id}/payload?{query}", iam_token
    )
    return _request_json(request, stage="payload", code="payload_retrieval_failed")


def _validate_payload(
    payload: Mapping[str, Any], version_id: str, entries: Mapping[str, Mapping[str, Any]]
) -> dict[str, SecretValue]:
    if payload.get("versionId") != version_id:
        _fail("payload", "payload_version_mismatch")
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        _fail("payload", "payload_malformed")
    values: dict[str, SecretValue] = {}
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict) or not isinstance(raw_entry.get("key"), str):
            _fail("payload", "payload_malformed")
        key = raw_entry["key"]
        if key in values:
            _fail("payload", "duplicate_entry")
        specification = entries.get(key)
        if specification is None:
            _fail("payload", "unknown_entry")
        value_fields = {field for field in ("textValue", "binaryValue") if field in raw_entry}
        expected_field = "textValue" if specification["type"] == "text" else "binaryValue"
        if value_fields != {expected_field} or set(raw_entry) != {"key", expected_field}:
            _fail("payload", "wrong_type")
        raw_value = raw_entry[expected_field]
        if not isinstance(raw_value, str):
            _fail("payload", "wrong_type")
        if expected_field == "binaryValue":
            try:
                value: str | bytes = base64.b64decode(raw_value, validate=True)
            except (ValueError, binascii.Error):
                _fail("payload", "payload_malformed")
        else:
            value = raw_value
        if specification["required"] and not value:
            _fail("payload", "empty_entry")
        values[key] = SecretValue(specification["type"], value)
    missing = set(entries) - set(values)
    if missing:
        _fail("payload", "missing_entry")
    return values


def _write_private(path: Path, data: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE_MODE)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if stat.S_IMODE(path.stat().st_mode) != PRIVATE_MODE:
            _fail("materialize", "private_mode_failed")
    except ResolverError:
        raise
    except OSError:
        _fail("materialize", "materialization_failed")


def _encode_dotenv(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("$", "\\$")
    )
    return f'"{escaped}"'


def _materialize(
    root: Path,
    projection: Sequence[str],
    entries: Mapping[str, Mapping[str, Any]],
    values: Mapping[str, SecretValue],
) -> Path:
    records: list[str] = []
    for key in projection:
        specification = entries[key]
        secret = values[key]
        target = specification["target"]
        if secret.kind == "binary":
            binary_path = root / f"binary-{len(records)}"
            if not isinstance(secret.value, bytes):
                _fail("materialize", "materialization_failed")
            _write_private(binary_path, secret.value)
            value = str(binary_path)
        else:
            if not isinstance(secret.value, str):
                _fail("materialize", "materialization_failed")
            value = secret.value
        records.append(f"{target}={_encode_dotenv(value)}\n")
    environment_path = root / "environment.env"
    _write_private(environment_path, "".join(records).encode())
    return environment_path


def _stop_child(process: subprocess.Popen[bytes], signal_number: int) -> None:
    if process.poll() is not None:
        return
    try:
        process.send_signal(signal_number)
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    except OSError:
        process.kill()
        process.wait()


def _run_child(
    command: Sequence[str],
    environment_path: Path,
    manifest: Mapping[str, Any],
    entries: Mapping[str, Mapping[str, Any]],
    consumer: str,
) -> int:
    child_environment = os.environ.copy()
    governed_names = set(entries)
    governed_names.update(entry["target"] for entry in entries.values())
    if consumer == "local-web":
        local_overrides = manifest.get("local_overrides")
        if not isinstance(local_overrides, dict) or any(
            not isinstance(name, str) or not ENVIRONMENT_NAME.fullmatch(name)
            for name in local_overrides
        ):
            _fail("manifest", "manifest_invalid")
        governed_names.update(local_overrides)
    governed_names.update({"ACTIONS_ID_TOKEN_REQUEST_URL", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"})
    for name in governed_names:
        child_environment.pop(name, None)
    child_environment["FINDME_ENV_FILE"] = str(environment_path)
    process: subprocess.Popen[bytes] | None = None
    previous_handlers: dict[int, Callable[[int, FrameType | None], Any] | int | None] = {}

    def handle_signal(number: int, _frame: FrameType | None) -> NoReturn:
        raise SignalReceived(number)

    for number in HANDLED_SIGNALS:
        previous_handlers[number] = signal.getsignal(number)
        signal.signal(number, handle_signal)
    try:
        try:
            process = subprocess.Popen(list(command), env=child_environment)
            return process.wait()
        except OSError:
            _fail("child", "child_start_failed")
        except SignalReceived as received:
            for number in HANDLED_SIGNALS:
                signal.signal(number, signal.SIG_IGN)
            if process is not None:
                _stop_child(process, received.number)
            return 128 + received.number
    finally:
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)


def _resolve_and_run(environment: str, consumer: str, identity: str, command: Sequence[str]) -> int:
    manifest = _load_manifest(environment)
    entries = _manifest_entries(manifest)
    projection = _consumer_projection(manifest, consumer, entries)
    iam_token = _obtain_identity(identity, manifest, os.environ)
    version_id = _active_version(manifest, entries, iam_token)
    payload = _fetch_payload(manifest, version_id, iam_token)
    values = _validate_payload(payload, version_id, entries)

    previous_handlers: dict[int, Callable[[int, FrameType | None], Any] | int | None] = {}

    def handle_signal(number: int, _frame: FrameType | None) -> NoReturn:
        raise SignalReceived(number)

    for number in HANDLED_SIGNALS:
        previous_handlers[number] = signal.getsignal(number)
        signal.signal(number, handle_signal)
    previous_umask = os.umask(PRIVATE_UMASK)
    temporary_root: Path | None = None
    exit_code: int | None = None
    failure: ResolverError | None = None
    try:
        try:
            temporary_root = Path(tempfile.mkdtemp(prefix="findme-environment-"))
            environment_path = _materialize(temporary_root, projection, entries, values)
            print(
                f"[environment-secrets] stage=resolve status=ok "
                f"environment={environment} consumer={consumer} version_id={version_id}"
            )
            exit_code = _run_child(command, environment_path, manifest, entries, consumer)
        except ResolverError as error:
            failure = error
        except SignalReceived as received:
            exit_code = 128 + received.number
    finally:
        for number in HANDLED_SIGNALS:
            signal.signal(number, signal.SIG_IGN)
        if temporary_root is not None:
            try:
                shutil.rmtree(temporary_root)
            except OSError:
                failure = ResolverError("cleanup", "cleanup_failed", str(temporary_root))
        os.umask(previous_umask)
        for number, handler in previous_handlers.items():
            signal.signal(number, handler)
    if failure is not None:
        raise failure
    if exit_code is None:
        _fail("child", "child_failed")
    return exit_code


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one command with reviewed environment secrets"
    )
    parser.add_argument("--environment", required=True)
    parser.add_argument("--consumer", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    parsed = _parser().parse_args(arguments)
    command = list(parsed.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print(
            "[environment-secrets] stage=arguments status=error code=missing_command",
            file=sys.stderr,
        )
        return 2
    try:
        return _resolve_and_run(parsed.environment, parsed.consumer, parsed.identity, command)
    except ResolverError as error:
        retained_path = f" retained_path={error.retained_path}" if error.retained_path else ""
        print(
            f"[environment-secrets] stage={error.stage} status=error code={error.code}"
            f"{retained_path}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

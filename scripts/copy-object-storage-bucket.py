#!/usr/bin/env python3
"""Copy one reviewed Object Storage role without changing bucket configuration.

Run this only through ``run-with-environment-secrets.py`` so credentials are
projected ephemerally.  This source-only tool intentionally has no bucket,
endpoint, prefix, KMS, or credential command-line options.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import boto3
from botocore.client import Config

_MAX_WORKERS = 8
_KMS_KEY_ID = "abjjca35o900fng2nk6v"
_ENDPOINT_URL = "https://storage.yandexcloud.net"
_REGION = "ru-central1"
_ROLES: dict[str, dict[str, object]] = {
    "public": {
        "source": "project-storage-dev-2026",
        "target": "findme-photo-public-media-b1g2qttg",
        "prefixes": (),
        "credential_prefix": "MEDIA_S3",
        "copy_options": {"ACL": "public-read"},
    },
    "private": {
        "source": "hires-staging",
        "target": "findme-photo-private-media-b1g2qttg",
        "prefixes": ("originals/", "derivatives/"),
        "credential_prefix": "PRIVATE_MEDIA_S3",
        "copy_options": {},
    },
    "feedback": {
        "source": "findme-selfie-feedback-staging-b1g2qttg",
        "target": "findme-photo-selfie-feedback-b1g2qttg",
        "prefixes": (),
        "credential_prefix": "SELFIE_FEEDBACK_S3",
        "copy_options": {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": _KMS_KEY_ID},
    },
}


class CopyError(RuntimeError):
    """A deliberately non-sensitive copy preflight or verification failure."""


class _SafeArgumentParser(argparse.ArgumentParser):
    """Reject malformed CLI input without reflecting possible secret values."""

    def error(self, _message: str) -> None:
        self.exit(2, "copy-object-storage-bucket: invalid arguments\n")


def copy_role(
    *, role: str, manifest_dir: Path, dry_run: bool, client: Any | None = None
) -> dict[str, int]:
    """Copy current objects for one fixed role and prove key/size equality."""
    config = _role_config(role)
    if not manifest_dir.is_absolute():
        raise CopyError("manifest directory must be absolute")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{role}.jsonl"
    previous = _read_manifest(manifest_path, role=role, config=config)
    storage = client if client is not None else _client(config)
    source = _inventory(storage, bucket=_source(config), prefixes=_prefixes(config))
    target = _inventory(storage, bucket=_target(config), prefixes=())
    _validate_existing_target(source=source, target=target, previous=previous, config=config)

    copied, skipped = _copy_inventory(
        storage=storage,
        role=role,
        config=config,
        source=source,
        target=target,
        previous=previous,
        manifest_path=manifest_path,
        dry_run=dry_run,
    )
    if dry_run:
        return {"copied": 0, "skipped": 0}

    # A source writer can add or replace an object after the initial inventory.
    # The final live cutover freezes writers before this delta pass.
    final_source = _inventory(storage, bucket=_source(config), prefixes=_prefixes(config))
    final_target = _inventory(storage, bucket=_target(config), prefixes=())
    _validate_existing_target(
        source=final_source,
        target=final_target,
        previous=_read_manifest(manifest_path, role=role, config=config),
        config=config,
    )
    delta_copied, _delta_skipped = _copy_inventory(
        storage=storage,
        role=role,
        config=config,
        source=final_source,
        target=final_target,
        previous=_read_manifest(manifest_path, role=role, config=config),
        manifest_path=manifest_path,
        dry_run=False,
    )
    verified_target = _inventory(storage, bucket=_target(config), prefixes=())
    _validate_existing_target(
        source=final_source,
        target=verified_target,
        previous=_read_manifest(manifest_path, role=role, config=config),
        config=config,
    )
    if _key_sizes(final_source) != _key_sizes(verified_target):
        raise CopyError("source and target key/size inventories differ")
    return {"copied": copied + delta_copied, "skipped": skipped}


def _role_config(role: str) -> dict[str, object]:
    try:
        return _ROLES[role]
    except KeyError as error:
        raise CopyError("unknown copy role") from error


def _source(config: dict[str, object]) -> str:
    value = config["source"]
    if not isinstance(value, str):
        raise CopyError("invalid reviewed source")
    return value


def _target(config: dict[str, object]) -> str:
    value = config["target"]
    if not isinstance(value, str):
        raise CopyError("invalid reviewed target")
    return value


def _prefixes(config: dict[str, object]) -> tuple[str, ...]:
    value = config["prefixes"]
    if not isinstance(value, tuple) or not all(isinstance(prefix, str) for prefix in value):
        raise CopyError("invalid reviewed prefixes")
    return value


def _inventory(
    client: Any, *, bucket: str, prefixes: tuple[str, ...]
) -> dict[str, dict[str, object]]:
    inventory: dict[str, dict[str, object]] = {}
    for prefix in prefixes or ("",):
        continuation: str | None = None
        while True:
            request: dict[str, object] = {"Bucket": bucket}
            if prefix:
                request["Prefix"] = prefix
            if continuation is not None:
                request["ContinuationToken"] = continuation
            try:
                response = client.list_objects_v2(**request)
            except Exception as error:
                raise CopyError("object inventory failed") from error
            contents = response.get("Contents", [])
            if not isinstance(contents, list):
                raise CopyError("invalid object inventory")
            for item in contents:
                record = _inventory_record(item, prefix=prefix)
                key = record["key"]
                assert isinstance(key, str)
                if key in inventory:
                    raise CopyError("duplicate object inventory key")
                inventory[key] = record
            if response.get("IsTruncated") is not True:
                break
            continuation = response.get("NextContinuationToken")
            if not isinstance(continuation, str) or not continuation:
                raise CopyError("invalid object inventory continuation")
    return inventory


def _inventory_record(item: object, *, prefix: str) -> dict[str, object]:
    if not isinstance(item, dict):
        raise CopyError("invalid object inventory item")
    key = item.get("Key")
    size = item.get("Size")
    etag = item.get("ETag")
    if (
        not isinstance(key, str)
        or not key
        or key.startswith("/")
        or (prefix and not key.startswith(prefix))
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or not isinstance(etag, str)
        or not etag
    ):
        raise CopyError("invalid object inventory item")
    return {"key": key, "size": size, "etag": etag}


def _validate_existing_target(
    *,
    source: dict[str, dict[str, object]],
    target: dict[str, dict[str, object]],
    previous: dict[str, dict[str, object]],
    config: dict[str, object],
) -> None:
    if not previous and target:
        raise CopyError("target bucket must be empty before the first copy")
    prefixes = _prefixes(config)
    for key, target_item in target.items():
        if prefixes and not key.startswith(prefixes):
            raise CopyError("target contains an unexpected object prefix")
        source_item = source.get(key)
        manifest_item = previous.get(key)
        if source_item is None or manifest_item is None:
            raise CopyError("target object is not justified by the copy manifest")
        if not _same_object(target_item, manifest_item):
            raise CopyError("target object does not match the copy manifest")


def _copy_inventory(
    *,
    storage: Any,
    role: str,
    config: dict[str, object],
    source: dict[str, dict[str, object]],
    target: dict[str, dict[str, object]],
    previous: dict[str, dict[str, object]],
    manifest_path: Path,
    dry_run: bool,
) -> tuple[int, int]:
    pending = [
        item
        for key, item in source.items()
        if not _already_copied(item=item, target=target.get(key), previous=previous.get(key))
    ]
    skipped = len(source) - len(pending)
    if dry_run:
        return 0, 0
    copied: list[dict[str, object]] = []
    failure: CopyError | None = None
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = [
            executor.submit(_copy_one, storage=storage, config=config, item=item)
            for item in pending
        ]
        for future in as_completed(futures):
            try:
                copied_item = future.result()
            except CopyError as error:
                failure = error
            else:
                _append_manifest(manifest_path, role=role, copied=[copied_item])
                copied.append(copied_item)
    if failure is not None:
        raise failure
    return len(copied), skipped


def _already_copied(
    *, item: dict[str, object], target: dict[str, object] | None, previous: dict[str, object] | None
) -> bool:
    return (
        target is not None
        and previous is not None
        and _same_object(target, item)
        and _same_object(previous, item)
    )


def _same_object(left: dict[str, object], right: dict[str, object]) -> bool:
    return left["size"] == right["size"] and left["etag"] == right["etag"]


def _copy_one(
    *, storage: Any, config: dict[str, object], item: dict[str, object]
) -> dict[str, object]:
    key = item["key"]
    etag = item["etag"]
    if not isinstance(key, str) or not isinstance(etag, str):
        raise CopyError("invalid reviewed object")
    options = config["copy_options"]
    if not isinstance(options, dict):
        raise CopyError("invalid reviewed copy options")
    try:
        storage.copy_object(
            Bucket=_target(config),
            Key=key,
            CopySource={"Bucket": _source(config), "Key": key},
            CopySourceIfMatch=etag,
            MetadataDirective="COPY",
            **options,
        )
    except Exception as error:
        raise CopyError("conditional object copy failed") from error
    return item


def _read_manifest(
    path: Path, *, role: str, config: dict[str, object]
) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    records: dict[str, dict[str, object]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise CopyError("invalid copy manifest") from error
        if not isinstance(record, dict) or set(record) != {"role", "key", "size", "etag"}:
            raise CopyError("invalid copy manifest")
        if record["role"] != role:
            raise CopyError("invalid copy manifest role")
        inventory = _inventory_record(
            {"Key": record["key"], "Size": record["size"], "ETag": record["etag"]}, prefix=""
        )
        key = inventory["key"]
        assert isinstance(key, str)
        if _prefixes(config) and not key.startswith(_prefixes(config)):
            raise CopyError("invalid copy manifest prefix")
        records[key] = inventory
    return records


def _append_manifest(path: Path, *, role: str, copied: list[dict[str, object]]) -> None:
    if not copied:
        return
    with path.open("a", encoding="utf-8") as output:
        for item in copied:
            output.write(
                json.dumps({"role": role, **item}, sort_keys=True, separators=(",", ":")) + "\n"
            )
        output.flush()
        os.fsync(output.fileno())


def _key_sizes(inventory: dict[str, dict[str, object]]) -> dict[str, object]:
    return {key: item["size"] for key, item in inventory.items()}


def _client(config: dict[str, object]) -> Any:
    prefix = config["credential_prefix"]
    if not isinstance(prefix, str):
        raise CopyError("invalid reviewed credential projection")
    values = _projected_values()
    access_key = values.get(f"{prefix}_ACCESS_KEY_ID")
    secret_key = values.get(f"{prefix}_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        raise CopyError("missing ephemeral credential projection")
    return boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        endpoint_url=_ENDPOINT_URL,
        region_name=_REGION,
        config=Config(signature_version="s3v4"),
    )


def _projected_values() -> dict[str, str]:
    raw_path = os.environ.get("FINDME_ENV_FILE")
    if not raw_path:
        raise CopyError("missing ephemeral credential projection")
    path = Path(raw_path)
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise OSError
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise CopyError("invalid ephemeral credential projection") from None
    values: dict[str, str] = {}
    for line in content.splitlines():
        if not line:
            continue
        key, separator, raw_value = line.partition("=")
        if not separator or key in values or not key.isidentifier() or not key.isupper():
            raise CopyError("invalid ephemeral credential projection")
        values[key] = _dotenv_value(raw_value)
    return values


def _dotenv_value(raw_value: str) -> str:
    if len(raw_value) < 2 or raw_value[0] != '"' or raw_value[-1] != '"':
        raise CopyError("invalid ephemeral credential projection")
    escapes = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t", "$": "$"}
    result: list[str] = []
    index = 1
    while index < len(raw_value) - 1:
        character = raw_value[index]
        if character != "\\":
            result.append(character)
            index += 1
            continue
        index += 1
        if index >= len(raw_value) - 1 or raw_value[index] not in escapes:
            raise CopyError("invalid ephemeral credential projection")
        result.append(escapes[raw_value[index]])
        index += 1
    return "".join(result)


def main() -> int:
    parser = _SafeArgumentParser(description=__doc__)
    parser.add_argument("role", choices=sorted(_ROLES))
    parser.add_argument("--manifest-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = copy_role(role=args.role, manifest_dir=args.manifest_dir, dry_run=args.dry_run)
    print(f"role={args.role} copied={result['copied']} skipped={result['skipped']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CopyError as error:
        raise SystemExit(f"copy-object-storage-bucket: {error}") from None

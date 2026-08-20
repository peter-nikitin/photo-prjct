import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "copy_object_storage_bucket", ROOT / "scripts/copy-object-storage-bucket.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _resolver() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "environment_secret_resolver", ROOT / "scripts/run-with-environment-secrets.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeS3:
    def __init__(self, objects: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.objects = objects
        self.copy_calls: list[dict[str, Any]] = []
        self.list_calls: list[dict[str, Any]] = []
        self.operations: list[tuple[str, dict[str, Any]]] = []
        self.fail_keys: set[str] = set()
        self._lock = threading.Lock()
        self._active_copies = 0
        self.peak_copies = 0

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        self.operations.append(("list", kwargs))
        keys = sorted(
            key
            for key in self.objects[kwargs["Bucket"]]
            if key.startswith(kwargs.get("Prefix", ""))
        )
        start = int(kwargs.get("ContinuationToken", "0"))
        page = keys[start : start + 1]
        next_start = start + len(page)
        return {
            "Contents": [
                {
                    "Key": key,
                    "Size": self.objects[kwargs["Bucket"]][key]["Size"],
                    "ETag": self.objects[kwargs["Bucket"]][key]["ETag"],
                }
                for key in page
            ],
            "IsTruncated": next_start < len(keys),
            **({"NextContinuationToken": str(next_start)} if next_start < len(keys) else {}),
        }

    def copy_object(self, **kwargs: Any) -> dict[str, Any]:
        with self._lock:
            self.copy_calls.append(kwargs)
            self.operations.append(("copy", kwargs))
            self._active_copies += 1
            self.peak_copies = max(self.peak_copies, self._active_copies)
        time.sleep(0.005)
        with self._lock:
            self._active_copies -= 1
        if kwargs["Key"] in self.fail_keys:
            raise RuntimeError("synthetic copy failure")
        source = self.objects[kwargs["CopySource"]["Bucket"]][kwargs["CopySource"]["Key"]]
        if kwargs["CopySourceIfMatch"] != source["ETag"]:
            raise AssertionError("copy was not conditional on the source ETag")
        self.objects[kwargs["Bucket"]][kwargs["Key"]] = source.copy()
        return {"CopyObjectResult": {"ETag": source["ETag"]}}


def _objects() -> dict[str, dict[str, dict[str, Any]]]:
    return {
        "project-storage-dev-2026": {
            "covers/event.jpg": {"Size": 7, "ETag": '"public-etag"'},
            "legacy/file.jpg": {"Size": 9, "ETag": '"legacy-etag"'},
        },
        "findme-photo-public-media-b1g2qttg": {},
        "hires-staging": {
            "originals/one": {"Size": 11, "ETag": '"original-etag"'},
            "derivatives/two": {"Size": 13, "ETag": '"derivative-etag"'},
            "processing-staging/skip": {"Size": 17, "ETag": '"temporary-etag"'},
        },
        "findme-photo-private-media-b1g2qttg": {},
        "findme-selfie-feedback-staging-b1g2qttg": {
            "feedback/one.jpg": {"Size": 19, "ETag": '"feedback-etag"'},
        },
        "findme-photo-selfie-feedback-b1g2qttg": {},
    }


def _write_manifest(path: Path, *records: dict[str, object]) -> None:
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def _private_record(*, key: str, size: int, etag: str) -> dict[str, object]:
    return {"role": "private", "key": key, "size": size, "etag": etag}


def test_public_copy_is_allowlisted_conditional_and_records_a_non_secret_manifest(
    tmp_path: Path,
) -> None:
    tool = _tool()
    client = FakeS3(_objects())

    result = tool.copy_role(role="public", manifest_dir=tmp_path, dry_run=False, client=client)

    assert result == {"copied": 2, "skipped": 0}
    assert set(client.objects["findme-photo-public-media-b1g2qttg"]) == {
        "covers/event.jpg",
        "legacy/file.jpg",
    }
    assert all(call["ACL"] == "public-read" for call in client.copy_calls)
    assert all(call["MetadataDirective"] == "COPY" for call in client.copy_calls)
    assert all(call["CopySourceIfMatch"] for call in client.copy_calls)
    manifest = (tmp_path / "public.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(manifest) == 2
    assert all("ACCESS_KEY" not in line and "SECRET" not in line for line in manifest)
    assert {json.loads(line)["key"] for line in manifest} == {"covers/event.jpg", "legacy/file.jpg"}


def test_private_copy_inventories_only_durable_prefixes_and_resumes_changed_delta(
    tmp_path: Path,
) -> None:
    tool = _tool()
    client = FakeS3(_objects())

    assert tool.copy_role(role="private", manifest_dir=tmp_path, dry_run=False, client=client) == {
        "copied": 2,
        "skipped": 0,
    }
    assert set(client.objects["findme-photo-private-media-b1g2qttg"]) == {
        "originals/one",
        "derivatives/two",
    }
    client.objects["hires-staging"]["derivatives/two"] = {"Size": 23, "ETag": '"new-etag"'}
    client.objects["hires-staging"]["originals/three"] = {"Size": 29, "ETag": '"three-etag"'}
    client.copy_calls.clear()

    assert tool.copy_role(role="private", manifest_dir=tmp_path, dry_run=False, client=client) == {
        "copied": 2,
        "skipped": 1,
    }
    assert {call["Key"] for call in client.copy_calls} == {"derivatives/two", "originals/three"}
    assert client.objects["findme-photo-private-media-b1g2qttg"]["derivatives/two"]["Size"] == 23
    assert {call.get("Prefix") for call in client.list_calls} >= {"originals/", "derivatives/"}


def test_feedback_copy_requires_the_reviewed_kms_key_and_dry_run_copies_nothing(
    tmp_path: Path,
) -> None:
    tool = _tool()
    client = FakeS3(_objects())

    assert tool.copy_role(role="feedback", manifest_dir=tmp_path, dry_run=True, client=client) == {
        "copied": 0,
        "skipped": 0,
    }
    assert client.copy_calls == []
    assert not (tmp_path / "feedback.jsonl").exists()

    tool.copy_role(role="feedback", manifest_dir=tmp_path, dry_run=False, client=client)

    assert client.copy_calls[0]["ServerSideEncryption"] == "aws:kms"
    assert client.copy_calls[0]["SSEKMSKeyId"] == "abjjca35o900fng2nk6v"


def test_copy_refuses_relative_manifest_directory_nonempty_first_target_and_unknown_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _tool()
    client = FakeS3(_objects())
    monkeypatch.chdir(tmp_path)

    with pytest.raises(tool.CopyError, match="absolute"):
        tool.copy_role(role="public", manifest_dir=Path("manifests"), dry_run=False, client=client)

    client.objects["findme-photo-public-media-b1g2qttg"]["unexpected"] = {
        "Size": 1,
        "ETag": '"unexpected"',
    }
    with pytest.raises(tool.CopyError, match="empty"):
        tool.copy_role(role="public", manifest_dir=tmp_path, dry_run=False, client=client)

    private_target = FakeS3(_objects())
    private_target.objects["findme-photo-private-media-b1g2qttg"][
        "processing-pending/previews/not-durable.jpg"
    ] = {"Size": 1, "ETag": '"unexpected"'}
    with pytest.raises(tool.CopyError, match="empty"):
        tool.copy_role(
            role="private",
            manifest_dir=tmp_path / "private",
            dry_run=False,
            client=private_target,
        )

    with pytest.raises(tool.CopyError, match="role"):
        tool.copy_role(role="other", manifest_dir=tmp_path, dry_run=False, client=client)


def test_partial_concurrent_copy_journals_success_and_retry_reaches_exact_equality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _tool()
    client = FakeS3(_objects())
    client.fail_keys.add("derivatives/two")
    durable_calls: list[int] = []
    monkeypatch.setattr(tool.os, "fsync", durable_calls.append)

    with pytest.raises(tool.CopyError, match="conditional"):
        tool.copy_role(role="private", manifest_dir=tmp_path, dry_run=False, client=client)

    manifest = (tmp_path / "private.jsonl").read_text(encoding="utf-8")
    assert "originals/one" in manifest
    assert durable_calls
    assert set(client.objects["findme-photo-private-media-b1g2qttg"]) == {"originals/one"}
    client.fail_keys.clear()

    assert tool.copy_role(role="private", manifest_dir=tmp_path, dry_run=False, client=client) == {
        "copied": 1,
        "skipped": 1,
    }
    assert set(client.objects["findme-photo-private-media-b1g2qttg"]) == {
        "originals/one",
        "derivatives/two",
    }


def test_resume_rejects_an_unexpected_private_target_key_even_with_a_valid_manifest(
    tmp_path: Path,
) -> None:
    tool = _tool()
    client = FakeS3(_objects())
    client.objects["findme-photo-private-media-b1g2qttg"].update(
        {
            "originals/one": {"Size": 11, "ETag": '"original-etag"'},
            "processing-pending/previews/unexpected": {"Size": 1, "ETag": '"unexpected"'},
        }
    )
    _write_manifest(
        tmp_path / "private.jsonl",
        _private_record(key="originals/one", size=11, etag='"original-etag"'),
    )

    with pytest.raises(tool.CopyError, match="unexpected"):
        tool.copy_role(role="private", manifest_dir=tmp_path, dry_run=False, client=client)

    assert client.copy_calls == []


@pytest.mark.parametrize(
    "target_key,record",
    [
        ("processing-pending/previews/unexpected", None),
        (
            "originals/one",
            _private_record(key="derivatives/two", size=13, etag='"derivative-etag"'),
        ),
        ("originals/one", _private_record(key="originals/one", size=11, etag='"stale-etag"')),
    ],
)
def test_resume_rejects_unexpected_target_forged_manifest_and_stale_source_relationship(
    tmp_path: Path, target_key: str, record: dict[str, object] | None
) -> None:
    tool = _tool()
    client = FakeS3(_objects())
    client.objects["findme-photo-private-media-b1g2qttg"][target_key] = {
        "Size": 11,
        "ETag": '"original-etag"',
    }
    if record is not None:
        _write_manifest(tmp_path / "private.jsonl", record)

    with pytest.raises(tool.CopyError):
        tool.copy_role(role="private", manifest_dir=tmp_path, dry_run=False, client=client)

    assert client.copy_calls == []


def test_resume_rejects_truncated_manifest_and_target_etag_mismatch_even_when_size_matches(
    tmp_path: Path,
) -> None:
    tool = _tool()
    client = FakeS3(_objects())
    client.objects["findme-photo-private-media-b1g2qttg"].update(
        {
            "originals/one": {"Size": 11, "ETag": '"original-etag"'},
            "derivatives/two": {"Size": 13, "ETag": '"derivative-etag"'},
        }
    )
    _write_manifest(
        tmp_path / "private.jsonl",
        _private_record(key="originals/one", size=11, etag='"original-etag"'),
    )

    with pytest.raises(tool.CopyError):
        tool.copy_role(role="private", manifest_dir=tmp_path, dry_run=False, client=client)

    client.objects["findme-photo-private-media-b1g2qttg"].pop("derivatives/two")
    client.objects["findme-photo-private-media-b1g2qttg"]["originals/one"] = {
        "Size": 11,
        "ETag": '"target-etag"',
    }
    with pytest.raises(tool.CopyError):
        tool.copy_role(role="private", manifest_dir=tmp_path, dry_run=False, client=client)


def test_inventories_the_complete_target_before_first_copy_and_bounds_concurrency(
    tmp_path: Path,
) -> None:
    tool = _tool()
    objects = _objects()
    objects["project-storage-dev-2026"].update(
        {f"extra/{index}.jpg": {"Size": index, "ETag": f'"etag-{index}"'} for index in range(12)}
    )
    client = FakeS3(objects)

    tool.copy_role(role="public", manifest_dir=tmp_path, dry_run=False, client=client)

    first_copy = next(
        index for index, (kind, _request) in enumerate(client.operations) if kind == "copy"
    )
    target_lists = [
        request
        for kind, request in client.operations[:first_copy]
        if kind == "list" and request["Bucket"] == "findme-photo-public-media-b1g2qttg"
    ]
    assert target_lists == [{"Bucket": "findme-photo-public-media-b1g2qttg"}]
    assert 1 < client.peak_copies <= tool._MAX_WORKERS


def test_pinned_client_reads_only_the_private_projection_and_ignores_hostile_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tool = _tool()
    environment = tmp_path / "environment.env"
    sentinel_access = "projected-access-sentinel"
    sentinel_secret = "projected-secret-sentinel"
    environment.write_text(
        f'MEDIA_S3_ACCESS_KEY_ID="{sentinel_access}"\n'
        f'MEDIA_S3_SECRET_ACCESS_KEY="{sentinel_secret}"\n',
        encoding="utf-8",
    )
    environment.chmod(0o600)
    monkeypatch.setenv("FINDME_ENV_FILE", str(environment))
    monkeypatch.setenv("MEDIA_S3_ACCESS_KEY_ID", "inherited-access-must-not-be-used")
    monkeypatch.setenv("MEDIA_S3_SECRET_ACCESS_KEY", "inherited-secret-must-not-be-used")
    monkeypatch.setenv("MEDIA_S3_ENDPOINT_URL", "https://storage.attacker.example")
    monkeypatch.setenv("MEDIA_S3_REGION", "attacker-region")
    captured: dict[str, object] = {}

    def client(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(tool.boto3, "client", client)
    tool._client(tool._role_config("public"))

    assert captured["endpoint_url"] == "https://storage.yandexcloud.net"
    assert captured["region_name"] == "ru-central1"
    assert captured["aws_access_key_id"] == sentinel_access
    assert captured["aws_secret_access_key"] == sentinel_secret
    assert sentinel_access not in capsys.readouterr().out
    assert sentinel_secret not in capsys.readouterr().err
    assert stat.S_IMODE(environment.stat().st_mode) == 0o600
    assert os.environ["MEDIA_S3_ACCESS_KEY_ID"] != captured["aws_access_key_id"]


def test_rejects_a_projection_file_without_the_resolver_private_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = _tool()
    environment = tmp_path / "environment.env"
    environment.write_text('MEDIA_S3_ACCESS_KEY_ID="key"\n', encoding="utf-8")
    environment.chmod(0o700)
    monkeypatch.setenv("FINDME_ENV_FILE", str(environment))

    with pytest.raises(tool.CopyError, match="invalid"):
        tool._projected_values()


def test_rejects_retired_preview_prefix_from_manifest(tmp_path: Path) -> None:
    tool = _tool()
    _write_manifest(
        tmp_path / "private.jsonl",
        _private_record(
            key="processing-staging/previews/00000000-0000-0000-0000-000000000000/preview-small-v1.jpg",
            size=1,
            etag='"old"',
        ),
    )

    with pytest.raises(tool.CopyError, match="prefix"):
        tool.copy_role(
            role="private", manifest_dir=tmp_path, dry_run=False, client=FakeS3(_objects())
        )


def test_rejects_a_final_target_inventory_mismatch(tmp_path: Path) -> None:
    tool = _tool()

    class MismatchingTarget(FakeS3):
        corrupted = False

        def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
            if (
                not self.corrupted
                and self.copy_calls
                and kwargs["Bucket"] == "findme-photo-public-media-b1g2qttg"
            ):
                self.objects[kwargs["Bucket"]]["covers/event.jpg"]["ETag"] = '"mismatched"'
                self.corrupted = True
            return super().list_objects_v2(**kwargs)

    with pytest.raises(tool.CopyError, match="manifest"):
        tool.copy_role(
            role="public",
            manifest_dir=tmp_path,
            dry_run=False,
            client=MismatchingTarget(_objects()),
        )


def test_rejects_a_successful_copy_response_that_omits_a_target_key(tmp_path: Path) -> None:
    tool = _tool()

    class OmittingTarget(FakeS3):
        def copy_object(self, **kwargs: Any) -> dict[str, Any]:
            response = super().copy_object(**kwargs)
            if kwargs["Key"] == "legacy/file.jpg":
                self.objects[kwargs["Bucket"]].pop(kwargs["Key"])
            return response

    with pytest.raises(tool.CopyError, match="key/size"):
        tool.copy_role(
            role="public",
            manifest_dir=tmp_path,
            dry_run=False,
            client=OmittingTarget(_objects()),
        )


@pytest.mark.parametrize(
    "argument",
    ["--source-bucket", "--target-bucket", "--prefix", "--endpoint", "--kms-key", "--access-key"],
)
def test_cli_rejects_unreviewed_copy_coordinates_without_echoing_values(
    tmp_path: Path, argument: str
) -> None:
    sentinel = "credential-sentinel-value"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/copy-object-storage-bucket.py"),
            "public",
            "--manifest-dir",
            str(tmp_path),
            argument,
            sentinel,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid arguments" in result.stderr
    assert sentinel not in result.stdout
    assert sentinel not in result.stderr


def test_projected_client_round_trips_resolver_escaped_credentials_without_disclosure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    tool = _tool()
    resolver = _resolver()
    access_key = 'access with = "quotes" $dollar\\tail\nnew\rreturn\ttab'
    secret_key = 'secret with = "quotes" $dollar\\tail\nnew\rreturn\ttab'
    environment = tmp_path / "environment.env"
    environment.write_text(
        "MEDIA_S3_ACCESS_KEY_ID=" + resolver._encode_dotenv(access_key) + "\n"
        "MEDIA_S3_SECRET_ACCESS_KEY=" + resolver._encode_dotenv(secret_key) + "\n",
        encoding="utf-8",
    )
    environment.chmod(0o600)
    monkeypatch.setenv("FINDME_ENV_FILE", str(environment))
    client = FakeS3(_objects())
    captured: dict[str, object] = {}

    def projected_client(*_args: object, **kwargs: object) -> FakeS3:
        captured.update(kwargs)
        return client

    monkeypatch.setattr(tool.boto3, "client", projected_client)
    assert tool.copy_role(role="public", manifest_dir=tmp_path, dry_run=False) == {
        "copied": 2,
        "skipped": 0,
    }

    streams = capsys.readouterr()
    manifest = (tmp_path / "public.jsonl").read_text(encoding="utf-8")
    assert captured["aws_access_key_id"] == access_key
    assert captured["aws_secret_access_key"] == secret_key
    for sentinel in (access_key, secret_key):
        assert sentinel not in streams.out
        assert sentinel not in streams.err
        assert sentinel not in manifest

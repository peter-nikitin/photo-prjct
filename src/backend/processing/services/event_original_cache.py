"""Read-only, resumable private cache for one event's original image inventory."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol, TypedDict, cast

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from picflow.models import Event, Photo


class CacheError(RuntimeError):
    """A sanitized failure while materializing the private local cache."""


@dataclass(frozen=True)
class InventoryEntry:
    photo_id: str
    filename: str
    key: str
    size: int
    content_type: str


@dataclass(frozen=True)
class ObjectMetadata:
    size: int
    etag: str
    content_type: str


class ReadableBody(Protocol):
    def read(self, size: int | None = None) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ObjectResponse:
    size: int
    etag: str
    content_type: str
    body: ReadableBody


class ReadOnlyObjectStorage(Protocol):
    def head(self, *, key: str, if_match: str | None = None) -> ObjectMetadata: ...

    def get(self, *, key: str, if_match: str) -> ObjectResponse: ...


@dataclass(frozen=True)
class CacheResult:
    event_slug: str
    downloaded_count: int
    reused_count: int
    unresolved_count: int


class ManifestFile(TypedDict):
    photo_id: str
    filename: str
    key: str
    size: int
    content_type: str
    etag: str
    sha256: str | None


class Manifest(TypedDict):
    version: int
    event: dict[str, str]
    files: list[ManifestFile]
    inventory_hash: str
    complete: bool
    unresolved_count: int
    manifest_hash: str


class S3EventOriginalStorage:
    """The cache's deliberately small, read-only Object Storage adapter."""

    def __init__(self, client: _S3Client | None = None) -> None:
        self._bucket = settings.PRIVATE_MEDIA_S3_BUCKET
        self._client = (
            client
            if client is not None
            else boto3.client(
                "s3",
                aws_access_key_id=settings.PRIVATE_MEDIA_S3_ACCESS_KEY_ID,
                aws_secret_access_key=settings.PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY,
                endpoint_url=settings.PRIVATE_MEDIA_S3_ENDPOINT_URL,
                region_name=settings.PRIVATE_MEDIA_S3_REGION,
                config=Config(signature_version="s3v4"),
            )
        )

    def head(self, *, key: str, if_match: str | None = None) -> ObjectMetadata:
        kwargs: dict[str, Any] = {"Bucket": self._bucket, "Key": key}
        if if_match is not None:
            kwargs["IfMatch"] = if_match
        try:
            return _metadata_from_response(self._client.head_object(**kwargs))
        except (BotoCoreError, ClientError, KeyError, TypeError, ValueError):
            raise CacheError("private object read failed") from None

    def get(self, *, key: str, if_match: str) -> ObjectResponse:
        body: ReadableBody | None = None
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key, IfMatch=if_match)
            body = response["Body"]
            if not all(
                (callable(getattr(body, "read", None)), callable(getattr(body, "close", None)))
            ):
                raise TypeError
            metadata = _metadata_from_response(response)
            return ObjectResponse(
                size=metadata.size,
                etag=metadata.etag,
                content_type=metadata.content_type,
                body=cast(ReadableBody, body),
            )
        except (BotoCoreError, ClientError, KeyError, TypeError, ValueError):
            _close_response_body(body)
            raise CacheError("private object read failed") from None


class _S3Client(Protocol):
    def head_object(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_object(self, **kwargs: Any) -> dict[str, Any]: ...


def select_event(*, event_slug: str | None = None, latest_published: bool = False) -> Event:
    if (event_slug is None) == (not latest_published):
        raise CacheError("select exactly one published event")
    if latest_published:
        event = Event.objects.published().order_by("-start_date", "-end_date", "slug").first()
        if event is None:
            raise CacheError("no published event is available")
        return event
    try:
        event = Event.objects.get(slug=event_slug)
    except Event.DoesNotExist:
        raise CacheError("event does not exist") from None
    if event.publication_status != Event.PublicationStatus.PUBLISHED:
        raise CacheError("event must be published")
    return event


def build_inventory(event: Event, photos: Iterable[object]) -> list[InventoryEntry]:
    """Freeze safe local names from current database metadata, never upload filenames."""
    entries: list[InventoryEntry] = []
    photo_ids: set[str] = set()
    filenames: set[str] = set()
    for photo in photos:
        photo_id = getattr(photo, "id", None)
        if not isinstance(photo_id, str) or not _safe_component(photo_id):
            raise CacheError("photo does not have a safe generated identifier")
        if photo_id in photo_ids:
            raise CacheError("duplicate photo identifier in event inventory")
        if getattr(photo, "src", None):
            raise CacheError("event has a legacy photo without private original metadata")
        key = getattr(photo, "original_key", None)
        size = getattr(photo, "original_size", None)
        content_type = getattr(photo, "original_content_type", None)
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 1
        ):
            raise CacheError("photo has incomplete original metadata")
        if not isinstance(content_type, str):
            raise CacheError("photo has an unsupported original content type")
        extensions: dict[str, str] = {"image/jpeg": "jpg", "image/png": "png"}
        extension = extensions.get(content_type)
        if extension is None:
            raise CacheError("photo has an unsupported original content type")
        filename = f"photo-{photo_id}.{extension}"
        if filename.casefold() in filenames:
            raise CacheError("duplicate safe local filename in event inventory")
        photo_ids.add(photo_id)
        filenames.add(filename.casefold())
        entries.append(
            InventoryEntry(
                photo_id=photo_id,
                filename=filename,
                key=key,
                size=size,
                content_type=content_type,
            )
        )
    entries.sort(key=lambda entry: entry.photo_id)
    if not entries:
        raise CacheError("event has no eligible private originals")
    return entries


class EventOriginalCache:
    def __init__(self, *, storage: ReadOnlyObjectStorage) -> None:
        self._storage = storage

    def cache(self, *, event: Event, output_root: Path) -> CacheResult:
        if getattr(event, "publication_status", None) != Event.PublicationStatus.PUBLISHED:
            raise CacheError("event must be published")
        event_root = _event_root(output_root, event.slug)
        _reject_git_checkout_root(event_root.parent)
        inventory = build_inventory(event, Photo.objects.filter(event=event).order_by("id"))
        _prepare_event_root(event_root)
        _recover_manifest_partial(event_root)
        manifest_path = event_root / "manifest.json"
        originals = event_root / "originals"

        if manifest_path.exists() or manifest_path.is_symlink():
            manifest = _load_manifest(manifest_path, event=event, inventory=inventory)
        else:
            _validate_local_structure(event_root, expected_filenames=set())
            files: list[ManifestFile] = []
            for entry in inventory:
                remote = self._storage.head(key=entry.key)
                _assert_remote_matches(entry, remote)
                files.append(
                    {
                        "photo_id": entry.photo_id,
                        "filename": entry.filename,
                        "key": entry.key,
                        "size": entry.size,
                        "content_type": entry.content_type,
                        "etag": remote.etag,
                        "sha256": None,
                    }
                )
            manifest = _new_manifest(event, files)
            _write_manifest(manifest_path, manifest)

        _validate_local_structure(
            event_root,
            expected_filenames={entry["filename"] for entry in manifest["files"]},
        )
        downloaded_count = 0
        reused_count = 0
        for manifest_entry in manifest["files"]:
            target = originals / manifest_entry["filename"]
            partial = target.with_name(f".{target.name}.partial")
            try:
                if partial.exists():
                    partial.unlink()
            except OSError:
                raise CacheError("cache directory is invalid") from None
            if _verified_local_file(target, manifest_entry):
                reused_count += 1
                continue
            manifest_entry["sha256"] = None
            manifest["complete"] = False
            manifest["unresolved_count"] = _unresolved_count(manifest["files"])
            _write_manifest(manifest_path, manifest)
            inventory_entry = InventoryEntry(
                photo_id=manifest_entry["photo_id"],
                filename=manifest_entry["filename"],
                key=manifest_entry["key"],
                size=manifest_entry["size"],
                content_type=manifest_entry["content_type"],
            )
            remote = self._storage.head(key=inventory_entry.key, if_match=manifest_entry["etag"])
            _assert_remote_matches(inventory_entry, remote, expected_etag=manifest_entry["etag"])
            digest = self._download(inventory_entry, etag=manifest_entry["etag"], target=target)
            manifest_entry["sha256"] = digest
            manifest["unresolved_count"] = _unresolved_count(manifest["files"])
            manifest["complete"] = manifest["unresolved_count"] == 0
            _write_manifest(manifest_path, manifest)
            downloaded_count += 1
        return CacheResult(
            event_slug=event.slug,
            downloaded_count=downloaded_count,
            reused_count=reused_count,
            unresolved_count=_unresolved_count(manifest["files"]),
        )

    def _download(self, entry: InventoryEntry, *, etag: str, target: Path) -> str:
        partial = target.with_name(f".{target.name}.partial")
        try:
            if partial.exists() or partial.is_symlink():
                if partial.is_symlink():
                    raise CacheError("cache contains a symlink")
                partial.unlink()
        except OSError:
            raise CacheError("cache directory is invalid") from None
        body: ReadableBody | None = None
        primary_failure = False
        try:
            response = self._storage.get(key=entry.key, if_match=etag)
            body = response.body
            _assert_remote_matches(entry, response, expected_etag=etag)
            digest = hashlib.sha256()
            received = 0
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as destination:
                while True:
                    chunk = body.read(64 * 1024)
                    if not isinstance(chunk, bytes):
                        raise TypeError
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > entry.size:
                        raise CacheError("private object changed during download")
                    digest.update(chunk)
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if received != entry.size:
                raise CacheError("private object changed during download")
            os.replace(partial, target)
            return digest.hexdigest()
        except CacheError:
            primary_failure = True
            raise
        except (OSError, TypeError, ValueError):
            primary_failure = True
            raise CacheError("private object download failed") from None
        finally:
            close_error = False
            if body is not None:
                try:
                    body.close()
                except (OSError, TypeError, ValueError):
                    close_error = True
            try:
                if partial.exists() or partial.is_symlink():
                    if partial.is_symlink():
                        raise CacheError("cache contains a symlink")
                    partial.unlink()
            except OSError:
                if not primary_failure:
                    raise CacheError("cache directory is invalid") from None
            if close_error and not primary_failure:
                raise CacheError("private object download failed") from None


def _metadata_from_response(response: dict[str, Any]) -> ObjectMetadata:
    size = response["ContentLength"]
    content_type = response["ContentType"]
    etag = response["ETag"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise TypeError
    if not isinstance(content_type, str) or content_type not in {"image/jpeg", "image/png"}:
        raise TypeError
    if (
        not isinstance(etag, str)
        or not etag.startswith('"')
        or not etag.endswith('"')
        or len(etag) < 3
    ):
        raise TypeError
    return ObjectMetadata(size=size, etag=etag, content_type=content_type)


def _assert_remote_matches(
    entry: InventoryEntry,
    remote: ObjectMetadata | ObjectResponse,
    *,
    expected_etag: str | None = None,
) -> None:
    if (
        remote.size != entry.size
        or remote.content_type != entry.content_type
        or not isinstance(remote.etag, str)
        or not remote.etag
        or (expected_etag is not None and remote.etag != expected_etag)
    ):
        raise CacheError("private object changed")


def _safe_component(value: str) -> bool:
    return (
        bool(value)
        and value not in {".", ".."}
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
    )


def _event_root(output_root: Path, slug: object) -> Path:
    if not isinstance(slug, str) or not _safe_component(slug):
        raise CacheError("unsafe event output path")
    try:
        root = Path(output_root).expanduser().resolve()
        candidate = root / slug
        if candidate.parent != root or not candidate.resolve().is_relative_to(root):
            raise CacheError("unsafe event output path")
    except (OSError, RuntimeError):
        raise CacheError("unsafe event output path") from None
    return candidate


def _reject_git_checkout_root(root: Path) -> None:
    for candidate in (root, *root.parents):
        marker = candidate / ".git"
        if marker.is_dir() or marker.is_file():
            raise CacheError("cache output must be outside a Git checkout")


def _prepare_event_root(event_root: Path) -> None:
    try:
        output_root = event_root.parent
        output_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        os.chmod(output_root, 0o700)
        if event_root.exists():
            if event_root.is_symlink():
                raise CacheError("cache contains a symlink")
            if not event_root.is_dir():
                raise CacheError("cache directory is invalid")
        else:
            event_root.mkdir(mode=0o700)
        os.chmod(event_root, 0o700)
        originals = event_root / "originals"
        if originals.exists():
            if originals.is_symlink():
                raise CacheError("cache contains a symlink")
            if not originals.is_dir():
                raise CacheError("cache directory is invalid")
        else:
            originals.mkdir(mode=0o700)
        os.chmod(originals, 0o700)
    except OSError:
        raise CacheError("cache directory is invalid") from None


def _recover_manifest_partial(event_root: Path) -> None:
    temporary = event_root / ".manifest.json.partial"
    try:
        if temporary.exists() or temporary.is_symlink():
            if temporary.is_symlink() or not temporary.is_file():
                raise CacheError("cache directory is invalid")
            temporary.unlink()
    except OSError:
        raise CacheError("cache manifest cleanup failed") from None


def _new_manifest(event: Event, files: list[ManifestFile]) -> Manifest:
    manifest = cast(
        Manifest,
        {
            "version": 1,
            "event": {"id": str(event.pk), "slug": event.slug},
            "files": files,
            "inventory_hash": _inventory_hash(files),
            "complete": False,
            "unresolved_count": len(files),
            "manifest_hash": "",
        },
    )
    manifest["manifest_hash"] = _manifest_hash(manifest)
    return manifest


def _load_manifest(path: Path, *, event: Event, inventory: list[InventoryEntry]) -> Manifest:
    if path.is_symlink():
        raise CacheError("cache contains a symlink")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("manifest_hash") != _manifest_hash(
            manifest
        ):
            raise ValueError
        if manifest.get("version") != 1 or manifest.get("event") != {
            "id": str(event.pk),
            "slug": event.slug,
        }:
            raise ValueError
        files = manifest.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError
        expected = [asdict(entry) for entry in inventory]
        if [
            {key: item.get(key) for key in ("photo_id", "filename", "key", "size", "content_type")}
            for item in files
            if isinstance(item, dict)
        ] != expected:
            raise ValueError
        if any(
            not isinstance(item, dict)
            or set(item)
            != {"photo_id", "filename", "key", "size", "content_type", "etag", "sha256"}
            or not isinstance(item["etag"], str)
            or not item["etag"]
            or (item["sha256"] is not None and not _is_sha256(item["sha256"]))
            for item in files
        ):
            raise ValueError
        if manifest.get("inventory_hash") != _inventory_hash(files):
            raise ValueError
        unresolved = _unresolved_count(files)
        if manifest.get("unresolved_count") != unresolved or manifest.get("complete") is not (
            unresolved == 0
        ):
            raise ValueError
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise CacheError("cache manifest is invalid") from None
    return cast(Manifest, manifest)


def _inventory_hash(files: Iterable[ManifestFile]) -> str:
    inventory = [
        {
            "photo_id": item["photo_id"],
            "filename": item["filename"],
            "key": item["key"],
            "size": item["size"],
            "content_type": item["content_type"],
            "etag": item["etag"],
        }
        for item in files
    ]
    return hashlib.sha256(_canonical_json(inventory)).hexdigest()


def _manifest_hash(manifest: Manifest | dict[str, object]) -> str:
    payload = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _write_manifest(path: Path, manifest: Manifest) -> None:
    manifest["manifest_hash"] = _manifest_hash(manifest)
    temporary_path = path.with_name(".manifest.json.partial")
    try:
        descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            json.dump(manifest, temporary, sort_keys=True, separators=(",", ":"))
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    except OSError:
        raise CacheError("cache manifest could not be published") from None
    finally:
        try:
            if temporary_path.exists():
                temporary_path.unlink()
        except OSError:
            pass


def _validate_local_structure(event_root: Path, *, expected_filenames: set[str]) -> None:
    allowed_root = {"manifest.json", "originals"}
    try:
        for child in event_root.iterdir():
            if child.is_symlink():
                raise CacheError("cache contains a symlink")
            if child.name not in allowed_root:
                raise CacheError("cache contains an unexpected entry")
        originals = event_root / "originals"
        expected_partial_filenames = {f".{name}.partial" for name in expected_filenames}
        for child in originals.iterdir():
            if child.is_symlink():
                raise CacheError("cache contains a symlink")
            if child.is_dir():
                raise CacheError("cache contains a nested directory")
            if child.name not in expected_filenames | expected_partial_filenames:
                raise CacheError("cache contains an unexpected entry")
    except OSError:
        raise CacheError("cache directory is invalid") from None


def _verified_local_file(path: Path, entry: ManifestFile) -> bool:
    expected_hash = entry["sha256"]
    if expected_hash is None or path.is_symlink() or not path.is_file():
        return False
    try:
        if path.stat().st_size != entry["size"]:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(64 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected_hash
    except OSError:
        return False


def _unresolved_count(files: Iterable[ManifestFile]) -> int:
    return sum(1 for entry in files if entry["sha256"] is None)


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _close_response_body(body: object | None) -> None:
    close = getattr(body, "close", None)
    if callable(close):
        try:
            close()
        except (OSError, TypeError, ValueError):
            pass

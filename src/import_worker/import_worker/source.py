"""Direct public Disk resources and byte-authoritative JPEG validation."""

from __future__ import annotations

import hashlib
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from PIL import Image, UnidentifiedImageError

from import_worker.contracts import Config
from import_worker.transport import Transport, TransportError, validate_source_url


class SourceError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VerifiedJPEG:
    sha256: str
    size: int
    geometry: tuple[int, int]


class DiskSource:
    def __init__(self, transport: Transport):
        self.transport = transport

    def pages(
        self, key: str, *, heartbeat: Callable[[], None] = lambda: None
    ) -> Iterator[tuple[str, list[dict[str, Any]]]]:
        # Submission persists the validated /d/ share token; Disk requires the full URL.
        key = "https://disk.yandex.ru/d/" + key
        offset = 0
        canonical = None
        total = None
        while True:
            payload = self.transport.json(
                "GET",
                self._url("", public_key=key, limit=Config.page_size, offset=offset, sort="name"),
                audience="source",
                heartbeat=heartbeat,
            )
            if payload.get("type") != "dir":
                raise SourceError("source_not_directory")
            current_key = payload.get("public_key")
            embedded = payload.get("_embedded")
            if (
                not isinstance(current_key, str)
                or not current_key
                or len(current_key) > 512
                or not isinstance(embedded, dict)
            ):
                raise SourceError("source_unavailable")
            current_total = embedded.get("total")
            items = embedded.get("items")
            if (
                type(current_total) is not int
                or current_total < 0
                or not isinstance(items, list)
                or len(items) > Config.page_size
                or embedded.get("offset") != offset
            ):
                raise SourceError("manifest_changed")
            if current_total > Config.max_entries:
                raise SourceError("manifest_too_large")
            if canonical is not None and (canonical != current_key or total != current_total):
                raise SourceError("manifest_changed")
            canonical, total = current_key, current_total
            if offset + len(items) > total or (not items and offset < total):
                raise SourceError("manifest_changed")
            entries = [self._entry(item) for item in items]
            yield canonical, entries
            offset += len(entries)
            if offset == total:
                return

    @staticmethod
    def _entry(item: object) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise SourceError("manifest_changed")
        path, name = item.get("path"), item.get("name")
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or len(path) > 1024
            or not isinstance(name, str)
            or not name
            or len(name) > 255
        ):
            raise SourceError("manifest_changed")
        kind = "directory" if item.get("type") == "dir" else "unsupported"
        if item.get("type") == "file" and name.lower().endswith((".jpg", ".jpeg")):
            kind = "jpeg"
        size = item.get("size")
        if size is not None and (type(size) is not int or size < 0 or size >= 2**63):
            raise SourceError("manifest_changed")
        if kind == "jpeg" and (size is None or size == 0):
            raise SourceError("manifest_changed")
        hashes = {}
        for field, length in (("sha256", 64), ("md5", 32)):
            value = item.get(field) or ""
            if not isinstance(value, str) or (
                value
                and (len(value) != length or any(c not in "0123456789abcdefABCDEF" for c in value))
            ):
                raise SourceError("manifest_changed")
            hashes[field] = value.lower() if value else None
        version = item.get("modified") or ""
        if not isinstance(version, str) or len(version) > 255:
            raise SourceError("manifest_changed")
        return dict(path=path, name=name, kind=kind, size=size, version=version, **hashes)

    def download_url(
        self, key: str, path: str, *, heartbeat: Callable[[], None] = lambda: None
    ) -> str:
        payload = self.transport.json(
            "GET",
            self._url("/download", public_key=key, path=path),
            audience="source",
            heartbeat=heartbeat,
        )
        href = payload.get("href")
        if (
            payload.get("method") != "GET"
            or payload.get("templated") is not False
            or not isinstance(href, str)
        ):
            raise SourceError("source_unavailable")
        try:
            validate_source_url(href)
        except TransportError:
            raise SourceError("source_unavailable") from None
        return href

    @staticmethod
    def _url(suffix: str, **query: object) -> str:
        return (
            "https://cloud-api.yandex.net/v1/disk/public/resources"
            + suffix
            + "?"
            + urlencode(query)
        )


def validate_jpeg(path: Path, metadata: dict[str, Any]) -> VerifiedJPEG:
    size = path.stat().st_size
    if size > Config.max_bytes:
        raise SourceError("file_too_large")
    if size != metadata["size"]:
        raise SourceError("source_changed")
    sha256, md5 = hashlib.sha256(), hashlib.md5()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            sha256.update(chunk)
            md5.update(chunk)
    for field, digest in (("sha256", sha256), ("md5", md5)):
        if metadata.get(field) and metadata[field].lower() != digest.hexdigest():
            raise SourceError("source_changed")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as photo:
                if photo.format != "JPEG":
                    raise SourceError("invalid_jpeg")
                photo.verify()
            with Image.open(path) as photo:
                photo.load()
                width, height = photo.size
                if photo.getexif().get(274) in {5, 6, 7, 8}:
                    width, height = height, width
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise SourceError("invalid_jpeg") from None
    return VerifiedJPEG(sha256.hexdigest(), size, (width, height))

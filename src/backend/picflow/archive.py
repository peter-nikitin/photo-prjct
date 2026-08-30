from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, Self
from zipfile import ZIP_STORED, ZipFile

from ingestion.storage import (
    ObjectChanged,
    ObjectMismatch,
    ObjectMissing,
    OpenedObject,
    ReadableBody,
    StorageUnavailable,
)

ZIP_STREAM_CHUNK_SIZE = 64 * 1024
_CONTENT_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/png"})

logger = logging.getLogger(__name__)


class FinalObjectStorage(Protocol):
    def open_final(self, *, key: str) -> OpenedObject: ...


class ArchiveSourceMissing(Exception):
    """An authorized original is missing or no longer has its expected identity."""


class ArchiveSourceUnavailable(Exception):
    """The original cannot currently be read from object storage."""


@dataclass(frozen=True)
class ArchiveEntry:
    """One already-authorized original that belongs in an archive."""

    photo_id: str
    original_key: str
    original_size: int
    original_content_type: Literal["image/jpeg", "image/png"]
    on_source_failure: Callable[[], None] | None = None


class _BoundedSink:
    def __init__(self) -> None:
        self._chunks: deque[bytes] = deque()
        self._position = 0

    def write(self, data: bytes) -> int:
        self._position += len(data)
        for start in range(0, len(data), ZIP_STREAM_CHUNK_SIZE):
            self._chunks.append(data[start : start + ZIP_STREAM_CHUNK_SIZE])
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        return None

    def close(self) -> None:
        return None

    def seekable(self) -> Literal[False]:
        return False

    def drain(self) -> Iterator[bytes]:
        while self._chunks:
            yield self._chunks.popleft()

    def discard(self) -> None:
        self._chunks.clear()


class PreparedZipArchive(Iterator[bytes]):
    """A prepared, single-use ZIP body that closes its storage body on disconnect."""

    def __init__(
        self,
        *,
        entries: tuple[ArchiveEntry, ...],
        storage: FinalObjectStorage,
        first_opened: OpenedObject,
        first_prefix: bytes,
    ) -> None:
        self._entries = entries
        self._storage = storage
        self._first_opened = first_opened
        self._first_prefix = first_prefix
        self._current_body: ReadableBody | None = first_opened.body
        self._stream: Iterator[bytes] | None = None
        self._closed = False

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> bytes:
        if self._closed:
            raise StopIteration
        if self._stream is None:
            self._stream = self._stream_zip()
        try:
            return next(self._stream)
        except StopIteration:
            self._closed = True
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._stream is not None:
            close = getattr(self._stream, "close", None)
            if callable(close):
                close()
        self._close_current_body()

    def _stream_zip(self) -> Iterator[bytes]:
        sink = _BoundedSink()
        archive = ZipFile(sink, mode="w", compression=ZIP_STORED, allowZip64=True)
        completed = False
        streamed_bytes = 0
        try:
            for index, entry in enumerate(self._entries):
                opened = self._first_opened if index == 0 else _open_entry(self._storage, entry)
                self._current_body = opened.body
                try:
                    member_name = _member_name(entry)
                    with archive.open(member_name, mode="w", force_zip64=True) as member:
                        prefix = self._first_prefix if index == 0 else b""
                        copied = len(prefix)
                        if prefix:
                            member.write(prefix)
                            streamed_bytes += len(prefix)
                            yield from sink.drain()
                        if index == 0 and not prefix:
                            data = b""
                        else:
                            data = _read_source(entry, opened.body)
                        while data:
                            copied += len(data)
                            if copied > entry.original_size:
                                raise _source_missing(entry)
                            member.write(data)
                            streamed_bytes += len(data)
                            yield from sink.drain()
                            data = _read_source(entry, opened.body)
                    if copied != entry.original_size:
                        raise _source_missing(entry)
                finally:
                    self._close_current_body()
                yield from sink.drain()
            archive.close()
            completed = True
            yield from sink.drain()
            logger.info(
                "archive stream completed",
                extra={
                    "archive_outcome": "completed",
                    "archive_file_count": len(self._entries),
                    "archive_streamed_bytes": streamed_bytes,
                },
            )
        except GeneratorExit:
            logger.info(
                "archive stream interrupted",
                extra={"archive_outcome": "interrupted", "archive_file_count": len(self._entries)},
            )
            raise
        except ArchiveSourceMissing:
            sink.discard()
            logger.warning(
                "archive source failed",
                extra={
                    "archive_outcome": "source_failure",
                    "archive_file_count": len(self._entries),
                },
            )
            raise
        except ArchiveSourceUnavailable:
            sink.discard()
            logger.warning(
                "archive source unavailable",
                extra={
                    "archive_outcome": "source_failure",
                    "archive_file_count": len(self._entries),
                },
            )
            raise
        finally:
            if not completed:
                self._close_current_body()
                archive.fp = None

    def _close_current_body(self) -> None:
        if self._current_body is not None:
            body = self._current_body
            self._current_body = None
            body.close()


def prepare_zip_archive(
    *, entries: Sequence[ArchiveEntry], storage: FinalObjectStorage
) -> PreparedZipArchive:
    """Preflight the first exact original, then return its single-use streaming ZIP body."""
    prepared_entries = tuple(entries)
    _validate_entries(prepared_entries)
    first_opened = _open_entry(storage, prepared_entries[0])
    try:
        first_prefix = _read_source(prepared_entries[0], first_opened.body)
        if len(first_prefix) > prepared_entries[0].original_size:
            raise _source_missing(prepared_entries[0])
        if not first_prefix and prepared_entries[0].original_size:
            raise _source_missing(prepared_entries[0])
    except (ArchiveSourceMissing, ArchiveSourceUnavailable):
        first_opened.body.close()
        raise
    return PreparedZipArchive(
        entries=prepared_entries,
        storage=storage,
        first_opened=first_opened,
        first_prefix=first_prefix,
    )


def _validate_entries(entries: tuple[ArchiveEntry, ...]) -> None:
    if not entries:
        raise ValueError("an archive needs at least one entry")
    seen_photo_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, ArchiveEntry):
            raise ValueError("archive entries must be ArchiveEntry values")
        _member_name(entry)
        if entry.photo_id in seen_photo_ids:
            raise ValueError("archive photo IDs must be unique")
        seen_photo_ids.add(entry.photo_id)
        if not isinstance(entry.original_key, str) or not entry.original_key:
            raise ValueError("archive original key must be a non-empty string")
        if (
            isinstance(entry.original_size, bool)
            or not isinstance(entry.original_size, int)
            or entry.original_size < 0
        ):
            raise ValueError("archive original size must be a non-negative integer")
        if entry.original_content_type not in _CONTENT_TYPES:
            raise ValueError("archive original content type must be JPEG or PNG")


def _member_name(entry: ArchiveEntry) -> str:
    if (
        not isinstance(entry.photo_id, str)
        or not entry.photo_id
        or len(entry.photo_id) > 32
        or not all(
            character.isascii() and (character.isalnum() or character in "-_")
            for character in entry.photo_id
        )
    ):
        raise ValueError("archive photo ID cannot form a safe member name")
    extension = "jpg" if entry.original_content_type == "image/jpeg" else "png"
    return f"findme-photo-{entry.photo_id}.{extension}"


def _open_entry(storage: FinalObjectStorage, entry: ArchiveEntry) -> OpenedObject:
    try:
        opened = storage.open_final(key=entry.original_key)
    except (ObjectMissing, ObjectChanged, ObjectMismatch, ValueError):
        raise _source_missing(entry) from None
    except StorageUnavailable:
        raise _source_unavailable(entry) from None
    except Exception:
        raise _source_unavailable(entry) from None
    if opened.size != entry.original_size or opened.content_type != entry.original_content_type:
        opened.body.close()
        raise _source_missing(entry)
    return opened


def _read_source(entry: ArchiveEntry, body: ReadableBody) -> bytes:
    try:
        data = body.read(ZIP_STREAM_CHUNK_SIZE)
    except (ObjectMissing, ObjectChanged, ObjectMismatch, ValueError):
        raise _source_missing(entry) from None
    except Exception:
        raise _source_unavailable(entry) from None
    if not isinstance(data, bytes):
        raise _source_unavailable(entry)
    return data


def _source_missing(entry: ArchiveEntry) -> ArchiveSourceMissing:
    _notify_source_failure(entry)
    return ArchiveSourceMissing()


def _source_unavailable(entry: ArchiveEntry) -> ArchiveSourceUnavailable:
    _notify_source_failure(entry)
    return ArchiveSourceUnavailable()


def _notify_source_failure(entry: ArchiveEntry) -> None:
    if entry.on_source_failure is None:
        return
    try:
        entry.on_source_failure()
    except Exception:
        logger.error(
            "archive source failure callback failed",
            extra={"archive_outcome": "callback_failure"},
        )

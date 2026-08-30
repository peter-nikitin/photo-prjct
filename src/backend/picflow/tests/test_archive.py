from __future__ import annotations

import logging
from collections.abc import Callable
from io import BytesIO
from unittest.mock import Mock
from zipfile import ZIP_STORED, BadZipFile, ZipFile

from django.test import SimpleTestCase
from ingestion.storage import ObjectMissing, OpenedObject, StorageUnavailable

from picflow.archive import (
    ZIP_STREAM_CHUNK_SIZE,
    ArchiveEntry,
    ArchiveSourceMissing,
    ArchiveSourceUnavailable,
    prepare_zip_archive,
)


class _Body:
    def __init__(self, chunks: list[bytes | Exception]) -> None:
        self._chunks = iter(chunks)
        self.close_calls = 0

    def read(self, amt: int | None = None) -> bytes:
        result = next(self._chunks, b"")
        if isinstance(result, Exception):
            raise result
        if amt is not None:
            assert len(result) <= amt
        return result

    def close(self) -> None:
        self.close_calls += 1


class _Storage:
    def __init__(self, opened: list[OpenedObject | Exception]) -> None:
        self._opened = iter(opened)
        self.opened_keys: list[str] = []
        self._active_bodies: list[_Body] = []

    def open_final(self, *, key: str) -> OpenedObject:
        if self._active_bodies:
            assert self._active_bodies[-1].close_calls > 0
        self.opened_keys.append(key)
        opened = next(self._opened)
        if isinstance(opened, Exception):
            raise opened
        if isinstance(opened.body, _Body):
            self._active_bodies.append(opened.body)
        return opened


def _opened(body: _Body, *, size: int, content_type: str = "image/jpeg") -> OpenedObject:
    return OpenedObject(body=body, size=size, content_type=content_type)  # type: ignore[arg-type]


def _entry(
    photo_id: str,
    *,
    key: str | None = None,
    size: int = 3,
    content_type: str = "image/jpeg",
    on_source_missing: Callable[[], None] | None = None,
) -> ArchiveEntry:
    return ArchiveEntry(
        photo_id=photo_id,
        original_key=key or f"originals/{photo_id}",
        original_size=size,
        original_content_type=content_type,  # type: ignore[arg-type]
        on_source_missing=on_source_missing,
    )


class PreparedZipArchiveTests(SimpleTestCase):
    def test_writes_ordered_stored_zip64_members_with_safe_flat_names(self) -> None:
        first = _Body([b"one"])
        second = _Body([b"four"])
        storage = _Storage(
            [
                _opened(first, size=3),
                _opened(second, size=4, content_type="image/png"),
            ]
        )

        stream = prepare_zip_archive(
            entries=(
                _entry("photo-2", key="originals/private-first", size=3),
                _entry(
                    "photo-1",
                    key="originals/private-second",
                    size=4,
                    content_type="image/png",
                ),
            ),
            storage=storage,
        )
        archive_bytes = b"".join(stream)

        with ZipFile(BytesIO(archive_bytes)) as archive:
            self.assertEqual(
                archive.namelist(),
                ["findme-photo-photo-2.jpg", "findme-photo-photo-1.png"],
            )
            self.assertEqual(archive.read("findme-photo-photo-2.jpg"), b"one")
            self.assertEqual(archive.read("findme-photo-photo-1.png"), b"four")
            for member in archive.infolist():
                self.assertEqual(member.compress_type, ZIP_STORED)
                self.assertGreaterEqual(member.extract_version, 45)
        self.assertEqual(
            storage.opened_keys, ["originals/private-first", "originals/private-second"]
        )
        self.assertEqual(first.close_calls, 1)
        self.assertEqual(second.close_calls, 1)

    def test_preflights_only_the_first_source_before_iteration(self) -> None:
        first = _Body([b"one"])
        second = _Body([b"two"])
        storage = _Storage([_opened(first, size=3), _opened(second, size=3)])

        stream = prepare_zip_archive(entries=(_entry("first"), _entry("second")), storage=storage)

        self.assertEqual(storage.opened_keys, ["originals/first"])
        self.assertEqual(first.close_calls, 0)
        self.assertEqual(b"".join(stream)[0:2], b"PK")
        self.assertEqual(storage.opened_keys, ["originals/first", "originals/second"])

    def test_opens_one_source_at_a_time_and_keeps_response_chunks_bounded(self) -> None:
        first = _Body([b"a" * ZIP_STREAM_CHUNK_SIZE, b"b"])
        second = _Body([b"c" * 9])
        storage = _Storage(
            [_opened(first, size=ZIP_STREAM_CHUNK_SIZE + 1), _opened(second, size=9)]
        )

        chunks = list(
            prepare_zip_archive(
                entries=(
                    _entry("first", size=ZIP_STREAM_CHUNK_SIZE + 1),
                    _entry("second", size=9),
                ),
                storage=storage,
            )
        )

        self.assertTrue(chunks)
        self.assertTrue(all(0 < len(chunk) <= ZIP_STREAM_CHUNK_SIZE for chunk in chunks))
        self.assertEqual(first.close_calls, 1)
        self.assertEqual(second.close_calls, 1)

    def test_disconnect_closes_current_source_and_opens_no_later_source(self) -> None:
        first = _Body([b"one"])
        second = _Body([b"two"])
        storage = _Storage([_opened(first, size=3), _opened(second, size=3)])
        stream = prepare_zip_archive(entries=(_entry("first"), _entry("second")), storage=storage)

        next(stream)
        stream.close()

        self.assertEqual(first.close_calls, 1)
        self.assertEqual(storage.opened_keys, ["originals/first"])

    def test_missing_or_identity_mismatched_first_source_fails_before_streaming(self) -> None:
        missing_callback = Mock()
        with self.subTest("missing"):
            with self.assertRaises(ArchiveSourceMissing):
                prepare_zip_archive(
                    entries=(_entry("missing", on_source_missing=missing_callback),),
                    storage=_Storage([ObjectMissing()]),
                )
            missing_callback.assert_called_once_with()

        mismatch_callback = Mock()
        mismatched_body = _Body([])
        with self.subTest("mismatch"):
            with self.assertRaises(ArchiveSourceMissing):
                prepare_zip_archive(
                    entries=(_entry("mismatch", size=3, on_source_missing=mismatch_callback),),
                    storage=_Storage([_opened(mismatched_body, size=4)]),
                )
            mismatch_callback.assert_called_once_with()
            self.assertEqual(mismatched_body.close_calls, 1)

    def test_unavailable_first_source_fails_before_streaming(self) -> None:
        callback = Mock()

        with self.assertRaises(ArchiveSourceUnavailable):
            prepare_zip_archive(
                entries=(_entry("unavailable", on_source_missing=callback),),
                storage=_Storage([StorageUnavailable()]),
            )

        callback.assert_not_called()

    def test_unreadable_first_source_fails_during_preparation(self) -> None:
        callback = Mock()
        unreadable = _Body([OSError("storage read failed")])

        with self.assertRaises(ArchiveSourceUnavailable):
            prepare_zip_archive(
                entries=(_entry("unreadable", on_source_missing=callback),),
                storage=_Storage([_opened(unreadable, size=3)]),
            )

        callback.assert_not_called()
        self.assertEqual(unreadable.close_calls, 1)

    def test_callback_failure_is_logged_without_sensitive_exception_values(self) -> None:
        sensitive_token = "token-do-not-log"
        sensitive_key = "originals/private-do-not-log"
        sensitive_photo_id = "photo-do-not-log"

        def failing_callback() -> None:
            raise RuntimeError(f"{sensitive_token} {sensitive_key} {sensitive_photo_id}")

        with self.assertLogs("picflow.archive", level="ERROR") as captured:
            with self.assertRaises(ArchiveSourceMissing):
                prepare_zip_archive(
                    entries=(
                        _entry(
                            "mismatch",
                            on_source_missing=failing_callback,
                        ),
                    ),
                    storage=_Storage([_opened(_Body([]), size=4)]),
                )

        formatted_logs = "\n".join(
            logging.Formatter().format(record) for record in captured.records
        )
        for sensitive_value in (sensitive_token, sensitive_key, sensitive_photo_id):
            self.assertNotIn(sensitive_value, formatted_logs)
        self.assertTrue(all(record.exc_info is None for record in captured.records))

    def test_later_source_failure_stops_without_a_central_directory_or_later_open(self) -> None:
        first = _Body([b"one"])
        failed = _Body([OSError("storage read failed")])
        later = _Body([b"later"])
        callback = Mock()
        storage = _Storage(
            [_opened(first, size=3), _opened(failed, size=3), _opened(later, size=5)]
        )
        stream = prepare_zip_archive(
            entries=(
                _entry("first"),
                _entry("failed", on_source_missing=callback),
                _entry("later", size=5),
            ),
            storage=storage,
        )

        sent: list[bytes] = []
        with self.assertRaises(ArchiveSourceUnavailable):
            while True:
                sent.append(next(stream))

        callback.assert_not_called()
        self.assertEqual(failed.close_calls, 1)
        self.assertEqual(storage.opened_keys, ["originals/first", "originals/failed"])
        with self.assertRaises(BadZipFile):
            ZipFile(BytesIO(b"".join(sent))).infolist()

    def test_later_missing_source_invokes_only_its_missing_callback(self) -> None:
        first = _Body([b"one"])
        missing = _Body([ObjectMissing()])
        callback = Mock()
        stream = prepare_zip_archive(
            entries=(
                _entry("first"),
                _entry("missing", on_source_missing=callback),
            ),
            storage=_Storage([_opened(first, size=3), _opened(missing, size=3)]),
        )

        with self.assertRaises(ArchiveSourceMissing):
            b"".join(stream)

        callback.assert_called_once_with()

    def test_rejects_unsafe_photo_identifiers_without_touching_storage(self) -> None:
        storage = _Storage([])

        with self.assertRaises(ValueError):
            prepare_zip_archive(entries=(_entry("../secret"),), storage=storage)

        self.assertEqual(storage.opened_keys, [])

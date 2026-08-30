from __future__ import annotations

import logging
from collections.abc import Callable
from io import BytesIO
from typing import Literal
from unittest.mock import Mock
from zipfile import ZIP_STORED, BadZipFile, ZipFile

from django.test import SimpleTestCase
from ingestion.storage import ObjectMissing, OpenedObject, StorageUnavailable

from picflow.archive import (
    ZIP_STREAM_CHUNK_SIZE,
    ArchiveEntry,
    ArchiveObservation,
    ArchiveSourceMissing,
    ArchiveSourceUnavailable,
    prepare_zip_archive,
)


def _observation(
    *, context: Literal["free_result", "paid_order"] = "free_result", page: int = 1
) -> ArchiveObservation:
    return ArchiveObservation(context=context, page=page)


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


def _storage_factory(storage: _Storage) -> Callable[[], _Storage]:
    return lambda: storage


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
            storage_factory=_storage_factory(storage),
            observation=_observation(),
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

        stream = prepare_zip_archive(
            entries=(_entry("first"), _entry("second")),
            storage_factory=_storage_factory(storage),
            observation=_observation(),
        )

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
                storage_factory=_storage_factory(storage),
                observation=_observation(),
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
        stream = prepare_zip_archive(
            entries=(_entry("first"), _entry("second")),
            storage_factory=_storage_factory(storage),
            observation=_observation(),
        )

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
                    storage_factory=_storage_factory(_Storage([ObjectMissing()])),
                    observation=_observation(),
                )
            missing_callback.assert_called_once_with()

        mismatch_callback = Mock()
        mismatched_body = _Body([])
        with self.subTest("mismatch"):
            with self.assertRaises(ArchiveSourceMissing):
                prepare_zip_archive(
                    entries=(_entry("mismatch", size=3, on_source_missing=mismatch_callback),),
                    storage_factory=_storage_factory(_Storage([_opened(mismatched_body, size=4)])),
                    observation=_observation(),
                )
            mismatch_callback.assert_called_once_with()
            self.assertEqual(mismatched_body.close_calls, 1)

    def test_unavailable_first_source_fails_before_streaming(self) -> None:
        callback = Mock()

        with self.assertRaises(ArchiveSourceUnavailable):
            prepare_zip_archive(
                entries=(_entry("unavailable", on_source_missing=callback),),
                storage_factory=_storage_factory(_Storage([StorageUnavailable()])),
                observation=_observation(),
            )

        callback.assert_not_called()

    def test_unreadable_first_source_fails_during_preparation(self) -> None:
        callback = Mock()
        unreadable = _Body([OSError("storage read failed")])

        with self.assertRaises(ArchiveSourceUnavailable):
            prepare_zip_archive(
                entries=(_entry("unreadable", on_source_missing=callback),),
                storage_factory=_storage_factory(_Storage([_opened(unreadable, size=3)])),
                observation=_observation(),
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
                    storage_factory=_storage_factory(_Storage([_opened(_Body([]), size=4)])),
                    observation=_observation(),
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
            storage_factory=_storage_factory(storage),
            observation=_observation(),
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
            storage_factory=_storage_factory(
                _Storage([_opened(first, size=3), _opened(missing, size=3)])
            ),
            observation=_observation(),
        )

        with self.assertRaises(ArchiveSourceMissing):
            b"".join(stream)

        callback.assert_called_once_with()

    def test_rejects_unsafe_photo_identifiers_without_touching_storage(self) -> None:
        storage = _Storage([])

        with self.assertRaises(ValueError):
            prepare_zip_archive(
                entries=(_entry("../secret"),),
                storage_factory=_storage_factory(storage),
                observation=_observation(),
            )

        self.assertEqual(storage.opened_keys, [])

    def test_completed_observation_counts_emitted_zip_bytes_for_both_contexts(self) -> None:
        for context in ("free_result", "paid_order"):
            with self.subTest(context=context):
                clock = Mock(side_effect=(10.0, 12.5))
                sensitive_values = (
                    f"photo-sensitive-{context}",
                    f"originals/private-sensitive-{context}",
                    f"bearer-sensitive-{context}",
                )
                with self.assertLogs("picflow.archive", level="INFO") as captured:
                    archive_bytes = b"".join(
                        prepare_zip_archive(
                            entries=(_entry(sensitive_values[0], key=sensitive_values[1], size=3),),
                            storage_factory=_storage_factory(
                                _Storage([_opened(_Body([b"one"]), size=3)])
                            ),
                            observation=_observation(context=context, page=7),
                            clock=clock,
                        )
                    )

                self._assert_outcome_record(
                    captured.records,
                    context=context,
                    page=7,
                    file_count=1,
                    declared_input_bytes=3,
                    streamed_bytes=len(archive_bytes),
                    duration_seconds=2.5,
                    outcome="completed",
                    sensitive_values=sensitive_values,
                )

    def test_interrupted_observation_counts_only_chunks_already_yielded(self) -> None:
        for context in ("free_result", "paid_order"):
            with self.subTest(context=context):
                clock = Mock(side_effect=(20.0, 21.25))
                stream = prepare_zip_archive(
                    entries=(_entry("interrupted-sensitive", size=3),),
                    storage_factory=_storage_factory(_Storage([_opened(_Body([b"one"]), size=3)])),
                    observation=_observation(context=context, page=2),
                    clock=clock,
                )

                with self.assertLogs("picflow.archive", level="INFO") as captured:
                    emitted = next(stream)
                    stream.close()

                self._assert_outcome_record(
                    captured.records,
                    context=context,
                    page=2,
                    file_count=1,
                    declared_input_bytes=3,
                    streamed_bytes=len(emitted),
                    duration_seconds=1.25,
                    outcome="interrupted",
                    sensitive_values=(
                        "interrupted-sensitive",
                        "originals/interrupted-sensitive",
                    ),
                )

    def test_setup_failure_observation_covers_validation_first_open_and_first_read(self) -> None:
        cases = (
            (
                "validation",
                (_entry("../validation-sensitive"),),
                _Storage([]),
                ValueError,
                0,
                0,
            ),
            (
                "first-open",
                (_entry("first-open-sensitive"),),
                _Storage([ObjectMissing()]),
                ArchiveSourceMissing,
                1,
                3,
            ),
            (
                "first-read",
                (_entry("first-read-sensitive"),),
                _Storage([_opened(_Body([OSError("exception-sensitive")]), size=3)]),
                ArchiveSourceUnavailable,
                1,
                3,
            ),
        )
        for index, (name, entries, storage, exception, file_count, declared_bytes) in enumerate(
            cases, start=1
        ):
            with self.subTest(name=name):
                clock = Mock(side_effect=(30.0, 30.5))
                with self.assertLogs("picflow.archive", level="WARNING") as captured:
                    with self.assertRaises(exception):
                        prepare_zip_archive(
                            entries=entries,
                            storage_factory=_storage_factory(storage),
                            observation=_observation(
                                context="free_result" if index % 2 else "paid_order",
                                page=index,
                            ),
                            clock=clock,
                        )

                self._assert_outcome_record(
                    captured.records,
                    context="free_result" if index % 2 else "paid_order",
                    page=index,
                    file_count=file_count,
                    declared_input_bytes=declared_bytes,
                    streamed_bytes=0,
                    duration_seconds=0.5,
                    outcome="setup_failure",
                    sensitive_values=(
                        "validation-sensitive",
                        "first-open-sensitive",
                        "first-read-sensitive",
                        "exception-sensitive",
                    ),
                )

    def test_source_failure_observation_preserves_emitted_byte_count(self) -> None:
        for context in ("free_result", "paid_order"):
            with self.subTest(context=context):
                clock = Mock(side_effect=(40.0, 43.0))
                stream = prepare_zip_archive(
                    entries=(
                        _entry("first-sensitive"),
                        _entry("later-sensitive", key="originals/private-later-sensitive"),
                    ),
                    storage_factory=_storage_factory(
                        _Storage([_opened(_Body([b"one"]), size=3), ObjectMissing()])
                    ),
                    observation=_observation(context=context, page=4),
                    clock=clock,
                )
                emitted: list[bytes] = []

                with self.assertLogs("picflow.archive", level="WARNING") as captured:
                    with self.assertRaises(ArchiveSourceMissing):
                        while True:
                            emitted.append(next(stream))

                self._assert_outcome_record(
                    captured.records,
                    context=context,
                    page=4,
                    file_count=2,
                    declared_input_bytes=6,
                    streamed_bytes=len(b"".join(emitted)),
                    duration_seconds=3.0,
                    outcome="source_failure",
                    sensitive_values=(
                        "first-sensitive",
                        "later-sensitive",
                        "originals/private-later-sensitive",
                    ),
                )

    def _assert_outcome_record(
        self,
        records: list[logging.LogRecord],
        *,
        context: str,
        page: int,
        file_count: int,
        declared_input_bytes: int,
        streamed_bytes: int,
        duration_seconds: float,
        outcome: str,
        sensitive_values: tuple[str, ...],
    ) -> None:
        outcome_records = [record for record in records if hasattr(record, "archive_context")]
        self.assertEqual(len(outcome_records), 1)
        record = outcome_records[0]
        archive_fields = {
            key: value for key, value in record.__dict__.items() if key.startswith("archive_")
        }
        self.assertEqual(
            archive_fields,
            {
                "archive_context": context,
                "archive_page": page,
                "archive_file_count": file_count,
                "archive_declared_input_bytes": declared_input_bytes,
                "archive_streamed_bytes": streamed_bytes,
                "archive_duration_seconds": duration_seconds,
                "archive_outcome": outcome,
            },
        )
        formatted_record = repr(record.__dict__)
        for sensitive_value in sensitive_values:
            self.assertNotIn(sensitive_value, formatted_record)
        self.assertIsNone(record.exc_info)

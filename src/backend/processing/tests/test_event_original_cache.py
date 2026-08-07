import hashlib
import io
import json
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from picflow.models import Event, Photo

from processing.services.event_original_cache import (
    CacheError,
    CacheResult,
    EventOriginalCache,
    ObjectMetadata,
    ObjectResponse,
    S3EventOriginalStorage,
    build_inventory,
    select_event,
)


class _Body:
    def __init__(self, value: bytes, *, fail_after: int | None = None) -> None:
        self._source = io.BytesIO(value)
        self.fail_after = fail_after
        self.closed_by_cache = False

    def read(self, size: int | None = None) -> bytes:
        if self.fail_after is not None and self._source.tell() >= self.fail_after:
            raise OSError("network interrupted")
        return self._source.read(-1 if size is None else size)

    def close(self) -> None:
        self.closed_by_cache = True
        self._source.close()


class _Storage:
    def __init__(self, objects: dict[str, tuple[bytes, str, str]]) -> None:
        self.objects = objects
        self.head_calls: list[tuple[str, str | None]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.bodies: list[_Body] = []
        self.interrupt_key: str | None = None
        self.get_override: tuple[bytes, str, str] | None = None
        self.get_response_size: int | None = None
        self.before_get: Callable[[], None] | None = None

    def head(self, *, key: str, if_match: str | None = None) -> ObjectMetadata:
        self.head_calls.append((key, if_match))
        value, etag, content_type = self.objects[key]
        if if_match is not None and if_match != etag:
            raise CacheError("object changed")
        return ObjectMetadata(size=len(value), etag=etag, content_type=content_type)

    def get(self, *, key: str, if_match: str) -> ObjectResponse:
        self.get_calls.append((key, if_match))
        if self.before_get is not None:
            self.before_get()
        value, etag, content_type = self.objects[key]
        if if_match != etag:
            raise CacheError("object changed")
        response_value, response_etag, response_type = self.get_override or (
            value,
            etag,
            content_type,
        )
        body = _Body(response_value, fail_after=1 if self.interrupt_key == key else None)
        self.bodies.append(body)
        return ObjectResponse(
            size=(
                self.get_response_size
                if self.get_response_size is not None
                else len(response_value)
            ),
            etag=response_etag,
            content_type=response_type,
            body=body,
        )


class EventOriginalCacheTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="cache-owner")
        self.event = self.create_event(slug="cache-event", publication_status="published")
        self.photo = self.create_photo("cache-photo", content_type="image/jpeg")
        self.storage = _Storage(
            {
                self.photo.original_key: (b"jpeg bytes", '"etag-jpeg"', "image/jpeg"),
            }
        )
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output_root = Path(self.temporary.name) / "event-corpora"

    def create_event(self, *, slug: str, publication_status: str) -> Event:
        return Event.objects.create(
            name=f"Event {slug}",
            slug=slug,
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 2),
            city="Moscow",
            publication_status=publication_status,
        )

    def create_photo(self, photo_id: str, *, content_type: str) -> Photo:
        extension = "jpg" if content_type == "image/jpeg" else "png"
        return Photo.objects.create(
            id=photo_id,
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{photo_id}",
            original_filename=f"source.{extension}",
            original_size=10,
            original_content_type=content_type,
            uploaded_at=timezone.now(),
        )

    def cache(self) -> CacheResult:
        return EventOriginalCache(storage=self.storage).cache(
            event=self.event, output_root=self.output_root
        )

    def test_selects_latest_published_event_by_dates_then_slug(self) -> None:
        earlier = self.create_event(slug="earlier", publication_status="published")
        earlier.start_date = date(2026, 7, 31)
        earlier.save(update_fields=["start_date"])
        a_event = self.create_event(slug="a-event", publication_status="published")
        z_event = self.create_event(slug="z-event", publication_status="published")
        a_event.start_date = z_event.start_date = date(2026, 8, 3)
        a_event.end_date = z_event.end_date = date(2026, 8, 4)
        a_event.save(update_fields=["start_date", "end_date"])
        z_event.save(update_fields=["start_date", "end_date"])
        self.create_event(slug="draft-latest", publication_status="draft")

        self.assertEqual(select_event(latest_published=True).pk, a_event.pk)
        self.assertEqual(select_event(event_slug=earlier.slug).pk, earlier.pk)

    def test_rejects_empty_or_draft_event_and_invalid_source_inventory(self) -> None:
        draft = self.create_event(slug="draft", publication_status="draft")
        with self.assertRaisesRegex(CacheError, "published"):
            EventOriginalCache(storage=self.storage).cache(
                event=draft, output_root=self.output_root
            )
        empty = self.create_event(slug="empty", publication_status="published")
        with self.assertRaisesRegex(CacheError, "eligible"):
            EventOriginalCache(storage=self.storage).cache(
                event=empty, output_root=self.output_root
            )

        invalid = SimpleNamespace(
            id="unsafe/name",
            src="",
            original_key="originals/a",
            original_size=1,
            original_content_type="image/jpeg",
        )
        with self.assertRaisesRegex(CacheError, "safe"):
            build_inventory(self.event, [invalid])

    def test_inventory_uses_generated_ids_and_only_jpeg_png(self) -> None:
        png = SimpleNamespace(
            id="png-photo",
            src="",
            original_key="originals/png-photo",
            original_size=1,
            original_content_type="image/png",
        )
        jpeg = SimpleNamespace(
            id="jpeg-photo",
            src="",
            original_key="originals/jpeg-photo",
            original_size=1,
            original_content_type="image/jpeg",
        )

        inventory = build_inventory(self.event, [png, jpeg])

        self.assertEqual(
            [entry.filename for entry in inventory],
            ["photo-jpeg-photo.jpg", "photo-png-photo.png"],
        )
        duplicate = [jpeg, SimpleNamespace(**jpeg.__dict__)]
        with self.assertRaisesRegex(CacheError, "duplicate"):
            build_inventory(self.event, duplicate)
        case_variant = SimpleNamespace(
            id="JPEG-PHOTO",
            src="",
            original_key="originals/other",
            original_size=1,
            original_content_type="image/jpeg",
        )
        with self.assertRaisesRegex(CacheError, "duplicate"):
            build_inventory(self.event, [jpeg, case_variant])
        png.original_content_type = "image/webp"
        with self.assertRaisesRegex(CacheError, "content type"):
            build_inventory(self.event, [png])

    def test_first_run_writes_manifest_then_conditionally_streams_and_closes_body(self) -> None:
        manifest_path = self.output_root / self.event.slug / "manifest.json"
        self.storage.before_get = lambda: self.assertTrue(manifest_path.is_file())
        result = self.cache()

        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(result.downloaded_count, 1)
        self.assertTrue(manifest["complete"])
        self.assertEqual(manifest["files"][0]["sha256"], hashlib.sha256(b"jpeg bytes").hexdigest())
        self.assertEqual(self.storage.get_calls, [(self.photo.original_key, '"etag-jpeg"')])
        self.assertTrue(self.storage.bodies[0].closed_by_cache)
        self.assertEqual(
            (
                self.output_root / self.event.slug / "originals" / "photo-cache-photo.jpg"
            ).read_bytes(),
            b"jpeg bytes",
        )

    def test_verified_file_is_reused_without_s3_calls_and_corrupt_file_is_refetched(self) -> None:
        self.cache()
        self.storage.head_calls.clear()
        self.storage.get_calls.clear()

        result = self.cache()
        self.assertEqual(result.reused_count, 1)
        self.assertEqual(self.storage.head_calls, [])
        self.assertEqual(self.storage.get_calls, [])

        target = self.output_root / self.event.slug / "originals" / "photo-cache-photo.jpg"
        target.write_bytes(b"corrupt")
        result = self.cache()
        self.assertEqual(result.downloaded_count, 1)
        self.assertEqual(len(self.storage.get_calls), 1)
        self.assertEqual(target.read_bytes(), b"jpeg bytes")

    def test_interruption_cleans_partial_and_keeps_resumable_manifest(self) -> None:
        self.storage.interrupt_key = self.photo.original_key

        with self.assertRaisesRegex(CacheError, "download"):
            self.cache()

        event_root = self.output_root / self.event.slug
        self.assertTrue((event_root / "manifest.json").is_file())
        self.assertFalse((event_root / "originals" / "photo-cache-photo.jpg").exists())
        self.assertFalse(any((event_root / "originals").glob("*.partial")))
        self.storage.interrupt_key = None
        self.cache()

    def test_partial_file_is_discarded_and_fetched_again(self) -> None:
        self.cache()
        originals = self.output_root / self.event.slug / "originals"
        (originals / "photo-cache-photo.jpg").unlink()
        partial = originals / ".photo-cache-photo.jpg.partial"
        partial.write_bytes(b"incomplete")
        self.storage.head_calls.clear()
        self.storage.get_calls.clear()

        result = self.cache()

        self.assertEqual(result.downloaded_count, 1)
        self.assertFalse(partial.exists())
        self.assertEqual(self.storage.get_calls, [(self.photo.original_key, '"etag-jpeg"')])

    def test_rejects_changed_remote_manifest_tampering_and_unexpected_local_entries(self) -> None:
        self.cache()
        event_root = self.output_root / self.event.slug
        target = event_root / "originals" / "photo-cache-photo.jpg"
        target.unlink()
        self.storage.objects[self.photo.original_key] = (b"new bytes", '"new-etag"', "image/jpeg")
        with self.assertRaisesRegex(CacheError, "changed"):
            self.cache()

        self.storage.objects[self.photo.original_key] = (b"jpeg bytes", '"etag-jpeg"', "image/jpeg")
        manifest_path = event_root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"][0]["size"] = 999
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(CacheError, "manifest"):
            self.cache()

        self.cache_fresh_root_for_structure_checks()
        extra = self.output_root / self.event.slug / "originals" / "extra.jpg"
        extra.write_bytes(b"not allowed")
        with self.assertRaisesRegex(CacheError, "unexpected"):
            self.cache()

    def cache_fresh_root_for_structure_checks(self) -> None:
        self.output_root = Path(self.temporary.name) / "other-root"
        self.cache()

    def test_rejects_symlink_nested_directory_and_unsafe_event_output_path(self) -> None:
        self.cache()
        originals = self.output_root / self.event.slug / "originals"
        target = originals / "photo-cache-photo.jpg"
        target.unlink()
        os.symlink("/tmp", target)
        with self.assertRaisesRegex(CacheError, "symlink"):
            self.cache()

        target.unlink()
        (originals / "nested").mkdir()
        with self.assertRaisesRegex(CacheError, "nested"):
            self.cache()

        unsafe_event = Event(
            pk=1,
            slug="../outside",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        with self.assertRaisesRegex(CacheError, "output"):
            EventOriginalCache(storage=self.storage).cache(
                event=unsafe_event, output_root=self.output_root
            )

    def test_rejects_non_directory_event_root_without_exposing_filesystem_error(self) -> None:
        event_root = self.output_root / self.event.slug
        event_root.parent.mkdir(parents=True)
        event_root.write_text("not a directory")

        with self.assertRaisesRegex(CacheError, "directory"):
            self.cache()

    def test_rejects_output_root_inside_primary_or_worktree_git_checkout(self) -> None:
        primary = Path(self.temporary.name) / "primary"
        (primary / ".git").mkdir(parents=True)
        with self.assertRaisesRegex(CacheError, "checkout"):
            EventOriginalCache(storage=self.storage).cache(
                event=self.event, output_root=primary / "private-cache"
            )
        worktree = Path(self.temporary.name) / "worktree"
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: /elsewhere")
        with self.assertRaisesRegex(CacheError, "checkout"):
            EventOriginalCache(storage=self.storage).cache(
                event=self.event, output_root=worktree / "private-cache"
            )

    def test_creates_and_tightens_private_cache_permissions_under_permissive_umask(self) -> None:
        original_umask = os.umask(0)
        try:
            self.cache()
        finally:
            os.umask(original_umask)
        event_root = self.output_root / self.event.slug
        original = event_root / "originals" / "photo-cache-photo.jpg"
        self.assertEqual(event_root.stat().st_mode & 0o777, 0o700)
        self.assertEqual((event_root / "originals").stat().st_mode & 0o777, 0o700)
        self.assertEqual(original.stat().st_mode & 0o777, 0o600)
        os.chmod(event_root, 0o755)
        self.cache()
        self.assertEqual(event_root.stat().st_mode & 0o777, 0o700)

    def test_failed_retry_marks_previously_complete_manifest_incomplete_before_s3(self) -> None:
        self.cache()
        target = self.output_root / self.event.slug / "originals" / "photo-cache-photo.jpg"
        target.unlink()
        self.storage.interrupt_key = self.photo.original_key

        with self.assertRaisesRegex(CacheError, "download"):
            self.cache()

        manifest = json.loads((self.output_root / self.event.slug / "manifest.json").read_text())
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["unresolved_count"], 1)
        self.storage.interrupt_key = None
        self.cache()

    def test_mismatched_get_response_closes_body_and_leaves_target_invisible(self) -> None:
        self.storage.get_override = (b"bad", '"etag-jpeg"', "image/jpeg")
        target = self.output_root / self.event.slug / "originals" / "photo-cache-photo.jpg"

        with self.assertRaisesRegex(CacheError, "changed"):
            self.cache()

        self.assertTrue(self.storage.bodies[0].closed_by_cache)
        self.assertFalse(target.exists())

    def test_manifest_publication_failure_cleans_temp_and_preserves_prior_manifest(self) -> None:
        self.cache()
        event_root = self.output_root / self.event.slug
        before = (event_root / "manifest.json").read_text()
        (event_root / "originals" / "photo-cache-photo.jpg").unlink()
        with patch("processing.services.event_original_cache.os.replace", side_effect=OSError("x")):
            with self.assertRaisesRegex(CacheError, "manifest") as raised:
                self.cache()
        self.assertNotIn(str(event_root), str(raised.exception))
        self.assertEqual((event_root / "manifest.json").read_text(), before)
        self.assertFalse((event_root / ".manifest.json.partial").exists())
        self.cache()

    def test_manifest_fsync_failure_cleans_unpublished_temp_file(self) -> None:
        self.cache()
        event_root = self.output_root / self.event.slug
        (event_root / "originals" / "photo-cache-photo.jpg").unlink()
        with patch("processing.services.event_original_cache.os.fsync", side_effect=OSError("x")):
            with self.assertRaisesRegex(CacheError, "manifest"):
                self.cache()
        self.assertFalse((event_root / ".manifest.json.partial").exists())

    def test_failed_manifest_temp_cleanup_is_recovered_on_next_invocation(self) -> None:
        self.cache()
        event_root = self.output_root / self.event.slug
        (event_root / "originals" / "photo-cache-photo.jpg").unlink()
        original_unlink = Path.unlink

        def fail_only_manifest_temp(path: Path, *, missing_ok: bool = False) -> None:
            if path.name == ".manifest.json.partial":
                raise OSError(str(event_root))
            original_unlink(path, missing_ok=missing_ok)

        with (
            patch("processing.services.event_original_cache.os.replace", side_effect=OSError("x")),
            patch.object(Path, "unlink", new=fail_only_manifest_temp),
        ):
            with self.assertRaisesRegex(CacheError, "manifest") as raised:
                self.cache()
        self.assertNotIn(str(event_root), str(raised.exception))
        self.assertTrue((event_root / ".manifest.json.partial").exists())
        self.cache()
        self.assertFalse((event_root / ".manifest.json.partial").exists())

    def test_final_partial_cleanup_failure_preserves_primary_failure_and_retries(self) -> None:
        self.storage.interrupt_key = self.photo.original_key
        event_root = self.output_root / self.event.slug
        original_unlink = Path.unlink

        def fail_only_partial(path: Path, *, missing_ok: bool = False) -> None:
            if path.name.endswith(".partial"):
                raise OSError(str(event_root))
            original_unlink(path, missing_ok=missing_ok)

        with patch.object(Path, "unlink", new=fail_only_partial):
            with self.assertRaisesRegex(CacheError, "download") as raised:
                self.cache()
        self.assertNotIn(str(event_root), str(raised.exception))
        self.storage.interrupt_key = None
        self.cache()

    def test_path_resolution_failure_is_sanitized(self) -> None:
        with patch.object(Path, "resolve", side_effect=OSError(str(self.output_root))):
            with self.assertRaisesRegex(CacheError, "output") as raised:
                self.cache()
        self.assertNotIn(str(self.output_root), str(raised.exception))

    def test_rejects_missing_legacy_metadata_and_no_published_event(self) -> None:
        missing = SimpleNamespace(
            id="missing",
            src="",
            original_key=None,
            original_size=None,
            original_content_type=None,
        )
        legacy = SimpleNamespace(
            id="legacy",
            src="photos/legacy.jpg",
            original_key=None,
            original_size=None,
            original_content_type=None,
        )
        with self.assertRaisesRegex(CacheError, "incomplete"):
            build_inventory(self.event, [missing])
        with self.assertRaisesRegex(CacheError, "legacy"):
            build_inventory(self.event, [legacy])
        Event.objects.update(publication_status=Event.PublicationStatus.DRAFT)
        with self.assertRaisesRegex(CacheError, "no published"):
            select_event(latest_published=True)

    def test_matching_metadata_short_and_oversized_streams_fail_closed(self) -> None:
        for payload in (b"short", b"01234567890"):
            self.storage.get_override = (payload, '"etag-jpeg"', "image/jpeg")
            self.storage.get_response_size = 10
            with self.assertRaisesRegex(CacheError, "changed"):
                self.cache()
            self.assertTrue(self.storage.bodies[-1].closed_by_cache)
            self.storage.get_override = None
            self.storage.get_response_size = None

    def test_database_inventory_mismatch_rejects_intact_manifest(self) -> None:
        self.cache()
        self.photo.original_size = 11
        self.photo.save(update_fields=["original_size"])
        with self.assertRaisesRegex(CacheError, "manifest"):
            self.cache()

    def test_event_identity_mismatch_rejects_self_hashed_manifest_before_s3(self) -> None:
        self.cache()
        manifest_path = self.output_root / self.event.slug / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["event"]["id"] = "different-event"
        payload = {key: value for key, value in manifest.items() if key != "manifest_hash"}
        manifest["manifest_hash"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        self.storage.head_calls.clear()
        self.storage.get_calls.clear()

        with self.assertRaisesRegex(CacheError, "manifest"):
            self.cache()

        self.assertEqual(self.storage.head_calls, [])
        self.assertEqual(self.storage.get_calls, [])

    def test_recovers_only_exact_manifest_partial_and_preserves_unrelated_entry(self) -> None:
        self.cache()
        event_root = self.output_root / self.event.slug
        recognized = event_root / ".manifest.json.partial"
        recognized.write_text("interrupted publication")
        self.cache()
        self.assertFalse(recognized.exists())

        unrelated = event_root / ".manifest-operator-note"
        unrelated.write_text("keep this")
        with self.assertRaisesRegex(CacheError, "unexpected"):
            self.cache()
        self.assertEqual(unrelated.read_text(), "keep this")

    def test_cache_database_queries_are_read_only(self) -> None:
        with CaptureQueriesContext(connection) as queries:
            self.cache()
        self.assertTrue(queries)
        self.assertTrue(
            all(
                query["sql"].lstrip().upper().startswith("SELECT")
                for query in queries.captured_queries
            )
        )


class CacheEventOriginalsCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="command-cache-owner")
        self.event = Event.objects.create(
            name="Command event",
            slug="command-event",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 2),
            city="Moscow",
            publication_status="published",
        )
        self.photo = Photo.objects.create(
            id="command-photo",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key="originals/command-photo",
            original_filename="source.jpg",
            original_size=12,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        self.storage = _Storage(
            {self.photo.original_key: (b"command data", '"command-etag"', "image/jpeg")}
        )
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def test_command_requires_one_safe_selector_and_hides_private_details(self) -> None:
        with self.assertRaises(CommandError):
            call_command("cache_event_originals")
        with self.assertRaises(CommandError):
            call_command("cache_event_originals", event=self.event.slug, latest_published=True)

        output = io.StringIO()
        with patch(
            "processing.management.commands.cache_event_originals.S3EventOriginalStorage",
            return_value=self.storage,
        ):
            call_command(
                "cache_event_originals",
                event=self.event.slug,
                output_root=Path(self.temporary.name),
                stdout=output,
            )
        text = output.getvalue()
        self.assertIn("1 original", text)
        self.assertNotIn(self.photo.original_key, text)
        self.assertNotIn(self.temporary.name, text)
        self.assertNotIn("command-etag", text)


class S3EventOriginalStorageTests(TestCase):
    def test_adapter_uses_only_head_and_conditional_get_and_closes_malformed_response(self) -> None:
        body = _Body(b"bytes")

        class Client:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def head_object(self, **kwargs: object) -> dict[str, object]:
                self.calls.append(("head_object", kwargs))
                return {
                    "ContentLength": 5,
                    "ContentType": "image/jpeg",
                    "ETag": '"etag"',
                }

            def get_object(self, **kwargs: object) -> dict[str, object]:
                self.calls.append(("get_object", kwargs))
                return {
                    "Body": body,
                    "ContentLength": "bad",
                    "ContentType": "image/jpeg",
                    "ETag": '"etag"',
                }

        client = Client()
        storage = S3EventOriginalStorage(client=client)
        metadata = storage.head(key="private-key", if_match='"etag"')
        self.assertEqual(metadata.size, 5)
        with self.assertRaisesRegex(CacheError, "private object read failed"):
            storage.get(key="private-key", if_match='"etag"')
        self.assertTrue(body.closed_by_cache)
        self.assertEqual([name for name, _kwargs in client.calls], ["head_object", "get_object"])
        self.assertEqual(client.calls[0][1]["IfMatch"], '"etag"')
        self.assertEqual(client.calls[1][1]["IfMatch"], '"etag"')

from __future__ import annotations

import json
from datetime import date, timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from ingestion.storage import StorageUnavailable
from picflow.models import Event, Photo
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchAttempt,
    SelfieSearchJob,
    SelfieSearchResult,
)


class RecordingStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.fail = False

    def delete(self, *, key: str) -> None:
        if self.fail:
            raise StorageUnavailable()
        self.deleted.append(key)


class SettlePreCutoverSearchesTests(TestCase):
    def setUp(self) -> None:
        self.now = timezone.now()
        self.cutoff = (self.now - timedelta(days=1)).isoformat()
        self.storage = RecordingStorage()
        self.events = [
            Event.objects.create(
                name=f"Cutover {model}",
                slug=f"cutover-{model}-{uuid4().hex[:6]}",
                start_date=date(2026, 10, 1),
                end_date=date(2026, 10, 1),
                city="Moscow",
                face_search_generation=model,
            )
            for model in (
                Event.FaceSearchGeneration.SFACE_V3,
                Event.FaceSearchGeneration.ADAFACE_V5,
            )
        ]

    def search(self, event: Event, *, key: str = "", old: bool = True) -> SelfieSearch:
        search = SelfieSearch.objects.create(
            event=event,
            public_token_digest=uuid4().hex + uuid4().hex,
            temporary_object_key=key,
            configuration={
                "embedding_model": (
                    "sface"
                    if event.face_search_generation == Event.FaceSearchGeneration.SFACE_V3
                    else "adaface-ir18-webface4m"
                )
            },
        )
        if old:
            SelfieSearch.objects.filter(pk=search.pk).update(
                created_at=self.now - timedelta(days=2)
            )
            search.refresh_from_db()
        return search

    def run_command(self, *searches: SelfieSearch, execute: bool = False) -> dict:
        output = StringIO()
        args = ["--cutoff", self.cutoff, "--limit", str(len(searches))]
        for search in searches:
            args.extend(("--search-id", str(search.pk)))
        if execute:
            args.append("--execute")
        with patch(
            "selfie_search.management.commands.settle_pre_cutover_searches.TemporarySelfieStorage",
            return_value=self.storage,
        ):
            call_command("settle_pre_cutover_searches", *args, stdout=output)
        for search in searches:
            self.assertNotIn(str(search.pk), output.getvalue())
            if search.temporary_object_key:
                self.assertNotIn(search.temporary_object_key, output.getvalue())
        return json.loads(output.getvalue())

    def test_dry_run_inventories_both_models_without_mutation(self) -> None:
        searches = [self.search(event) for event in self.events]
        receipt = self.run_command(*searches)
        self.assertEqual(receipt["selected"], 2)
        self.assertEqual(receipt["eligible"], 2)
        self.assertEqual(receipt["search_status_before"], {"queued": 2})
        self.assertEqual(receipt["frozen_model"], {"sface": 1, "adaface-ir18-webface4m": 1})
        self.assertEqual(self.storage.deleted, [])
        self.assertTrue(all(search.status == "queued" for search in SelfieSearch.objects.all()))

    def test_execute_settles_gallery_and_expired_attempt_but_preserves_evidence(self) -> None:
        gallery = self.search(self.events[0])
        upload = self.search(self.events[1], key="selfie-search/" + uuid4().hex)
        key = upload.temporary_object_key
        job = SelfieSearchJob.objects.create(search=upload)
        attempt = SelfieSearchAttempt.objects.create(
            job=job,
            lease_expires_at=self.now - timedelta(hours=1),
            error_code="observed_worker_error",
        )
        receipt = self.run_command(gallery, upload, execute=True)
        gallery.refresh_from_db()
        upload.refresh_from_db()
        job.refresh_from_db()
        attempt.refresh_from_db()
        self.assertEqual(receipt["terminalized"], 2)
        self.assertEqual((gallery.status, upload.status), ("failed", "failed"))
        self.assertEqual(job.status, "failed")
        self.assertEqual(attempt.status, "expired")
        self.assertEqual(attempt.error_code, "observed_worker_error")
        self.assertEqual(upload.failure_code, "")
        self.assertEqual(upload.temporary_object_key, "")
        self.assertEqual(self.storage.deleted, [key])
        self.assertEqual(self.run_command(gallery, upload, execute=True)["terminalized"], 0)

    def test_active_lease_new_and_ready_searches_are_skipped(self) -> None:
        active = self.search(self.events[0])
        job = SelfieSearchJob.objects.create(search=active, status="processing")
        SelfieSearchAttempt.objects.create(job=job, lease_expires_at=self.now + timedelta(hours=1))
        new = self.search(self.events[1], old=False)
        ready = self.search(self.events[0])
        SelfieSearch.objects.filter(pk=ready.pk).update(status="ready", terminal_at=self.now)
        receipt = self.run_command(active, new, ready, execute=True)
        self.assertEqual(receipt["terminalized"], 0)
        self.assertEqual(receipt["skipped_active_lease"], 1)
        for search in (active, new, ready):
            search.refresh_from_db()
        self.assertEqual((active.status, new.status, ready.status), ("queued", "queued", "ready"))

    def test_cleanup_failure_is_retryable_without_losing_diagnosis(self) -> None:
        search = self.search(self.events[0], key="selfie-search/" + uuid4().hex)
        key = search.temporary_object_key
        self.storage.fail = True
        first = self.run_command(search, execute=True)
        search.refresh_from_db()
        self.assertEqual(first["cleanup_pending"], 1)
        self.assertEqual(search.status, "cleanup_pending")
        self.assertEqual(search.temporary_object_key, key)
        self.storage.fail = False
        second = self.run_command(search, execute=True)
        search.refresh_from_db()
        self.assertEqual(second["terminalized"], 1)
        self.assertEqual(search.status, "failed")
        self.assertEqual(self.storage.deleted, [key])

    def test_prepared_result_rows_are_never_terminalized_or_deleted(self) -> None:
        search = self.search(self.events[0])
        owner = get_user_model().objects.create_user(username="cutover-result-owner")
        photo = Photo.objects.create(
            id="cutover-result-photo",
            event=search.event,
            uploaded_by=owner,
            original_key="originals/" + uuid4().hex,
            original_filename="result.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=self.now,
        )
        result = SelfieSearchResult.objects.create(search=search, photo=photo, rank=1)
        receipt = self.run_command(search, execute=True)
        search.refresh_from_db()
        self.assertEqual(receipt["terminalized"], 0)
        self.assertEqual(search.status, "queued")
        self.assertTrue(SelfieSearchResult.objects.filter(pk=result.pk).exists())

    def test_failed_attempt_evidence_remains_unchanged(self) -> None:
        search = self.search(self.events[1])
        job = SelfieSearchJob.objects.create(search=search, status="retry_wait")
        attempt = SelfieSearchAttempt.objects.create(
            job=job,
            status="failed",
            terminal_at=self.now - timedelta(hours=1),
            error_code="observed_failure",
            error_detail="diagnostic retained in database",
        )
        receipt = self.run_command(search, execute=True)
        attempt.refresh_from_db()
        self.assertEqual(receipt["failure_evidence_fields_present"], 1)
        self.assertEqual((attempt.status, attempt.error_code), ("failed", "observed_failure"))
        self.assertEqual(attempt.error_detail, "diagnostic retained in database")

    def test_rejects_unbounded_or_duplicate_selection(self) -> None:
        search = self.search(self.events[0])
        for args in (
            ("--cutoff", self.cutoff),
            ("--cutoff", self.cutoff, "--search-id", str(search.pk), "--limit", "0"),
        ):
            with self.assertRaises(CommandError):
                call_command("settle_pre_cutover_searches", *args)

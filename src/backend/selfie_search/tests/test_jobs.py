from __future__ import annotations

import json
from datetime import date, timedelta
from math import sqrt
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from ingestion.storage import StorageUnavailable
from picflow.models import Event, Photo
from processing.models import (
    EventProcessingRun,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    ProcessingAttempt,
    ProcessingJob,
)
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchAttempt,
    SelfieSearchCandidate,
    SelfieSearchJob,
    SelfieSearchResult,
)
from selfie_search.services.jobs import (
    CleanupPending,
    SearchCompletionConflict,
    claim_search_job,
    complete_search_attempt,
    fail_search_attempt,
    heartbeat_search_attempt,
    recover_expired_search_attempts,
    refresh_search_download,
    search_attempt_reference,
)
from selfie_search.services.submission import _configuration as submission_configuration


class RecordingStorage:
    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.fail_delete = False

    def delete(self, *, key: str) -> None:
        if self.fail_delete:
            raise StorageUnavailable()
        self.deleted.append(key)


class SearchJobTests(TestCase):
    """The production break caught here is publishing or mutating a search before cleanup."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="search-jobs-owner")
        self.event = Event.objects.create(
            name="Search jobs",
            slug="search-jobs",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
        )
        self.storage = RecordingStorage()

    def make_search(self, *, with_candidate: bool = True) -> SelfieSearch:
        ordinal = SelfieSearch.objects.count()
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest=f"search-{ordinal:0>57}"[:64],
            temporary_object_key="selfie-search/0123456789abcdef0123456789abcdef",
            configuration=submission_configuration(
                content_type="image/jpeg",
                content_size=1024,
            ),
        )
        SelfieSearchJob.objects.create(search=search, configuration=search.configuration)
        if with_candidate:
            self.add_candidate(search=search, photo_id=f"candidate-{ordinal}", distance=0.1)
        return search

    def add_candidate(self, *, search: SelfieSearch, photo_id: str, distance: float) -> None:
        photo = Photo.objects.create(
            id=photo_id,
            event=self.event,
            uploaded_by=self.user,
            original_key=f"originals/{photo_id:0>32}"[-42:],
            original_filename=f"{photo_id}.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="a" * 64,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="a" * 64,
            input_fingerprint={},
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        detection = PhotoFaceDetection.objects.create(
            artifact=artifact,
            attempt=attempt,
            face_index=0,
            status=PhotoFaceDetection.Status.KEPT,
        )
        embedding = FaceEmbedding.objects.create(
            detection=detection,
            model_version="sface",
            vector=[1.0 - distance, sqrt(1 - (1.0 - distance) ** 2)] + [0.0] * 126,
            metadata={},
        )
        SelfieSearchCandidate.objects.create(search=search, embedding=embedding, photo=photo)

    def claim(self, search: SelfieSearch, *, now=None):
        return claim_search_job(
            contract_version=1,
            processor_type="selfie_query",
            processor_version=1,
            worker_build="worker-test",
            lease_seconds=120,
            now=now,
        )

    def result(self, *, first: float = 1.0) -> dict[str, object]:
        vector = [first, sqrt(1 - first**2)] + [0.0] * 126
        return {
            "model": "sface",
            "embedding": vector,
            "bbox": [1.0, 2.0, 32.0, 32.0],
            "confidence": 0.96,
            "landmarks": [[1.0, 2.0]] * 5,
            "timings": {"decode_ms": 1, "model_load_ms": 1, "detect_ms": 1, "embed_ms": 1},
        }

    def test_claim_is_atomic_leased_and_uses_a_namespaced_transport_reference(self) -> None:
        search = self.make_search()
        now = timezone.now()

        claimed = self.claim(search, now=now)
        second = self.claim(search, now=now)
        search.refresh_from_db()

        self.assertFalse(claimed.empty)
        self.assertTrue(second.empty)
        self.assertEqual(claimed.job.search_id, search.id)
        self.assertEqual(claimed.attempt.status, SelfieSearchAttempt.Status.IN_PROGRESS)
        self.assertEqual(search.status, SelfieSearch.Status.PROCESSING)
        self.assertEqual(search_attempt_reference(claimed.attempt), f"selfie_{claimed.attempt.id}")

    def test_heartbeat_and_download_refresh_require_the_current_unexpired_lease(self) -> None:
        search = self.make_search()
        now = timezone.now()
        claimed = self.claim(search, now=now)

        renewed = heartbeat_search_attempt(
            claimed.attempt.id, lease_seconds=120, now=now + timedelta(seconds=10)
        )
        current = refresh_search_download(claimed.attempt.id, now=now + timedelta(seconds=119))
        expired = refresh_search_download(claimed.attempt.id, now=now + timedelta(seconds=131))

        assert renewed is not None
        assert current is not None
        self.assertEqual(renewed.lease_expires_at, now + timedelta(seconds=130))
        self.assertEqual(current.id, claimed.attempt.id)
        self.assertIsNone(expired)

    def test_retryable_failure_uses_the_bounded_existing_retry_policy(self) -> None:
        search = self.make_search()
        now = timezone.now()
        claimed = self.claim(search, now=now)

        completion = fail_search_attempt(
            claimed.attempt.id,
            error_code="network_interruption",
            retryable=True,
            storage=self.storage,
            now=now,
            jitter=lambda _low, _high: 0,
        )
        job = SelfieSearchJob.objects.get(search=search)
        search.refresh_from_db()

        self.assertEqual(completion.attempt.status, SelfieSearchAttempt.Status.FAILED)
        self.assertEqual(job.status, SelfieSearchJob.Status.RETRY_WAIT)
        self.assertEqual(job.available_at, now + timedelta(seconds=30))
        self.assertEqual(search.status, SelfieSearch.Status.QUEUED)
        self.assertEqual(self.storage.deleted, [])

    def test_expired_lease_recovery_marks_the_attempt_and_schedules_a_retry(self) -> None:
        search = self.make_search()
        now = timezone.now()
        claimed = self.claim(search, now=now)

        recovered = recover_expired_search_attempts(
            storage=self.storage,
            now=now + timedelta(seconds=120),
            jitter=lambda _low, _high: 0,
        )
        job = SelfieSearchJob.objects.get(search=search)
        search.refresh_from_db()

        self.assertEqual([attempt.id for attempt in recovered], [claimed.attempt.id])
        self.assertEqual(recovered[0].status, SelfieSearchAttempt.Status.EXPIRED)
        self.assertEqual(job.status, SelfieSearchJob.Status.RETRY_WAIT)
        self.assertEqual(search.status, SelfieSearch.Status.QUEUED)

    def test_late_callback_after_expiry_stays_stale_without_retaining_a_query(self) -> None:
        search = self.make_search()
        now = timezone.now()
        claimed = self.claim(search, now=now)
        recover_expired_search_attempts(
            storage=self.storage,
            now=now + timedelta(seconds=120),
            jitter=lambda _low, _high: 0,
        )

        late = complete_search_attempt(
            claimed.attempt.id,
            result=self.result(),
            storage=self.storage,
            now=now + timedelta(seconds=121),
        )
        repeated = complete_search_attempt(
            claimed.attempt.id,
            result=self.result(first=0.9),
            storage=self.storage,
            now=now + timedelta(seconds=122),
        )

        self.assertTrue(late.stale)
        self.assertTrue(repeated.stale)
        self.assertEqual(SelfieSearchResult.objects.filter(search=search).count(), 0)

    def test_corrupt_frozen_candidate_fails_closed_then_deletes_the_selfie(self) -> None:
        search = self.make_search()
        candidate = SelfieSearchCandidate.objects.get(search=search)
        foreign_event = Event.objects.create(
            name="Foreign candidate event",
            slug="foreign-candidate-event",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
        )
        foreign_photo = Photo.objects.create(
            id="foreign-candidate-photo",
            event=foreign_event,
            uploaded_by=self.user,
            original_key="originals/abcdefabcdefabcdefabcdefabcdefab",
            original_filename="foreign.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        # Simulate a database-level corruption which model validation normally prevents.
        SelfieSearchCandidate.objects.filter(pk=candidate.pk).update(photo_id=foreign_photo.id)
        claimed = self.claim(search)

        completion = complete_search_attempt(
            claimed.attempt.id,
            result=self.result(),
            storage=self.storage,
        )
        search.refresh_from_db()

        self.assertEqual(completion.attempt.status, SelfieSearchAttempt.Status.FAILED)
        self.assertEqual(search.status, SelfieSearch.Status.FAILED)
        self.assertEqual(search.failure_code, "failed")
        self.assertEqual(search.temporary_object_key, "")
        self.assertEqual(self.storage.deleted, ["selfie-search/0123456789abcdef0123456789abcdef"])

    def test_cleanup_failure_keeps_progress_then_an_identical_callback_publishes_once(self) -> None:
        search = self.make_search()
        claimed = self.claim(search)
        self.storage.fail_delete = True
        payload = self.result()

        with self.assertRaises(CleanupPending):
            complete_search_attempt(claimed.attempt.id, result=payload, storage=self.storage)

        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.CLEANUP_PENDING)
        self.assertEqual(search.intended_terminal_status, SelfieSearch.Status.READY)
        self.assertEqual(
            search.temporary_object_key,
            "selfie-search/0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(SelfieSearchResult.objects.filter(search=search).count(), 1)
        self.assertEqual(search.matched_photo_count, 0)

        self.storage.fail_delete = False
        with self.assertLogs("selfie_search.services.jobs", level="INFO") as logs:
            with self.captureOnCommitCallbacks(execute=True):
                replay = complete_search_attempt(
                    claimed.attempt.id, result=payload, storage=self.storage
                )
        search.refresh_from_db()

        self.assertTrue(replay.idempotent)
        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertEqual(search.temporary_object_key, "")
        self.assertEqual(search.matched_photo_count, 1)
        self.assertEqual(SelfieSearchResult.objects.filter(search=search).count(), 1)
        self.assertEqual(self.storage.deleted, ["selfie-search/0123456789abcdef0123456789abcdef"])
        events = [json.loads(line.split(":", 2)[2]) for line in logs.output]
        terminal = next(event for event in events if event["event"] == "selfie_search_terminal")
        self.assertEqual(terminal["status"], SelfieSearch.Status.READY)
        self.assertTrue(terminal["cleanup_confirmed"])

    def test_terminal_observability_query_failure_cannot_change_the_committed_result(self) -> None:
        search = self.make_search()
        claimed = self.claim(search)

        with patch(
            "selfie_search.services.jobs._terminal_attempt_count",
            side_effect=RuntimeError("SECRET-QUERY-FAILURE"),
        ):
            with self.assertLogs("selfie_search.services.jobs", level="ERROR") as logs:
                with self.captureOnCommitCallbacks(execute=True):
                    completion = complete_search_attempt(
                        claimed.attempt.id, result=self.result(), storage=self.storage
                    )

        search.refresh_from_db()
        self.assertFalse(completion.idempotent)
        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertIsNotNone(search.cleanup_confirmed_at)
        self.assertEqual(SelfieSearchResult.objects.filter(search=search).count(), 1)
        self.assertEqual(len(logs.output), 1)
        self.assertTrue(logs.output[0].endswith("selfie_observability_emit_failed"))
        self.assertNotIn("SECRET-QUERY-FAILURE", logs.output[0])

    def test_terminal_logger_failure_cannot_change_the_committed_result(self) -> None:
        search = self.make_search()
        claimed = self.claim(search)

        with patch(
            "selfie_search.services.jobs.logger.log",
            side_effect=RuntimeError("logger unavailable"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                completion = complete_search_attempt(
                    claimed.attempt.id, result=self.result(), storage=self.storage
                )

        search.refresh_from_db()
        self.assertFalse(completion.idempotent)
        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertIsNotNone(search.cleanup_confirmed_at)
        self.assertEqual(SelfieSearchResult.objects.filter(search=search).count(), 1)

    def test_terminal_callbacks_are_hash_only_idempotent_and_reject_conflicts(self) -> None:
        search = self.make_search()
        claimed = self.claim(search)
        accepted = self.result()

        first = complete_search_attempt(claimed.attempt.id, result=accepted, storage=self.storage)
        repeated = complete_search_attempt(
            claimed.attempt.id,
            result=accepted,
            storage=self.storage,
        )

        self.assertFalse(first.idempotent)
        self.assertTrue(repeated.idempotent)
        attempt = SelfieSearchAttempt.objects.get(pk=claimed.attempt.id)
        durable = json.dumps(
            {
                "search": {
                    field.name: getattr(search, field.name) for field in SelfieSearch._meta.fields
                },
                "attempt": {
                    field.name: getattr(attempt, field.name)
                    for field in SelfieSearchAttempt._meta.fields
                },
            },
            default=str,
        )
        self.assertNotIn("[1.0,", durable)
        self.assertNotIn("query_vector", durable)
        with self.assertRaises(SearchCompletionConflict):
            complete_search_attempt(
                claimed.attempt.id,
                result=self.result(first=0.9),
                storage=self.storage,
            )

    def test_domain_failure_and_empty_frozen_cohort_delete_before_terminal_states(self) -> None:
        failure_search = self.make_search()
        failure_claim = self.claim(failure_search)
        failure = fail_search_attempt(
            failure_claim.attempt.id,
            error_code="no_face_detected",
            retryable=False,
            storage=self.storage,
        )
        failure_search.refresh_from_db()

        empty_search = self.make_search(with_candidate=False)
        empty_claim = self.claim(empty_search)
        ready = complete_search_attempt(
            empty_claim.attempt.id,
            result=self.result(),
            storage=self.storage,
        )
        empty_search.refresh_from_db()

        self.assertEqual(failure.attempt.status, SelfieSearchAttempt.Status.FAILED)
        self.assertEqual(failure_search.status, SelfieSearch.Status.NO_FACE)
        self.assertEqual(failure_search.failure_code, "no_face")
        self.assertEqual(ready.attempt.status, SelfieSearchAttempt.Status.SUCCEEDED)
        self.assertEqual(empty_search.status, SelfieSearch.Status.SEARCH_UNAVAILABLE)
        self.assertEqual(empty_search.matched_photo_count, 0)

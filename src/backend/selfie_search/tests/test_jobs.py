from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from math import sqrt
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection
from django.test import TestCase, override_settings
from django.utils import timezone
from face_cluster_contract import POLICY_ID, cluster_expansion_policy_hash
from ingestion.storage import StorageUnavailable
from picflow.models import Event, Photo
from processing.models import (
    EventFaceClusterActivation,
    EventProcessingRun,
    FaceCluster,
    FaceClusterCorpus,
    FaceClusterMember,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    CONTRACT_VERSION,
    FACE_EMBEDDING_CONFIGURATION,
    FACE_EMBEDDING_PROCESSOR_VERSION,
)
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchAttempt,
    SelfieSearchClusterEvidence,
    SelfieSearchDirectEvidence,
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
    selfie_worker_configuration,
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

    def test_local_adaface_selfie_claim_pins_scrfd_and_recognizer(self) -> None:
        """Changing either artifact must make the transient worker claim incompatible."""
        search = cast(
            SelfieSearch,
            SimpleNamespace(
                configuration={
                    "embedding_model": "adaface-ir18-webface4m",
                    "embedding_dimensions": 512,
                }
            ),
        )

        configuration = selfie_worker_configuration(search)

        self.assertEqual(
            cast(dict[str, object], configuration["scrfd"])["model_artifact_sha256"],
            "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91",
        )
        self.assertEqual(
            configuration["adaface"],
            {
                "alignment": "scrfd-five-landmark-112x112",
                "input_normalization": "rgb-value-over-255-minus-0.5-over-0.5",
                "model_artifact_sha256": (
                    "3a416518b11ece107b43385fc3678aad1d4f2405fde9f58f0be7f530230e368b"
                ),
                "model_revision": "0dd53f188fa27968b0a1326970ebf4aeb37ce2ca",
            },
        )

    def make_search(self, *, with_candidate: bool = True) -> SelfieSearch:
        ordinal = SelfieSearch.objects.count()
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest=f"search-{ordinal:0>57}"[:64],
            temporary_object_key="selfie-search/0123456789abcdef0123456789abcdef",
            configuration=submission_configuration(
                event=self.event,
                content_type="image/jpeg",
                content_size=1024,
            ),
        )
        SelfieSearchJob.objects.create(search=search, configuration=search.configuration)
        if with_candidate:
            self.add_candidate(search=search, photo_id=f"candidate-{ordinal}", distance=0.1)
        return search

    def add_candidate(
        self, *, search: SelfieSearch, photo_id: str, distance: float
    ) -> PhotoFaceDetection:
        configuration_hash = hashlib.sha256(
            json.dumps(FACE_EMBEDDING_CONFIGURATION, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
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
            contract_version=CONTRACT_VERSION,
            processor_type="face_embedding",
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_CONFIGURATION,
            configuration_hash=configuration_hash,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=CONTRACT_VERSION,
            processor_type="face_embedding",
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_CONFIGURATION,
            configuration_hash=configuration_hash,
            input_fingerprint={},
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=CONTRACT_VERSION,
            processor_type="face_embedding",
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration=FACE_EMBEDDING_CONFIGURATION,
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
        FaceEmbedding.objects.create(
            detection=detection,
            model_version="sface",
            vector=[1.0 - distance, sqrt(1 - (1.0 - distance) ** 2)] + [0.0] * 126,
            metadata={},
        )
        PhotoFaceEmbeddingProjection.objects.create(
            photo=photo,
            contract_version=CONTRACT_VERSION,
            processor_version=FACE_EMBEDDING_PROCESSOR_VERSION,
            configuration_hash=configuration_hash,
            accepted_attempt=attempt,
        )
        PhotoProcessingState.objects.create(
            photo=photo,
            processor_type="face_embedding",
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt,
            succeeded_at=timezone.now(),
        )
        return detection

    def claim(self, search: SelfieSearch, *, now=None):
        return claim_search_job(
            contract_version=1,
            processor_type="selfie_query",
            processor_version=2,
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

    def test_claim_rejects_the_superseded_selfie_processor_identity(self) -> None:
        search = self.make_search()

        claimed = claim_search_job(
            contract_version=1,
            processor_type="selfie_query",
            processor_version=1,
            worker_build="worker-test",
        )

        self.assertTrue(claimed.empty)
        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.QUEUED)

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

    @override_settings(SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=True)
    def test_enabled_completion_persists_direct_and_cluster_provenance_before_cleanup(self) -> None:
        search = self.make_search(with_candidate=False)
        anchor = self.add_candidate(search=search, photo_id="integration-anchor", distance=0.1)
        member = self.add_candidate(search=search, photo_id="integration-member", distance=0.4)
        generations = search.configuration["gallery_face_embedding_generations"]
        corpus = FaceClusterCorpus.objects.create(
            event=self.event,
            version=1,
            status=FaceClusterCorpus.Status.BUILDING,
            algorithm_version="guarded-graph-v1",
            configuration={"face_embedding_generations": generations},
            configuration_hash="b" * 64,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            model_version="sface",
            embedding_dimensions=128,
            edge_threshold=0.2,
            representative_threshold=0.2,
            distance_block_size=1,
            max_candidate_edges=1,
        )
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=corpus,
            cluster_key="integration",
            representative_detection=anchor,
            member_count=2,
        )
        for index, detection in enumerate((anchor, member)):
            FaceClusterMember.objects.create(
                event=self.event,
                corpus=corpus,
                cluster=cluster,
                detection=detection,
                member_index=index,
                distance_to_representative=index / 10,
            )
        FaceClusterCorpus.objects.filter(pk=corpus.pk).update(
            status=FaceClusterCorpus.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        EventFaceClusterActivation.objects.create(
            event=self.event,
            corpus=FaceClusterCorpus.objects.get(pk=corpus.pk),
            active=True,
            anchor_threshold=0.2,
            configuration={
                "policy_id": POLICY_ID,
                "corpus_configuration_hash": corpus.configuration_hash,
                "direct_threshold": 0.363,
                "anchor_threshold": 0.2,
            },
            configuration_hash=cluster_expansion_policy_hash(corpus.configuration_hash, 0.363, 0.2),
            approved_evaluation_report_hash="d" * 64,
        )
        claimed = self.claim(search)

        with self.assertLogs("selfie_search.services.jobs", level="INFO") as logs:
            with self.captureOnCommitCallbacks(execute=True):
                complete_search_attempt(
                    claimed.attempt.id,
                    result=self.result(),
                    storage=self.storage,
                )

        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertEqual(search.direct_matched_photo_count, 1)
        self.assertEqual(search.cluster_expanded_photo_count, 1)
        self.assertEqual(search.final_matched_photo_count, 2)
        self.assertEqual(search.cluster_expansion_outcome, "expanded")
        self.assertEqual(
            list(search.results.values_list("photo_id", "primary_source")),
            [
                ("integration-anchor", "direct"),
                ("integration-member", "face_cluster_expansion"),
            ],
        )
        events = [json.loads(line.split(":", 2)[2]) for line in logs.output]
        ranking_event = next(
            event for event in events if event["event"] == "selfie_ranking_finished"
        )
        terminal_event = next(
            event for event in events if event["event"] == "selfie_search_terminal"
        )
        self.assertEqual(ranking_event["schema_version"], 2)
        self.assertEqual(ranking_event["direct_matched_photo_count"], 1)
        self.assertEqual(ranking_event["cluster_expanded_photo_count"], 1)
        self.assertEqual(ranking_event["final_matched_photo_count"], 2)
        self.assertEqual(ranking_event["cluster_expansion_outcome"], "expanded")
        self.assertEqual(terminal_event["schema_version"], 2)
        self.assertEqual(terminal_event["direct_matched_photo_count"], 1)
        self.assertEqual(terminal_event["cluster_expanded_photo_count"], 1)
        self.assertEqual(terminal_event["cluster_corpus_version"], 1)
        self.assertEqual(
            terminal_event["cluster_configuration_hash"],
            cluster_expansion_policy_hash("b" * 64, 0.363, 0.2),
        )
        evidence_count = SelfieSearchClusterEvidence.objects.filter(result__search=search).count()
        self.assertEqual(evidence_count, 2)

    @override_settings(SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=True)
    def test_empty_direct_cohort_clears_identity_for_non_ready_observability(self) -> None:
        search = self.make_search(with_candidate=False)
        generations = search.configuration["gallery_face_embedding_generations"]
        corpus = FaceClusterCorpus.objects.create(
            event=self.event,
            version=1,
            status=FaceClusterCorpus.Status.BUILDING,
            algorithm_version="guarded-graph-v1",
            configuration={"face_embedding_generations": generations},
            configuration_hash="b" * 64,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            model_version="sface",
            embedding_dimensions=128,
            edge_threshold=0.2,
            representative_threshold=0.2,
            distance_block_size=1,
            max_candidate_edges=1,
        )
        FaceClusterCorpus.objects.filter(pk=corpus.pk).update(
            status=FaceClusterCorpus.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        EventFaceClusterActivation.objects.create(
            event=self.event,
            corpus=FaceClusterCorpus.objects.get(pk=corpus.pk),
            active=True,
            anchor_threshold=0.2,
            configuration={
                "policy_id": POLICY_ID,
                "corpus_configuration_hash": corpus.configuration_hash,
                "direct_threshold": 0.363,
                "anchor_threshold": 0.2,
            },
            configuration_hash=cluster_expansion_policy_hash(corpus.configuration_hash, 0.363, 0.2),
            approved_evaluation_report_hash="d" * 64,
        )
        claimed = self.claim(search)

        with self.assertLogs("selfie_search.services.jobs", level="INFO") as logs:
            with self.captureOnCommitCallbacks(execute=True):
                complete_search_attempt(
                    claimed.attempt.id,
                    result=self.result(),
                    storage=self.storage,
                )

        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.SEARCH_UNAVAILABLE)
        events = [json.loads(line.split(":", 2)[2]) for line in logs.output]
        ranking = next(event for event in events if event["event"] == "selfie_ranking_finished")
        terminal = next(event for event in events if event["event"] == "selfie_search_terminal")
        self.assertEqual(ranking["cluster_expansion_outcome"], "no_strong_anchor")
        self.assertIsNone(ranking["cluster_corpus_version"])
        self.assertIsNone(ranking["cluster_configuration_hash"])
        self.assertIsNone(ranking["cluster_expansion_ms"])
        self.assertEqual(terminal["status"], SelfieSearch.Status.SEARCH_UNAVAILABLE)
        self.assertEqual(terminal["matched_photo_count"], 0)
        self.assertEqual(terminal["direct_matched_photo_count"], 0)
        self.assertEqual(terminal["cluster_expanded_photo_count"], 0)
        self.assertIsNone(terminal["cluster_corpus_version"])
        self.assertIsNone(terminal["cluster_configuration_hash"])

    @override_settings(SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=True)
    def test_member_read_database_error_keeps_outer_completion_transaction_usable(self) -> None:
        search = self.make_search(with_candidate=False)
        anchor = self.add_candidate(search=search, photo_id="member-error-anchor", distance=0.1)
        member = self.add_candidate(search=search, photo_id="member-error-member", distance=0.4)
        corpus = FaceClusterCorpus.objects.create(
            event=self.event,
            version=1,
            status=FaceClusterCorpus.Status.BUILDING,
            algorithm_version="guarded-graph-v1",
            configuration={
                "face_embedding_generations": search.configuration[
                    "gallery_face_embedding_generations"
                ]
            },
            configuration_hash="a" * 64,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            model_version="sface",
            embedding_dimensions=128,
            edge_threshold=0.2,
            representative_threshold=0.2,
            distance_block_size=1,
            max_candidate_edges=1,
        )
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=corpus,
            cluster_key="member-error",
            representative_detection=anchor,
            member_count=2,
        )
        for index, detection in enumerate((anchor, member)):
            FaceClusterMember.objects.create(
                event=self.event,
                corpus=corpus,
                cluster=cluster,
                detection=detection,
                member_index=index,
                distance_to_representative=index / 10,
            )
        FaceClusterCorpus.objects.filter(pk=corpus.pk).update(
            status=FaceClusterCorpus.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        EventFaceClusterActivation.objects.create(
            event=self.event,
            corpus=FaceClusterCorpus.objects.get(pk=corpus.pk),
            active=True,
            anchor_threshold=0.2,
            configuration={
                "policy_id": POLICY_ID,
                "corpus_configuration_hash": corpus.configuration_hash,
                "direct_threshold": 0.363,
                "anchor_threshold": 0.2,
            },
            configuration_hash=cluster_expansion_policy_hash(corpus.configuration_hash, 0.363, 0.2),
            approved_evaluation_report_hash="j" * 64,
        )
        claimed = self.claim(search)

        def broken_gallery_queryset(*_args, **_kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM missing_cluster_member_relation")
            raise AssertionError("the database query must fail")

        with patch(
            "selfie_search.services.cluster_expansion.gallery_photo_queryset",
            side_effect=broken_gallery_queryset,
        ):
            complete_search_attempt(claimed.attempt.id, result=self.result(), storage=self.storage)

        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.READY)
        self.assertEqual(search.direct_matched_photo_count, 1)
        self.assertEqual(search.cluster_expanded_photo_count, 0)
        self.assertEqual(search.final_matched_photo_count, 1)
        self.assertEqual(search.cluster_expansion_outcome, "corpus_unavailable")
        self.assertEqual(
            list(search.results.values_list("photo_id", "primary_source")),
            [("member-error-anchor", "direct")],
        )
        self.assertEqual(
            SelfieSearchDirectEvidence.objects.filter(result__search=search).count(), 1
        )
        self.assertEqual(
            SelfieSearchClusterEvidence.objects.filter(result__search=search).count(), 0
        )
        self.assertEqual(self.storage.deleted, ["selfie-search/0123456789abcdef0123456789abcdef"])

    @override_settings(SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=True)
    def test_cluster_evidence_persistence_failure_rolls_back_accepted_callback(self) -> None:
        search = self.make_search(with_candidate=False)
        anchor = self.add_candidate(search=search, photo_id="rollback-anchor", distance=0.1)
        member = self.add_candidate(search=search, photo_id="rollback-member", distance=0.4)
        corpus = FaceClusterCorpus.objects.create(
            event=self.event,
            version=1,
            status=FaceClusterCorpus.Status.BUILDING,
            algorithm_version="guarded-graph-v1",
            configuration={
                "face_embedding_generations": search.configuration[
                    "gallery_face_embedding_generations"
                ]
            },
            configuration_hash="e" * 64,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            model_version="sface",
            embedding_dimensions=128,
            edge_threshold=0.2,
            representative_threshold=0.2,
            distance_block_size=1,
            max_candidate_edges=1,
        )
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=corpus,
            cluster_key="rollback",
            representative_detection=anchor,
            member_count=2,
        )
        for index, detection in enumerate((anchor, member)):
            FaceClusterMember.objects.create(
                event=self.event,
                corpus=corpus,
                cluster=cluster,
                detection=detection,
                member_index=index,
                distance_to_representative=index / 10,
            )
        FaceClusterCorpus.objects.filter(pk=corpus.pk).update(
            status=FaceClusterCorpus.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        EventFaceClusterActivation.objects.create(
            event=self.event,
            corpus=FaceClusterCorpus.objects.get(pk=corpus.pk),
            active=True,
            anchor_threshold=0.2,
            configuration={
                "policy_id": POLICY_ID,
                "corpus_configuration_hash": corpus.configuration_hash,
                "direct_threshold": 0.363,
                "anchor_threshold": 0.2,
            },
            configuration_hash=cluster_expansion_policy_hash(corpus.configuration_hash, 0.363, 0.2),
            approved_evaluation_report_hash="g" * 64,
        )
        claimed = self.claim(search)
        observed: dict[str, int] = {}

        def reject_cluster_evidence(*_args, **_kwargs) -> None:
            observed["results"] = SelfieSearchResult.objects.filter(search=search).count()
            observed["direct_evidence"] = SelfieSearchDirectEvidence.objects.filter(
                result__search=search
            ).count()
            raise IntegrityError("cluster evidence persistence failed")

        with patch(
            "selfie_search.services.jobs.SelfieSearchClusterEvidence.objects.bulk_create",
            side_effect=reject_cluster_evidence,
        ):
            with self.assertRaises(IntegrityError):
                complete_search_attempt(
                    claimed.attempt.id, result=self.result(), storage=self.storage
                )

        self.assertEqual(observed, {"results": 2, "direct_evidence": 1})
        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.PROCESSING)
        self.assertEqual(search.intended_terminal_status, "")
        self.assertIsNone(search.final_matched_photo_count)
        self.assertIsNone(search.direct_matched_photo_count)
        self.assertIsNone(search.cluster_expanded_photo_count)
        self.assertIsNone(search.strong_anchor_count)
        self.assertIsNone(search.expanded_cluster_count)
        self.assertIsNone(search.cluster_corpus_id)
        self.assertIsNone(search.cluster_corpus_version)
        self.assertIsNone(search.cluster_configuration_hash)
        self.assertIsNone(search.cluster_expansion_outcome)
        self.assertEqual(SelfieSearchResult.objects.filter(search=search).count(), 0)
        self.assertEqual(
            SelfieSearchDirectEvidence.objects.filter(result__search=search).count(), 0
        )
        self.assertEqual(
            SelfieSearchClusterEvidence.objects.filter(result__search=search).count(), 0
        )
        self.assertEqual(self.storage.deleted, [])

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
        self.assertEqual(
            list(search.results.values_list("primary_source", flat=True)),
            ["direct"],
        )
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

    def test_successful_completion_emits_v2_ranking_and_terminal_source_counts(self) -> None:
        search = self.make_search()
        claimed = self.claim(search)

        with self.assertLogs("selfie_search.services.jobs", level="INFO") as logs:
            with self.captureOnCommitCallbacks(execute=True):
                complete_search_attempt(
                    claimed.attempt.id,
                    result=self.result(),
                    storage=self.storage,
                )

        events = [json.loads(line.split(":", 2)[2]) for line in logs.output]
        ranking = next(event for event in events if event["event"] == "selfie_ranking_finished")
        terminal = next(event for event in events if event["event"] == "selfie_search_terminal")
        assert ranking["schema_version"] == 2
        assert ranking["direct_matched_photo_count"] == 1
        assert ranking["cluster_expanded_photo_count"] == 0
        assert ranking["final_matched_photo_count"] == 1
        assert ranking["cluster_expansion_outcome"] == "disabled"
        assert terminal["schema_version"] == 2
        assert terminal["matched_photo_count"] == 1
        assert terminal["direct_matched_photo_count"] == 1
        assert terminal["cluster_expanded_photo_count"] == 0

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

    def test_domain_failure_and_projected_cohort_delete_before_terminal_states(self) -> None:
        failure_search = self.make_search()
        failure_claim = self.claim(failure_search)
        failure = fail_search_attempt(
            failure_claim.attempt.id,
            error_code="no_face_detected",
            retryable=False,
            storage=self.storage,
        )
        failure_search.refresh_from_db()

        PhotoProcessingState.objects.update(accepted_attempt=None)
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
        self.assertEqual(empty_search.status, SelfieSearch.Status.READY)
        self.assertEqual(empty_search.matched_photo_count, 1)

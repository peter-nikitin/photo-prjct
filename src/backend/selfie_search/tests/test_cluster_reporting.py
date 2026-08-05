from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo
from processing.models import (
    EventProcessingRun,
    FaceCluster,
    FaceClusterCorpus,
    FaceClusterMember,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    ProcessingAttempt,
    ProcessingJob,
)
from selfie_search.models import (
    FEEDBACK_CONSENT_TEXT_VERSION,
    SelfieSearch,
    SelfieSearchClusterEvidence,
    SelfieSearchDirectEvidence,
    SelfieSearchFeedback,
    SelfieSearchFeedbackLabel,
    SelfieSearchResult,
)
from selfie_search.services.cluster_reporting import build_cluster_expansion_report

MOSCOW = ZoneInfo("Europe/Moscow")


class ClusterExpansionReportTests(TestCase):
    def setUp(self) -> None:
        self.owner = get_user_model().objects.create_user(username="cluster-report-owner")
        self.event = Event.objects.create(
            name="Cluster report event",
            slug=f"cluster-report-{uuid4().hex[:8]}",
            start_date=date(2026, 8, 5),
            end_date=date(2026, 8, 5),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        self.event_sentinel = f"event-sentinel-{uuid4()}"
        self.photo_sentinel = f"photo-{uuid4().hex[:12]}"
        self.contact_sentinel = f"contact-{uuid4()}@example.test"

    def make_search(
        self,
        *,
        created_at: datetime,
        direct: int | None,
        expanded: int | None,
        final: int | None,
        event: Event | None = None,
    ) -> SelfieSearch:
        search = SelfieSearch.objects.create(
            event=event or self.event,
            public_token_digest=(uuid4().hex * 2)[:64],
            status=SelfieSearch.Status.READY,
            temporary_object_key="",
            configuration={"contract": 2},
            matched_photo_count=final or 0,
            direct_matched_photo_count=direct,
            cluster_expanded_photo_count=expanded,
            final_matched_photo_count=final,
            cluster_expansion_outcome=(
                SelfieSearch.ClusterExpansionOutcome.EXPANDED if expanded else None
            ),
        )
        SelfieSearch.objects.filter(pk=search.pk).update(created_at=created_at)
        search.refresh_from_db()
        return search

    def make_photo_detection(self, *, photo_id: str) -> PhotoFaceDetection:
        photo = Photo.objects.create(
            id=photo_id,
            event=self.event,
            uploaded_by=self.owner,
            original_key=f"originals/{uuid4().hex}",
            original_filename="report-photo.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        configuration_hash = "a" * 64
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash=configuration_hash,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash=configuration_hash,
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
        return PhotoFaceDetection.objects.create(
            artifact=artifact,
            attempt=attempt,
            face_index=0,
            status=PhotoFaceDetection.Status.KEPT,
        )

    def make_dual_result(self, *, search: SelfieSearch) -> SelfieSearchResult:
        anchor = self.make_photo_detection(photo_id=self.photo_sentinel)
        member = self.make_photo_detection(photo_id=f"member-{uuid4().hex[:12]}")
        corpus = FaceClusterCorpus.objects.create(
            event=self.event,
            version=1,
            status=FaceClusterCorpus.Status.BUILDING,
            algorithm_version="guarded-graph-v1",
            configuration={},
            configuration_hash="b" * 64,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            model_version="sface",
            embedding_dimensions=128,
            edge_threshold=0.2,
            representative_threshold=0.2,
            distance_block_size=2,
            max_candidate_edges=10,
            published_at=timezone.now(),
        )
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=corpus,
            cluster_key="report-cluster",
            representative_detection=anchor,
            member_count=2,
        )
        FaceClusterMember.objects.bulk_create(
            [
                FaceClusterMember(
                    event=self.event,
                    corpus=corpus,
                    cluster=cluster,
                    detection=anchor,
                    member_index=0,
                    distance_to_representative=0,
                ),
                FaceClusterMember(
                    event=self.event,
                    corpus=corpus,
                    cluster=cluster,
                    detection=member,
                    member_index=1,
                    distance_to_representative=0.1,
                ),
            ]
        )
        FaceClusterCorpus.objects.filter(pk=corpus.pk).update(
            status=FaceClusterCorpus.Status.PUBLISHED,
        )
        result = SelfieSearchResult.objects.create(
            search=search,
            photo_id=anchor.attempt.photo_id,
            primary_source=SelfieSearchResult.PrimarySource.DIRECT,
            rank=1,
        )
        SelfieSearchDirectEvidence.objects.create(
            result=result,
            detection=anchor,
            cosine_distance=0.1,
        )
        SelfieSearchClusterEvidence.objects.create(
            result=result,
            corpus=corpus,
            cluster=cluster,
            anchor_result=result,
            anchor_detection=anchor,
            member_detection=member,
            representative_distance=0.1,
            source_order=1,
        )
        return result

    def make_result(
        self,
        *,
        search: SelfieSearch,
        source: str,
        rank: int,
        photo_id: str | None = None,
    ) -> SelfieSearchResult:
        photo = Photo.objects.create(
            id=photo_id or f"report-result-{uuid4().hex[:12]}",
            event=self.event,
            uploaded_by=self.owner,
            original_key=f"originals/{uuid4().hex}",
            original_filename="report-result.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        return SelfieSearchResult.objects.create(
            search=search,
            photo=photo,
            primary_source=source,
            rank=rank,
        )

    def make_feedback(self, *, search: SelfieSearch) -> SelfieSearchFeedback:
        return SelfieSearchFeedback.objects.create(
            search=search,
            variant=SelfieSearchFeedback.Variant.RESULT_LABELS,
            contact=self.contact_sentinel,
            personal_data_consent=True,
            consent_text_version=FEEDBACK_CONSENT_TEXT_VERSION,
            consented_at=timezone.now(),
            source_status=SelfieSearch.Status.READY,
            source_matched_photo_count=search.matched_photo_count,
            source_visible_result_count=search.final_matched_photo_count or 0,
            source_configuration={},
            object_key=f"feedback/{uuid4().hex}",
            object_content_type="image/jpeg",
            object_size=1,
            object_uploaded_at=timezone.now(),
        )

    def test_report_separates_sources_and_calculates_partial_label_precision(self) -> None:
        search = self.make_search(
            created_at=datetime(2026, 8, 5, 9, 0, tzinfo=MOSCOW),
            direct=2,
            expanded=1,
            final=3,
        )
        dual = self.make_dual_result(search=search)
        direct = self.make_result(
            search=search,
            source="direct",
            rank=2,
        )
        expanded = self.make_result(
            search=search,
            source="face_cluster_expansion",
            rank=3,
        )
        feedback = self.make_feedback(search=search)
        SelfieSearchFeedbackLabel.objects.create(
            feedback=feedback, result=dual, value=SelfieSearchFeedbackLabel.Value.PRESENT
        )
        SelfieSearchFeedbackLabel.objects.create(
            feedback=feedback, result=direct, value=SelfieSearchFeedbackLabel.Value.ABSENT
        )
        SelfieSearchFeedbackLabel.objects.create(
            feedback=feedback, result=expanded, value=SelfieSearchFeedbackLabel.Value.PRESENT
        )

        report = build_cluster_expansion_report(start=date(2026, 8, 5), end=date(2026, 8, 6))

        self.assertEqual(report["results"], {"direct": 2, "expanded": 1, "dual_evidence": 1})
        self.assertEqual(report["feedback"]["direct"]["present"], 1)
        self.assertEqual(report["feedback"]["direct"]["absent"], 1)
        self.assertEqual(
            report["feedback"]["direct"]["coverage"],
            {"numerator": 2, "denominator": 2, "rate": 1.0},
        )
        self.assertEqual(
            report["feedback"]["direct"]["labelled_sample_precision"],
            {"numerator": 1, "denominator": 2, "rate": 0.5},
        )
        self.assertEqual(report["feedback"]["expanded"]["present"], 1)
        self.assertEqual(report["feedback"]["direct"]["volume"], 2)
        self.assertEqual(report["feedback"]["dual_evidence"]["volume"], 1)
        self.assertEqual(report["feedback"]["expanded"]["coverage"]["numerator"], 1)
        self.assertEqual(report["feedback"]["dual_evidence"]["coverage"]["denominator"], 1)

    def test_report_emits_only_policy_hashes_and_aggregate_search_counts(self) -> None:
        first = self.make_search(
            created_at=datetime(2026, 8, 5, 9, 0, tzinfo=MOSCOW),
            direct=1,
            expanded=0,
            final=1,
        )
        second = self.make_search(
            created_at=datetime(2026, 8, 5, 10, 0, tzinfo=MOSCOW),
            direct=1,
            expanded=0,
            final=1,
        )
        SelfieSearch.objects.filter(pk__in=(first.pk, second.pk)).update(
            cluster_configuration_hash="a" * 64
        )

        report = build_cluster_expansion_report(start=date(2026, 8, 5), end=date(2026, 8, 6))

        self.assertEqual(report["policy_hashes"], [{"hash": "a" * 64, "searches": 2}])

    def test_report_keeps_unmarked_unknown_and_reports_historical_not_available(self) -> None:
        historical = self.make_search(
            created_at=datetime(2026, 8, 5, 10, 0, tzinfo=MOSCOW),
            direct=None,
            expanded=None,
            final=None,
        )
        self.make_result(
            search=historical,
            source="direct",
            rank=1,
        )

        report = build_cluster_expansion_report(start=date(2026, 8, 5), end=date(2026, 8, 6))

        self.assertEqual(report["searches"], {"total": 1, "available": 0, "historical": 1})
        self.assertEqual(report["results"], "not_available")
        self.assertEqual(report["feedback"], "not_available")

    def test_report_with_zero_labels_keeps_current_result_unknown(self) -> None:
        search = self.make_search(
            created_at=datetime(2026, 8, 5, 11, 0, tzinfo=MOSCOW),
            direct=1,
            expanded=0,
            final=1,
        )
        self.make_result(
            search=search,
            source="direct",
            rank=1,
        )

        report = build_cluster_expansion_report(start=date(2026, 8, 5), end=date(2026, 8, 6))

        direct = report["feedback"]["direct"]
        self.assertEqual(direct["present"], 0)
        self.assertEqual(direct["absent"], 0)
        self.assertEqual(direct["unmarked"], 1)
        self.assertEqual(direct["coverage"], {"numerator": 0, "denominator": 1, "rate": 0.0})
        self.assertEqual(
            direct["labelled_sample_precision"],
            {"numerator": 0, "denominator": 0, "rate": None},
        )

    def test_report_uses_closed_open_moscow_window_and_event_filter_without_echoing_identity(
        self,
    ) -> None:
        inside = self.make_search(
            created_at=datetime(2026, 8, 6, 0, 0, tzinfo=MOSCOW),
            direct=1,
            expanded=0,
            final=1,
        )
        self.make_result(
            search=inside,
            source="direct",
            rank=1,
            photo_id=self.photo_sentinel,
        )
        other_event = Event.objects.create(
            name=self.event_sentinel,
            slug=f"other-{uuid4().hex[:8]}",
            start_date=date(2026, 8, 5),
            end_date=date(2026, 8, 5),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        outside = self.make_search(
            created_at=datetime(2026, 8, 6, 0, 0, tzinfo=MOSCOW),
            direct=1,
            expanded=0,
            final=1,
            event=other_event,
        )
        self.make_result(
            search=outside,
            source="direct",
            rank=1,
        )

        report = build_cluster_expansion_report(
            start=date(2026, 8, 5), end=date(2026, 8, 6), event=self.event
        )

        self.assertEqual(report["searches"]["total"], 0)
        self.assertNotIn(self.event_sentinel, str(report))
        self.assertNotIn(self.event_sentinel, str(report))
        self.assertNotIn(self.photo_sentinel, str(report))
        self.assertNotIn(self.contact_sentinel, str(report))

    def test_report_rejects_invalid_bounds_and_event(self) -> None:
        with self.assertRaises(ValueError):
            build_cluster_expansion_report(start=date(2026, 8, 6), end=date(2026, 8, 5))
        with self.assertRaises(ValueError):
            build_cluster_expansion_report(start=cast(Any, "2026-08-05"), end=date(2026, 8, 6))
        with self.assertRaises(ValueError):
            build_cluster_expansion_report(
                start=date(2026, 8, 5), end=date(2026, 8, 6), event="not-a-uuid"
            )

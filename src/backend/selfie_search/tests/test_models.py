from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo
from processing.models import FaceCluster, FaceClusterCorpus, FaceClusterMember, PhotoFaceDetection
from selfie_search.models import (
    SelfieSearch,
    SelfieSearchAttempt,
    SelfieSearchClusterEvidence,
    SelfieSearchDirectEvidence,
    SelfieSearchFeedback,
    SelfieSearchFeedbackAccessAudit,
    SelfieSearchFeedbackLabel,
    SelfieSearchJob,
    SelfieSearchResult,
)


class SelfieSearchModelTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="selfie-search-owner")
        self.event = self.make_event("one")
        self.other_event = self.make_event("two")
        self.photo = self.make_photo("one", self.event)
        self.expanded_photo = self.make_photo("expanded", self.event)
        self.dual_photo = self.make_photo("dual", self.event)
        self.other_photo = self.make_photo("two", self.other_event)
        self.search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="a" * 64,
            temporary_object_key="selfie-search/one.jpg",
            configuration={"embedding_model": "sface-v1"},
        )

    def make_event(self, suffix: str) -> Event:
        return Event.objects.create(
            name=f"Selfie search {suffix}",
            slug=f"selfie-search-{suffix}",
            start_date=date(2026, 7, 30),
            end_date=date(2026, 7, 30),
            city="Moscow",
        )

    def make_photo(self, suffix: str, event: Event) -> Photo:
        return Photo.objects.create(
            id=f"selfie-{suffix}",
            event=event,
            uploaded_by=self.user,
            original_key=f"private/{suffix}.jpg",
            original_filename=f"{suffix}.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def test_search_state_is_limited_to_public_states(self) -> None:
        self.search.status = "invented"

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.search.save()

    def test_one_job_is_allowed_per_search(self) -> None:
        SelfieSearchJob.objects.create(search=self.search, configuration={})

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SelfieSearchJob.objects.create(search=self.search, configuration={})

    def test_result_photo_and_rank_are_unique_per_search(self) -> None:
        detection_id = self.make_detection_id()
        result = SelfieSearchResult.objects.create(
            search=self.search,
            photo=self.photo,
            rank=1,
        )
        SelfieSearchDirectEvidence.objects.create(
            result=result, detection_id=detection_id, cosine_distance=0.1
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SelfieSearchResult.objects.create(
                    search=self.search,
                    photo=self.photo,
                    rank=2,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SelfieSearchResult.objects.create(
                    search=self.search,
                    photo=self.other_photo,
                    rank=1,
                )

    def test_result_rejects_cross_event_evidence(self) -> None:
        result = SelfieSearchResult(
            search=self.search,
            photo=self.photo,
            rank=1,
        )
        result.save()
        evidence = SelfieSearchDirectEvidence(
            result=result,
            detection_id=self.make_detection_id(other_event=True),
            cosine_distance=0.1,
        )

        with self.assertRaises(ValidationError):
            evidence.full_clean()

    def test_ready_result_rows_are_immutable(self) -> None:
        result = self.make_result()
        self.search.status = SelfieSearch.Status.READY
        self.search.save()
        result.rank = 2

        with self.assertRaises(ValidationError):
            result.save()

    def test_historical_search_expansion_snapshot_fields_are_null(self) -> None:
        for field_name in (
            "cluster_corpus",
            "cluster_corpus_version",
            "cluster_configuration_hash",
            "direct_matched_photo_count",
            "cluster_expanded_photo_count",
            "final_matched_photo_count",
            "strong_anchor_count",
            "expanded_cluster_count",
            "cluster_expansion_outcome",
        ):
            self.assertIsNone(getattr(self.search, field_name))

    def test_direct_evidence_is_unique_and_keeps_finite_distance(self) -> None:
        result = self.make_result()
        evidence = result.direct_evidence
        self.assertEqual(evidence.detection.attempt.event_id, self.event.id)
        self.assertEqual(evidence.cosine_distance, 0.1)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SelfieSearchDirectEvidence.objects.create(
                    result=result,
                    detection=evidence.detection,
                    cosine_distance=0.2,
                )

        evidence.cosine_distance = float("nan")
        with self.assertRaises(ValidationError):
            evidence.full_clean()

    def test_cluster_evidence_supports_expanded_and_dual_results(self) -> None:
        anchor_result = self.make_result()
        anchor_evidence = anchor_result.direct_evidence
        member_detection_id = self.make_detection_id(photo=self.expanded_photo)
        corpus = self.make_corpus()
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=corpus,
            cluster_key="cluster-1",
            representative_detection=anchor_evidence.detection,
            member_count=2,
        )
        FaceClusterMember.objects.create(
            event=self.event,
            corpus=corpus,
            cluster=cluster,
            detection=anchor_evidence.detection,
            member_index=0,
            distance_to_representative=0.0,
        )
        member_detection = PhotoFaceDetection.objects.get(pk=member_detection_id)
        FaceClusterMember.objects.create(
            event=self.event,
            corpus=corpus,
            cluster=cluster,
            detection=member_detection,
            member_index=1,
            distance_to_representative=0.2,
        )
        dual_detection = PhotoFaceDetection.objects.get(
            pk=self.make_detection_id(photo=self.dual_photo)
        )
        FaceClusterMember.objects.create(
            event=self.event,
            corpus=corpus,
            cluster=cluster,
            detection=dual_detection,
            member_index=2,
            distance_to_representative=0.3,
        )
        corpus.status = FaceClusterCorpus.Status.PUBLISHED
        corpus.published_at = timezone.now()
        corpus.save(update_fields=["status", "published_at"])

        expanded = SelfieSearchResult.objects.create(
            search=self.search,
            photo=self.expanded_photo,
            rank=2,
            primary_source=SelfieSearchResult.PrimarySource.FACE_CLUSTER_EXPANSION,
        )
        evidence = SelfieSearchClusterEvidence.objects.create(
            result=expanded,
            corpus=corpus,
            cluster=cluster,
            anchor_result=anchor_result,
            anchor_detection=anchor_evidence.detection,
            member_detection=member_detection,
            representative_distance=0.2,
            source_order=1,
        )
        self.assertEqual(evidence.result.primary_source, "face_cluster_expansion")

        dual = SelfieSearchResult.objects.create(
            search=self.search,
            photo=self.dual_photo,
            rank=3,
            primary_source=SelfieSearchResult.PrimarySource.DIRECT,
        )
        SelfieSearchDirectEvidence.objects.create(
            result=dual,
            detection=anchor_evidence.detection,
            cosine_distance=0.1,
        )
        SelfieSearchClusterEvidence.objects.create(
            result=dual,
            corpus=corpus,
            cluster=cluster,
            anchor_result=anchor_result,
            anchor_detection=anchor_evidence.detection,
            member_detection=dual_detection,
            representative_distance=0.2,
            source_order=2,
        )
        self.assertTrue(dual.direct_evidence)
        self.assertEqual(dual.cluster_evidence.count(), 1)

    def test_cluster_evidence_rejects_cross_search_and_corpus_members(self) -> None:
        anchor_result = self.make_result()
        anchor_detection = anchor_result.direct_evidence.detection
        corpus = self.make_corpus()
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=corpus,
            cluster_key="cluster-1",
            representative_detection=anchor_detection,
            member_count=1,
        )
        FaceClusterMember.objects.create(
            event=self.event,
            corpus=corpus,
            cluster=cluster,
            detection=anchor_detection,
            member_index=0,
            distance_to_representative=0.0,
        )
        corpus.status = FaceClusterCorpus.Status.PUBLISHED
        corpus.published_at = timezone.now()
        corpus.save(update_fields=["status", "published_at"])
        other_search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="b" * 64,
            temporary_object_key="selfie-search/other.jpg",
            configuration={"embedding_model": "sface-v1"},
        )
        result = SelfieSearchResult.objects.create(
            search=other_search,
            photo=self.photo,
            rank=1,
            primary_source=SelfieSearchResult.PrimarySource.FACE_CLUSTER_EXPANSION,
        )
        evidence = SelfieSearchClusterEvidence(
            result=result,
            corpus=corpus,
            cluster=cluster,
            anchor_result=anchor_result,
            anchor_detection=anchor_detection,
            member_detection=anchor_detection,
            representative_distance=0.0,
            source_order=1,
        )
        with self.assertRaises(ValidationError):
            evidence.full_clean()

    def test_result_primary_source_is_limited_to_supported_values(self) -> None:
        result = SelfieSearchResult(
            search=self.search,
            photo=self.photo,
            rank=1,
            primary_source="sentinel",
        )
        with self.assertRaises(ValidationError):
            result.full_clean()

    def test_product_models_do_not_persist_a_query_vector(self) -> None:
        for model in (SelfieSearch, SelfieSearchJob, SelfieSearchAttempt, SelfieSearchResult):
            fields = {field.name for field in model._meta.fields}
            self.assertNotIn("query_vector", fields)
            self.assertNotIn("query_embedding", fields)
            self.assertNotIn("vector", fields)

    def make_result(self, *, search=None, photo=None, other_event: bool = False):
        search = search or self.search
        photo = photo or (self.other_photo if other_event else self.photo)
        result = SelfieSearchResult.objects.create(
            search=search,
            photo=photo,
            rank=1,
        )
        SelfieSearchDirectEvidence.objects.create(
            result=result,
            detection_id=self.make_detection_id(other_event=other_event),
            cosine_distance=0.1,
        )
        return result

    def make_feedback(self, **overrides):
        values = {
            "search": self.search,
            "variant": SelfieSearchFeedback.Variant.PROBLEM,
            "contact": "person@example.test",
            "personal_data_consent": True,
            "consent_text_version": "2026-08-05",
            "consented_at": timezone.now(),
            "source_status": SelfieSearch.Status.FAILED,
            "source_matched_photo_count": 0,
            "source_visible_result_count": 0,
            "source_configuration": {"embedding_model": "sface-v1"},
            "object_key": "feedback/0123456789abcdef0123456789abcdef",
            "object_content_type": "image/jpeg",
            "object_size": 1,
            "object_uploaded_at": timezone.now(),
        }
        values.update(overrides)
        return SelfieSearchFeedback.objects.create(**values)

    def test_feedback_requires_true_personal_data_consent(self) -> None:
        feedback = SelfieSearchFeedback(
            **{
                "search": self.search,
                "variant": SelfieSearchFeedback.Variant.PROBLEM,
                "contact": "person@example.test",
                "personal_data_consent": False,
                "consent_text_version": "2026-08-05",
                "consented_at": timezone.now(),
                "source_status": SelfieSearch.Status.FAILED,
                "source_configuration": {},
                "object_key": "feedback/0123456789abcdef0123456789abcdef",
                "object_content_type": "image/jpeg",
                "object_size": 1,
                "object_uploaded_at": timezone.now(),
            }
        )

        with self.assertRaises(ValidationError):
            feedback.full_clean()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                feedback.save(force_insert=True)

    def test_feedback_normalizes_optional_contact_and_rejects_unsafe_values(self) -> None:
        for contact in ("", "   "):
            with self.subTest(contact=repr(contact)):
                feedback = SelfieSearchFeedback(
                    search=self.search,
                    variant=SelfieSearchFeedback.Variant.PROBLEM,
                    contact=contact,
                    personal_data_consent=True,
                    consent_text_version="2026-08-05",
                    consented_at=timezone.now(),
                    source_status=SelfieSearch.Status.FAILED,
                    source_configuration={"embedding_model": "sface-v1"},
                    object_key=f"feedback/{uuid4().hex}",
                    object_content_type="image/jpeg",
                    object_size=1,
                    object_uploaded_at=timezone.now(),
                )

                feedback.full_clean()
                self.assertEqual(feedback.contact, "")

        for contact in ("x\x00@example.test", "x" * 255):
            with self.subTest(contact=repr(contact)):
                feedback = SelfieSearchFeedback(
                    search=self.search,
                    variant=SelfieSearchFeedback.Variant.PROBLEM,
                    contact=contact,
                    personal_data_consent=True,
                    consent_text_version="2026-08-05",
                    consented_at=timezone.now(),
                    source_status=SelfieSearch.Status.FAILED,
                    source_configuration={"embedding_model": "sface-v1"},
                    object_key=f"feedback/{uuid4().hex}",
                    object_content_type="image/jpeg",
                    object_size=1,
                    object_uploaded_at=timezone.now(),
                )

                with self.assertRaises(ValidationError):
                    feedback.full_clean()

    def test_feedback_is_unique_per_search(self) -> None:
        self.make_feedback()

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_feedback()

    def test_feedback_variant_matches_terminal_source_snapshot(self) -> None:
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_feedback(
                    variant=SelfieSearchFeedback.Variant.RESULT_LABELS,
                    source_status=SelfieSearch.Status.FAILED,
                    source_visible_result_count=1,
                )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_feedback(
                    variant=SelfieSearchFeedback.Variant.RESULT_LABELS,
                    source_status=SelfieSearch.Status.READY,
                    source_visible_result_count=0,
                )

    def test_problem_feedback_does_not_accept_labels(self) -> None:
        feedback = self.make_feedback()
        result = self.make_result()
        label = SelfieSearchFeedbackLabel(
            feedback=feedback,
            result=result,
            value=SelfieSearchFeedbackLabel.Value.PRESENT,
        )

        with self.assertRaises(ValidationError):
            label.full_clean()

    def test_feedback_label_is_unique_per_result_membership(self) -> None:
        self.search.status = SelfieSearch.Status.READY
        self.search.save(update_fields=["status"])
        result = self.make_result()
        feedback = self.make_feedback(
            variant=SelfieSearchFeedback.Variant.RESULT_LABELS,
            source_status=SelfieSearch.Status.READY,
            source_matched_photo_count=1,
            source_visible_result_count=1,
        )
        SelfieSearchFeedbackLabel.objects.create(
            feedback=feedback,
            result=result,
            value=SelfieSearchFeedbackLabel.Value.PRESENT,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SelfieSearchFeedbackLabel.objects.create(
                    feedback=feedback,
                    result=result,
                    value=SelfieSearchFeedbackLabel.Value.ABSENT,
                )

    def test_feedback_label_rejects_result_from_another_search(self) -> None:
        other_search = SelfieSearch.objects.create(
            event=self.other_event,
            public_token_digest="b" * 64,
            temporary_object_key="selfie-search/two.jpg",
            configuration={"embedding_model": "sface-v1"},
            status=SelfieSearch.Status.READY,
        )
        other_result = self.make_result(search=other_search, other_event=True)
        feedback = self.make_feedback(
            variant=SelfieSearchFeedback.Variant.RESULT_LABELS,
            source_status=SelfieSearch.Status.READY,
            source_matched_photo_count=1,
            source_visible_result_count=1,
        )
        label = SelfieSearchFeedbackLabel(
            feedback=feedback,
            result=other_result,
            value=SelfieSearchFeedbackLabel.Value.PRESENT,
        )

        with self.assertRaises(ValidationError):
            label.full_clean()

    def test_feedback_and_labels_are_immutable_after_creation(self) -> None:
        feedback = self.make_feedback()
        feedback.contact = "changed@example.test"

        with self.assertRaises(ValidationError):
            feedback.save()

        self.search.status = SelfieSearch.Status.READY
        self.search.save(update_fields=["status"])
        labels_feedback = self.make_feedback(
            search=SelfieSearch.objects.create(
                event=self.other_event,
                public_token_digest="c" * 64,
                temporary_object_key="selfie-search/three.jpg",
                configuration={"embedding_model": "sface-v1"},
                status=SelfieSearch.Status.READY,
            ),
            variant=SelfieSearchFeedback.Variant.RESULT_LABELS,
            source_status=SelfieSearch.Status.READY,
            source_matched_photo_count=1,
            source_visible_result_count=1,
            object_key="feedback/abcdefabcdefabcdefabcdefabcdefab",
        )
        # A label mutation is rejected independently of the feedback row mutation guard.
        label = SelfieSearchFeedbackLabel.objects.create(
            feedback=labels_feedback,
            result=self.make_result(search=labels_feedback.search, other_event=True),
            value=SelfieSearchFeedbackLabel.Value.PRESENT,
        )
        label.value = SelfieSearchFeedbackLabel.Value.ABSENT

        with self.assertRaises(ValidationError):
            label.save()

    def test_feedback_access_audit_is_append_only(self) -> None:
        feedback = self.make_feedback()
        audit = SelfieSearchFeedbackAccessAudit.objects.create(
            feedback=feedback,
            staff=self.user,
            action=SelfieSearchFeedbackAccessAudit.Action.CONTACT_VIEW,
        )
        audit.action = SelfieSearchFeedbackAccessAudit.Action.SELFIE_VIEW

        with self.assertRaises(ValidationError):
            audit.save()

    def test_sensitive_feedback_permission_is_registered(self) -> None:
        permission = Permission.objects.get(
            content_type__app_label="selfie_search",
            codename="view_sensitive_feedback",
        )
        self.assertEqual(permission.name, "Can view sensitive selfie search feedback")

    def test_contact_is_not_indexed_or_rendered_in_feedback_string(self) -> None:
        feedback = self.make_feedback()
        contact_field = SelfieSearchFeedback._meta.get_field("contact")
        self.assertFalse(contact_field.db_index)
        self.assertNotIn(feedback.contact, str(feedback))

    def make_embedding_id(self, *, other_event: bool = False, photo=None):
        from processing.models import (
            EventProcessingRun,
            FaceEmbedding,
            FaceProcessingAttemptArtifact,
            PhotoFaceDetection,
            ProcessingAttempt,
            ProcessingJob,
        )

        event = self.other_event if other_event else self.event
        photo = photo or (self.other_photo if other_event else self.photo)
        run = EventProcessingRun.objects.create(
            event=event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="b" * 64,
        )
        job = ProcessingJob.objects.create(
            event=event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="b" * 64,
            input_fingerprint={},
        )
        attempt = ProcessingAttempt.objects.create(
            event=event,
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
        )
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        detection = PhotoFaceDetection.objects.create(
            artifact=artifact,
            attempt=attempt,
            face_index=0,
            status=PhotoFaceDetection.Status.KEPT,
        )
        return FaceEmbedding.objects.create(detection=detection).id

    def make_detection_id(self, *, other_event: bool = False, photo=None):
        from processing.models import FaceEmbedding

        return FaceEmbedding.objects.get(
            pk=self.make_embedding_id(other_event=other_event, photo=photo)
        ).detection_id

    def make_corpus(self, *, event=None) -> FaceClusterCorpus:
        event = event or self.event
        return FaceClusterCorpus.objects.create(
            event=event,
            version=1,
            status=FaceClusterCorpus.Status.BUILDING,
            algorithm_version="test-v1",
            configuration={"edge_threshold": 0.4},
            configuration_hash="c" * 64,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            model_version="sface",
            embedding_dimensions=128,
            edge_threshold=0.4,
            representative_threshold=0.5,
            distance_block_size=10,
            max_candidate_edges=100,
            published_at=timezone.now(),
        )

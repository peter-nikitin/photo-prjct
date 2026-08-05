from __future__ import annotations

from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
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
    ProcessingAttempt,
    ProcessingJob,
)


class FaceClusterModelTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="cluster-model-owner")
        self.event = self.make_event("main")
        self.other_event = self.make_event("other")
        self.detection, self.other_detection = self.make_detections()

    def make_event(self, suffix: str) -> Event:
        return Event.objects.create(
            name=f"Cluster event {suffix}",
            slug=f"cluster-event-{suffix}-{uuid4().hex[:8]}",
            start_date=date(2026, 8, 5),
            end_date=date(2026, 8, 5),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )

    def make_detections(self) -> tuple[PhotoFaceDetection, PhotoFaceDetection]:
        detections: list[PhotoFaceDetection] = []
        for number, event in enumerate((self.event, self.other_event), start=1):
            photo = Photo.objects.create(
                id=f"cluster-model-{number}",
                event=event,
                uploaded_by=self.user,
                original_key=f"originals/cluster-model-{number}",
                original_filename=f"cluster-model-{number}.jpg",
                original_size=1,
                original_content_type="image/jpeg",
                uploaded_at=timezone.now(),
            )
            run = EventProcessingRun.objects.create(
                event=event,
                contract_version=1,
                processor_type="face_embedding",
                processor_version=1,
                configuration={"model": "sface"},
                configuration_hash="a" * 64,
            )
            job = ProcessingJob.objects.create(
                event=event,
                run=run,
                photo=photo,
                contract_version=1,
                processor_type="face_embedding",
                processor_version=1,
                configuration={"model": "sface"},
                configuration_hash="a" * 64,
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
                configuration={"model": "sface"},
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
                vector=[1.0, 0.0],
                metadata={},
            )
            detections.append(detection)
        return detections[0], detections[1]

    def make_corpus(
        self, *, event: Event | None = None, status: str = "building"
    ) -> FaceClusterCorpus:
        return FaceClusterCorpus.objects.create(
            event=event or self.event,
            version=1,
            status=status,
            algorithm_version="guarded-graph-v1",
            configuration={"dimensions": 2, "generations": [{"model": "sface"}]},
            configuration_hash="b" * 64,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            model_version="sface",
            embedding_dimensions=2,
            edge_threshold=0.2,
            representative_threshold=0.2,
            distance_block_size=2,
            max_candidate_edges=100,
            input_count=2,
            cluster_count=1,
            member_count=2,
            singleton_count=0,
            candidate_edge_count=1,
        )

    def test_status_vocabularies_and_bounded_version_are_explicit(self) -> None:
        self.assertEqual(
            {value for value, _ in FaceClusterCorpus.Status.choices},
            {"building", "failed", "published"},
        )
        with self.assertRaises(ValidationError):
            FaceClusterCorpus(
                event=self.event,
                version=0,
                status="building",
                algorithm_version="v1",
                configuration={},
                configuration_hash="a" * 64,
                processor_type="face_embedding",
                processor_version=1,
                model_version="sface",
                embedding_dimensions=2,
                edge_threshold=0.2,
                representative_threshold=0.2,
                distance_block_size=1,
                max_candidate_edges=1,
            ).full_clean()

    def test_cluster_members_and_active_event_selection_are_unique(self) -> None:
        corpus = self.make_corpus()
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=corpus,
            cluster_key="cluster-0001",
            representative_detection=self.detection,
            member_count=1,
        )
        FaceClusterMember.objects.create(
            event=self.event,
            corpus=corpus,
            cluster=cluster,
            detection=self.detection,
            member_index=0,
            distance_to_representative=0.0,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FaceClusterMember.objects.create(
                    event=self.event,
                    corpus=corpus,
                    cluster=cluster,
                    detection=self.detection,
                    member_index=1,
                    distance_to_representative=0.1,
                )

        corpus.status = "published"
        corpus.published_at = timezone.now()
        corpus.save(update_fields=["status", "published_at"])

        EventFaceClusterActivation.objects.create(
            event=self.event,
            corpus=corpus,
            active=True,
            anchor_threshold=0.1,
            configuration={"anchor_threshold": 0.1},
            configuration_hash="c" * 64,
            approved_evaluation_report_hash="d" * 64,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EventFaceClusterActivation.objects.create(
                    event=self.event,
                    corpus=corpus,
                    active=True,
                    anchor_threshold=0.1,
                    configuration={"anchor_threshold": 0.1},
                    configuration_hash="e" * 64,
                    approved_evaluation_report_hash="f" * 64,
                )

    def test_published_corpus_rows_are_immutable(self) -> None:
        corpus = self.make_corpus(status="building")
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=corpus,
            cluster_key="cluster-0001",
            representative_detection=self.detection,
            member_count=1,
        )
        member = FaceClusterMember.objects.create(
            event=self.event,
            corpus=corpus,
            cluster=cluster,
            detection=self.detection,
            member_index=0,
            distance_to_representative=0.0,
        )
        corpus.status = "published"
        corpus.published_at = timezone.now()
        corpus.save(update_fields=["status", "published_at"])
        corpus.edge_threshold = 0.1
        with self.assertRaises(ValidationError):
            corpus.save(update_fields=["edge_threshold"])

        cluster = FaceCluster.objects.get(pk=cluster.pk)
        cluster.cluster_key = "changed"
        with self.assertRaises(ValidationError):
            cluster.save(update_fields=["cluster_key"])

        member.distance_to_representative = 0.1
        with self.assertRaises(ValidationError):
            member.save(update_fields=["distance_to_representative"])

    def test_cross_event_and_corpus_detection_relationships_are_rejected(self) -> None:
        corpus = self.make_corpus()
        foreign_corpus = self.make_corpus(event=self.other_event)
        with self.assertRaises(ValidationError):
            FaceCluster(
                event=self.event,
                corpus=foreign_corpus,
                cluster_key="foreign",
                representative_detection=self.detection,
                member_count=1,
            ).full_clean()

        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=corpus,
            cluster_key="cluster-0001",
            representative_detection=self.detection,
            member_count=1,
        )
        with self.assertRaises(ValidationError):
            FaceClusterMember(
                event=self.event,
                corpus=corpus,
                cluster=cluster,
                detection=self.other_detection,
                member_index=0,
                distance_to_representative=0.0,
            ).full_clean()

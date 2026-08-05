from __future__ import annotations

import hashlib
import json
from datetime import date
from math import sqrt
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo
from selfie_search.services.submission import _face_embedding_generations

from processing.models import (
    EventFaceClusterActivation,
    EventProcessingRun,
    FaceClusterCorpus,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import FACE_EMBEDDING_CONFIGURATION
from processing.services.face_cluster_corpora import (
    activate_face_cluster_corpus,
    build_face_cluster_corpus,
)
from processing.services.face_cohort import (
    CompatibleFaceEmbedding,
    load_compatible_face_embeddings,
)


class FaceClusterCorpusTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="cluster-corpus-owner")
        self.event = self.make_event("main")
        self.other_event = self.make_event("other")
        self.generations = (
            self.generation(contract_version=1, processor_version=1),
            self.generation(contract_version=2, processor_version=2),
        )

    def make_event(self, suffix: str) -> Event:
        return Event.objects.create(
            name=f"Corpus event {suffix}",
            slug=f"corpus-event-{suffix}-{uuid4().hex[:8]}",
            start_date=date(2026, 8, 5),
            end_date=date(2026, 8, 5),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )

    def generation(self, *, contract_version: int, processor_version: int) -> dict[str, object]:
        configuration = {**FACE_EMBEDDING_CONFIGURATION, "generation": contract_version}
        return {
            "contract_version": contract_version,
            "processor_type": "face_embedding",
            "processor_version": processor_version,
            "configuration": configuration,
            "configuration_hash": hashlib.sha256(
                json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "model": "sface",
        }

    def make_embedding(
        self,
        *,
        event: Event,
        photo_id: str,
        vector: list[float],
        contract_version: int = 1,
        processor_version: int = 1,
        model: str = "sface",
    ) -> FaceEmbedding:
        configuration = next(
            generation["configuration"]
            for generation in self.generations
            if generation["contract_version"] == contract_version
        )
        configuration_hash = next(
            generation["configuration_hash"]
            for generation in self.generations
            if generation["contract_version"] == contract_version
        )
        photo = Photo.objects.create(
            id=photo_id,
            event=event,
            uploaded_by=self.user,
            original_key=f"originals/{photo_id}",
            original_filename=f"{photo_id}.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        run = EventProcessingRun.objects.create(
            event=event,
            contract_version=contract_version,
            processor_type="face_embedding",
            processor_version=processor_version,
            configuration=configuration,
            configuration_hash=configuration_hash,
        )
        job = ProcessingJob.objects.create(
            event=event,
            run=run,
            photo=photo,
            contract_version=contract_version,
            processor_type="face_embedding",
            processor_version=processor_version,
            configuration=configuration,
            configuration_hash=configuration_hash,
            input_fingerprint={},
        )
        attempt = ProcessingAttempt.objects.create(
            event=event,
            run=run,
            job=job,
            photo=photo,
            contract_version=contract_version,
            processor_type="face_embedding",
            processor_version=processor_version,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        state = PhotoProcessingState.objects.create(
            photo=photo,
            processor_type="face_embedding",
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt,
        )
        assert state.pk
        artifact = FaceProcessingAttemptArtifact.objects.create(attempt=attempt)
        detection = PhotoFaceDetection.objects.create(
            artifact=artifact,
            attempt=attempt,
            face_index=0,
            status=PhotoFaceDetection.Status.KEPT,
        )
        return FaceEmbedding.objects.create(
            detection=detection,
            model_version=model,
            vector=vector,
            metadata={},
        )

    def make_runtime_compatible(self, corpus: FaceClusterCorpus) -> FaceClusterCorpus:
        FaceClusterCorpus.objects.filter(pk=corpus.pk).update(
            configuration={"face_embedding_generations": list(_face_embedding_generations())},
            model_version="sface",
            embedding_dimensions=128,
        )
        return FaceClusterCorpus.objects.get(pk=corpus.pk)

    def test_shared_loader_is_event_and_generation_scoped(self) -> None:
        accepted = self.make_embedding(event=self.event, photo_id="accepted", vector=[1.0, 0.0])
        preview = self.make_embedding(
            event=self.event,
            photo_id="preview",
            vector=[0.0, 1.0],
            contract_version=2,
            processor_version=2,
        )
        foreign = self.make_embedding(event=self.other_event, photo_id="foreign", vector=[1.0, 0.0])
        rows = load_compatible_face_embeddings(self.event, self.generations, 2)
        self.assertEqual(
            {row.photo_id for row in rows},
            {
                str(accepted.detection.attempt.photo_id),
                str(preview.detection.attempt.photo_id),
            },
        )
        self.assertNotIn(str(foreign.detection.attempt.photo_id), {row.photo_id for row in rows})
        self.assertTrue(all(isinstance(row, CompatibleFaceEmbedding) for row in rows))

    def test_builder_freezes_exact_membership_including_singletons(self) -> None:
        self.make_embedding(event=self.event, photo_id="one", vector=[1.0, 0.0])
        self.make_embedding(event=self.event, photo_id="two", vector=[0.99, sqrt(1 - 0.99**2)])
        self.make_embedding(event=self.event, photo_id="three", vector=[0.0, 1.0])

        corpus = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations[:1],
            dimensions=2,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )

        self.assertEqual(corpus.status, FaceClusterCorpus.Status.PUBLISHED)
        self.assertEqual(corpus.input_count, 3)
        self.assertEqual(corpus.member_count, 3)
        self.assertEqual(corpus.cluster_count, 2)
        self.assertEqual(corpus.singleton_count, 1)
        self.assertEqual(corpus.clusters.count(), 2)
        self.assertEqual(corpus.clusters.get(member_count=2).members.count(), 2)

    def test_repeated_builds_have_reproducible_configuration_and_membership_hashes(self) -> None:
        self.make_embedding(event=self.event, photo_id="hash-one", vector=[1.0, 0.0])
        first = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations[:1],
            dimensions=2,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        second = build_face_cluster_corpus(
            event=self.event,
            version=2,
            generations=self.generations[:1],
            dimensions=2,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        self.assertEqual(first.configuration_hash, second.configuration_hash)
        self.assertEqual(first.membership_hash, second.membership_hash)

    def test_candidate_edge_limit_leaves_failed_non_selectable_corpus(self) -> None:
        self.make_embedding(event=self.event, photo_id="limit-one", vector=[1.0, 0.0])
        self.make_embedding(event=self.event, photo_id="limit-two", vector=[1.0, 0.0])
        with self.assertRaises(ValueError):
            build_face_cluster_corpus(
                event=self.event,
                version=1,
                generations=self.generations[:1],
                dimensions=2,
                edge_threshold=0.0,
                representative_threshold=0.1,
                distance_block_size=2,
                max_candidate_edges=0,
            )
        corpus = FaceClusterCorpus.objects.get(event=self.event, version=1)
        self.assertEqual(corpus.status, FaceClusterCorpus.Status.FAILED)
        self.assertIsNone(corpus.published_at)
        self.assertFalse(corpus.clusters.exists())

    def test_activation_replaces_only_the_event_pointer_after_explicit_review(self) -> None:
        self.make_embedding(event=self.event, photo_id="activate", vector=[1.0, 0.0])
        first = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations[:1],
            dimensions=2,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        second = build_face_cluster_corpus(
            event=self.event,
            version=2,
            generations=self.generations[:1],
            dimensions=2,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        first = self.make_runtime_compatible(first)
        second = self.make_runtime_compatible(second)
        old = activate_face_cluster_corpus(
            event=self.event,
            corpus=first,
            configuration_hash=first.configuration_hash,
            anchor_threshold=0.05,
            evaluation_report_hash="a" * 64,
            numeric_gates_reviewed=True,
        )

        activation = activate_face_cluster_corpus(
            event=self.event,
            corpus=second,
            configuration_hash=second.configuration_hash,
            anchor_threshold=0.05,
            evaluation_report_hash="b" * 64,
            numeric_gates_reviewed=True,
        )

        old.refresh_from_db()
        self.assertFalse(old.active)
        self.assertIsNotNone(old.deactivated_at)
        self.assertTrue(activation.active)
        self.assertEqual(
            activation.configuration,
            {"direct_threshold": 0.363, "anchor_threshold": 0.05},
        )
        active = EventFaceClusterActivation.objects.filter(event=self.event, active=True).get()
        self.assertEqual(active, activation)

    def test_activation_denies_unpublished_mismatched_or_unreviewed_inputs(self) -> None:
        self.make_embedding(event=self.event, photo_id="deny", vector=[1.0, 0.0])
        corpus = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations[:1],
            dimensions=2,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        with self.assertRaises(ValueError):
            activate_face_cluster_corpus(
                event=self.event,
                corpus=corpus,
                configuration_hash="0" * 64,
                anchor_threshold=0.05,
                evaluation_report_hash="a" * 64,
                numeric_gates_reviewed=True,
            )
        with self.assertRaises(ValueError):
            activate_face_cluster_corpus(
                event=self.event,
                corpus=corpus,
                configuration_hash=corpus.configuration_hash,
                anchor_threshold=0.05,
                evaluation_report_hash="a" * 64,
                numeric_gates_reviewed=False,
            )
        with self.assertRaises(ValueError):
            activate_face_cluster_corpus(
                event=self.event,
                corpus=corpus,
                configuration_hash=corpus.configuration_hash,
                anchor_threshold=1.0,
                evaluation_report_hash="a" * 64,
                numeric_gates_reviewed=True,
            )
        with self.assertRaises(ValueError):
            activate_face_cluster_corpus(
                event=self.event,
                corpus=corpus,
                configuration_hash=corpus.configuration_hash,
                anchor_threshold=0.05,
                evaluation_report_hash="a" * 64,
                numeric_gates_reviewed=1,  # type: ignore[arg-type]
            )
        with self.assertRaises(ValueError):
            activate_face_cluster_corpus(
                event=self.event,
                corpus=corpus,
                configuration_hash=corpus.configuration_hash,
                anchor_threshold=0.05,
                evaluation_report_hash="a" * 64,
                numeric_gates_reviewed=True,
            )
        FaceClusterCorpus.objects.filter(pk=corpus.pk).update(
            status=FaceClusterCorpus.Status.FAILED
        )
        with self.assertRaises(ValueError):
            activate_face_cluster_corpus(
                event=self.event,
                corpus=corpus,
                configuration_hash=corpus.configuration_hash,
                anchor_threshold=0.05,
                evaluation_report_hash="a" * 64,
                numeric_gates_reviewed=True,
            )

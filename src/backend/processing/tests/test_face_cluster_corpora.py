from __future__ import annotations

from datetime import date
from math import sqrt
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from face_cluster_contract import cluster_expansion_policy_hash
from picflow.models import Event, Photo

from processing.models import (
    EventFaceClusterActivation,
    EventProcessingRun,
    FaceClusterCorpus,
    FaceEmbeddingVector,
    FaceProcessingAttemptArtifact,
    PhotoDerivative,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    GENERATE_PREVIEW_CONFIGURATION,
    request_processor,
)
from processing.services.face_cluster_corpora import (
    activate_face_cluster_corpus,
    build_face_cluster_corpus,
)
from processing.services.face_cohort import (
    CompatibleFaceEmbedding,
    load_compatible_face_embeddings,
)
from processing.services.face_quality import active_face_embedding_generations


class FaceClusterCorpusTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="cluster-corpus-owner")
        self.event = self.make_event("main")
        self.other_event = self.make_event("other")
        self.generations = active_face_embedding_generations(self.event)

    def make_event(self, suffix: str) -> Event:
        return Event.objects.create(
            name=f"Corpus event {suffix}",
            slug=f"corpus-event-{suffix}-{uuid4().hex[:8]}",
            start_date=date(2026, 8, 5),
            end_date=date(2026, 8, 5),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )

    def make_embedding(
        self,
        *,
        event: Event,
        photo_id: str,
        vector: list[float],
        contract_version: int = 3,
        processor_version: int = 5,
        model: str = "adaface-ir18-webface4m",
        native: bool = False,
    ) -> FaceEmbeddingVector:
        generation = self.generations[1 if native else 0]
        configuration = generation["configuration"]
        configuration_hash = generation["configuration_hash"]
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
        embedding_model = FaceEmbeddingVector
        embedding = embedding_model.objects.create(
            detection=detection,
            model_version=model,
            vector=vector + [0.0] * ((128 if model == "sface" else 512) - len(vector)),
            metadata={},
        )
        PhotoFaceEmbeddingProjection.objects.create(
            photo=photo,
            contract_version=contract_version,
            processor_version=processor_version,
            configuration_hash=configuration_hash,
            accepted_attempt=attempt,
        )
        return embedding

    def test_native_corpus_freezes_only_exact_generation_without_json(self) -> None:
        old = self.make_embedding(
            event=self.event,
            photo_id="old-sface",
            vector=[1.0, 0.0],
            contract_version=1,
            processor_version=1,
            model="sface",
        )
        native = self.make_embedding(
            event=self.event,
            photo_id="native-only",
            vector=[1.0] + [0.0] * 511,
            contract_version=3,
            processor_version=5,
            model="adaface-ir18-webface4m",
            native=True,
        )
        corpus = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        self.assertEqual(corpus.embedding_dimensions, 512)
        self.assertEqual(corpus.input_count, 1)
        self.assertEqual(
            list(corpus.members.values_list("detection_id", flat=True)), [native.detection_id]
        )
        self.assertNotIn(old.detection_id, corpus.members.values_list("detection_id", flat=True))
        self.assertEqual(FaceEmbeddingVector.objects.filter(detection=native.detection).count(), 1)

    def test_native_corpus_fails_closed_on_missing_or_wrong_vector(self) -> None:
        self.make_embedding(
            event=self.event,
            photo_id="native-wrong",
            vector=[1.0] + [0.0] * 127,
            contract_version=3,
            processor_version=5,
            model="sface",
            native=True,
        )
        for version in (1, 2):
            if version == 2:
                self.make_embedding(
                    event=self.other_event,
                    photo_id="native-missing",
                    vector=[1.0] + [0.0] * 511,
                    contract_version=3,
                    processor_version=5,
                    model="adaface-ir18-webface4m",
                    native=True,
                )
                valid = FaceEmbeddingVector.objects.get(detection__attempt__event=self.other_event)
                PhotoFaceDetection.objects.create(
                    artifact=valid.detection.artifact,
                    attempt=valid.detection.attempt,
                    face_index=1,
                    status="kept",
                )
            with self.assertRaises(ValueError):
                build_face_cluster_corpus(
                    event=self.event if version == 1 else self.other_event,
                    version=version,
                    generations=self.generations,
                    dimensions=512,
                    edge_threshold=0.1,
                    representative_threshold=0.1,
                    distance_block_size=2,
                    max_candidate_edges=100,
                )
            self.assertEqual(FaceClusterCorpus.objects.get(version=version).status, "failed")
        self.assertFalse(self.event.face_cluster_corpora.filter(members__isnull=False).exists())

    def make_runtime_compatible(self, corpus: FaceClusterCorpus) -> FaceClusterCorpus:
        return corpus

    def publish_accepted_preview(self, photo: Photo) -> None:
        state = request_processor(
            photo,
            processor_type="generate_preview",
            contract_version=2,
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint={
                "object_key": photo.original_key,
                "object_size": photo.original_size,
                "object_content_type": photo.original_content_type,
                "object_etag": None,
                "media_kind": "original",
                "pixel_width": 1600,
                "pixel_height": 1000,
            },
        )
        assert state.current_job is not None
        attempt = ProcessingAttempt.objects.create(
            event=photo.event,
            run=state.current_job.run,
            job=state.current_job,
            photo=photo,
            contract_version=2,
            processor_type="generate_preview",
            processor_version=1,
            configuration=GENERATE_PREVIEW_CONFIGURATION,
            input_fingerprint=state.current_job.input_fingerprint,
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=f"previews/{photo.pk}.jpg",
            byte_size=8,
            content_type="image/jpeg",
            width=1600,
            height=1000,
            oriented_source_width=1600,
            oriented_source_height=1000,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.succeeded_at = timezone.now()
        state.save(
            update_fields=[
                "status",
                "current_attempt",
                "accepted_attempt",
                "succeeded_at",
                "updated_at",
            ]
        )

    def test_shared_loader_is_event_and_generation_scoped(self) -> None:
        accepted = self.make_embedding(event=self.event, photo_id="accepted", vector=[1.0, 0.0])
        preview = self.make_embedding(
            event=self.event,
            photo_id="preview",
            vector=[0.0, 1.0],
            native=True,
        )
        foreign = self.make_embedding(event=self.other_event, photo_id="foreign", vector=[1.0, 0.0])
        rows = load_compatible_face_embeddings(self.event, self.generations, 512)
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
            generations=self.generations,
            dimensions=512,
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

    def test_default_builder_freezes_the_current_generation_set(self) -> None:
        self.make_embedding(event=self.event, photo_id="current", vector=[1.0, 0.0])
        corpus = build_face_cluster_corpus(
            event=self.event,
            version=1,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        self.assertEqual(corpus.configuration["face_embedding_generations"], list(self.generations))
        self.assertEqual(corpus.input_count, 1)

    def test_repeated_builds_have_reproducible_configuration_and_membership_hashes(self) -> None:
        self.make_embedding(event=self.event, photo_id="hash-one", vector=[1.0, 0.0])
        first = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations,
            dimensions=512,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        second = build_face_cluster_corpus(
            event=self.event,
            version=2,
            generations=self.generations,
            dimensions=512,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        self.assertEqual(first.configuration_hash, second.configuration_hash)
        self.assertEqual(first.membership_hash, second.membership_hash)

    def test_configuration_identity_is_independent_of_frozen_input_and_membership(self) -> None:
        self.make_embedding(event=self.event, photo_id="identity-one", vector=[1.0, 0.0])
        first = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations,
            dimensions=512,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        self.make_embedding(event=self.event, photo_id="identity-two", vector=[0.0, 1.0])
        second = build_face_cluster_corpus(
            event=self.event,
            version=2,
            generations=self.generations,
            dimensions=512,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )

        self.assertEqual(first.configuration_hash, second.configuration_hash)
        self.assertNotEqual(first.input_hash, second.input_hash)
        self.assertNotEqual(first.membership_hash, second.membership_hash)

    def test_candidate_edge_limit_leaves_failed_non_selectable_corpus(self) -> None:
        self.make_embedding(event=self.event, photo_id="limit-one", vector=[1.0, 0.0])
        self.make_embedding(event=self.event, photo_id="limit-two", vector=[1.0, 0.0])
        with self.assertRaises(ValueError):
            build_face_cluster_corpus(
                event=self.event,
                version=1,
                generations=self.generations,
                dimensions=512,
                edge_threshold=0.0,
                representative_threshold=0.1,
                distance_block_size=2,
                max_candidate_edges=0,
            )
        corpus = FaceClusterCorpus.objects.get(event=self.event, version=1)
        self.assertEqual(corpus.status, FaceClusterCorpus.Status.FAILED)
        self.assertIsNone(corpus.published_at)
        self.assertFalse(corpus.clusters.exists())

    def test_loader_failure_leaves_a_durable_failed_corpus(self) -> None:
        with patch(
            "processing.services.face_cluster_corpora.load_compatible_face_embeddings",
            side_effect=ValueError("loader unavailable"),
        ):
            with self.assertRaises(ValueError):
                build_face_cluster_corpus(
                    event=self.event,
                    version=1,
                    generations=self.generations,
                    dimensions=512,
                    edge_threshold=0.1,
                    representative_threshold=0.1,
                    distance_block_size=2,
                    max_candidate_edges=100,
                )

        corpus = FaceClusterCorpus.objects.get(event=self.event, version=1)
        self.assertEqual(corpus.status, FaceClusterCorpus.Status.FAILED)
        self.assertFalse(corpus.clusters.exists())

    @patch("selfie_search.services.submission._face_embedding_generations")
    def test_activation_replaces_only_the_event_pointer_after_explicit_review(
        self, active_generations
    ) -> None:
        active_generations.return_value = self.generations
        self.make_embedding(event=self.event, photo_id="activate", vector=[1.0, 0.0])
        first = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations,
            dimensions=512,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        second = build_face_cluster_corpus(
            event=self.event,
            version=2,
            generations=self.generations,
            dimensions=512,
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
            configuration_hash=cluster_expansion_policy_hash(first.configuration_hash, 0.42, 0.05),
            anchor_threshold=0.05,
            evaluation_report_hash="a" * 64,
            numeric_gates_reviewed=True,
        )

        activation = activate_face_cluster_corpus(
            event=self.event,
            corpus=second,
            configuration_hash=cluster_expansion_policy_hash(second.configuration_hash, 0.42, 0.05),
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
            {
                "policy_id": "face-cluster-expansion-policy-v1",
                "corpus_configuration_hash": second.configuration_hash,
                "direct_threshold": 0.42,
                "anchor_threshold": 0.05,
            },
        )
        active = EventFaceClusterActivation.objects.filter(event=self.event, active=True).get()
        self.assertEqual(active, activation)

    @patch("selfie_search.services.submission._face_embedding_generations")
    def test_activation_hash_is_a_policy_identity_not_the_corpus_hash(
        self, active_generations
    ) -> None:
        active_generations.return_value = self.generations
        from processing.services.face_cluster_corpora import cluster_expansion_policy_hash

        self.make_embedding(event=self.event, photo_id="policy", vector=[1.0, 0.0])
        corpus = self.make_runtime_compatible(
            build_face_cluster_corpus(
                event=self.event,
                version=1,
                generations=self.generations,
                dimensions=512,
                edge_threshold=0.1,
                representative_threshold=0.1,
                distance_block_size=2,
                max_candidate_edges=100,
            )
        )
        expected = cluster_expansion_policy_hash(corpus.configuration_hash, 0.42, 0.05)
        self.assertNotEqual(
            expected,
            cluster_expansion_policy_hash(corpus.configuration_hash, 0.419, 0.05),
        )
        self.assertNotEqual(
            expected,
            cluster_expansion_policy_hash(corpus.configuration_hash, 0.42, 0.04),
        )

        activation = activate_face_cluster_corpus(
            event=self.event,
            corpus=corpus,
            configuration_hash=expected,
            anchor_threshold=0.05,
            evaluation_report_hash="a" * 64,
            numeric_gates_reviewed=True,
        )

        self.assertEqual(activation.configuration_hash, expected)
        self.assertNotEqual(activation.configuration_hash, corpus.configuration_hash)
        self.assertEqual(
            activation.configuration["corpus_configuration_hash"], corpus.configuration_hash
        )

    def test_activation_denies_unpublished_mismatched_or_unreviewed_inputs(self) -> None:
        self.make_embedding(event=self.event, photo_id="deny", vector=[1.0, 0.0])
        corpus = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations,
            dimensions=512,
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

    def test_ordinary_adaface_native_corpus_defaults_to_model_dimensions(self) -> None:
        corpus = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        self.assertEqual(corpus.embedding_dimensions, 512)

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from math import sqrt
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from face_cluster_contract import cluster_expansion_policy_hash
from picflow.models import Event, Photo
from selfie_search.services.submission import _face_embedding_generations

from processing.models import (
    EventFaceClusterActivation,
    EventFaceEmbeddingActivation,
    EventProcessingRun,
    FaceClusterCorpus,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    PhotoFaceEmbeddingProjection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import (
    FACE_EMBEDDING_CONFIGURATION,
    FaceEmbeddingGenerationApproval,
)
from processing.services.face_cluster_corpora import (
    activate_face_cluster_corpus,
    build_face_cluster_corpus,
)
from processing.services.face_cohort import (
    CompatibleFaceEmbedding,
    load_compatible_face_embeddings,
)
from processing.services.face_quality import (
    activate_face_embedding_generation,
    candidate_face_embedding_generations,
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
        self.candidate_generations = candidate_face_embedding_generations()

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
        configuration = FACE_EMBEDDING_CONFIGURATION
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
        generation = next(
            generation
            for generation in (*self.generations, *self.candidate_generations)
            if generation["contract_version"] == contract_version
            and generation["processor_version"] == processor_version
        )
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
        embedding = FaceEmbedding.objects.create(
            detection=detection,
            model_version=model,
            vector=vector,
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

    def make_runtime_compatible(self, corpus: FaceClusterCorpus) -> FaceClusterCorpus:
        FaceClusterCorpus.objects.filter(pk=corpus.pk).update(
            configuration={
                "face_embedding_generations": list(_face_embedding_generations(self.event))
            },
            model_version="sface",
            embedding_dimensions=128,
        )
        return FaceClusterCorpus.objects.get(pk=corpus.pk)

    def candidate_approval(
        self, *, event: Event, photo_count: int
    ) -> FaceEmbeddingGenerationApproval:
        configuration_hash = self.candidate_generations[0]["configuration_hash"]
        assert isinstance(configuration_hash, str)
        return FaceEmbeddingGenerationApproval(
            event_slug=event.slug,
            photo_count=photo_count,
            configuration_hash=configuration_hash,
            evaluation_report_hash="d" * 64,
            complete=True,
            approved=True,
            clear_loss_count=0,
            relevant_result_loss_count=0,
            unresolved_count=0,
        )

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
            generations=self.generations,
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

    def test_default_builder_freezes_the_events_active_generation_set(self) -> None:
        generations = list(candidate_face_embedding_generations())
        self.make_embedding(
            event=self.event,
            photo_id="active-candidate",
            vector=[1.0] + [0.0] * 127,
            contract_version=3,
            processor_version=3,
        )
        approval = self.candidate_approval(event=self.event, photo_count=1)
        with patch("processing.services.enrollment.FACE_EMBEDDING_QUALITY_APPROVAL", approval):
            activate_face_embedding_generation(
                event=self.event,
                generations=self.candidate_generations,
                approved_configuration_hash=approval.configuration_hash,
                evaluation_report_hash=approval.evaluation_report_hash,
                review_confirmed=True,
            )

            corpus = build_face_cluster_corpus(
                event=self.event,
                version=1,
                dimensions=128,
                edge_threshold=0.1,
                representative_threshold=0.1,
                distance_block_size=2,
                max_candidate_edges=100,
            )

        self.assertEqual(corpus.configuration["face_embedding_generations"], generations)

    def test_default_builder_fails_closed_on_a_direct_unapproved_candidate_row(self) -> None:
        generations = list(self.candidate_generations)
        EventFaceEmbeddingActivation.objects.create(
            event=self.event,
            generations=generations,
            generation_set_hash=hashlib.sha256(
                json.dumps(generations, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            approved_configuration_hash=generations[0]["configuration_hash"],
            approved_evaluation_report_hash="d" * 64,
        )

        with self.assertRaisesRegex(ValueError, "approved benchmark evidence"):
            build_face_cluster_corpus(
                event=self.event,
                version=1,
                dimensions=128,
                edge_threshold=0.1,
                representative_threshold=0.1,
                distance_block_size=2,
                max_candidate_edges=100,
            )

        self.assertFalse(FaceClusterCorpus.objects.filter(event=self.event).exists())

    def test_explicit_candidate_build_is_isolated_and_malformed_sets_write_no_corpus(self) -> None:
        candidate = self.make_embedding(
            event=self.event,
            photo_id="candidate-only",
            vector=[1.0, 0.0],
            contract_version=3,
            processor_version=3,
        )

        corpus = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.candidate_generations,
            dimensions=2,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )

        self.assertEqual(corpus.input_count, 1)
        self.assertEqual(
            corpus.members.get().detection_id,
            candidate.detection_id,
        )
        self.assertFalse(EventFaceEmbeddingActivation.objects.filter(event=self.event).exists())

        mutated = dict(self.candidate_generations[0])
        mutated["configuration_hash"] = "f" * 64
        invalid_sets = (
            (*self.generations, *self.candidate_generations),
            self.generations[:1],
            tuple(reversed(self.generations)),
            (*self.candidate_generations, *self.candidate_generations),
            (mutated,),
        )
        for offset, generations in enumerate(invalid_sets, start=2):
            with self.subTest(generations=generations):
                with self.assertRaisesRegex(ValueError, "generation set"):
                    build_face_cluster_corpus(
                        event=self.event,
                        version=offset,
                        generations=generations,
                        dimensions=2,
                        edge_threshold=0.1,
                        representative_threshold=0.1,
                        distance_block_size=2,
                        max_candidate_edges=100,
                    )
                self.assertFalse(
                    FaceClusterCorpus.objects.filter(event=self.event, version=offset).exists()
                )
        self.assertFalse(EventFaceEmbeddingActivation.objects.filter(event=self.event).exists())

    def test_repeated_builds_have_reproducible_configuration_and_membership_hashes(self) -> None:
        self.make_embedding(event=self.event, photo_id="hash-one", vector=[1.0, 0.0])
        first = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations,
            dimensions=2,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        second = build_face_cluster_corpus(
            event=self.event,
            version=2,
            generations=self.generations,
            dimensions=2,
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
            dimensions=2,
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
            dimensions=2,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )

        self.assertEqual(first.configuration_hash, second.configuration_hash)
        self.assertNotEqual(first.input_hash, second.input_hash)
        self.assertNotEqual(first.membership_hash, second.membership_hash)

    def test_django_corpus_hash_matches_evaluator_projection_for_the_same_fixture(self) -> None:
        experiment_root = (
            Path(__file__).resolve().parents[4] / "experiments" / "face_recognition_spike"
        )
        sys.path.insert(0, str(experiment_root))
        try:
            from face_spike.analysis import BoundingBox
            from face_spike.cluster_expansion import production_corpus_configuration_hash
            from face_spike.index import FaceIndex, FaceIndexEntry
            from face_spike.index_artifacts import FaceIndexManifest
            from face_spike.quality import FaceQuality

            self._assert_django_corpus_hash_matches_evaluator_projection(
                BoundingBox,
                FaceIndex,
                FaceIndexEntry,
                FaceIndexManifest,
                FaceQuality,
                production_corpus_configuration_hash,
            )
        finally:
            sys.path.remove(str(experiment_root))

    def _assert_django_corpus_hash_matches_evaluator_projection(
        self,
        bounding_box_type: Any,
        face_index_type: Any,
        face_index_entry_type: Any,
        face_index_manifest_type: Any,
        face_quality_type: Any,
        evaluator_projection: Any,
    ) -> None:
        self.make_embedding(event=self.event, photo_id="projection", vector=[1.0, 0.0])
        thresholds: dict[str, int | float] = {
            "cluster_threshold": 0.1,
            "representative_threshold": 0.1,
            "distance_block_size": 2,
            "max_candidate_edges": 100,
        }
        corpus = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations,
            dimensions=2,
            edge_threshold=float(thresholds["cluster_threshold"]),
            representative_threshold=float(thresholds["representative_threshold"]),
            distance_block_size=int(thresholds["distance_block_size"]),
            max_candidate_edges=int(thresholds["max_candidate_edges"]),
        )
        index = face_index_type(
            (
                face_index_entry_type(
                    "projection.jpg#face-001",
                    "projection.jpg",
                    1,
                    bounding_box_type(0, 0, 10, 10),
                    "faces/projection.jpg#face-001.png",
                    face_quality_type(0.9, 10.0, 0.1, 100.0, "accepted", ()),
                ),
            ),
            np.asarray([[1.0, 0.0]], dtype=np.float32),
            face_index_manifest_type(
                "a" * 64,
                "b" * 64,
                {"basename": "yunet.onnx", "size": 1, "sha256": "c" * 64},
                {"basename": "sface.onnx", "size": 1, "sha256": "d" * 64},
                thresholds,
                {"numpy": "test"},
                1,
                2,
                "2026-08-05T00:00:00Z",
            ),
        )

        self.assertEqual(
            corpus.configuration_hash,
            evaluator_projection(
                index,
                source_parameters=thresholds,
                generations=self.generations,
            ),
        )

    def test_candidate_edge_limit_leaves_failed_non_selectable_corpus(self) -> None:
        self.make_embedding(event=self.event, photo_id="limit-one", vector=[1.0, 0.0])
        self.make_embedding(event=self.event, photo_id="limit-two", vector=[1.0, 0.0])
        with self.assertRaises(ValueError):
            build_face_cluster_corpus(
                event=self.event,
                version=1,
                generations=self.generations,
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
                    dimensions=2,
                    edge_threshold=0.1,
                    representative_threshold=0.1,
                    distance_block_size=2,
                    max_candidate_edges=100,
                )

        corpus = FaceClusterCorpus.objects.get(event=self.event, version=1)
        self.assertEqual(corpus.status, FaceClusterCorpus.Status.FAILED)
        self.assertFalse(corpus.clusters.exists())

    def test_activation_replaces_only_the_event_pointer_after_explicit_review(self) -> None:
        self.make_embedding(event=self.event, photo_id="activate", vector=[1.0, 0.0])
        first = build_face_cluster_corpus(
            event=self.event,
            version=1,
            generations=self.generations,
            dimensions=2,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        second = build_face_cluster_corpus(
            event=self.event,
            version=2,
            generations=self.generations,
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
            configuration_hash=cluster_expansion_policy_hash(first.configuration_hash, 0.363, 0.05),
            anchor_threshold=0.05,
            evaluation_report_hash="a" * 64,
            numeric_gates_reviewed=True,
        )

        activation = activate_face_cluster_corpus(
            event=self.event,
            corpus=second,
            configuration_hash=cluster_expansion_policy_hash(
                second.configuration_hash, 0.363, 0.05
            ),
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
                "direct_threshold": 0.363,
                "anchor_threshold": 0.05,
            },
        )
        active = EventFaceClusterActivation.objects.filter(event=self.event, active=True).get()
        self.assertEqual(active, activation)

    def test_activation_hash_is_a_policy_identity_not_the_corpus_hash(self) -> None:
        from processing.services.face_cluster_corpora import cluster_expansion_policy_hash

        self.make_embedding(event=self.event, photo_id="policy", vector=[1.0, 0.0])
        corpus = self.make_runtime_compatible(
            build_face_cluster_corpus(
                event=self.event,
                version=1,
                generations=self.generations,
                dimensions=2,
                edge_threshold=0.1,
                representative_threshold=0.1,
                distance_block_size=2,
                max_candidate_edges=100,
            )
        )
        expected = cluster_expansion_policy_hash(corpus.configuration_hash, 0.363, 0.05)
        self.assertNotEqual(
            expected,
            cluster_expansion_policy_hash(corpus.configuration_hash, 0.362, 0.05),
        )
        self.assertNotEqual(
            expected,
            cluster_expansion_policy_hash(corpus.configuration_hash, 0.363, 0.04),
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

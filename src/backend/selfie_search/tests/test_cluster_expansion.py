from __future__ import annotations

from datetime import date
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import TestCase
from django.utils import timezone
from face_cluster_contract import POLICY_ID, cluster_expansion_policy_hash
from picflow.models import Event, Photo
from processing.models import (
    EventFaceClusterActivation,
    EventProcessingRun,
    FaceCluster,
    FaceClusterCorpus,
    FaceClusterMember,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    ProcessingAttempt,
    ProcessingJob,
)
from selfie_search.models import SelfieSearch
from selfie_search.services.cluster_expansion import expand_ranked_photos
from selfie_search.services.ranking import RankedPhoto


class ClusterExpansionTests(TestCase):
    """The production break caught here is an ordinary direct match widening result membership."""

    def setUp(self) -> None:
        self.owner = get_user_model().objects.create_user(username="cluster-expansion-owner")
        self.event = Event.objects.create(
            name="Cluster expansion",
            slug="cluster-expansion",
            start_date=date(2026, 8, 5),
            end_date=date(2026, 8, 5),
            city="Moscow",
        )
        self.search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="a" * 64,
            temporary_object_key="selfie-search/temporary",
            configuration={
                "embedding_model": "sface",
                "embedding_dimensions": 128,
                "cosine_distance_threshold": 0.363,
                "gallery_face_embedding_generations": [{"generation": "v1"}],
            },
        )
        self.corpus = FaceClusterCorpus.objects.create(
            event=self.event,
            version=1,
            status=FaceClusterCorpus.Status.BUILDING,
            algorithm_version="guarded-graph-v1",
            configuration={"face_embedding_generations": [{"generation": "v1"}]},
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
        self.activation = EventFaceClusterActivation.objects.create(
            event=self.event,
            corpus=self.corpus,
            active=True,
            anchor_threshold=0.2,
            configuration={
                "policy_id": POLICY_ID,
                "corpus_configuration_hash": self.corpus.configuration_hash,
                "direct_threshold": 0.363,
                "anchor_threshold": 0.2,
            },
            configuration_hash=cluster_expansion_policy_hash(
                self.corpus.configuration_hash, 0.363, 0.2
            ),
            approved_evaluation_report_hash="d" * 64,
        )

    def detection(self, photo_id: str) -> PhotoFaceDetection:
        photo = Photo.objects.create(
            id=photo_id,
            event=self.event,
            uploaded_by=self.owner,
            original_key=f"originals/{uuid4().hex}",
            original_filename=f"{photo_id}.jpg",
            original_size=1,
            original_content_type="image/jpeg",
            gallery_media_policy=Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED,
            uploaded_at=timezone.now(),
        )
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="e" * 64,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="e" * 64,
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
        return PhotoFaceDetection.objects.create(
            artifact=FaceProcessingAttemptArtifact.objects.create(attempt=attempt),
            attempt=attempt,
            face_index=0,
            status=PhotoFaceDetection.Status.KEPT,
        )

    def additional_detection(self, detection: PhotoFaceDetection) -> PhotoFaceDetection:
        return PhotoFaceDetection.objects.create(
            artifact=detection.artifact,
            attempt=detection.attempt,
            face_index=detection.face_index + 1,
            status=PhotoFaceDetection.Status.KEPT,
        )

    def publish(self) -> None:
        FaceClusterCorpus.objects.filter(pk=self.corpus.pk).update(
            status=FaceClusterCorpus.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        self.activation.refresh_from_db()

    def test_only_strong_direct_anchor_expands_and_preserves_direct_order(self) -> None:
        strong = self.detection("direct-strong")
        ordinary = self.detection("direct-ordinary")
        appended = self.detection("cluster-appended")
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=self.corpus,
            cluster_key="cluster-1",
            representative_detection=strong,
            member_count=3,
        )
        for index, detection in enumerate((strong, ordinary, appended)):
            FaceClusterMember.objects.create(
                event=self.event,
                corpus=self.corpus,
                cluster=cluster,
                detection=detection,
                member_index=index,
                distance_to_representative=index / 10,
            )
        self.publish()

        expansion = expand_ranked_photos(
            self.search,
            (
                RankedPhoto(photo_id="direct-strong", detection_id=strong.id, cosine_distance=0.1),
                RankedPhoto(
                    photo_id="direct-ordinary", detection_id=ordinary.id, cosine_distance=0.3
                ),
            ),
            (1.0,) + (0.0,) * 127,
            self.activation,
        )

        self.assertEqual(
            [row.photo_id for row in expansion.results],
            ["direct-strong", "direct-ordinary", "cluster-appended"],
        )
        self.assertEqual(expansion.direct_matched_photo_count, 2)
        self.assertEqual(expansion.cluster_expanded_photo_count, 1)
        self.assertEqual(expansion.final_matched_photo_count, 3)
        self.assertEqual(expansion.strong_anchor_count, 1)
        self.assertEqual(expansion.expanded_cluster_count, 1)
        self.assertEqual(expansion.outcome, "expanded")
        self.assertEqual(len(expansion.results[0].cluster_evidence), 1)
        self.assertEqual(len(expansion.results[1].cluster_evidence), 1)

    def test_missing_activation_keeps_complete_direct_only_snapshot(self) -> None:
        direct = RankedPhoto(photo_id="direct", detection_id=uuid4(), cosine_distance=0.1)

        expansion = expand_ranked_photos(self.search, (direct,), (1.0,) + (0.0,) * 127, None)

        self.assertEqual([row.photo_id for row in expansion.results], ["direct"])
        self.assertEqual(expansion.direct_matched_photo_count, 1)
        self.assertEqual(expansion.cluster_expanded_photo_count, 0)
        self.assertEqual(expansion.final_matched_photo_count, 1)
        self.assertEqual(expansion.outcome, "corpus_unavailable")

    def test_incompatible_or_unreadable_corpus_keeps_direct_only_snapshot(self) -> None:
        anchor = self.detection("fallback-anchor")
        member = self.detection("fallback-member")
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=self.corpus,
            cluster_key="fallback",
            representative_detection=anchor,
            member_count=2,
        )
        for index, detection in enumerate((anchor, member)):
            FaceClusterMember.objects.create(
                event=self.event,
                corpus=self.corpus,
                cluster=cluster,
                detection=detection,
                member_index=index,
                distance_to_representative=index / 10,
            )
        self.publish()
        direct = (RankedPhoto("fallback-anchor", anchor.id, 0.1),)
        incompatible_search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="f" * 64,
            temporary_object_key="selfie-search/fallback",
            configuration=self.search.configuration | {"embedding_dimensions": 127},
        )
        incompatible = expand_ranked_photos(
            incompatible_search, direct, (1.0,) + (0.0,) * 127, self.activation
        )

        self.assertEqual(incompatible.outcome, "corpus_incompatible")
        self.assertEqual([row.photo_id for row in incompatible.results], ["fallback-anchor"])

        with patch(
            "selfie_search.services.cluster_expansion.gallery_photo_queryset",
            side_effect=DatabaseError,
        ):
            unreadable = expand_ranked_photos(
                self.search, direct, (1.0,) + (0.0,) * 127, self.activation
            )

        self.assertEqual(unreadable.outcome, "corpus_incompatible")
        self.assertEqual([row.photo_id for row in unreadable.results], ["fallback-anchor"])

    def test_runtime_direct_threshold_mismatch_keeps_direct_only_snapshot(self) -> None:
        self.publish()
        self.activation.configuration = {
            "policy_id": "face-cluster-expansion-policy-v1",
            "corpus_configuration_hash": self.corpus.configuration_hash,
            "direct_threshold": 0.2,
            "anchor_threshold": 0.1,
        }
        self.activation.anchor_threshold = 0.1
        self.activation.save(update_fields=["configuration", "anchor_threshold"])
        direct = (RankedPhoto("fallback-direct", uuid4(), 0.1),)

        expansion = expand_ranked_photos(
            self.search, direct, (1.0,) + (0.0,) * 127, self.activation
        )

        self.assertEqual(expansion.outcome, "corpus_incompatible")
        self.assertEqual([row.photo_id for row in expansion.results], ["fallback-direct"])

    def test_frozen_non_singleton_counts_when_only_its_anchor_is_currently_eligible(self) -> None:
        self.corpus.configuration = {"face_embedding_generations": [{"generation": "v1"}]}
        self.corpus.save(update_fields=["configuration"])
        anchor = self.detection("count-anchor")
        hidden = self.detection("count-hidden")
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=self.corpus,
            cluster_key="count-cluster",
            representative_detection=anchor,
            member_count=2,
        )
        for index, detection in enumerate((anchor, hidden)):
            FaceClusterMember.objects.create(
                event=self.event,
                corpus=self.corpus,
                cluster=cluster,
                detection=detection,
                member_index=index,
                distance_to_representative=index / 10,
            )
        Photo.objects.filter(pk="count-hidden").update(
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        self.publish()

        expansion = expand_ranked_photos(
            self.search,
            (RankedPhoto(photo_id="count-anchor", detection_id=anchor.id, cosine_distance=0.1),),
            (1.0,) + (0.0,) * 127,
            self.activation,
        )

        self.assertEqual(expansion.outcome, "no_new_photos")
        self.assertEqual(expansion.expanded_cluster_count, 1)

    def test_strong_singleton_keeps_the_direct_result_without_expanding(self) -> None:
        anchor = self.detection("singleton-anchor")
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=self.corpus,
            cluster_key="singleton",
            representative_detection=anchor,
            member_count=1,
        )
        FaceClusterMember.objects.create(
            event=self.event,
            corpus=self.corpus,
            cluster=cluster,
            detection=anchor,
            member_index=0,
            distance_to_representative=0,
        )
        self.publish()

        expansion = expand_ranked_photos(
            self.search,
            (
                RankedPhoto(
                    photo_id="singleton-anchor", detection_id=anchor.id, cosine_distance=0.1
                ),
            ),
            (1.0,) + (0.0,) * 127,
            self.activation,
        )

        self.assertEqual([row.photo_id for row in expansion.results], ["singleton-anchor"])
        self.assertEqual(expansion.outcome, "no_new_photos")
        self.assertEqual(expansion.strong_anchor_count, 1)
        self.assertEqual(expansion.expanded_cluster_count, 0)
        self.assertEqual(expansion.cluster_expanded_photo_count, 0)

    def test_repeated_anchors_and_multi_cluster_photos_keep_all_evidence(self) -> None:
        first_anchor = self.detection("first-anchor")
        repeated_anchor = self.detection("repeated-anchor")
        second_anchor = self.detection("second-anchor")
        group = self.detection("group-photo")
        group_again = self.additional_detection(group)
        shared = self.detection("shared-photo")
        shared_again = self.additional_detection(shared)
        second_only = self.detection("second-only")
        first_cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=self.corpus,
            cluster_key="first",
            representative_detection=first_anchor,
            member_count=4,
        )
        second_cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=self.corpus,
            cluster_key="second",
            representative_detection=second_anchor,
            member_count=4,
        )
        for index, detection in enumerate((first_anchor, repeated_anchor, group, shared)):
            FaceClusterMember.objects.create(
                event=self.event,
                corpus=self.corpus,
                cluster=first_cluster,
                detection=detection,
                member_index=index,
                distance_to_representative=index / 10,
            )
        for index, detection in enumerate((second_anchor, group_again, second_only, shared_again)):
            FaceClusterMember.objects.create(
                event=self.event,
                corpus=self.corpus,
                cluster=second_cluster,
                detection=detection,
                member_index=index,
                distance_to_representative=index / 10,
            )
        self.publish()

        expansion = expand_ranked_photos(
            self.search,
            (
                RankedPhoto("first-anchor", first_anchor.id, 0.1),
                RankedPhoto("repeated-anchor", repeated_anchor.id, 0.11),
                RankedPhoto("second-anchor", second_anchor.id, 0.12),
            ),
            (1.0,) + (0.0,) * 127,
            self.activation,
        )

        self.assertEqual(
            [row.photo_id for row in expansion.results],
            [
                "first-anchor",
                "repeated-anchor",
                "second-anchor",
                "group-photo",
                "shared-photo",
                "second-only",
            ],
        )
        by_photo = {row.photo_id: row for row in expansion.results}
        self.assertEqual(expansion.strong_anchor_count, 3)
        self.assertEqual(expansion.expanded_cluster_count, 2)
        self.assertEqual(len(by_photo["group-photo"].cluster_evidence), 2)
        self.assertEqual(len(by_photo["shared-photo"].cluster_evidence), 2)

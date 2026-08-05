from __future__ import annotations

import re
from datetime import date
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone
from face_cluster_contract import cluster_expansion_policy_hash
from picflow.models import Event, Photo

from processing.models import (
    EventProcessingRun,
    FaceEmbedding,
    FaceProcessingAttemptArtifact,
    PhotoFaceDetection,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.services.enrollment import FACE_EMBEDDING_CONFIGURATION
from processing.services.face_cluster_corpora import build_face_cluster_corpus


class FaceClusterCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="cluster-command-owner")
        self.event = Event.objects.create(
            name="Command cluster event",
            slug=f"command-cluster-event-{uuid4().hex[:8]}",
            start_date=date(2026, 8, 5),
            end_date=date(2026, 8, 5),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        self.make_embedding()

    def make_embedding(self) -> None:
        photo = Photo.objects.create(
            id="command-cluster-photo",
            event=self.event,
            uploaded_by=self.user,
            original_key="originals/command-cluster-photo",
            original_filename="command-cluster-photo.jpg",
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
            configuration=FACE_EMBEDDING_CONFIGURATION,
            configuration_hash=configuration_hash,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration=FACE_EMBEDDING_CONFIGURATION,
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
            configuration=FACE_EMBEDDING_CONFIGURATION,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoProcessingState.objects.create(
            photo=photo,
            processor_type="face_embedding",
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt,
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
            vector=[1.0] + [0.0] * 127,
            metadata={},
        )

    def test_command_requires_explicit_thresholds_and_prints_only_uuid(self) -> None:
        from io import StringIO

        output = StringIO()
        call_command(
            "build_face_cluster_corpus",
            str(self.event.pk),
            "--version",
            "1",
            "--edge-threshold",
            "0.1",
            "--representative-threshold",
            "0.1",
            "--distance-block-size",
            "2",
            "--max-candidate-edges",
            "100",
            stdout=output,
        )
        self.assertRegex(output.getvalue().strip(), re.compile(r"^[0-9a-f-]{36}$"))

    def test_command_failure_does_not_echo_sensitive_exception_details(self) -> None:
        from io import StringIO
        from unittest.mock import patch

        output = StringIO()
        with patch(
            "processing.services.face_cluster_corpora.build_face_cluster_corpus",
            side_effect=ValueError("vector secret-photo-id"),
        ):
            with self.assertRaises(CommandError):
                call_command(
                    "build_face_cluster_corpus",
                    str(self.event.pk),
                    "--version",
                    "1",
                    "--edge-threshold",
                    "0.1",
                    "--representative-threshold",
                    "0.1",
                    "--distance-block-size",
                    "2",
                    "--max-candidate-edges",
                    "100",
                    stdout=output,
                )
        self.assertNotIn("secret-photo-id", output.getvalue())

    def test_activation_command_requires_explicit_confirmation_and_prints_only_uuid(self) -> None:
        from io import StringIO

        corpus = build_face_cluster_corpus(
            event=self.event,
            version=1,
            edge_threshold=0.1,
            representative_threshold=0.1,
            distance_block_size=2,
            max_candidate_edges=100,
        )
        arguments = [
            "activate_face_cluster_corpus",
            str(self.event.pk),
            "--corpus",
            str(corpus.pk),
            "--policy-hash",
            cluster_expansion_policy_hash(corpus.configuration_hash, 0.363, 0.05),
            "--anchor-threshold",
            "0.05",
            "--evaluation-report-hash",
            "a" * 64,
        ]
        with self.assertRaises(CommandError):
            call_command(*arguments)
        output = StringIO()
        call_command(*arguments, "--confirm-numeric-gates-reviewed", stdout=output)
        self.assertRegex(output.getvalue().strip(), re.compile(r"^[0-9a-f-]{36}$"))

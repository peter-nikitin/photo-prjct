from datetime import date

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import EventProcessingRun, ProcessingJob


class FaceEmbeddingBenchmarkCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="benchmark-owner")
        self.event = Event.objects.create(
            name="Benchmark event",
            slug="benchmark-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def test_event_command_rejects_yunet_benchmark_before_creating_work(self) -> None:
        Photo.objects.create(
            id="benchmark-001",
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key="originals/001.jpg",
            original_filename="001.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

        with self.assertRaisesRegex(CommandError, "SCRFD benchmark generation is not approved"):
            call_command(
                "run_face_embedding_benchmark",
                event=self.event.slug,
                limit=1,
                label="baseline",
            )

        self.assertFalse(EventProcessingRun.objects.exists())
        self.assertFalse(ProcessingJob.objects.exists())

    def test_replay_command_preserves_historical_run_and_creates_no_new_work(self) -> None:
        source = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=3,
            processor_type="face_embedding_benchmark",
            processor_version=1,
            configuration={"label": "historical-yunet"},
            configuration_hash="a" * 64,
            status=EventProcessingRun.Status.CLOSED,
            closed_at=timezone.now(),
        )

        with self.assertRaisesRegex(CommandError, "SCRFD benchmark generation is not approved"):
            call_command(
                "run_face_embedding_benchmark",
                source_run=str(source.id),
                label="replay",
            )

        self.assertEqual(list(EventProcessingRun.objects.values_list("id", flat=True)), [source.id])
        self.assertFalse(ProcessingJob.objects.exists())

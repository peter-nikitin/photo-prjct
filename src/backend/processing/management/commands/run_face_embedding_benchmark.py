from collections.abc import Iterable

from django.core.management.base import BaseCommand, CommandError
from picflow.models import Event, Photo

from processing.models import EventProcessingRun, ProcessingJob
from processing.services.enrollment import (
    FACE_EMBEDDING_BENCHMARK_CONTRACT_VERSION,
    FACE_EMBEDDING_BENCHMARK_PROCESSOR_VERSION,
    create_face_embedding_benchmark_run,
)


class Command(BaseCommand):
    help = "Create an isolated, event-scoped face-embedding benchmark cohort."

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--event")
        source.add_argument("--source-run")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--label", required=True)

    def handle(self, *args, **options):
        label = options["label"]
        if not isinstance(label, str) or not 1 <= len(label) <= 64:
            raise CommandError("label must be between 1 and 64 characters")
        if options["event"]:
            limit = options["limit"]
            if not isinstance(limit, int) or not 1 <= limit <= 500:
                raise CommandError("--limit must be between 1 and 500 with --event")
            try:
                event = Event.objects.get(slug=options["event"])
            except Event.DoesNotExist as error:
                raise CommandError("event does not exist") from error
            photos = list(
                Photo.objects.filter(
                    event=event,
                    original_key__isnull=False,
                    original_key__gt="",
                    original_size__isnull=False,
                    original_content_type="image/jpeg",
                ).order_by("pk")[:limit]
            )
            if len(photos) != limit:
                raise CommandError("event does not have the exact eligible photo count requested")
            run = create_face_embedding_benchmark_run(
                event=event, photos=photos, label=label, source_run_id=None
            )
        else:
            if options["limit"] is not None:
                raise CommandError("--limit is only valid with --event")
            try:
                source = EventProcessingRun.objects.get(pk=options["source_run"])
            except (EventProcessingRun.DoesNotExist, ValueError) as error:
                raise CommandError("source run does not exist") from error
            if not (
                source.contract_version == FACE_EMBEDDING_BENCHMARK_CONTRACT_VERSION
                and source.processor_type == "face_embedding_benchmark"
                and source.processor_version == FACE_EMBEDDING_BENCHMARK_PROCESSOR_VERSION
                and source.status == EventProcessingRun.Status.CLOSED
            ):
                raise CommandError("source run must be a closed benchmark run")
            photos = _validated_source_jobs(
                source,
                source.jobs.select_related("photo").order_by("created_at", "id"),
            )
            run = create_face_embedding_benchmark_run(
                event=source.event,
                photos=photos,
                label=label,
                source_run_id=str(source.id),
            )
        self.stdout.write(str(run.id))


def _validated_source_jobs(
    source: EventProcessingRun, jobs: Iterable[ProcessingJob]
) -> list[Photo]:
    """Copy only an internally consistent benchmark cohort, preserving stored membership order."""
    photos: list[Photo] = []
    for job in jobs:
        if not (
            job.event_id == source.event_id
            and job.contract_version == source.contract_version
            and job.processor_type == source.processor_type
            and job.processor_version == source.processor_version
            and job.configuration == source.configuration
            and job.configuration_hash == source.configuration_hash
            and job.photo.event_id == source.event_id
            and job.photo.original_key
            and job.photo.original_size is not None
            and job.photo.original_content_type == "image/jpeg"
        ):
            raise CommandError("source job does not match its closed benchmark run")
        photos.append(job.photo)
    if not photos:
        raise CommandError("source run has no benchmark jobs")
    return photos

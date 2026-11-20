from datetime import date

import pytest
from config.queue_telemetry_protocol import FACE_QUEUE, GALLERY_QUEUE
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from picflow.models import Event, GalleryMediaProjection, Photo

from processing.models import (
    EventProcessingRun,
    PhotoFaceEmbeddingProjection,
    PhotoImportCounter,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.photo_import_telemetry import (
    _point_photo_import_backlogs,
    photo_import_backlogs,
    read_photo_import_backlog_page,
    read_photo_import_backlog_upper_bound,
)


@pytest.mark.django_db(transaction=True)
def test_manual_rebuild_counts_current_membership_in_keyset_pages(monkeypatch):
    user = get_user_model().objects.create_user(username="reconciliation")
    event = Event.objects.create(
        name="Reconciliation", slug="reconciliation", start_date=date.today(), end_date=date.today()
    )
    now = timezone.now()

    def photo(index: int) -> Photo:
        identifier = f"{index:032x}"
        return Photo(
            id=identifier,
            event=event,
            uploaded_by=user,
            original_key=f"originals/{identifier}.jpg",
            original_filename=f"{identifier}.jpg",
            original_size=20,
            original_content_type="image/jpeg",
            uploaded_at=now,
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )

    photos = Photo.objects.bulk_create([photo(index) for index in range(52)])
    ready = [item for index, item in enumerate(photos) if index not in {0, 24, 25, 51}]
    for processor, contract, version in (
        ("generate_preview", 2, 1),
        ("face_embedding", 3, 5),
    ):
        run = EventProcessingRun.objects.create(
            event=event,
            processor_type=processor,
            contract_version=contract,
            processor_version=version,
            configuration_hash="a" * 64,
        )
        jobs = ProcessingJob.objects.bulk_create(
            [
                ProcessingJob(
                    photo=item,
                    event=event,
                    run=run,
                    processor_type=processor,
                    contract_version=contract,
                    processor_version=version,
                    configuration_hash=run.configuration_hash,
                    status=ProcessingJob.Status.SUCCEEDED,
                )
                for item in ready
            ]
        )
        attempts = ProcessingAttempt.objects.bulk_create(
            [
                ProcessingAttempt(
                    job=job,
                    photo_id=job.photo_id,
                    event=event,
                    run=run,
                    processor_type=processor,
                    contract_version=contract,
                    processor_version=version,
                    status=ProcessingAttempt.Status.SUCCEEDED,
                    terminal_at=now,
                    accepted=True,
                )
                for job in jobs
            ]
        )
        PhotoProcessingState.objects.bulk_create(
            [
                PhotoProcessingState(
                    photo_id=attempt.photo_id,
                    processor_type=processor,
                    status=PhotoProcessingState.Status.SUCCEEDED,
                    accepted_attempt=attempt,
                    current_attempt=attempt,
                    current_job_id=attempt.job_id,
                    current_run=run,
                )
                for attempt in attempts
            ]
        )
        if processor == "generate_preview":
            GalleryMediaProjection.objects.bulk_create(
                [
                    GalleryMediaProjection(
                        photo_id=attempt.photo_id,
                        clean_preview_final_key=f"preview/{attempt.photo_id}.jpg",
                        clean_preview_source_attempt=attempt,
                    )
                    for attempt in attempts
                ]
            )
        else:
            PhotoFaceEmbeddingProjection.objects.bulk_create(
                [
                    PhotoFaceEmbeddingProjection(
                        photo_id=attempt.photo_id,
                        contract_version=contract,
                        processor_version=version,
                        configuration_hash=run.configuration_hash,
                        accepted_attempt=attempt,
                    )
                    for attempt in attempts
                ]
            )

    # A durable older face projection can remain while a new job is current.
    rotated = photos[49]
    PhotoProcessingState.objects.filter(photo=rotated, processor_type="face_embedding").update(
        status=PhotoProcessingState.Status.QUEUED,
        current_attempt=None,
        accepted_attempt=None,
    )
    with CaptureQueriesContext(connection) as point_queries:
        point_membership = _point_photo_import_backlogs(rotated.pk)
    assert len(point_queries) == 1
    membership = photo_import_backlogs([rotated.pk])
    assert membership == point_membership
    assert membership == {GALLERY_QUEUE: (0, 0), FACE_QUEUE: (1, 0)}

    legacy = Photo.objects.create(id="QWZ-1432", event=event, src="legacy.jpg")
    upper_bound = read_photo_import_backlog_upper_bound()
    assert upper_bound is not None and upper_bound == legacy.pk
    newer = photo((1 << 128) - 1)
    newer.save(force_insert=True)
    assert newer.pk > upper_bound

    after = None
    page_totals = []
    while page := read_photo_import_backlog_page(after=after, upper_bound=upper_bound):
        after, counts = page
        page_totals.append(counts)

    assert page_totals == [
        {GALLERY_QUEUE: (2, 0), FACE_QUEUE: (2, 0)},
        {GALLERY_QUEUE: (1, 0), FACE_QUEUE: (2, 0)},
        {GALLERY_QUEUE: (1, 0), FACE_QUEUE: (1, 0)},
    ]

    for queue in (FACE_QUEUE, GALLERY_QUEUE):
        PhotoImportCounter.objects.get_or_create(pk=queue)
    pauses: list[float] = []
    monkeypatch.setattr(
        "picflow.management.commands.rebuild_photo_import_counters.time.sleep", pauses.append
    )
    call_command("rebuild_photo_import_counters")
    assert pauses == [0.1, 0.1]
    assert set(
        PhotoImportCounter.objects.values_list("queue", "pending", "failed", "initialized")
    ) == {(GALLERY_QUEUE, 5, 0, True), (FACE_QUEUE, 6, 0, True)}

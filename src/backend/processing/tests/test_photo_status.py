from datetime import date
from itertools import count
from typing import cast

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from picflow.models import Event, Photo

from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    FACE_EMBEDDING_BENCHMARK_PROCESSOR,
    FACE_EMBEDDING_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from processing.photo_status import (
    CATEGORY_CANCELLED,
    CATEGORY_FAILED,
    CATEGORY_NOT_REQUIRED,
    CATEGORY_NOT_STARTED,
    CATEGORY_PROCESSING,
    CATEGORY_QUEUED,
    CATEGORY_SUCCEEDED,
    STAGE_NOT_REQUIRED,
    STAGE_NOT_STARTED,
    STAGE_WAITING,
    PhotoProcessingDetail,
    annotate_photo_processing_status,
    photo_processing_details,
    summarize_photo_processing,
)

GENERATION_LEGACY = cast(str, Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1)
GENERATION_PREVIEW = cast(str, Photo.ProcessingGeneration.PREVIEW_FIRST_V1)
GENERATION_WATERMARKED = cast(
    str,
    Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
)
STATE_QUEUED = cast(str, PhotoProcessingState.Status.QUEUED)
STATE_PROCESSING = cast(str, PhotoProcessingState.Status.PROCESSING)
STATE_RETRY_WAIT = cast(str, PhotoProcessingState.Status.RETRY_WAIT)
STATE_SUCCEEDED = cast(str, PhotoProcessingState.Status.SUCCEEDED)
STATE_FAILED = cast(str, PhotoProcessingState.Status.FAILED)
STATE_CANCELLED = cast(str, PhotoProcessingState.Status.CANCELLED)


class PhotoProcessingStatusTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="photo-status-owner")
        self.event = Event.objects.create(
            name="Photo status event",
            slug="photo-status-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            timezone_name="Europe/Moscow",
            face_search_generation=Event.FaceSearchGeneration.ADAFACE_V5,
        )
        self._identity_counter = count(1)

    def private_photo(
        self,
        suffix: str,
        *,
        generation: str = GENERATION_LEGACY,
        event: Event | None = None,
    ) -> Photo:
        policy = {
            GENERATION_LEGACY: cast(str, Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED),
            GENERATION_PREVIEW: cast(str, Photo.GalleryMediaPolicy.PREVIEW_REQUIRED),
            GENERATION_WATERMARKED: cast(
                str,
                Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
            ),
        }[generation]
        return Photo.objects.create(
            id=f"status-{suffix}",
            event=event or self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{suffix}.jpg",
            original_filename=f"{suffix}.jpg",
            original_size=100,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=generation,
            gallery_media_policy=policy,
        )

    def legacy_photo(self, suffix: str) -> Photo:
        return Photo.objects.create(
            id=f"legacy-{suffix}",
            event=self.event,
            src=f"photos/{suffix}.jpg",
        )

    def expected_stages(self, photo: Photo) -> tuple[str, ...]:
        stages = [CAPTURE_METADATA_PROCESSOR]
        if photo.processing_generation in {
            GENERATION_PREVIEW,
            GENERATION_WATERMARKED,
        }:
            stages.append(GENERATE_PREVIEW_PROCESSOR)
        stages.append(FACE_EMBEDDING_PROCESSOR)
        if photo.processing_generation == GENERATION_WATERMARKED:
            stages.append(GENERATE_WATERMARKED_PREVIEW_PROCESSOR)
        return tuple(stages)

    def current_identity(self, photo: Photo, processor_type: str) -> tuple[int, int]:
        if processor_type == CAPTURE_METADATA_PROCESSOR:
            return (1, 2)
        if processor_type in {
            GENERATE_PREVIEW_PROCESSOR,
            GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
        }:
            return (2, 1)
        if photo.processing_generation == GENERATION_LEGACY:
            return (1, 1)
        return (3, 5)

    def set_stage(
        self,
        photo: Photo,
        processor_type: str,
        status: str,
        *,
        contract_version: int | None = None,
        processor_version: int | None = None,
        result: dict[str, object] | None = None,
        error_code: str = "",
        error_detail: str = "",
    ) -> PhotoProcessingState:
        current_contract, current_version = self.current_identity(photo, processor_type)
        contract_version = contract_version or current_contract
        processor_version = processor_version or current_version
        identity = next(self._identity_counter)
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=contract_version,
            processor_type=processor_type,
            processor_version=processor_version,
            configuration={},
            configuration_hash=f"{identity:064x}",
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=contract_version,
            processor_type=processor_type,
            processor_version=processor_version,
            configuration={},
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=status,
        )
        attempt = None
        if status in {
            STATE_PROCESSING,
            STATE_RETRY_WAIT,
            STATE_SUCCEEDED,
            STATE_FAILED,
        }:
            attempt_status = {
                STATE_PROCESSING: ProcessingAttempt.Status.IN_PROGRESS,
                STATE_RETRY_WAIT: ProcessingAttempt.Status.FAILED,
                STATE_SUCCEEDED: ProcessingAttempt.Status.SUCCEEDED,
                STATE_FAILED: ProcessingAttempt.Status.FAILED,
            }[status]
            attempt = ProcessingAttempt.objects.create(
                event=photo.event,
                run=run,
                job=job,
                photo=photo,
                contract_version=contract_version,
                processor_type=processor_type,
                processor_version=processor_version,
                configuration={},
                input_fingerprint={},
                status=attempt_status,
                terminal_at=(
                    None
                    if attempt_status == ProcessingAttempt.Status.IN_PROGRESS
                    else timezone.now()
                ),
                accepted=status == STATE_SUCCEEDED,
                result=result or {},
                error_code=error_code,
                error_detail=error_detail,
            )
        state, _ = PhotoProcessingState.objects.get_or_create(
            photo=photo,
            processor_type=processor_type,
        )
        state.status = status
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt if status == STATE_SUCCEEDED else None
        state.save(
            update_fields=[
                "status",
                "current_run",
                "current_job",
                "current_attempt",
                "accepted_attempt",
                "updated_at",
            ]
        )
        return state

    def complete_other_stages(self, photo: Photo, excluded: set[str]) -> None:
        for processor_type in self.expected_stages(photo):
            if processor_type not in excluded:
                self.set_stage(
                    photo,
                    processor_type,
                    STATE_SUCCEEDED,
                )

    def detail(self, photo: Photo) -> PhotoProcessingDetail:
        return photo_processing_details(Photo.objects.filter(pk=photo.pk))[0]

    def test_processing_precedes_failure_but_failed_stage_remains_visible_and_sanitized(
        self,
    ) -> None:
        photo = self.private_photo(
            "active-and-failed",
            generation=GENERATION_PREVIEW,
        )
        self.set_stage(
            photo,
            CAPTURE_METADATA_PROCESSOR,
            STATE_FAILED,
            error_code="unsupported_input",
            error_detail="originals/private/customer.jpg",
        )
        self.set_stage(
            photo,
            GENERATE_PREVIEW_PROCESSOR,
            STATE_PROCESSING,
        )

        detail = self.detail(photo)

        self.assertEqual(detail["category"], CATEGORY_PROCESSING)
        self.assertIs(detail["has_active_work"], True)
        stages = {stage["processor_type"]: stage for stage in detail["stages"]}
        self.assertEqual(stages[CAPTURE_METADATA_PROCESSOR]["status"], "failed")
        self.assertEqual(stages[CAPTURE_METADATA_PROCESSOR]["error_code"], "unsupported_input")
        self.assertNotIn("error_detail", stages[CAPTURE_METADATA_PROCESSOR])

    def test_retry_wait_is_active_and_not_a_terminal_error(self) -> None:
        photo = self.private_photo("retry")
        self.complete_other_stages(photo, {CAPTURE_METADATA_PROCESSOR})
        self.set_stage(
            photo,
            CAPTURE_METADATA_PROCESSOR,
            STATE_RETRY_WAIT,
            error_code="network_interruption",
            error_detail="temporary private detail",
        )

        detail = self.detail(photo)

        self.assertEqual(detail["category"], CATEGORY_QUEUED)
        self.assertIs(detail["has_active_work"], True)
        capture = next(
            stage
            for stage in detail["stages"]
            if stage["processor_type"] == CAPTURE_METADATA_PROCESSOR
        )
        self.assertEqual(capture["status"], "retry_wait")
        self.assertIsNone(capture["error_code"])

    def test_preview_dependents_wait_only_while_the_predecessor_is_active(self) -> None:
        waiting = self.private_photo(
            "waiting-dependents",
            generation=GENERATION_WATERMARKED,
        )
        self.set_stage(
            waiting,
            GENERATE_PREVIEW_PROCESSOR,
            STATE_QUEUED,
        )
        gated = self.private_photo(
            "gated-dependents",
            generation=GENERATION_WATERMARKED,
        )
        self.set_stage(
            gated,
            GENERATE_PREVIEW_PROCESSOR,
            STATE_SUCCEEDED,
        )
        self.set_stage(
            gated,
            CAPTURE_METADATA_PROCESSOR,
            STATE_SUCCEEDED,
        )

        waiting_detail = self.detail(waiting)
        gated_detail = self.detail(gated)

        self.assertEqual(waiting_detail["category"], CATEGORY_QUEUED)
        waiting_stages = {
            stage["processor_type"]: stage["status"] for stage in waiting_detail["stages"]
        }
        self.assertEqual(waiting_stages[FACE_EMBEDDING_PROCESSOR], STAGE_WAITING)
        self.assertEqual(waiting_stages[GENERATE_WATERMARKED_PREVIEW_PROCESSOR], STAGE_WAITING)
        self.assertEqual(gated_detail["category"], CATEGORY_NOT_STARTED)
        gated_stages = {
            stage["processor_type"]: stage["status"] for stage in gated_detail["stages"]
        }
        self.assertEqual(gated_stages[FACE_EMBEDDING_PROCESSOR], STAGE_NOT_STARTED)
        self.assertEqual(gated_stages[GENERATE_WATERMARKED_PREVIEW_PROCESSOR], STAGE_NOT_STARTED)
        self.assertIs(gated_detail["has_active_work"], False)

    def test_terminal_categories_and_never_enrolled_photos_are_mutually_exclusive(self) -> None:
        failed = self.private_photo("failed")
        self.complete_other_stages(failed, {FACE_EMBEDDING_PROCESSOR})
        self.set_stage(
            failed,
            FACE_EMBEDDING_PROCESSOR,
            STATE_FAILED,
            error_code="model_inference_error",
        )
        cancelled = self.private_photo("cancelled")
        self.complete_other_stages(cancelled, {FACE_EMBEDDING_PROCESSOR})
        self.set_stage(
            cancelled,
            FACE_EMBEDDING_PROCESSOR,
            STATE_CANCELLED,
        )
        never_enrolled = self.private_photo("never-enrolled")

        categories = dict(
            annotate_photo_processing_status(
                Photo.objects.filter(pk__in=[failed.pk, cancelled.pk, never_enrolled.pk])
            ).values_list("pk", "processing_category")
        )

        self.assertEqual(categories[failed.pk], CATEGORY_FAILED)
        self.assertEqual(categories[cancelled.pk], CATEGORY_CANCELLED)
        self.assertEqual(categories[never_enrolled.pk], CATEGORY_NOT_STARTED)

    def test_successful_no_face_result_is_processed(self) -> None:
        photo = self.private_photo("no-face")
        self.set_stage(
            photo,
            CAPTURE_METADATA_PROCESSOR,
            STATE_SUCCEEDED,
            result={"capture_time": None},
        )
        self.set_stage(
            photo,
            FACE_EMBEDDING_PROCESSOR,
            STATE_SUCCEEDED,
            result={"face_count": 0, "faces": [], "warnings": ["no_face_detected"]},
        )

        detail = self.detail(photo)

        self.assertEqual(detail["category"], CATEGORY_SUCCEEDED)
        self.assertTrue(all(stage["status"] == "succeeded" for stage in detail["stages"]))

    def test_old_face_generation_and_benchmark_work_do_not_count_as_current(self) -> None:
        photo = self.private_photo(
            "old-and-benchmark",
            generation=GENERATION_PREVIEW,
        )
        self.set_stage(
            photo,
            CAPTURE_METADATA_PROCESSOR,
            STATE_SUCCEEDED,
        )
        self.set_stage(
            photo,
            GENERATE_PREVIEW_PROCESSOR,
            STATE_SUCCEEDED,
        )
        self.set_stage(
            photo,
            FACE_EMBEDDING_PROCESSOR,
            STATE_SUCCEEDED,
            contract_version=2,
            processor_version=3,
        )
        self.set_stage(
            photo,
            FACE_EMBEDDING_BENCHMARK_PROCESSOR,
            STATE_PROCESSING,
            contract_version=3,
            processor_version=1,
        )

        detail = self.detail(photo)

        self.assertEqual(detail["category"], CATEGORY_NOT_STARTED)
        self.assertIs(detail["has_active_work"], False)
        stages = {stage["processor_type"]: stage for stage in detail["stages"]}
        self.assertEqual(stages[FACE_EMBEDDING_PROCESSOR]["status"], STAGE_NOT_STARTED)
        self.assertNotIn(FACE_EMBEDDING_BENCHMARK_PROCESSOR, stages)

    def test_summary_partitions_exact_scope_and_counts_each_stage(self) -> None:
        succeeded = self.private_photo("summary-succeeded")
        self.complete_other_stages(succeeded, set())
        active = self.private_photo("summary-active")
        Photo.objects.filter(pk=active.pk).update(is_hidden=True)
        self.set_stage(
            active,
            CAPTURE_METADATA_PROCESSOR,
            STATE_PROCESSING,
        )
        not_required = self.legacy_photo("summary-not-required")
        excluded = self.private_photo("summary-excluded")
        self.set_stage(
            excluded,
            CAPTURE_METADATA_PROCESSOR,
            STATE_PROCESSING,
        )

        with CaptureQueriesContext(connection) as queries:
            summary = summarize_photo_processing(
                Photo.objects.filter(pk__in=[succeeded.pk, active.pk, not_required.pk])
            )

        self.assertEqual(summary["total"], 3)
        self.assertEqual(sum(summary["categories"].values()), 3)
        self.assertEqual(summary["categories"][CATEGORY_SUCCEEDED], 1)
        self.assertEqual(summary["categories"][CATEGORY_PROCESSING], 1)
        self.assertEqual(summary["categories"][CATEGORY_NOT_REQUIRED], 1)
        self.assertIs(summary["has_active_work"], True)
        self.assertEqual(summary["stages"][CAPTURE_METADATA_PROCESSOR]["succeeded"], 1)
        self.assertEqual(summary["stages"][CAPTURE_METADATA_PROCESSOR]["processing"], 1)
        self.assertEqual(summary["stages"][CAPTURE_METADATA_PROCESSOR][STAGE_NOT_REQUIRED], 1)
        self.assertEqual(sum(summary["stages"][CAPTURE_METADATA_PROCESSOR].values()), 3)
        self.assertEqual(len(queries), 1)
        processing_table = "processing_photoprocessingstate"
        summary_sql = queries.captured_queries[0]["sql"].lower()
        self.assertIn(processing_table, summary_sql)
        self.assertIn("count(", summary_sql)
        self.assertIn("group by", summary_sql)

    def test_summary_keeps_identity_success_and_dependency_rules(self) -> None:
        old_face = self.private_photo("summary-old-face", generation=GENERATION_PREVIEW)
        self.set_stage(old_face, CAPTURE_METADATA_PROCESSOR, STATE_SUCCEEDED)
        self.set_stage(old_face, GENERATE_PREVIEW_PROCESSOR, STATE_SUCCEEDED)
        self.set_stage(
            old_face,
            FACE_EMBEDDING_PROCESSOR,
            STATE_SUCCEEDED,
            contract_version=2,
            processor_version=3,
        )
        waiting = self.private_photo("summary-waiting", generation=GENERATION_WATERMARKED)
        self.set_stage(waiting, GENERATE_PREVIEW_PROCESSOR, STATE_QUEUED)
        invalid_success = self.private_photo("summary-invalid-success")
        invalid_state = self.set_stage(
            invalid_success,
            CAPTURE_METADATA_PROCESSOR,
            STATE_SUCCEEDED,
        )
        self.set_stage(invalid_success, FACE_EMBEDDING_PROCESSOR, STATE_SUCCEEDED)
        PhotoProcessingState.objects.filter(pk=invalid_state.pk).update(accepted_attempt=None)

        summary = summarize_photo_processing(
            Photo.objects.filter(pk__in=[old_face.pk, waiting.pk, invalid_success.pk])
        )

        self.assertEqual(summary["categories"][CATEGORY_QUEUED], 1)
        self.assertEqual(summary["categories"][CATEGORY_NOT_STARTED], 2)
        self.assertEqual(summary["stages"][CAPTURE_METADATA_PROCESSOR][STATE_SUCCEEDED], 1)
        self.assertEqual(summary["stages"][CAPTURE_METADATA_PROCESSOR][STAGE_NOT_STARTED], 2)
        self.assertEqual(summary["stages"][GENERATE_PREVIEW_PROCESSOR][STATE_SUCCEEDED], 1)
        self.assertEqual(summary["stages"][GENERATE_PREVIEW_PROCESSOR][STATE_QUEUED], 1)
        self.assertEqual(summary["stages"][FACE_EMBEDDING_PROCESSOR][STAGE_WAITING], 1)
        self.assertEqual(summary["stages"][FACE_EMBEDDING_PROCESSOR][STAGE_NOT_STARTED], 1)
        self.assertEqual(
            summary["stages"][GENERATE_WATERMARKED_PREVIEW_PROCESSOR][STAGE_WAITING], 1
        )

    def test_annotation_is_sql_filterable_and_detail_queries_do_not_grow_per_photo(self) -> None:
        first = self.private_photo("bounded-first")
        second = self.private_photo("bounded-second")
        for photo in (first, second):
            self.complete_other_stages(photo, set())

        succeeded_ids = list(
            annotate_photo_processing_status(Photo.objects.all())
            .filter(processing_category=CATEGORY_SUCCEEDED)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        with self.assertNumQueries(1):
            details = photo_processing_details(
                Photo.objects.filter(pk__in=[first.pk, second.pk]).order_by("pk")
            )

        self.assertEqual(succeeded_ids, [first.pk, second.pk])
        self.assertEqual([detail["photo_id"] for detail in details], [first.pk, second.pk])

    def test_detail_read_rejects_more_than_one_bounded_page(self) -> None:
        photos = [
            Photo(id=f"legacy-bounded-{index}", event=self.event, src=f"photos/{index}.jpg")
            for index in range(101)
        ]
        Photo.objects.bulk_create(photos)

        with (
            self.assertNumQueries(1),
            self.assertRaisesMessage(
                ValueError,
                "photo detail page must contain at most 100 photos",
            ),
        ):
            photo_processing_details(Photo.objects.filter(pk__startswith="legacy-bounded-"))

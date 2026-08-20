from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.http import QueryDict
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from ingestion.storage import ObjectMissing, OpenedObject
from processing.models import (
    CAPTURE_METADATA_PROCESSOR,
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

from picflow.forms import EventGalleryFolderFilterForm, EventGalleryTimeFilterForm
from picflow.gallery import (
    CloseableMediaIterator,
    GalleryFaceCrop,
    GalleryMedia,
    GalleryPhoto,
    GalleryPhotoFactory,
    PublicMediaResolver,
    ResolvedPublicMedia,
    gallery_face_crop,
    gallery_folder_choices,
    gallery_photo_queryset,
)
from picflow.models import Event, EventFolder, Photo


class GalleryPresentationContractTests(SimpleTestCase):
    def test_gallery_values_are_frozen(self) -> None:
        small = GalleryMedia(
            url="/events/city-run/photos/photo-42/media/preview-small/",
            variant="preview-small",
        )
        large = GalleryMedia(
            url="/events/city-run/photos/photo-42/media/preview-large/",
            variant="preview-large",
        )
        gallery_photo = GalleryPhoto(
            photo_id="photo-42",
            preview_media_small=small,
            preview_media_large=large,
            download_url="/events/city-run/photos/photo-42/download/",
            capture_time_display="11:03",
            faces=(
                GalleryFaceCrop(
                    detection_id="face-42",
                    face_number=1,
                    left_percent=10,
                    top_percent=20,
                    size_percent=30,
                    search_url="/events/city-run/photos/photo-42/similar-search/face-42/",
                ),
            ),
            alt="Фото photo-42 с события City Run",
        )

        self.assertEqual(gallery_photo.photo_id, "photo-42")
        self.assertEqual(gallery_photo.preview_media_small, small)
        self.assertEqual(gallery_photo.preview_media_large, large)
        self.assertEqual(gallery_photo.download_url, "/events/city-run/photos/photo-42/download/")
        self.assertEqual(gallery_photo.capture_time_display, "11:03")
        self.assertEqual(
            gallery_photo.faces,
            (
                GalleryFaceCrop(
                    detection_id="face-42",
                    face_number=1,
                    left_percent=10,
                    top_percent=20,
                    size_percent=30,
                    search_url="/events/city-run/photos/photo-42/similar-search/face-42/",
                ),
            ),
        )
        self.assertEqual(gallery_photo.alt, "Фото photo-42 с события City Run")
        alt_field = "alt"
        with self.assertRaises(FrozenInstanceError):
            setattr(gallery_photo, alt_field, "changed")
        url_field = "url"
        with self.assertRaises(FrozenInstanceError):
            setattr(small, url_field, "/changed/")

    @patch("boto3.client")
    @patch("picflow.gallery.reverse")
    def test_factory_builds_stable_variant_urls_without_storage(
        self, reverse, boto3_client
    ) -> None:
        event = Event(name="City Run", slug="city-run")
        photo = Photo(id="photo-42", event=event)
        reverse.side_effect = [
            "/events/city-run/photos/photo-42/media/preview-small/",
            "/events/city-run/photos/photo-42/media/preview-large/",
            "/events/city-run/photos/photo-42/download/",
        ]

        gallery_photo = GalleryPhotoFactory.from_photo(photo=photo, event_slug="city-run")

        self.assertEqual(gallery_photo.photo_id, "photo-42")
        self.assertEqual(
            gallery_photo.preview_media_small,
            GalleryMedia(
                url="/events/city-run/photos/photo-42/media/preview-small/",
                variant="preview-small",
            ),
        )
        self.assertEqual(
            gallery_photo.preview_media_large,
            GalleryMedia(
                url="/events/city-run/photos/photo-42/media/preview-large/",
                variant="preview-large",
            ),
        )
        self.assertEqual(gallery_photo.download_url, "/events/city-run/photos/photo-42/download/")
        self.assertEqual(gallery_photo.faces, ())
        self.assertIsNone(gallery_photo.capture_time_display)
        self.assertEqual(gallery_photo.alt, "Фото photo-42 с события City Run")
        self.assertEqual(
            reverse.call_args_list,
            [
                (
                    ("photo_media",),
                    {
                        "kwargs": {
                            "slug": "city-run",
                            "photo_id": "photo-42",
                            "variant": "preview-small",
                        }
                    },
                ),
                (
                    ("photo_media",),
                    {
                        "kwargs": {
                            "slug": "city-run",
                            "photo_id": "photo-42",
                            "variant": "preview-large",
                        }
                    },
                ),
                (
                    ("photo_download",),
                    {"kwargs": {"slug": "city-run", "photo_id": "photo-42"}},
                ),
            ],
        )
        boto3_client.assert_not_called()

    @patch("boto3.client")
    def test_factory_formats_known_capture_time_in_the_event_timezone(self, boto3_client) -> None:
        event = Event(
            name="City Run",
            slug="city-run",
            timezone_name="Europe/London",
        )
        photo = Photo(
            id="photo-42",
            event=event,
            capture_time=datetime(2026, 6, 10, 10, 3, tzinfo=UTC),
        )

        gallery_photo = GalleryPhotoFactory.from_photo(photo=photo, event_slug=event.slug)

        self.assertEqual(gallery_photo.capture_time_display, "11:03")
        boto3_client.assert_not_called()

    @patch("boto3.client")
    def test_factory_uses_scoped_media_and_download_url_builders_without_storage(
        self, boto3_client
    ) -> None:
        event = Event(name="City Run", slug="city-run")
        photo = Photo(id="photo-42", event=event)
        media_calls: list[tuple[str, str]] = []
        download_calls: list[str] = []

        def result_media_url(photo: Photo, variant: str) -> str:
            media_calls.append((photo.id, variant))
            return f"/events/city-run/selfie-search/bearer-token/photos/{photo.id}/media/{variant}/"

        def result_download_url(photo: Photo) -> str:
            download_calls.append(photo.id)
            return f"/events/city-run/selfie-search/bearer-token/photos/{photo.id}/download/"

        gallery_photo = GalleryPhotoFactory.from_photo(
            photo=photo,
            event_slug=event.slug,
            media_url_builder=result_media_url,
            download_url_builder=result_download_url,
        )

        self.assertEqual(
            gallery_photo.preview_media_small.url,
            "/events/city-run/selfie-search/bearer-token/photos/photo-42/media/preview-small/",
        )
        self.assertEqual(
            gallery_photo.preview_media_large.url,
            "/events/city-run/selfie-search/bearer-token/photos/photo-42/media/preview-large/",
        )
        self.assertEqual(
            gallery_photo.download_url,
            "/events/city-run/selfie-search/bearer-token/photos/photo-42/download/",
        )
        self.assertEqual(gallery_photo.faces, ())
        self.assertEqual(
            media_calls,
            [("photo-42", "preview-small"), ("photo-42", "preview-large")],
        )
        self.assertEqual(download_calls, ["photo-42"])
        boto3_client.assert_not_called()

    @patch("boto3.client")
    def test_factory_omits_download_capability_for_watermarked_policy(self, boto3_client) -> None:
        event = Event(name="Paid Run", slug="paid-run", access_type=Event.AccessType.PAID)
        photo = Photo(
            id="paid-photo-42",
            event=event,
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        download_calls: list[str] = []

        gallery_photo = GalleryPhotoFactory.from_photo(
            photo=photo,
            event_slug=event.slug,
            download_url_builder=lambda candidate: download_calls.append(candidate.pk) or "/wrong/",
        )

        self.assertEqual(gallery_photo.photo_id, photo.pk)
        self.assertEqual(gallery_photo.preview_media_small.variant, "preview-small")
        self.assertEqual(gallery_photo.preview_media_large.variant, "preview-large")
        self.assertIsNone(gallery_photo.download_url)
        self.assertEqual(download_calls, [])
        boto3_client.assert_not_called()

    @patch("boto3.client")
    def test_factory_preserves_prepared_face_urls_without_storage(self, boto3_client) -> None:
        event = Event(name="City Run", slug="city-run")
        photo = Photo(id="photo-42", event=event)
        face = GalleryFaceCrop(
            detection_id="face-42",
            face_number=1,
            left_percent=10,
            top_percent=20,
            size_percent=30,
            search_url="/events/city-run/photos/photo-42/similar-search/face-42/",
        )

        gallery_photo = GalleryPhotoFactory.from_photo(
            photo=photo,
            event_slug=event.slug,
            faces=(face,),
        )

        self.assertEqual(gallery_photo.faces, (face,))
        boto3_client.assert_not_called()


class GalleryFaceCropTests(SimpleTestCase):
    """The production break caught here is exposing an invalid or distorted face crop."""

    def test_returns_a_padded_normalized_square_for_a_centered_face(self) -> None:
        geometry = {
            "coordinate_space": "preview-small-v1",
            "pixel_width": 100,
            "pixel_height": 100,
            "bbox": [40, 40, 20, 20],
        }

        crop = gallery_face_crop(detection_id="face-1", face_index=0, geometry=geometry)

        self.assertEqual(
            crop,
            GalleryFaceCrop(
                detection_id="face-1",
                face_number=1,
                left_percent=38.0,
                top_percent=38.0,
                size_percent=24.0,
            ),
        )
        self.assertEqual(
            geometry,
            {
                "coordinate_space": "preview-small-v1",
                "pixel_width": 100,
                "pixel_height": 100,
                "bbox": [40, 40, 20, 20],
            },
        )

    def test_clips_a_padded_square_to_the_preview_edge_without_distortion(self) -> None:
        crop = gallery_face_crop(
            detection_id="face-1",
            face_index=2,
            geometry={
                "coordinate_space": "preview-small-v1",
                "pixel_width": 100,
                "pixel_height": 60,
                "bbox": [85, 45, 15, 15],
            },
        )

        self.assertEqual(
            crop,
            GalleryFaceCrop(
                detection_id="face-1",
                face_number=3,
                left_percent=82.0,
                top_percent=70.0,
                size_percent=18.0,
            ),
        )

    def test_pads_a_rectangular_face_from_its_largest_dimension(self) -> None:
        crop = gallery_face_crop(
            detection_id="face-1",
            face_index=0,
            geometry={
                "coordinate_space": "preview-small-v1",
                "pixel_width": 200,
                "pixel_height": 100,
                "bbox": [80, 20, 20, 40],
            },
        )
        self.assertEqual(
            crop,
            GalleryFaceCrop(
                detection_id="face-1",
                face_number=1,
                left_percent=33.0,
                top_percent=16.0,
                size_percent=24.0,
            ),
        )

    def test_clamps_an_oversized_padded_square_to_the_largest_containing_crop(self) -> None:
        crop = gallery_face_crop(
            detection_id="face-1",
            face_index=0,
            geometry={
                "coordinate_space": "preview-small-v1",
                "pixel_width": 100,
                "pixel_height": 60,
                "bbox": [10, 0, 50, 60],
            },
        )

        self.assertEqual(
            crop,
            GalleryFaceCrop(
                detection_id="face-1",
                face_number=1,
                left_percent=5.0,
                top_percent=0.0,
                size_percent=60.0,
            ),
        )

    def test_rejects_malformed_nonfinite_wrong_space_and_nonpositive_geometry(self) -> None:
        valid = {
            "coordinate_space": "preview-small-v1",
            "pixel_width": 100,
            "pixel_height": 100,
            "bbox": [40, 40, 20, 20],
        }
        invalid_geometries: tuple[object, ...] = (
            {},
            valid | {"bbox": [40, 40, 20]},
            valid | {"bbox": [40, 40, float("nan"), 20]},
            valid | {"coordinate_space": "original-v1"},
            valid | {"pixel_width": 0},
            valid | {"pixel_height": -1},
            valid | {"bbox": [-1, 40, 20, 20]},
            valid | {"bbox": [40, 40, 61, 20]},
        )

        for geometry in invalid_geometries:
            with self.subTest(geometry=geometry):
                self.assertIsNone(
                    gallery_face_crop(detection_id="face-1", face_index=0, geometry=geometry)
                )


class _ReadableBody:
    def __init__(self, reads: list[bytes | Exception]) -> None:
        self._reads = iter(reads)
        self.close_calls = 0

    def read(self, amt: int | None = None) -> bytes:  # noqa: ARG002
        result = next(self._reads)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self) -> None:
        self.close_calls += 1


class _FinalObjectStorage:
    def __init__(self, opened_objects: list[OpenedObject]) -> None:
        self._opened_objects = iter(opened_objects)
        self.opened_keys: list[str] = []

    def open_final(self, *, key: str) -> OpenedObject:
        self.opened_keys.append(key)
        return next(self._opened_objects)

    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str:  # noqa: ARG002
        raise AssertionError("inline resolver tests must not sign media")


class _SignedFinalObjectStorage:
    def __init__(self) -> None:
        self.signed_keys: list[str] = []

    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str:  # noqa: ARG002
        self.signed_keys.append(key)
        return f"https://storage.example.test/{key}?signature=secret"


class _DownloadFinalObjectStorage:
    def __init__(self) -> None:
        self.signed_requests: list[tuple[str, str | None]] = []

    def open_final(self, *, key: str) -> OpenedObject:  # noqa: ARG002
        raise AssertionError("download resolver tests must not open media")

    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str:
        self.signed_requests.append((key, attachment_filename))
        return f"https://storage.example.test/{key}?signature=secret"


class EventGalleryTimeFilterFormTests(SimpleTestCase):
    """The production breaks caught here are broad or timezone-dependent time searches."""

    def make_event(self, **overrides) -> Event:
        values = {
            "name": "Summer festival",
            "slug": "summer-festival",
            "start_date": date(2026, 6, 10),
            "end_date": date(2026, 6, 12),
            "city": "London",
            "timezone_name": "Europe/London",
        }
        values.update(overrides)
        return Event(**values)

    def form(self, event: Event, query: str) -> EventGalleryTimeFilterForm:
        return EventGalleryTimeFilterForm(event, QueryDict(query))

    def test_unfiltered_request_has_no_bounds(self) -> None:
        form = self.form(self.make_event(), "")

        self.assertFalse(form.is_requested)
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.utc_bounds)

    def test_blank_browser_values_do_not_request_a_time_filter(self) -> None:
        form = self.form(self.make_event(), "from=&to=")

        self.assertFalse(form.is_requested)
        self.assertTrue(form.is_valid())
        self.assertIsNone(form.utc_bounds)

    def test_end_only_uses_the_entered_upper_bound(self) -> None:
        form = self.form(self.make_event(), "to=2026-06-10T12:00")

        self.assertTrue(form.is_requested)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.utc_bounds, (None, datetime(2026, 6, 10, 11, 0, tzinfo=UTC)))

    def test_start_only_uses_the_entered_lower_bound(self) -> None:
        event = self.make_event()
        form = self.form(event, "from=2026-06-11T23:50&to=")

        self.assertTrue(form.is_valid())
        self.assertEqual(
            form.utc_bounds,
            (
                datetime(2026, 6, 11, 22, 50, tzinfo=UTC),
                None,
            ),
        )

    def test_uses_event_timezone_not_server_or_browser_timezone(self) -> None:
        event = self.make_event()
        with override_settings(TIME_ZONE="Pacific/Auckland"):
            form = self.form(event, "from=2026-06-10T12:03&to=2026-06-10T12:04")
            self.assertTrue(form.is_valid())

        self.assertEqual(
            form.utc_bounds,
            (
                datetime(2026, 6, 10, 11, 3, tzinfo=UTC),
                datetime(2026, 6, 10, 11, 4, tzinfo=UTC),
            ),
        )

    def test_accepts_event_local_midnight_and_event_range_end(self) -> None:
        form = self.form(self.make_event(), "from=2026-06-10T00:00&to=2026-06-12T23:59")

        self.assertTrue(form.is_valid())
        self.assertEqual(
            form.utc_bounds,
            (
                datetime(2026, 6, 9, 23, 0, tzinfo=UTC),
                datetime(2026, 6, 12, 22, 59, tzinfo=UTC),
            ),
        )

    def test_rejects_repeated_or_malformed_scalar_datetime_values(self) -> None:
        event = self.make_event()
        cases = (
            "from=2026-06-10T12:00&from=2026-06-10T12:01",
            "from=&from=2026-06-10T12:00",
            "from=2026-06-10T12:00&from=",
            "from=2026-06-10T12:00&to=2026-06-10T12:01&to=2026-06-10T12:02",
            "from=2026-06-10",
            "from=2026-06-10T12:00Z",
            "from=2026-06-10T12:00&to=2026-06-10T12:00",
            "from=2026-06-10T12:01&to=2026-06-10T12:00",
            "from=2026-06-13T00:00",
        )

        for query in cases:
            with self.subTest(query=query):
                form = self.form(event, query)
                self.assertFalse(form.is_valid())
                self.assertIsNone(form.utc_bounds)

    def test_rejects_nonexistent_and_ambiguous_dst_wall_times(self) -> None:
        event = self.make_event(
            start_date=date(2026, 3, 8),
            end_date=date(2026, 11, 1),
            timezone_name="America/New_York",
        )
        for query, error_field in (
            ("from=2026-03-08T02:30", "from"),
            ("from=2026-11-01T01:30", "from"),
            ("from=2026-03-08T01:30&to=2026-03-08T02:30", "to"),
        ):
            with self.subTest(query=query):
                form = self.form(event, query)
                self.assertFalse(form.is_valid())
                self.assertIn(error_field, form.errors)


class FilteredGalleryQuerysetTests(TestCase):
    """The break caught here is treating convenient metadata JSON as accepted evidence."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="time-filter-gallery")
        self.event = Event.objects.create(
            name="Time filter gallery",
            slug="time-filter-gallery",
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 12),
            city="London",
            timezone_name="Europe/London",
        )

    def photo(
        self, photo_id: str, *, event: Event | None = None, filename: str | None = None
    ) -> Photo:
        return Photo.objects.create(
            id=photo_id,
            event=event or self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{photo_id}",
            original_filename=filename or f"{photo_id}.jpg",
            original_size=4,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )

    def capture_evidence(
        self,
        photo: Photo,
        *,
        capture_time: object,
    ) -> ProcessingAttempt:
        configuration = {"capture_metadata": {"event_timezone": photo.event.timezone_name}}
        run = EventProcessingRun.objects.create(
            event=photo.event,
            contract_version=2,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=2,
            configuration=configuration,
            configuration_hash=uuid4().hex + uuid4().hex,
        )
        job = ProcessingJob.objects.create(
            event=photo.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=2,
            configuration=configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
            completed_at=timezone.now(),
        )
        attempt = ProcessingAttempt.objects.create(
            event=photo.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type=CAPTURE_METADATA_PROCESSOR,
            processor_version=2,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
            result={"capture_time": capture_time},
        )
        state = PhotoProcessingState.objects.get(
            photo=photo, processor_type=CAPTURE_METADATA_PROCESSOR
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.save()
        return attempt

    def queryset(self):
        return gallery_photo_queryset(
            event=self.event,
            capture_time_start=datetime(2026, 6, 10, 9, 50, tzinfo=UTC),
            capture_time_end=datetime(2026, 6, 10, 10, 10, tzinfo=UTC),
        )

    def test_folder_choices_come_from_base_eligible_photos_and_folder_filters_are_or_then_time_and(
        self,
    ) -> None:
        start = EventFolder.objects.create(event=self.event, name="Старт")
        finish = EventFolder.objects.create(event=self.event, name="Финиш")
        empty = EventFolder.objects.create(event=self.event, name="Пустая")
        start_photo = self.photo("start", filename="a.jpg")
        finish_photo = self.photo("finish", filename="b.jpg")
        unfiled_photo = self.photo("unfiled", filename="c.jpg")
        hidden_photo = Photo.objects.create(id="hidden-folder", event=self.event, src="hidden.jpg")
        other_event = Event.objects.create(
            name="Other folders",
            slug="other-folders",
            start_date=self.event.start_date,
            end_date=self.event.end_date,
            city="London",
            timezone_name="Europe/London",
        )
        other_folder = EventFolder.objects.create(event=other_event, name="Other")
        other_photo = self.photo("other-folder", event=other_event)
        attempts = {
            photo: self.capture_evidence(photo, capture_time="2026-06-10T10:00:00Z")
            for photo in (start_photo, finish_photo, unfiled_photo, hidden_photo, other_photo)
        }
        for photo, capture_attempt in attempts.items():
            Photo.objects.filter(pk=photo.pk).update(
                capture_time=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
                capture_time_source_attempt=capture_attempt,
            )
        Photo.objects.filter(pk=start_photo.pk).update(folder=start)
        Photo.objects.filter(pk=finish_photo.pk).update(folder=finish)
        Photo.objects.filter(pk=hidden_photo.pk).update(folder=empty)
        Photo.objects.filter(pk=other_photo.pk).update(folder=other_folder)

        base_queryset = gallery_photo_queryset(event=self.event)
        choices, has_unfiled = gallery_folder_choices(event=self.event, base_queryset=base_queryset)

        self.assertEqual(tuple(folder.pk for folder in choices), (start.pk, finish.pk))
        self.assertTrue(has_unfiled)
        self.assertEqual(
            list(gallery_photo_queryset(event=self.event, folder_ids=(start.pk, finish.pk))),
            [start_photo, finish_photo],
        )
        self.assertEqual(
            list(
                gallery_photo_queryset(
                    event=self.event, folder_ids=(start.pk,), include_unfiled=True
                )
            ),
            [start_photo, unfiled_photo],
        )
        self.assertEqual(
            list(
                gallery_photo_queryset(
                    event=self.event,
                    folder_ids=(start.pk, finish.pk),
                    capture_time_start=datetime(2026, 6, 10, 9, 50, tzinfo=UTC),
                    capture_time_end=datetime(2026, 6, 10, 10, 10, tzinfo=UTC),
                )
            ),
            [start_photo, finish_photo],
        )

    def test_uses_photo_capture_time_with_inclusive_boundaries_after_media_eligibility(
        self,
    ) -> None:
        included_lower = self.photo("included-lower", filename="b.jpg")
        included_upper = self.photo("included-upper", filename="a.jpg")
        excluded_before = self.photo("excluded-before")
        excluded_after = self.photo("excluded-after")
        null_projection = self.photo("null-projection")
        attempts = {
            photo: self.capture_evidence(photo, capture_time="2026-06-10T10:00:00Z")
            for photo in (included_lower, included_upper, excluded_before, excluded_after)
        }
        Photo.objects.filter(pk=included_lower.pk).update(
            capture_time=datetime(2026, 6, 10, 9, 50, tzinfo=UTC),
            capture_time_source_attempt=attempts[included_lower],
        )
        Photo.objects.filter(pk=included_upper.pk).update(
            capture_time=datetime(2026, 6, 10, 10, 10, tzinfo=UTC),
            capture_time_source_attempt=attempts[included_upper],
        )
        Photo.objects.filter(pk=excluded_before.pk).update(
            capture_time=datetime(2026, 6, 10, 9, 49, 59, tzinfo=UTC),
            capture_time_source_attempt=attempts[excluded_before],
        )
        Photo.objects.filter(pk=excluded_after.pk).update(
            capture_time=datetime(2026, 6, 10, 10, 10, 1, tzinfo=UTC),
            capture_time_source_attempt=attempts[excluded_after],
        )
        self.capture_evidence(null_projection, capture_time="2026-06-10T10:00:00Z")

        self.assertEqual(list(self.queryset()), [included_upper, included_lower])

    def test_filtered_projection_query_has_no_capture_metadata_json_reader(self) -> None:
        photo = self.photo("projected")
        attempt = self.capture_evidence(photo, capture_time="2026-06-10T10:00:00Z")
        Photo.objects.filter(pk=photo.pk).update(
            capture_time=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
            capture_time_source_attempt=attempt,
        )

        query_sql = str(self.queryset().query)

        self.assertNotIn("capture_metadata", query_sql)
        self.assertNotIn("::timestamp", query_sql)
        self.assertNotIn("JSON", query_sql)

    def test_unfiltered_gallery_does_not_require_a_capture_time_projection(self) -> None:
        without_projection = self.photo("without-projection")
        with_projection = self.photo("with-projection")
        attempt = self.capture_evidence(with_projection, capture_time="2026-06-10T10:00:00Z")
        Photo.objects.filter(pk=with_projection.pk).update(
            capture_time=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
            capture_time_source_attempt=attempt,
        )

        self.assertEqual(
            list(gallery_photo_queryset(event=self.event)),
            [without_projection, with_projection],
        )

    def test_filtered_projection_reader_preserves_event_and_media_eligibility(self) -> None:
        included = self.photo("included")
        accepted_attempt = self.capture_evidence(included, capture_time="2026-06-10T10:00:00Z")
        Photo.objects.filter(pk=included.pk).update(
            capture_time=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
            capture_time_source_attempt=accepted_attempt,
        )
        other_event = Event.objects.create(
            name="Other gallery",
            slug="other-gallery",
            start_date=date(2026, 6, 10),
            end_date=date(2026, 6, 10),
            city="London",
            timezone_name="Europe/London",
        )
        other_photo = self.photo("other", event=other_event)
        other_attempt = self.capture_evidence(other_photo, capture_time="2026-06-10T10:00:00Z")
        Photo.objects.filter(pk=other_photo.pk).update(
            capture_time=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
            capture_time_source_attempt=other_attempt,
        )
        hidden = Photo.objects.create(id="hidden", event=self.event, src="photos/hidden.jpg")
        hidden_attempt = self.capture_evidence(hidden, capture_time="2026-06-10T10:00:00Z")
        Photo.objects.filter(pk=hidden.pk).update(
            capture_time=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
            capture_time_source_attempt=hidden_attempt,
        )

        self.assertEqual(list(self.queryset()), [included])

    def test_keeps_filename_id_order_and_one_hundred_item_pages_after_filtering(self) -> None:
        for index in range(101):
            photo = self.photo(f"photo-{index:03}", filename=f"image-{index:03}.jpg")
            attempt = self.capture_evidence(photo, capture_time="2026-06-10T10:00:00Z")
            Photo.objects.filter(pk=photo.pk).update(
                capture_time=datetime(2026, 6, 10, 10, 0, tzinfo=UTC),
                capture_time_source_attempt=attempt,
            )

        page_one = Paginator(self.queryset(), 100).page(1)
        page_two = Paginator(self.queryset(), 100).page(2)

        self.assertEqual(
            [photo.pk for photo in page_one.object_list],
            [f"photo-{index:03}" for index in range(100)],
        )
        self.assertEqual([photo.pk for photo in page_two.object_list], ["photo-100"])


class EventGalleryFolderFilterFormTests(SimpleTestCase):
    def test_ignores_unknown_malformed_duplicate_and_foreign_folder_ids(self) -> None:
        event = Event(pk=1)
        start = EventFolder(pk=4, event=event, name="Старт")
        finish = EventFolder(pk=8, event=event, name="Финиш")

        form = EventGalleryFolderFilterForm(
            event,
            (start, finish),
            QueryDict("folder=8&folder=nope&folder=8&folder=4&folder=999&unfiled=1"),
            include_unfiled=True,
        )

        self.assertTrue(form.is_valid())
        self.assertEqual(form.selected_folder_ids, (4, 8))
        self.assertTrue(form.include_unfiled)


class PublicGalleryMediaTests(SimpleTestCase):
    def test_resolver_maps_legacy_small_and_large_variants_to_original(self) -> None:
        jpeg_body = _ReadableBody([])
        png_body = _ReadableBody([])
        storage = _FinalObjectStorage(
            [
                OpenedObject(body=jpeg_body, size=123, content_type="image/jpeg"),
                OpenedObject(body=png_body, size=456, content_type="image/png"),
            ]
        )
        resolver = PublicMediaResolver(storage)
        photo = Photo(id="photo-42", original_key="originals/0123456789abcdef0123456789abcdef")

        small = resolver.resolve(photo=photo, variant="preview-small")
        large = resolver.resolve(photo=photo, variant="preview-large")

        self.assertEqual(
            small,
            ResolvedPublicMedia(
                body=jpeg_body,
                content_length=123,
                content_type="image/jpeg",
                extension="jpg",
            ),
        )
        self.assertEqual(
            large,
            ResolvedPublicMedia(
                body=png_body,
                content_length=456,
                content_type="image/png",
                extension="png",
            ),
        )
        self.assertEqual(storage.opened_keys, [photo.original_key, photo.original_key])

    def test_resolver_rejects_unknown_variant_before_storage(self) -> None:
        storage = _FinalObjectStorage([])
        resolver = PublicMediaResolver(storage)
        photo = Photo(id="photo-42", original_key="originals/0123456789abcdef0123456789abcdef")

        with self.assertRaisesMessage(ValueError, "ineligible gallery media"):
            resolver.resolve(photo=photo, variant="original")  # type: ignore[arg-type]

        self.assertEqual(storage.opened_keys, [])

    def test_download_resolver_signs_the_original_with_a_jpeg_attachment_name(self) -> None:
        storage = _DownloadFinalObjectStorage()
        photo = Photo(
            id="photo-42",
            original_key="originals/0123456789abcdef0123456789abcdef",
            original_content_type="image/jpeg",
        )

        url = PublicMediaResolver(storage).resolve_download(photo=photo)

        self.assertEqual(
            url,
            "https://storage.example.test/originals/0123456789abcdef0123456789abcdef?signature=secret",
        )
        self.assertEqual(
            storage.signed_requests,
            [(photo.original_key, "findme-photo-photo-42.jpg")],
        )

    def test_download_resolver_derives_a_png_attachment_name(self) -> None:
        storage = _DownloadFinalObjectStorage()
        photo = Photo(
            id="photo-42",
            original_key="originals/0123456789abcdef0123456789abcdef",
            original_content_type="image/png",
        )

        PublicMediaResolver(storage).resolve_download(photo=photo)

        self.assertEqual(
            storage.signed_requests,
            [(photo.original_key, "findme-photo-photo-42.png")],
        )

    def test_download_resolver_rejects_a_missing_original_key_before_signing(self) -> None:
        storage = _DownloadFinalObjectStorage()
        photo = Photo(id="photo-42", original_key=None, original_content_type="image/jpeg")

        with self.assertRaisesMessage(ValueError, "ineligible original download"):
            PublicMediaResolver(storage).resolve_download(photo=photo)

        self.assertEqual(storage.signed_requests, [])

    def test_download_resolver_rejects_an_unsupported_original_type_before_signing(self) -> None:
        storage = _DownloadFinalObjectStorage()
        photo = Photo(
            id="photo-42",
            original_key="originals/0123456789abcdef0123456789abcdef",
            original_content_type="image/webp",
        )

        with self.assertRaisesMessage(ValueError, "ineligible original download"):
            PublicMediaResolver(storage).resolve_download(photo=photo)

        self.assertEqual(storage.signed_requests, [])


class PaidWatermarkedGalleryTests(TestCase):
    """The breaks caught here expose paid originals or accept inconsistent watermark evidence."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="paid-watermark-gallery")
        self.event = Event.objects.create(
            name="Paid gallery",
            slug="paid-gallery",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
        )

    def photo(self, photo_id: str, *, policy: str) -> Photo:
        generation = {
            Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED: (
                Photo.ProcessingGeneration.LEGACY_ORIGINAL_V1
            ),
            Photo.GalleryMediaPolicy.PREVIEW_REQUIRED: Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED: (
                Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
            ),
        }[policy]
        return Photo.objects.create(
            id=photo_id,
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{photo_id}",
            original_filename=f"{photo_id}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=generation,
            gallery_media_policy=policy,
        )

    def publish(self, photo: Photo, *, processor_type: str, variant: str) -> PhotoDerivative:
        configuration = {processor_type: {"variant": variant}}
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            configuration_hash=uuid4().hex + uuid4().hex,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            configuration_hash=run.configuration_hash,
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
            completed_at=timezone.now(),
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        state, _ = PhotoProcessingState.objects.get_or_create(
            photo=photo,
            processor_type=processor_type,
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.succeeded_at = timezone.now()
        state.save()
        return PhotoDerivative.objects.create(
            photo=photo,
            variant=variant,
            final_key=f"derivatives/previews/{photo.pk}/{variant}/{uuid4().hex}.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )

    def test_paid_event_surface_requires_gate_and_only_lists_consistent_watermarks(self) -> None:
        legacy = self.photo("paid-legacy", policy=Photo.GalleryMediaPolicy.LEGACY_ORIGINAL_ALLOWED)
        clean = self.photo("paid-clean", policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED)
        self.publish(
            clean,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            variant="preview-small-v1",
        )
        ready = self.photo(
            "paid-watermark-ready",
            policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        self.publish(
            ready,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
        pending = self.photo(
            "paid-watermark-pending",
            policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        pending_state, _ = PhotoProcessingState.objects.get_or_create(
            photo=pending,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
        )
        pending_state.status = PhotoProcessingState.Status.PROCESSING
        pending_state.save(update_fields=["status"])
        inconsistent = self.photo(
            "paid-watermark-inconsistent",
            policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        derivative = self.publish(
            inconsistent,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
        inconsistent_state = PhotoProcessingState.objects.get(
            photo=inconsistent,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
        )
        inconsistent_state.accepted_attempt = None
        inconsistent_state.save(update_fields=["accepted_attempt"])

        self.assertEqual(
            list(
                gallery_photo_queryset(
                    event=self.event,
                    paid_watermarked_previews_enabled=False,
                )
            ),
            [],
        )
        self.assertEqual(
            list(
                gallery_photo_queryset(
                    event=self.event,
                    paid_watermarked_previews_enabled=True,
                )
            ),
            [ready],
        )
        self.assertTrue(derivative.final_key)
        self.assertTrue(legacy.original_key)

    def test_resolver_maps_both_semantic_roles_to_watermark_and_denies_download_before_signing(
        self,
    ) -> None:
        photo = self.photo(
            "paid-resolver", policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
        )
        derivative = self.publish(
            photo,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
        storage = _SignedFinalObjectStorage()
        resolver = PublicMediaResolver(storage)  # type: ignore[arg-type]

        for variant in ("preview-small", "preview-large"):
            self.assertEqual(
                resolver.resolve_signed(photo=photo, variant=variant),
                f"https://storage.example.test/{derivative.final_key}?signature=secret",
            )
        with self.assertRaisesMessage(ValueError, "ineligible original download"):
            resolver.resolve_download(photo=photo)

        self.assertEqual(storage.signed_keys, [derivative.final_key, derivative.final_key])


class PreviewRequiredPublicGalleryMediaTests(TestCase):
    """The break caught here would expose an original tile before preview publication."""

    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="preview-gallery-reader")
        self.event = Event.objects.create(
            name="Preview gallery",
            slug="preview-gallery",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
        )

    def make_preview_required_photo(self, *, photo_id: str) -> Photo:
        return Photo.objects.create(
            id=photo_id,
            event=self.event,
            src="",
            uploaded_by=self.user,
            original_key=f"originals/{photo_id}",
            original_filename="race.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )

    def publish_preview(self, photo: Photo) -> PhotoDerivative:
        configuration = {"generate_preview": {"variant": "preview-small-v1"}}
        run = EventProcessingRun.objects.create(
            event=self.event,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
        )
        job = ProcessingJob.objects.create(
            event=self.event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash="a" * 64,
            input_fingerprint={},
            status=ProcessingJob.Status.SUCCEEDED,
            completed_at=timezone.now(),
        )
        attempt = ProcessingAttempt.objects.create(
            event=self.event,
            run=run,
            job=job,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        state = PhotoProcessingState.objects.get(
            photo=photo, processor_type=GENERATE_PREVIEW_PROCESSOR
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.succeeded_at = timezone.now()
        state.save()
        return PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-small-v1",
            final_key=f"derivatives/previews/{photo.id}/preview-small-v1/{uuid4().hex}.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )

    def test_resolver_reads_new_small_from_derivative_and_large_from_original(self) -> None:
        photo = self.make_preview_required_photo(photo_id="preview-photo")
        derivative = self.publish_preview(photo)
        preview_body = _ReadableBody([])
        original_body = _ReadableBody([])
        storage = _FinalObjectStorage(
            [
                OpenedObject(body=preview_body, size=9, content_type="image/jpeg"),
                OpenedObject(body=original_body, size=10, content_type="image/jpeg"),
            ]
        )

        resolver = PublicMediaResolver(storage)

        self.assertEqual(resolver.resolve(photo=photo, variant="preview-small").body, preview_body)
        self.assertEqual(resolver.resolve(photo=photo, variant="preview-large").body, original_body)
        self.assertEqual(storage.opened_keys, [derivative.final_key, photo.original_key])

    def test_signed_resolver_selects_preview_for_tile_and_original_for_lightbox(self) -> None:
        photo = self.make_preview_required_photo(photo_id="preview-photo")
        derivative = self.publish_preview(photo)
        storage = _SignedFinalObjectStorage()
        resolver = PublicMediaResolver(storage)  # type: ignore[arg-type]

        self.assertEqual(
            resolver.resolve_signed(photo=photo, variant="preview-small"),
            f"https://storage.example.test/{derivative.final_key}?signature=secret",
        )
        self.assertEqual(
            resolver.resolve_signed(photo=photo, variant="preview-large"),
            f"https://storage.example.test/{photo.original_key}?signature=secret",
        )
        self.assertEqual(storage.signed_keys, [derivative.final_key, photo.original_key])

    def test_resolver_never_falls_back_to_original_when_new_small_preview_is_missing(self) -> None:
        photo = self.make_preview_required_photo(photo_id="missing-preview")
        storage = _FinalObjectStorage([])

        with self.assertRaises(ObjectMissing):
            PublicMediaResolver(storage).resolve(photo=photo, variant="preview-small")

        self.assertEqual(storage.opened_keys, [])

    def test_resolver_requires_original_key_before_storage(self) -> None:
        storage = _FinalObjectStorage([])
        resolver = PublicMediaResolver(storage)
        photo = Photo(id="photo-42", original_key=None)

        with self.assertRaisesMessage(ValueError, "ineligible gallery media"):
            resolver.resolve(photo=photo, variant="preview-small")

        self.assertEqual(storage.opened_keys, [])

    def test_iterator_closes_after_eof(self) -> None:
        body = _ReadableBody([b"first", b""])
        iterator = CloseableMediaIterator(
            media=ResolvedPublicMedia(body, 5, "image/jpeg", "jpg"),
            event_slug="city-run",
            photo_id="photo-42",
        )

        self.assertEqual(list(iterator), [b"first"])
        self.assertEqual(body.close_calls, 1)

    def test_iterator_closes_and_sanitizes_read_failure(self) -> None:
        body = _ReadableBody([RuntimeError("S3 access token: secret")])
        iterator = CloseableMediaIterator(
            media=ResolvedPublicMedia(body, 5, "image/jpeg", "jpg"),
            event_slug="city-run",
            photo_id="photo-42",
        )

        with self.assertLogs("picflow.gallery", level="ERROR") as logs:
            with self.assertRaises(StopIteration):
                next(iterator)

        self.assertEqual(body.close_calls, 1)
        self.assertEqual(logs.output, ["ERROR:picflow.gallery:Public photo stream ended early"])
        self.assertEqual(logs.records[0].event_slug, "city-run")
        self.assertEqual(logs.records[0].photo_id, "photo-42")

    def test_iterator_close_before_first_next_closes_body(self) -> None:
        body = _ReadableBody([])
        iterator = CloseableMediaIterator(
            media=ResolvedPublicMedia(body, 0, "image/jpeg", "jpg"),
            event_slug="city-run",
            photo_id="photo-42",
        )

        iterator.close()

        self.assertEqual(body.close_calls, 1)
        with self.assertRaises(StopIteration):
            next(iterator)
        self.assertEqual(body.close_calls, 1)

    def test_iterator_close_after_partial_read_closes_body(self) -> None:
        body = _ReadableBody([b"first"])
        iterator = CloseableMediaIterator(
            media=ResolvedPublicMedia(body, 5, "image/jpeg", "jpg"),
            event_slug="city-run",
            photo_id="photo-42",
        )

        self.assertEqual(next(iterator), b"first")
        iterator.close()

        self.assertEqual(body.close_calls, 1)
        with self.assertRaises(StopIteration):
            next(iterator)
        self.assertEqual(body.close_calls, 1)

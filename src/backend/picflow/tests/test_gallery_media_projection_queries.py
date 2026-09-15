from datetime import date, timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from commerce.checkout import CheckoutEmptyCart, create_checkout
from commerce.identity import browser_token_sha256
from commerce.models import Cart, CartItem
from commerce.services import set_photo_selected
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, modify_settings, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from feature_flags.registry import PAID_EVENTS, PAID_WATERMARKED_PREVIEWS
from feature_flags.states import FEATURE_FLAG_ON
from feature_flags.testing import override_feature_flags
from ingestion.storage import ObjectMissing, OpenedObject
from processing.models import (
    GENERATE_PREVIEW_PROCESSOR,
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from selfie_search.models import SelfieSearch, SelfieSearchResult
from selfie_search.services.results import saved_ready_result_photos
from selfie_search.services.submission import submit_gallery_photo_search

from picflow.gallery import (
    GalleryMediaPurpose,
    PublicMediaResolver,
    gallery_photo_queryset,
    public_gallery_photo,
)
from picflow.gallery_media_projection import publish_gallery_media
from picflow.models import Event, GalleryMediaProjection, Photo

FORBIDDEN_RELATIONS = (
    "processing_photoprocessingstate",
    "processing_processingattempt",
    "processing_photoderivative",
)


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
@modify_settings(MIDDLEWARE={"remove": "whitenoise.middleware.WhiteNoiseMiddleware"})
class GalleryMediaProjectionQueryTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user(username="gallery-projection-query")
        self.event = Event.objects.create(
            name="Projection query event",
            slug="projection-query-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        self.paid_event = Event.objects.create(
            name="Paid projection query event",
            slug="paid-projection-query-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )

    def photo(self, photo_id: str, **overrides) -> Photo:
        values = {
            "id": photo_id,
            "event": self.event,
            "src": "",
            "uploaded_by": self.user,
            "original_key": f"originals/{photo_id}",
            "original_filename": f"{photo_id}.jpg",
            "original_size": 10,
            "original_content_type": "image/jpeg",
            "uploaded_at": timezone.now(),
        }
        values.update(overrides)
        return Photo.objects.create(**values)

    def projected_photo(
        self,
        photo_id: str,
        *,
        event: Event,
        processor_type: str,
        variant: str,
    ) -> Photo:
        watermarked = processor_type == GENERATE_WATERMARKED_PREVIEW_PROCESSOR
        photo = self.photo(
            photo_id,
            event=event,
            processing_generation=(
                Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1
                if watermarked
                else Photo.ProcessingGeneration.PREVIEW_FIRST_V1
            ),
            gallery_media_policy=(
                Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED
                if watermarked
                else Photo.GalleryMediaPolicy.PREVIEW_REQUIRED
            ),
        )
        configuration = {processor_type: {"variant": variant}}
        run = EventProcessingRun.objects.create(
            event=event,
            contract_version=2,
            processor_type=processor_type,
            processor_version=1,
            configuration=configuration,
            configuration_hash=uuid4().hex + uuid4().hex,
        )
        job = ProcessingJob.objects.create(
            event=event,
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
            event=event,
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
        PhotoProcessingState.objects.update_or_create(
            photo=photo,
            processor_type=processor_type,
            defaults={
                "status": PhotoProcessingState.Status.SUCCEEDED,
                "current_run": run,
                "current_job": job,
                "current_attempt": attempt,
                "accepted_attempt": attempt,
                "succeeded_at": timezone.now(),
            },
        )
        derivative = PhotoDerivative.objects.create(
            photo=photo,
            variant=variant,
            final_key=f"derivatives/previews/{photo.pk}/{variant}/accepted.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )
        photo.gallery_media_projection = publish_gallery_media(derivative)
        return photo

    def assert_no_processing_relations(self, queries: CaptureQueriesContext) -> None:
        sql = "\n".join(query["sql"] for query in queries.captured_queries).casefold()
        for relation in FORBIDDEN_RELATIONS:
            with self.subTest(relation=relation):
                self.assertNotIn(relation, sql)

    def test_collection_eligibility_does_not_join_processing_relations(self) -> None:
        self.photo("legacy-original")

        with CaptureQueriesContext(connection) as queries:
            photo_ids = list(gallery_photo_queryset(event=self.event).values_list("pk", flat=True))

        self.assertEqual(photo_ids, ["legacy-original"])
        self.assert_no_processing_relations(queries)

    def test_collection_semantics_use_only_matching_projection_slots(self) -> None:
        legacy = self.photo("semantic-legacy")
        clean = self.projected_photo(
            "semantic-clean",
            event=self.event,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            variant="preview-small-v1",
        )
        watermarked = self.projected_photo(
            "semantic-watermarked",
            event=self.paid_event,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
        self.photo(
            "semantic-missing-slot",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        self.photo("semantic-hidden", is_hidden=True)
        unpublished = Event.objects.create(
            name="Unpublished projection query event",
            slug="unpublished-projection-query-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            publication_status=Event.PublicationStatus.DRAFT,
        )
        self.photo("semantic-unpublished", event=unpublished)
        other = Event.objects.create(
            name="Other projection query event",
            slug="other-projection-query-event",
            start_date=date.today(),
            end_date=date.today(),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
        )
        self.photo("semantic-other-event", event=other)

        free_ids = set(gallery_photo_queryset(event=self.event).values_list("pk", flat=True))
        paid_ids = set(
            gallery_photo_queryset(
                event=self.paid_event,
                paid_watermarked_previews_enabled=True,
            ).values_list("pk", flat=True)
        )

        self.assertEqual(free_ids, {legacy.pk, clean.pk})
        self.assertEqual(paid_ids, {watermarked.pk})

    @patch("config.views.gallery_search_faces_by_photo", return_value={})
    @patch("config.views.PrivateUploadStorage")
    def test_gallery_pages_sign_projected_previews_without_processing_relation_queries(
        self,
        storage_class,
        _gallery_search_faces_by_photo,
    ) -> None:
        clean = self.projected_photo(
            "page-clean",
            event=self.event,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            variant="preview-small-v1",
        )
        watermarked = self.projected_photo(
            "page-watermarked",
            event=self.paid_event,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )
        storage_class.return_value.sign_accepted_preview.side_effect = lambda *, key, expires_in: (
            f"https://storage.example.test/{expires_in}/{key}"
        )

        with CaptureQueriesContext(connection) as clean_queries:
            clean_response = self.client.get(
                reverse("event_detail", kwargs={"slug": self.event.slug})
            )
        with (
            override_feature_flags(
                {
                    PAID_EVENTS: FEATURE_FLAG_ON,
                    PAID_WATERMARKED_PREVIEWS: FEATURE_FLAG_ON,
                }
            ),
            CaptureQueriesContext(connection) as watermarked_queries,
        ):
            watermarked_response = self.client.get(
                reverse("event_detail", kwargs={"slug": self.paid_event.slug})
            )

        self.assertContains(
            clean_response,
            f"https://storage.example.test/21600/{clean.gallery_media_projection.clean_preview_final_key}",
        )
        self.assertContains(
            watermarked_response,
            "https://storage.example.test/21600/"
            f"{watermarked.gallery_media_projection.watermarked_preview_final_key}",
        )
        self.assert_no_processing_relations(clean_queries)
        self.assert_no_processing_relations(watermarked_queries)

    def test_exact_lookup_is_one_projection_query_scoped_by_photo_and_event(self) -> None:
        photo = self.projected_photo(
            "exact-clean",
            event=self.event,
            processor_type=GENERATE_PREVIEW_PROCESSOR,
            variant="preview-small-v1",
        )
        storage = _SigningStorage()
        with CaptureQueriesContext(connection) as queries:
            selected = public_gallery_photo(
                event_id=self.event.pk,
                photo_id=photo.pk,
                purpose=GalleryMediaPurpose.PRESENTATION,
            )
            signed_url = PublicMediaResolver(storage).resolve_signed(
                photo=selected,
                variant="preview-small",
            )

        self.assertEqual(selected, photo)
        self.assertEqual(signed_url, "https://storage.example.test/signed")
        self.assertEqual(len(queries), 1)
        sql = queries[0]["sql"].casefold()
        self.assertIn('"picflow_photo"."id" =', sql)
        self.assertIn('"picflow_photo"."event_id" =', sql)
        for relation in FORBIDDEN_RELATIONS:
            with self.subTest(relation=relation):
                self.assertNotIn(relation, sql)

    def test_exact_lookup_fails_closed_for_scope_visibility_slot_and_purpose(self) -> None:
        hidden = self.photo("exact-hidden", is_hidden=True)
        missing_slot = self.photo(
            "exact-missing-slot",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        watermarked = self.projected_photo(
            "exact-watermarked",
            event=self.paid_event,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )

        rejected = (
            (hidden, self.event.pk, GalleryMediaPurpose.PRESENTATION, False),
            (missing_slot, self.event.pk, GalleryMediaPurpose.PRESENTATION, False),
            (watermarked, self.event.pk, GalleryMediaPurpose.PRESENTATION, False),
            (watermarked, self.event.pk, GalleryMediaPurpose.PRESENTATION, True),
            (watermarked, self.paid_event.pk, GalleryMediaPurpose.ORIGINAL_DOWNLOAD, True),
        )
        for photo, event_id, purpose, gate in rejected:
            with self.subTest(photo=photo.pk, purpose=purpose, gate=gate):
                with self.assertRaises(Photo.DoesNotExist):
                    public_gallery_photo(
                        event_id=event_id,
                        photo_id=photo.pk,
                        purpose=purpose,
                        paid_watermarked_previews_enabled=gate,
                    )

        purchased = public_gallery_photo(
            event_id=self.paid_event.pk,
            photo_id=watermarked.pk,
            purpose=GalleryMediaPurpose.PURCHASE,
            paid_watermarked_previews_enabled=True,
        )
        self.assertEqual(purchased, watermarked)

    def test_exact_download_uses_projection_query_without_processing_relations(self) -> None:
        photo = self.photo("exact-download")

        with CaptureQueriesContext(connection) as queries:
            selected = public_gallery_photo(
                event_id=self.event.pk,
                photo_id=photo.pk,
                purpose=GalleryMediaPurpose.ORIGINAL_DOWNLOAD,
            )

        self.assertEqual(selected, photo)
        self.assertEqual(len(queries), 1)
        self.assert_no_processing_relations(queries)

    def test_saved_results_use_projection_eligibility_without_processing_relations(self) -> None:
        photo = self.photo("saved-result-legacy")
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest="a" * 64,
            temporary_object_key="",
            configuration={},
            configuration_hash="b" * 64,
            status=SelfieSearch.Status.READY,
            eligible_photo_count=1,
            matched_photo_count=1,
        )
        SelfieSearchResult.objects.create(search=search, photo=photo, rank=1)

        with CaptureQueriesContext(connection) as queries:
            selected = saved_ready_result_photos(search)

        self.assertEqual(selected, (photo,))
        self.assert_no_processing_relations(queries)

    def test_gallery_search_submission_uses_projection_eligibility_without_processing_relations(
        self,
    ) -> None:
        photo = self.photo("gallery-search-source")
        with (
            patch("selfie_search.services.submission._gallery_source_candidate"),
            CaptureQueriesContext(connection) as queries,
        ):
            created = submit_gallery_photo_search(
                event=self.event,
                photo=photo,
                detection_id=uuid4(),
                user=self.user,
            )

        self.assertEqual(created.search.event, self.event)
        self.assert_no_processing_relations(queries)

    def test_cart_validation_uses_projection_eligibility_without_processing_relations(self) -> None:
        photo = self.projected_photo(
            "cart-projected",
            event=self.paid_event,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            variant="preview-watermarked-v1",
        )

        with CaptureQueriesContext(connection) as queries:
            result = set_photo_selected(
                event=self.paid_event,
                photo_id=photo.pk,
                selected=True,
                browser_token=None,
                watermarked_previews_enabled=True,
            )

        self.assertTrue(result.selected)
        self.assert_no_processing_relations(queries)

    def test_checkout_eligibility_uses_projection_without_processing_relations(self) -> None:
        token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"
        missing_slot = self.photo(
            "checkout-missing-slot",
            event=self.paid_event,
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        cart = Cart.objects.create(
            browser_token_sha256=browser_token_sha256(token),
            event=self.paid_event,
            expires_at=timezone.now() + timedelta(days=1),
        )
        CartItem.objects.create(cart=cart, photo=missing_slot)
        gateway = Mock(adapter_key="query-shape")

        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(CheckoutEmptyCart):
                create_checkout(
                    event=self.paid_event,
                    cart_browser_token=token,
                    purchase_browser_token=None,
                    checkout_email="query@example.test",
                    watermarked_previews_enabled=True,
                    purchase_enabled=True,
                    adapter_key="query-shape",
                    gateway=gateway,
                    return_url_for_order=lambda public_number: f"/{public_number}/",
                )

        self.assert_no_processing_relations(queries)

    def test_projection_key_selection_performs_no_database_query(self) -> None:
        photo = Photo(
            id="projected-clean",
            original_key="originals/projected-clean",
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )
        photo.gallery_media_projection = GalleryMediaProjection(
            photo=photo,
            clean_preview_final_key="derivatives/previews/projected-clean/clean.jpg",
        )
        storage = _SigningStorage()

        with self.assertNumQueries(0):
            url = PublicMediaResolver(storage).resolve_signed(
                photo=photo,
                variant="preview-small",
            )

        self.assertEqual(url, "https://storage.example.test/signed")
        self.assertEqual(
            storage.signed_keys,
            ["derivatives/previews/projected-clean/clean.jpg"],
        )

    def test_uncached_projection_is_rejected_without_database_query(self) -> None:
        photo = self.photo(
            "uncached-clean",
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.PREVIEW_REQUIRED,
        )

        with self.assertNumQueries(0), self.assertRaises(ObjectMissing):
            PublicMediaResolver(_SigningStorage()).resolve_signed(
                photo=photo,
                variant="preview-small",
            )


class _SigningStorage:
    def __init__(self) -> None:
        self.signed_keys: list[str] = []

    def sign_final(self, *, key: str, attachment_filename: str | None = None) -> str:
        self.signed_keys.append(key)
        return "https://storage.example.test/signed"

    def open_final(self, *, key: str) -> OpenedObject:  # pragma: no cover
        raise AssertionError(f"unexpected open for {key}")

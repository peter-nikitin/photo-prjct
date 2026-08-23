import hashlib
from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.registry import (
    PAID_EVENTS,
    PAID_PHOTO_CART,
    PAID_WATERMARKED_PREVIEWS,
    FeatureDefinition,
)
from feature_flags.states import FEATURE_FLAG_STAFF, FeatureFlagState
from feature_flags.testing import override_feature_flags
from picflow.models import Event, Photo
from processing.models import (
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)
from selfie_search.models import SelfieSearch, SelfieSearchResult

from commerce.identity import browser_token_sha256
from commerce.models import Cart, CartItem


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
class PaidPhotoCartCriticalPathTests(TestCase):
    """Auditable staff path across catalog, saved results, cart, and protected media."""

    token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"

    def setUp(self) -> None:
        self.feature_flag_states: dict[FeatureDefinition, FeatureFlagState] = {}
        self.enterContext(override_feature_flags(self.feature_flag_states))
        self.staff = get_user_model().objects.create_user(username="cart-flow-staff", is_staff=True)
        self.photographer = get_user_model().objects.create_user(username="cart-flow-photographer")
        self.event = self.make_paid_event(name="Cart flow", slug="cart-flow")
        self.photo = self.make_watermarked_photo(event=self.event, photo_id="cart-flow-photo")
        self.enable_staff_gates()
        self.client.force_login(self.staff)

    def make_paid_event(self, *, name: str, slug: str) -> Event:
        return Event.objects.create(
            name=name,
            slug=slug,
            start_date=date(2026, 8, 20),
            end_date=date(2026, 8, 20),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )

    def make_watermarked_photo(self, *, event: Event, photo_id: str) -> Photo:
        photo = Photo.objects.create(
            id=photo_id,
            event=event,
            uploaded_by=self.photographer,
            original_key=f"private/{photo_id}",
            original_filename=f"{photo_id}.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
            processing_generation=Photo.ProcessingGeneration.PREVIEW_FIRST_WATERMARKED_V1,
            gallery_media_policy=Photo.GalleryMediaPolicy.WATERMARKED_PREVIEW_REQUIRED,
        )
        configuration = {
            GENERATE_WATERMARKED_PREVIEW_PROCESSOR: {"variant": "preview-watermarked-v1"}
        }
        run = EventProcessingRun.objects.create(
            event=event,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            configuration_hash=(photo_id.encode().hex() + "0" * 64)[:64],
        )
        job = ProcessingJob.objects.create(
            event=event,
            run=run,
            photo=photo,
            contract_version=2,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
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
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            processor_version=1,
            configuration=configuration,
            input_fingerprint={},
            status=ProcessingAttempt.Status.SUCCEEDED,
            terminal_at=timezone.now(),
            accepted=True,
        )
        PhotoProcessingState.objects.create(
            photo=photo,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
            status=PhotoProcessingState.Status.SUCCEEDED,
            current_run=run,
            current_job=job,
            current_attempt=attempt,
            accepted_attempt=attempt,
            succeeded_at=timezone.now(),
        )
        PhotoDerivative.objects.create(
            photo=photo,
            variant="preview-watermarked-v1",
            final_key=f"private/watermarked/{photo_id}.jpg",
            byte_size=10,
            content_type="image/jpeg",
            width=10,
            height=10,
            oriented_source_width=10,
            oriented_source_height=10,
            sha256="a" * 64,
            accepted_attempt=attempt,
        )
        return photo

    def enable_staff_gates(self) -> None:
        self.feature_flag_states.update(
            {
                PAID_EVENTS: FEATURE_FLAG_STAFF,
                PAID_PHOTO_CART: FEATURE_FLAG_STAFF,
                PAID_WATERMARKED_PREVIEWS: FEATURE_FLAG_STAFF,
            }
        )

    def cart_url(self, event: Event | None = None) -> str:
        return reverse("commerce:detail", kwargs={"event_slug": (event or self.event).slug})

    def selection_url(self, event: Event | None = None) -> str:
        return reverse(
            "commerce:set_photo_state", kwargs={"event_slug": (event or self.event).slug}
        )

    def select(self, *, event: Event, photo: Photo, selected: bool) -> None:
        response = self.client.post(
            self.selection_url(event),
            {"photo_id": photo.pk, "selected": "1" if selected else "0"},
        )
        self.assertEqual(response.status_code, 302)

    def saved_result_url(self, *, token: str) -> str:
        return reverse(
            "selfie_search:result",
            kwargs={"event_slug": self.event.slug, "public_token": token},
        )

    def test_staff_can_select_across_paid_gallery_saved_result_and_cart_without_original_access(
        self,
    ) -> None:
        gallery = self.client.get(reverse("event_detail", kwargs={"slug": self.event.slug}))
        self.assertEqual(gallery.status_code, 200)
        self.assertContains(gallery, 'data-cart-form data-photo-id="cart-flow-photo"')
        self.assertContains(gallery, "Добавить в корзину")

        self.select(event=self.event, photo=self.photo, selected=True)
        token = self.client.cookies["findme_cart"].value
        self.assertEqual(Cart.objects.get().browser_token_sha256, browser_token_sha256(token))

        search_token = "paid-cart-flow-result"
        search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest=hashlib.sha256(search_token.encode()).hexdigest(),
            status=SelfieSearch.Status.READY,
            temporary_object_key="",
            configuration={"public-contract": 1},
            eligible_photo_count=1,
            matched_photo_count=1,
        )
        SelfieSearchResult.objects.create(search=search, photo=self.photo, rank=1)
        saved_result = self.client.get(self.saved_result_url(token=search_token))
        self.assertEqual(saved_result.status_code, 200)
        self.assertContains(saved_result, "Удалить из корзины")
        self.assertContains(saved_result, 'aria-pressed="true"')

        reloaded_gallery = self.client.get(
            reverse("event_detail", kwargs={"slug": self.event.slug})
        )
        cart = self.client.get(self.cart_url())
        self.assertContains(reloaded_gallery, "Корзина")
        self.assertContains(cart, "Фотографий: 1")
        self.assertContains(cart, "Итого: 300 ₽")

        with (
            patch("config.views._public_media_resolver") as gallery_resolver,
            patch("selfie_search.views._public_media_resolver") as result_resolver,
        ):
            gallery_download = self.client.get(
                reverse(
                    "photo_download",
                    kwargs={"slug": self.event.slug, "photo_id": self.photo.pk},
                )
            )
            result_download = self.client.get(
                reverse(
                    "selfie_search:result_download",
                    kwargs={
                        "event_slug": self.event.slug,
                        "public_token": search_token,
                        "photo_id": self.photo.pk,
                    },
                )
            )
        self.assertEqual(gallery_download.status_code, 404)
        self.assertEqual(result_download.status_code, 404)
        gallery_resolver.assert_not_called()
        result_resolver.assert_not_called()

        self.select(event=self.event, photo=self.photo, selected=False)
        self.assertFalse(Cart.objects.exists())
        self.select(event=self.event, photo=self.photo, selected=True)
        cleared = self.client.post(
            reverse("commerce:clear", kwargs={"event_slug": self.event.slug})
        )
        self.assertEqual(cleared.status_code, 302)
        self.assertFalse(Cart.objects.exists())

    def test_selection_keeps_browser_and_event_isolation_and_uses_the_current_price(self) -> None:
        self.select(event=self.event, photo=self.photo, selected=True)
        other_browser = Client()
        other_browser.force_login(self.staff)
        self.assertContains(other_browser.get(self.cart_url()), "В корзине пока нет фотографий")

        other_event = self.make_paid_event(name="Other cart flow", slug="other-cart-flow")
        other_photo = self.make_watermarked_photo(
            event=other_event,
            photo_id="other-cart-flow-photo",
        )
        self.select(event=other_event, photo=other_photo, selected=True)
        self.assertContains(self.client.get(self.cart_url()), "Фотографий: 1")
        self.assertContains(self.client.get(self.cart_url(other_event)), "Фотографий: 1")

        self.event.price_per_photo_kopecks = 45000
        self.event.save(update_fields=["price_per_photo_kopecks"])
        priced = self.client.get(self.cart_url())
        self.assertContains(priced, "Итого: 450 ₽")

    def test_reads_prune_and_expire_without_changing_free_or_legacy_paid(
        self,
    ) -> None:
        self.client.cookies["findme_cart"] = self.token
        cart = Cart.objects.create(
            browser_token_sha256=browser_token_sha256(self.token),
            event=self.event,
            expires_at=timezone.now() + timedelta(days=1),
        )
        CartItem.objects.create(cart=cart, photo=self.photo)
        PhotoProcessingState.objects.filter(photo=self.photo).update(
            status=PhotoProcessingState.Status.FAILED,
            failed_at=timezone.now(),
        )
        pruned = self.client.get(self.cart_url())
        self.assertContains(pruned, "Некоторые фотографии больше недоступны и удалены из корзины")
        self.assertFalse(Cart.objects.filter(pk=cart.pk).exists())

        valid = self.make_watermarked_photo(event=self.event, photo_id="expired-cart-flow-photo")
        expired = Cart.objects.create(
            browser_token_sha256=browser_token_sha256(self.token),
            event=self.event,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        CartItem.objects.create(cart=expired, photo=valid)
        self.client.cookies["findme_cart"] = self.token
        expired_response = self.client.get(self.cart_url())
        self.assertContains(expired_response, "В корзине пока нет фотографий")
        self.assertNotContains(expired_response, valid.pk)
        self.assertNotContains(expired_response, "Итого: 300 ₽")
        self.assertTrue(Cart.objects.filter(pk=expired.pk).exists())

        free = Event.objects.create(
            name="Free cart flow",
            slug="free-cart-flow",
            start_date=date(2026, 8, 20),
            end_date=date(2026, 8, 20),
            city="Moscow",
            publication_status=Event.PublicationStatus.PUBLISHED,
            access_type=Event.AccessType.FREE,
        )
        free_photo = Photo.objects.create(
            id="free-cart-flow-photo",
            event=free,
            uploaded_by=self.photographer,
            original_key="private/free-cart-flow-photo",
            original_filename="free-cart-flow-photo.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        free_page = self.client.get(reverse("event_detail", kwargs={"slug": free.slug}))
        self.assertNotContains(free_page, "data-cart-form")

        legacy = Photo.objects.create(
            id="legacy-cart-flow-photo",
            event=self.event,
            uploaded_by=self.photographer,
            original_key="private/legacy-cart-flow-photo",
            original_filename="legacy-cart-flow-photo.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        search_token = "legacy-cart-flow-result"
        legacy_search = SelfieSearch.objects.create(
            event=self.event,
            public_token_digest=hashlib.sha256(search_token.encode()).hexdigest(),
            status=SelfieSearch.Status.READY,
            temporary_object_key="",
            configuration={"public-contract": 1},
            eligible_photo_count=1,
            matched_photo_count=1,
        )
        SelfieSearchResult.objects.create(search=legacy_search, photo=legacy, rank=1)
        legacy_page = self.client.get(self.saved_result_url(token=search_token))
        self.assertContains(legacy_page, "gallery-lightbox-download")
        self.assertNotContains(legacy_page, "data-cart-form")
        self.assertEqual(free_photo.event_id, free.pk)

from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.views.debug import technical_500_response
from feature_flags.models import FeatureFlag
from picflow.models import Event, Photo
from picflow.photo_policy import PAID_WATERMARKED_PREVIEWS_FLAG
from processing.models import (
    GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
    EventProcessingRun,
    PhotoDerivative,
    PhotoProcessingState,
    ProcessingAttempt,
    ProcessingJob,
)

from commerce.identity import browser_token_sha256
from commerce.models import Cart, CartItem

PAID_PHOTO_CART_FLAG = "paid-photo-cart"


@override_settings(
    STORAGES={"staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
)
class CartViewTests(TestCase):
    """These tests protect the anonymous bearer, authority, CSRF, and private-cache boundary."""

    token = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8"

    def setUp(self) -> None:
        self.photographer = get_user_model().objects.create_user(username="cart-photographer")
        self.event = self.make_event(name="Paid race", slug="paid-race")
        self.photo = self.make_watermarked_photo(self.event, photo_id="photo-one")

    def make_event(self, *, name: str, slug: str, **overrides: object) -> Event:
        values: dict[str, object] = {
            "name": name,
            "slug": slug,
            "start_date": date(2026, 8, 20),
            "end_date": date(2026, 8, 20),
            "city": "Moscow",
            "publication_status": Event.PublicationStatus.PUBLISHED,
            "access_type": Event.AccessType.PAID,
            "price_per_photo_kopecks": 30000,
        }
        values.update(overrides)
        return Event.objects.create(**values)

    def make_watermarked_photo(self, event: Event, *, photo_id: str) -> Photo:
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
            configuration_hash=(photo_id.encode("utf-8").hex() + "0" * 64)[:64],
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
        state, _ = PhotoProcessingState.objects.get_or_create(
            photo=photo,
            processor_type=GENERATE_WATERMARKED_PREVIEW_PROCESSOR,
        )
        state.status = PhotoProcessingState.Status.SUCCEEDED
        state.current_run = run
        state.current_job = job
        state.current_attempt = attempt
        state.accepted_attempt = attempt
        state.succeeded_at = timezone.now()
        state.save()
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

    def enable(self, *, cart: str = FeatureFlag.State.ON, watermark: str = FeatureFlag.State.ON):
        FeatureFlag.objects.update_or_create(
            key=PAID_PHOTO_CART_FLAG,
            defaults={"description": "Paid photo cart", "state": cart},
        )
        FeatureFlag.objects.update_or_create(
            key=PAID_WATERMARKED_PREVIEWS_FLAG,
            defaults={"description": "Paid watermarked previews", "state": watermark},
        )

    def detail_url(self, event: Event | None = None) -> str:
        return reverse("commerce:detail", kwargs={"event_slug": (event or self.event).slug})

    def set_url(self, event: Event | None = None) -> str:
        return reverse(
            "commerce:set_photo_state",
            kwargs={
                "event_slug": (event or self.event).slug,
            },
        )

    def selection_data(
        self,
        selected: str,
        *,
        photo: Photo | None = None,
        **extra: str,
    ) -> dict[str, str]:
        return {
            "photo_id": (photo or self.photo).pk,
            "selected": selected,
            **extra,
        }

    def clear_url(self, event: Event | None = None) -> str:
        return reverse("commerce:clear", kwargs={"event_slug": (event or self.event).slug})

    def assert_private(self, response) -> None:
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Vary"], "Cookie")

    def test_missing_off_and_sibling_gate_denials_are_sanitized_and_side_effect_free(self) -> None:
        urls = (self.detail_url(), self.set_url(), self.clear_url())

        missing = (
            self.client.get(urls[0]),
            self.client.post(urls[1], self.selection_data("1")),
            self.client.post(urls[2]),
        )
        self.enable(cart=FeatureFlag.State.OFF)
        off = (
            self.client.get(urls[0]),
            self.client.post(urls[1], self.selection_data("1")),
            self.client.post(urls[2]),
        )
        self.enable(cart=FeatureFlag.State.ON, watermark=FeatureFlag.State.OFF)
        sibling_off = self.client.get(urls[0])

        for response in (*missing, *off, sibling_off):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.content, b"")
            self.assertEqual(response.cookies, {})
            self.assert_private(response)
        self.assertEqual(Cart.objects.count(), 0)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_staff_mode_permits_only_active_staff_and_on_permits_anonymous(self) -> None:
        self.enable(cart=FeatureFlag.State.STAFF, watermark=FeatureFlag.State.STAFF)
        anonymous = self.client.get(self.detail_url())
        staff = get_user_model().objects.create_user(username="cart-staff", is_staff=True)
        self.client.force_login(staff)
        staff_response = self.client.get(self.detail_url())
        self.client.logout()
        FeatureFlag.objects.filter(key=PAID_PHOTO_CART_FLAG).update(state=FeatureFlag.State.ON)
        FeatureFlag.objects.filter(key=PAID_WATERMARKED_PREVIEWS_FLAG).update(
            state=FeatureFlag.State.ON
        )
        public_response = self.client.get(self.detail_url())

        self.assertEqual(anonymous.status_code, 404)
        self.assertEqual(staff_response.status_code, 200)
        self.assertEqual(public_response.status_code, 200)
        self.assertEqual(Cart.objects.count(), 0)
        self.assertEqual(self.client.cookies.get("findme_cart"), None)

    def test_disabled_gate_preserves_an_existing_cart_and_browser_cookie(self) -> None:
        self.enable(cart=FeatureFlag.State.OFF)
        cart = Cart.objects.create(
            browser_token_sha256=browser_token_sha256(self.token),
            event=self.event,
            expires_at=timezone.now() + timedelta(days=30),
        )
        CartItem.objects.create(cart=cart, photo=self.photo)
        self.client.cookies["findme_cart"] = self.token

        responses = (
            self.client.get(self.detail_url()),
            self.client.post(self.set_url(), self.selection_data("0")),
            self.client.post(self.clear_url()),
        )

        self.assertTrue(Cart.objects.filter(pk=cart.pk).exists())
        self.assertTrue(CartItem.objects.filter(cart=cart, photo=self.photo).exists())
        self.assertEqual(self.client.cookies["findme_cart"].value, self.token)
        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.cookies, {})

    def test_malformed_cookie_is_treated_as_absent_without_replacement_on_reads_or_no_ops(
        self,
    ) -> None:
        self.enable()
        self.client.cookies["findme_cart"] = "not-a-valid-browser-token"

        read_response = self.client.get(self.detail_url())
        no_op_response = self.client.post(self.set_url(), self.selection_data("0"))

        self.assertEqual(read_response.status_code, 200)
        self.assertEqual(no_op_response.status_code, 302)
        self.assertEqual(read_response.cookies, {})
        self.assertEqual(no_op_response.cookies, {})
        self.assertEqual(Cart.objects.count(), 0)
        self.assertEqual(CartItem.objects.count(), 0)

    @override_settings(DEBUG=False)
    def test_detail_exception_report_redacts_cart_bearer_and_selection_only(self) -> None:
        self.enable()
        cart = Cart.objects.create(
            browser_token_sha256=browser_token_sha256(self.token),
            event=self.event,
            expires_at=timezone.now() + timedelta(days=30),
        )
        CartItem.objects.create(cart=cart, photo=self.photo)
        exception_client = Client(raise_request_exception=False)
        exception_client.cookies["findme_cart"] = self.token
        exception_client.cookies["ordinary_cookie"] = "visible-cookie-value"

        with patch("commerce.views.render", side_effect=RuntimeError("forced cart detail")):
            response = exception_client.get(self.detail_url())

        report = self.technical_report(response)
        self.assertNotIn(self.token, report)
        self.assertNotIn(self.photo.pk, report)
        self.assertIn("ordinary_cookie", report)
        self.assertIn("visible-cookie-value", report)
        self.assertIn("callback_kwargs", report)
        self.assertIn("event_slug", report)
        self.assertIn("paid-race", report)
        self.assertIn("forced cart detail", report)

    @override_settings(DEBUG=False)
    def test_mutation_exception_report_redacts_post_bearers_and_cart_frames_only(self) -> None:
        self.enable()
        selfie_bearer = f"return-bearer-{uuid4().hex}"
        exception_client = Client(raise_request_exception=False)
        exception_client.cookies["findme_cart"] = self.token

        with patch(
            "commerce.views._apply_mutation_cookie",
            side_effect=RuntimeError("forced cart mutation"),
        ):
            response = exception_client.post(
                self.set_url(),
                self.selection_data(
                    "1",
                    return_to=reverse(
                        "selfie_search:result",
                        kwargs={
                            "event_slug": self.event.slug,
                            "public_token": selfie_bearer,
                        },
                    ),
                    ordinary_field="visible-post-value",
                ),
            )

        report = self.technical_report(response)
        self.assertNotIn(self.token, report)
        self.assertNotIn(self.photo.pk, report)
        self.assertNotIn(selfie_bearer, report)
        self.assertIn("ordinary_field", report)
        self.assertIn("visible-post-value", report)
        self.assertIn("callback_kwargs", report)
        self.assertIn("event_slug", report)
        self.assertIn("paid-race", report)
        self.assertIn("forced cart mutation", report)

    def technical_report(self, response) -> str:
        self.assertEqual(response.status_code, 500)
        self.assertIsNotNone(response.exc_info)
        technical_response = technical_500_response(
            response.wsgi_request,
            *response.exc_info,
        )
        return technical_response.content.decode(technical_response.charset)

    def test_free_draft_foreign_and_ineligible_photo_commands_create_no_state_or_cookie(
        self,
    ) -> None:
        self.enable()
        free = self.make_event(
            name="Free",
            slug="free",
            access_type=Event.AccessType.FREE,
            price_per_photo_kopecks=None,
        )
        draft = self.make_event(
            name="Draft",
            slug="draft",
            publication_status=Event.PublicationStatus.DRAFT,
        )
        foreign = self.make_watermarked_photo(
            self.make_event(name="Other", slug="other"),
            photo_id="foreign-photo",
        )
        legacy = Photo.objects.create(
            id="legacy-paid",
            event=self.event,
            uploaded_by=self.photographer,
            original_key="private/legacy",
            original_filename="legacy.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        responses = (
            self.client.get(self.detail_url(free)),
            self.client.get(self.detail_url(draft)),
            self.client.post(self.set_url(), self.selection_data("1", photo=foreign)),
            self.client.post(self.set_url(), self.selection_data("1", photo=legacy)),
            self.client.post(self.set_url(), self.selection_data("0", photo=legacy)),
            self.client.post(self.set_url(), self.selection_data("yes")),
        )

        for response in responses:
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.content, b"")
            self.assertEqual(response.cookies, {})
        self.assertEqual(Cart.objects.count(), 0)
        self.assertEqual(CartItem.objects.count(), 0)

    def test_mutation_requires_csrf_and_an_explicit_selected_state(self) -> None:
        self.enable()
        csrf_client = Client(enforce_csrf_checks=True)

        self.assertNotIn(self.photo.pk, self.set_url())
        missing_csrf = csrf_client.post(self.set_url(), self.selection_data("1"))
        clear_missing_csrf = csrf_client.post(self.clear_url())
        missing_state = self.client.post(self.set_url())

        self.assertEqual(missing_csrf.status_code, 403)
        self.assertEqual(clear_missing_csrf.status_code, 403)
        self.assertEqual(missing_state.status_code, 404)
        self.assertEqual(Cart.objects.count(), 0)

    @patch("commerce.services.generate_browser_token", return_value=token)
    def test_first_actual_add_sets_the_exact_private_cookie_and_authoritative_json(
        self, _generate_browser_token
    ) -> None:
        self.enable()

        response = self.client.post(
            self.set_url(),
            self.selection_data(
                "1",
                return_to="https://attacker.example/cart-token",
            ),
            HTTP_ACCEPT="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "photo_id": self.photo.pk,
                "selected": True,
                "item_count": 1,
                "unit_price_kopecks": 30000,
                "unit_price_display": "300 ₽",
                "total_kopecks": 30000,
                "total_display": "300 ₽",
            },
        )
        cookie = response.cookies["findme_cart"]
        self.assertEqual(cookie.value, self.token)
        self.assertEqual(cookie["max-age"], 30 * 24 * 60 * 60)
        self.assertTrue(cookie["expires"])
        self.assertEqual(cookie["path"], "/")
        self.assertTrue(cookie["secure"])
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.assertNotIn(self.token, response.content.decode())
        self.assert_private(response)

    def test_idempotent_retries_return_state_without_refreshing_the_cookie_or_expiry(self) -> None:
        self.enable()
        self.client.cookies["findme_cart"] = self.token
        first = self.client.post(
            self.set_url(), self.selection_data("1"), HTTP_ACCEPT="application/json"
        )
        expiry = Cart.objects.get().expires_at
        duplicate = self.client.post(
            self.set_url(), self.selection_data("1"), HTTP_ACCEPT="application/json"
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        self.assertTrue(duplicate.json()["selected"])
        self.assertEqual(duplicate.json()["item_count"], 1)
        self.assertEqual(duplicate.cookies, {})
        self.assertEqual(Cart.objects.get().expires_at, expiry)

    def test_safe_local_return_is_preserved_and_open_or_token_reflecting_returns_fall_back(
        self,
    ) -> None:
        self.enable()
        safe_path = f"{self.detail_url()}?from=gallery"
        safe = self.client.post(self.set_url(), self.selection_data("1", return_to=safe_path))
        open_redirect = self.client.post(
            self.set_url(),
            self.selection_data("0", return_to="//attacker.example/steal"),
        )
        unapproved_local = self.client.post(
            self.set_url(),
            self.selection_data("0", return_to=reverse("legal")),
        )
        browser_normalized = self.client.post(
            self.set_url(),
            self.selection_data("0", return_to="/\\attacker.example/steal"),
        )
        control_character = self.client.post(
            self.set_url(),
            self.selection_data(
                "0",
                return_to="/events/paid-race/\r\nX-Leak: value",
            ),
        )
        self.client.cookies["findme_cart"] = self.token
        reflected = self.client.post(
            self.set_url(),
            self.selection_data("1", return_to=f"/events/?value={self.token}"),
        )

        event_fallback = reverse("event_detail", kwargs={"slug": self.event.slug})
        self.assertRedirects(safe, safe_path, fetch_redirect_response=False)
        self.assertRedirects(open_redirect, event_fallback, fetch_redirect_response=False)
        self.assertRedirects(unapproved_local, event_fallback, fetch_redirect_response=False)
        self.assertRedirects(browser_normalized, event_fallback, fetch_redirect_response=False)
        self.assertRedirects(control_character, event_fallback, fetch_redirect_response=False)
        self.assertRedirects(reflected, event_fallback, fetch_redirect_response=False)
        self.assertNotIn(self.token, reflected["Location"])

    def test_final_cart_deletion_expires_cookie_only_after_other_event_cart_is_removed(
        self,
    ) -> None:
        self.enable()
        other_event = self.make_event(name="Other race", slug="other-race")
        other_photo = self.make_watermarked_photo(other_event, photo_id="other-photo")
        self.client.cookies["findme_cart"] = self.token
        self.client.post(self.set_url(), self.selection_data("1"))
        self.client.post(
            self.set_url(other_event),
            self.selection_data("1", photo=other_photo),
        )

        first_removal = self.client.post(self.set_url(), self.selection_data("0"))
        final_removal = self.client.post(
            self.set_url(other_event),
            self.selection_data("0", photo=other_photo),
        )

        self.assertEqual(first_removal.cookies["findme_cart"].value, self.token)
        self.assertEqual(first_removal.cookies["findme_cart"]["max-age"], 30 * 24 * 60 * 60)
        self.assertEqual(final_removal.cookies["findme_cart"]["max-age"], 0)
        self.assertEqual(final_removal.cookies["findme_cart"]["path"], "/")
        self.assertTrue(final_removal.cookies["findme_cart"]["secure"])
        self.assertTrue(final_removal.cookies["findme_cart"]["httponly"])
        self.assertEqual(final_removal.cookies["findme_cart"]["samesite"], "Lax")
        self.assertEqual(Cart.objects.count(), 0)

    def test_clear_is_event_scoped_and_returns_an_authoritative_empty_json_snapshot(self) -> None:
        self.enable()
        other_event = self.make_event(name="Other race", slug="clear-other")
        other_photo = self.make_watermarked_photo(other_event, photo_id="clear-other-photo")
        self.client.cookies["findme_cart"] = self.token
        self.client.post(self.set_url(), self.selection_data("1"))
        self.client.post(
            self.set_url(other_event),
            self.selection_data("1", photo=other_photo),
        )

        response = self.client.post(self.clear_url(), HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "selected": False,
                "item_count": 0,
                "unit_price_kopecks": 30000,
                "unit_price_display": "300 ₽",
                "total_kopecks": 0,
                "total_display": "0 ₽",
            },
        )
        self.assertFalse(Cart.objects.filter(event=self.event).exists())
        self.assertTrue(Cart.objects.filter(event=other_event).exists())
        self.assertEqual(response.cookies["findme_cart"].value, self.token)

    def test_cart_page_renders_current_items_in_addition_order_without_private_identifiers(
        self,
    ) -> None:
        self.enable()
        second = self.make_watermarked_photo(self.event, photo_id="photo-two")
        cart = Cart.objects.create(
            browser_token_sha256=browser_token_sha256(self.token),
            event=self.event,
            expires_at=timezone.now() + timedelta(days=30),
        )
        now = timezone.now()
        CartItem.objects.create(cart=cart, photo=second, added_at=now)
        CartItem.objects.create(cart=cart, photo=self.photo, added_at=now + timedelta(seconds=1))
        self.client.cookies["findme_cart"] = self.token

        response = self.client.get(self.detail_url())

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "commerce/cart.html")
        self.assertContains(response, self.event.name)
        self.assertContains(response, "300 ₽", count=2)
        self.assertContains(response, "Фотографий: 2")
        self.assertContains(response, "Итого: 600 ₽")
        self.assertContains(response, "Продолжить выбор")
        self.assertContains(response, "Очистить корзину")
        self.assertContains(response, "Удалить все фотографии из корзины?")
        self.assertContains(response, 'name="selected" value="0"', count=2)
        self.assertContains(response, 'data-cart-form data-photo-id="photo-two"')
        self.assertContains(response, 'data-cart-form data-photo-id="photo-one"')
        self.assertContains(
            response, 'data-cart-count data-cart-count-label="Фотографий: ">Фотографий: 2'
        )
        self.assertContains(
            response, 'data-cart-total data-cart-total-label="Итого: ">Итого: 600 ₽'
        )
        self.assertContains(response, "data-cart-error", count=1)
        self.assertNotContains(response, "mc.yandex.ru")
        self.assertIsNone(response.context["yandex_metrika_counter_id"])
        body = response.content.decode(response.charset)
        self.assertLess(body.index("photo-two"), body.index("photo-one"))
        for secret in (
            second.original_filename,
            second.original_key,
            self.photo.original_filename,
            self.photo.original_key,
            self.token,
        ):
            self.assertNotIn(secret, body)
        for forbidden in (
            "quantity",
            "package",
            "checkout",
            "payment",
            "download",
            "количество",
            "пакет",
            "оплата",
            "скачать",
            "оригинал",
        ):
            self.assertNotIn(forbidden, body.lower())
        self.assert_private(response)

    def test_empty_and_pruned_cart_page_uses_the_exact_customer_copy(self) -> None:
        self.enable()
        empty = self.client.get(self.detail_url())
        legacy = Photo.objects.create(
            id="pruned-legacy",
            event=self.event,
            uploaded_by=self.photographer,
            original_key="private/pruned-legacy",
            original_filename="pruned-legacy.jpg",
            original_size=10,
            original_content_type="image/jpeg",
            uploaded_at=timezone.now(),
        )
        cart = Cart.objects.create(
            browser_token_sha256=browser_token_sha256(self.token),
            event=self.event,
            expires_at=timezone.now() + timedelta(days=30),
        )
        CartItem.objects.create(cart=cart, photo=legacy)
        self.client.cookies["findme_cart"] = self.token

        pruned = self.client.get(self.detail_url())

        self.assertContains(empty, "В корзине пока нет фотографий")
        self.assertContains(pruned, "В корзине пока нет фотографий")
        self.assertContains(
            pruned,
            "Некоторые фотографии больше недоступны и удалены из корзины",
        )
        self.assertFalse(Cart.objects.filter(pk=cart.pk).exists())
        self.assertEqual(pruned.cookies["findme_cart"]["max-age"], 0)

from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.views.debug import technical_500_response
from feature_flags.states import FEATURE_FLAG_ON
from ingestion.storage import ObjectMissing
from selfie_search.middleware import PublicSelfieBearerProtectionMiddleware

from commerce.capabilities import create_order_access_grant, sign_order_access_grant
from commerce.delivery import ResendOrderAccessRateLimited
from commerce.identity import browser_token_sha256, generate_browser_token
from commerce.models import Order, OrderItem
from commerce.tests.test_checkout_views import CheckoutViewTestCase


class OrderViewFixture(CheckoutViewTestCase):
    def make_order(
        self,
        *,
        status: str = "paid",
        public_number: str = "FM-ABCDEFGH",
        total_kopecks: int = 30000,
    ) -> Order:
        order = Order.objects.create(
            public_number=public_number,
            event=self.event,
            originating_cart_token_sha256=browser_token_sha256(self.cart_token),
            purchase_browser_token_sha256=browser_token_sha256(self.cart_token),
            checkout_email="buyer@example.test",
            delivery_email="buyer@example.test",
            total_kopecks=total_kopecks,
            currency="RUB",
            status=status,
            paid_at=timezone.now() if status == "paid" else None,
        )
        OrderItem.objects.create(
            order=order,
            photo=self.photo,
            photo_public_id=self.photo.pk,
            unit_price_kopecks=30000,
            quantity=1,
            line_total_kopecks=30000,
        )
        self.client.cookies["findme_purchase"] = self.cart_token
        return order

    def order_url(self, order: Order) -> str:
        return reverse("commerce:order", kwargs={"public_number": order.public_number})

    def return_url(self, order: Order) -> str:
        return reverse("commerce:order_return", kwargs={"public_number": order.public_number})

    def status_url(self, order: Order) -> str:
        return reverse("commerce:order_status", kwargs={"public_number": order.public_number})

    def media_url(self, order: Order, photo_id: str, variant: str = "preview-small") -> str:
        return reverse(
            "commerce:order_media",
            kwargs={
                "public_number": order.public_number,
                "photo_id": photo_id,
                "variant": variant,
            },
        )

    def grant_url(self, order: Order, grant, signature: str) -> str:
        return reverse(
            "commerce:grant_order",
            kwargs={
                "public_number": order.public_number,
                "grant_identifier": grant.pk,
                "signature": signature,
            },
        )

    def add_order_photos(self, order: Order, *, count: int) -> tuple[str, ...]:
        photo_ids = tuple(f"zz-order-photo-{number:03d}" for number in range(count))
        photos = [
            self.photo.__class__.objects.create(
                id=photo_id,
                event=self.event,
                src="",
                original_key=f"private/{photo_id}.jpg",
                original_filename=f"{photo_id}.jpg",
                original_size=10,
                original_content_type="image/jpeg",
                uploaded_at=timezone.now(),
                uploaded_by=self.photographer,
                processing_generation=self.photo.processing_generation,
                gallery_media_policy=self.photo.gallery_media_policy,
            )
            for photo_id in photo_ids
        ]
        OrderItem.objects.bulk_create(
            OrderItem(
                order=order,
                photo=photo,
                photo_public_id=photo.pk,
                unit_price_kopecks=30000,
                quantity=1,
                line_total_kopecks=30000,
            )
            for photo in photos
        )
        return photo_ids


@override_settings(
    COMMERCE_ORDER_ACCESS_SIGNING_SECRET="order-view-test-secret",
    COMMERCE_SUPPORT_CONTACT="support@example.test",
)
class OrderViewTests(OrderViewFixture):
    def test_pending_browser_page_two_polling_reloads_the_same_validated_page(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order(status="pending", total_kopecks=102 * 30000)
        self.add_order_photos(order, count=101)

        response = self.client.get(self.order_url(order), {"page": "2"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'data-order-url="{self.order_url(order)}?page=2"',
        )

    def test_pending_grant_page_two_polling_reloads_the_same_signed_page(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order(status="pending", total_kopecks=102 * 30000)
        self.add_order_photos(order, count=101)
        grant = create_order_access_grant(order=order, source="checkout")
        signature = sign_order_access_grant(
            grant=grant,
            signing_secret="order-view-test-secret",
        )
        grant_url = self.grant_url(order, grant, signature)
        self.client.cookies.pop("findme_purchase", None)

        response = self.client.get(grant_url, {"page": "2"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'data-order-url="{grant_url}?page=2"',
        )

    def test_order_pages_keep_total_summary_and_render_only_the_open_page(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order(total_kopecks=102 * 30000)
        photo_ids = self.add_order_photos(order, count=101)

        first = self.client.get(self.order_url(order))
        second = self.client.get(self.order_url(order), {"page": "2"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.context["order_presentation"].item_count, 102)
        self.assertEqual(len(first.context["order_presentation"].photos), 100)
        self.assertEqual(
            tuple(item.photo.photo_id for item in second.context["order_presentation"].photos),
            photo_ids[-2:],
        )
        self.assertContains(second, "Страница 2 из 2")
        self.assertContains(second, "<dt>Фотографий</dt><dd>102</dd>", html=True)

    def test_invalid_order_pages_use_the_private_sanitized_not_found(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()

        for value in ("", "wrong", "0", "-1", "2"):
            with self.subTest(value=value):
                response = self.client.get(self.order_url(order), {"page": value})
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.content, b"")
                self.assertEqual(response["Cache-Control"], "private, no-store")
                self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_paid_archive_is_primary_for_the_open_page_and_pending_stays_archive_free(
        self,
    ) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        paid_order = self.make_order(total_kopecks=60000)
        self.add_order_photos(paid_order, count=1)
        archive_url = reverse(
            "commerce:order_archive", kwargs={"public_number": paid_order.public_number}
        )

        paid = self.client.get(self.order_url(paid_order))

        self.assertContains(paid, f'href="{archive_url}"')
        self.assertContains(paid, ">Скачать все<", html=False)
        self.assertLess(
            paid.content.index(b"order-archive-action"),
            paid.content.index("Отправить письмо ещё раз".encode()),
        )
        self.assertContains(paid, 'class="order-resend order-resend--secondary"')

        Order.objects.filter(pk=paid_order.pk).update(status=Order.Status.PENDING, paid_at=None)
        pending = self.client.get(self.order_url(paid_order))
        self.assertNotContains(pending, "order-archive-action")

    def test_page_two_archive_action_preserves_the_open_page(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order(total_kopecks=102 * 30000)
        self.add_order_photos(order, count=101)

        response = self.client.get(self.order_url(order), {"page": "2"})

        archive_url = reverse(
            "commerce:order_archive", kwargs={"public_number": order.public_number}
        )
        self.assertContains(response, f'href="{archive_url}?page=2"')
        self.assertContains(response, "Скачать эту страницу")
        self.assertContains(
            response,
            (
                "В архив попадут 2 фотографии со страницы 2 из 2. "
                "Остальные страницы можно скачать отдельно."
            ),
        )

    def test_paid_browser_order_page_has_approved_details_without_secrets_or_analytics(
        self,
    ) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()

        response = self.client.get(self.order_url(order))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Заказ оплачен")
        self.assertContains(response, order.public_number)
        self.assertContains(response, self.event.name)
        self.assertContains(response, "300 ₽")
        self.assertContains(response, "b***r@example.test")
        self.assertContains(response, "Скачать оригинал")
        self.assertContains(response, "Отправить письмо ещё раз")
        self.assertContains(response, "Поддержка")
        self.assertContains(response, "support@example.test")
        self.assertContains(response, "Вернуться к мероприятию")
        self.assertContains(
            response,
            f'href="{reverse("event_detail", kwargs={"slug": self.event.slug})}"',
        )
        self.assertNotContains(response, "buyer@example.test")
        self.assertNotContains(response, "provider_payment_id")
        self.assertNotContains(response, "private/")
        self.assertNotContains(response, "https://storage")
        self.assertNotContains(response, "mc.yandex")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        order.refresh_from_db()
        self.assertIsNotNone(order.first_customer_access_at)

    def test_hidden_unpublished_order_item_still_uses_its_exact_watermarked_media_route(
        self,
    ) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()
        resolver = Mock()
        resolver.resolve_signed.return_value = "https://storage.test.invalid/watermark"
        self.event.publication_status = self.event.PublicationStatus.UNAVAILABLE
        self.event.save(update_fields=["publication_status"])
        self.photo.is_hidden = True
        self.photo.save(update_fields=["is_hidden"])

        with patch("commerce.views._purchased_watermarked_media_resolver", return_value=resolver):
            page = self.client.get(self.order_url(order))
            media = self.client.get(self.media_url(order, self.photo.pk))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.media_url(order, self.photo.pk))
        self.assertEqual(media.status_code, 302)
        self.assertEqual(media["Location"], "https://storage.test.invalid/watermark")
        resolver.resolve_signed.assert_called_once_with(photo=self.photo, variant="preview-small")
        self.assertEqual(media["Cache-Control"], "private, no-store")
        self.assertEqual(media["Referrer-Policy"], "no-referrer")

    def test_order_media_rejects_cross_item_and_missing_watermark_without_original_fallback(
        self,
    ) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()
        resolver = Mock()
        resolver.resolve_signed.side_effect = ObjectMissing()

        with patch("commerce.views._purchased_watermarked_media_resolver", return_value=resolver):
            missing = self.client.get(self.media_url(order, self.photo.pk))
            cross_item = self.client.get(self.media_url(order, "other-photo"))

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(cross_item.status_code, 404)
        resolver.resolve_signed.assert_called_once_with(photo=self.photo, variant="preview-small")

    @override_settings(COMMERCE_SUPPORT_CONTACT="")
    def test_order_rendering_fails_closed_without_configured_fulfillment_support(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()

        response = self.client.get(self.order_url(order))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b"")

    @override_settings(DEBUG=False)
    def test_presentation_failure_does_not_record_customer_access(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()
        client = Client(raise_request_exception=False)
        client.cookies["findme_purchase"] = self.cart_token

        with patch("commerce.views.order_presentation", side_effect=RuntimeError("render failed")):
            response = client.get(self.order_url(order))

        order.refresh_from_db()
        self.assertEqual(response.status_code, 500)
        self.assertIsNone(order.first_customer_access_at)

    def test_return_page_is_inert_and_pending_status_is_a_private_authorized_read(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order(status="pending")

        with patch("commerce.views.apply_authenticated_notification") as notification:
            response = self.client.get(self.return_url(order))

        order.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Проверяем оплату")
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertIsNone(order.first_customer_access_at)
        notification.assert_not_called()
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_status_is_a_private_authorized_read_without_payment_mutation(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order(status="pending")

        with patch("commerce.views.apply_authenticated_notification") as notification:
            response = self.client.get(self.status_url(order))

        order.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "pending"})
        self.assertEqual(order.status, Order.Status.PENDING)
        notification.assert_not_called()
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_active_signed_grant_opens_only_its_order_without_a_purchase_cookie(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()
        grant = create_order_access_grant(order=order, source="checkout")
        signature = sign_order_access_grant(
            grant=grant,
            signing_secret="order-view-test-secret",
        )
        self.photo.is_hidden = True
        self.photo.save(update_fields=["is_hidden"])
        del self.client.cookies["findme_purchase"]
        access_url = reverse(
            "commerce:grant_order",
            kwargs={
                "public_number": order.public_number,
                "grant_identifier": grant.pk,
                "signature": signature,
            },
        )

        response = self.client.get(access_url)

        order.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, order.public_number)
        self.assertContains(response, "Скачать оригинал")
        self.assertContains(
            response,
            reverse(
                "commerce:grant_order_media",
                kwargs={
                    "public_number": order.public_number,
                    "grant_identifier": grant.pk,
                    "signature": signature,
                    "photo_id": self.photo.pk,
                    "variant": "preview-small",
                },
            ),
        )
        self.assertIsNotNone(order.first_customer_access_at)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_every_grant_response_is_private_before_view_or_csrf_rejection(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()
        grant = create_order_access_grant(order=order, source="checkout")
        signature = sign_order_access_grant(grant=grant, signing_secret="order-view-test-secret")
        access_url = self.grant_url(order, grant, signature)
        del self.client.cookies["findme_purchase"]
        csrf_client = Client(enforce_csrf_checks=True)
        responses = (
            self.client.get(access_url),
            self.client.get(f"{access_url}status/"),
            self.client.get(f"{access_url}photos/{self.photo.pk}/download/"),
            self.client.post(access_url),
            csrf_client.post(f"{access_url}resend/"),
        )

        for response in responses:
            self.assertIn(response.status_code, {200, 302, 403, 405, 503})
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.assertEqual(response["Referrer-Policy"], "no-referrer")
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertIn("Cookie", response["Vary"])

    @override_settings(DEBUG=False)
    def test_grant_exception_report_redacts_path_referrer_and_bearers(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()
        grant = create_order_access_grant(order=order, source="checkout")
        signature = sign_order_access_grant(grant=grant, signing_secret="order-view-test-secret")
        access_url = self.grant_url(order, grant, signature)
        reports: list[str] = []
        client = Client(raise_request_exception=False)
        client.cookies["findme_purchase"] = self.cart_token
        original_process_exception = PublicSelfieBearerProtectionMiddleware.process_exception

        def capture_report(middleware, request, error):
            response = technical_500_response(
                request,
                type(error),
                error,
                error.__traceback__,
            )
            reports.append(response.content.decode(response.charset))
            return original_process_exception(middleware, request, error)

        with (
            patch("commerce.views.order_presentation", side_effect=RuntimeError("forced grant")),
            patch.object(
                PublicSelfieBearerProtectionMiddleware,
                "process_exception",
                autospec=True,
                side_effect=capture_report,
            ),
        ):
            response = client.get(
                access_url,
                HTTP_REFERER=f"https://findme.test{access_url}?grant-referrer-marker",
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(reports), 1)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("Cookie", response["Vary"])
        report = reports[0]
        self.assertNotIn(str(grant.pk), report)
        self.assertNotIn(signature, report)
        self.assertNotIn("grant-referrer-marker", report)
        self.assertNotIn(self.cart_token, report)
        self.assertIn("forced grant", report)

    def test_unknown_and_revoked_order_grants_share_one_sanitized_not_found_response(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()
        grant = create_order_access_grant(order=order, source="checkout")
        signature = sign_order_access_grant(
            grant=grant,
            signing_secret="order-view-test-secret",
        )
        grant.revoked_at = timezone.now()
        grant.save(update_fields=["revoked_at"])
        revoked_url = reverse(
            "commerce:grant_order",
            kwargs={
                "public_number": order.public_number,
                "grant_identifier": grant.pk,
                "signature": signature,
            },
        )
        unknown_url = reverse(
            "commerce:grant_order",
            kwargs={
                "public_number": "FM-ZYXWVUTS",
                "grant_identifier": grant.pk,
                "signature": signature,
            },
        )
        del self.client.cookies["findme_purchase"]

        revoked = self.client.get(revoked_url)
        unknown = self.client.get(unknown_url)

        for response in (revoked, unknown):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.content, b"")
            self.assertEqual(response["Cache-Control"], "private, no-store")
            self.assertEqual(response["Referrer-Policy"], "no-referrer")
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertIn("Cookie", response["Vary"])

    def test_grant_resend_rate_limit_is_private_before_and_after_view_handling(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()
        grant = create_order_access_grant(order=order, source="checkout")
        signature = sign_order_access_grant(grant=grant, signing_secret="order-view-test-secret")
        del self.client.cookies["findme_purchase"]

        with patch(
            "commerce.views.resend_order_access",
            side_effect=ResendOrderAccessRateLimited(),
        ):
            response = self.client.post(f"{self.grant_url(order, grant, signature)}resend/")

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertIn("Cookie", response["Vary"])


class OrderListTests(OrderViewFixture):
    def test_history_is_private_scoped_and_paginated(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        orders = [
            self.make_order(public_number=f"FM-AAAAAA{chr(65 + index // 8)}{index % 8 + 2}")
            for index in range(51)
        ]
        current_token = self.cart_token
        self.cart_token = generate_browser_token()
        foreign = self.make_order(public_number="FM-ZZZZZZZZ")
        self.cart_token = current_token
        self.client.cookies["findme_purchase"] = current_token
        url = reverse("commerce:order_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["orders_page"]), 50)
        self.assertContains(response, orders[-1].public_number)
        self.assertNotContains(response, orders[0].public_number)
        self.assertNotContains(response, foreign.public_number)
        self.assertNotContains(response, "buyer@example.test")
        self.assertNotContains(response, self.cart_token)
        self.assertNotContains(response, "mc.yandex.ru")
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertIn("Cookie", response["Vary"])
        page_two = self.client.get(url, {"page": 2})
        self.assertEqual(len(page_two.context["orders_page"]), 1)
        self.assertContains(page_two, orders[0].public_number)

    def test_missing_foreign_invalid_and_expired_cookie_have_empty_history(self) -> None:
        self.enable(purchase=FEATURE_FLAG_ON)
        order = self.make_order()
        url = reverse("commerce:order_list")
        for token in (None, generate_browser_token(), "invalid"):
            if token is None:
                self.client.cookies.pop("findme_purchase", None)
            else:
                self.client.cookies["findme_purchase"] = token
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "В этом браузере пока нет заказов")
            self.assertNotContains(response, order.public_number)
        self.client.cookies["findme_purchase"] = self.cart_token
        with patch(
            "commerce.capabilities.timezone.now", return_value=order.created_at + timedelta(days=30)
        ):
            self.assertNotContains(self.client.get(url), order.public_number)
            self.assertEqual(self.client.get(self.order_url(order)).status_code, 404)

    def test_purchase_gate_controls_history_and_navigation(self) -> None:
        url = reverse("commerce:order_list")
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotContains(self.client.get(reverse("event_catalog")), "Мои заказы")
        self.enable(purchase=FEATURE_FLAG_ON)
        self.assertContains(self.client.get(reverse("event_catalog")), "Мои заказы")
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(self.client.post(url)["Cache-Control"], "private, no-store")


@override_settings(COMMERCE_SUPPORT_CONTACT="support@example.test")
class OrderPaymentContinuationTests(OrderViewFixture):
    def setUp(self):
        super().setUp()
        self.enable(purchase=FEATURE_FLAG_ON)
        self.pending_order = self.make_order(status="pending")
        self.retry_url = f"/orders/{self.pending_order.public_number}/retry-payment/"

    def attempt(self, **values):
        from commerce.models import PaymentAttempt

        return PaymentAttempt.objects.create(
            order=self.pending_order,
            amount_kopecks=30000,
            currency="RUB",
            adapter_key="deterministic-test",
            idempotency_key=generate_browser_token(),
            **values,
        )

    def test_active_hosted_payment_resumes_without_cart_or_gateway_call(self):
        attempt = self.attempt(
            provider_payment_id="existing", confirmation_url="https://bank.test/pay"
        )
        del self.client.cookies["findme_cart"]
        with patch("commerce.views._payment_gateway") as gateway:
            response = self.client.post(self.retry_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, attempt.confirmation_url)
        gateway.assert_not_called()
        self.assertEqual(self.pending_order.payment_attempts.count(), 1)

    def test_active_no_url_and_unsafe_url_show_waiting_without_new_attempt(self):
        for url in ("", "javascript:alert(1)", "https://[malformed"):
            attempt = self.attempt(confirmation_url=url)
            with patch("commerce.views._payment_gateway") as gateway:
                response = self.client.post(self.retry_url)
            self.assertContains(response, "Проверяем связь с банком; повторите позже")
            gateway.assert_not_called()
            self.assertEqual(
                self.pending_order.payment_attempts.filter(status="pending").count(), 1
            )
            type(attempt).objects.filter(pk=attempt.pk).update(
                status="failed", terminal_at=timezone.now()
            )

    def test_terminal_retry_uses_saved_terms_and_exact_order(self):
        from commerce.models import PaymentAttempt
        from commerce.test_payment_gateway import TestPaymentOutcome
        from commerce.tests.test_checkout import RecordingGateway

        self.attempt(status=PaymentAttempt.Status.CANCELED, terminal_at=timezone.now())
        self.event.price_per_photo_kopecks = 99900
        self.event.save(update_fields=["price_per_photo_kopecks"])
        gateway = RecordingGateway(
            outcome=TestPaymentOutcome.PENDING, notification_secret=b"secret"
        )
        del self.client.cookies["findme_cart"]
        with patch("commerce.views._payment_gateway", return_value=gateway):
            response = self.client.post(
                self.retry_url,
                {"email": "attacker@example.test", "amount": "1", "photo": "foreign"},
            )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(self.pending_order.payment_attempts.count(), 2)
        request = gateway.requests[0]
        self.assertEqual(request.order_public_number, self.pending_order.public_number)
        self.assertEqual(request.amount_kopecks, 30000)
        self.assertEqual(request.checkout_email, "buyer@example.test")
        self.assertEqual(request.currency, "RUB")
        self.assertEqual(request.receipt_lines[0].unit_amount_kopecks, 30000)

    def test_gateway_uncertainty_keeps_single_active_attempt_and_waiting(self):
        from commerce.models import PaymentAttempt
        from commerce.test_payment_gateway import TestPaymentOutcome
        from commerce.tests.test_checkout import TimeoutOnceGateway

        self.attempt(status=PaymentAttempt.Status.FAILED, terminal_at=timezone.now())
        gateway = TimeoutOnceGateway(
            adapter_key="tbank-eacq-v1",
            outcome=TestPaymentOutcome.PENDING,
            notification_secret=b"secret",
        )
        with patch("commerce.views._payment_gateway", return_value=gateway):
            first = self.client.post(self.retry_url)
            second = self.client.post(self.retry_url)
        self.assertContains(first, "Проверяем связь с банком; повторите позже")
        self.assertContains(second, "Проверяем связь с банком; повторите позже")
        self.assertEqual(self.pending_order.payment_attempts.count(), 2)
        self.assertEqual(len(gateway.requests), 1)
        active = self.pending_order.payment_attempts.get(status=PaymentAttempt.Status.PENDING)
        self.assertIsNotNone(active.initiation_started_at)
        self.assertIsNotNone(active.reconciliation_next_attempt_at)

    def test_nonpending_order_or_invalid_cookie_fail_closed(self):
        for status in ("paid", "canceled", "superseded"):
            Order.objects.filter(pk=self.pending_order.pk).update(
                status=status, paid_at=timezone.now() if status == "paid" else None
            )
            self.assertEqual(self.client.post(self.retry_url).status_code, 404)
        Order.objects.filter(pk=self.pending_order.pk).update(status="pending", paid_at=None)
        for token in ("", generate_browser_token()):
            self.client.cookies["findme_purchase"] = token
            self.assertEqual(self.client.post(self.retry_url).status_code, 404)
        self.client.cookies["findme_purchase"] = self.cart_token
        with patch(
            "commerce.order_payment.timezone.now", return_value=timezone.now() + timedelta(days=31)
        ):
            self.assertEqual(self.client.post(self.retry_url).status_code, 404)
        self.assertEqual(self.pending_order.payment_attempts.count(), 0)

    def test_retry_requires_post_and_csrf_and_page_distinguishes_terminal(self):
        from commerce.models import PaymentAttempt

        self.attempt(status=PaymentAttempt.Status.CANCELED, terminal_at=timezone.now())
        self.assertEqual(self.client.get(self.retry_url).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.cookies["findme_purchase"] = self.cart_token
        self.assertEqual(csrf_client.post(self.retry_url).status_code, 403)
        page = self.client.get(self.order_url(self.pending_order))
        self.assertContains(page, "Повторить оплату")
        self.assertContains(page, "Оплата не завершена")
        self.assertNotContains(page, "Проверяем оплату")
        self.assertContains(page, self.retry_url)

    def test_mismatched_bank_success_conflict_cannot_start_another_payment(self):
        from commerce.models import PaymentAttempt
        from commerce.payment_gateway import NormalizedPaymentStatus, PaymentObservation
        from commerce.payments import apply_payment_observation

        attempt = self.attempt(provider_payment_id="conflicted-payment")
        apply_payment_observation(
            attempt_id=attempt.pk,
            adapter_key=attempt.adapter_key,
            source="notification",
            observation=PaymentObservation(
                provider_payment_id=attempt.provider_payment_id,
                status=NormalizedPaymentStatus.SUCCEEDED,
                amount_kopecks=29999,
                currency="RUB",
                idempotency_key=attempt.idempotency_key,
            ),
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, PaymentAttempt.Status.CONFLICT)
        page = self.client.get(self.order_url(self.pending_order))
        self.assertNotContains(page, "Повторить оплату")
        self.assertNotContains(page, "Оплата не завершена")
        with patch("commerce.views._payment_gateway") as gateway:
            response = self.client.post(self.retry_url)
        self.assertContains(response, "Свяжитесь с поддержкой")
        gateway.assert_not_called()
        self.assertEqual(self.pending_order.payment_attempts.count(), 1)
        self.pending_order.refresh_from_db()
        self.assertEqual(self.pending_order.status, Order.Status.PENDING)

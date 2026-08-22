from datetime import timedelta
from unittest.mock import patch

from django.contrib import admin
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection, transaction
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.models import FeatureFlag
from picflow.models import Event, Photo

from commerce.attention import open_attention, resolve_attention_automatically
from commerce.checkout import CheckoutPaymentUnavailable
from commerce.models import (
    CommerceAttention,
    EmailDelivery,
    Order,
    OrderAccessGrant,
    OrderItem,
    PaymentAttempt,
    PaymentEvidence,
)
from commerce.payment_gateway import NormalizedPaymentStatus, PaymentObservation


@override_settings(
    COMMERCE_ORDER_ACCESS_SIGNING_SECRET="admin-order-access-test-secret",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class CommerceAdminTests(TransactionTestCase):
    """The breaks caught here would make trusted recovery mutate evidence or bypass the gate."""

    def setUp(self) -> None:
        self.operator = get_user_model().objects.create_superuser(
            username="commerce-admin",
            email="commerce-admin@example.test",
            password="password",
        )
        self.client.force_login(self.operator)
        self.event = Event.objects.create(
            name="Admin commerce event",
            slug="admin-commerce-event",
            start_date=timezone.localdate(),
            end_date=timezone.localdate(),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )
        self.photo = Photo.objects.create(
            id="admin-commerce-photo",
            event=self.event,
            src="photos/admin-commerce.jpg",
        )
        self.order = self.make_order()
        self.checkout_grant = OrderAccessGrant.objects.create(
            order=self.order,
            source=OrderAccessGrant.Source.CHECKOUT,
        )
        self.attempt = PaymentAttempt.objects.create(
            order=self.order,
            amount_kopecks=30000,
            currency="RUB",
            adapter_key="admin-test-gateway",
            idempotency_key="admin-test-attempt",
            provider_payment_id="safe-provider-reference",
        )
        PaymentEvidence.objects.create(
            payment_attempt=self.attempt,
            source=PaymentEvidence.Source.STATUS_FETCH,
            provider_event_id="safe-provider-event",
            normalized_status=PaymentAttempt.Status.PENDING,
            amount_kopecks=30000,
            currency="RUB",
            observed_at=timezone.now(),
        )

    def make_order(self, *, status: str = str(Order.Status.PENDING)) -> Order:
        paid_at = timezone.now() if status == str(Order.Status.PAID) else None
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET CONSTRAINTS commerce_order_insert_total_guard, "
                    "commerce_order_item_total_guard DEFERRED"
                )
            order = Order.objects.create(
                event=self.event,
                checkout_email="checkout@example.test",
                delivery_email="checkout@example.test",
                total_kopecks=30000,
                status=status,
                paid_at=paid_at,
            )
            OrderItem.objects.create(
                order=order,
                photo=self.photo,
                photo_public_id=self.photo.pk,
                unit_price_kopecks=30000,
                line_total_kopecks=30000,
            )
        order.delivery_email = "delivery@example.test"
        order.save(update_fields=["delivery_email"])
        return order

    def enable_purchase(self) -> None:
        FeatureFlag.objects.create(
            key="paid-photo-purchase",
            description="Admin recovery rehearsal",
            state=FeatureFlag.State.STAFF,
        )

    def action_url(self, model_name: str) -> str:
        return reverse(f"admin:commerce_{model_name}_changelist")

    def post_action(self, model_name: str, action: str, selected: object, **extra: object):
        selected_ids = selected if isinstance(selected, list) else [selected]
        return self.client.post(
            self.action_url(model_name),
            {
                "action": action,
                "_selected_action": [str(value) for value in selected_ids],
                **extra,
            },
        )

    def order_change_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "delivery_email": self.order.delivery_email,
            "_save": "Save",
        }
        inline_counts = {
            "items": OrderItem.objects.filter(order=self.order).count(),
            "payment_attempts": PaymentAttempt.objects.filter(order=self.order).count(),
            "email_deliveries": EmailDelivery.objects.filter(order=self.order).count(),
            "access_grants": OrderAccessGrant.objects.filter(order=self.order).count(),
            "attention_records": CommerceAttention.objects.filter(order=self.order).count(),
        }
        for prefix, count in inline_counts.items():
            payload.update(
                {
                    f"{prefix}-TOTAL_FORMS": str(count),
                    f"{prefix}-INITIAL_FORMS": str(count),
                    f"{prefix}-MIN_NUM_FORMS": "0",
                    f"{prefix}-MAX_NUM_FORMS": "1000",
                }
            )
        for prefix, objects in (
            ("items", OrderItem.objects.filter(order=self.order)),
            ("payment_attempts", PaymentAttempt.objects.filter(order=self.order)),
            ("email_deliveries", EmailDelivery.objects.filter(order=self.order)),
            ("access_grants", OrderAccessGrant.objects.filter(order=self.order)),
            ("attention_records", CommerceAttention.objects.filter(order=self.order)),
        ):
            for index, obj in enumerate(objects):
                payload[f"{prefix}-{index}-id"] = str(obj.pk)
                payload[f"{prefix}-{index}-order"] = str(self.order.pk)
        payload.update(overrides)
        return payload

    def test_order_admin_shows_full_commercial_evidence_but_only_delivery_email_is_editable(
        self,
    ) -> None:
        """Making immutable checkout, price, or evidence fields editable falsifies an Order."""
        model_admin = admin.site._registry[Order]
        response = self.client.get(reverse("admin:commerce_order_change", args=(self.order.pk,)))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "checkout@example.test")
        self.assertContains(response, "delivery@example.test")
        self.assertContains(response, "safe-provider-reference")
        self.assertContains(response, "safe-provider-event")
        self.assertContains(response, 'name="delivery_email"')
        for field in (
            "event",
            "checkout_email",
            "total_kopecks",
            "currency",
            "paid_at",
            "status",
        ):
            self.assertIn(field, model_admin.get_readonly_fields(None, self.order))
            self.assertNotContains(response, f'name="{field}"')
        self.assertTrue(any(inline.model is OrderItem for inline in model_admin.inlines))
        self.assertTrue(any(inline.model is PaymentAttempt for inline in model_admin.inlines))
        self.assertTrue(any(inline.model is EmailDelivery for inline in model_admin.inlines))
        self.assertTrue(any(inline.model is OrderAccessGrant for inline in model_admin.inlines))

    def test_delivery_correction_uses_ordinary_admin_history_without_changing_checkout_email(
        self,
    ) -> None:
        """A support correction must not rewrite the immutable checkout fact."""
        obsolete_delivery = EmailDelivery.objects.create(
            order=self.order,
            message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
            recipient_email=self.order.delivery_email,
            access_grant=self.checkout_grant,
            next_attempt_at=timezone.now(),
        )
        response = self.client.post(
            reverse("admin:commerce_order_change", args=(self.order.pk,)),
            self.order_change_payload(delivery_email=" Corrected@Example.TEST "),
        )

        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.checkout_email, "checkout@example.test")
        self.assertEqual(self.order.delivery_email, "corrected@example.test")
        obsolete_delivery.refresh_from_db()
        self.assertEqual(obsolete_delivery.state, EmailDelivery.State.CANCELED)
        history = LogEntry.objects.get(object_id=str(self.order.pk), action_flag=CHANGE)
        self.assertEqual(history.user, self.operator)

    def test_manual_paid_requires_confirmation_and_the_enabled_gate_without_bank_evidence_fields(
        self,
    ) -> None:
        """A raw POST or a closed gate must not create paid entitlement."""
        closed = self.post_action("order", "confirm_payment_manually", self.order.pk)

        self.assertEqual(closed.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)
        self.enable_purchase()
        prompt = self.post_action("order", "confirm_payment_manually", self.order.pk)
        self.assertEqual(prompt.status_code, 200)
        self.assertContains(prompt, "Подтвердить оплату вручную")
        self.assertNotContains(prompt, "bank_reference")
        self.assertNotContains(prompt, "attachment")
        self.assertNotContains(prompt, 'name="amount"')
        self.assertNotContains(prompt, 'name="comment"')

        confirmed = self.post_action(
            "order",
            "confirm_payment_manually",
            self.order.pk,
            confirm_manual_paid="yes",
        )

        self.assertEqual(confirmed.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)
        audit = LogEntry.objects.filter(object_id=str(self.order.pk)).latest("action_time")
        self.assertEqual(audit.user, self.operator)
        self.assertIn("Оплата подтверждена вручную", audit.change_message)

    def test_manual_paid_is_not_available_for_terminal_orders_or_when_the_gate_is_closed(
        self,
    ) -> None:
        """Terminal Orders or a closed gate cannot create a manual paid side effect."""
        model_admin = admin.site._registry[Order]
        view_only_operator = get_user_model().objects.create_user(
            username="order-viewer", password="password", is_staff=True
        )
        view_only_operator.user_permissions.add(
            Permission.objects.get(content_type__app_label="commerce", codename="view_order")
        )
        self.client.force_login(view_only_operator)
        self.assertNotIn(
            "confirm_payment_manually",
            model_admin.get_actions(self.client.request().wsgi_request),
        )
        self.client.force_login(self.operator)
        request = self.client.request().wsgi_request
        request.user = self.operator
        self.assertNotIn("confirm_payment_manually", model_admin.get_actions(request))

        self.enable_purchase()
        self.order.status = Order.Status.CANCELED
        self.order.save(update_fields=["status"])
        actions = model_admin.get_actions(request)
        self.assertIn("confirm_payment_manually", actions)
        response = self.post_action(
            "order",
            "confirm_payment_manually",
            self.order.pk,
            confirm_manual_paid="yes",
        )
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELED)

    def test_pending_cancel_and_resend_are_standard_actions_with_gate_closed_for_new_delivery(
        self,
    ) -> None:
        """Cancellation is constrained to pending while new email work remains dark-deployed."""
        canceled = self.post_action("order", "cancel_pending_orders", self.order.pk)
        self.assertEqual(canceled.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.CANCELED)

        paid_order = self.make_order(status=str(Order.Status.PAID))
        closed_resend = self.post_action("order", "resend_order_access", paid_order.pk)
        self.assertEqual(closed_resend.status_code, 302)
        self.assertFalse(EmailDelivery.objects.filter(order=paid_order).exists())
        self.enable_purchase()
        sent = self.post_action("order", "resend_order_access", paid_order.pk)
        self.assertEqual(sent.status_code, 302)
        self.assertEqual(EmailDelivery.objects.filter(order=paid_order).count(), 1)

    def test_provider_refresh_uses_the_matching_adapter_outside_a_database_transaction(
        self,
    ) -> None:
        """Bank I/O inside a transaction can lock data or corrupt evidence."""
        self.enable_purchase()
        in_atomic_blocks: list[bool] = []

        class Gateway:
            adapter_key = "admin-test-gateway"

            def fetch_payment(self, provider_payment_id: str) -> PaymentObservation:
                in_atomic_blocks.append(connection.in_atomic_block)
                return PaymentObservation(
                    provider_payment_id=provider_payment_id,
                    status=NormalizedPaymentStatus.PENDING,
                    amount_kopecks=30000,
                    currency="RUB",
                    idempotency_key="admin-test-attempt",
                    provider_event_id="refresh-event",
                )

        refresh_url = reverse("admin:commerce_paymentattempt_refresh", args=(self.attempt.pk,))
        with patch(
            "commerce.admin._payment_gateway_for_adapter", return_value=Gateway()
        ) as factory:
            response = self.client.post(refresh_url)

        self.assertEqual(response.status_code, 302)
        factory.assert_called_once_with(response.wsgi_request, "admin-test-gateway")
        self.assertEqual(in_atomic_blocks, [False])
        self.assertEqual(PaymentEvidence.objects.filter(payment_attempt=self.attempt).count(), 2)

    def test_provider_refresh_control_is_gated_and_exposes_only_a_csrf_post(self) -> None:
        """A bank-status endpoint without an Admin form is not an operator recovery action."""
        change_url = reverse("admin:commerce_paymentattempt_change", args=(self.attempt.pk,))
        refresh_url = reverse("admin:commerce_paymentattempt_refresh", args=(self.attempt.pk,))

        self.assertEqual(self.client.get(refresh_url).status_code, 405)
        self.assertNotContains(self.client.get(change_url), "Проверить статус в банке")
        self.assertEqual(self.client.post(refresh_url).status_code, 403)

        flag = FeatureFlag.objects.create(
            key="paid-photo-purchase",
            description="Admin refresh gate",
            state=FeatureFlag.State.OFF,
        )
        self.assertNotContains(self.client.get(change_url), "Проверить статус в банке")
        self.assertEqual(self.client.post(refresh_url).status_code, 403)

        flag.state = FeatureFlag.State.STAFF
        flag.save(update_fields=["state"])
        staff_response = self.client.get(change_url)
        self.assertContains(staff_response, "Проверить статус в банке")
        self.assertContains(staff_response, f'action="{refresh_url}"')
        self.assertContains(staff_response, 'name="csrfmiddlewaretoken"')
        self.assertEqual(
            self.client.post(
                reverse("admin:commerce_paymentattempt_refresh", args=("missing",))
            ).status_code,
            403,
        )

        flag.state = FeatureFlag.State.ON
        flag.save(update_fields=["state"])
        self.assertContains(self.client.get(change_url), "Проверить статус в банке")

    def test_provider_refresh_form_requires_csrf_and_granular_permissions(self) -> None:
        """Only staff with both evidence view and Order-change authority may submit refresh."""
        self.enable_purchase()
        change_url = reverse("admin:commerce_paymentattempt_change", args=(self.attempt.pk,))
        refresh_url = reverse("admin:commerce_paymentattempt_refresh", args=(self.attempt.pk,))
        viewer = get_user_model().objects.create_user(
            username="attempt-viewer", password="password", is_staff=True
        )
        viewer.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="commerce", codename="view_paymentattempt"
            )
        )
        self.client.force_login(viewer)
        self.assertNotContains(self.client.get(change_url), "Проверить статус в банке")
        self.assertEqual(self.client.post(refresh_url).status_code, 403)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.operator)
        form_response = csrf_client.get(change_url)
        csrf_token = csrf_client.cookies["csrftoken"].value
        self.assertEqual(csrf_client.post(refresh_url).status_code, 403)

        class Gateway:
            adapter_key = "admin-test-gateway"

            def fetch_payment(self, provider_payment_id: str) -> PaymentObservation:
                return PaymentObservation(
                    provider_payment_id=provider_payment_id,
                    status=NormalizedPaymentStatus.PENDING,
                    amount_kopecks=30000,
                    currency="RUB",
                    idempotency_key="admin-test-attempt",
                    provider_event_id="csrf-refresh-event",
                )

        self.assertContains(form_response, 'name="csrfmiddlewaretoken"')
        with patch("commerce.admin._payment_gateway_for_adapter", return_value=Gateway()):
            submitted = csrf_client.post(refresh_url, HTTP_X_CSRFTOKEN=csrf_token)
        self.assertEqual(submitted.status_code, 302)

    def test_provider_refresh_handles_unavailable_adapter_without_changing_evidence(
        self,
    ) -> None:
        """An unavailable provider must leave staff on a safe Admin response."""
        self.enable_purchase()
        refresh_url = reverse("admin:commerce_paymentattempt_refresh", args=(self.attempt.pk,))

        with patch(
            "commerce.admin._payment_gateway_for_adapter",
            side_effect=CheckoutPaymentUnavailable(),
        ):
            response = self.client.post(refresh_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(PaymentEvidence.objects.filter(payment_attempt=self.attempt).count(), 1)

    def test_grant_create_copy_and_revoke_show_a_bearer_only_once_without_persisting_it(
        self,
    ) -> None:
        """A stored permanent bearer would expose Orders to later readers."""
        self.enable_purchase()
        created = self.post_action("order", "create_order_access_grant", self.order.pk)
        self.assertEqual(created.status_code, 200)
        grant = OrderAccessGrant.objects.get(order=self.order, source=OrderAccessGrant.Source.ADMIN)
        self.assertContains(created, str(grant.pk))
        self.assertNotIn("signature", {field.name for field in OrderAccessGrant._meta.local_fields})
        self.assertNotIn(
            "access_url", {field.name for field in OrderAccessGrant._meta.local_fields}
        )

        copied = self.post_action("orderaccessgrant", "copy_order_access_grant", grant.pk)
        self.assertEqual(copied.status_code, 200)
        self.assertContains(copied, str(grant.pk))
        self.assertIn("no-store", copied["Cache-Control"])
        self.assertEqual(copied["Referrer-Policy"], "no-referrer")
        revoked = self.post_action("orderaccessgrant", "revoke_order_access_grant", grant.pk)
        self.assertEqual(revoked.status_code, 302)
        grant.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)

    def test_grant_copy_is_hidden_and_denied_until_the_purchase_gate_allows_it(self) -> None:
        """Regenerating a bearer is issuance, not gate-off evidence inspection."""
        model_admin = admin.site._registry[OrderAccessGrant]
        request = self.client.request().wsgi_request
        request.user = self.operator

        self.assertNotIn("copy_order_access_grant", model_admin.get_actions(request))
        closed = self.post_action(
            "orderaccessgrant", "copy_order_access_grant", self.checkout_grant.pk
        )
        self.assertEqual(closed.status_code, 302)

        flag = FeatureFlag.objects.create(
            key="paid-photo-purchase",
            description="Grant copy gate",
            state=FeatureFlag.State.OFF,
        )
        self.assertNotIn("copy_order_access_grant", model_admin.get_actions(request))
        off_closed = self.post_action(
            "orderaccessgrant", "copy_order_access_grant", self.checkout_grant.pk
        )
        self.assertEqual(off_closed.status_code, 302)
        flag.state = FeatureFlag.State.STAFF
        flag.save(update_fields=["state"])
        self.assertIn("copy_order_access_grant", model_admin.get_actions(request))
        flag.state = FeatureFlag.State.ON
        flag.save(update_fields=["state"])
        self.assertIn("copy_order_access_grant", model_admin.get_actions(request))

    def test_failed_delivery_can_be_retried_only_while_the_gate_permits_new_side_effects(
        self,
    ) -> None:
        """An exhausted email needs recovery but not after a dark rollback."""
        paid_order = self.make_order(status=str(Order.Status.PAID))
        grant = OrderAccessGrant.objects.create(
            order=paid_order, source=OrderAccessGrant.Source.CHECKOUT
        )
        delivery = EmailDelivery.objects.create(
            order=paid_order,
            message_kind=EmailDelivery.MessageKind.ORDER_ACCESS,
            recipient_email=paid_order.delivery_email,
            access_grant=grant,
            state=EmailDelivery.State.FAILED,
            attempt_count=6,
            next_attempt_at=timezone.now() - timedelta(minutes=1),
            last_failure_category="provider_failure",
        )
        closed = self.post_action("emaildelivery", "retry_failed_delivery", delivery.pk)
        self.assertEqual(closed.status_code, 200)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, EmailDelivery.State.FAILED)

        self.enable_purchase()
        retried = self.post_action("emaildelivery", "retry_failed_delivery", delivery.pk)
        self.assertEqual(retried.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, EmailDelivery.State.PENDING)
        self.assertEqual(delivery.attempt_count, 6)
        repeated = self.post_action("emaildelivery", "retry_failed_delivery", delivery.pk)
        self.assertEqual(repeated.status_code, 302)
        self.assertEqual(EmailDelivery.objects.filter(order=paid_order).count(), 1)

    def test_attention_list_has_open_count_and_requires_permission_and_comment(
        self,
    ) -> None:
        """Resolution requires dedicated authority and a durable comment."""
        attention = open_attention(
            kind=str(CommerceAttention.Kind.PAYMENT_MISMATCH),
            subject=f"payment-attempt:{self.attempt.pk}",
            order=self.order,
            payment_attempt=self.attempt,
        )
        list_response = self.client.get(self.action_url("commerceattention"))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, "Открытых обращений: 1")

        permission = Permission.objects.get(
            content_type__app_label="commerce", codename="handle_attention"
        )
        restricted = get_user_model().objects.create_user(
            username="attention-viewer", password="password", is_staff=True
        )
        restricted.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="commerce", codename="view_commerceattention"
            )
        )
        self.client.force_login(restricted)
        actions = admin.site._registry[CommerceAttention].get_actions(
            self.client.request().wsgi_request
        )
        self.assertNotIn("resolve_attention", actions)
        self.client.force_login(self.operator)
        self.operator.user_permissions.add(permission)
        prompt = self.post_action("commerceattention", "resolve_attention", attention.pk)
        self.assertEqual(prompt.status_code, 200)
        self.assertContains(prompt, 'name="resolution_comment"')
        self.assertContains(prompt, "required")
        self.assertContains(prompt, f'name="_selected_action" value="{attention.pk}"')
        unresolved = self.post_action(
            "commerceattention",
            "resolve_attention",
            attention.pk,
            confirm_attention_resolution="yes",
            resolution_comment=" ",
        )
        self.assertEqual(unresolved.status_code, 200)
        self.assertContains(unresolved, "Нужен комментарий к разрешению.")
        attention.refresh_from_db()
        self.assertIsNone(attention.resolved_at)
        resolved = self.post_action(
            "commerceattention",
            "resolve_attention",
            attention.pk,
            confirm_attention_resolution="yes",
            resolution_comment="Проверено оператором.",
        )
        self.assertEqual(resolved.status_code, 302)
        attention.refresh_from_db()
        self.assertEqual(attention.resolution_comment, "Проверено оператором.")

    def test_attention_auto_repair_winning_confirmation_writes_no_manual_history(self) -> None:
        """An automatic repair during confirmation must not fabricate a manual Admin event."""
        attention = open_attention(
            kind=str(CommerceAttention.Kind.PAYMENT_MISMATCH),
            subject=f"payment-attempt:{self.attempt.pk}",
            order=self.order,
            payment_attempt=self.attempt,
        )
        self.operator.user_permissions.add(
            Permission.objects.get(content_type__app_label="commerce", codename="handle_attention")
        )

        from commerce.attention import resolve_attention_manually as domain_manual_resolution

        def automatic_repair_then_manual_result(**kwargs):
            resolve_attention_automatically(attention_id=attention.pk)
            return domain_manual_resolution(**kwargs)

        with patch(
            "commerce.admin.resolve_attention_manually",
            side_effect=automatic_repair_then_manual_result,
        ):
            response = self.post_action(
                "commerceattention",
                "resolve_attention",
                attention.pk,
                confirm_attention_resolution="yes",
                resolution_comment="Проверено оператором.",
            )

        self.assertEqual(response.status_code, 302)
        attention.refresh_from_db()
        self.assertEqual(attention.resolution_source, CommerceAttention.ResolutionSource.AUTOMATIC)
        self.assertEqual(attention.resolution_comment, "")
        completed = self.client.get(response["Location"])
        self.assertContains(completed, "Разрешено обращений: 0.")
        self.assertContains(completed, "Открытых обращений: 0")
        self.assertFalse(
            LogEntry.objects.filter(
                object_id=str(attention.pk),
                change_message="Обращение разрешено вручную.",
            ).exists()
        )

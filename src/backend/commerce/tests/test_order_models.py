from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Sum
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.utils import timezone
from picflow.models import Event, Photo

from commerce.models import Order, OrderItem, PaymentAttempt, PaymentEvidence


class OrderModelTests(TestCase):
    """The breaks caught here would rewrite commercial records or payment facts."""

    def setUp(self) -> None:
        self.event = self.make_event(name="Order event", slug="order-event")
        self.other_event = self.make_event(name="Other event", slug="other-order-event")
        self.photo = Photo.objects.create(
            id="order-photo", event=self.event, src="photos/order.jpg"
        )
        self.other_photo = Photo.objects.create(
            id="other-order-photo", event=self.other_event, src="photos/other-order.jpg"
        )
        self.defer_order_total_guards()

    def make_event(self, *, name: str, slug: str) -> Event:
        return Event.objects.create(
            name=name,
            slug=slug,
            start_date=date(2026, 8, 20),
            end_date=date(2026, 8, 20),
            city="Moscow",
            access_type=Event.AccessType.PAID,
            price_per_photo_kopecks=30000,
        )

    def make_order(
        self,
        *,
        public_number: str = "FM-ABCDEFGH",
        total_kopecks: int = 30000,
    ) -> Order:
        return Order.objects.create(
            public_number=public_number,
            event=self.event,
            checkout_email="buyer@example.test",
            total_kopecks=total_kopecks,
        )

    def make_item(
        self,
        *,
        order: Order,
        photo: Photo | None = None,
        unit_price_kopecks: int = 30000,
    ) -> OrderItem:
        photo = photo or self.photo
        return OrderItem.objects.create(
            order=order,
            photo=photo,
            photo_public_id=photo.pk,
            unit_price_kopecks=unit_price_kopecks,
            quantity=1,
            line_total_kopecks=unit_price_kopecks,
        )

    def make_complete_order(self, *, public_number: str = "FM-ABCDEFGH") -> Order:
        order = self.make_order(public_number=public_number)
        self.make_item(order=order)
        return order

    def defer_order_total_guards(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                "SET CONSTRAINTS commerce_order_insert_total_guard, "
                "commerce_order_item_total_guard DEFERRED"
            )

    def make_attempt(
        self,
        *,
        order: Order,
        idempotency_key: str = "checkout-attempt-1",
        provider_payment_id: str | None = None,
        status: str = PaymentAttempt.Status.PENDING,
        adapter_key: str = "test-gateway",
    ) -> PaymentAttempt:
        values = {
            "order": order,
            "amount_kopecks": order.total_kopecks,
            "currency": "RUB",
            "adapter_key": adapter_key,
            "idempotency_key": idempotency_key,
            "status": status,
        }
        if provider_payment_id is not None:
            values["provider_payment_id"] = provider_payment_id
        if status != PaymentAttempt.Status.PENDING:
            values["terminal_at"] = timezone.now()
        return PaymentAttempt.objects.create(**values)

    def test_order_snapshots_one_event_rub_total_and_checkout_email(self) -> None:
        """Changing these values would alter the customer\'s accepted order after checkout."""
        order = self.make_complete_order()

        self.assertEqual(order.event_id, self.event.pk)
        self.assertEqual(order.currency, "RUB")
        self.assertEqual(order.total_kopecks, 30000)
        self.assertEqual(order.checkout_email, "buyer@example.test")
        self.assertEqual(order.delivery_email, order.checkout_email)

        for field, replacement in (
            ("event", self.other_event),
            ("checkout_email", "rewritten@example.test"),
            ("total_kopecks", 1),
            ("currency", "USD"),
            ("public_number", "FM-HGFEDCBA"),
        ):
            with self.subTest(field=field):
                setattr(order, field, replacement)
                with self.assertRaisesRegex(ValidationError, "immutable"):
                    order.save()
                order.refresh_from_db()

        order.delivery_email = "corrected@example.test"
        order.save(update_fields=["delivery_email"])
        order.refresh_from_db()
        self.assertEqual(order.delivery_email, "corrected@example.test")

    def test_order_database_allows_only_rub_and_the_exact_states(self) -> None:
        """An invented currency or state would make payment and entitlement rules ambiguous."""
        valid_states = {
            Order.Status.PENDING,
            Order.Status.SUPERSEDED,
            Order.Status.PAID,
            Order.Status.CANCELED,
        }

        self.assertEqual(
            {choice for choice, _label in Order.Status.choices},
            {"pending", "superseded", "paid", "canceled"},
        )
        self.assertEqual(Order._meta.get_field("currency").default, "RUB")
        self.assertEqual(valid_states, {"pending", "superseded", "paid", "canceled"})

        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(
                public_number="FM-USDRUBAA",
                event=self.event,
                checkout_email="buyer@example.test",
                total_kopecks=30000,
                currency="USD",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(
                public_number="FM-BADSTATE",
                event=self.event,
                checkout_email="buyer@example.test",
                total_kopecks=30000,
                status="refunded",
            )

    def test_items_are_quantity_one_unique_immutable_price_snapshots_from_the_order_event(
        self,
    ) -> None:
        """Duplicate, cross-event, or rewritten lines would change what the customer purchased."""
        order = self.make_order()
        item = self.make_item(order=order)

        self.assertEqual(item.quantity, 1)
        self.assertEqual(item.line_total_kopecks, item.unit_price_kopecks)
        self.assertEqual(item.photo_public_id, self.photo.pk)
        self.assertEqual(item.photo.event_id, order.event_id)

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_item(order=order)
        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderItem.objects.create(
                order=order,
                photo=self.photo,
                photo_public_id=self.photo.pk,
                unit_price_kopecks=30000,
                quantity=2,
                line_total_kopecks=60000,
            )
        item.unit_price_kopecks = 1
        with self.assertRaisesRegex(ValidationError, "immutable"):
            item.save()
        with self.assertRaisesRegex(ValidationError, "immutable"):
            item.delete()

    def test_order_item_persistence_rejects_a_photo_from_another_event(self) -> None:
        """A cross-event line would authorize a photo that was not in the checkout event."""
        order = self.make_complete_order(public_number="FM-CRSVENTA")

        with self.assertRaises(DatabaseError), transaction.atomic():
            self.make_item(order=order, photo=self.other_photo)

    def test_order_item_photo_event_cannot_be_reassigned(self) -> None:
        """Reassigning the purchased Photo would make its saved line cross-event."""
        order = self.make_order(public_number="FM-PHTEVNTA")
        self.make_item(order=order)

        with (
            self.assertRaisesRegex(DatabaseError, "referenced by an order item"),
            transaction.atomic(),
        ):
            Photo.objects.filter(pk=self.photo.pk).update(event=self.other_event)

    def test_order_item_persistence_rejects_a_mismatched_public_photo_identity(self) -> None:
        """A mismatched public id would disagree with the protected Photo."""
        order = self.make_complete_order(public_number="FM-PHTAJDBQ")

        with self.assertRaises(IntegrityError), transaction.atomic():
            OrderItem.objects.create(
                order=order,
                photo=self.photo,
                photo_public_id="different-photo",
                unit_price_kopecks=30000,
                quantity=1,
                line_total_kopecks=30000,
            )

    def test_order_total_remains_the_sum_of_immutable_line_snapshots_after_event_price_changes(
        self,
    ) -> None:
        """Using the current Event price would silently alter a completed checkout."""
        second_photo = Photo.objects.create(
            id="second-order-photo", event=self.event, src="photos/second-order.jpg"
        )
        with transaction.atomic():
            order = self.make_order(public_number="FM-TTALSUMP", total_kopecks=60000)
            self.make_item(order=order, photo=self.photo)
            self.make_item(order=order, photo=second_photo)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET CONSTRAINTS commerce_order_insert_total_guard, "
                    "commerce_order_item_total_guard IMMEDIATE"
                )

        self.event.price_per_photo_kopecks = 99000
        self.event.save(update_fields=["price_per_photo_kopecks"])
        order.refresh_from_db()

        self.assertEqual(
            order.items.aggregate(total=Sum("line_total_kopecks"))["total"], order.total_kopecks
        )
        self.assertEqual(
            list(order.items.values_list("unit_price_kopecks", flat=True)), [30000, 30000]
        )

    def test_order_item_total_mismatch_is_rejected_when_an_atomic_snapshot_commits(self) -> None:
        """A stored Order total that differs from its lines would charge the wrong amount."""
        with self.assertRaises(DatabaseError), transaction.atomic():
            order = self.make_order(public_number="FM-TTALMJSN", total_kopecks=60000)
            self.make_item(order=order, photo=self.photo)
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS commerce_order_item_total_guard IMMEDIATE")

    def test_itemless_order_total_is_rejected_when_an_atomic_snapshot_commits(self) -> None:
        """A positive order without lines would be a charge with no purchased photos."""
        with self.assertRaisesRegex(
            DatabaseError, "order total must equal its immutable line totals"
        ):
            with transaction.atomic():
                self.make_order(public_number="FM-ABCD2345")
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS commerce_order_insert_total_guard IMMEDIATE")

    def test_paid_order_items_protect_their_photo_and_event_from_deletion(self) -> None:
        """Deleting purchased records would orphan an earned original-download entitlement."""
        order = self.make_complete_order()
        order.status = Order.Status.PAID
        order.paid_at = timezone.now()
        order.save(update_fields=["status", "paid_at"])

        with self.assertRaises(ProtectedError):
            self.photo.delete()
        with self.assertRaises(ProtectedError):
            self.event.delete()

    def test_order_creation_requires_matching_emails_and_an_exact_public_number(self) -> None:
        """A divergent initial delivery address or guessable reference breaks the order contract."""
        with self.assertRaises(DatabaseError), transaction.atomic():
            Order.objects.create(
                public_number="FM-ABCDEFGH",
                event=self.event,
                checkout_email="buyer@example.test",
                delivery_email="other@example.test",
                total_kopecks=30000,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(
                public_number="FM-INVALID0",
                event=self.event,
                checkout_email="buyer@example.test",
                total_kopecks=30000,
            )

    def test_payment_attempts_lock_the_order_amount_and_allow_only_one_pending_attempt(
        self,
    ) -> None:
        """A second active payment can charge the customer twice or incorrectly."""
        order = self.make_complete_order()
        attempt = self.make_attempt(order=order)

        self.assertEqual(attempt.amount_kopecks, order.total_kopecks)
        self.assertEqual(attempt.currency, "RUB")
        self.assertEqual(
            {choice for choice, _label in PaymentAttempt.Status.choices},
            {"pending", "succeeded", "canceled", "expired", "failed", "conflict"},
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_attempt(order=order, idempotency_key="checkout-attempt-2")

        attempt.status = PaymentAttempt.Status.FAILED
        attempt.terminal_at = timezone.now()
        attempt.save(update_fields=["status", "terminal_at"])
        replacement = self.make_attempt(order=order, idempotency_key="checkout-attempt-2")
        self.assertEqual(replacement.status, PaymentAttempt.Status.PENDING)

        replacement.amount_kopecks = 1
        with self.assertRaisesRegex(ValidationError, "immutable"):
            replacement.save()

    def test_payment_attempt_persistence_rejects_an_amount_or_currency_not_in_its_order(
        self,
    ) -> None:
        """A payment attempt must never make an immutable Order payable for another amount."""
        order = self.make_complete_order(public_number="FM-PAYMJSMN")

        with self.assertRaises(DatabaseError), transaction.atomic():
            PaymentAttempt.objects.create(
                order=order,
                amount_kopecks=1,
                currency="RUB",
                adapter_key="test-gateway",
                idempotency_key="mismatched-amount",
            )
        with self.assertRaises(DatabaseError), transaction.atomic():
            PaymentAttempt.objects.create(
                order=order,
                amount_kopecks=30000,
                currency="USD",
                adapter_key="test-gateway",
                idempotency_key="mismatched-currency",
            )

    def test_payment_attempts_keep_idempotency_and_provider_references_unique(self) -> None:
        """Reusing either identifier could attach another order to the wrong provider payment."""
        first_order = self.make_complete_order()
        second_order = self.make_complete_order(public_number="FM-SECNDPRQ")
        self.make_attempt(
            order=first_order,
            idempotency_key="same-idempotency",
            provider_payment_id="provider-payment-1",
            status=PaymentAttempt.Status.SUCCEEDED,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_attempt(
                order=second_order,
                idempotency_key="same-idempotency",
                status=PaymentAttempt.Status.SUCCEEDED,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_attempt(
                order=second_order,
                idempotency_key="second-idempotency",
                provider_payment_id="provider-payment-1",
                status=PaymentAttempt.Status.SUCCEEDED,
                adapter_key="other-gateway",
            )

    def test_provider_response_fields_can_be_populated_once_but_not_rewritten(self) -> None:
        """Hosted-payment details arrive after creation but are immutable afterward."""
        attempt = self.make_attempt(order=self.make_complete_order(public_number="FM-PRVDRWRT"))
        first_expiry = timezone.now() + timedelta(minutes=15)

        attempt.provider_payment_id = "provider-payment-1"
        attempt.confirmation_url = "https://gateway.example.test/pay/1"
        attempt.expires_at = first_expiry
        attempt.save(update_fields=["provider_payment_id", "confirmation_url", "expires_at"])

        attempt.provider_payment_id = "provider-payment-2"
        with self.assertRaisesRegex(ValidationError, "write-once"):
            attempt.save(update_fields=["provider_payment_id"])
        with self.assertRaises(DatabaseError), transaction.atomic():
            PaymentAttempt.objects.filter(pk=attempt.pk).update(
                confirmation_url="https://gateway.example.test/pay/2"
            )

    def test_payment_evidence_is_append_only_without_raw_callback_or_access_columns(self) -> None:
        """Editing provider facts or retaining a bearer undermines payment and privacy."""
        order = self.make_complete_order()
        attempt = self.make_attempt(order=order)
        evidence = PaymentEvidence.objects.create(
            payment_attempt=attempt,
            source=PaymentEvidence.Source.NOTIFICATION,
            provider_event_id="gateway-event-1",
            normalized_status=PaymentAttempt.Status.SUCCEEDED,
            amount_kopecks=30000,
            currency="RUB",
            observed_at=timezone.now(),
        )

        self.assertEqual(
            {choice for choice, _label in PaymentEvidence.Source.choices},
            {"notification", "status_fetch"},
        )
        evidence.normalized_status = PaymentAttempt.Status.FAILED
        with self.assertRaisesRegex(ValidationError, "append-only"):
            evidence.save()
        with self.assertRaisesRegex(ValidationError, "append-only"):
            evidence.delete()

        stored_columns = {
            field.name
            for model in (Order, PaymentAttempt, PaymentEvidence)
            for field in model._meta.local_fields
        }
        self.assertTrue(
            {
                "raw_callback",
                "callback_payload",
                "authorization_header",
                "card_number",
                "access_secret",
                "access_token",
                "signed_url",
            }.isdisjoint(stored_columns)
        )

    def test_payment_evidence_preserves_an_authenticated_observed_currency_code(self) -> None:
        """Forcing received USD evidence to RUB would erase why automatic fulfillment was denied."""
        order = self.make_complete_order(public_number="FM-EVDUSDAA")
        attempt = self.make_attempt(order=order, idempotency_key="observed-currency-attempt")

        evidence = PaymentEvidence.objects.create(
            payment_attempt=attempt,
            source=PaymentEvidence.Source.NOTIFICATION,
            provider_event_id="gateway-event-usd",
            normalized_status=PaymentAttempt.Status.SUCCEEDED,
            amount_kopecks=30000,
            currency="USD",
            observed_at=timezone.now(),
        )

        self.assertEqual(evidence.currency, "USD")
        evidence.currency = "usd"
        with self.assertRaises(ValidationError):
            evidence.full_clean()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentEvidence.objects.create(
                payment_attempt=attempt,
                source=PaymentEvidence.Source.STATUS_FETCH,
                provider_event_id="gateway-event-invalid-currency",
                normalized_status=PaymentAttempt.Status.SUCCEEDED,
                amount_kopecks=30000,
                currency="usd",
                observed_at=timezone.now(),
            )

    def test_database_guards_reject_queryset_and_direct_sql_mutation_or_deletion(self) -> None:
        """Bulk ORM and SQL paths must not bypass immutable commercial facts."""
        order = self.make_order(public_number="FM-DURABLE2")
        item = self.make_item(order=order)
        attempt = self.make_attempt(order=order)
        evidence = PaymentEvidence.objects.create(
            payment_attempt=attempt,
            source=PaymentEvidence.Source.NOTIFICATION,
            provider_event_id="gateway-event-immutable",
            normalized_status=PaymentAttempt.Status.PENDING,
            amount_kopecks=30000,
            currency="RUB",
            observed_at=timezone.now(),
        )

        with self.assertRaises(DatabaseError), transaction.atomic():
            Order.objects.filter(pk=order.pk).update(total_kopecks=1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            OrderItem.objects.filter(pk=item.pk).update(unit_price_kopecks=1)
        with self.assertRaises(DatabaseError), transaction.atomic():
            PaymentAttempt.objects.filter(pk=attempt.pk).update(amount_kopecks=1)
        evidence.normalized_status = PaymentAttempt.Status.FAILED
        with self.assertRaises(DatabaseError), transaction.atomic():
            PaymentEvidence.objects.bulk_update([evidence], ["normalized_status"])
        with self.assertRaises(DatabaseError), transaction.atomic():
            PaymentEvidence.objects.filter(pk=evidence.pk).delete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            OrderItem.objects.filter(pk=item.pk).delete()

        with self.assertRaises(DatabaseError), transaction.atomic():
            empty_order = self.make_order(public_number="FM-NUDELETE")
            Order.objects.filter(pk=empty_order.pk).delete()
        bare_attempt = self.make_attempt(
            order=self.make_complete_order(public_number="FM-ATTMPTDL"),
            idempotency_key="delete-attempt",
        )
        with self.assertRaises(DatabaseError), transaction.atomic():
            PaymentAttempt.objects.filter(pk=bare_attempt.pk).delete()
        with self.assertRaises(DatabaseError), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE commerce_paymentevidence SET normalized_status = %s WHERE id = %s",
                    [PaymentAttempt.Status.FAILED, evidence.pk],
                )

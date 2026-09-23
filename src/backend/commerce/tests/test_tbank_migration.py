from uuid import uuid4

from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class BankInitiationMigrationTests(TransactionTestCase):
    def test_previous_simulator_attempts_and_evidence_survive_upgrade(self):
        previous = [("commerce", "0009_rename_commerce_indexes")]
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(previous)
            apps = executor.loader.project_state(previous).apps
            Event = apps.get_model("picflow", "Event")
            Photo = apps.get_model("picflow", "Photo")
            Order = apps.get_model("commerce", "Order")
            Item = apps.get_model("commerce", "OrderItem")
            Attempt = apps.get_model("commerce", "PaymentAttempt")
            Evidence = apps.get_model("commerce", "PaymentEvidence")
            now = timezone.now()
            event = Event.objects.create(
                name="Upgrade",
                slug="upgrade",
                city="Moscow",
                start_date=now.date(),
                end_date=now.date(),
            )
            photo = Photo.objects.create(id="upgrade-photo", event=event, src="photo.jpg")
            for index, status in enumerate(("succeeded", "pending", "failed", "pending")):
                with transaction.atomic():
                    order = Order.objects.create(
                        event=event,
                        public_number=f"FM-UPGRADE{index + 2}",
                        originating_cart_token_sha256=str(index) * 64,
                        purchase_browser_token_sha256="a" * 64,
                        checkout_email="buyer@example.test",
                        delivery_email="buyer@example.test",
                        total_kopecks=100,
                        status="paid" if status == "succeeded" else "pending",
                        paid_at=now if status == "succeeded" else None,
                    )
                    Item.objects.create(
                        order=order,
                        photo=photo,
                        photo_public_id=photo.pk,
                        unit_price_kopecks=100,
                        line_total_kopecks=100,
                    )
                    attempt = Attempt.objects.create(
                        order=order,
                        amount_kopecks=100,
                        adapter_key="feature-payment-simulator-v1",
                        idempotency_key=f"attempt-{index}",
                        provider_payment_id=f"payment-{index}",
                        status=status,
                        terminal_at=now if status in ("succeeded", "failed") else None,
                        reconciliation_state="processing" if index == 3 else "pending",
                        reconciliation_lease_id=uuid4() if index == 3 else None,
                        reconciliation_lease_expires_at=now if index == 3 else None,
                        reconciliation_next_attempt_at=now,
                    )
                    Evidence.objects.create(
                        payment_attempt=attempt,
                        source="notification",
                        normalized_status=status,
                        amount_kopecks=100,
                        currency="RUB",
                        observed_at=now,
                    )
            before = list(Attempt.objects.order_by("pk").values())
            evidence = list(Evidence.objects.order_by("pk").values())
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            apps = executor.loader.project_state(latest).apps
            upgraded = apps.get_model("commerce", "PaymentAttempt")
            after = list(upgraded.objects.order_by("pk").values())
            for row in after:
                self.assertIsNone(row.pop("initiation_started_at"))
            self.assertEqual(before, after)
            self.assertEqual(
                evidence,
                list(apps.get_model("commerce", "PaymentEvidence").objects.order_by("pk").values()),
            )
        finally:
            MigrationExecutor(connection).migrate(latest)

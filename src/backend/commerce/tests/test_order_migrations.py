from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, TransactionTestCase


class OrderMigrationDefinitionTests(TestCase):
    """The breaks caught here would ship an order model without its durable database contract."""

    def test_order_payment_migration_is_linear_and_declares_the_snapshot_constraints(self) -> None:
        loader = MigrationLoader(connection)
        migration = loader.get_migration("commerce", "0002_orders_and_payments")
        state = loader.project_state([("commerce", "0002_orders_and_payments")])

        self.assertEqual(migration.dependencies, [("commerce", "0001_initial")])
        order = state.apps.get_model("commerce", "Order")
        item = state.apps.get_model("commerce", "OrderItem")
        attempt = state.apps.get_model("commerce", "PaymentAttempt")
        evidence = state.apps.get_model("commerce", "PaymentEvidence")

        self.assertTrue(
            {
                "public_number",
                "event",
                "checkout_email",
                "delivery_email",
                "total_kopecks",
                "currency",
                "status",
                "paid_at",
                "created_at",
            }.issubset({field.name for field in order._meta.local_fields})
        )
        self.assertTrue(
            {
                "order",
                "photo",
                "photo_public_id",
                "unit_price_kopecks",
                "quantity",
                "line_total_kopecks",
            }.issubset({field.name for field in item._meta.local_fields})
        )
        self.assertTrue(
            {
                "order",
                "amount_kopecks",
                "currency",
                "adapter_key",
                "idempotency_key",
                "provider_payment_id",
                "confirmation_url",
                "expires_at",
                "status",
                "terminal_at",
            }.issubset({field.name for field in attempt._meta.local_fields})
        )
        self.assertTrue(
            {
                "payment_attempt",
                "source",
                "provider_event_id",
                "normalized_status",
                "amount_kopecks",
                "currency",
                "observed_at",
                "created_at",
            }.issubset({field.name for field in evidence._meta.local_fields})
        )
        self.assertEqual(
            {constraint.name for constraint in order._meta.constraints},
            {
                "commerce_order_currency_rub_chk",
                "commerce_order_status_chk",
                "commerce_order_total_positive_chk",
                "commerce_order_paid_time_chk",
                "commerce_order_public_number_format_chk",
            },
        )
        self.assertEqual(
            {constraint.name for constraint in item._meta.constraints},
            {
                "commerce_order_item_order_photo_uniq",
                "commerce_order_item_quantity_one_chk",
                "commerce_order_item_line_total_chk",
                "commerce_order_item_photo_public_id_chk",
            },
        )
        self.assertEqual(
            {constraint.name for constraint in attempt._meta.constraints},
            {
                "commerce_payment_attempt_currency_rub_chk",
                "commerce_payment_attempt_status_chk",
                "commerce_payment_attempt_amount_positive_chk",
                "commerce_payment_attempt_terminal_time_chk",
                "commerce_payment_attempt_idempotency_uniq",
                "commerce_payment_attempt_provider_uniq",
                "commerce_payment_attempt_one_pending_uniq",
            },
        )
        self.assertEqual(
            {constraint.name for constraint in evidence._meta.constraints},
            {
                "commerce_payment_evidence_source_chk",
                "commerce_payment_evidence_status_chk",
                "commerce_payment_evidence_currency_rub_chk",
                "commerce_payment_evidence_amount_positive_chk",
            },
        )
        self.assertEqual(
            {index.name for index in order._meta.indexes},
            {"commerce_order_event_status_idx"},
        )
        self.assertEqual(
            {index.name for index in attempt._meta.indexes},
            {"commerce_payment_attempt_order_status_idx"},
        )
        self.assertEqual(
            {index.name for index in evidence._meta.indexes},
            {"commerce_payment_evidence_attempt_time_idx"},
        )
        provider_constraint = next(
            constraint
            for constraint in attempt._meta.constraints
            if constraint.name == "commerce_payment_attempt_provider_uniq"
        )
        self.assertEqual(provider_constraint.fields, ("provider_payment_id",))
        database_guard_sql = "\n".join(
            str(operation.sql)
            for operation in migration.operations
            if getattr(operation, "sql", None)
        )
        self.assertIn("commerce_order_item_photo_event_guard_trg", database_guard_sql)
        self.assertIn("commerce_order_insert_total_guard", database_guard_sql)

    def test_payment_evidence_currency_migration_preserves_normalized_observed_codes(self) -> None:
        """Received mismatch currency remains durable evidence; payable currency does not widen."""
        loader = MigrationLoader(connection)
        migration = loader.get_migration("commerce", "0005_observed_payment_evidence_currency")
        state = loader.project_state([("commerce", "0005_observed_payment_evidence_currency")])
        evidence = state.apps.get_model("commerce", "PaymentEvidence")

        self.assertEqual(
            migration.dependencies, [("commerce", "0004_order_originating_cart_digest")]
        )
        self.assertEqual(
            {constraint.name for constraint in evidence._meta.constraints},
            {
                "commerce_payment_evidence_source_chk",
                "commerce_payment_evidence_status_chk",
                "commerce_payment_evidence_currency_code_chk",
                "commerce_payment_evidence_amount_positive_chk",
            },
        )


class OrderMigrationDatabaseTests(TransactionTestCase):
    """The breaks caught here would let direct database writes bypass commercial invariants."""

    migrate_from = [("commerce", "0001_initial")]
    migrate_to = [("commerce", "0002_orders_and_payments")]

    def tearDown(self) -> None:
        try:
            MigrationExecutor(connection).migrate(
                MigrationExecutor(connection).loader.graph.leaf_nodes()
            )
        finally:
            super().tearDown()

    def test_forward_migration_creates_database_guards_for_order_and_attempt_states(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT to_regprocedure(%s) IS NULL",
                ["commerce_validate_order_total(bigint)"],
            )
            self.assertTrue(cursor.fetchone()[0])
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        apps = executor.loader.project_state(self.migrate_to).apps
        Event = apps.get_model("picflow", "Event")
        Photo = apps.get_model("picflow", "Photo")
        Order = apps.get_model("commerce", "Order")
        OrderItem = apps.get_model("commerce", "OrderItem")
        PaymentAttempt = apps.get_model("commerce", "PaymentAttempt")

        event = Event.objects.create(
            name="Migrated order event",
            slug="migrated-order-event",
            start_date="2026-08-20",
            end_date="2026-08-20",
            city="Moscow",
            access_type="paid",
            price_per_photo_kopecks=30000,
        )
        other_event = Event.objects.create(
            name="Migrated other order event",
            slug="migrated-other-order-event",
            start_date="2026-08-20",
            end_date="2026-08-20",
            city="Moscow",
            access_type="paid",
            price_per_photo_kopecks=30000,
        )
        photo = Photo.objects.create(
            id="migrated-order-photo",
            event=event,
            src="photos/migrated-order.jpg",
        )

        with transaction.atomic():
            order = Order.objects.create(
                public_number="FM-MJGRATED",
                event=event,
                checkout_email="buyer@example.test",
                delivery_email="buyer@example.test",
                total_kopecks=30000,
                currency="RUB",
                status="pending",
            )
            OrderItem.objects.create(
                order=order,
                photo=photo,
                photo_public_id=photo.pk,
                unit_price_kopecks=30000,
                quantity=1,
                line_total_kopecks=30000,
            )
            PaymentAttempt.objects.create(
                order=order,
                amount_kopecks=30000,
                currency="RUB",
                adapter_key="test-gateway",
                idempotency_key="migrated-attempt",
                status="pending",
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "SET CONSTRAINTS commerce_order_insert_total_guard, "
                    "commerce_order_item_total_guard IMMEDIATE"
                )

        with (
            self.assertRaisesRegex(DatabaseError, "referenced by an order item"),
            transaction.atomic(),
        ):
            Photo.objects.filter(pk=photo.pk).update(event=other_event)

        with self.assertRaisesRegex(
            DatabaseError, "order total must equal its immutable line totals"
        ):
            with transaction.atomic():
                Order.objects.create(
                    public_number="FM-EMPT2345",
                    event=event,
                    checkout_email="buyer@example.test",
                    delivery_email="buyer@example.test",
                    total_kopecks=30000,
                    currency="RUB",
                    status="pending",
                )
                with connection.cursor() as cursor:
                    cursor.execute("SET CONSTRAINTS commerce_order_insert_total_guard IMMEDIATE")

        with self.assertRaises(DatabaseError), transaction.atomic():
            PaymentAttempt.objects.create(
                order=order,
                amount_kopecks=1,
                currency="RUB",
                adapter_key="test-gateway",
                idempotency_key="migrated-mismatched-attempt",
                status="pending",
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(
                public_number="FM-INVALID0",
                event=event,
                checkout_email="buyer@example.test",
                delivery_email="buyer@example.test",
                total_kopecks=30000,
                currency="RUB",
                status="pending",
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(
                public_number="FM-MJGRAUSD",
                event=event,
                checkout_email="buyer@example.test",
                delivery_email="buyer@example.test",
                total_kopecks=30000,
                currency="USD",
                status="pending",
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            PaymentAttempt.objects.create(
                order=order,
                amount_kopecks=30000,
                currency="RUB",
                adapter_key="test-gateway",
                idempotency_key="migrated-second-attempt",
                status="pending",
            )

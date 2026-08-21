from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase


class CheckoutMigrationDefinitionTests(TestCase):
    """The break caught here would lose the durable Order-to-cart correlation contract."""

    def test_originating_cart_digest_migration_is_linear_and_declares_exact_guards(self) -> None:
        loader = MigrationLoader(connection)
        migration = loader.get_migration("commerce", "0004_order_originating_cart_digest")
        state = loader.project_state([("commerce", "0004_order_originating_cart_digest")])
        order = state.apps.get_model("commerce", "Order")

        self.assertEqual(migration.dependencies, [("commerce", "0003_fulfillment")])
        field = order._meta.get_field("originating_cart_token_sha256")
        self.assertEqual(field.max_length, 64)
        self.assertTrue(field.default)
        self.assertEqual(
            {constraint.name for constraint in order._meta.constraints}
            - {
                "commerce_order_currency_rub_chk",
                "commerce_order_status_chk",
                "commerce_order_total_positive_chk",
                "commerce_order_paid_time_chk",
                "commerce_order_public_number_format_chk",
                "commerce_order_purchase_digest_chk",
            },
            {
                "commerce_order_origin_cart_digest_chk",
                "commerce_order_pending_origin_uniq",
            },
        )
        self.assertIn(
            "commerce_order_origin_cart_idx",
            {index.name for index in order._meta.indexes},
        )
        guard_sql = "\n".join(
            str(operation.sql)
            for operation in migration.operations
            if getattr(operation, "sql", None)
        )
        self.assertIn("commerce_order_origin_cart_guard_trg", guard_sql)

import django.db.models.deletion
from django.db import migrations, models

import commerce.order_numbers


class Migration(migrations.Migration):
    dependencies = [("commerce", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="Order",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "public_number",
                    models.CharField(
                        default=commerce.order_numbers.generate_order_public_number,
                        max_length=11,
                        unique=True,
                    ),
                ),
                ("checkout_email", models.EmailField(max_length=254)),
                ("delivery_email", models.EmailField(max_length=254)),
                ("total_kopecks", models.PositiveIntegerField()),
                ("currency", models.CharField(default="RUB", max_length=3)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("superseded", "Superseded"),
                            ("paid", "Paid"),
                            ("canceled", "Canceled"),
                        ],
                        default="pending",
                        max_length=10,
                    ),
                ),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "event",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="orders",
                        to="picflow.event",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["event", "status"], name="commerce_order_event_status_idx")
                ],
            },
        ),
        migrations.CreateModel(
            name="OrderItem",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("photo_public_id", models.CharField(max_length=32)),
                ("unit_price_kopecks", models.PositiveIntegerField()),
                ("quantity", models.PositiveSmallIntegerField(default=1)),
                ("line_total_kopecks", models.PositiveIntegerField()),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="items",
                        to="commerce.order",
                    ),
                ),
                (
                    "photo",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="order_items",
                        to="picflow.photo",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="PaymentAttempt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("amount_kopecks", models.PositiveIntegerField()),
                ("currency", models.CharField(default="RUB", max_length=3)),
                ("adapter_key", models.CharField(max_length=64)),
                ("idempotency_key", models.CharField(max_length=128)),
                ("provider_payment_id", models.CharField(blank=True, max_length=255)),
                ("confirmation_url", models.URLField(blank=True, max_length=2000)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("succeeded", "Succeeded"),
                            ("canceled", "Canceled"),
                            ("expired", "Expired"),
                            ("failed", "Failed"),
                            ("conflict", "Conflict"),
                        ],
                        default="pending",
                        max_length=10,
                    ),
                ),
                ("terminal_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="payment_attempts",
                        to="commerce.order",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["order", "status"],
                        name="commerce_payment_attempt_order_status_idx",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="PaymentEvidence",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("notification", "Notification"),
                            ("status_fetch", "Status fetch"),
                        ],
                        max_length=16,
                    ),
                ),
                ("provider_event_id", models.CharField(blank=True, max_length=255)),
                (
                    "normalized_status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("succeeded", "Succeeded"),
                            ("canceled", "Canceled"),
                            ("expired", "Expired"),
                            ("failed", "Failed"),
                            ("conflict", "Conflict"),
                        ],
                        max_length=10,
                    ),
                ),
                ("amount_kopecks", models.PositiveIntegerField()),
                ("currency", models.CharField(default="RUB", max_length=3)),
                ("observed_at", models.DateTimeField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "payment_attempt",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="evidence",
                        to="commerce.paymentattempt",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["payment_attempt", "observed_at"],
                        name="commerce_payment_evidence_attempt_time_idx",
                    )
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.CheckConstraint(
                condition=models.Q(("currency", "RUB")), name="commerce_order_currency_rub_chk"
            ),
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.CheckConstraint(
                condition=models.Q(("status__in", ("pending", "superseded", "paid", "canceled"))),
                name="commerce_order_status_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.CheckConstraint(
                condition=models.Q(("total_kopecks__gt", 0)),
                name="commerce_order_total_positive_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "public_number__regex",
                        "^FM-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}$",
                    )
                ),
                name="commerce_order_public_number_format_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.CheckConstraint(
                condition=models.Q(("paid_at__isnull", False), ("status", "paid"))
                | models.Q(
                    ("paid_at__isnull", True),
                    ("status__in", ("pending", "superseded", "canceled")),
                ),
                name="commerce_order_paid_time_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.UniqueConstraint(
                fields=("order", "photo"), name="commerce_order_item_order_photo_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.CheckConstraint(
                condition=models.Q(("quantity", 1)), name="commerce_order_item_quantity_one_chk"
            ),
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.CheckConstraint(
                condition=models.Q(("line_total_kopecks", models.F("unit_price_kopecks"))),
                name="commerce_order_item_line_total_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="orderitem",
            constraint=models.CheckConstraint(
                condition=models.Q(("photo_public_id", models.F("photo"))),
                name="commerce_order_item_photo_public_id_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(("currency", "RUB")),
                name="commerce_payment_attempt_currency_rub_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "status__in",
                        ("pending", "succeeded", "canceled", "expired", "failed", "conflict"),
                    )
                ),
                name="commerce_payment_attempt_status_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount_kopecks__gt", 0)),
                name="commerce_payment_attempt_amount_positive_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentattempt",
            constraint=models.CheckConstraint(
                condition=models.Q(("status", "pending"), ("terminal_at__isnull", True))
                | models.Q(
                    ("status__in", ("succeeded", "canceled", "expired", "failed", "conflict")),
                    ("terminal_at__isnull", False),
                ),
                name="commerce_payment_attempt_terminal_time_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentattempt",
            constraint=models.UniqueConstraint(
                fields=("idempotency_key",), name="commerce_payment_attempt_idempotency_uniq"
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentattempt",
            constraint=models.UniqueConstraint(
                condition=~models.Q(("provider_payment_id", "")),
                fields=("provider_payment_id",),
                name="commerce_payment_attempt_provider_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentattempt",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "pending")),
                fields=("order",),
                name="commerce_payment_attempt_one_pending_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentevidence",
            constraint=models.CheckConstraint(
                condition=models.Q(("source__in", ("notification", "status_fetch"))),
                name="commerce_payment_evidence_source_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentevidence",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "normalized_status__in",
                        ("pending", "succeeded", "canceled", "expired", "failed", "conflict"),
                    )
                ),
                name="commerce_payment_evidence_status_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentevidence",
            constraint=models.CheckConstraint(
                condition=models.Q(("currency", "RUB")),
                name="commerce_payment_evidence_currency_rub_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentevidence",
            constraint=models.CheckConstraint(
                condition=models.Q(("amount_kopecks__gt", 0)),
                name="commerce_payment_evidence_amount_positive_chk",
            ),
        ),
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION commerce_guard_order_insert() RETURNS trigger AS $$
                BEGIN
                    IF NEW.delivery_email IS DISTINCT FROM NEW.checkout_email THEN
                        RAISE EXCEPTION 'initial order delivery email must match checkout email'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER commerce_order_insert_guard_trg
                    BEFORE INSERT ON commerce_order
                    FOR EACH ROW
                    EXECUTE FUNCTION commerce_guard_order_insert();

                CREATE FUNCTION commerce_guard_order_immutability() RETURNS trigger AS $$
                BEGIN
                    IF TG_OP = 'DELETE' THEN
                        RAISE EXCEPTION 'orders are immutable and cannot be deleted'
                            USING ERRCODE = '23514';
                    END IF;
                    IF OLD.public_number IS DISTINCT FROM NEW.public_number
                       OR OLD.event_id IS DISTINCT FROM NEW.event_id
                       OR OLD.checkout_email IS DISTINCT FROM NEW.checkout_email
                       OR OLD.total_kopecks IS DISTINCT FROM NEW.total_kopecks
                       OR OLD.currency IS DISTINCT FROM NEW.currency
                       OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
                        RAISE EXCEPTION 'order commercial snapshot is immutable'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER commerce_order_immutability_guard_trg
                    BEFORE UPDATE OR DELETE ON commerce_order
                    FOR EACH ROW
                    EXECUTE FUNCTION commerce_guard_order_immutability();

                CREATE FUNCTION commerce_guard_order_item() RETURNS trigger AS $$
                BEGIN
                    IF TG_OP <> 'INSERT' THEN
                        RAISE EXCEPTION 'order items are immutable and cannot be changed or deleted'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1
                        FROM commerce_order AS commerce_order
                        JOIN picflow_photo AS photo ON photo.id = NEW.photo_id
                        WHERE commerce_order.id = NEW.order_id
                          AND commerce_order.event_id = photo.event_id
                    ) THEN
                        RAISE EXCEPTION 'order item photo must belong to the order event'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER commerce_order_item_guard_trg
                    BEFORE INSERT OR UPDATE OR DELETE ON commerce_orderitem
                    FOR EACH ROW
                    EXECUTE FUNCTION commerce_guard_order_item();

                CREATE FUNCTION commerce_guard_order_item_photo_event() RETURNS trigger AS $$
                BEGIN
                    IF NEW.event_id IS DISTINCT FROM OLD.event_id
                       AND EXISTS (
                           SELECT 1
                           FROM commerce_orderitem
                           WHERE photo_id = OLD.id
                       ) THEN
                        RAISE EXCEPTION
                            'photo event is immutable while referenced by an order item'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER commerce_order_item_photo_event_guard_trg
                    BEFORE UPDATE OF event_id ON picflow_photo
                    FOR EACH ROW
                    EXECUTE FUNCTION commerce_guard_order_item_photo_event();

                CREATE FUNCTION commerce_validate_order_total(target_order_id bigint)
                RETURNS void AS $$
                DECLARE
                    expected_total integer;
                    line_total bigint;
                BEGIN
                    SELECT total_kopecks INTO expected_total
                    FROM commerce_order
                    WHERE id = target_order_id;
                    SELECT COALESCE(SUM(line_total_kopecks), 0) INTO line_total
                    FROM commerce_orderitem
                    WHERE order_id = target_order_id;
                    IF expected_total IS DISTINCT FROM line_total THEN
                        RAISE EXCEPTION 'order total must equal its immutable line totals'
                            USING ERRCODE = '23514';
                    END IF;
                END;
                $$ LANGUAGE plpgsql;

                CREATE FUNCTION commerce_validate_order_item_total() RETURNS trigger AS $$
                BEGIN
                    PERFORM commerce_validate_order_total(COALESCE(NEW.order_id, OLD.order_id));
                    RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;

                CREATE FUNCTION commerce_validate_order_insert_total() RETURNS trigger AS $$
                BEGIN
                    PERFORM commerce_validate_order_total(NEW.id);
                    RETURN NULL;
                END;
                $$ LANGUAGE plpgsql;

                CREATE CONSTRAINT TRIGGER commerce_order_item_total_guard
                    AFTER INSERT OR UPDATE OR DELETE ON commerce_orderitem
                    DEFERRABLE INITIALLY DEFERRED
                    FOR EACH ROW
                    EXECUTE FUNCTION commerce_validate_order_item_total();

                CREATE CONSTRAINT TRIGGER commerce_order_insert_total_guard
                    AFTER INSERT ON commerce_order
                    DEFERRABLE INITIALLY DEFERRED
                    FOR EACH ROW
                    EXECUTE FUNCTION commerce_validate_order_insert_total();

                CREATE FUNCTION commerce_guard_payment_attempt() RETURNS trigger AS $$
                BEGIN
                    IF TG_OP = 'DELETE' THEN
                        RAISE EXCEPTION 'payment attempts are immutable and cannot be deleted'
                            USING ERRCODE = '23514';
                    END IF;
                    IF NOT EXISTS (
                        SELECT 1
                        FROM commerce_order
                        WHERE id = NEW.order_id
                          AND total_kopecks = NEW.amount_kopecks
                          AND currency = NEW.currency
                    ) THEN
                        RAISE EXCEPTION 'payment attempt amount and currency must match its order'
                            USING ERRCODE = '23514';
                    END IF;
                    IF TG_OP = 'UPDATE' THEN
                        IF OLD.order_id IS DISTINCT FROM NEW.order_id
                           OR OLD.amount_kopecks IS DISTINCT FROM NEW.amount_kopecks
                           OR OLD.currency IS DISTINCT FROM NEW.currency
                           OR OLD.adapter_key IS DISTINCT FROM NEW.adapter_key
                           OR OLD.idempotency_key IS DISTINCT FROM NEW.idempotency_key
                           OR OLD.created_at IS DISTINCT FROM NEW.created_at THEN
                            RAISE EXCEPTION 'payment attempt commercial identity is immutable'
                                USING ERRCODE = '23514';
                        END IF;
                        IF (OLD.provider_payment_id <> ''
                            AND OLD.provider_payment_id IS DISTINCT FROM NEW.provider_payment_id)
                           OR (OLD.confirmation_url <> ''
                            AND OLD.confirmation_url IS DISTINCT FROM NEW.confirmation_url)
                           OR (OLD.expires_at IS NOT NULL
                            AND OLD.expires_at IS DISTINCT FROM NEW.expires_at) THEN
                            RAISE EXCEPTION
                                'payment attempt provider response fields are write-once'
                                USING ERRCODE = '23514';
                        END IF;
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER commerce_payment_attempt_guard_trg
                    BEFORE INSERT OR UPDATE OR DELETE ON commerce_paymentattempt
                    FOR EACH ROW
                    EXECUTE FUNCTION commerce_guard_payment_attempt();

                CREATE FUNCTION commerce_guard_payment_evidence() RETURNS trigger AS $$
                BEGIN
                    RAISE EXCEPTION 'payment evidence is append-only'
                        USING ERRCODE = '23514';
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER commerce_payment_evidence_guard_trg
                    BEFORE UPDATE OR DELETE ON commerce_paymentevidence
                    FOR EACH ROW
                    EXECUTE FUNCTION commerce_guard_payment_evidence();
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS commerce_payment_evidence_guard_trg
                    ON commerce_paymentevidence;
                DROP FUNCTION IF EXISTS commerce_guard_payment_evidence();
                DROP TRIGGER IF EXISTS commerce_payment_attempt_guard_trg
                    ON commerce_paymentattempt;
                DROP FUNCTION IF EXISTS commerce_guard_payment_attempt();
                DROP TRIGGER IF EXISTS commerce_order_item_photo_event_guard_trg
                    ON picflow_photo;
                DROP FUNCTION IF EXISTS commerce_guard_order_item_photo_event();
                DROP TRIGGER IF EXISTS commerce_order_insert_total_guard
                    ON commerce_order;
                DROP FUNCTION IF EXISTS commerce_validate_order_insert_total();
                DROP TRIGGER IF EXISTS commerce_order_item_total_guard
                    ON commerce_orderitem;
                DROP FUNCTION IF EXISTS commerce_validate_order_item_total();
                DROP FUNCTION IF EXISTS commerce_validate_order_total(bigint);
                DROP TRIGGER IF EXISTS commerce_order_item_guard_trg
                    ON commerce_orderitem;
                DROP FUNCTION IF EXISTS commerce_guard_order_item();
                DROP TRIGGER IF EXISTS commerce_order_immutability_guard_trg
                    ON commerce_order;
                DROP FUNCTION IF EXISTS commerce_guard_order_immutability();
                DROP TRIGGER IF EXISTS commerce_order_insert_guard_trg
                    ON commerce_order;
                DROP FUNCTION IF EXISTS commerce_guard_order_insert();
            """,
        ),
    ]

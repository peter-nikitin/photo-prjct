import django.core.validators
from django.db import migrations, models

import commerce.models


class Migration(migrations.Migration):
    dependencies = [("commerce", "0003_fulfillment")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="originating_cart_token_sha256",
            field=models.CharField(
                default=commerce.models.generate_unclaimed_originating_cart_digest,
                max_length=64,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Browser token digest must be 64 lowercase hexadecimal characters.",
                        regex="^[0-9a-f]{64}$",
                    )
                ],
            ),
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.CheckConstraint(
                condition=models.Q(originating_cart_token_sha256__regex=r"^[0-9a-f]{64}$"),
                name="commerce_order_origin_cart_digest_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="order",
            constraint=models.UniqueConstraint(
                fields=("event", "originating_cart_token_sha256"),
                condition=models.Q(status="pending"),
                name="commerce_order_pending_origin_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="order",
            index=models.Index(
                fields=("event", "originating_cart_token_sha256"),
                name="commerce_order_origin_cart_idx",
            ),
        ),
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION commerce_guard_order_origin_cart() RETURNS trigger AS $$
                BEGIN
                    IF OLD.originating_cart_token_sha256 IS DISTINCT FROM
                            NEW.originating_cart_token_sha256 THEN
                        RAISE EXCEPTION 'order originating cart digest is immutable'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER commerce_order_origin_cart_guard_trg
                    BEFORE UPDATE ON commerce_order
                    FOR EACH ROW
                    EXECUTE FUNCTION commerce_guard_order_origin_cart();
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS commerce_order_origin_cart_guard_trg ON commerce_order;
                DROP FUNCTION IF EXISTS commerce_guard_order_origin_cart();
            """,
        ),
    ]

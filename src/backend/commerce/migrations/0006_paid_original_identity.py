from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("commerce", "0005_observed_payment_evidence_currency")]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE FUNCTION commerce_guard_paid_order_item_photo_original_identity()
                RETURNS trigger AS $$
                BEGIN
                    IF (
                        NEW.original_key IS DISTINCT FROM OLD.original_key
                        OR NEW.original_content_type IS DISTINCT FROM OLD.original_content_type
                    ) AND EXISTS (
                        SELECT 1
                        FROM commerce_orderitem
                        JOIN commerce_order ON commerce_order.id = commerce_orderitem.order_id
                        WHERE commerce_orderitem.photo_id = OLD.id
                          AND commerce_order.status = 'paid'
                    ) THEN
                        RAISE EXCEPTION
                            'photo original identity is immutable while referenced by a paid '
                            'order item'
                            USING ERRCODE = '23514';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;

                CREATE TRIGGER commerce_paid_order_item_photo_original_identity_guard_trg
                    BEFORE UPDATE OF original_key, original_content_type ON picflow_photo
                    FOR EACH ROW
                    EXECUTE FUNCTION commerce_guard_paid_order_item_photo_original_identity();
            """,
            reverse_sql="""
                DROP TRIGGER IF EXISTS commerce_paid_order_item_photo_original_identity_guard_trg
                    ON picflow_photo;
                DROP FUNCTION IF EXISTS commerce_guard_paid_order_item_photo_original_identity();
            """,
        )
    ]

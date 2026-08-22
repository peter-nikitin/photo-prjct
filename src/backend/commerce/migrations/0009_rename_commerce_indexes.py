from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("commerce", "0008_commerce_attention_permission")]

    operations = [
        migrations.RenameIndex(
            model_name="order",
            old_name="commerce_order_event_status_idx",
            new_name="cm_order_event_status_idx",
        ),
        migrations.RenameIndex(
            model_name="paymentattempt",
            old_name="commerce_payment_attempt_order_status_idx",
            new_name="cm_pay_attempt_ord_status_idx",
        ),
        migrations.RenameIndex(
            model_name="paymentevidence",
            old_name="commerce_payment_evidence_attempt_time_idx",
            new_name="cm_pay_evidence_att_time_idx",
        ),
        migrations.RenameIndex(
            model_name="orderaccessgrant",
            old_name="commerce_access_grant_order_active_idx",
            new_name="cm_access_grant_ord_active_idx",
        ),
        migrations.RenameIndex(
            model_name="emaildelivery",
            old_name="commerce_email_delivery_ready_idx",
            new_name="cm_email_delivery_ready_idx",
        ),
        migrations.RenameIndex(
            model_name="commerceattention",
            old_name="commerce_attention_open_reminder_idx",
            new_name="cm_attention_open_reminder_idx",
        ),
        migrations.RenameIndex(
            model_name="downloadgrantaudit",
            old_name="commerce_download_audit_item_time_idx",
            new_name="cm_download_audit_item_tm_idx",
        ),
        migrations.RenameIndex(
            model_name="paymentattempt",
            old_name="commerce_payment_reconcile_due_idx",
            new_name="cm_pay_reconcile_due_idx",
        ),
    ]

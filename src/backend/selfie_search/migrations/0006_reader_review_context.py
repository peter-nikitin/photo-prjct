from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("selfie_search", "0005_optional_feedback_contact")]
    operations = [
        migrations.AddField(
            model_name="selfiesearch",
            name="reader_staff_eligible",
            field=models.BooleanField(default=False, editable=False),
        ),
        migrations.AddField(
            model_name="selfiesearch",
            name="reader_comparison_requested",
            field=models.BooleanField(default=False, editable=False),
        ),
    ]

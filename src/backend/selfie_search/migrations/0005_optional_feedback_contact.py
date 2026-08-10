from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("selfie_search", "0003_optional_feedback_contact"),
        ("selfie_search", "0004_remove_selfie_search_candidate"),
    ]

    operations: list[migrations.operations.base.Operation] = []

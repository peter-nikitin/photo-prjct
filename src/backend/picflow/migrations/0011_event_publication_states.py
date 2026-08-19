from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("picflow", "0010_event_face_search_generation")]

    operations = [
        migrations.AlterField(
            model_name="event",
            name="publication_status",
            field=models.CharField(
                choices=[
                    ("unavailable", "Недоступно"),
                    ("draft", "Черновик"),
                    ("published", "Опубликовано"),
                ],
                db_default="unavailable",
                default="unavailable",
                max_length=12,
            ),
        ),
    ]

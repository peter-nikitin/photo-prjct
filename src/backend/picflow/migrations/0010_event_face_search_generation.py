from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("picflow", "0009_eventfolder_photo_folder")]

    operations = [
        migrations.AddField(
            model_name="event",
            name="face_search_generation",
            field=models.CharField(
                choices=[("sface_v3", "SFace v3"), ("adaface_v5", "AdaFace v5")],
                db_default="sface_v3",
                default="sface_v3",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="event",
            name="face_search_generation",
            field=models.CharField(
                choices=[("sface_v3", "SFace v3"), ("adaface_v5", "AdaFace v5")],
                db_default="adaface_v5",
                default="adaface_v5",
                max_length=16,
            ),
        ),
    ]

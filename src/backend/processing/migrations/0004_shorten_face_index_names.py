from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("processing", "0003_add_preview_derivative_schema"),
    ]

    operations = [
        migrations.RenameIndex(
            model_name="photofacedetection",
            old_name="proc_face_detection_attempt_idx",
            new_name="proc_face_det_attempt_idx",
        ),
        migrations.RenameIndex(
            model_name="faceembedding",
            old_name="proc_face_embedding_detection_idx",
            new_name="proc_face_embed_det_idx",
        ),
    ]

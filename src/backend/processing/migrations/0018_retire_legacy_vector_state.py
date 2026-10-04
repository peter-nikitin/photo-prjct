"""Declare current state; physical contraction is an explicit post-activation step.

The canonical deployment runs migrate while old web is still active. Purging rows
or changing vector typmod here would break that writer before candidate activation.
Run retire_legacy_face_vectors only after the release safeguards are satisfied.
"""

from django.db import migrations, models
from django.db.models.lookups import Exact
from pgvector.django import VectorField


class Migration(migrations.Migration):
    dependencies = [("processing", "0017_retire_json_face_embedding")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="faceembeddingvector",
                    name="vector",
                    field=VectorField(dimensions=512),
                ),
                migrations.RemoveConstraint(
                    model_name="faceembeddingvector", name="proc_vector_model_dimension"
                ),
                migrations.AddConstraint(
                    model_name="faceembeddingvector",
                    constraint=models.CheckConstraint(
                        condition=models.Q(model_version="adaface-ir18-webface4m")
                        & Exact(
                            models.Func(
                                "vector", function="vector_dims", output_field=models.IntegerField()
                            ),
                            models.Value(512),
                        ),
                        name="proc_vector_model_dimension",
                    ),
                ),
            ],
        ),
    ]

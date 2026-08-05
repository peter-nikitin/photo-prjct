import math
import uuid

import django.db.models.deletion
from django.db import migrations, models


def _copy_direct_evidence(apps, schema_editor) -> None:  # noqa: ARG001
    Result = apps.get_model("selfie_search", "SelfieSearchResult")
    DirectEvidence = apps.get_model("selfie_search", "SelfieSearchDirectEvidence")
    for result in Result.objects.all().iterator():
        distance = float(result.cosine_distance)
        if not math.isfinite(distance) or not 0 <= distance <= 2:
            raise RuntimeError("Cannot convert a result with an invalid cosine distance.")
        if not result.detection_id:
            raise RuntimeError("Cannot convert a result without truthful detection evidence.")
        DirectEvidence.objects.create(
            result_id=result.pk,
            detection_id=result.detection_id,
            cosine_distance=distance,
        )


def _restore_direct_evidence(apps, schema_editor) -> None:  # noqa: ARG001
    Result = apps.get_model("selfie_search", "SelfieSearchResult")
    DirectEvidence = apps.get_model("selfie_search", "SelfieSearchDirectEvidence")
    for evidence in DirectEvidence.objects.all().iterator():
        result = Result.objects.get(pk=evidence.result_id)
        result.detection_id = evidence.detection_id
        result.cosine_distance = evidence.cosine_distance
        result.save(update_fields=["detection", "cosine_distance"])


class Migration(migrations.Migration):
    dependencies = [
        ("picflow", "0006_photo_processing_policy"),
        ("processing", "0006_face_cluster_corpus"),
        ("selfie_search", "0002_selfiesearchfeedback_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="SelfieSearchDirectEvidence",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("cosine_distance", models.FloatField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="SelfieSearchClusterEvidence",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("representative_distance", models.FloatField()),
                ("source_order", models.PositiveIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddField(
            model_name="selfiesearch",
            name="cluster_configuration_hash",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="selfiesearch",
            name="cluster_corpus",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="selfie_searches",
                to="processing.faceclustercorpus",
            ),
        ),
        migrations.AddField(
            model_name="selfiesearch",
            name="cluster_corpus_version",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="selfiesearch",
            name="cluster_expanded_photo_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="selfiesearch",
            name="cluster_expansion_outcome",
            field=models.CharField(
                blank=True,
                choices=[
                    ("expanded", "Expanded"),
                    ("no_strong_anchor", "No strong anchor"),
                    ("no_new_photos", "No new photos"),
                    ("corpus_unavailable", "Corpus unavailable"),
                    ("corpus_incompatible", "Corpus incompatible"),
                    ("disabled", "Disabled"),
                ],
                max_length=24,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="selfiesearch",
            name="direct_matched_photo_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="selfiesearch",
            name="final_matched_photo_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="selfiesearch",
            name="expanded_cluster_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="selfiesearch",
            name="strong_anchor_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="selfiesearchresult",
            name="primary_source",
            field=models.CharField(
                choices=[
                    ("direct", "Direct"),
                    ("face_cluster_expansion", "Face-cluster expansion"),
                ],
                default="direct",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="selfiesearchclusterevidence",
            name="anchor_detection",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="selfie_search_anchor_evidence",
                to="processing.photofacedetection",
            ),
        ),
        migrations.AddField(
            model_name="selfiesearchclusterevidence",
            name="anchor_result",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cluster_anchor_evidence",
                to="selfie_search.selfiesearchresult",
            ),
        ),
        migrations.AddField(
            model_name="selfiesearchclusterevidence",
            name="cluster",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="selfie_search_evidence",
                to="processing.facecluster",
            ),
        ),
        migrations.AddField(
            model_name="selfiesearchclusterevidence",
            name="corpus",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="selfie_search_evidence",
                to="processing.faceclustercorpus",
            ),
        ),
        migrations.AddField(
            model_name="selfiesearchclusterevidence",
            name="member_detection",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="selfie_search_member_evidence",
                to="processing.photofacedetection",
            ),
        ),
        migrations.AddField(
            model_name="selfiesearchclusterevidence",
            name="result",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cluster_evidence",
                to="selfie_search.selfiesearchresult",
            ),
        ),
        migrations.AddField(
            model_name="selfiesearchdirectevidence",
            name="detection",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="selfie_search_direct_evidence",
                to="processing.photofacedetection",
            ),
        ),
        migrations.AddField(
            model_name="selfiesearchdirectevidence",
            name="result",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="direct_evidence",
                to="selfie_search.selfiesearchresult",
            ),
        ),
        migrations.RunPython(_copy_direct_evidence, _restore_direct_evidence),
        migrations.RemoveField(model_name="selfiesearchresult", name="cosine_distance"),
        migrations.RemoveField(model_name="selfiesearchresult", name="detection"),
        migrations.AddConstraint(
            model_name="selfiesearch",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("cluster_corpus_version__isnull", True),
                    ("cluster_corpus_version__gte", 1),
                    _connector="OR",
                ),
                name="selfie_search_cluster_version_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearch",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("cluster_expansion_outcome__isnull", True),
                    (
                        "cluster_expansion_outcome__in",
                        (
                            "expanded",
                            "no_strong_anchor",
                            "no_new_photos",
                            "corpus_unavailable",
                            "corpus_incompatible",
                            "disabled",
                        ),
                    ),
                    _connector="OR",
                ),
                name="selfie_search_cluster_outcome_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearch",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("direct_matched_photo_count__isnull", True),
                    ("cluster_expanded_photo_count__isnull", True),
                    (
                        "final_matched_photo_count",
                        models.F("direct_matched_photo_count")
                        + models.F("cluster_expanded_photo_count"),
                    ),
                    _connector="OR",
                ),
                name="selfie_search_result_count_identity_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchresult",
            constraint=models.CheckConstraint(
                condition=models.Q(("primary_source__in", ("direct", "face_cluster_expansion"))),
                name="selfie_result_primary_source_chk",
            ),
        ),
        migrations.AddIndex(
            model_name="selfiesearchclusterevidence",
            index=models.Index(
                fields=["result", "source_order"], name="selfie_cluster_evidence_order_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchclusterevidence",
            constraint=models.UniqueConstraint(
                fields=("result", "corpus", "cluster"),
                name="selfie_cluster_evidence_result_cluster_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchclusterevidence",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("representative_distance__gte", 0),
                    ("representative_distance__lte", 2),
                ),
                name="selfie_cluster_evidence_distance_chk",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchclusterevidence",
            constraint=models.CheckConstraint(
                condition=models.Q(("source_order__gte", 1)),
                name="selfie_cluster_evidence_order_chk",
            ),
        ),
        migrations.AddIndex(
            model_name="selfiesearchdirectevidence",
            index=models.Index(fields=["detection"], name="selfie_direct_evidence_det_idx"),
        ),
        migrations.AddConstraint(
            model_name="selfiesearchdirectevidence",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("cosine_distance__gte", 0),
                    ("cosine_distance__lte", 2),
                ),
                name="selfie_direct_evidence_distance_chk",
            ),
        ),
    ]

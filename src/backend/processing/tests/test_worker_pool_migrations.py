"""Worker coordination expands a populated deployed pgvector schema without rewriting evidence."""

from datetime import date

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from feature_flags.models import FeatureFlag

pytestmark = [pytest.mark.migration, pytest.mark.django_db(transaction=True)]


def test_populated_pgvector_baseline_survives_worker_schema_and_flag_sync():
    baseline = [
        ("processing", "0011_pgvector_face_embeddings"),
        ("selfie_search", "0006_reader_review_context"),
    ]
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    assert executor.loader.detect_conflicts() == {}
    assert [node for node in leaves if node[0] == "processing"] == [
        ("processing", "0017_retire_json_face_embedding")
    ]
    try:
        executor.migrate(baseline)
        apps = executor.loader.project_state(baseline).apps
        Event = apps.get_model("picflow", "Event")
        Photo = apps.get_model("picflow", "Photo")
        event = Event.objects.create(
            name="Deployed vector",
            slug="deployed-vector",
            start_date=date.today(),
            end_date=date.today(),
        )
        photo = Photo.objects.create(id="deployed-vector-photo", event=event, src="local.jpg")
        fields = dict(
            event=event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="a" * 64,
        )
        Run = apps.get_model("processing", "EventProcessingRun")
        Job = apps.get_model("processing", "ProcessingJob")
        Attempt = apps.get_model("processing", "ProcessingAttempt")
        run = Run.objects.create(**fields)
        job = Job.objects.create(
            **fields, run=run, photo=photo, input_fingerprint={}, status="succeeded"
        )
        attempt = Attempt.objects.create(
            **{key: value for key, value in fields.items() if key != "configuration_hash"},
            run=run,
            job=job,
            photo=photo,
            status="succeeded",
            accepted=True,
            terminal_at=timezone.now(),
        )
        artifact = apps.get_model("processing", "FaceProcessingAttemptArtifact").objects.create(
            attempt=attempt
        )
        for index, (model, dimensions) in enumerate(
            (("sface", 128), ("adaface-ir18-webface4m", 512))
        ):
            detection = apps.get_model("processing", "PhotoFaceDetection").objects.create(
                attempt=attempt,
                artifact=artifact,
                face_index=index,
                status="kept",
            )
            vector = [1.0] + [0.0] * (dimensions - 1)
            for name in ("FaceEmbedding", "FaceEmbeddingVector"):
                apps.get_model("processing", name).objects.create(
                    detection=detection,
                    model_version=model,
                    vector=vector,
                    metadata={"baseline": True},
                )
        Search = apps.get_model("selfie_search", "SelfieSearch")
        search = Search.objects.create(
            event=event,
            status="ready",
            public_token_digest="b" * 64,
            temporary_object_key="",
            configuration={"frozen": True},
            reader_staff_eligible=True,
            reader_comparison_requested=False,
        )
        search_job = apps.get_model("selfie_search", "SelfieSearchJob").objects.create(
            search=search,
            configuration=search.configuration,
            status="succeeded",
        )
        apps.get_model("selfie_search", "SelfieSearchAttempt").objects.create(
            job=search_job,
            status="succeeded",
            terminal_at=timezone.now(),
        )
        result = apps.get_model("selfie_search", "SelfieSearchResult").objects.create(
            search=search,
            photo=photo,
            rank=1,
        )
        apps.get_model("selfie_search", "SelfieSearchDirectEvidence").objects.create(
            result=result,
            detection=detection,
            cosine_distance=0.0,
        )
        flag = FeatureFlag.objects.create(key="pgvector-face-search-read", state="on")
        names = [
            ("processing", name)
            for name in (
                "EventProcessingRun",
                "ProcessingJob",
                "ProcessingAttempt",
                "FaceProcessingAttemptArtifact",
                "PhotoFaceDetection",
                "FaceEmbedding",
                "FaceEmbeddingVector",
            )
        ] + [
            ("selfie_search", name)
            for name in (
                "SelfieSearch",
                "SelfieSearchJob",
                "SelfieSearchAttempt",
                "SelfieSearchResult",
                "SelfieSearchDirectEvidence",
            )
        ]

        def snapshot(registry, app, name):
            rows = list(registry.get_model(app, name).objects.order_by("pk").values())
            if name == "ProcessingAttempt":
                for row in rows:
                    row.pop("pool_member_id", None)
                    row.pop("worker_build", None)
            if name == "FaceEmbeddingVector":
                for row in rows:
                    row["vector"] = list(row["vector"])
            if name == "SelfieSearch":
                for row in rows:
                    row.pop("reader_staff_eligible", None)
                    row.pop("reader_comparison_requested", None)
            return rows

        before = {pair: snapshot(apps, *pair) for pair in names}
        executor = MigrationExecutor(connection)
        executor.migrate(leaves)
        current = executor.loader.project_state(leaves).apps
        for pair in names:
            if pair == ("processing", "FaceEmbedding"):
                assert snapshot(apps, *pair) == before[pair]
                continue
            assert snapshot(current, *pair) == before[pair]
        with pytest.raises(LookupError):
            current.get_model("processing", "FaceEmbedding")
        assert current.get_model("processing", "WorkerPool").objects.count() == 0
        assert current.get_model("processing", "WorkerPoolMember").objects.count() == 0
        call_command("sync_feature_flags")
        assert not FeatureFlag.objects.filter(pk=flag.pk).exists()
    finally:
        MigrationExecutor(connection).migrate(leaves)

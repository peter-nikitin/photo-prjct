"""Schema expansion preserves previous-version evidence and pending work."""

from datetime import date, timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = [pytest.mark.migration, pytest.mark.django_db(transaction=True)]


def test_previous_processing_and_search_rows_survive_vector_schema_expansion():
    previous = [
        ("processing", "0010_face_projection_generation_index"),
        ("selfie_search", "0005_optional_feedback_contact"),
    ]
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps

        def model(name):
            return apps.get_model("processing", name)

        Event = apps.get_model("picflow", "Event")
        Photo = apps.get_model("picflow", "Photo")
        Search = apps.get_model("selfie_search", "SelfieSearch")
        event = Event.objects.create(
            name="Migration", slug="migration", start_date=date.today(), end_date=date.today()
        )
        photo = Photo.objects.create(id="migration-photo", event=event, src="local.jpg")
        Photo.objects.create(id="never-enrolled", event=event, src="local.jpg")
        fields = dict(
            event=event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="a" * 64,
        )

        for i, status in enumerate(
            ("succeeded", "failed", "expired", "stale", "in_progress", "in_progress")
        ):
            job_fields = fields | {"configuration_hash": str(i) * 64}
            run = model("EventProcessingRun").objects.create(**job_fields)
            job = model("ProcessingJob").objects.create(
                **job_fields,
                run=run,
                photo=photo,
                input_fingerprint={},
                status="retry_wait"
                if status == "failed"
                else "processing"
                if status == "in_progress"
                else "succeeded",
            )
            attempt = model("ProcessingAttempt").objects.create(
                **{k: v for k, v in fields.items() if k != "configuration_hash"},
                run=run,
                job=job,
                photo=photo,
                status=status,
                accepted=status == "succeeded",
                terminal_at=None if status == "in_progress" else timezone.now(),
                lease_expires_at=timezone.now() + timedelta(seconds=120 if i == 4 else -120),
            )
            if status == "succeeded":
                artifact = model("FaceProcessingAttemptArtifact").objects.create(attempt=attempt)
                for index, (version, dimensions) in enumerate(
                    (("sface", 128), ("adaface-ir18-webface4m", 512))
                ):
                    detection = model("PhotoFaceDetection").objects.create(
                        attempt=attempt, artifact=artifact, face_index=index, status="kept"
                    )
                    if version == "sface":
                        cluster_detection = detection
                    model("FaceEmbedding").objects.create(
                        detection=detection,
                        model_version=version,
                        vector=[1.0] + [0.0] * (dimensions - 1),
                    )
                model("PhotoFaceEmbeddingProjection").objects.create(
                    photo=photo,
                    accepted_attempt=attempt,
                    contract_version=1,
                    processor_version=1,
                    configuration_hash=job.configuration_hash,
                )
        for index, status in enumerate(("queued", "ready", "failed", "cleanup_pending")):
            search = Search.objects.create(
                event=event,
                status=status,
                public_token_digest=str(index) * 64,
                temporary_object_key="" if status == "ready" else "local-only",
                configuration={"frozen": True},
            )
            if status == "ready":
                result = apps.get_model("selfie_search", "SelfieSearchResult").objects.create(
                    search=search, photo=photo, rank=1
                )
                apps.get_model("selfie_search", "SelfieSearchDirectEvidence").objects.create(
                    result=result, detection=detection, cosine_distance=0.1
                )
        corpus = model("FaceClusterCorpus").objects.create(
            event=event,
            version=1,
            algorithm_version="guarded-graph-v1",
            configuration={},
            configuration_hash="b" * 64,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            model_version="sface",
            embedding_dimensions=128,
            edge_threshold=0.2,
            representative_threshold=0.2,
            distance_block_size=100,
            max_candidate_edges=100,
            input_count=1,
            cluster_count=1,
            member_count=1,
        )
        cluster = model("FaceCluster").objects.create(
            event=event,
            corpus=corpus,
            cluster_key="c" * 64,
            representative_detection=cluster_detection,
            member_count=1,
        )
        model("FaceClusterMember").objects.create(
            event=event,
            corpus=corpus,
            cluster=cluster,
            detection=cluster_detection,
            member_index=0,
            distance_to_representative=0.0,
        )
        model("FaceClusterCorpus").objects.filter(pk=corpus.pk).update(
            status="published",
            published_at=timezone.now(),
        )
        model("EventFaceClusterActivation").objects.create(
            event=event,
            corpus=corpus,
            anchor_threshold=0.2,
            configuration={},
            configuration_hash="d" * 64,
            approved_evaluation_report_hash="e" * 64,
        )
        saved = {
            name: list(apps.get_model("selfie_search", name).objects.order_by("pk").values())
            for name in ("SelfieSearchResult", "SelfieSearchDirectEvidence")
        }
        names = (
            "ProcessingJob",
            "ProcessingAttempt",
            "FaceProcessingAttemptArtifact",
            "PhotoFaceDetection",
            "FaceEmbedding",
            "PhotoFaceEmbeddingProjection",
            "FaceClusterCorpus",
            "FaceCluster",
            "FaceClusterMember",
            "EventFaceClusterActivation",
        )
        before = {name: list(model(name).objects.order_by("pk").values()) for name in names}
        for row in before["ProcessingAttempt"]:
            row.pop("worker_build")
        before_search = list(Search.objects.order_by("pk").values())
        executor = MigrationExecutor(connection)
        executor.migrate(leaves)
        current = executor.loader.project_state(leaves).apps
        for name in names:
            if name == "FaceEmbedding":
                # State retirement leaves historical rows physically intact until post-commit.
                rows = list(model(name).objects.order_by("pk").values())
            else:
                rows = list(current.get_model("processing", name).objects.order_by("pk").values())
            if name == "ProcessingAttempt":
                assert all(row.pop("pool_member_id") is None for row in rows)
            assert rows == before[name]
        with pytest.raises(LookupError):
            current.get_model("processing", "FaceEmbedding")
        rows = list(
            current.get_model("selfie_search", "SelfieSearch").objects.order_by("pk").values()
        )
        assert rows == before_search
        for name, values in saved.items():
            assert (
                list(current.get_model("selfie_search", name).objects.order_by("pk").values())
                == values
            )
        assert current.get_model("processing", "FaceEmbeddingVector").objects.count() == 0
        assert Photo.objects.filter(pk="never-enrolled").exists()
    finally:
        MigrationExecutor(connection).migrate(leaves)

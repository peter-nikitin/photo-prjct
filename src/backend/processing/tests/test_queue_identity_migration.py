"""Removing release identity preserves durable work, leases and accepted artifacts."""

from datetime import date, timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = [pytest.mark.migration, pytest.mark.django_db(transaction=True)]


def test_previous_attempts_and_evidence_survive_release_identity_removal():
    previous = [("processing", "0015_bulk_member_attempts")]
    executor = MigrationExecutor(connection)
    leaves = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate(previous)
        apps = executor.loader.project_state(previous).apps

        def model(name):
            return apps.get_model("processing", name)

        event = apps.get_model("picflow", "Event").objects.create(
            name="Independent images",
            slug="independent-images",
            start_date=date.today(),
            end_date=date.today(),
        )
        Photo = apps.get_model("picflow", "Photo")
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
            photo = Photo.objects.create(id=f"image-migration-{i}", event=event, src="local.jpg")
            job_fields = fields | {"configuration_hash": str(i) * 64}
            run = model("EventProcessingRun").objects.create(**job_fields)
            job = model("ProcessingJob").objects.create(
                **job_fields,
                run=run,
                photo=photo,
                input_fingerprint={"size": 10},
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
                worker_build="previous-image",
                status=status,
                accepted=status == "succeeded",
                input_fingerprint=job.input_fingerprint,
                terminal_at=None if status == "in_progress" else timezone.now(),
                claimed_at=timezone.now(),
                heartbeat_at=timezone.now(),
                lease_expires_at=timezone.now() + timedelta(seconds=120 if i == 4 else -120),
                result={"model": "sface"} if status == "succeeded" else {},
            )
            model("PhotoProcessingState").objects.create(
                photo=photo,
                processor_type="face_embedding",
                current_job=job,
                current_attempt=attempt,
                status="processing" if status == "in_progress" else "succeeded",
            )
            if status == "succeeded":
                artifact = model("FaceProcessingAttemptArtifact").objects.create(attempt=attempt)
                detection = model("PhotoFaceDetection").objects.create(
                    attempt=attempt,
                    artifact=artifact,
                    face_index=0,
                    status="kept",
                )
                for name in ("FaceEmbedding", "FaceEmbeddingVector"):
                    model(name).objects.create(
                        detection=detection,
                        model_version="sface",
                        vector=[1.0] + [0.0] * 127,
                        metadata={"accepted": True},
                    )
                preview_fields = fields | {
                    "processor_type": "generate_preview",
                    "configuration_hash": "f" * 64,
                }
                preview_run = model("EventProcessingRun").objects.create(**preview_fields)
                preview_job = model("ProcessingJob").objects.create(
                    **preview_fields,
                    run=preview_run,
                    photo=photo,
                    status="succeeded",
                )
                preview_attempt = model("ProcessingAttempt").objects.create(
                    **{k: v for k, v in preview_fields.items() if k != "configuration_hash"},
                    run=preview_run,
                    job=preview_job,
                    photo=photo,
                    worker_build="previous-image",
                    status="succeeded",
                    accepted=True,
                    terminal_at=timezone.now(),
                )
                model("PhotoDerivative").objects.create(
                    photo=photo,
                    accepted_attempt=preview_attempt,
                    variant="preview-small-v1",
                    final_key="accepted-preview",
                    byte_size=10,
                    content_type="image/jpeg",
                    width=10,
                    height=10,
                    oriented_source_width=10,
                    oriented_source_height=10,
                    sha256="a" * 64,
                )
        names = (
            "EventProcessingRun",
            "ProcessingJob",
            "ProcessingAttempt",
            "PhotoProcessingState",
            "FaceProcessingAttemptArtifact",
            "PhotoFaceDetection",
            "FaceEmbedding",
            "FaceEmbeddingVector",
            "PhotoDerivative",
        )

        def snapshot(registry, name):
            rows = list(registry.get_model("processing", name).objects.order_by("pk").values())
            for row in rows:
                row.pop("worker_build", None)
                if name == "FaceEmbeddingVector":
                    row["vector"] = list(row["vector"])
            return rows

        before = {name: snapshot(apps, name) for name in names}
        photo_rows = list(Photo.objects.order_by("pk").values())
        executor = MigrationExecutor(connection)
        executor.migrate(leaves)
        current = executor.loader.project_state(leaves).apps
        assert "worker_build" not in {
            field.name
            for field in current.get_model("processing", "ProcessingAttempt")._meta.fields
        }
        for name in names:
            if name == "FaceEmbedding":
                assert snapshot(apps, name) == before[name]
                continue
            assert snapshot(current, name) == before[name]
        with pytest.raises(LookupError):
            current.get_model("processing", "FaceEmbedding")
        assert (
            list(current.get_model("picflow", "Photo").objects.order_by("pk").values())
            == photo_rows
        )
        from processing.services.jobs import recover_expired_attempts

        assert len(recover_expired_attempts()) == 1
        assert (
            current.get_model("processing", "ProcessingAttempt")
            .objects.filter(status="expired")
            .count()
            == 2
        )
    finally:
        MigrationExecutor(connection).migrate(leaves)

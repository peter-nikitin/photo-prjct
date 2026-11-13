from unittest.mock import patch

from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone
from selfie_search.tests.test_submission import GalleryPhotoSubmissionTests


class SelfieSearchMigrationTests(TransactionTestCase):
    migrate_from = [("selfie_search", "0002_selfiesearchfeedback_and_more")]
    migrate_to = [("selfie_search", "0005_optional_feedback_contact")]

    def test_schema_migrates_forward_and_back_without_errors(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        migrated_apps = executor.loader.project_state(self.migrate_to).apps
        for model_name in (
            "selfiesearch",
            "selfiesearchjob",
            "selfiesearchattempt",
            "selfiesearchresult",
            "selfiesearchdirectevidence",
            "selfiesearchclusterevidence",
            "selfiesearchfeedback",
            "selfiesearchfeedbacklabel",
            "selfiesearchfeedbackaccessaudit",
        ):
            self.assertIn(model_name, migrated_apps.all_models["selfie_search"])

        reverse_executor = MigrationExecutor(connection)
        reverse_executor.migrate(self.migrate_from)
        reverted_apps = reverse_executor.loader.project_state(self.migrate_from).apps
        self.assertIn("selfie_search", reverted_apps.all_models)
        self.assertNotIn("selfiesearchdirectevidence", reverted_apps.all_models["selfie_search"])
        self.assertNotIn("selfiesearchclusterevidence", reverted_apps.all_models["selfie_search"])
        self.assertIn("selfiesearchcandidate", reverted_apps.all_models["selfie_search"])
        restorer = MigrationExecutor(connection)
        restorer.migrate(restorer.loader.graph.leaf_nodes())


class SelfieSearchOptionalContactRecoveryMigrationTests(TransactionTestCase):
    migrate_from = [("selfie_search", "0003_optional_feedback_contact")]
    migrate_to = [("selfie_search", "0005_optional_feedback_contact")]

    def test_historical_optional_contact_frontier_upgrades_without_reapplying_its_ddl(self) -> None:
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)

        forward_executor = MigrationExecutor(connection)
        forward_executor.migrate(self.migrate_to)
        migrated_apps = forward_executor.loader.project_state(self.migrate_to).apps
        Feedback = migrated_apps.get_model("selfie_search", "SelfieSearchFeedback")

        self.assertTrue(Feedback._meta.get_field("contact").blank)
        self.assertNotIn(
            "selfie_feedback_contact_nonempty_chk",
            {constraint.name for constraint in Feedback._meta.constraints},
        )

        restorer = MigrationExecutor(connection)
        restorer.migrate(restorer.loader.graph.leaf_nodes())


class SelfieSearchResultProvenanceDataMigrationTests(TransactionTestCase):
    migrate_from = [("selfie_search", "0002_selfiesearchfeedback_and_more")]
    migrate_to = [("selfie_search", "0004_remove_selfie_search_candidate")]

    def test_existing_direct_result_is_converted_without_sentinels(self) -> None:
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(leaves))
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        Event = old_apps.get_model("picflow", "Event")
        Photo = old_apps.get_model("picflow", "Photo")
        Search = old_apps.get_model("selfie_search", "SelfieSearch")
        Result = old_apps.get_model("selfie_search", "SelfieSearchResult")
        Run = old_apps.get_model("processing", "EventProcessingRun")
        Job = old_apps.get_model("processing", "ProcessingJob")
        Attempt = old_apps.get_model("processing", "ProcessingAttempt")
        Artifact = old_apps.get_model("processing", "FaceProcessingAttemptArtifact")
        Detection = old_apps.get_model("processing", "PhotoFaceDetection")

        event = Event.objects.create(
            name="Migration conversion event",
            slug="migration-conversion-event",
            start_date="2026-08-01",
            end_date="2026-08-01",
            city="Moscow",
            access_type="free",
            publication_status="published",
            description="",
        )
        photo = Photo.objects.create(id="migration-photo", event=event, src="photos/old.jpg")
        run = Run.objects.create(
            event=event,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="a" * 64,
        )
        job = Job.objects.create(
            event=event,
            run=run,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            configuration_hash="a" * 64,
            input_fingerprint={},
        )
        attempt = Attempt.objects.create(
            event=event,
            run=run,
            job=job,
            photo=photo,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            configuration={},
            input_fingerprint={},
            status="succeeded",
            terminal_at=timezone.now(),
        )
        artifact = Artifact.objects.create(attempt=attempt)
        detection = Detection.objects.create(artifact=artifact, attempt=attempt, face_index=0)
        search = Search.objects.create(
            event=event,
            public_token_digest="d" * 64,
            temporary_object_key="selfie-search/migration.jpg",
            configuration={"embedding_model": "sface-v1"},
        )
        result = Result.objects.create(
            search=search,
            photo=photo,
            detection=detection,
            rank=1,
            cosine_distance=-9.589174565505232e-08,
        )

        forward_executor = MigrationExecutor(connection)
        forward_executor.migrate(self.migrate_to)
        new_apps = forward_executor.loader.project_state(self.migrate_to).apps
        NewResult = new_apps.get_model("selfie_search", "SelfieSearchResult")
        DirectEvidence = new_apps.get_model("selfie_search", "SelfieSearchDirectEvidence")
        migrated_result = NewResult.objects.get(pk=result.pk)
        evidence = DirectEvidence.objects.get(result_id=result.pk)

        self.assertEqual(migrated_result.primary_source, "direct")
        self.assertEqual(evidence.detection_id, detection.pk)
        self.assertEqual(evidence.cosine_distance, 0.0)
        self.assertIsNone(migrated_result.search.cluster_corpus_id)
        self.assertIsNone(migrated_result.search.direct_matched_photo_count)
        self.assertIsNone(migrated_result.search.final_matched_photo_count)
        self.assertIsNone(migrated_result.search.cluster_expansion_outcome)


class GalleryQueueUpgradeTests(TransactionTestCase):
    def test_queue_fields_migration_preserves_existing_search_status(self):
        before = [("selfie_search", "0007_remove_reader_review_context")]
        after = [("selfie_search", "0008_selfiesearch_gallery_attempt_count_and_more")]
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            Event = apps.get_model("picflow", "Event")
            Search = apps.get_model("selfie_search", "SelfieSearch")
            event = Event.objects.create(
                name="Upgrade",
                slug="queue-upgrade",
                start_date="2026-11-09",
                end_date="2026-11-09",
                city="Moscow",
            )
            queued = Search.objects.create(
                event=event,
                public_token_digest="1" * 64,
                temporary_object_key="",
                configuration={"processor": "gallery_photo_query"},
            )
            ready = Search.objects.create(
                event=event,
                public_token_digest="2" * 64,
                status="ready",
                temporary_object_key="",
                configuration={"processor": "gallery_photo_query"},
                matched_photo_count=7,
            )
            MigrationExecutor(connection).migrate(after)
            new_apps = MigrationExecutor(connection).loader.project_state(after).apps
            NewSearch = new_apps.get_model("selfie_search", "SelfieSearch")
            self.assertEqual(NewSearch.objects.get(pk=queued.pk).status, "queued")
            self.assertEqual(NewSearch.objects.get(pk=ready.pk).matched_photo_count, 7)
        finally:
            MigrationExecutor(connection).migrate(leaves)


class GallerySearchTimingUpgradeTests(TransactionTestCase):
    def test_upgrade_preserves_old_results_without_fabricating_old_starts(self):
        from uuid import uuid4

        before = [("selfie_search", "0008_selfiesearch_gallery_attempt_count_and_more")]
        after = [("selfie_search", "0009_selfiesearch_gallery_started_at")]
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            Event = apps.get_model("picflow", "Event")
            Photo = apps.get_model("picflow", "Photo")
            Search = apps.get_model("selfie_search", "SelfieSearch")
            Result = apps.get_model("selfie_search", "SelfieSearchResult")
            event = Event.objects.create(
                name="Timing upgrade",
                slug="timing-upgrade",
                city="Moscow",
                start_date="2026-11-10",
                end_date="2026-11-10",
            )
            ready = Search.objects.create(
                event=event,
                public_token_digest=uuid4().hex * 2,
                temporary_object_key="",
                status="ready",
                configuration={"processor": "gallery_photo_query"},
            )
            photo = Photo.objects.create(id="timing-upgrade-photo", event=event, src="photo.jpg")
            Result.objects.create(search=ready, photo=photo, rank=1)
            saved = Search.objects.values().get()
            saved_results = list(Result.objects.values())
            MigrationExecutor(connection).migrate(after)
            new_apps = MigrationExecutor(connection).loader.project_state(after).apps
            NewSearch = new_apps.get_model("selfie_search", "SelfieSearch")
            NewResult = new_apps.get_model("selfie_search", "SelfieSearchResult")
            self.assertEqual(NewSearch.objects.values(*saved).get(), saved)
            self.assertEqual(list(NewResult.objects.values()), saved_results)
            self.assertIsNone(NewSearch.objects.get().gallery_started_at)
        finally:
            MigrationExecutor(connection).migrate(leaves)


class SharedFaceSearchUpgradeTests(TransactionTestCase):
    make_event = GalleryPhotoSubmissionTests.make_event
    make_eligible_embedding = GalleryPhotoSubmissionTests.make_eligible_embedding

    def test_upgrade_resumes_gallery_and_preserves_live_ml_leases_and_prepared_results(self):
        from datetime import timedelta
        from uuid import uuid4

        from selfie_search.models import FaceSearchTask, SelfieSearch
        from selfie_search.services.face_search_queue import run_once
        from selfie_search.services.jobs import complete_search_attempt
        from selfie_search.services.submission import _configuration, _gallery_configuration
        from selfie_search.tests.test_jobs import RecordingStorage

        GalleryPhotoSubmissionTests.setUp(self)
        photo = self.make_eligible_embedding(
            event=self.event, photo_id="migration-source", vector=[1.0] + [0.0] * 511
        )
        selfie_config = _configuration(event=self.event, content_type="image/jpeg", content_size=1)
        gallery_config = _gallery_configuration(
            event=self.event, photo=photo.detection.attempt.photo, detection_id=photo.detection_id
        )
        before = [("selfie_search", "0009_selfiesearch_gallery_started_at")]
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        now = timezone.now()
        storage = RecordingStorage()

        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            Search = apps.get_model("selfie_search", "SelfieSearch")
            Job = apps.get_model("selfie_search", "SelfieSearchJob")
            Attempt = apps.get_model("selfie_search", "SelfieSearchAttempt")
            Result = apps.get_model("selfie_search", "SelfieSearchResult")

            def make_search(configuration, *, status="queued", temporary_object_key="", **kwargs):
                return Search.objects.create(
                    event_id=self.event.pk,
                    public_token_digest=uuid4().hex * 2,
                    configuration=configuration,
                    status=status,
                    temporary_object_key=temporary_object_key,
                    **kwargs,
                )

            queued = make_search(gallery_config)
            running = make_search(gallery_config, status="processing", gallery_started_at=now)
            missing = make_search({"processor": "gallery_photo_query"})
            ready = make_search(
                gallery_config, status="ready", matched_photo_count=1, terminal_at=now
            )
            ready_result = Result.objects.create(
                search=ready, photo_id=photo.detection.attempt.photo_id, rank=1
            )
            ml_waiting = make_search(
                selfie_config, temporary_object_key=f"selfie-search/{'a' * 32}"
            )
            ml_active = make_search(
                selfie_config, status="processing", temporary_object_key=f"selfie-search/{'b' * 32}"
            )
            legacy_cleanup = make_search(
                selfie_config,
                status="cleanup_pending",
                temporary_object_key=f"selfie-search/{'c' * 32}",
                intended_terminal_status="ready",
                final_matched_photo_count=0,
                direct_matched_photo_count=0,
                cluster_expanded_photo_count=0,
                state_changed_at=now - timedelta(minutes=1),
            )
            Job.objects.create(search=ml_waiting, configuration=selfie_config)
            active_job = Job.objects.create(
                search=ml_active, configuration=selfie_config, status="processing", claimed_at=now
            )
            active_attempt = Attempt.objects.create(
                job=active_job, claimed_at=now, lease_expires_at=now + timedelta(minutes=5)
            )
            cleanup_job = Job.objects.create(
                search=legacy_cleanup, configuration=selfie_config, status="succeeded"
            )
            Attempt.objects.create(job=cleanup_job, status="succeeded", terminal_at=now)

            MigrationExecutor(connection).migrate(leaves)

            self.assertSetEqual(
                set(FaceSearchTask.objects.values_list("search_id", flat=True)),
                {queued.pk, running.pk},
            )
            self.assertEqual(SelfieSearch.objects.get(pk=running.pk).matching_started_at, now)
            self.assertEqual(SelfieSearch.objects.get(pk=missing.pk).status, "search_unavailable")
            self.assertEqual(
                SelfieSearch.objects.get(pk=ready.pk).results.get().pk, ready_result.pk
            )
            self.assertEqual(SelfieSearch.objects.get(pk=ml_waiting.pk).job.status, "queued")
            complete_search_attempt(
                active_attempt.pk,
                result={"model": "adaface-ir18-webface4m", "embedding": [1.0] + [0.0] * 511},
            )
            for _ in range(3):
                self.assertTrue(run_once())
            self.assertFalse(run_once())
            with patch(
                "selfie_search.storage.TemporarySelfieStorage.for_matching_cleanup",
                return_value=storage,
            ):
                call_command("delete_temporary_selfies")
            for search in (queued, running, ml_active):
                self.assertEqual(
                    SelfieSearch.objects.get(pk=search.pk).results.get().photo_id,
                    photo.detection.attempt.photo_id,
                )
            self.assertEqual(SelfieSearch.objects.get(pk=legacy_cleanup.pk).status, "ready")
            self.assertCountEqual(
                storage.deleted,
                [ml_active.temporary_object_key, legacy_cleanup.temporary_object_key],
            )
        finally:
            MigrationExecutor(connection).migrate(leaves)

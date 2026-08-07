from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


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

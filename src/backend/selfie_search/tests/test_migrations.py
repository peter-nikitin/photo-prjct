from unittest.mock import patch
from uuid import uuid4

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


class FaceAnchorExpansionUpgradeTests(TransactionTestCase):
    make_event = GalleryPhotoSubmissionTests.make_event
    make_photo = GalleryPhotoSubmissionTests.make_photo

    def test_existing_results_and_writes_from_old_code_keep_default_zero_expansion(self):
        from selfie_search.models import SelfieSearch

        GalleryPhotoSubmissionTests.setUp(self)
        direct = self.make_photo(event=self.event, photo_id="legacy-direct")
        cluster = self.make_photo(event=self.event, photo_id="legacy-cluster")
        before = [("selfie_search", "0012_publish_before_cleanup")]
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(leaves))
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps
        OldSearch = old_apps.get_model("selfie_search", "SelfieSearch")
        OldResult = old_apps.get_model("selfie_search", "SelfieSearchResult")

        def save_old_result():
            snapshot = OldSearch.objects.create(
                event_id=self.event.pk,
                public_token_digest=uuid4().hex * 2,
                status="ready",
                stage="matching",
                terminal_at=timezone.now(),
                matched_photo_count=2,
                direct_matched_photo_count=1,
                cluster_expanded_photo_count=1,
                final_matched_photo_count=2,
            )
            OldResult.objects.bulk_create(
                [
                    OldResult(search_id=snapshot.pk, photo_id=direct.pk, rank=1),
                    OldResult(
                        search_id=snapshot.pk,
                        photo_id=cluster.pk,
                        primary_source="face_cluster_expansion",
                        rank=2,
                    ),
                ]
            )
            return snapshot.pk

        old_search = save_old_result()
        MigrationExecutor(connection).migrate(leaves)
        old_code_write = save_old_result()
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(
                cursor, SelfieSearch._meta.db_table
            )
        self.assertNotIn("anchor_expansion_staff_eligible", {column.name for column in columns})
        for pk in (old_search, old_code_write):
            migrated = SelfieSearch.objects.get(pk=pk)
            self.assertNotIn("anchor_expansion_staff_eligible", migrated.configuration)
            self.assertEqual(migrated.anchor_expanded_photo_count, 0)
            self.assertEqual(migrated.final_matched_photo_count, 2)
            self.assertEqual(
                list(migrated.results.order_by("rank").values_list("primary_source", flat=True)),
                ["direct", "face_cluster_expansion"],
            )


class IndependentSearchFeedbackMigrationTests(TransactionTestCase):
    migrate_from = [("selfie_search", "0013_face_anchor_expansion")]
    migrate_to = [("selfie_search", "0014_independent_search_feedback")]

    def test_migrates_legacy_answers_and_preserves_sensitive_records_and_audit(self) -> None:
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(leaves))
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps

        event = old_apps.get_model("picflow", "Event").objects.create(
            name="Event with historic feedback",
            slug="historic-feedback",
            start_date="2026-08-04",
            end_date="2026-08-04",
            city="Moscow",
            publication_status="published",
        )
        staff = old_apps.get_model("auth", "User").objects.create(
            username="historic-feedback-staff", is_staff=True
        )
        search = old_apps.get_model("selfie_search", "SelfieSearch").objects.create(
            event=event,
            public_token_digest="9" * 64,
            status="ready",
            matched_photo_count=2,
            configuration={"processor": "selfie", "contract": 2},
        )
        submitted = timezone.now()
        old_feedback_model = old_apps.get_model("selfie_search", "SelfieSearchFeedback")
        selfie = old_feedback_model.objects.create(
            search=search,
            variant="result_labels",
            contact="guest@example.test",
            personal_data_consent=True,
            consent_text_version="2026-08-05",
            consented_at=submitted,
            source_status="ready",
            source_matched_photo_count=2,
            source_visible_result_count=2,
            source_configuration=search.configuration,
            object_key="feedback/historical.jpg",
            object_content_type="image/jpeg",
            object_size=850,
            object_uploaded_at=submitted,
        )
        old_results = old_apps.get_model("selfie_search", "SelfieSearchResult")
        old_labels = old_apps.get_model("selfie_search", "SelfieSearchFeedbackLabel")
        historical_labels = {}
        for rank, value in enumerate(("present", "absent"), 1):
            photo = old_apps.get_model("picflow", "Photo").objects.create(
                id=f"historic-feedback-{rank}", event=event, src=f"sample-{rank}.jpg"
            )
            result = old_results.objects.create(search=search, rank=rank, photo=photo)
            label = old_labels.objects.create(feedback=selfie, result=result, value=value)
            historical_labels[label.pk] = (
                event.pk,
                photo.pk,
                "selfie",
                value,
                {
                    "search_id": str(search.pk),
                    "result_id": str(result.pk),
                    "primary_source": result.primary_source,
                },
                selfie.pk,
                selfie.created_at,
            )
        audit = old_apps.get_model(
            "selfie_search", "SelfieSearchFeedbackAccessAudit"
        ).objects.create(feedback=selfie, staff=staff, action="contact_view")

        forward = MigrationExecutor(connection)
        forward.migrate(self.migrate_to)
        current = forward.loader.project_state(self.migrate_to).apps
        feedback = current.get_model("selfie_search", "SelfieSearchFeedback")
        labels = current.get_model("search_feedback", "SearchFeedback")
        audits = current.get_model("selfie_search", "SelfieSearchFeedbackAccessAudit")
        self.assertEqual(
            feedback.objects.values().get(pk=selfie.pk),
            old_feedback_model_to_dict(selfie) | {"variant": "selfie"},
        )
        self.assertEqual(
            {
                submission_id: tuple(fields)
                for submission_id, *fields in labels.objects.values_list(
                    "submission_id",
                    "event_id",
                    "photo_id",
                    "search_type",
                    "value",
                    "search_context",
                    "legacy_attachment_id",
                    "created_at",
                )
            },
            historical_labels,
        )
        self.assertEqual(
            audits.objects.values().get(pk=audit.pk),
            old_feedback_model_to_dict(audit),
        )


def old_feedback_model_to_dict(row):
    return {field.attname: getattr(row, field.attname) for field in row._meta.concrete_fields}


class FaceCentroidSourcesMigrationTests(TransactionTestCase):
    make_event = GalleryPhotoSubmissionTests.make_event
    make_eligible_embedding = GalleryPhotoSubmissionTests.make_eligible_embedding
    before = [("selfie_search", "0014_independent_search_feedback")]
    after = [("selfie_search", "0015_face_centroid_sources")]

    def test_preserves_saved_singleton_and_rejects_lossy_downgrade(self) -> None:
        GalleryPhotoSubmissionTests.setUp(self)
        faces = [
            self.make_eligible_embedding(
                event=self.event, photo_id=photo_id, vector=[1.0] + [0.0] * 511
            )
            for photo_id in ("historical-anchor", "other-anchor", "historical-addition")
        ]
        executor = MigrationExecutor(connection)
        leaves = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(leaves))
        executor.migrate(self.before)
        old_apps = executor.loader.project_state(self.before).apps
        Search = old_apps.get_model("selfie_search", "SelfieSearch")
        Result = old_apps.get_model("selfie_search", "SelfieSearchResult")
        DirectEvidence = old_apps.get_model("selfie_search", "SelfieSearchDirectEvidence")
        AnchorEvidence = old_apps.get_model("selfie_search", "SelfieSearchAnchorEvidence")
        search = Search.objects.create(
            event_id=self.event.pk,
            public_token_digest="f" * 64,
            status="ready",
            terminal_at=timezone.now(),
            matched_photo_count=3,
            direct_matched_photo_count=2,
            cluster_expanded_photo_count=0,
            anchor_expanded_photo_count=1,
            final_matched_photo_count=3,
        )
        results = [
            Result.objects.create(
                search=search,
                photo_id=face.detection.attempt.photo_id,
                rank=rank,
                primary_source="direct" if rank < 3 else "face_anchor_expansion",
            )
            for rank, face in enumerate(faces, start=1)
        ]
        anchors = [
            DirectEvidence.objects.create(
                result=result, detection_id=face.detection_id, cosine_distance=0.3
            )
            for result, face in zip(results[:2], faces[:2], strict=True)
        ]
        evidence = AnchorEvidence.objects.create(
            result=results[2],
            anchor=anchors[0],
            detection_id=faces[2].detection_id,
            cosine_distance=0.4,
        )
        saved_search = Search.objects.values().get(pk=search.pk)
        saved_results = list(Result.objects.filter(search=search).order_by("rank").values())
        saved_evidence = AnchorEvidence.objects.values().get(pk=evidence.pk)

        executor = MigrationExecutor(connection)
        executor.migrate(self.after)
        new_apps = executor.loader.project_state(self.after).apps
        NewSearch = new_apps.get_model("selfie_search", "SelfieSearch")
        NewResult = new_apps.get_model("selfie_search", "SelfieSearchResult")
        NewEvidence = new_apps.get_model("selfie_search", "SelfieSearchAnchorEvidence")
        Source = new_apps.get_model("selfie_search", "SelfieSearchAnchorEvidenceSource")
        self.assertEqual(NewSearch.objects.values().get(pk=search.pk), saved_search)
        self.assertEqual(
            list(NewResult.objects.filter(search_id=search.pk).order_by("rank").values()),
            saved_results,
        )
        self.assertEqual(
            NewEvidence.objects.values().get(pk=evidence.pk),
            {key: value for key, value in saved_evidence.items() if key != "anchor_id"},
        )
        self.assertEqual(
            list(
                Source.objects.filter(evidence_id=evidence.pk).values_list("anchor_id", "position")
            ),
            [(anchors[0].pk, 1)],
        )

        second = Source.objects.create(evidence_id=evidence.pk, anchor_id=anchors[1].pk, position=2)
        with self.assertRaisesRegex(RuntimeError, "exactly one source"):
            MigrationExecutor(connection).migrate(self.before)
        self.assertEqual(Source.objects.filter(evidence_id=evidence.pk).count(), 2)
        second.delete()
        Source.objects.filter(evidence_id=evidence.pk).delete()
        with self.assertRaisesRegex(RuntimeError, "exactly one source"):
            MigrationExecutor(connection).migrate(self.before)
        Source.objects.create(evidence_id=evidence.pk, anchor_id=anchors[0].pk, position=1)

        MigrationExecutor(connection).migrate(self.before)
        self.assertEqual(AnchorEvidence.objects.values().get(pk=evidence.pk), saved_evidence)

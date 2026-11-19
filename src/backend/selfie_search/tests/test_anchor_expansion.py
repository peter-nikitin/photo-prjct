import hashlib
import json
from io import StringIO
from math import sqrt
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import DatabaseError, connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from feature_flags.models import FeatureFlag
from processing.models import PhotoFaceDetection
from selfie_search.models import FaceSearchTask, SelfieSearch
from selfie_search.services.anchor_expansion import _EXPAND_SQL
from selfie_search.services.face_search_queue import run_once
from selfie_search.services.jobs import ClaimedSearchJob, claim_search_job, complete_search_attempt
from selfie_search.services.submission import submit_gallery_photo_search, submit_selfie_search
from selfie_search.tests import test_jobs
from selfie_search.tests import test_submission as fixtures


def _vector(first, axis=1):
    vector = [0.0] * 512
    vector[0] = first
    vector[axis] = sqrt(1 - first**2)
    return vector


def _pad_vector(*components: float) -> list[float]:
    return [*components, *([0.0] * (512 - len(components)))]


class AnchorExpansionTests(TestCase):
    setUp = fixtures.GalleryPhotoSubmissionTests.setUp
    make_event = fixtures.GalleryPhotoSubmissionTests.make_event
    make_eligible_embedding = fixtures.GalleryPhotoSubmissionTests.make_eligible_embedding
    make_additional_face = fixtures.GalleryPhotoSubmissionTests.make_additional_face

    def add_face(self, photo_id, first, axis=1, *, event=None):
        return self.make_eligible_embedding(
            event=event or self.event, photo_id=photo_id, vector=_vector(first, axis)
        )

    def submit_gallery(self, face, user=None):
        return submit_gallery_photo_search(
            event=self.event,
            photo=face.detection.attempt.photo,
            detection_id=face.detection_id,
            user=user or self.user,
        ).search

    def submit_selfie(self, user):
        search = submit_selfie_search(
            event=self.event,
            selfie=fixtures.valid_selfie(),
            storage=fixtures.RecordingStorage(),
            user=user,
        ).search
        attempt = claim_search_job(
            contract_version=1,
            processor_type="selfie_query",
            processor_version=2,
            lease_seconds=120,
        )
        assert isinstance(attempt, ClaimedSearchJob)
        complete_search_attempt(attempt.attempt.pk, result=test_jobs.SearchJobTests.result(self))
        return search

    def set_gate(self, state):
        FeatureFlag.objects.update_or_create(
            key="face-search-anchor-expansion", defaults={"state": state, "description": "Test"}
        )

    def result_photos(self, search):
        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.READY)
        return list(search.results.order_by("rank").values_list("photo_id", flat=True))

    def test_gate_uses_current_state_and_records_staff_at_submission_for_both_sources(self):
        source = self.add_face("query-source", 1)
        self.add_face("anchor", 0.75)
        self.add_face("addition", 0.5)
        staff = get_user_model().objects.create_user(username="anchor-staff", is_staff=True)
        inactive_staff = get_user_model().objects.create_user(
            username="anchor-inactive-staff", is_staff=True, is_active=False
        )
        for submit, baseline in (
            (lambda user: self.submit_gallery(source, user), ["query-source", "anchor"]),
            (self.submit_selfie, ["query-source", "anchor"]),
        ):
            for at_execution, user, expected in (
                ("missing", staff, baseline),
                ("off", staff, baseline),
                ("staff", self.user, baseline),
                ("staff", inactive_staff, baseline),
                ("staff", staff, [*baseline, "addition"]),
                ("on", self.user, [*baseline, "addition"]),
            ):
                with self.subTest(submit=submit, state=at_execution, staff=user.is_staff):
                    self.set_gate("off")
                    with CaptureQueriesContext(connection) as submission_queries:
                        search = submit(user)
                    self.assertFalse(
                        any(
                            "face-search-anchor-expansion" in query["sql"]
                            for query in submission_queries
                        )
                    )
                    search.refresh_from_db()
                    self.assertIs(
                        search.configuration["anchor_expansion_staff_eligible"],
                        user.is_staff and user.is_active,
                    )
                    self.assertEqual(
                        search.configuration_hash,
                        hashlib.sha256(
                            json.dumps(
                                search.configuration, sort_keys=True, separators=(",", ":")
                            ).encode()
                        ).hexdigest(),
                    )
                    if at_execution == "missing":
                        FeatureFlag.objects.filter(key="face-search-anchor-expansion").delete()
                    else:
                        self.set_gate(at_execution)
                    # The worker must use the submitted eligibility after staff status changes.
                    get_user_model().objects.filter(pk=user.pk).update(is_staff=not user.is_staff)
                    try:
                        with CaptureQueriesContext(connection) as queries:
                            self.assertTrue(run_once())
                        self.assertEqual(self.result_photos(search), expected)
                        self.assertEqual(
                            sum(
                                'FROM "feature_flags_featureflag"' in query["sql"]
                                and "face-search-anchor-expansion" in query["sql"]
                                for query in queries
                            ),
                            1,
                        )
                    finally:
                        get_user_model().objects.filter(pk=user.pk).update(is_staff=user.is_staff)

    def test_staff_gate_requires_a_json_boolean_true_in_the_search_snapshot(self):
        source = self.add_face("query-source", 1)
        self.add_face("anchor", 0.75)
        self.add_face("addition", 0.5)
        self.set_gate("staff")
        staff = get_user_model().objects.create_user(username="snapshot-staff", is_staff=True)
        for invalid_eligibility in (None, 1, "true"):
            with self.subTest(staff_eligibility=invalid_eligibility):
                search = self.submit_gallery(source, staff)
                configuration = search.configuration.copy()
                if invalid_eligibility is None:
                    configuration.pop("anchor_expansion_staff_eligible", None)
                else:
                    configuration["anchor_expansion_staff_eligible"] = invalid_eligibility
                SelfieSearch.objects.filter(pk=search.pk).update(
                    configuration=configuration,
                    configuration_hash=hashlib.sha256(
                        json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                )
                self.assertTrue(run_once())
                self.assertEqual(self.result_photos(search), ["query-source", "anchor"])

    @override_settings(
        STORAGES={
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
        }
    )
    def test_admin_marks_anchor_evidence_as_read_only_analytics(self):
        self.set_gate("on")
        source = self.add_face("query-source", 1)
        anchor = self.add_face("anchor", 0.75)
        addition = self.add_face("addition", 0.5)
        search = self.submit_gallery(source)
        self.assertTrue(run_once())
        evidence = search.results.get(photo_id="addition").anchor_evidence

        user = get_user_model().objects.create_superuser(username="anchor-admin")
        self.client.force_login(user)
        url = reverse("admin:selfie_search_selfiesearchanchorevidence_change", args=(evidence.pk,))
        response = self.client.get(url)
        self.assertContains(response, "Anchor match evidence (analytics)")
        self.assertContains(response, "Cosine distance")
        self.assertNotContains(response, str(anchor.detection_id))
        self.assertNotContains(response, str(addition.detection_id))
        self.assertEqual(self.client.post(url, {"cosine_distance": 0}).status_code, 403)

    def test_finds_a_face_close_to_the_centroid_of_two_anchors(self):
        source = self.add_face("query-source", 1)
        for photo_id, third_component in (("first", 0.3), ("second", -0.3)):
            self.make_eligible_embedding(
                event=self.event,
                photo_id=photo_id,
                vector=_pad_vector(0.7, sqrt(0.42), third_component),
            )
        addition = self.make_eligible_embedding(
            event=self.event,
            photo_id="addition",
            vector=_pad_vector(0.4, 0.44, 0, sqrt(0.6464)),
            detection_id=UUID(int=1),
        )
        better_vector = _pad_vector(0.4, 0.45, 0, sqrt(0.6375))
        tied_face = self.make_additional_face(
            embedding=addition, vector=better_vector, detection_id=UUID(int=3)
        )
        # The worst detection sorts first by UUID. The best two tie on distance.
        better_face = self.make_additional_face(
            embedding=tied_face, vector=better_vector, detection_id=UUID(int=2)
        )

        self.set_gate("off")
        baseline = self.submit_gallery(source)
        self.assertTrue(run_once())
        self.assertEqual(self.result_photos(baseline), ["query-source", "first", "second"])
        self.set_gate("on")
        search = self.submit_gallery(source)
        self.assertTrue(run_once())

        # The better face's dot product is about .572 with either unit anchor
        # and .599 with their unit centroid. Only the centroid passes .58.
        self.assertEqual(
            self.result_photos(search), ["query-source", "first", "second", "addition"]
        )
        self.assertEqual(search.direct_matched_photo_count, 3)
        self.assertEqual(search.anchor_expanded_photo_count, 1)
        self.assertEqual(search.final_matched_photo_count, 4)
        direct_fields = (
            "photo_id",
            "rank",
            "direct_evidence__detection_id",
            "direct_evidence__cosine_distance",
        )
        self.assertEqual(
            list(
                search.results.filter(primary_source="direct")
                .order_by("rank")
                .values_list(*direct_fields)
            ),
            list(baseline.results.order_by("rank").values_list(*direct_fields)),
        )
        evidence = search.results.get(photo_id="addition").anchor_evidence
        self.assertEqual(evidence.detection_id, better_face.detection_id)
        self.assertAlmostEqual(evidence.cosine_distance, 0.40077, places=4)
        self.assertEqual(
            list(evidence.sources.values_list("position", "anchor__result__photo_id")),
            [(1, "first"), (2, "second")],
        )

    def test_uses_at_most_five_nearest_consistent_single_face_results(self):
        self.set_gate("on")
        source = self.add_face("query-source", 1)
        self.add_face("first", 0.8)
        group = self.add_face("group", 0.78)
        PhotoFaceDetection.objects.create(
            artifact=group.detection.artifact,
            attempt=group.detection.attempt,
            face_index=1,
            status=PhotoFaceDetection.Status.QUALITY_REJECTED,
        )
        self.add_face("inconsistent", 0.77, axis=2)
        for suffix in "edcba":
            self.add_face(f"near-{suffix}", 0.75)
        self.add_face("weak", 0.60)
        self.add_face("addition", 0.5)
        self.add_face("inconsistent-only", 0.5, axis=2)
        hidden = self.add_face("hidden", 0.5).detection.attempt.photo
        hidden.is_hidden = True
        hidden.save(update_fields=["is_hidden"])
        self.add_face("foreign", 0.5, event=self.other_event)
        search = self.submit_gallery(source)

        self.assertTrue(run_once())

        self.assertEqual(
            self.result_photos(search),
            [
                "query-source",
                "first",
                "group",
                "inconsistent",
                "near-a",
                "near-b",
                "near-c",
                "near-d",
                "near-e",
                "weak",
                "addition",
            ],
        )
        evidence = search.results.get(photo_id="addition").anchor_evidence
        self.assertEqual(
            list(evidence.sources.values_list("position", "anchor__result__photo_id")),
            [(1, "first"), (2, "near-a"), (3, "near-b"), (4, "near-c"), (5, "near-d")],
        )

    def test_gallery_search_has_no_centroid_when_the_source_is_the_only_strong_result(self):
        self.set_gate("on")
        source = self.add_face("query-source", 1)
        self.add_face("weak", 0.60)
        self.add_face("weak-only", 0.50)
        search = self.submit_gallery(source)

        self.assertTrue(run_once())

        self.assertEqual(self.result_photos(search), ["query-source", "weak"])
        self.assertEqual(search.anchor_expanded_photo_count, 0)

    def test_includes_selected_anchor_at_exact_pgvector_distance_boundary(self):
        self.set_gate("on")
        # The dyadic coordinates make pgvector's float32 accumulation exact.
        # Both squared norms are 4194300 / 4194304, and the dot is 2726295 / 4194304.
        positive = (3719, 98, 25)
        negative = (1712, 71, 5)
        first = _pad_vector(*(component / 4096 for component in (*positive, *negative)))
        second = _pad_vector(
            *(component / 4096 for component in positive),
            *(-component / 4096 for component in negative),
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s::vector <=> %s::vector", (first, second))
            self.assertEqual(cursor.fetchone(), (0.35,))

        positive_norm = sqrt(sum(component**2 for component in positive))
        negative_norm = sqrt(sum(component**2 for component in negative))

        def mixed_vector(positive_weight, negative_weight):
            return _pad_vector(
                *(component * positive_weight / positive_norm for component in positive),
                *(component * negative_weight / negative_norm for component in negative),
            )

        source = self.make_eligible_embedding(
            event=self.event, photo_id="boundary-query", vector=mixed_vector(sqrt(0.9975), 0.05)
        )
        self.make_eligible_embedding(event=self.event, photo_id="boundary-first", vector=first)
        self.make_eligible_embedding(event=self.event, photo_id="boundary-second", vector=second)
        self.make_eligible_embedding(
            event=self.event, photo_id="boundary-addition", vector=mixed_vector(0.6, -0.8)
        )
        search = self.submit_gallery(source)

        self.assertTrue(run_once())

        self.assertEqual(
            self.result_photos(search),
            ["boundary-query", "boundary-first", "boundary-second", "boundary-addition"],
        )
        evidence = search.results.get(photo_id="boundary-addition").anchor_evidence
        self.assertEqual(
            list(evidence.sources.values_list("anchor__result__photo_id", flat=True)),
            ["boundary-first", "boundary-second"],
        )

    def test_flag_is_registered_and_synchronized_off(self):
        call_command("sync_feature_flags", stdout=StringIO())
        self.assertEqual(FeatureFlag.objects.get(key="face-search-anchor-expansion").state, "off")

    def test_ranking_and_publication_database_failures_retry_without_partial_results(self):
        self.set_gate("on")
        source = self.add_face("query-source", 1)
        self.add_face("anchor", 0.75)
        self.add_face("addition", 0.5)

        def fail_source_insert(execute, sql, params, many, context):
            if 'INSERT INTO "selfie_search_selfiesearchanchorevidencesource"' in sql:
                raise DatabaseError("simulated source write failure")
            return execute(sql, params, many, context)

        for failure, inject in (
            (
                "ranking",
                patch(
                    "selfie_search.services.anchor_expansion._EXPAND_SQL",
                    f"{_EXPAND_SQL}\nLIMIT (1 / 0)",
                ),
            ),
            ("publication", connection.execute_wrapper(fail_source_insert)),
        ):
            with self.subTest(failure=failure):
                search = self.submit_gallery(source)
                with inject:
                    self.assertTrue(run_once())

                search.refresh_from_db()
                self.assertEqual(search.status, SelfieSearch.Status.QUEUED)
                self.assertFalse(search.results.exists())
                FaceSearchTask.objects.filter(search=search).update(available_at=timezone.now())
                self.assertTrue(run_once())
                self.assertEqual(self.result_photos(search), ["query-source", "anchor", "addition"])

    def test_anchor_statement_timeout_protects_parameters_and_publishes_no_rows(self):
        self.set_gate("on")
        source = self.add_face("synthetic-centroid-source", 1)
        anchor = self.make_eligible_embedding(
            event=self.event,
            photo_id="synthetic-centroid-anchor",
            vector=_vector(0.7654321),
            detection_id=UUID("01234567-89ab-cdef-0123-456789abcdef"),
        )
        self.add_face("addition", 0.5)
        search = self.submit_gallery(source)

        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL log_parameter_max_length_on_error = -1")
            cursor.execute("""
                CREATE FUNCTION pg_temp.observe_centroid_timeout() RETURNS void
                LANGUAGE plpgsql AS $$
                BEGIN
                    RAISE NOTICE USING MESSAGE = json_build_array(
                        current_query(),
                        current_setting('log_parameter_max_length_on_error'),
                        extract(epoch FROM current_setting('statement_timeout')::interval)
                    )::text;
                    PERFORM pg_sleep(1);
                END;
                $$
            """)
        timed_sql = f"""
            WITH delay AS MATERIALIZED (SELECT pg_temp.observe_centroid_timeout()),
            matches AS MATERIALIZED ({_EXPAND_SQL})
            SELECT matches.* FROM matches CROSS JOIN delay
        """
        statements = []

        def record_statement(diagnostic):
            if diagnostic.message_primary.startswith("["):
                statements.append(json.loads(diagnostic.message_primary))

        database = connection.connection
        database.add_notice_handler(record_statement)
        try:
            with (
                patch("selfie_search.services.face_search_queue.RANKING_TIMEOUT_MS", 250),
                patch("selfie_search.services.anchor_expansion._EXPAND_SQL", timed_sql),
            ):
                self.assertTrue(run_once())
        finally:
            database.remove_notice_handler(record_statement)

        self.assertEqual(len(statements), 1)
        sql, parameter_log_limit, timeout = statements[0]
        self.assertNotIn(source.detection.attempt.photo_id, sql)
        self.assertNotIn(anchor.detection.attempt.photo_id, sql)
        self.assertNotIn(anchor.detection_id.hex, sql)
        self.assertNotIn(str(anchor.detection_id), sql)
        self.assertRegex(sql, r"unnest\(\$\d+::varchar\[\], \$\d+::uuid\[\], \$\d+::float8\[\]\)")
        self.assertRegex(sql, r"direct.photo_id IS DISTINCT FROM \$\d+")
        self.assertEqual(parameter_log_limit, "0")
        self.assertGreater(timeout, 0)
        self.assertLess(timeout, 0.25)
        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.FAILED)
        self.assertEqual(search.failure_code, "failed")
        self.assertEqual(search.matching_task.status, FaceSearchTask.Status.TERMINAL)
        self.assertFalse(search.results.exists())
        with connection.cursor() as cursor:
            cursor.execute("SHOW log_parameter_max_length_on_error")
            self.assertEqual(cursor.fetchone(), ("-1",))

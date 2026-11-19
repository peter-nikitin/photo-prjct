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
                            sum("anchor_candidates AS" in query["sql"] for query in queries),
                            int(expected != baseline),
                        )
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

    def test_selects_three_diverse_anchors_and_saves_the_best_single_evidence(self):
        self.set_gate("on")
        source = self.add_face("query-source", 1)
        anchor_a = self.add_face("a", 0.8, 1)
        self.add_face("a-near", 0.75, 1)
        vector_b = _pad_vector(0.7, 0.2, sqrt(0.47))
        anchor_b = self.make_eligible_embedding(event=self.event, photo_id="b", vector=vector_b)
        self.make_eligible_embedding(event=self.event, photo_id="b-near", vector=vector_b)
        anchor_c = self.make_eligible_embedding(
            event=self.event, photo_id="c", vector=_pad_vector(0.7, 0.2, 0, sqrt(0.47))
        )
        self.add_face("weak", 0.6, 4)
        group = self.add_face("group", 0.7, 5)
        PhotoFaceDetection.objects.create(
            artifact=group.detection.artifact,
            attempt=group.detection.attempt,
            face_index=1,
            status=PhotoFaceDetection.Status.QUALITY_REJECTED,
        )
        addition_a = self.add_face("a-addition", 0.5, 1)
        addition_b = self.add_face("b-addition", 0.5, 2)
        addition_c = self.add_face("c-addition", 0.5, 3)
        better_c_face = self.make_additional_face(
            embedding=addition_c,
            vector=_vector(0.51, 3),
            detection_id=UUID(int=1),
        )
        shared = self.make_eligible_embedding(
            event=self.event,
            photo_id="b-c-shared",
            vector=[0.5, 0, sqrt(0.375), sqrt(0.375)] + [0.0] * 508,
        )
        self.add_face("weak-only", 0.5, 4)
        self.add_face("group-only", 0.5, 5)
        hidden = self.add_face("hidden", 0.5, 1).detection.attempt.photo
        hidden.is_hidden = True
        hidden.save(update_fields=["is_hidden"])
        self.add_face("foreign", 0.5, 1, event=self.other_event)
        search = self.submit_gallery(source)

        with CaptureQueriesContext(connection) as queries:
            self.assertTrue(run_once())

        self.assertEqual(
            self.result_photos(search),
            [
                "query-source",
                "a",
                "a-near",
                "b",
                "b-near",
                "c",
                "group",
                "weak",
                "c-addition",
                "b-addition",
                "a-addition",
                "b-c-shared",
            ],
        )
        self.assertEqual(sum("anchor_candidates AS" in q["sql"] for q in queries), 1)
        self.assertEqual(search.anchor_expanded_photo_count, 4)
        self.assertEqual(search.final_matched_photo_count, 12)
        self.assertEqual(
            set(
                search.results.filter(primary_source="face_anchor_expansion").values_list(
                    "photo_id",
                    "anchor_evidence__anchor__detection_id",
                    "anchor_evidence__detection_id",
                )
            ),
            {
                ("a-addition", anchor_a.detection_id, addition_a.detection_id),
                ("b-addition", anchor_b.detection_id, addition_b.detection_id),
                ("c-addition", anchor_c.detection_id, better_c_face.detection_id),
                ("b-c-shared", anchor_b.detection_id, shared.detection_id),
            },
        )

    def test_filters_original_choices_by_distance_to_first_without_replacement(self):
        self.set_gate("on")
        for retain_third in (True, False):
            with self.subTest(retain_third=retain_third):
                self.event = self.make_event(f"consistency-{retain_third}", "free")
                suffix = "keep-third" if retain_third else "drop-third"
                source = self.add_face(f"{suffix}-query", 1)
                self.add_face(f"{suffix}-first", 0.8)
                self.add_face(f"{suffix}-second", 0.64, 2)
                self.make_eligible_embedding(
                    event=self.event,
                    photo_id=f"{suffix}-spare",
                    vector=_pad_vector(0.68, 0.25, 0.65, -sqrt(0.0526)),
                )
                third_y = 0.35 if retain_third else 0.1
                third_w = sqrt(0.3875 - third_y**2)
                self.make_eligible_embedding(
                    event=self.event,
                    photo_id=f"{suffix}-third",
                    vector=_pad_vector(0.7, third_y, -0.35, third_w),
                )
                first_only = self.add_face(f"{suffix}-first-only", 0.3).detection.attempt.photo_id
                second_only = self.add_face(
                    f"{suffix}-second-only", 0, 2
                ).detection.attempt.photo_id
                third_only = f"{suffix}-third-only"
                self.make_eligible_embedding(
                    event=self.event,
                    photo_id=third_only,
                    vector=_pad_vector(
                        0, third_y / sqrt(0.51), -0.35 / sqrt(0.51), third_w / sqrt(0.51)
                    ),
                )
                self.make_eligible_embedding(
                    event=self.event,
                    photo_id=f"{suffix}-spare-only",
                    vector=_pad_vector(0.5, 0, 0.3, -sqrt(0.66)),
                )
                search = self.submit_gallery(source)

                self.assertTrue(run_once())

                self.assertEqual(
                    self.result_photos(search)[:5],
                    [f"{suffix}-{name}" for name in ("query", "first", "third", "spare", "second")],
                )
                # The spare is farther from the first than the retained third, but is
                # too close to the second to win the original maximin selection.
                original_additions = {first_only, second_only, third_only}
                removed = {second_only} if retain_third else {second_only, third_only}
                self.assertEqual(
                    set(
                        search.results.filter(primary_source="face_anchor_expansion").values_list(
                            "photo_id", flat=True
                        )
                    ),
                    original_additions - removed,
                )

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
        boundary = self.make_eligible_embedding(
            event=self.event, photo_id="boundary-second", vector=second
        )
        self.make_eligible_embedding(
            event=self.event, photo_id="boundary-addition", vector=mixed_vector(0.4, -sqrt(0.84))
        )
        search = self.submit_gallery(source)

        self.assertTrue(run_once())

        self.assertEqual(
            self.result_photos(search),
            ["boundary-query", "boundary-first", "boundary-second", "boundary-addition"],
        )
        evidence = search.results.get(photo_id="boundary-addition").anchor_evidence
        self.assertEqual(evidence.anchor.detection_id, boundary.detection_id)

    def test_flag_is_registered_and_synchronized_off(self):
        call_command("sync_feature_flags", stdout=StringIO())
        self.assertEqual(FeatureFlag.objects.get(key="face-search-anchor-expansion").state, "off")

    def test_ranking_and_publication_database_failures_retry_without_partial_results(self):
        self.set_gate("on")
        source = self.add_face("query-source", 1)
        self.add_face("anchor", 0.75)
        self.add_face("addition", 0.5)
        for statement in (
            "anchor_candidates AS",
            'INSERT INTO "selfie_search_selfiesearchanchorevidence"',
        ):
            with self.subTest(statement=statement):
                search = self.submit_gallery(source)

                def fail_statement(execute, sql, params, many, context, *, statement=statement):
                    if statement in sql:
                        raise DatabaseError("simulated transient failure")
                    return execute(sql, params, many, context)

                with connection.execute_wrapper(fail_statement):
                    self.assertTrue(run_once())

                search.refresh_from_db()
                self.assertEqual(search.status, SelfieSearch.Status.QUEUED)
                self.assertFalse(search.results.exists())
                FaceSearchTask.objects.filter(search=search).update(available_at=timezone.now())
                self.assertTrue(run_once())
                self.assertEqual(self.result_photos(search), ["query-source", "anchor", "addition"])

    def test_anchor_statement_timeout_uses_remaining_budget_and_publishes_no_rows(self):
        self.set_gate("on")
        source = self.add_face("query-source", 1)
        self.add_face("anchor", 0.75)
        self.add_face("addition", 0.5)
        search = self.submit_gallery(source)
        ranking_timeouts = []

        def time_out_expansion(execute, sql, params, many, context):
            if "anchor_candidates AS" in sql:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT current_setting('statement_timeout')::interval")
                    timeout = cursor.fetchone()[0].total_seconds()
                    ranking_timeouts.append(timeout)
                    cursor.execute("SELECT pg_sleep(1)")
            return execute(sql, params, many, context)

        with (
            patch("selfie_search.services.face_search_queue.RANKING_TIMEOUT_MS", 250),
            connection.execute_wrapper(time_out_expansion),
        ):
            self.assertTrue(run_once())

        self.assertEqual(len(ranking_timeouts), 1)
        self.assertGreater(ranking_timeouts[0], 0)
        self.assertLess(ranking_timeouts[0], 0.25)
        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.FAILED)
        self.assertEqual(search.failure_code, "failed")
        self.assertEqual(search.matching_task.status, FaceSearchTask.Status.TERMINAL)
        self.assertFalse(search.results.exists())

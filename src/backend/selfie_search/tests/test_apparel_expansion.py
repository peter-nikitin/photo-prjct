from dataclasses import replace
from datetime import timedelta
from math import sqrt
from unittest.mock import call, patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import DatabaseError, connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from feature_flags.models import FeatureFlag
from picflow.gallery_media_projection import publish_gallery_media
from picflow.models import Event
from processing.contracts import ClaimedJob
from processing.models import PhotoFaceDetection, PhotoProcessingState, QueueWorkCounter
from processing.services.enrollment import _derivative_fingerprint
from processing.services.jobs import claim_job, complete_attempt
from processing.services.person_appearance import request_person_appearance
from processing.tests import test_views
from processing.tests.test_appearance import appearance_result
from psycopg import Cursor
from selfie_search.models import (
    FaceSearchTask,
    SelfieSearch,
    SelfieSearchApparelEvidence,
    SelfieSearchResult,
)
from selfie_search.services import face_search_queue
from selfie_search.services.apparel_expansion import POLICY, candidate_veto, expand_apparel
from selfie_search.services.face_search_queue import run_once
from selfie_search.services.submission import submit_gallery_photo_search
from selfie_search.tests import test_anchor_expansion, test_gallery_queue, test_submission


def _regional_color_vector(head: float, torso: float | None) -> list[float]:
    scale = sqrt(0.5) if torso is not None else 1.0
    vector = [head * scale, sqrt(1 - head**2) * scale] + [0.0] * 190
    if torso is None:
        return vector + [0.0] * 192
    return vector + [torso * scale, sqrt(1 - torso**2) * scale] + [0.0] * 190


class ApparelPolicyTests(SimpleTestCase):
    def test_face_distance_and_helmet_similarity_use_distinct_units(self):
        assert candidate_veto(face_distance=0.55, helmet_similarity=0.68) == ""
        assert candidate_veto(face_distance=0.551, helmet_similarity=None) == "face_veto"
        assert candidate_veto(face_distance=None, helmet_similarity=0.679) == "helmet_veto"
        assert candidate_veto(face_distance=None, helmet_similarity=None) == ""


class ApparelQueueTests(TestCase):
    event: Event
    setUp = test_submission.GalleryPhotoSubmissionTests.setUp
    make_event = test_submission.GalleryPhotoSubmissionTests.make_event
    make_photo = test_submission.GalleryPhotoSubmissionTests.make_photo
    make_eligible_embedding = test_submission.GalleryPhotoSubmissionTests.make_eligible_embedding
    publish_preview = test_views.WorkerApiTests.publish_preview
    submit_gallery = test_anchor_expansion.AnchorExpansionTests.submit_gallery
    submit_selfie = test_anchor_expansion.AnchorExpansionTests.submit_selfie
    result_photos = test_anchor_expansion.AnchorExpansionTests.result_photos

    def index_appearance(
        self,
        identifier,
        *,
        face_similarity=None,
        reason="",
        event=None,
        associate_face=True,
        second_head=False,
        reid_similarity=1.0,
        color_similarity=1.0,
        head_color_similarity=None,
        zero_torso=False,
        headless_bystander_matches=None,
        unassigned_head=False,
        helmet_similarity=None,
    ):
        event = event or self.event
        photo = self.make_photo(event=event, photo_id=identifier)
        # The preview helper binds the run from the supplied photo and the fixture's event.
        if event != self.event:
            initial_event = self.event
            self.event = event
            try:
                preview = self.publish_preview(photo)
            finally:
                self.event = initial_event
        else:
            preview = self.publish_preview(photo)
        PhotoProcessingState.objects.filter(photo=photo, processor_type="generate_preview").update(
            current_attempt=preview.accepted_attempt
        )
        publish_gallery_media(preview)
        face = None
        if face_similarity is not None:
            face = self.make_eligible_embedding(
                event=event,
                photo_id=photo.pk,
                photo=photo,
                vector=[face_similarity, sqrt(1 - face_similarity**2)] + [0] * 510,
                input_fingerprint=_derivative_fingerprint(preview),
            )
        assert request_person_appearance(photo.pk, preview.final_key)
        claim = claim_job(
            contract_version=2, processor_type="person_appearance", processor_version=1
        )
        assert isinstance(claim, ClaimedJob)
        row = {
            "bbox": [0, 0, 100, 300],
            "confidence": 0.9,
            "reason": reason,
            "focus": [50, 50, 12, 12] if reason else [450, 450, 12, 12],
            "head": [20, 20, 60, 60] if face is not None and associate_face else None,
        }
        if not reason:
            row.update(
                reid=[reid_similarity, sqrt(1 - reid_similarity**2)] + [0] * 254,
                color=_regional_color_vector(
                    color_similarity if head_color_similarity is None else head_color_similarity,
                    None if zero_torso else color_similarity,
                ),
                helmet=(
                    [helmet_similarity, sqrt(1 - helmet_similarity**2)] + [0] * 1762
                    if helmet_similarity is not None
                    else None
                ),
            )
        rows = [row]
        if second_head:
            rows.append(
                {
                    "bbox": [1, 0, 101, 300],
                    "confidence": 0.8,
                    "reason": "strong_blur",
                    "focus": [50, 50, 12, 12],
                    "head": [21, 20, 60, 60],
                }
            )
        if headless_bystander_matches is not None:
            rows.append(
                {
                    "bbox": [150, 0, 250, 300],
                    "confidence": 0.8,
                    "reason": "",
                    "focus": [450, 450, 12, 12],
                    "head": None,
                    "reid": [1] + [0] * 255 if headless_bystander_matches else row["reid"],
                    "color": (
                        _regional_color_vector(1.0, 1.0)
                        if headless_bystander_matches
                        else row["color"]
                    ),
                    "helmet": None,
                }
            )
        head_count = int(face is not None) + int(second_head) + int(unassigned_head)
        complete_attempt(claim.attempt.pk, result=appearance_result(*rows, head_count=head_count))
        return photo, face

    def test_gallery_source_candidate_veto_and_stale_preview_are_event_scoped(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        self.index_appearance("good-no-face")
        self.index_appearance("veto-face", face_similarity=0.1)
        self.index_appearance("other-event-appearance", event=self.other_event)
        stale, _ = self.index_appearance("stale-preview")
        FeatureFlag.objects.filter(key="person-appearance-processing").update(state="off")
        PhotoProcessingState.objects.filter(photo=stale, processor_type="generate_preview").update(
            accepted_attempt=None
        )
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        search = self.submit_gallery(source)
        assert run_once()
        assert self.result_photos(search) == ["apparel-source", "good-no-face"]
        result = search.results.get(photo_id="good-no-face")
        assert result.primary_source == SelfieSearchResult.PrimarySource.APPAREL_EXPANSION
        assert search.apparel_expanded_photo_count == 1
        assert search.final_matched_photo_count == 2
        decisions = search.apparel_evidence.decisions
        assert decisions["anchors"][0]["detection_id"] == str(source.detection_id)
        assert {row["photo_id"]: row["veto"] for row in decisions["candidates"]} == {
            "good-no-face": "",
            "veto-face": "face_veto",
        }

    def test_centroid_face_result_stays_in_baseline_with_quality_recommendation(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        FeatureFlag.objects.create(key="face-search-anchor-expansion", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        self.index_appearance("direct-anchor", face_similarity=0.75)
        self.index_appearance("centroid-match", face_similarity=0.5, reason="strong_blur")
        search = self.submit_gallery(source)
        assert run_once()
        assert self.result_photos(search) == ["apparel-source", "direct-anchor", "centroid-match"]
        assert (
            search.results.get(photo_id="centroid-match").primary_source == "face_anchor_expansion"
        )
        decisions = search.apparel_evidence.decisions
        assert "centroid-match" not in {a["photo_id"] for a in decisions["anchors"]}
        assert [(row["result_rank"], row["reason"]) for row in decisions["recommendations"]] == [
            (3, "strong_blur")
        ]

    def test_cross_signal_checks_keep_incompatible_anchors_out_of_both_expansions(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        FeatureFlag.objects.create(key="face-search-anchor-expansion", state="on")
        _, source = self.index_appearance("grey-source", face_similarity=1.0)
        _, clean = self.index_appearance("grey-anchor", face_similarity=0.75)
        _, white = self.index_appearance(
            "white-anchor", face_similarity=0.78, reid_similarity=0.4336, color_similarity=0.7233
        )
        self.index_appearance(
            "pink-anchor", face_similarity=0.77, reid_similarity=0.4924, color_similarity=0.5267
        )
        self.index_appearance("grey-centroid", face_similarity=0.5, reason="strong_blur")
        self.index_appearance("pink-only", reid_similarity=0.4924, color_similarity=0.5267)
        self.index_appearance("grey-only")
        search = self.submit_gallery(source)

        with patch.dict(POLICY, {"max_recommendations": 1}):
            assert run_once()
        assert "grey-only" in self.result_photos(search)
        assert "pink-only" not in self.result_photos(search)
        centroid = search.results.get(photo_id="grey-centroid")
        sources = list(
            centroid.anchor_evidence.sources.values_list("anchor__detection_id", flat=True)
        )
        assert sources == [clean.detection_id]
        autosuppressed = search.results.get(photo_id="white-anchor")
        assert autosuppressed.excluded_by_apparel
        assert autosuppressed.direct_evidence.detection_id == white.detection_id
        assert search.apparel_excluded_photo_count == 2
        decisions = search.apparel_evidence.decisions
        assert [row["photo_id"] for row in decisions["anchors"]] == ["grey-source", "grey-anchor"]
        assert decisions["recommendations_truncated"]
        assert {row["photo_id"] for row in decisions["exclusions"]} == {
            "pink-anchor",
            "white-anchor",
        }
        assert {
            row["photo_id"] for row in decisions["anchor_checks"] if row["decision"] == "rejected"
        } == {"pink-anchor", "white-anchor"}

    def test_helmet_disagreement_rejects_an_anchor_without_hiding_the_face_result(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        _, source = self.index_appearance(
            "helmet-source", face_similarity=1.0, helmet_similarity=1.0
        )
        self.index_appearance("weak-helmet", face_similarity=0.9, helmet_similarity=0.65)
        self.index_appearance("unknown-helmet", face_similarity=0.8)
        search = self.submit_gallery(source)

        assert run_once()
        assert self.result_photos(search) == ["helmet-source", "weak-helmet", "unknown-helmet"]
        decisions = search.apparel_evidence.decisions
        assert [row["photo_id"] for row in decisions["anchors"]] == [
            "helmet-source",
            "unknown-helmet",
        ]
        outcomes = {row["photo_id"]: row["decision"] for row in decisions["anchor_checks"]}
        assert outcomes["weak-helmet"] == "rejected"
        assert search.apparel_excluded_photo_count == 0

    def test_torso_agreement_preserves_result_despite_other_color_disagreement(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        _, source = self.index_appearance("grey-source", face_similarity=1.0)
        self.index_appearance(
            "same-kit-side-view",
            face_similarity=0.95,
            reid_similarity=0.45,
            head_color_similarity=0.60,
            color_similarity=0.86,
        )
        self.index_appearance(
            "regional-anchor-mismatch",
            face_similarity=0.92,
            head_color_similarity=0.69,
            color_similarity=0.97,
        )
        self.index_appearance(
            "unknown-torso",
            face_similarity=0.9,
            reid_similarity=0.45,
            color_similarity=0.60,
            zero_torso=True,
        )
        search = self.submit_gallery(source)

        assert run_once()
        assert set(self.result_photos(search)) == {
            "grey-source",
            "same-kit-side-view",
            "regional-anchor-mismatch",
            "unknown-torso",
        }
        assert search.apparel_excluded_photo_count == 0
        decisions = search.apparel_evidence.decisions
        assert [row["photo_id"] for row in decisions["anchors"]] == ["grey-source"]
        pair = next(
            row
            for row in decisions["anchor_comparisons"]
            if {row["left_photo_id"], row["right_photo_id"]}
            == {"grey-source", "regional-anchor-mismatch"}
        )
        assert pair["color_cosine_similarity"] >= 0.8
        assert pair["head_color_cosine_similarity"] < 0.8
        assert pair["torso_color_cosine_similarity"] >= 0.8

    def test_group_protection_and_quality_keep_uncertain_face_matches_visible(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        _, source = self.index_appearance("grey-source", face_similarity=1.0)
        self.index_appearance("unknown-quality", face_similarity=0.85, reason="strong_blur")
        self.index_appearance(
            "group-with-match",
            face_similarity=0.83,
            reid_similarity=0.42,
            color_similarity=0.7,
            headless_bystander_matches=True,
        )
        self.index_appearance(
            "one-signal-disagrees", face_similarity=0.81, reid_similarity=0.42, color_similarity=0.9
        )
        self.index_appearance(
            "two-signals-disagree", face_similarity=0.79, reid_similarity=0.42, color_similarity=0.7
        )
        search = self.submit_gallery(source)

        assert run_once()
        assert len(self.result_photos(search)) == 5
        assert set(
            search.results.filter(excluded_by_apparel=True).values_list("photo_id", flat=True)
        ) == {"two-signals-disagree"}
        decisions = search.apparel_evidence.decisions
        excluded = decisions["exclusions"][0]
        assert excluded["photo_id"] == "two-signals-disagree"
        assert excluded["matches"][0]["comparisons"][0]["photo_id"] == "grey-source"
        ordinary = {row["photo_id"]: row for row in decisions["recommendations"]}
        assert ordinary["group-with-match"]["protection"] == "agreeing_figure"
        assert ordinary["unknown-quality"]["reason"] == "strong_blur"

    def test_unassigned_head_protects_group_even_if_detected_figures_disagree(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        _, source = self.index_appearance("grey-source", face_similarity=1.0)
        for photo_id, unassigned_head in (("complete-group", False), ("incomplete-group", True)):
            self.index_appearance(
                photo_id,
                face_similarity=0.83,
                reid_similarity=0.42,
                color_similarity=0.7,
                headless_bystander_matches=False,
                unassigned_head=unassigned_head,
            )
        search = self.submit_gallery(source)

        assert run_once()
        assert self.result_photos(search) == ["grey-source", "complete-group", "incomplete-group"]
        assert search.results.get(photo_id="complete-group").excluded_by_apparel
        assert not search.results.get(photo_id="incomplete-group").excluded_by_apparel
        assert search.published_matched_photo_count == 2
        recommendations = {
            row["photo_id"]: row["protection"]
            for row in search.apparel_evidence.decisions["recommendations"]
        }
        assert recommendations["incomplete-group"] == "uncertain_figure"

    def test_publication_keeps_excluded_face_results_and_original_anchor_sources(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        FeatureFlag.objects.create(key="face-search-anchor-expansion", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        _, direct = self.index_appearance("direct-anchor", face_similarity=0.75)
        self.index_appearance("centroid-match", face_similarity=0.5, reason="strong_blur")
        search = self.submit_gallery(source)

        def record_exclusion(*args, **kwargs):
            expansion = expand_apparel(*args, **kwargs)
            assert expansion.apparel_evidence is not None
            audit = {
                "photo_id": "direct-anchor",
                "result_rank": 2,
                "reason": "all_matched_figures_disagree",
                "matches": [{"detection_id": str(direct.detection_id)}],
            }
            return replace(
                expansion,
                excluded_photo_ids=frozenset({"direct-anchor"}),
                apparel_evidence=expansion.apparel_evidence | {"exclusions": [audit]},
            )

        with patch.object(face_search_queue, "expand_apparel", side_effect=record_exclusion):
            assert run_once()

        assert self.result_photos(search) == ["apparel-source", "direct-anchor", "centroid-match"]
        assert search.apparel_excluded_photo_count == 1
        assert search.matched_photo_count == search.final_matched_photo_count == 3
        assert search.published_matched_photo_count == 2
        excluded = search.results.get(rank=2)
        assert excluded.excluded_by_apparel
        assert excluded.primary_source == SelfieSearchResult.PrimarySource.DIRECT
        assert excluded.direct_evidence.detection_id == direct.detection_id
        centroid = search.results.get(rank=3)
        assert centroid.anchor_evidence.sources.get().anchor_id == excluded.direct_evidence.pk
        assert search.apparel_evidence.decisions

    def test_publication_retries_when_excluded_row_has_no_audit_decision(self):
        _, source = self.index_source_with_processing_enabled()
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        search = self.submit_gallery(source)

        def record_exclusion(*args, **kwargs):
            return replace(
                expand_apparel(*args, **kwargs),
                excluded_photo_ids=frozenset({"apparel-source"}),
            )

        with patch.object(face_search_queue, "expand_apparel", side_effect=record_exclusion):
            assert run_once()

        self.assert_retry_pending(search)

    def test_face_association_does_not_repeat_corpus_work_for_each_person(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        candidate_count = 12
        for index in range(candidate_count):
            self.index_appearance(f"other-person-{index}", face_similarity=0.1)
        search = self.submit_gallery(source)
        rows_per_operation = []

        def collect_association_work(node):
            loops = node["Actual Loops"]
            if node["Node Type"] == "WindowAgg":
                rows_per_operation.append(node["Actual Rows"] * loops)
            rows_per_operation.append(node.get("Rows Removed by Join Filter", 0) * loops)
            for child in node.get("Plans", []):
                collect_association_work(child)

        class PlanRecordingCursor(Cursor):
            def execute(self, query, params=None, **kwargs):
                if params is not None:
                    super().execute("EXPLAIN (ANALYZE, FORMAT JSON, TIMING OFF) " + query, params)
                    explained = self.fetchone()
                    assert explained is not None
                    plan = explained[0][0]["Plan"]
                    association = next(
                        node for node in plan["Plans"] if node.get("Subplan Name") == "CTE linked"
                    )
                    collect_association_work(association)
                return super().execute(query, params, **kwargs)

        with patch("selfie_search.services.apparel_expansion.Cursor", PlanRecordingCursor):
            assert run_once()
        assert len(search.apparel_evidence.decisions["candidates"]) == candidate_count
        # Each person matches one face here. A corpus self-join rejects N × (N - 1) pairs.
        assert max(rows_per_operation) <= candidate_count + 1

    def test_ambiguous_face_associations_do_not_supply_vetoes_or_recommendations(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        _, overlapping_face = self.index_appearance("two-faces-one-person", face_similarity=0.1)
        kept = overlapping_face.detection
        PhotoFaceDetection.objects.create(
            artifact=kept.artifact,
            attempt=kept.attempt,
            face_index=1,
            status=PhotoFaceDetection.Status.QUALITY_REJECTED,
            geometry=kept.geometry | {"bbox": [21, 20, 40, 40]},
        )
        self.index_appearance("one-face-two-people", face_similarity=0.1, second_head=True)
        self.index_appearance("ambiguous-direct", face_similarity=0.95, second_head=True)

        search = self.submit_gallery(source)
        assert run_once()
        decisions = search.apparel_evidence.decisions
        assert [row["photo_id"] for row in decisions["anchors"]] == ["apparel-source"]
        assert decisions["recommendations"] == []
        assert [
            (row["photo_id"], row["face_cosine_distance"], row["veto"])
            for row in decisions["candidates"]
        ] == [
            ("one-face-two-people", None, ""),
            ("two-faces-one-person", None, ""),
        ]
        assert self.result_photos(search) == [
            "apparel-source",
            "ambiguous-direct",
            "one-face-two-people",
            "two-faces-one-person",
        ]

    def test_gate_off_does_not_enter_optional_appearance_step(self):
        _, source = self.index_source_with_processing_enabled()
        search = self.submit_gallery(source)
        with (
            patch.object(face_search_queue, "expand_apparel") as apparel,
            patch.object(face_search_queue, "verify_apparel_anchors") as verify_anchors,
        ):
            assert run_once()
        apparel.assert_not_called()
        verify_anchors.assert_not_called()
        assert self.result_photos(search) == ["apparel-source"]

    def index_source_with_processing_enabled(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        return self.index_appearance("apparel-source", face_similarity=1.0)

    def test_selfie_staff_expansion_does_not_veto_from_another_person_in_the_group(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="staff")
        self.index_appearance("anchor", face_similarity=1.0)
        self.index_appearance("another-anchor", face_similarity=0.85)
        self.index_appearance("person-without-face", face_similarity=0.1, associate_face=False)
        visitor = self.submit_selfie(self.user)
        staff = get_user_model().objects.create_user(username="apparel-reviewer", is_staff=True)
        private = self.submit_selfie(staff)
        assert run_once()
        assert run_once()
        assert self.result_photos(visitor) == ["anchor", "another-anchor"]
        assert self.result_photos(private) == ["anchor", "another-anchor", "person-without-face"]
        decisions = private.apparel_evidence.decisions
        assert [row["photo_id"] for row in decisions["anchors"]] == ["anchor", "another-anchor"]
        assert decisions["candidates"][0]["face_cosine_distance"] is None

    def test_selfie_with_two_equally_supported_groups_keeps_the_face_baseline(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        FeatureFlag.objects.create(key="face-search-anchor-expansion", state="on")
        self.index_appearance("first-grey", face_similarity=1.0)
        self.index_appearance("second-grey", face_similarity=0.95)
        self.index_appearance(
            "first-pink", face_similarity=0.90, reid_similarity=0.4, color_similarity=0.6
        )
        self.index_appearance(
            "second-pink", face_similarity=0.85, reid_similarity=0.4, color_similarity=0.6
        )
        self.index_appearance("grey-appearance-only")
        search = self.submit_selfie(self.user)

        assert run_once()
        assert self.result_photos(search) == [
            "first-grey",
            "second-grey",
            "first-pink",
            "second-pink",
        ]
        decisions = search.apparel_evidence.decisions
        assert decisions["anchor_outcome"] == "ambiguous_agreement"
        assert decisions["anchors"] == decisions["exclusions"] == []

    def test_apparel_statement_timeout_requeues_and_preserves_vector_for_successful_retry(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        self.index_appearance("good-no-face")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        search = self.submit_gallery(source)
        task = search.matching_task
        vector = list(task.vector)

        def time_out(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', '20', true)")
                cursor.execute("SELECT pg_sleep(1)")

        with (
            patch("selfie_search.services.face_search_queue.expand_apparel", side_effect=time_out),
            patch.object(
                face_search_queue,
                "schedule_transition",
                wraps=face_search_queue.schedule_transition,
            ) as transitions,
        ):
            assert run_once()
        assert transitions.call_args_list == [
            call("gallery-search", "queued", "processing"),
            call("gallery-search", "processing", "queued"),
        ]
        task = self.assert_retry_pending(search)
        assert list(task.vector) == vector
        assert not run_once()

        self.make_retry_available(task)
        assert run_once()
        assert self.result_photos(search) == ["apparel-source", "good-no-face"]
        assert search.failure_code == ""
        assert search.apparel_evidence.decisions["added_people"]
        task.refresh_from_db()
        assert task.status == FaceSearchTask.Status.TERMINAL
        assert task.attempt_count == 2

    @override_settings(
        STORAGES={
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
        }
    )
    def test_apparel_second_failure_ends_search_without_publishing_baseline(self):
        photo, source = self.index_source_with_processing_enabled()
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        created = submit_gallery_photo_search(
            event=self.event, photo=photo, detection_id=source.detection_id, user=self.user
        )
        search = created.search
        with (
            patch(
                "selfie_search.services.face_search_queue.expand_apparel",
                side_effect=[DatabaseError("unsafe SQL parameter"), ValueError("unsafe vector")],
            ),
            self.assertLogs(face_search_queue.logger, level="WARNING") as logs,
        ):
            assert run_once()
            task = self.assert_retry_pending(search)
            self.make_retry_available(task)
            with patch.object(
                face_search_queue,
                "schedule_transition",
                wraps=face_search_queue.schedule_transition,
            ) as transitions:
                assert run_once()
        assert transitions.call_args_list == [
            call("gallery-search", "queued", "processing"),
            call("gallery-search", "processing", "terminal"),
        ]
        assert all("unsafe" not in line for line in logs.output)
        search.refresh_from_db()
        task.refresh_from_db()
        assert search.status == SelfieSearch.Status.FAILED
        assert search.failure_code == "apparel_search_unavailable"
        assert search.terminal_at is not None
        assert task.status == FaceSearchTask.Status.TERMINAL
        assert task.attempt_count == 2
        assert not search.results.exists()
        assert search.apparel_expanded_photo_count == 0
        assert not hasattr(search, "apparel_evidence")
        response = self.client.get(
            reverse(
                "selfie_search:result",
                kwargs={"event_slug": self.event.slug, "public_token": created.public_token},
            )
        )
        self.assertContains(response, "Поиск похожих фотографий сейчас недоступен.")
        self.assertNotContains(response, "apparel_search_unavailable")

    def test_apparel_evidence_write_failure_rolls_back_and_retries_publication(self):
        _, source = self.index_source_with_processing_enabled()
        self.index_appearance("good-no-face")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        search = self.submit_gallery(source)
        write_evidence = SelfieSearchApparelEvidence.objects.create

        def fail_after_writing_evidence(**values):
            write_evidence(**values)
            assert search.results.count() == 2
            raise DatabaseError("unsafe evidence")

        with patch.object(
            SelfieSearchApparelEvidence.objects, "create", side_effect=fail_after_writing_evidence
        ):
            assert run_once()
        task = self.assert_retry_pending(search)
        self.make_retry_available(task)
        assert run_once()
        assert self.result_photos(search) == ["apparel-source", "good-no-face"]
        assert search.apparel_evidence.decisions["added_people"]

    def test_apparel_uses_remaining_budget_below_the_stage_maximum(self):
        _, source = self.index_source_with_processing_enabled()
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        search = self.submit_gallery(source)
        with (
            patch.object(face_search_queue, "APPAREL_TIMEOUT_MS", 180_000),
            patch.object(face_search_queue, "expand_apparel", wraps=expand_apparel) as apparel,
        ):
            assert run_once()
        apparel.assert_called_once()
        assert self.result_photos(search) == ["apparel-source"]
        assert search.apparel_evidence.decisions["candidates"] == []

    def test_unindexed_event_uses_face_centroid_and_records_missing_appearance(self):
        source = self.make_eligible_embedding(event=self.event, photo_id="face-only-source")
        for photo_id, similarity in (("face-only-anchor", 0.75), ("face-only-centroid", 0.50)):
            self.make_eligible_embedding(
                event=self.event,
                photo_id=photo_id,
                vector=[similarity, sqrt(1 - similarity**2)] + [0] * 510,
            )
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        FeatureFlag.objects.create(key="face-search-anchor-expansion", state="on")
        search = self.submit_gallery(source)

        assert run_once()
        assert self.result_photos(search) == [
            "face-only-source",
            "face-only-anchor",
            "face-only-centroid",
        ]
        decisions = search.apparel_evidence.decisions
        assert decisions["anchor_outcome"] == "no_compatible_appearance"
        assert decisions["anchors"] == decisions["candidates"] == decisions["exclusions"] == []

    def test_expiry_while_waiting_for_apparel_retry_ends_search_without_results(self):
        _, source = self.index_source_with_processing_enabled()
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        search = self.submit_gallery(source)
        with patch.object(
            face_search_queue, "expand_apparel", side_effect=DatabaseError
        ) as apparel:
            assert run_once()
            task = self.assert_retry_pending(search)
            FaceSearchTask.objects.filter(pk=task.pk).update(
                expires_at=timezone.now() - timedelta(seconds=1)
            )
            assert run_once()
        apparel.assert_called_once()
        search.refresh_from_db()
        assert search.status == SelfieSearch.Status.FAILED
        assert search.failure_code == "matching_query_expired"
        assert not search.results.exists()
        assert not SelfieSearchApparelEvidence.objects.filter(search=search).exists()

    def assert_retry_pending(self, search):
        search.refresh_from_db()
        task = search.matching_task
        task.refresh_from_db()
        assert search.status == SelfieSearch.Status.QUEUED
        assert search.terminal_at is None
        assert search.failure_code == ""
        assert task.status == FaceSearchTask.Status.QUEUED
        assert task.attempt_count == 1
        assert task.available_at > timezone.now()
        assert not search.results.exists()
        assert not SelfieSearchApparelEvidence.objects.filter(search=search).exists()
        return task

    def make_retry_available(self, task):
        FaceSearchTask.objects.filter(pk=task.pk).update(available_at=timezone.now())


class ApparelPublicationCommitTests(TransactionTestCase):
    setUp = test_gallery_queue.GalleryQueueTests.setUp
    make_event = test_gallery_queue.GalleryQueueTests.make_event
    make_eligible_embedding = test_gallery_queue.GalleryQueueTests.make_eligible_embedding
    create_search = test_gallery_queue.GalleryQueueTests.create_search

    def test_commit_failure_rolls_back_publication_and_updates_queue_counters(self):
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        search = self.create_search()
        task = search.matching_task
        prepare_result = face_search_queue.prepare_search_result
        counter = QueueWorkCounter.objects.get(queue="gallery-search", processor="")
        assert (counter.waiting, counter.processing) == (1, 0)

        def write_bad_result_at_commit(current_search, **kwargs):
            prepare_result(current_search, **kwargs)
            if kwargs["intended_status"] == SelfieSearch.Status.READY:
                # The deferred foreign key raises when PostgreSQL commits, after publication.
                SelfieSearchResult.objects.create(
                    search_id=uuid4(), photo_id=current_search.results.get().photo_id, rank=1
                )

        with (
            patch.object(
                face_search_queue, "prepare_search_result", side_effect=write_bad_result_at_commit
            ),
            patch.object(face_search_queue, "notify_queue_completion") as publish_completion,
        ):
            assert run_once()
            search.refresh_from_db()
            task.refresh_from_db()
            counter.refresh_from_db()
            assert search.status == SelfieSearch.Status.QUEUED
            assert task.status == FaceSearchTask.Status.QUEUED
            assert (counter.waiting, counter.processing) == (1, 0)
            assert not search.results.exists()
            assert not SelfieSearchApparelEvidence.objects.filter(search=search).exists()

            FaceSearchTask.objects.filter(pk=task.pk).update(available_at=timezone.now())
            assert run_once()
        publish_completion.assert_not_called()
        search.refresh_from_db()
        task.refresh_from_db()
        counter.refresh_from_db()
        assert search.status == SelfieSearch.Status.FAILED
        assert search.failure_code == "apparel_search_unavailable"
        assert task.status == FaceSearchTask.Status.TERMINAL
        assert (counter.waiting, counter.processing) == (0, 0)
        assert not search.results.exists()
        assert not SelfieSearchApparelEvidence.objects.filter(search=search).exists()

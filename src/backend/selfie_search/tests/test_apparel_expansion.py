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
from face_cluster_contract import POLICY_ID, cluster_expansion_policy_hash
from feature_flags.models import FeatureFlag
from picflow.gallery_media_projection import publish_gallery_media
from picflow.models import Event
from processing.contracts import ClaimedJob
from processing.models import (
    EventFaceClusterActivation,
    FaceCluster,
    FaceClusterCorpus,
    FaceClusterMember,
    FaceEmbeddingVector,
    PersonAppearanceDetection,
    PhotoFaceDetection,
    PhotoProcessingState,
    QueueWorkCounter,
)
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
from selfie_search.services.apparel_expansion import (
    POLICY,
    candidate_veto,
    expand_apparel,
    verify_apparel_anchors,
)
from selfie_search.services.face_search_queue import run_once
from selfie_search.services.submission import submit_gallery_photo_search
from selfie_search.tests import test_anchor_expansion, test_gallery_queue, test_submission


def _regional_color_vector(head: float, torso: float | None) -> list[float]:
    scale = sqrt(0.5) if torso is not None else 1.0
    vector = [head * scale, sqrt(1 - head**2) * scale] + [0.0] * 190
    if torso is None:
        return vector + [0.0] * 192
    return vector + [torso * scale, sqrt(1 - torso**2) * scale] + [0.0] * 190


def _rejected_person(bbox, *, reason="strong_blur", head=None):
    return {
        "bbox": bbox,
        "confidence": 0.8,
        "reason": reason,
        "focus": [50, 50, 12, 12],
        "head": head,
    }


class ApparelPolicyTests(SimpleTestCase):
    def test_face_distance_and_helmet_similarity_use_distinct_units(self):
        assert (
            candidate_veto(face_distance=0.55, helmet_similarity=0.68, color_similarity=0.7) == ""
        )
        assert (
            candidate_veto(face_distance=0.551, helmet_similarity=None, color_similarity=0.6)
            == "face_veto"
        )
        assert (
            candidate_veto(face_distance=None, helmet_similarity=0.679, color_similarity=0.6)
            == "helmet_veto"
        )
        assert candidate_veto(face_distance=None, helmet_similarity=None, color_similarity=1) == ""


class ApparelQueueTests(TestCase):
    event: Event
    setUp = test_submission.GalleryPhotoSubmissionTests.setUp
    make_event = test_submission.GalleryPhotoSubmissionTests.make_event
    make_photo = test_submission.GalleryPhotoSubmissionTests.make_photo
    make_eligible_embedding = test_submission.GalleryPhotoSubmissionTests.make_eligible_embedding
    make_additional_face = test_submission.GalleryPhotoSubmissionTests.make_additional_face
    publish_preview = test_views.WorkerApiTests.publish_preview
    submit_gallery = test_anchor_expansion.AnchorExpansionTests.submit_gallery
    submit_selfie = test_anchor_expansion.AnchorExpansionTests.submit_selfie
    result_photos = test_anchor_expansion.AnchorExpansionTests.result_photos

    def index_appearance(
        self,
        identifier,
        *,
        face_similarity=None,
        face_axis=1,
        face_vector=None,
        reason="",
        event=None,
        associate_face=True,
        second_head=False,
        reid_similarity=1.0,
        color_similarity=1.0,
        head_color_similarity=None,
        zero_torso=False,
        color_vector=None,
        headless_bystander_matches=None,
        unassigned_head=False,
        helmet_similarity=None,
        extra_people=(),
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
        if face_vector is None and face_similarity is not None:
            face_vector = [0.0] * 512
            face_vector[0] = face_similarity
            face_vector[face_axis] = sqrt(1 - face_similarity**2)
        if face_vector is not None:
            face = self.make_eligible_embedding(
                event=event,
                photo_id=photo.pk,
                photo=photo,
                vector=face_vector,
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
                color=color_vector
                or _regional_color_vector(
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
                    "bbox": [150, 0, 450, 300],
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
        rows.extend(extra_people)
        head_count = int(face is not None) + int(second_head) + int(unassigned_head)
        head_count += sum(person["head"] is not None for person in extra_people)
        complete_attempt(claim.attempt.pk, result=appearance_result(*rows, head_count=head_count))
        return photo, face

    def test_empty_anchor_proposals_skip_the_reader(self):
        search = self.submit_selfie(self.user)
        with patch("selfie_search.services.apparel_expansion.Cursor") as cursor:
            verification = verify_apparel_anchors(
                search, (), embedding_model="adaface-ir18-webface4m", gallery=False
            )
        cursor.assert_not_called()
        assert verification.outcome == "no_compatible_appearance"
        assert verification.anchors == ()
        assert verification.checks == []
        assert verification.comparisons == []

    def test_gallery_source_candidate_veto_and_stale_preview_are_event_scoped(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        self.index_appearance("good-no-face")
        self.index_appearance("veto-face", face_similarity=0.1)
        self.index_appearance("other-event-appearance", event=self.other_event)
        hidden, _ = self.index_appearance("hidden-appearance")
        hidden.is_hidden = True
        hidden.save(update_fields=["is_hidden"])
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

    def test_addition_requires_color_agreement_with_the_best_reid_anchor(self):
        _, source = self.index_source_with_processing_enabled()
        self.index_appearance(
            "other-anchor", face_similarity=0.9, reid_similarity=0.94, color_similarity=0.9
        )
        self.index_appearance("closer-color-to-other-anchor", color_similarity=0.65)
        self.index_appearance("addition-color-above-minimum", color_similarity=0.72)
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        search = self.submit_gallery(source)

        assert run_once()
        assert self.result_photos(search) == [
            "apparel-source",
            "other-anchor",
            "addition-color-above-minimum",
        ]
        decisions = search.apparel_evidence.decisions
        assert [row["photo_id"] for row in decisions["anchors"]] == [
            "apparel-source",
            "other-anchor",
        ]
        candidate = next(
            row
            for row in decisions["candidates"]
            if row["photo_id"] == "closer-color-to-other-anchor"
        )
        assert candidate["veto"] == "color_below_threshold"
        assert candidate["source_person_id"] == decisions["anchors"][0]["person_id"]
        assert candidate["color_cosine_similarity"] == 0.65
        assert candidate["face_cosine_distance"] is None
        assert candidate["helmet_cosine_similarity"] is None
        assert decisions["policy"]["addition_color_cosine_similarity_min"] == 0.70
        assert decisions["policy"]["color_cosine_similarity_min"] == 0.80

    def test_addition_accepts_color_agreement_at_the_minimum(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        source_region = [0.5, 0.5] + [0.0] * 190
        _, source = self.index_appearance(
            "apparel-source", face_similarity=1.0, color_vector=source_region * 2
        )
        # Dyadic inputs keep 1 - cosine distance equal to 0.70 after float32 storage.
        scale = 205 / 2048
        shared = [3.5 * scale] * 2
        boundary_color = shared + [7 * scale, scale] + [0.0] * 188
        boundary_color += shared + [scale] + [0.0] * 189
        self.index_appearance("boundary-color", color_vector=boundary_color)
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        search = self.submit_gallery(source)

        assert run_once()
        assert self.result_photos(search) == ["apparel-source", "boundary-color"]
        candidate = search.apparel_evidence.decisions["candidates"][0]
        assert candidate["color_cosine_similarity"] == 0.70
        assert candidate["veto"] == ""

    def test_initial_gallery_centroid_includes_the_verified_source(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        FeatureFlag.objects.create(key="face-search-anchor-expansion", state="on")
        _, source = self.index_appearance("gallery-source", face_similarity=1.0)
        _, anchor = self.index_appearance("direct-anchor", face_similarity=0.74)
        # Cosine similarity is 0.55 to both individual faces and above 0.58 to their centroid.
        second = 0.55 * (1 - 0.74) / sqrt(1 - 0.74**2)
        face_vector = [0.55, second, sqrt(1 - 0.55**2 - second**2)] + [0.0] * 509
        # Keep the photo outside apparel retrieval, without qualifying it for exclusion.
        self.index_appearance("combined-only", face_vector=face_vector, reid_similarity=0.6)

        search = self.submit_gallery(source)

        assert run_once()
        assert self.result_photos(search) == ["gallery-source", "direct-anchor", "combined-only"]
        result = search.results.get(photo_id="combined-only")
        assert result.primary_source == SelfieSearchResult.PrimarySource.FACE_ANCHOR_EXPANSION
        assert not result.excluded_by_apparel
        assert search.published_matched_photo_count == 3
        assert list(
            result.anchor_evidence.sources.values_list("anchor__detection_id", flat=True)
        ) == [source.detection_id, anchor.detection_id]

    def test_results_continue_from_the_apparel_match_face_on_a_group_photo(self):
        from selfie_search.services.continuation import (
            available_manual_photo_ids,
            submit_continuation,
        )

        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        self.index_appearance("confirmed-first-pass-face", face_similarity=0.75)
        _, target = self.index_appearance("group", face_similarity=0.55, reid_similarity=0.75)
        self.make_additional_face(
            embedding=target,
            vector=[0.0, 0.0, 1.0] + [0.0] * 509,
            geometry={
                "coordinate_space": "preview-small-v1",
                "pixel_width": 100,
                "pixel_height": 100,
                "bbox": [75, 20, 20, 20],
            },
        )
        search = self.submit_gallery(source)
        assert run_once()
        result = search.results.get(photo_id="group")
        assert result.primary_source == SelfieSearchResult.PrimarySource.APPAREL_EXPANSION
        assert available_manual_photo_ids(search, result_rows=(result,), apparel_enabled=True) == {
            "group"
        }

        child = submit_continuation(search=search, user=self.user, photo_id="group")
        assert child.configuration["query_source"]["detection_id"] == str(target.detection_id)
        assert run_once()
        assert self.result_photos(child) == []

    def test_apparel_found_face_requires_first_pass_confirmation(self):
        from selfie_search.services.continuation import submit_continuation

        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        self.index_appearance("unconfirmed", face_similarity=0.55, reid_similarity=0.75)
        self.index_appearance("shape-only-addition", reid_similarity=0.5)
        search = self.submit_gallery(source)
        assert run_once()
        assert self.result_photos(search) == ["apparel-source", "unconfirmed"]

        child = submit_continuation(search=search, user=self.user, photo_id="unconfirmed")

        assert child.configuration["query_source"]["kind"] == "continuation_apparel"
        assert run_once()
        assert self.result_photos(child) == ["shape-only-addition"]

    def test_results_can_continue_from_the_saved_person_without_a_face(self):
        from selfie_search.services.submission import _token_digest

        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        FeatureFlag.objects.create(key="selfie-search-passes", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        self.index_appearance(
            "saved-person", face_similarity=0.1, associate_face=False, reid_similarity=0.75
        )
        self.index_appearance("new-no-face", reid_similarity=0.5)
        self.index_appearance("wrong-face", face_similarity=0.1, reid_similarity=0.5)
        self.index_appearance("wrong-color", reid_similarity=0.5, color_similarity=0.0)
        search = self.submit_gallery(source)
        token = uuid4().hex
        SelfieSearch.objects.filter(pk=search.pk).update(public_token_digest=_token_digest(token))
        kwargs = {"event_slug": self.event.slug, "public_token": token}
        result_url = reverse("selfie_search:result", kwargs=kwargs)
        continue_url = reverse("selfie_search:continue", kwargs=kwargs)

        assert run_once()
        assert self.result_photos(search) == ["apparel-source", "saved-person"]
        page = self.client.get(result_url)
        assert "saved-person" in page.context["continuation_photo_ids"]

        gate = FeatureFlag.objects.filter(key="apparel-search-expansion")
        gate.update(state="off")
        page = self.client.get(result_url)
        assert page.context["continuation_photo_ids"] == {"apparel-source"}
        response = self.client.post(
            continue_url, {"photo_id": "saved-person"}, HTTP_ACCEPT="application/json"
        )
        assert response.status_code == 409
        assert not search.continuation_passes.exists()
        gate.update(state="on")

        response = self.client.post(
            continue_url, {"photo_id": "saved-person"}, HTTP_ACCEPT="application/json"
        )
        assert response.status_code == 200
        assert response.json()["result_url"] == result_url
        assert run_once()
        child = search.continuation_passes.get()
        assert self.result_photos(child) == ["new-no-face", "wrong-face"]
        assert child.results.get(photo_id="wrong-face").excluded_by_face_history
        assert (
            child.apparel_evidence.decisions["policy"]["addition_color_cosine_similarity_min"]
            == 0.70
        )
        assert child.published_matched_photo_count == 1
        page = self.client.get(result_url)
        assert "new-no-face" in page.context["continuation_photo_ids"]

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
        assert sources == [source.detection_id, clean.detection_id]
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

    def test_small_headless_quality_rejections_do_not_protect_a_mismatching_face(self):
        _, source = self.index_source_with_processing_enabled()
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        area_boundary = [150, 0, 180, 100]
        height_boundary = [200, 0, 220, 105]
        photo, matched = self.index_appearance(
            "small-background",
            face_similarity=0.83,
            reid_similarity=0.42,
            color_similarity=0.72,
            extra_people=(
                _rejected_person(area_boundary, reason="lack_details"),
                _rejected_person(height_boundary),
            ),
        )
        search = self.submit_gallery(source)

        assert run_once()
        assert self.result_photos(search) == ["apparel-source", "small-background"]
        assert search.results.get(photo=photo).excluded_by_apparel
        assert search.published_matched_photo_count == 1
        people = list(
            PersonAppearanceDetection.objects.filter(attempt__photo=photo).order_by("ordinal")
        )
        decisions = search.apparel_evidence.decisions
        assert decisions["policy"]["version"] == 3
        excluded = decisions["exclusions"][0]
        assert excluded["photo_id"] == photo.pk
        expected_background = [
            {
                "person_id": person.pk,
                "reason": person.reason,
                "bbox": person.bbox,
                "area_ratio": area_ratio,
                "height_ratio": height_ratio,
                "reference_person_id": people[0].pk,
                "reference_detection_id": str(matched.detection_id),
                "reference_bbox": [0, 0, 100, 300],
                "decision_reason": "small_headless_quality_rejection",
            }
            for person, area_ratio, height_ratio in (
                (people[1], 0.10, 1 / 3),
                (people[2], 0.07, 0.35),
            )
        ]
        assert excluded["ignored_background_people"] == expected_background
        assert decisions["recommendations"][0]["ignored_background_people"] == expected_background

    def test_small_background_rule_keeps_other_group_protection(self):
        _, source = self.index_source_with_processing_enabled()
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        small_background = _rejected_person([150, 0, 170, 70])
        for photo_id, other_people in (
            ("assigned-head", (_rejected_person([150, 0, 170, 70], head=[153, 0, 165, 15]),)),
            ("unassigned-head", (small_background,)),
            ("large-area", (_rejected_person([150, 0, 190, 90]),)),
            ("too-tall", (_rejected_person([150, 0, 167, 106]),)),
            ("different-reason", (_rejected_person([150, 0, 170, 70], reason="mixed_sharpness"),)),
            (
                "larger-unrelated-figure",
                (_rejected_person([460, 0, 500, 90]),),
            ),
        ):
            self.index_appearance(
                photo_id,
                face_similarity=0.83,
                reid_similarity=0.42,
                color_similarity=0.72,
                extra_people=other_people,
                unassigned_head=photo_id == "unassigned-head",
                headless_bystander_matches=False if photo_id == "larger-unrelated-figure" else None,
            )
        search = self.submit_gallery(source)

        assert run_once()
        assert len(self.result_photos(search)) == 7
        assert search.published_matched_photo_count == 7
        decisions = search.apparel_evidence.decisions
        assert decisions["exclusions"] == []
        recommendations = {row["photo_id"]: row for row in decisions["recommendations"]}
        assert all(row["protection"] == "uncertain_figure" for row in recommendations.values())
        assert {p for p, row in recommendations.items() if row["ignored_background_people"]} == {
            "unassigned-head"
        }

    @override_settings(SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=True)
    def test_every_matched_face_must_disagree_after_ignoring_small_background(self):
        _, source = self.index_source_with_processing_enabled()
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        _, anchor = self.index_appearance("cluster-anchor", face_similarity=0.92)
        photo, matched = self.index_appearance(
            "group-with-cluster-member",
            face_similarity=0.83,
            reid_similarity=0.42,
            color_similarity=0.72,
            extra_people=(_rejected_person([150, 0, 170, 70]),),
        )
        unlinked = PhotoFaceDetection.objects.create(
            artifact=matched.detection.artifact,
            attempt=matched.detection.attempt,
            face_index=1,
            status=PhotoFaceDetection.Status.KEPT,
            geometry=matched.detection.geometry | {"bbox": [70, 20, 20, 20]},
        )
        FaceEmbeddingVector.objects.create(
            detection=unlinked,
            model_version=source.model_version,
            vector=[0.55, sqrt(1 - 0.55**2)] + [0] * 510,
        )
        search = self.submit_gallery(source)
        corpus = FaceClusterCorpus.objects.create(
            event=self.event,
            version=1,
            algorithm_version="guarded-graph-v1",
            configuration={
                "face_embedding_generations": search.configuration[
                    "gallery_face_embedding_generations"
                ]
            },
            configuration_hash="b" * 64,
            contract_version=1,
            processor_type="face_embedding",
            processor_version=1,
            model_version=source.model_version,
            embedding_dimensions=512,
            edge_threshold=0.2,
            representative_threshold=0.2,
            distance_block_size=1,
            max_candidate_edges=1,
        )
        cluster = FaceCluster.objects.create(
            event=self.event,
            corpus=corpus,
            cluster_key="retained-group",
            representative_detection=anchor.detection,
            member_count=2,
        )
        for index, (detection, distance) in enumerate(((anchor.detection, 0.0), (unlinked, 0.17))):
            FaceClusterMember.objects.create(
                event=self.event,
                corpus=corpus,
                cluster=cluster,
                detection=detection,
                member_index=index,
                distance_to_representative=distance,
            )
        FaceClusterCorpus.objects.filter(pk=corpus.pk).update(
            status=FaceClusterCorpus.Status.PUBLISHED, published_at=timezone.now()
        )
        threshold = search.configuration["cosine_distance_threshold"]
        EventFaceClusterActivation.objects.create(
            event=self.event,
            corpus=corpus,
            anchor_threshold=0.2,
            configuration={
                "policy_id": POLICY_ID,
                "corpus_configuration_hash": corpus.configuration_hash,
                "direct_threshold": threshold,
                "anchor_threshold": 0.2,
            },
            configuration_hash=cluster_expansion_policy_hash(
                corpus.configuration_hash, threshold, 0.2
            ),
            approved_evaluation_report_hash="d" * 64,
        )

        assert run_once()
        assert self.result_photos(search) == [
            "apparel-source",
            "cluster-anchor",
            "group-with-cluster-member",
        ]
        result = search.results.get(photo=photo)
        assert result.direct_evidence.detection_id == matched.detection_id
        assert result.cluster_evidence.get().member_detection_id == unlinked.pk
        assert not result.excluded_by_apparel
        decisions = search.apparel_evidence.decisions
        assert decisions["exclusions"] == []
        recommendation = decisions["recommendations"][0]
        assert recommendation["photo_id"] == photo.pk
        assert recommendation["protection"] == ""
        assert len(recommendation["ignored_background_people"]) == 1

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
        assert list(
            centroid.anchor_evidence.sources.values_list("anchor__detection_id", flat=True)
        ) == [source.detection_id, excluded.direct_evidence.detection_id]
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

    def test_reid_probe_keeps_ties_and_filters_missing_descriptors_before_limit(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        _, source = self.index_appearance("probe-source", face_similarity=1.0)
        for photo_id in ("a-no-color", "b-no-reid", "c-first", "d-second", "e-overflow"):
            photo, _ = self.index_appearance(photo_id, helmet_similarity=None)
            if photo_id in ("a-no-color", "b-no-reid"):
                PersonAppearanceDetection.objects.filter(attempt__photo=photo).update(
                    **{"color" if photo_id == "a-no-color" else "reid": None}
                )
        search = self.submit_gallery(source)
        with patch.dict(POLICY, {"max_candidates": 2}):
            assert run_once()
        decisions = search.apparel_evidence.decisions
        assert decisions["candidates_truncated"]
        assert [row["photo_id"] for row in decisions["candidates"]] == ["c-first", "d-second"]
        assert all(row["helmet_cosine_similarity"] is None for row in decisions["candidates"])
        assert list(
            search.results.filter(primary_source=SelfieSearchResult.PrimarySource.APPAREL_EXPANSION)
            .order_by("rank")
            .values_list("photo_id", flat=True)
        ) == ["c-first", "d-second"]

    def test_face_association_only_reads_people_on_selected_photos(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        detection_ids = {}
        for index in range(12):
            photo_id = f"other-person-{index}"
            _, face = self.index_appearance(photo_id, face_similarity=0.1)
            assert face is not None
            detection_ids[photo_id] = str(face.detection_id)
        for index in range(4):
            self.index_appearance(
                f"outside-reid-probe-{index}", face_similarity=0.1, reid_similarity=0.2
            )
        search = self.submit_gallery(source)
        candidate_limit = 3
        rows_per_operation = []
        association_keys = {
            "processing_processingattempt": "id",
            "processing_photofacedetection": "attempt_id",
            "processing_faceembeddingvector": "detection_id",
        }

        def collect_association_work(node):
            loops = node["Actual Loops"]
            if node["Node Type"] == "WindowAgg":
                rows_per_operation.append(node["Actual Rows"] * loops)
            rows_per_operation.append(node.get("Rows Removed by Join Filter", 0) * loops)
            key = association_keys.get(node.get("Relation Name"))
            if key is not None and loops > 1:
                condition = node.get("Index Cond", node.get("Recheck Cond", ""))
                assert f"{key} =" in condition, node["Node Type"]
            for child in node.get("Plans", []):
                collect_association_work(child)

        class PlanRecordingCursor(Cursor):
            def execute(self, query, params=None, **kwargs):
                if params is not None:
                    super().execute("EXPLAIN (ANALYZE, FORMAT JSON, TIMING OFF) " + query, params)
                    explained = self.fetchone()
                    assert explained is not None
                    plan = explained[0][0]["Plan"]

                    def record_associations(node):
                        if node.get("Subplan Name") in {"CTE linked", "CTE resolved_people"}:
                            collect_association_work(node)
                        else:
                            for child in node.get("Plans", []):
                                record_associations(child)

                    record_associations(plan)
                return super().execute(query, params, **kwargs)

        # A higher page cost exposes repeated scans even with this small fixture.
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL random_page_cost = 40")
        with (
            patch("selfie_search.services.apparel_expansion.Cursor", PlanRecordingCursor),
            patch.dict(POLICY, {"max_candidates": candidate_limit}),
        ):
            assert run_once()
        decisions = search.apparel_evidence.decisions
        assert decisions["candidates_truncated"]
        assert [(row["photo_id"], row["veto"]) for row in decisions["candidates"]] == [
            (photo_id, "face_veto")
            for photo_id in ("other-person-0", "other-person-1", "other-person-10")
        ]
        assert all(
            row["detection_id"] == detection_ids[row["photo_id"]] for row in decisions["candidates"]
        )
        # The face veto runs after the ordered ReID probe. Each selected photo has one
        # person: the probe contains one overflow row and the baseline has the source.
        assert max(rows_per_operation) <= candidate_limit + 2

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
        low_reid_bystander = {
            "bbox": [1, 0, 101, 300],
            "confidence": 0.8,
            "reason": "",
            "focus": [450, 450, 12, 12],
            "head": [21, 20, 60, 60],
            "reid": [0, 1] + [0] * 254,
            "color": _regional_color_vector(0.3, 0.3),
            "helmet": None,
        }
        self.index_appearance(
            "one-face-two-people", face_similarity=0.1, extra_people=(low_reid_bystander,)
        )
        self.index_appearance(
            "ambiguous-direct", face_similarity=0.95, extra_people=(low_reid_bystander,)
        )

        search = self.submit_gallery(source)
        assert run_once()
        decisions = search.apparel_evidence.decisions
        assert [row["photo_id"] for row in decisions["anchors"]] == ["apparel-source"]
        assert decisions["recommendations"] == []
        anchor_check = next(
            row for row in decisions["anchor_checks"] if row["photo_id"] == "ambiguous-direct"
        )
        assert anchor_check["reason"] == "face_association_unknown"
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

    def test_unassessable_apparel_groups_do_not_hide_later_continuation_anchors(self):
        from selfie_search.services.continuation import (
            consumed_anchor_photo_ids,
            submit_continuation,
        )

        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        _, seed = self.index_appearance("gallery-seed", face_similarity=1.0)
        for axis in range(1, 4):
            self.index_appearance(
                f"unassessable-{axis}",
                face_similarity=0.73,
                face_axis=axis,
                second_head=True,
            )
        self.index_appearance("usable-reference", face_similarity=0.65, face_axis=4)
        self.index_appearance("appearance-only-result")
        root = self.submit_gallery(seed)
        assert run_once()
        root.refresh_from_db()
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")

        child = submit_continuation(search=root, user=self.user, photo_id=None)
        assert run_once()
        assert self.result_photos(child) == ["appearance-only-result"]
        assert consumed_anchor_photo_ids(root) == {"gallery-seed", "usable-reference"}

    def test_automatic_continuation_keeps_incompatible_apparel_anchors_for_later_pass(self):
        from selfie_search.services.continuation import (
            consumed_anchor_photo_ids,
            submit_continuation,
        )

        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        _, seed = self.index_appearance("initial-gallery-seed", face_similarity=1.0)
        self.index_appearance("grey-reference", face_similarity=0.7)
        self.index_appearance(
            "pink-reference", face_similarity=0.8, reid_similarity=0.0, color_similarity=0.0
        )
        self.index_appearance("grey-addition")
        self.index_appearance("pink-addition", reid_similarity=0.0, color_similarity=0.0)
        root = self.submit_gallery(seed)
        assert run_once()
        root.refresh_from_db()
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")

        first = submit_continuation(search=root, user=self.user, photo_id=None)
        from selfie_search.services import apparel_expansion

        with patch.object(
            apparel_expansion, "_read_anchor_checks", wraps=apparel_expansion._read_anchor_checks
        ) as read_checks:
            assert run_once()
            read_checks.assert_called_once()
        assert self.result_photos(first) == ["pink-addition"]
        assert first.apparel_evidence.decisions["centroid_anchor_outcomes"] == ["verified"]
        assert consumed_anchor_photo_ids(root) == {"initial-gallery-seed", "pink-reference"}

        second = submit_continuation(search=root, user=self.user, photo_id=None)
        assert run_once()
        assert self.result_photos(second) == ["grey-addition"]
        assert second.apparel_evidence.decisions["centroid_anchor_outcomes"] == ["verified"]

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

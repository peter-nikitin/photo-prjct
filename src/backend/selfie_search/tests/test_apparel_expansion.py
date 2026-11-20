from math import sqrt
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase
from feature_flags.models import FeatureFlag
from picflow.gallery_media_projection import publish_gallery_media
from picflow.models import Event
from processing.contracts import ClaimedJob
from processing.models import PhotoProcessingState
from processing.services.enrollment import _derivative_fingerprint
from processing.services.jobs import claim_job, complete_attempt
from processing.services.person_appearance import request_person_appearance
from processing.tests import test_views
from processing.tests.test_appearance import appearance_result
from selfie_search.models import SelfieSearchResult
from selfie_search.services.apparel_expansion import candidate_veto
from selfie_search.services.face_search_queue import run_once
from selfie_search.tests import test_anchor_expansion, test_submission


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
        self, identifier, *, face_similarity=None, reason="", event=None, associate_face=True
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
            row.update(reid=[1] + [0] * 255, color=[1] + [0] * 383, helmet=None)
        complete_attempt(
            claim.attempt.pk, result=appearance_result(row, head_count=int(face is not None))
        )
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

    def test_gate_off_does_not_enter_optional_appearance_step(self):
        _, source = self.index_source_with_processing_enabled()
        search = self.submit_gallery(source)
        with patch("selfie_search.services.face_search_queue.expand_apparel") as apparel:
            assert run_once()
        apparel.assert_not_called()
        assert self.result_photos(search) == ["apparel-source"]

    def index_source_with_processing_enabled(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        return self.index_appearance("apparel-source", face_similarity=1.0)

    def test_selfie_staff_expansion_does_not_veto_from_another_person_in_the_group(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        FeatureFlag.objects.create(key="apparel-search-expansion", state="staff")
        self.index_appearance("anchor", face_similarity=1.0)
        self.index_appearance("person-without-face", face_similarity=0.1, associate_face=False)
        visitor = self.submit_selfie(self.user)
        staff = get_user_model().objects.create_user(username="apparel-reviewer", is_staff=True)
        private = self.submit_selfie(staff)
        assert run_once()
        assert run_once()
        assert self.result_photos(visitor) == ["anchor"]
        assert self.result_photos(private) == ["anchor", "person-without-face"]
        decisions = private.apparel_evidence.decisions
        assert len(decisions["anchors"]) == 1
        assert decisions["anchors"][0]["photo_id"] == "anchor"
        assert decisions["candidates"][0]["face_cosine_distance"] is None

    def test_apparel_database_failure_keeps_face_baseline(self):
        FeatureFlag.objects.create(key="person-appearance-processing", state="on")
        _, source = self.index_appearance("apparel-source", face_similarity=1.0)
        FeatureFlag.objects.create(key="apparel-search-expansion", state="on")
        search = self.submit_gallery(source)
        with patch(
            "selfie_search.services.face_search_queue.expand_apparel", side_effect=DatabaseError
        ):
            assert run_once()
        assert self.result_photos(search) == ["apparel-source"]
        assert search.apparel_expanded_photo_count == 0
        assert not hasattr(search, "apparel_evidence")

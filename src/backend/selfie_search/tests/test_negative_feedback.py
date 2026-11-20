from math import cos, sin
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse
from feature_flags.models import FeatureFlag
from feature_flags.registry import BULK_PHOTO_DOWNLOAD
from feature_flags.states import FEATURE_FLAG_ON
from picflow.models import PhotoArchiveJob
from processing.models import PhotoProcessingState
from search_feedback.models import SearchFeedback
from selfie_search.models import SelfieSearch, SelfieSearchDirectEvidence, SelfieSearchResult
from selfie_search.services.face_search_queue import run_once
from selfie_search.services.submission import _gallery_configuration, _token_digest
from selfie_search.tests import test_submission as fixtures


class NegativeFeedbackTests(TestCase):
    setUp = fixtures.GalleryPhotoSubmissionTests.setUp
    make_event = fixtures.GalleryPhotoSubmissionTests.make_event
    make_eligible_embedding = fixtures.GalleryPhotoSubmissionTests.make_eligible_embedding
    make_additional_face = fixtures.GalleryPhotoSubmissionTests.make_additional_face

    def root(self):
        token = uuid4().hex
        search = SelfieSearch.objects.create(
            event=self.event,
            status=SelfieSearch.Status.READY,
            public_token_digest=_token_digest(token),
            configuration=_gallery_configuration(event=self.event),
        )
        return search, token

    def save_photo(self, search, name, angle, *, distance=0.37, source="direct"):
        face = self.make_eligible_embedding(
            event=self.event, photo_id=name, vector=[cos(angle), sin(angle)] + [0.0] * 510
        )
        photo = face.detection.attempt.photo
        photo.original_key = f"originals/{uuid4().hex}"
        photo.save(update_fields=["original_key"])
        row = SelfieSearchResult.objects.create(
            search=search,
            photo_id=name,
            rank=search.results.count() + 1,
            primary_source=source,
        )
        if source == "direct":
            SelfieSearchDirectEvidence.objects.create(
                result=row, detection_id=face.detection_id, cosine_distance=distance
            )
        return row

    def report(self, token, photo_id, action, *, submission_id=None):
        return self.client.post(
            reverse("search_feedback_submit", args=(self.event.slug,)),
            data={
                "public_token": token,
                "photo_id": photo_id,
                "action": action,
                "submission_id": str(submission_id or uuid4()),
            },
        )

    def test_mark_and_undo_are_idempotent_and_keep_history(self):
        search, token = self.root()
        self.save_photo(search, "wrong", 0.0)
        self.save_photo(search, "near", 0.1)
        self.save_photo(search, "second-hop", 0.8)
        self.save_photo(search, "outside", 1.4)
        self.save_photo(search, "apparel-only", 0.1, source="apparel_expansion")
        action_id = uuid4()

        marked = self.report(token, "wrong", "mark", submission_id=action_id)
        self.assertEqual(marked.status_code, 201)
        self.assertEqual(marked.json()["manual_photo_ids"], ["wrong"])
        self.assertEqual(set(marked.json()["similar_photo_ids"]), {"near"})
        self.assertEqual(self.report(token, "wrong", "mark").status_code, 201)
        self.assertEqual(SearchFeedback.objects.filter(withdrawn_at__isnull=True).count(), 1)

        FeatureFlag.objects.create(key=BULK_PHOTO_DOWNLOAD.key, state=FEATURE_FLAG_ON)
        archive = self.client.post(
            reverse("selfie_search:result_archive", args=(self.event.slug, token))
        )
        self.assertEqual(archive.status_code, 202)
        self.assertEqual(
            {entry["photo_id"] for entry in PhotoArchiveJob.objects.get().entries},
            {"near", "second-hop", "outside", "apparel-only"},
        )

        undone = self.report(token, "wrong", "unmark")
        self.assertEqual(undone.status_code, 200)
        self.assertEqual(undone.json()["manual_photo_ids"], [])
        self.assertEqual(undone.json()["similar_photo_ids"], [])
        self.assertEqual(self.report(token, "wrong", "unmark").status_code, 200)
        self.assertEqual(SearchFeedback.objects.count(), 4)
        self.assertEqual(
            SearchFeedback.objects.filter(value=SearchFeedback.Value.UNMARK).count(), 2
        )
        self.assertIsNotNone(SearchFeedback.objects.get(submission_id=action_id).withdrawn_at)
        self.assertEqual(
            self.report(token, "wrong", "mark", submission_id=action_id).json()["manual_photo_ids"],
            [],
        )

    def test_retried_noop_unmark_does_not_withdraw_a_later_mark(self):
        search, token = self.root()
        self.save_photo(search, "wrong", 0.0)
        first_undo = uuid4()
        self.assertEqual(
            self.report(token, "wrong", "unmark", submission_id=first_undo).json()[
                "manual_photo_ids"
            ],
            [],
        )
        self.assertEqual(self.report(token, "wrong", "mark").json()["manual_photo_ids"], ["wrong"])

        replay = self.report(token, "wrong", "unmark", submission_id=first_undo)

        self.assertEqual(replay.json()["manual_photo_ids"], ["wrong"])

    def test_retried_noop_mark_does_not_restore_a_withdrawn_mark(self):
        search, token = self.root()
        self.save_photo(search, "wrong", 0.0)
        self.assertEqual(self.report(token, "wrong", "mark").json()["manual_photo_ids"], ["wrong"])
        second_mark = uuid4()
        self.assertEqual(
            self.report(token, "wrong", "mark", submission_id=second_mark).json()[
                "manual_photo_ids"
            ],
            ["wrong"],
        )
        self.assertEqual(self.report(token, "wrong", "unmark").json()["manual_photo_ids"], [])

        replay = self.report(token, "wrong", "mark", submission_id=second_mark)

        self.assertEqual(replay.json()["manual_photo_ids"], [])

    def test_retried_undo_does_not_withdraw_a_new_mark_from_another_tab(self):
        search, token = self.root()
        self.save_photo(search, "wrong", 0.0)

        self.assertEqual(self.report(token, "wrong", "mark").status_code, 201)
        undo_id = uuid4()
        self.assertEqual(
            self.report(token, "wrong", "unmark", submission_id=undo_id).json()["manual_photo_ids"],
            [],
        )
        self.assertEqual(self.report(token, "wrong", "mark").json()["manual_photo_ids"], ["wrong"])

        retry = self.report(token, "wrong", "unmark", submission_id=undo_id)

        self.assertEqual(retry.json()["manual_photo_ids"], ["wrong"])
        self.assertEqual(SearchFeedback.objects.filter(withdrawn_at__isnull=True).count(), 1)

    def test_explicit_gallery_seed_on_group_photo_protects_its_own_match(self):
        from selfie_search.services.submission import submit_gallery_photo_search

        seed = self.make_eligible_embedding(
            event=self.event, photo_id="gallery-seed", vector=[cos(0.1), sin(0.1)] + [0.0] * 510
        )
        self.make_additional_face(embedding=seed, vector=[0.0] * 2 + [1.0] + [0.0] * 509)
        self.make_eligible_embedding(event=self.event, photo_id="wrong", vector=[1.0] + [0.0] * 511)
        created = submit_gallery_photo_search(
            event=self.event,
            photo=seed.detection.attempt.photo,
            detection_id=seed.detection_id,
            user=self.user,
        )
        self.assertTrue(run_once())
        row = created.search.results.get(photo_id="gallery-seed")
        self.assertEqual(row.direct_evidence.detection_id, seed.detection_id)

        state = self.report(created.public_token, "wrong", "mark").json()
        self.assertEqual(state["manual_photo_ids"], ["wrong"])
        self.assertEqual(state["similar_photo_ids"], [])
        state = self.report(created.public_token, "gallery-seed", "mark").json()
        self.assertEqual(state["manual_photo_ids"], ["gallery-seed", "wrong"])

    def test_closer_independent_positive_protects_a_matching_face(self):
        search, token = self.root()
        self.save_photo(search, "negative", 0.0)
        self.save_photo(search, "protected", 0.5)
        self.save_photo(search, "positive", 0.8, distance=0.1)
        self.save_photo(search, "near-negative", 0.1)

        result = self.report(token, "negative", "mark").json()

        self.assertEqual(result["similar_photo_ids"], ["near-negative"])

    def test_strong_direct_hits_close_to_a_negative_do_not_protect_one_another(self):
        search, token = self.root()
        self.save_photo(search, "wrong", 0.0, distance=0.12)
        self.save_photo(search, "same-other-person", 0.1, distance=0.1)
        self.save_photo(search, "another-hit-of-other-person", 0.05, distance=0.1)

        self.assertEqual(
            set(self.report(token, "wrong", "mark").json()["similar_photo_ids"]),
            {"same-other-person", "another-hit-of-other-person"},
        )

    def test_stale_negative_evidence_does_not_select_a_different_current_face(self):
        from selfie_search.services.negative_feedback import negative_feedback_state

        search, token = self.root()
        self.save_photo(search, "wrong", 0.0)
        self.save_photo(search, "near", 0.1)
        self.assertEqual(self.report(token, "wrong", "mark").json()["similar_photo_ids"], ["near"])
        PhotoProcessingState.objects.filter(
            photo_id="wrong", processor_type="face_embedding"
        ).update(accepted_attempt=None)

        state = negative_feedback_state(search)

        self.assertEqual(state.manual_photo_ids, {"wrong"})
        self.assertEqual(state.similar_photo_ids, set())

    def test_new_continuation_match_is_restored_on_undo(self):
        from selfie_search.services.continuation import ContinuationUnavailable, submit_continuation
        from selfie_search.services.negative_feedback import negative_feedback_state

        search, token = self.root()
        self.save_photo(search, "wrong", 0.0)
        self.save_photo(search, "earlier", 0.1)
        self.save_photo(search, "rerun-anchor", 0.6)
        self.make_eligible_embedding(
            event=self.event, photo_id="new-result", vector=[cos(0.12), sin(0.12)] + [0.0] * 510
        )
        self.assertEqual(self.report(token, "wrong", "mark").status_code, 201)
        child = submit_continuation(search=search, user=self.user, photo_id="rerun-anchor")
        self.assertTrue(run_once())
        row = child.results.get(photo_id="new-result")

        state = negative_feedback_state(search)
        self.assertEqual(state.similar_photo_ids, {"earlier", "rerun-anchor"})
        self.assertEqual(state.excluded_photo_ids, {"new-result"})
        self.assertEqual(self.report(token, "new-result", "mark").status_code, 404)
        with self.assertRaises(ContinuationUnavailable):
            submit_continuation(search=search, user=self.user, photo_id="new-result")
        self.assertEqual(self.report(token, "wrong", "unmark").json()["excluded_photo_count"], 0)
        self.assertTrue(SelfieSearchDirectEvidence.objects.filter(result=row).exists())

    def test_marked_or_dimmed_results_cannot_seed_automatic_continuation(self):
        from selfie_search.services.continuation import ContinuationUnavailable, submit_continuation

        search, token = self.root()
        self.save_photo(search, "wrong", 0.0)
        self.save_photo(search, "near", 0.1)
        self.assertEqual(self.report(token, "wrong", "mark").status_code, 201)

        with self.assertRaises(ContinuationUnavailable):
            submit_continuation(search=search, user=self.user, photo_id=None)

        self.assertEqual(self.report(token, "wrong", "unmark").status_code, 200)
        self.assertEqual(
            submit_continuation(search=search, user=self.user, photo_id=None).parent_search_id,
            search.pk,
        )

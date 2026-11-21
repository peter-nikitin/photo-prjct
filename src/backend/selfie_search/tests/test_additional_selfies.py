from unittest.mock import Mock, patch
from uuid import uuid4

from django.test import TestCase
from feature_flags.models import FeatureFlag
from feature_flags.registry import APPAREL_SEARCH_EXPANSION
from feature_flags.states import FEATURE_FLAG_ON
from selfie_search.models import SelfieSearch, SelfieSearchFeedback
from selfie_search.services.face_search_queue import run_once
from selfie_search.services.feedback import save_feedback_selfie
from selfie_search.services.jobs import (
    ClaimedSearchJob,
    claim_search_job,
    complete_search_attempt,
    fail_search_attempt,
)
from selfie_search.services.results import saved_ready_result_photos
from selfie_search.services.submission import submit_selfie_search
from selfie_search.tests import test_jobs as jobs_fixtures
from selfie_search.tests import test_submission as fixtures


class AdditionalSelfieTests(TestCase):
    setUp = fixtures.GalleryPhotoSubmissionTests.setUp
    make_event = fixtures.GalleryPhotoSubmissionTests.make_event
    make_eligible_embedding = fixtures.GalleryPhotoSubmissionTests.make_eligible_embedding
    recognition_result = jobs_fixtures.SearchJobTests.result

    def claim(self, search):
        claimed = claim_search_job(
            contract_version=1,
            processor_type="selfie_query",
            processor_version=2,
            lease_seconds=120,
        )
        assert isinstance(claimed, ClaimedSearchJob)
        self.assertEqual(claimed.job.search_id, search.pk)
        return claimed.attempt.id

    def fail_recognition(self, search):
        fail_search_attempt(self.claim(search), error_code="no_face_detected", retryable=False)
        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.NO_FACE)

    def finish(self, search):
        complete_search_attempt(self.claim(search), result=self.recognition_result())
        self.assertTrue(run_once())
        search.refresh_from_db()
        self.assertEqual(search.status, SelfieSearch.Status.READY)

    def submit_root(self, storage):
        return submit_selfie_search(
            event=self.event, selfie=fixtures.valid_selfie(), storage=storage, user=self.user
        ).search

    def add_selfie(self, search, storage, expected_upload_count):
        from selfie_search.services.submission import submit_additional_selfie

        return submit_additional_selfie(
            search=search,
            selfie=fixtures.valid_selfie(),
            storage=storage,
            user=self.user,
            expected_upload_count=expected_upload_count,
        )

    def test_failed_initial_selfie_can_recover_and_all_three_attempts_share_one_link(self):
        from selfie_search.services.continuation import submit_continuation
        from selfie_search.services.results import has_ready_search_pass
        from selfie_search.services.submission import (
            AdditionalSelfieUnavailable,
            selfie_upload_count,
        )

        storage = fixtures.RecordingStorage()
        root = self.submit_root(storage)
        self.fail_recognition(root)
        snapshot = SelfieSearch.objects.filter(pk=root.pk).values().get()
        self.make_eligible_embedding(event=self.event, photo_id="first-match")
        FeatureFlag.objects.create(key=APPAREL_SEARCH_EXPANSION.key, state=FEATURE_FLAG_ON)
        second = self.add_selfie(root, storage, expected_upload_count=1)
        self.assertEqual(second.parent_search_id, root.pk)
        self.assertEqual(second.job.search_id, second.pk)
        self.finish(second)
        self.assertEqual(
            second.apparel_evidence.decisions["anchor_outcome"], "no_compatible_appearance"
        )
        self.assertTrue(has_ready_search_pass(root))
        self.assertEqual([photo.pk for photo in saved_ready_result_photos(root)], ["first-match"])
        with self.assertRaises(AdditionalSelfieUnavailable) as replayed:
            self.add_selfie(root, storage, expected_upload_count=1)
        self.assertEqual(replayed.exception.reason, "stale")
        with self.assertRaises(AdditionalSelfieUnavailable) as invalid:
            self.add_selfie(root, storage, expected_upload_count=3)
        self.assertEqual(invalid.exception.reason, "unavailable")
        self.assertEqual(len(storage.objects), 2)
        self.assertEqual(storage.deleted, [])

        self.make_eligible_embedding(event=self.event, photo_id="later-match")
        third = self.add_selfie(root, storage, expected_upload_count=2)
        self.finish(third)
        self.assertEqual(list(third.results.values_list("photo_id", flat=True)), ["later-match"])
        self.assertEqual(
            [photo.pk for photo in saved_ready_result_photos(root)], ["first-match", "later-match"]
        )
        self.assertEqual(SelfieSearch.objects.filter(pk=root.pk).values().get(), snapshot)
        self.assertEqual(selfie_upload_count(root), 3)
        with self.assertRaises(AdditionalSelfieUnavailable) as replayed:
            self.add_selfie(root, storage, expected_upload_count=2)
        self.assertEqual(replayed.exception.reason, "stale")
        with self.assertRaises(AdditionalSelfieUnavailable) as rejected:
            self.add_selfie(root, storage, expected_upload_count=3)
        self.assertEqual(rejected.exception.reason, "limit")
        self.assertEqual(len(storage.objects), 3)
        self.assertEqual(storage.deleted, [])

        feedback_storage = Mock()
        feedback_storage.put.return_value = Mock(
            key="0123456789abcdef0123456789abcdef", size=100, content_type="image/jpeg"
        )
        self.assertTrue(
            save_feedback_selfie(
                search_id=root.pk,
                upload=fixtures.valid_selfie(),
                contact="person@example.test",
                storage=feedback_storage,
                paid_watermarked_previews_enabled=False,
            )
        )
        feedback = SelfieSearchFeedback.objects.get(search=root)
        self.assertEqual(feedback.source_status, SelfieSearch.Status.NO_FACE)
        self.assertEqual(feedback.variant, SelfieSearchFeedback.Variant.PROBLEM)
        self.assertEqual(feedback.source_visible_result_count, 0)
        next_pass = submit_continuation(search=root, user=self.user, photo_id="later-match")
        self.assertEqual(next_pass.parent_search_id, root.pk)
        self.assertEqual(selfie_upload_count(root), 3)

    def test_pending_pass_prevents_storage_and_failed_recognition_still_counts(self):
        from selfie_search.services.submission import (
            AdditionalSelfieUnavailable,
            selfie_upload_count,
        )

        storage = fixtures.RecordingStorage()
        root = self.submit_root(storage)
        self.make_eligible_embedding(event=self.event, photo_id="prior-match")
        self.finish(root)
        pending = self.add_selfie(root, storage, expected_upload_count=1)
        with self.assertRaises(AdditionalSelfieUnavailable) as rejected:
            self.add_selfie(root, storage, expected_upload_count=2)
        self.assertEqual(rejected.exception.reason, "pending")
        self.assertEqual(len(storage.objects), 2)
        self.assertEqual(storage.deleted, [])
        self.fail_recognition(pending)
        self.assertEqual(selfie_upload_count(root), 2)
        self.assertEqual([photo.pk for photo in saved_ready_result_photos(root)], ["prior-match"])

    def test_recheck_after_storage_removes_the_upload_if_another_selfie_used_the_slot(self):
        from selfie_search.services.submission import (
            AdditionalSelfieUnavailable,
            selfie_upload_count,
        )

        storage = fixtures.RecordingStorage()
        root = self.submit_root(storage)
        self.fail_recognition(root)
        stored_keys = set(storage.objects)
        put = storage.put

        def put_while_another_pass_starts(**kwargs):
            SelfieSearch.objects.create(
                parent_search=root,
                event=self.event,
                public_token_digest=uuid4().hex,
                configuration={"processor": "selfie_query"},
                status=SelfieSearch.Status.NO_FACE,
            )
            return put(**kwargs)

        with (
            patch.object(storage, "put", side_effect=put_while_another_pass_starts),
            self.assertRaises(AdditionalSelfieUnavailable) as rejected,
        ):
            self.add_selfie(root, storage, expected_upload_count=1)
        self.assertEqual(rejected.exception.reason, "stale")
        self.assertEqual(set(storage.objects), stored_keys)
        self.assertEqual(len(storage.deleted), 1)
        self.assertEqual(selfie_upload_count(root), 2)

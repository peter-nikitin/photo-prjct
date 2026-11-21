from concurrent.futures import ThreadPoolExecutor
from math import sqrt
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from feature_flags.registry import SELFIE_SEARCH_PASSES
from feature_flags.states import FEATURE_FLAG_ON
from feature_flags.testing import override_feature_flags
from picflow.models import Photo
from processing.models import PhotoFaceDetection
from search_feedback.models import SearchFeedback
from selfie_search.models import SelfieSearch, SelfieSearchDirectEvidence, SelfieSearchResult
from selfie_search.services.face_search_queue import claim, recover_processing, run_once
from selfie_search.services.submission import _token_digest
from selfie_search.tests import test_submission as fixtures


def face_vector(axis: int, primary: float) -> list[float]:
    vector = [0.0] * 512
    vector[0] = primary
    vector[axis] = sqrt(1 - primary**2)
    return vector


class SearchContinuationTests(TestCase):
    setUp = fixtures.GalleryPhotoSubmissionTests.setUp
    make_event = fixtures.GalleryPhotoSubmissionTests.make_event
    make_eligible_embedding = fixtures.GalleryPhotoSubmissionTests.make_eligible_embedding

    def root_search(self, *, token: str | None = None) -> SelfieSearch:
        return SelfieSearch.objects.create(
            event=self.event,
            status=SelfieSearch.Status.READY,
            public_token_digest=_token_digest(token or uuid4().hex),
        )

    def face(self, photo_id, *, axis=1, primary=0.7):
        return self.make_eligible_embedding(
            event=self.event, photo_id=photo_id, vector=face_vector(axis, primary)
        )

    def save_match(self, search, face, rank, *, distance=0.30):
        row = SelfieSearchResult.objects.create(
            search=search, photo_id=face.detection.attempt.photo_id, rank=rank
        )
        SelfieSearchDirectEvidence.objects.create(
            result=row, detection=face.detection, cosine_distance=distance
        )
        return row

    def test_manual_uses_visible_saved_single_face_and_is_idempotent(self):
        from selfie_search.services.continuation import ContinuationUnavailable, submit_continuation

        root = self.root_search()
        source = self.save_match(root, self.face("manual-source"), 1)
        other = self.save_match(root, self.face("other", axis=2), 2)
        self.face("manual-addition", primary=0.3)
        snapshot = list(root.results.values())
        outsider = self.face("unrelated", axis=3)
        with self.assertRaises(ContinuationUnavailable):
            submit_continuation(
                search=root, user=self.user, photo_id=outsider.detection.attempt.photo_id
            )

        child = submit_continuation(search=root, user=self.user, photo_id=source.photo_id)
        self.assertEqual(child.parent_search_id, root.pk)
        self.assertEqual(
            child.pk, submit_continuation(search=root, user=self.user, photo_id=source.photo_id).pk
        )
        self.assertIsNotNone(claim())
        self.assertEqual(
            child.pk, submit_continuation(search=root, user=self.user, photo_id=other.photo_id).pk
        )

        recover_processing()
        self.assertTrue(run_once())
        child.refresh_from_db()
        self.assertEqual(child.status, SelfieSearch.Status.READY)
        self.assertEqual(
            list(child.results.values_list("photo_id", flat=True)), ["manual-addition"]
        )
        self.assertEqual(list(root.results.values()), snapshot)
        repeat = submit_continuation(search=root, user=self.user, photo_id=source.photo_id)
        self.assertNotEqual(child.pk, repeat.pk)
        self.assertTrue(run_once())
        repeat.refresh_from_db()
        self.assertEqual(repeat.status, SelfieSearch.Status.READY)
        self.assertFalse(repeat.results.exists())

    @override_feature_flags({SELFIE_SEARCH_PASSES: FEATURE_FLAG_ON})
    def test_http_continuation_requires_a_photo_and_returns_its_status_link(self):
        token = uuid4().hex
        root = self.root_search(token=token)
        self.save_match(root, self.face("manual-source"), 1)
        self.face("manual-addition", primary=0.3)
        url_kwargs = {"event_slug": self.event.slug, "public_token": token}
        uri = reverse("selfie_search:continue", kwargs=url_kwargs)
        result = reverse("selfie_search:result", kwargs=url_kwargs)

        self.assertEqual(self.client.post(uri).status_code, 404)
        self.assertFalse(root.continuation_passes.exists())
        response = self.client.post(
            uri, {"photo_id": "manual-source"}, HTTP_ACCEPT="application/json"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result_url"], result)
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        child = root.continuation_passes.get()
        self.assertEqual(
            response.json()["status_url"],
            f"{reverse('selfie_search:status', kwargs=url_kwargs)}?pass={child.pk}",
        )
        self.assertEqual(self.client.get(response.json()["status_url"]).json()["status"], "queued")
        other = self.root_search()
        url = reverse("selfie_search:status", kwargs=url_kwargs)
        self.assertEqual(self.client.get(url, {"pass": str(other.pk)}).status_code, 404)
        self.assertTrue(run_once())
        self.assertEqual(self.client.get(response.json()["status_url"]).json()["status"], "ready")
        self.assertEqual(child.results.get().photo_id, "manual-addition")

    def test_automatic_anchor_query_is_scoped_to_saved_visible_results(self):
        from selfie_search.services import continuation

        root = self.root_search()
        saved = self.save_match(root, self.face("visible-anchor"), 1)
        hidden = self.save_match(root, self.face("hidden-anchor"), 2)
        Photo.objects.filter(pk=hidden.photo_id).update(is_hidden=True)
        self.face("not-in-results")
        child = continuation.submit_continuation(search=root, user=self.user, photo_id=None)
        with patch.object(
            continuation, "_select_centroids", wraps=continuation._select_centroids
        ) as select:
            groups = continuation.select_continuation_centroids(
                child, model=child.configuration["embedding_model"], check_apparel=None
            )
        self.assertEqual(
            [anchor.photo_id for group in groups for anchor in group], [saved.photo_id]
        )
        self.assertEqual(select.call_args.kwargs["eligible_result_ids"], (saved.pk,))
        faces = select.call_args.kwargs["faces"]
        self.assertEqual(
            set(faces.values_list("detection__attempt__photo_id", flat=True)), {saved.photo_id}
        )

    def test_apparel_exclusion_applies_to_all_passes_of_the_same_search(self):
        from selfie_search.services.continuation import ContinuationUnavailable, submit_continuation
        from selfie_search.services.results import saved_ready_result_rows

        root = self.root_search()
        face = self.face("previously-shown")
        shown = self.save_match(root, face, 1)
        child = self.root_search()
        child.parent_search = root
        child.save(update_fields=["parent_search"])
        veto = self.save_match(child, face, 1)
        SelfieSearchResult.objects.filter(pk=veto.pk).update(excluded_by_apparel=True)
        other = self.root_search()
        self.save_match(other, face, 1)

        self.assertFalse(saved_ready_result_rows(root).filter(photo_id=shown.photo_id).exists())
        self.assertTrue(saved_ready_result_rows(other).filter(photo_id=shown.photo_id).exists())
        with self.assertRaises(ContinuationUnavailable):
            submit_continuation(search=root, user=self.user, photo_id=shown.photo_id)
        self.assertEqual(root.results.get().pk, shown.pk)

    def test_worker_checks_new_faces_against_the_whole_history_before_publication(self):
        from selfie_search.services.continuation import submit_continuation
        from selfie_search.services.direct_ranking import DirectRankingOutcome
        from selfie_search.services.ranking import RankedPhoto
        from selfie_search.services.results import saved_ready_result_rows

        root = self.root_search()
        source = self.save_match(root, self.face("history-source", primary=0), 1)
        earlier = self.root_search()
        earlier.parent_search = root
        earlier.save(update_fields=["parent_search"])
        self.save_match(earlier, self.face("history-other-angle", axis=2, primary=0), 1)
        addition = self.face("supported-by-other-angle", axis=2, primary=0.1)
        outsider = self.face("unsupported-face", axis=3, primary=0)

        child = submit_continuation(search=root, user=self.user, photo_id=source.photo_id)
        with patch(
            "selfie_search.services.face_search_queue.rank_selected_direct",
            return_value=DirectRankingOutcome(
                photos=tuple(
                    RankedPhoto(
                        photo_id=face.detection.attempt.photo_id,
                        detection_id=face.detection_id,
                        cosine_distance=0.31,
                    )
                    for face in (addition, outsider)
                ),
                eligible_photo_count=4,
                eligible_face_count=4,
                has_vectors=True,
                identity_ms=0,
                build_ms=0,
                ranking_ms=0,
            ),
        ):
            self.assertTrue(run_once())

        child.refresh_from_db()
        self.assertEqual(child.published_matched_photo_count, 1)
        self.assertTrue(child.results.get(photo_id="unsupported-face").excluded_by_face_history)
        self.assertTrue(
            saved_ready_result_rows(root).filter(photo_id="supported-by-other-angle").exists()
        )
        self.assertFalse(saved_ready_result_rows(root).filter(photo_id="unsupported-face").exists())

    def test_consumed_anchors_use_one_query_for_multiple_passes(self):
        from selfie_search.services.continuation import consumed_anchor_photo_ids

        root = self.root_search()
        for index in range(5):
            SelfieSearch.objects.create(
                parent_search=root,
                event=self.event,
                status=SelfieSearch.Status.READY,
                configuration={"query_source": {"kind": "gallery_photo", "photo_id": str(index)}},
                public_token_digest=uuid4().hex,
            )
        with self.assertNumQueries(1):
            self.assertEqual(consumed_anchor_photo_ids(root), set(map(str, range(5))))

    def test_automatic_batch_ranks_distinct_centroids_and_consumes_only_used_anchors(self):
        from selfie_search.services.continuation import (
            consumed_anchor_photo_ids,
            submit_continuation,
        )

        root = self.root_search()
        for axis in range(1, 5):
            self.save_match(root, self.face(f"anchor-{axis}", axis=axis), axis)
            self.face(f"addition-{axis}", axis=axis, primary=0.3)
        hidden = self.save_match(root, self.face("hidden", axis=5), 5)
        Photo.objects.filter(pk=hidden.photo_id).update(is_hidden=True)
        absent = self.save_match(root, self.face("marked-absent", axis=6), 6)
        SearchFeedback.objects.create(
            submission_id=uuid4(),
            event_id=root.event_id,
            photo_id=absent.photo_id,
            search_type=SearchFeedback.SearchType.SELFIE,
            value=SearchFeedback.Value.ABSENT,
            search_context={"search_id": str(root.pk)},
        )

        first = submit_continuation(search=root, user=self.user, photo_id=None)
        self.assertTrue(run_once())
        first.refresh_from_db()
        self.assertEqual(first.status, SelfieSearch.Status.READY)
        self.assertEqual(consumed_anchor_photo_ids(root), {"anchor-1", "anchor-2", "anchor-3"})
        self.assertEqual(
            dict(first.results.values_list("photo_id", "direct_evidence__centroid_index")),
            {"addition-1": 1, "addition-2": 2, "addition-3": 3},
        )
        self.assertAlmostEqual(
            first.results.get(photo_id="addition-1").direct_evidence.cosine_distance,
            0.108751,
            places=5,
        )

        second = submit_continuation(search=root, user=self.user, photo_id=None)
        self.assertTrue(run_once())
        self.assertEqual(list(second.results.values_list("photo_id", flat=True)), ["addition-4"])
        exhausted = submit_continuation(search=root, user=self.user, photo_id=None)
        self.assertTrue(run_once())
        exhausted.refresh_from_db()
        self.assertEqual(exhausted.failure_code, "continuation_unavailable")
        self.assertFalse(exhausted.results.exists())

    def test_automatic_and_manual_refuse_photo_with_another_detected_face(self):
        from selfie_search.services.continuation import ContinuationUnavailable, submit_continuation

        root = self.root_search()
        embedding = self.face("group-photo")
        row = self.save_match(root, embedding, 1)
        PhotoFaceDetection.objects.create(
            artifact=embedding.detection.artifact,
            attempt=embedding.detection.attempt,
            face_index=1,
            status=PhotoFaceDetection.Status.QUALITY_REJECTED,
        )
        with self.assertRaises(ContinuationUnavailable):
            submit_continuation(search=root, user=self.user, photo_id=row.photo_id)
        automatic = submit_continuation(search=root, user=self.user, photo_id=None)
        self.assertTrue(run_once())
        automatic.refresh_from_db()
        self.assertEqual(automatic.failure_code, "continuation_unavailable")
        self.assertFalse(automatic.results.exists())

    def test_centroid_anchor_remains_available_for_exact_manual_search(self):
        from selfie_search.services.continuation import (
            available_manual_photo_ids,
            submit_continuation,
        )

        root = self.root_search()
        self.save_match(root, self.face("first-source"), 1)
        self.save_match(root, self.face("other-source", primary=0.85), 2)
        auto = submit_continuation(search=root, user=self.user, photo_id=None)
        self.assertTrue(run_once())
        self.assertEqual(
            available_manual_photo_ids(root, photo_ids=("first-source", "other-source")),
            {"first-source", "other-source"},
        )
        manual = submit_continuation(search=root, user=self.user, photo_id="first-source")
        self.assertNotEqual(auto.pk, manual.pk)
        self.assertTrue(run_once())
        self.assertEqual(
            available_manual_photo_ids(root, photo_ids=("first-source", "other-source")),
            {"first-source", "other-source"},
        )

    def test_automatic_can_use_new_result_supported_by_two_trusted_faces(self):
        from selfie_search.services.continuation import (
            consumed_anchor_photo_ids,
            submit_continuation,
        )

        root = self.root_search()
        self.save_match(root, self.face("trusted-left", primary=0.7), 1)
        self.save_match(root, self.face("trusted-right", primary=0.9), 2)
        self.face("supported", primary=0.3)
        self.face("later-addition", primary=-0.1)

        first = submit_continuation(search=root, user=self.user, photo_id=None)
        self.assertTrue(run_once())
        first_match = first.results.get()
        self.assertEqual(first_match.photo_id, "supported")
        # The equal-weight normalized mean of the two trusted faces lies at this distance.
        self.assertAlmostEqual(first_match.direct_evidence.cosine_distance, 0.199634, places=4)
        self.assertEqual(consumed_anchor_photo_ids(root), {"trusted-left", "trusted-right"})

        second = submit_continuation(search=root, user=self.user, photo_id=None)
        self.assertTrue(run_once())
        self.assertEqual(second.results.get().photo_id, "later-addition")
        self.assertIn("supported", consumed_anchor_photo_ids(root))
        exhausted = submit_continuation(search=root, user=self.user, photo_id=None)
        self.assertTrue(run_once())
        exhausted.refresh_from_db()
        self.assertEqual(exhausted.failure_code, "continuation_unavailable")
        self.assertFalse(exhausted.results.exists())

    def test_failed_pass_does_not_consume_manual_source(self):
        from selfie_search.services.continuation import submit_continuation

        root = self.root_search()
        row = self.save_match(root, self.face("retry-source"), 1)
        child = submit_continuation(search=root, user=self.user, photo_id=row.photo_id)
        Photo.objects.filter(pk=row.photo_id).update(is_hidden=True)
        self.assertTrue(run_once())
        child.refresh_from_db()
        self.assertEqual(child.status, SelfieSearch.Status.FAILED)
        Photo.objects.filter(pk=row.photo_id).update(is_hidden=False)
        retry = submit_continuation(search=root, user=self.user, photo_id=row.photo_id)
        self.assertNotEqual(retry.pk, child.pk)
        self.assertTrue(run_once())
        retry.refresh_from_db()
        self.assertEqual(retry.status, SelfieSearch.Status.READY)


class ContinuationConcurrencyTests(TransactionTestCase):
    setUp = fixtures.GalleryPhotoSubmissionTests.setUp
    make_event = fixtures.GalleryPhotoSubmissionTests.make_event
    make_eligible_embedding = fixtures.GalleryPhotoSubmissionTests.make_eligible_embedding

    def test_two_submissions_race_and_queue_one_pass(self):
        from selfie_search.services.continuation import submit_continuation

        root = SelfieSearch.objects.create(
            event=self.event, status=SelfieSearch.Status.READY, public_token_digest=uuid4().hex
        )
        photo_id = "concurrent"
        self.make_eligible_embedding(event=self.event, photo_id=photo_id)
        SelfieSearchResult.objects.create(search=root, photo_id=photo_id, rank=1)
        starting = Barrier(2)

        def start():
            close_old_connections()
            try:
                starting.wait(timeout=5)
                return submit_continuation(search=root, user=self.user, photo_id=photo_id).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(start)
            second = pool.submit(start)
            self.assertEqual(first.result(timeout=10), second.result(timeout=10))
        self.assertEqual(root.continuation_passes.count(), 1)

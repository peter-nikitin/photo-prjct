from dataclasses import replace
from uuid import UUID

import pytest
from selfie_search.services.direct_ranking import DirectRankingOutcome
from selfie_search.services.ranking import RankedPhoto
from selfie_search.services.reader_comparison import compare_outcomes


def outcome(distance=0.3, detection=1, included=True):
    row = RankedPhoto("photo-secret", UUID(int=detection), distance)
    return DirectRankingOutcome(
        (row,) if included else (), 1, 1, (), 0, 0, 0, best_candidates=(row,)
    )


def test_boundary_membership_is_classified_without_identity_diagnostics():
    result = compare_outcomes(
        outcome(0.363 - 1e-8), outcome(0.363 + 1e-8, included=False), threshold=0.363
    )
    assert result["boundary_membership_count"] == 1
    assert result["unexplained_count"] == 0
    assert "photo-secret" not in str(result)


def test_nonboundary_membership_and_cohort_are_rejected():
    result = compare_outcomes(
        outcome(), replace(outcome(included=False), eligible_face_count=2), threshold=0.363
    )
    assert result["unexplained_count"] >= 2


def test_detection_difference_without_crossface_evidence_is_unexplained():
    result = compare_outcomes(outcome(), outcome(0.3 + 1e-8, detection=2), threshold=0.363)
    assert result["boundary_detection_count"] == 0
    assert result["unexplained_count"] == 1


@pytest.mark.django_db(transaction=True)
def test_comparison_uses_repeatable_read_and_rolls_back_review_database_errors(caplog):
    from unittest.mock import patch

    from django.db import connection
    from selfie_search.models import SelfieSearch
    from selfie_search.services.cluster_expansion import direct_only_ranked_photos
    from selfie_search.services.reader_comparison import comparison_snapshot, review_other_reader

    search = SelfieSearch(reader_staff_eligible=True, reader_comparison_requested=True)
    search.configuration = {"cosine_distance_threshold": 0.363}

    def database_failure(*args, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 / 0")

    with comparison_snapshot(search) as comparing:
        assert comparing
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_isolation")
            assert cursor.fetchone()[0] == "repeatable read"
        with patch(
            "selfie_search.services.reader_comparison.rank_vector_direct",
            side_effect=database_failure,
        ):
            report = review_other_reader(
                search=search,
                query=(1.0,),
                reader="legacy",
                selected=outcome(),
                expansion=direct_only_ranked_photos(outcome().photos, outcome="disabled"),
                expand=lambda rows: direct_only_ranked_photos(rows, outcome="disabled"),
            )
        assert report == {"outcome": "comparison_failed"}
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1


@pytest.mark.django_db
def test_ambient_transaction_disables_review_without_aborting_selected_path(caplog):
    from selfie_search.models import SelfieSearch
    from selfie_search.services.reader_comparison import comparison_snapshot

    with comparison_snapshot(
        SelfieSearch(reader_staff_eligible=True, reader_comparison_requested=True)
    ) as comparing:
        assert not comparing


def test_gallery_source_identity_and_float32_values_are_strict():
    from selfie_search.services.ranking import CandidateEmbedding
    from selfie_search.services.reader_comparison import verify_source_representations

    candidate = CandidateEmbedding(
        vector=[1.0, 0.0],
        model_version="sface",
        detection_id=UUID(int=1),
        photo_id="source",
        photo_event_id=1,
        attempt_event_id=1,
        attempt_photo_id="source",
    )
    verify_source_representations(candidate, replace(candidate, vector=[1.0 + 1e-9, 0.0]))
    with pytest.raises(ValueError):
        verify_source_representations(candidate, replace(candidate, vector=[0.9, 0.1]))
    with pytest.raises(ValueError):
        verify_source_representations(candidate, replace(candidate, model_version="foreign"))


@pytest.mark.parametrize("maximum,repeats", [(101, 1), (0, 1), (1, 31), (1, 0)])
def test_private_management_command_has_hard_bounds(maximum, repeats):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command(
            "review_pgvector_face_search", event_slug="secret", max_sources=maximum, repeats=repeats
        )


@pytest.fixture
def gallery_review_source():
    from django.test import override_settings
    from processing.models import FaceEmbeddingVector
    from selfie_search.tests import test_submission as fixtures

    helper = fixtures.GalleryPhotoSubmissionTests(
        methodName="test_gallery_presentation_returns_all_current_compatible_faces_for_page_photos"
    )
    with override_settings(
        SELFIE_SEARCH_EMBEDDING_MODEL="sface", SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED=False
    ):
        helper.setUp()
        helper.user.is_staff = True
        embedding = helper.make_eligible_embedding(
            event=helper.event, photo_id="private-source", vector=[1.0] + [0.0] * 127
        )
        FaceEmbeddingVector.objects.create(
            detection=embedding.detection, model_version="sface", vector=embedding.vector
        )
        yield helper, embedding


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("comparison_failure", [False, True])
def test_optin_gallery_publishes_one_selected_result_and_isolates_comparison_failure(
    gallery_review_source, comparison_failure, caplog
):
    import logging
    from contextlib import nullcontext
    from unittest.mock import patch

    from django.db import connection
    from selfie_search.models import SelfieSearch, SelfieSearchResult
    from selfie_search.services.submission import (
        process_gallery_photo_search,
        submit_gallery_photo_search,
    )

    helper, embedding = gallery_review_source
    created = submit_gallery_photo_search(
        event=helper.event,
        photo=embedding.detection.attempt.photo,
        detection_id=embedding.detection_id,
        user=helper.user,
        compare_readers=True,
    )

    def failure(*args, **kwargs):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1/0")

    comparison_patch = (
        patch("selfie_search.services.reader_comparison.rank_vector_direct", side_effect=failure)
        if comparison_failure
        else nullcontext()
    )
    with (
        caplog.at_level(logging.INFO, logger="selfie_search.services.reader_comparison"),
        comparison_patch,
    ):
        result = process_gallery_photo_search(search=created.search)
    assert result.status == SelfieSearch.Status.READY
    assert SelfieSearch.objects.count() == 1
    assert SelfieSearchResult.objects.filter(search=result).count() == 1
    assert ("comparison_failed" if comparison_failure else "compared") in caplog.text
    assert "private-source" not in caplog.text
    assert str(embedding.detection_id) not in caplog.text
    assert created.public_token not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_private_command_compares_existing_gallery_without_creating_results(gallery_review_source):
    import json
    from io import StringIO

    from django.core.management import call_command
    from selfie_search.models import SelfieSearch

    helper, _ = gallery_review_source
    output = StringIO()
    call_command(
        "review_pgvector_face_search",
        event_slug=helper.event.slug,
        max_sources=1,
        repeats=2,
        stdout=output,
    )
    report = json.loads(output.getvalue())
    assert report["unexplained_count"] == report["failed_count"] == 0
    assert len(report["native_total_ms"]) == 2
    assert report["legacy_cache_hits"] >= 1
    assert len(report["legacy_cold_total_ms"]) == 1
    assert len(report["legacy_warm_total_ms"]) == 1
    assert SelfieSearch.objects.count() == 0
    assert "private-source" not in output.getvalue()


def test_near_tie_order_and_anchor_boundaries_are_classified():
    a = RankedPhoto("a", UUID(int=1), 0.3)
    b = RankedPhoto("b", UUID(int=2), 0.3 + 1e-8)
    left = replace(outcome(), photos=(a, b), best_candidates=(a, b), eligible_photo_count=2)
    ar, br = replace(a, cosine_distance=0.3 + 1e-8), replace(b, cosine_distance=0.3)
    right = replace(left, photos=(br, ar), best_candidates=(br, ar))
    report = compare_outcomes(left, right, threshold=0.363, anchor_threshold=0.3)
    assert report["unexplained_count"] == 0
    assert report["boundary_order_count"] == 1
    assert report["boundary_anchor_count"] == 2


@pytest.mark.django_db(transaction=True)
def test_comparison_selected_native_failure_never_falls_back(gallery_review_source):
    from unittest.mock import patch

    from feature_flags.registry import PGVECTOR_FACE_SEARCH_READ
    from feature_flags.testing import override_feature_flags
    from selfie_search.models import SelfieSearch
    from selfie_search.services.ranking import RankingError
    from selfie_search.services.submission import (
        process_gallery_photo_search,
        submit_gallery_photo_search,
    )

    helper, embedding = gallery_review_source
    with override_feature_flags({PGVECTOR_FACE_SEARCH_READ: "on"}):
        created = submit_gallery_photo_search(
            event=helper.event,
            photo=embedding.detection.attempt.photo,
            detection_id=embedding.detection_id,
            user=helper.user,
            compare_readers=True,
        )
        with (
            patch(
                "selfie_search.services.read_selection.rank_vector_direct", side_effect=RankingError
            ),
            patch("selfie_search.services.reader_comparison.rank_legacy_direct") as legacy,
        ):
            result = process_gallery_photo_search(search=created.search)
        legacy.assert_not_called()
    assert result.status != SelfieSearch.Status.READY
    assert result.results.count() == 0


@pytest.mark.django_db(transaction=True)
def test_both_readers_receive_same_transient_query_object(gallery_review_source):
    from unittest.mock import patch

    from selfie_search.services.direct_ranking import rank_legacy_direct
    from selfie_search.services.submission import (
        process_gallery_photo_search,
        submit_gallery_photo_search,
    )
    from selfie_search.services.vector_ranking import rank_vector_direct

    helper, embedding = gallery_review_source
    created = submit_gallery_photo_search(
        event=helper.event,
        photo=embedding.detection.attempt.photo,
        detection_id=embedding.detection_id,
        user=helper.user,
        compare_readers=True,
    )
    with (
        patch(
            "selfie_search.services.read_selection.rank_legacy_direct", wraps=rank_legacy_direct
        ) as legacy,
        patch(
            "selfie_search.services.reader_comparison.rank_vector_direct", wraps=rank_vector_direct
        ) as native,
    ):
        process_gallery_photo_search(search=created.search)
    assert legacy.call_args.args[0] is native.call_args.args[0]
    assert legacy.call_args.args[1] is native.call_args.args[1]


def test_expansion_drift_without_numeric_anchor_evidence_is_unexplained():
    from selfie_search.services.cluster_expansion import (
        ExpandedRankedPhoto,
        direct_only_ranked_photos,
    )
    from selfie_search.services.reader_comparison import classify_expansions

    left = direct_only_ranked_photos(outcome().photos, outcome="no_new_photos")
    right = replace(
        left,
        results=left.results
        + (ExpandedRankedPhoto("extra-private", "face_cluster_expansion", None, ()),),
        final_matched_photo_count=2,
        cluster_expanded_photo_count=1,
        strong_anchor_count=1,
        outcome="expanded",
    )
    report = compare_outcomes(outcome(), outcome(), threshold=0.363)
    classify_expansions(report, left, right)
    assert report["unexplained_count"] > 0
    assert "extra-private" not in str(report)


def test_optional_expansion_failure_is_incomplete_even_if_direct_matches():
    from selfie_search.services.cluster_expansion import direct_only_ranked_photos
    from selfie_search.services.reader_comparison import classify_expansions

    left = direct_only_ranked_photos(outcome().photos, outcome="corpus_unavailable")
    right = replace(left, outcome="no_new_photos", strong_anchor_count=1)
    report = compare_outcomes(outcome(), outcome(), threshold=0.363)
    classify_expansions(report, left, right)
    assert report["incomplete_expansion_count"] > 0
    assert report["unexplained_count"] > 0


def test_numeric_anchor_boundary_expansion_effect_is_classified():
    from selfie_search.services.cluster_expansion import (
        ExpandedRankedPhoto,
        direct_only_ranked_photos,
    )
    from selfie_search.services.reader_comparison import classify_expansions

    left = direct_only_ranked_photos(outcome(0.3 - 1e-8).photos, outcome="expanded")
    left = replace(
        left,
        results=left.results
        + (ExpandedRankedPhoto("extra-private", "face_cluster_expansion", None, ()),),
        final_matched_photo_count=2,
        cluster_expanded_photo_count=1,
        strong_anchor_count=1,
    )
    right = direct_only_ranked_photos(outcome(0.3 + 1e-8).photos, outcome="no_strong_anchor")
    report = compare_outcomes(
        outcome(0.3 - 1e-8), outcome(0.3 + 1e-8), threshold=0.363, anchor_threshold=0.3
    )
    classify_expansions(report, left, right)
    assert report["boundary_expansion_effect_count"] == 1
    assert report["unexplained_count"] == 0


@pytest.mark.django_db(transaction=True)
def test_command_times_regular_readers_before_separate_diagnostic_work(gallery_review_source):
    from io import StringIO
    from unittest.mock import patch

    from django.core.management import call_command
    from selfie_search.services.direct_ranking import rank_legacy_direct
    from selfie_search.services.vector_ranking import rank_vector_direct

    prefix = "selfie_search.management.commands.review_pgvector_face_search"
    helper, _ = gallery_review_source
    with (
        patch(f"{prefix}.rank_legacy_direct", wraps=rank_legacy_direct) as legacy,
        patch(f"{prefix}.rank_vector_direct", wraps=rank_vector_direct) as native,
    ):
        call_command(
            "review_pgvector_face_search",
            event_slug=helper.event.slug,
            max_sources=1,
            repeats=1,
            stdout=StringIO(),
        )
    assert not legacy.call_args_list[0].kwargs.get("comparison_evidence", False)
    assert not native.call_args_list[0].kwargs.get("comparison_evidence", False)
    assert legacy.call_args_list[1].kwargs["comparison_evidence"] is True
    assert native.call_args_list[1].kwargs["comparison_evidence"] is True


@pytest.mark.django_db(transaction=True)
def test_online_incomplete_expansion_review_preserves_selected_publication(
    gallery_review_source, caplog
):
    import logging
    from unittest.mock import patch

    from selfie_search.models import SelfieSearch
    from selfie_search.services.cluster_expansion import (
        ExpandedRankedPhoto,
        direct_only_ranked_photos,
    )
    from selfie_search.services.submission import (
        process_gallery_photo_search,
        submit_gallery_photo_search,
    )

    helper, embedding = gallery_review_source
    created = submit_gallery_photo_search(
        event=helper.event,
        photo=embedding.detection.attempt.photo,
        detection_id=embedding.detection_id,
        user=helper.user,
        compare_readers=True,
    )

    def expand_side_effect(*, search, ranked, query):
        expansion = direct_only_ranked_photos(
            ranked, outcome="corpus_unavailable" if not expanded else "expanded"
        )
        if expanded:
            expansion = replace(
                expansion,
                results=expansion.results
                + (ExpandedRankedPhoto("extra-private", "face_cluster_expansion", None, ()),),
                strong_anchor_count=1,
                cluster_expanded_photo_count=1,
                final_matched_photo_count=2,
            )
        expanded.append(True)
        return expansion

    expanded = []
    with (
        caplog.at_level(logging.INFO, logger="selfie_search.services.reader_comparison"),
        patch(
            "selfie_search.services.submission._expand_gallery_ranking",
            side_effect=expand_side_effect,
        ),
    ):
        result = process_gallery_photo_search(search=created.search)
    assert result.status == SelfieSearch.Status.READY
    assert result.results.count() == 1
    assert '"incomplete_expansion_count": 1' in caplog.text
    assert '"outcome": "unexplained"' in caplog.text


@pytest.mark.django_db(transaction=True)
def test_command_incomplete_expansion_is_nonzero_review(gallery_review_source):
    import json
    from io import StringIO
    from unittest.mock import patch

    from django.core.management import call_command
    from django.core.management.base import CommandError
    from selfie_search.services.cluster_expansion import direct_only_ranked_photos

    helper, _ = gallery_review_source
    states = iter(("corpus_unavailable", "no_new_photos"))

    def expand(*, search, ranked, query):
        return direct_only_ranked_photos(ranked, outcome=next(states))

    output = StringIO()
    with (
        patch(
            "selfie_search.management.commands.review_pgvector_face_search._expand_gallery_ranking",
            side_effect=expand,
        ),
        pytest.raises(CommandError),
    ):
        call_command(
            "review_pgvector_face_search",
            event_slug=helper.event.slug,
            max_sources=1,
            repeats=1,
            stdout=output,
        )
    report = json.loads(output.getvalue())
    assert report["incomplete_expansion_count"] == 1
    assert report["unexplained_count"] > 0

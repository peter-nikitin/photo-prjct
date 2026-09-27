from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser, User
from feature_flags.models import FeatureFlag
from feature_flags.registry import PGVECTOR_FACE_SEARCH_READ
from selfie_search.models import SelfieSearch
from selfie_search.services.read_selection import (
    rank_selected_direct,
    select_reader,
    staff_eligible,
)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "state,eligible,expected",
    [
        (None, False, "legacy"),
        ("off", True, "legacy"),
        ("staff", False, "legacy"),
        ("staff", True, "pgvector"),
        ("on", False, "pgvector"),
    ],
)
def test_current_state_routes_saved_eligibility(state, eligible, expected):
    if state is not None:
        FeatureFlag.objects.create(key=PGVECTOR_FACE_SEARCH_READ.key, state=state)
    assert select_reader(SelfieSearch(reader_staff_eligible=eligible)) == expected


@pytest.mark.parametrize(
    "user,expected",
    [
        (AnonymousUser(), False),
        (User(is_staff=False), False),
        (User(is_staff=True, is_active=False), False),
        (User(is_staff=True), True),
    ],
)
def test_only_active_authenticated_staff_are_eligible(user, expected):
    assert staff_eligible(user) is expected


def test_old_rows_default_to_ordinary_context():
    search = SelfieSearch()
    assert search.reader_staff_eligible is False
    assert search.reader_comparison_requested is False


def test_selected_failure_does_not_fallback():
    with (
        patch("selfie_search.services.read_selection.rank_vector_direct", side_effect=RuntimeError),
        patch("selfie_search.services.read_selection.rank_legacy_direct") as legacy,
        pytest.raises(RuntimeError),
    ):
        rank_selected_direct(SelfieSearch(), [1.0], reader="pgvector")
    legacy.assert_not_called()

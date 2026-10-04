from unittest.mock import patch

import pytest
from selfie_search.models import SelfieSearch
from selfie_search.services.read_selection import rank_selected_direct


def test_selected_native_failure_does_not_fallback():
    with (
        patch("selfie_search.services.read_selection.rank_vector_direct", side_effect=RuntimeError),
        pytest.raises(RuntimeError),
    ):
        rank_selected_direct(SelfieSearch(), [1.0])

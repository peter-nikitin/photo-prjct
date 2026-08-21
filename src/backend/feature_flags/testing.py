from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from unittest.mock import patch

from feature_flags import services
from feature_flags.states import FEATURE_FLAG_OFF, FeatureFlagState


@contextmanager
def override_feature_flags(states: Mapping[str, FeatureFlagState]) -> Iterator[None]:
    """Temporarily serve feature-flag states from a supplied mutable mapping."""

    def overridden_state_for(key: str) -> FeatureFlagState:
        return states.get(key, FEATURE_FLAG_OFF)

    with patch.object(services, "_state_for", side_effect=overridden_state_for):
        yield

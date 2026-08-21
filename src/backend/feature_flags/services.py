from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser

from feature_flags.models import FeatureFlag
from feature_flags.states import (
    FEATURE_FLAG_OFF,
    FEATURE_FLAG_ON,
    FEATURE_FLAG_STAFF,
    FeatureFlagState,
)


def _database_state_for(key: str) -> FeatureFlagState:
    try:
        state = FeatureFlag.objects.only("state").get(key=key).state
    except FeatureFlag.DoesNotExist:
        return FEATURE_FLAG_OFF
    if state == FEATURE_FLAG_ON:
        return FEATURE_FLAG_ON
    if state == FEATURE_FLAG_STAFF:
        return FEATURE_FLAG_STAFF
    return FEATURE_FLAG_OFF


def _state_for(key: str) -> FeatureFlagState:
    return _database_state_for(key)


def is_enabled(key: str, user: AbstractBaseUser | AnonymousUser) -> bool:
    """Return whether a runtime release gate permits the current user."""
    state = _state_for(key)

    if state == FEATURE_FLAG_ON:
        return True
    return (
        state == FEATURE_FLAG_STAFF and user.is_authenticated and user.is_active and user.is_staff
    )

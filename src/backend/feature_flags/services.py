from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser

from feature_flags.models import FeatureFlag
from feature_flags.registry import FeatureDefinition
from feature_flags.states import (
    FEATURE_FLAG_OFF,
    FEATURE_FLAG_ON,
    FEATURE_FLAG_STAFF,
    FeatureFlagState,
)


def _database_state_for(definition: FeatureDefinition) -> FeatureFlagState:
    try:
        state = FeatureFlag.objects.only("state").get(key=definition.key).state
    except FeatureFlag.DoesNotExist:
        return FEATURE_FLAG_OFF
    if state == FEATURE_FLAG_ON:
        return FEATURE_FLAG_ON
    if state == FEATURE_FLAG_STAFF:
        return FEATURE_FLAG_STAFF
    return FEATURE_FLAG_OFF


def _state_for(definition: FeatureDefinition) -> FeatureFlagState:
    return _database_state_for(definition)


def _require_definition(definition: FeatureDefinition) -> FeatureDefinition:
    if not isinstance(definition, FeatureDefinition):
        raise TypeError("Feature checks require a registered FeatureDefinition.")
    return definition


def is_enabled(definition: FeatureDefinition, user: AbstractBaseUser | AnonymousUser) -> bool:
    """Return whether a runtime release gate permits the current user."""
    state = _state_for(_require_definition(definition))

    if state == FEATURE_FLAG_ON:
        return True
    return (
        state == FEATURE_FLAG_STAFF and user.is_authenticated and user.is_active and user.is_staff
    )


def is_server_enabled(definition: FeatureDefinition) -> bool:
    """Permit trusted server callbacks during staff rehearsal and public release."""
    return _state_for(_require_definition(definition)) in {FEATURE_FLAG_STAFF, FEATURE_FLAG_ON}

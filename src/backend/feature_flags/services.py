from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser

from feature_flags.models import FeatureFlag


def is_enabled(key: str, user: AbstractBaseUser | AnonymousUser) -> bool:
    """Return whether a runtime release gate permits the current user."""
    try:
        state = FeatureFlag.objects.only("state").get(key=key).state
    except FeatureFlag.DoesNotExist:
        return False

    if state == FeatureFlag.State.ON:
        return True
    return (
        state == FeatureFlag.State.STAFF
        and user.is_authenticated
        and user.is_active
        and user.is_staff
    )

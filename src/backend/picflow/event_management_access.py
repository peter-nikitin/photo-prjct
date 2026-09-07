"""Independent inspection and upload permissions for the private event workspace."""

from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.models import AbstractUser, AnonymousUser
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.urls import reverse


def can_inspect_event_photos(user: AbstractUser | AnonymousUser) -> bool:
    return bool(
        user.is_authenticated
        and user.is_active
        and user.is_staff
        and user.has_perms(("picflow.view_event", "picflow.view_photo"))
    )


def can_upload_event_photos(user: AbstractUser | AnonymousUser) -> bool:
    return bool(
        settings.PHOTO_UPLOAD_ENABLED
        and user.is_authenticated
        and user.is_active
        and user.has_perm("ingestion.upload_photos")
    )


def event_management_denial(
    request: HttpRequest, *, admin_only: bool = False
) -> HttpResponse | None:
    """Check access before reading event, photo, or batch data."""
    if not request.user.is_authenticated:
        return redirect_to_login(
            request.get_full_path(), reverse("photographer_login"), REDIRECT_FIELD_NAME
        )
    if can_inspect_event_photos(request.user) or (
        not admin_only and can_upload_event_photos(request.user)
    ):
        return None
    return HttpResponseForbidden()

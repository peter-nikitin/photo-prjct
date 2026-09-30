from django.http import HttpRequest
from feature_flags import services
from feature_flags.registry import PAID_PHOTO_PURCHASE


def purchase_navigation(request: HttpRequest) -> dict[str, bool]:
    return {"purchase_navigation": services.is_enabled(PAID_PHOTO_PURCHASE, request.user)}

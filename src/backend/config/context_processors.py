from django.conf import settings
from django.http import HttpRequest
from feature_flags.registry import WEB_VITALS
from feature_flags.services import is_enabled
from picflow.access import is_event_staff_preview

from config.metrics import WEB_VITALS_PAGE_BY_ROUTE


def analytics(request: HttpRequest) -> dict[str, int | None]:
    counter_id = (
        None
        if (
            is_event_staff_preview(request)
            or getattr(request, "_is_public_selfie_bearer_request", False)
            or getattr(request, "_is_commerce_order_bearer_request", False)
            or request.resolver_match
            and request.resolver_match.url_name == "event_detail"
        )
        else settings.YANDEX_METRIKA_COUNTER_ID
    )
    return {"yandex_metrika_counter_id": counter_id}


def web_vitals(request: HttpRequest) -> dict[str, str]:
    if request.method != "GET" or not (request.is_secure() or settings.DEBUG):
        return {}
    match = request.resolver_match
    page = WEB_VITALS_PAGE_BY_ROUTE.get(match.view_name) if match is not None else None
    if page is None or is_event_staff_preview(request) or not is_enabled(WEB_VITALS, request.user):
        return {}
    return {"web_vitals_page": page}

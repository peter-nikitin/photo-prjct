from django.conf import settings
from django.http import HttpRequest
from picflow.access import is_event_staff_preview


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

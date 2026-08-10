from django.conf import settings
from django.http import HttpRequest


def analytics(request: HttpRequest) -> dict[str, int | None]:
    counter_id = (
        None
        if (
            getattr(request, "_is_public_selfie_bearer_request", False)
            or request.resolver_match
            and request.resolver_match.url_name == "event_detail"
        )
        else settings.YANDEX_METRIKA_COUNTER_ID
    )
    return {"yandex_metrika_counter_id": counter_id}

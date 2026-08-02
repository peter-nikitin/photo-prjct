from django.conf import settings
from django.http import HttpRequest


def analytics(request: HttpRequest) -> dict[str, int | None]:  # noqa: ARG001
    return {"yandex_metrika_counter_id": settings.YANDEX_METRIKA_COUNTER_ID}

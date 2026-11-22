import re
from collections.abc import Callable
from functools import wraps

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from agent_api.models import AgentToken, token_digest

View = Callable[..., HttpResponse]
TOKEN = re.compile(r"(?i:Bearer) (fmp_[A-Za-z0-9_-]{43})")


def error(code: str, message: str, status: int) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message, "fields": {}}}, status=status)


def agent_view(view: View) -> View:
    @csrf_exempt
    @never_cache
    @wraps(view)
    def wrapped(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        match = TOKEN.fullmatch(request.headers.get("Authorization", ""))
        now = timezone.now()
        token = None
        if match:
            token = (
                AgentToken.objects.select_related("owner")
                .filter(
                    secret_hash=token_digest(match[1]),
                    expires_at__gt=now,
                    revoked_at__isnull=True,
                    owner__is_active=True,
                    owner__is_staff=True,
                )
                .first()
            )
        if token is None:
            response = error("unauthorized", "A valid personal token is required.", 401)
            response["WWW-Authenticate"] = "Bearer"
            return response
        AgentToken.objects.filter(pk=token.pk).update(last_used_at=now)
        request.user = token.owner
        return view(request, *args, **kwargs)

    return wrapped

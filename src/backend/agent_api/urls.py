from django.http import HttpRequest, HttpResponse
from django.urls import path
from ingestion import views as ingestion

from agent_api import read, upload
from agent_api.auth import View, agent_view, error


def endpoint(*, get: View | None = None, post: View | None = None) -> View:
    methods = {method: view for method, view in (("GET", get), ("POST", post)) if view}

    @agent_view
    def dispatch(request: HttpRequest, **kwargs: object) -> HttpResponse:
        selected = methods.get(request.method or "")
        if selected is None:
            response = error("method_not_allowed", "This method is unavailable.", 405)
            response["Allow"] = ", ".join(methods)
            return response
        return selected(request, **kwargs)

    return dispatch


urlpatterns = [
    path("whoami/", endpoint(get=read.whoami)),
    path("events/", endpoint(get=read.events)),
    path("events/<int:event>/", endpoint(get=read.event_detail)),
    path("events/<int:event>/folders/", endpoint(get=read.folders)),
    path("batches/", endpoint(get=read.batches, post=upload.start)),
    path("batches/<uuid:batch>/", endpoint(get=read.batch_detail)),
    path(
        "batches/<uuid:batch>/items/",
        endpoint(get=read.items, post=upload.register),
    ),
    path("batches/<uuid:batch>/items/<uuid:item>/", endpoint(get=read.item_detail)),
    path(
        "batches/<uuid:batch>/items/<uuid:item>/authorize/",
        endpoint(post=ingestion.upload_item_authorize),
    ),
    path(
        "batches/<uuid:batch>/items/<uuid:item>/retry/", endpoint(post=ingestion.upload_item_retry)
    ),
    path(
        "batches/<uuid:batch>/items/<uuid:item>/confirm/",
        endpoint(post=ingestion.upload_item_confirm),
    ),
    path(
        "batches/<uuid:batch>/items/<uuid:item>/failed/",
        endpoint(post=ingestion.upload_item_failed),
    ),
    path("batches/<uuid:batch>/finalize/", endpoint(post=ingestion.upload_batch_finalize)),
]

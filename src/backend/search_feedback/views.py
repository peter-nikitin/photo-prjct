from typing import cast

from config.views import _paid_watermarked_previews_enabled
from django import forms
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import cache_control
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST
from feature_flags import services as feature_flag_services
from feature_flags.registry import GALLERY_BIB_SEARCH
from picflow.forms import BibSearchForm
from picflow.gallery import gallery_photo_queryset
from picflow.models import Event
from selfie_search.models import SelfieSearch
from selfie_search.services.negative_feedback import (
    negative_feedback_state,
    refresh_negative_feedback,
)
from selfie_search.services.results import (
    PublicSearchNotFound,
    resolve_public_result,
    saved_ready_result_row,
)

from search_feedback.models import SearchFeedback
from search_feedback.services import record_result_report, set_result_report


class ResultReportForm(BibSearchForm):
    photo_id = forms.CharField(max_length=32, strip=False)
    submission_id = forms.UUIDField()
    public_token = forms.CharField(max_length=128, required=False, strip=False)
    action = forms.ChoiceField(choices=(("mark", "Mark"), ("unmark", "Unmark")), required=False)

    def clean(self) -> dict:
        data = super().clean()
        if bool(data.get("public_token")) == bool(data.get("bib")):
            raise forms.ValidationError("Specify one search context.")
        if data.get("public_token") and not data.get("action"):
            raise forms.ValidationError("Specify an action for a saved result.")
        return data


@require_POST
@cache_control(private=True, no_store=True)
@sensitive_post_parameters()
@sensitive_variables()
def submit(request, event_slug: str) -> JsonResponse:
    form = ResultReportForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"status": "invalid"}, status=422)
    photo_id = form.cleaned_data["photo_id"]
    token = form.cleaned_data["public_token"]
    paid_previews_enabled = _paid_watermarked_previews_enabled(request)
    if token:
        return _submit_face_report(
            request,
            event_slug=event_slug,
            form=form,
            paid_previews_enabled=paid_previews_enabled,
        )

    if not feature_flag_services.is_enabled(GALLERY_BIB_SEARCH, request.user):
        raise Http404
    event = get_object_or_404(
        Event.objects.site_visible_to(request.user), slug=event_slug, bib_search_enabled=True
    )
    if event.access_type != Event.AccessType.FREE and not paid_previews_enabled:
        raise Http404
    bib = form.cleaned_data["bib"]
    if (
        not gallery_photo_queryset(
            event=event,
            bib_number=bib,
            paid_watermarked_previews_enabled=paid_previews_enabled,
        )
        .filter(pk=photo_id)
        .exists()
    ):
        raise Http404

    record_result_report(
        submission_id=form.cleaned_data["submission_id"],
        event_id=event.pk,
        photo_id=photo_id,
        search_type=cast(SearchFeedback.SearchType, SearchFeedback.SearchType.BIB),
        search_context={"bib_number": bib},
    )
    return JsonResponse({"status": "saved"}, status=201)


@transaction.atomic
@sensitive_variables()
def _submit_face_report(
    request,
    *,
    event_slug: str,
    form: ResultReportForm,
    paid_previews_enabled: bool,
) -> JsonResponse:
    try:
        search = resolve_public_result(
            event_slug=event_slug, public_token=form.cleaned_data["public_token"], user=request.user
        )
    except PublicSearchNotFound:
        raise Http404 from None
    search = (
        SelfieSearch.objects.select_for_update(of=("self",))
        .select_related("event")
        .get(pk=search.pk)
    )
    photo_id = form.cleaned_data["photo_id"]
    row = saved_ready_result_row(
        search=search,
        photo_id=photo_id,
        paid_watermarked_previews_enabled=paid_previews_enabled,
    )
    if row is None:
        raise Http404
    if search.negative_feedback_photos.get(photo_id) == "excluded":
        raise Http404

    search_type = (
        SearchFeedback.SearchType.GALLERY
        if search.configuration.get("processor") == "gallery_photo_query"
        else SearchFeedback.SearchType.SELFIE
    )
    context = {
        "search_id": str(search.pk),
        "result_id": str(row.pk),
        "primary_source": row.primary_source,
    }
    source = search.configuration.get("query_source")
    if search_type == SearchFeedback.SearchType.GALLERY and isinstance(source, dict):
        if source.get("kind") == "gallery_photo":
            for key in ("photo_id", "detection_id"):
                if isinstance(source.get(key), str):
                    context[f"source_{key}"] = source[key]

    mark = form.cleaned_data["action"] == "mark"
    changed = set_result_report(
        search=search,
        photo_id=photo_id,
        submission_id=form.cleaned_data["submission_id"],
        search_type=cast(SearchFeedback.SearchType, search_type),
        search_context=context,
        mark=mark,
    )
    state = refresh_negative_feedback(search) if changed else negative_feedback_state(search)
    return JsonResponse(
        {
            "status": "saved",
            "manual_photo_ids": sorted(state.manual_photo_ids),
            "similar_photo_ids": sorted(state.similar_photo_ids),
            "excluded_photo_count": len(state.excluded_photo_ids),
        },
        status=201 if mark else 200,
    )

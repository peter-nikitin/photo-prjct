from datetime import date

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from picflow.models import Event


def health(request):  # noqa: ARG001
    return JsonResponse({"status": "ok"})


def event_catalog(request):
    today = date.today()
    published = Event.objects.published()
    upcoming = list(published.filter(end_date__gte=today).order_by("start_date", "name"))
    past = list(published.filter(end_date__lt=today).order_by("-start_date", "name"))
    return render(request, "catalog/event_catalog.html", {"events": [*upcoming, *past]})


def event_detail(request, slug: str):
    event = get_object_or_404(Event.objects.published(), slug=slug)
    return render(request, "catalog/event_detail.html", {"event": event})


def legacy_events_redirect(request):  # noqa: ARG001
    return redirect("event_catalog")


def legal(request):
    return render(request, "legal.html")

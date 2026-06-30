import json
from pathlib import Path

from django.shortcuts import render

from picflow.models import Event, Photo


def build_bootstrap_context():
    events = [
        {
            "name": event.name,
            "dateFrom": event.date_from.isoformat(),
            "dateTo": event.date_to.isoformat(),
            "location": event.location,
            "description": event.description,
            "active": event.active,
        }
        for event in Event.objects.order_by("date_from", "name")
    ]
    demo_images = {
        photo.id: f"/static/assets/{Path(photo.src.name).name}"
        for photo in Photo.objects.select_related("event").order_by("id")
        if photo.src
    }
    return {
        "picflow_default_events_json": json.dumps(events, ensure_ascii=False),
        "picflow_demo_images_json": json.dumps(demo_images, ensure_ascii=False),
    }


def render_page(request, template_name, extra_context=None):
    context = build_bootstrap_context()
    if extra_context:
        context.update(extra_context)
    return render(request, template_name, context)


def index(request):
    return render_page(request, "index.html")


def events(request):
    return render_page(request, "events.html")


def dashboard(request):
    return render_page(request, "admin.html")


def upload(request):
    return render_page(request, "upload.html")


def orders(request):
    return render_page(request, "orders.html")


def promos(request):
    return render_page(request, "promos.html")


def purchased(request):
    return render_page(request, "purchased.html")


def legal(request):
    return render_page(request, "legal.html")

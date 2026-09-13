# ruff: noqa: E402
"""Local-only fixture and read-only one-event evidence inside the disposable web container."""

import hashlib
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import boto3
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client
from picflow.gallery import gallery_photo_queryset
from picflow.models import Event, Photo
from processing.management.commands.report_event_bib_processing import _build_report, _distribution
from processing.models import BibReading, PhotoProcessingState, ProcessingAttempt

SLUG = os.environ["BIB_EVENT_SLUG"]
BASELINE = json.loads(Path("/baseline.json").read_text())


def seed():
    if (
        Event.objects.filter(slug=SLUG).exists()
        or Photo.objects.filter(original_key__isnull=False).exists()
    ):
        raise RuntimeError(
            "acceptance target must be new; migration catalog fixtures are permitted"
        )
    get_user_model().objects.create_superuser("bib-local", password="local-only-password")
    Event.objects.create(
        name="Local Istra bib acceptance",
        slug=SLUG,
        city="Istra",
        start_date="2026-04-19",
        end_date="2026-04-19",
        timezone_name="Europe/Moscow",
        publication_status="published",
        bib_search_enabled=True,
    )
    s3 = boto3.client(
        "s3",
        endpoint_url="http://minio:9000",
        aws_access_key_id="local-minio",
        aws_secret_access_key="local-minio-password",
        region_name="us-east-1",
    )
    for attempt in range(30):
        try:
            s3.create_bucket(Bucket="local-private")
            return
        except Exception:
            if attempt == 29:
                raise
            time.sleep(1)


def upload():
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
    base = "http://web:8000"
    login = base + "/photographer/login/"
    opener.open(login, timeout=10).read()

    def csrf():
        return next(c.value for c in cookies if c.name == "csrftoken")

    opener.open(
        login,
        data=urllib.parse.urlencode(
            {
                "username": "bib-local",
                "password": "local-only-password",
                "csrfmiddlewaretoken": csrf(),
            }
        ).encode(),
        timeout=10,
    ).read()

    def post(path, data):
        request = urllib.request.Request(
            base + path,
            data=json.dumps(data).encode(),
            headers={"X-CSRFToken": csrf(), "Content-Type": "application/json"},
        )
        with opener.open(request, timeout=60) as response:
            return json.load(response)

    event = Event.objects.get(slug=SLUG)
    photos = BASELINE["corpus"]["photos"]
    batch = post(
        "/photographer/uploads/batches/", {"event_id": event.pk, "expected_item_count": len(photos)}
    )["batch"]["id"]
    prefix = f"/photographer/uploads/{batch}/"
    for photo in photos:
        path = Path("/source") / photo["relative_path"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != photo["sha256"]:
            raise RuntimeError("source checksum changed")
        item = post(
            prefix + "items/",
            {
                "items": [
                    {
                        "client_item_id": str(uuid.uuid4()),
                        "filename": path.name,
                        "size": path.stat().st_size,
                        "content_type": "image/jpeg",
                    }
                ]
            },
        )["items"][0]["id"]
        grant = post(prefix + f"items/{item}/authorize/", {"reason": "data_attempt"})["grant"]
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in grant["fields"].items():
            parts.append(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
                    f"\r\n{value}\r\n"
                ).encode()
            )
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{path.name}"\r\nContent-Type: image/jpeg\r\n\r\n'
            ).encode()
            + path.read_bytes()
            + f"\r\n--{boundary}--\r\n".encode()
        )
        request = urllib.request.Request(
            grant["url"],
            data=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        urllib.request.urlopen(request, timeout=120).read()
        post(prefix + f"items/{item}/confirm/", {})
    post(prefix + "finalize/", {})
    print(json.dumps({"uploaded": len(photos)}))


def snapshot():
    event = Event.objects.get(slug=SLUG)
    published = set(gallery_photo_queryset(event=event).values_list("pk", flat=True))
    states = list(PhotoProcessingState.objects.filter(photo__event=event))
    rows = []
    for source in BASELINE["corpus"]["photos"]:
        photo = Photo.objects.filter(event=event, original_filename=source["relative_path"]).first()
        if photo is None:
            continue
        owned = [s for s in states if s.photo_id == photo.pk]
        terminal = all(
            any(
                s.processor_type == kind and s.status in ("succeeded", "failed", "cancelled")
                for s in owned
            )
            for kind in (
                "capture_metadata",
                "generate_preview",
                "face_embedding",
                "bib_recognition",
            )
        ) and not any(s.status in ("queued", "processing", "retry_wait") for s in owned)
        rows.append(
            {
                "relative_path": source["relative_path"],
                "sha256": source["sha256"],
                "photo_id": photo.pk,
                "event_slug": event.slug,
                "published": photo.pk in published,
                "terminal": terminal,
                "numbers": list(
                    BibReading.objects.filter(photo=photo)
                    .order_by("number")
                    .values_list("number", flat=True)
                ),
            }
        )
    report = _build_report(event)
    attempts = list(ProcessingAttempt.objects.filter(event=event))
    stages = dict(report["durations_ms"])
    for kind in ("capture_metadata", "generate_preview", "face_embedding"):
        stages[kind] = _distribution(
            a.total_duration_ms for a in attempts if a.processor_type == kind and a.accepted
        )
    leases = [
        (a.terminal_at - a.claimed_at).total_seconds()
        for a in attempts
        if a.terminal_at and a.claimed_at
    ]
    print(
        json.dumps(
            {
                "event_slug": SLUG,
                "rows": rows,
                "aggregate": report,
                "stages": stages,
                "lease_expired": sum(a.status == "expired" for a in attempts),
                "lease_max_seconds": max(leases, default=0),
                "terminal": len(rows) == len(BASELINE["corpus"]["photos"])
                and all(r["terminal"] for r in rows),
            }
        )
    )


def search():
    event = Event.objects.get(slug=SLUG)
    numbers = set(BibReading.objects.filter(photo__event=event).values_list("number", flat=True))
    numbers |= {n for _, n in BASELINE["confirmed"] + BASELINE["rejected"]}
    numbers |= {str(int(n)) if n.startswith("0") else "0" + n for n in numbers if len(n) < 16}
    results = {}
    for number in sorted(numbers):
        with urllib.request.urlopen(
            f"http://web:8000/events/{SLUG}/?" + urllib.parse.urlencode({"bib": number}), timeout=30
        ) as response:
            results[number] = re.findall(
                r'<figure class="gallery-card" data-photo-id="([^"]+)"', response.read().decode()
            )
    # Exercise the real public view against an empty event identity inside a rollback
    # transaction. No second event survives and no projection is fabricated.
    with transaction.atomic():
        other = Event.objects.create(
            name="Transactional isolation probe",
            slug="local-isolation-probe",
            city="Istra",
            start_date="2026-04-19",
            end_date="2026-04-19",
            timezone_name="Europe/Moscow",
            publication_status="published",
            bib_search_enabled=True,
        )
        client = Client()
        isolated = True
        for number in sorted(numbers):
            response = client.get(f"/events/{other.slug}/", {"bib": number})
            isolated &= (
                response.status_code == 200
                and b'<figure class="gallery-card"' not in response.content
            )
        transaction.set_rollback(True)
    print(json.dumps({"search": results, "isolation_passed": isolated}))


if __name__ == "__main__":
    {"seed": seed, "upload": upload, "snapshot": snapshot, "search": search}[sys.argv[1]]()

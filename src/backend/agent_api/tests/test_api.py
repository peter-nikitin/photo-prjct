from datetime import date
from uuid import uuid4

import pytest
from django.contrib.auth.models import Permission
from django.test import Client
from ingestion.models import UploadBatch
from picflow.models import Event

pytestmark = pytest.mark.django_db
ROOT = "/api/agent/v1/"


def test_browser_session_cannot_authorize_agent_requests(django_user_model):
    staff = django_user_model.objects.create_user(username="operator", is_staff=True)
    client = Client(enforce_csrf_checks=True)
    client.force_login(staff)
    for response in (client.get(ROOT + "whoami/"), client.post(ROOT + "batches/", {})):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


def test_start_recovers_lost_response_and_checks_current_permissions(django_user_model, settings):
    from agent_api.models import AgentToken

    settings.PHOTO_UPLOAD_ENABLED = True
    staff = django_user_model.objects.create_user(username="operator", is_staff=True)
    secret = AgentToken.issue(owner=staff, name="Laptop")
    staff.user_permissions.add(
        Permission.objects.get(codename="view_event"),
        Permission.objects.get(codename="upload_photos"),
    )
    event = Event.objects.create(
        name="Draft", slug="draft", start_date=date.today(), end_date=date.today()
    )
    client = Client(enforce_csrf_checks=True, HTTP_AUTHORIZATION=f"Bearer {secret}")
    payload = {"client_task_id": str(uuid4()), "event_id": event.pk, "expected_item_count": 1}

    first = client.post(ROOT + "batches/", payload, content_type="application/json")
    replay = client.post(ROOT + "batches/", payload, content_type="application/json")
    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.json()["batch"]["id"] == replay.json()["batch"]["id"]
    assert UploadBatch.objects.get().uploader == staff
    assert (
        client.post(
            ROOT + "batches/",
            {**payload, "expected_item_count": 2},
            content_type="application/json",
        ).status_code
        == 409
    )

    staff.user_permissions.clear()
    assert (
        client.post(ROOT + "batches/", payload, content_type="application/json").status_code == 403
    )
    token = AgentToken.objects.get(owner=staff)
    assert token.secret_hash != secret
    assert token.last_used_at is not None
    token.revoke()
    assert client.get(ROOT + "whoami/").status_code == 401


def test_admin_issues_secret_once_and_keeps_token_ownership(django_user_model, settings):
    import re

    from agent_api.models import AgentToken
    from django.urls import reverse

    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    staff = django_user_model.objects.create_user(username="operator", is_staff=True)
    other = django_user_model.objects.create_user(username="other", is_staff=True)
    admin = django_user_model.objects.create_superuser(username="root")
    client = Client()
    client.force_login(staff)
    create = reverse("admin:agent_api_agenttoken_add")
    listing = reverse("admin:agent_api_agenttoken_changelist")
    issue = client.post(create, {"name": "Laptop", "owner": other.pk, "_save": "1"})
    assert issue.status_code == 200
    assert "no-store" in issue.headers["Cache-Control"]
    match = re.search(rb"fmp_[A-Za-z0-9_-]{43}", issue.content)
    assert match is not None
    secret = match[0].decode()
    token = AgentToken.objects.get()
    assert token.owner_id == staff.pk
    assert secret.encode() not in client.get(listing).content
    api = Client(HTTP_AUTHORIZATION=f"Bearer {secret}")
    assert api.get(ROOT + "whoami/").json()["user"]["id"] == staff.pk

    selection = {"action": "revoke_tokens", "_selected_action": [token.pk]}
    client.force_login(other)
    client.post(listing, selection)
    assert api.get(ROOT + "whoami/").status_code == 200
    client.force_login(admin)
    client.post(listing, selection)
    assert api.get(ROOT + "whoami/").status_code == 401


def test_batch_and_item_details_are_owned_and_auth_checks_staff_state(django_user_model, settings):
    from agent_api.models import AgentToken

    settings.PHOTO_UPLOAD_ENABLED = True
    operator = django_user_model.objects.create_superuser(username="operator")
    other = django_user_model.objects.create_superuser(username="other")
    event = Event.objects.create(
        name="Draft",
        slug="draft",
        start_date=date.today(),
        end_date=date.today(),
    )
    batch = UploadBatch.objects.create(uploader=other, event=event, expected_item_count=1)
    secret = AgentToken.issue(owner=operator, name="Laptop")
    client = Client(HTTP_AUTHORIZATION=f"Bearer {secret}")
    batch_url = ROOT + f"batches/{batch.pk}/"
    assert client.get(batch_url).status_code == 404
    assert client.get(batch_url + "items/").status_code == 404
    assert (
        client.post(batch_url + "finalize/", {}, content_type="application/json").status_code == 404
    )
    operator.is_staff = False
    operator.save(update_fields=["is_staff"])
    assert client.get(ROOT + "whoami/").status_code == 401


@pytest.mark.parametrize("condition", ["inactive", "expired"])
def test_api_requires_current_active_unexpired_token(django_user_model, condition):
    from datetime import timedelta

    from agent_api.models import AgentToken
    from django.utils import timezone

    staff = django_user_model.objects.create_user(username="operator", is_staff=True)
    secret = AgentToken.issue(owner=staff, name="Laptop")
    client = Client(HTTP_AUTHORIZATION=f"Bearer {secret}")
    assert client.get(ROOT + "whoami/").status_code == 200
    if condition == "inactive":
        staff.is_active = False
        staff.save(update_fields=["is_active"])
    else:
        AgentToken.objects.filter(owner=staff).update(expires_at=timezone.now() - timedelta(days=1))
    assert client.get(ROOT + "whoami/").status_code == 401


def test_existing_batch_checks_current_upload_permission_and_folder_event(
    django_user_model,
    settings,
):
    from agent_api.models import AgentToken
    from django.urls import reverse
    from ingestion.models import UploadItem
    from picflow.models import EventFolder

    settings.PHOTO_UPLOAD_ENABLED = True
    staff = django_user_model.objects.create_user(username="operator", is_staff=True)
    permission = Permission.objects.get(codename="upload_photos")
    staff.user_permissions.add(permission)
    event = Event.objects.create(
        name="Draft",
        slug="draft",
        start_date=date.today(),
        end_date=date.today(),
    )
    other_event = Event.objects.create(
        name="Other",
        slug="other",
        start_date=date.today(),
        end_date=date.today(),
    )
    folder = EventFolder.objects.create(event=other_event, name="Finish")
    batch = UploadBatch.objects.create(uploader=staff, event=event, expected_item_count=1)
    secret = AgentToken.issue(owner=staff, name="Laptop")
    agent = Client(enforce_csrf_checks=True, HTTP_AUTHORIZATION=f"Bearer {secret}")
    url = ROOT + f"batches/{batch.pk}/items/"
    payload = {
        "items": [
            {
                "client_item_id": str(uuid4()),
                "filename": "photo.jpg",
                "size": 512,
                "content_type": "image/jpeg",
                "folder_id": folder.pk,
            }
        ]
    }
    response = agent.post(url, payload, content_type="application/json")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "folder_not_found"
    assert not UploadItem.objects.filter(batch=batch).exists()

    browser = Client(enforce_csrf_checks=True)
    browser.force_login(staff)
    response = browser.post(
        reverse("upload_items_register", args=[batch.pk]), payload, content_type="application/json"
    )
    assert response.status_code == 403
    staff.user_permissions.remove(permission)
    assert agent.post(url, payload, content_type="application/json").status_code == 403


@pytest.mark.parametrize(
    ("scenario", "status"),
    [
        ("fully_registered", 409),
        ("browser_batch", 409),
        ("foreign_batch", 404),
        ("invalid_item", 409),
    ],
)
def test_registration_does_not_reopen_ineligible_expired_batch(
    django_user_model,
    settings,
    monkeypatch,
    scenario,
    status,
):
    from datetime import timedelta

    from agent_api.models import AgentToken
    from django.core.management import call_command
    from django.utils import timezone
    from ingestion.management.commands import cleanup_stale_uploads
    from ingestion.tests.test_import_flow import ByteStorage

    settings.PHOTO_UPLOAD_ENABLED = True
    owner = django_user_model.objects.create_superuser(username="operator")
    secret = AgentToken.issue(owner=owner, name="Laptop")
    agent = Client(HTTP_AUTHORIZATION=f"Bearer {secret}")
    event = Event.objects.create(
        name="Draft",
        slug="draft",
        start_date=date.today(),
        end_date=date.today(),
    )
    if scenario == "browser_batch":
        batch = UploadBatch.objects.create(uploader=owner, event=event, expected_item_count=1)
    else:
        created = agent.post(
            ROOT + "batches/",
            {"client_task_id": str(uuid4()), "event_id": event.pk, "expected_item_count": 1},
            content_type="application/json",
        )
        assert created.status_code == 201
        batch = UploadBatch.objects.get(pk=created.json()["batch"]["id"])
    url = ROOT + f"batches/{batch.pk}/items/"
    payload = {
        "items": [
            {
                "client_item_id": str(uuid4()),
                "filename": "photo.jpg",
                "size": 512,
                "content_type": "image/jpeg",
            }
        ],
    }
    if scenario == "fully_registered":
        assert agent.post(url, payload, content_type="application/json").status_code == 201
    elif scenario == "foreign_batch":
        other = django_user_model.objects.create_superuser(username="other")
        other_secret = AgentToken.issue(owner=other, name="Other")
        agent = Client(HTTP_AUTHORIZATION=f"Bearer {other_secret}")
    elif scenario == "invalid_item":
        payload["items"][0]["folder_id"] = 9999999

    UploadBatch.objects.filter(pk=batch.pk).update(
        last_activity_at=timezone.now() - timedelta(days=2)
    )
    monkeypatch.setattr(cleanup_stale_uploads, "PrivateUploadStorage", ByteStorage)
    call_command("cleanup_stale_uploads")
    batch.refresh_from_db()
    assert batch.status == UploadBatch.Status.FAILED
    completed_at = batch.completed_at
    response = agent.post(url, payload, content_type="application/json")
    assert response.status_code == status
    if scenario == "invalid_item":
        assert response.json()["error"]["code"] == "folder_not_found"
    batch.refresh_from_db()
    assert batch.status == UploadBatch.Status.FAILED
    assert batch.completed_at == completed_at

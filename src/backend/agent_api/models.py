import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def token_digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class AgentToken(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    secret_hash = models.CharField(max_length=64, unique=True, editable=False)  # noqa: DJ001
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return self.name

    @classmethod
    def issue(cls, *, owner, name: str) -> str:
        token = cls(owner=owner, name=name)
        secret = token.prepare()
        token.save()
        return secret

    def prepare(self) -> str:
        secret = "fmp_" + secrets.token_urlsafe(32)
        self.secret_hash = token_digest(secret)
        self.expires_at = timezone.now() + timedelta(days=90)
        return secret

    def revoke(self) -> None:
        type(self).objects.filter(pk=self.pk, revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )


class AgentUpload(models.Model):  # noqa: DJ008
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    client_task_id = models.UUIDField()
    batch = models.OneToOneField("ingestion.UploadBatch", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "client_task_id"], name="agent_upload_owner_task_uniq"
            )
        ]

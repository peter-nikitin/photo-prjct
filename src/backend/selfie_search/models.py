import json
from math import isfinite
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from picflow.models import Event, Photo
from processing.models import (
    FaceCluster,
    FaceClusterCorpus,
    FaceClusterMember,
    PhotoFaceDetection,
)

JSON_MAX_BYTES = 16_384
FEEDBACK_OBJECT_MAX_BYTES = 20 * 1024 * 1024
FEEDBACK_CONSENT_TEXT_VERSION = "2026-08-04"
_TERMINAL_SEARCH_STATUSES = (
    "ready",
    "no_face",
    "multiple_faces",
    "quality_rejected",
    "search_unavailable",
    "failed",
)
_CLUSTER_EXPANSION_OUTCOMES = (
    "expanded",
    "no_strong_anchor",
    "no_new_photos",
    "corpus_unavailable",
    "corpus_incompatible",
    "disabled",
)


def validate_bounded_json(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if len(serialized.encode()) > JSON_MAX_BYTES:
        raise ValidationError(f"JSON payload must not exceed {JSON_MAX_BYTES} bytes.")


class SelfieSearch(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        CLEANUP_PENDING = "cleanup_pending", "Cleanup pending"
        READY = "ready", "Ready"
        NO_FACE = "no_face", "No face"
        MULTIPLE_FACES = "multiple_faces", "Multiple faces"
        QUALITY_REJECTED = "quality_rejected", "Quality rejected"
        SEARCH_UNAVAILABLE = "search_unavailable", "Search unavailable"
        FAILED = "failed", "Failed"

    class ClusterExpansionOutcome(models.TextChoices):
        EXPANDED = "expanded", "Expanded"
        NO_STRONG_ANCHOR = "no_strong_anchor", "No strong anchor"
        NO_NEW_PHOTOS = "no_new_photos", "No new photos"
        CORPUS_UNAVAILABLE = "corpus_unavailable", "Corpus unavailable"
        CORPUS_INCOMPATIBLE = "corpus_incompatible", "Corpus incompatible"
        DISABLED = "disabled", "Disabled"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.PROTECT, related_name="selfie_searches")
    public_token_digest = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=24, choices=Status, default=Status.QUEUED)
    temporary_object_key = models.CharField(max_length=255)
    temporary_object_etag = models.CharField(max_length=128, blank=True, default="")
    configuration = models.JSONField(default=dict, validators=[validate_bounded_json])
    configuration_hash = models.CharField(max_length=64, blank=True, default="")
    eligible_photo_count = models.PositiveIntegerField(default=0)
    eligible_face_count = models.PositiveIntegerField(default=0)
    matched_photo_count = models.PositiveIntegerField(default=0)
    final_matched_photo_count = models.PositiveIntegerField(null=True, blank=True)
    cluster_corpus = models.ForeignKey(
        FaceClusterCorpus,
        on_delete=models.PROTECT,
        related_name="selfie_searches",
        null=True,
        blank=True,
    )
    cluster_corpus_version = models.PositiveSmallIntegerField(null=True, blank=True)
    cluster_configuration_hash = models.CharField(  # noqa: DJ001
        max_length=64, null=True, blank=True
    )
    direct_matched_photo_count = models.PositiveIntegerField(null=True, blank=True)
    cluster_expanded_photo_count = models.PositiveIntegerField(null=True, blank=True)
    strong_anchor_count = models.PositiveIntegerField(null=True, blank=True)
    expanded_cluster_count = models.PositiveIntegerField(null=True, blank=True)
    cluster_expansion_outcome = models.CharField(  # noqa: DJ001
        max_length=24,
        choices=ClusterExpansionOutcome,
        null=True,
        blank=True,
    )
    failure_code = models.CharField(max_length=64, blank=True, default="")
    intended_terminal_status = models.CharField(max_length=24, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    state_changed_at = models.DateTimeField(default=timezone.now)
    terminal_at = models.DateTimeField(null=True, blank=True)
    cleanup_confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=(
                        "queued",
                        "processing",
                        "cleanup_pending",
                        "ready",
                        "no_face",
                        "multiple_faces",
                        "quality_rejected",
                        "search_unavailable",
                        "failed",
                    )
                ),
                name="selfie_search_status_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    intended_terminal_status__in=(
                        "",
                        "ready",
                        "no_face",
                        "multiple_faces",
                        "quality_rejected",
                        "search_unavailable",
                        "failed",
                    )
                ),
                name="selfie_search_intended_status_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(cluster_corpus_version__isnull=True)
                | models.Q(cluster_corpus_version__gte=1),
                name="selfie_search_cluster_version_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(cluster_expansion_outcome__isnull=True)
                | models.Q(cluster_expansion_outcome__in=_CLUSTER_EXPANSION_OUTCOMES),
                name="selfie_search_cluster_outcome_chk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(direct_matched_photo_count__isnull=True)
                    | models.Q(cluster_expanded_photo_count__isnull=True)
                    | models.Q(
                        final_matched_photo_count=models.F("direct_matched_photo_count")
                        + models.F("cluster_expanded_photo_count")
                    )
                ),
                name="selfie_search_result_count_identity_chk",
            ),
        ]
        indexes = [models.Index(fields=["event", "status"], name="selfie_search_event_status_idx")]

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.cluster_corpus_id:
            if self.cluster_corpus.event_id != self.event_id:
                errors["cluster_corpus"] = "The selected corpus must belong to the search event."
            if self.cluster_corpus_version != self.cluster_corpus.version:
                errors["cluster_corpus_version"] = (
                    "The corpus version must match the selected corpus."
                )
        elif self.cluster_corpus_version is not None:
            errors["cluster_corpus_version"] = "A corpus version requires a selected corpus."
        if self.cluster_expansion_outcome == self.ClusterExpansionOutcome.EXPANDED:
            if self.cluster_expanded_photo_count is None or self.cluster_expanded_photo_count <= 0:
                errors["cluster_expanded_photo_count"] = (
                    "Expanded searches require at least one cluster photo."
                )
        elif (
            self.cluster_expansion_outcome in _CLUSTER_EXPANSION_OUTCOMES
            and self.cluster_expanded_photo_count is not None
            and self.cluster_expanded_photo_count != 0
        ):
            errors["cluster_expanded_photo_count"] = (
                "Non-expanded outcomes cannot add cluster photos."
            )
        if (
            self.direct_matched_photo_count is not None
            and self.cluster_expanded_photo_count is not None
            and self.final_matched_photo_count
            != self.direct_matched_photo_count + self.cluster_expanded_photo_count
        ):
            errors["final_matched_photo_count"] = "Search result counts must reconcile."
        if errors:
            raise ValidationError(errors)


class SelfieSearchJob(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        RETRY_WAIT = "retry_wait", "Retry wait"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    search = models.OneToOneField(SelfieSearch, on_delete=models.PROTECT, related_name="job")
    status = models.CharField(max_length=16, choices=Status, default=Status.QUEUED)
    configuration = models.JSONField(default=dict, validators=[validate_bounded_json])
    available_at = models.DateTimeField(default=timezone.now)
    claimed_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=("queued", "processing", "retry_wait", "succeeded", "failed")
                ),
                name="selfie_job_status_chk",
            )
        ]
        indexes = [models.Index(fields=["status", "available_at"], name="selfie_job_claim_idx")]


class SelfieSearchAttempt(models.Model):  # noqa: DJ008
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "In progress"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"
        STALE = "stale", "Stale"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    job = models.ForeignKey(SelfieSearchJob, on_delete=models.PROTECT, related_name="attempts")
    status = models.CharField(max_length=16, choices=Status, default=Status.IN_PROGRESS)
    created_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    terminal_at = models.DateTimeField(null=True, blank=True)
    result_hash = models.CharField(max_length=64, blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_detail = models.CharField(max_length=512, blank=True, default="")
    download_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    compute_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    total_duration_ms = models.PositiveIntegerField(null=True, blank=True)
    worker_started_at = models.DateTimeField(null=True, blank=True)
    worker_finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    status__in=("in_progress", "succeeded", "failed", "expired", "stale")
                ),
                name="selfie_attempt_status_chk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="in_progress", terminal_at__isnull=True)
                    | models.Q(
                        status__in=("succeeded", "failed", "expired", "stale"),
                        terminal_at__isnull=False,
                    )
                ),
                name="selfie_attempt_terminal_at_chk",
            ),
        ]
        indexes = [
            models.Index(fields=["job", "status"], name="selfie_attempt_job_status_idx"),
            models.Index(fields=["lease_expires_at"], name="selfie_attempt_lease_idx"),
        ]


class SelfieSearchResult(models.Model):  # noqa: DJ008
    class PrimarySource(models.TextChoices):
        DIRECT = "direct", "Direct"
        FACE_CLUSTER_EXPANSION = "face_cluster_expansion", "Face-cluster expansion"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    search = models.ForeignKey(SelfieSearch, on_delete=models.PROTECT, related_name="results")
    photo = models.ForeignKey(Photo, on_delete=models.PROTECT, related_name="selfie_results")
    primary_source = models.CharField(
        max_length=32,
        choices=PrimarySource,
        default=PrimarySource.DIRECT,
    )
    rank = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("search", "photo"), name="selfie_result_photo_uniq"),
            models.UniqueConstraint(fields=("search", "rank"), name="selfie_result_rank_uniq"),
            models.CheckConstraint(
                condition=models.Q(primary_source__in=("direct", "face_cluster_expansion")),
                name="selfie_result_primary_source_chk",
            ),
        ]
        indexes = [models.Index(fields=["search", "rank"], name="selfie_result_search_rank_idx")]

    def save(self, *args, **kwargs) -> None:
        if (
            self.pk
            and self.__class__.objects.filter(
                pk=self.pk, search__status__in=_TERMINAL_SEARCH_STATUSES
            ).exists()
        ):
            raise ValidationError("Terminal selfie search results are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        if (
            self.pk
            and self.__class__.objects.filter(
                pk=self.pk, search__status__in=_TERMINAL_SEARCH_STATUSES
            ).exists()
        ):
            raise ValidationError("Terminal selfie search results are immutable.")
        return super().delete(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.search_id and self.photo_id and self.search.event_id != self.photo.event_id:
            errors["photo"] = "The result photo must belong to the search event."
        if errors:
            raise ValidationError(errors)


class SelfieSearchDirectEvidence(models.Model):  # noqa: DJ008
    """Immutable direct-match evidence for one saved result."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    result = models.OneToOneField(
        SelfieSearchResult,
        on_delete=models.PROTECT,
        related_name="direct_evidence",
    )
    detection = models.ForeignKey(
        PhotoFaceDetection,
        on_delete=models.PROTECT,
        related_name="selfie_search_direct_evidence",
    )
    cosine_distance = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(cosine_distance__gte=0, cosine_distance__lte=2),
                name="selfie_direct_evidence_distance_chk",
            )
        ]
        indexes = [
            models.Index(fields=["detection"], name="selfie_direct_evidence_det_idx"),
        ]

    def save(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Selfie direct evidence is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Selfie direct evidence is immutable.")
        return super().delete(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors = {}
        try:
            distance = float(self.cosine_distance)
        except (TypeError, ValueError):
            distance = None
        if distance is None or not isfinite(distance) or not 0 <= distance <= 2:
            errors["cosine_distance"] = "Cosine distance must be finite and between 0 and 2."
        if self.result_id and self.result.primary_source != SelfieSearchResult.PrimarySource.DIRECT:
            errors["result"] = "Direct evidence requires a direct-primary result."
        if self.result_id and self.detection_id:
            detection = self.detection
            if detection.attempt.event_id != self.result.search.event_id:
                errors["detection"] = "The direct detection must belong to the search event."
            if detection.attempt.photo_id != self.result.photo_id:
                errors["detection"] = "The direct detection must belong to the result photo."
        if errors:
            raise ValidationError(errors)


class SelfieSearchClusterEvidence(models.Model):  # noqa: DJ008
    """Immutable face-cluster source evidence for one saved result."""

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    result = models.ForeignKey(
        SelfieSearchResult,
        on_delete=models.PROTECT,
        related_name="cluster_evidence",
    )
    corpus = models.ForeignKey(
        FaceClusterCorpus,
        on_delete=models.PROTECT,
        related_name="selfie_search_evidence",
    )
    cluster = models.ForeignKey(
        FaceCluster,
        on_delete=models.PROTECT,
        related_name="selfie_search_evidence",
    )
    anchor_result = models.ForeignKey(
        SelfieSearchResult,
        on_delete=models.PROTECT,
        related_name="cluster_anchor_evidence",
    )
    anchor_detection = models.ForeignKey(
        PhotoFaceDetection,
        on_delete=models.PROTECT,
        related_name="selfie_search_anchor_evidence",
    )
    member_detection = models.ForeignKey(
        PhotoFaceDetection,
        on_delete=models.PROTECT,
        related_name="selfie_search_member_evidence",
    )
    representative_distance = models.FloatField()
    source_order = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("result", "corpus", "cluster"),
                name="selfie_cluster_evidence_result_cluster_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    representative_distance__gte=0,
                    representative_distance__lte=2,
                ),
                name="selfie_cluster_evidence_distance_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(source_order__gte=1),
                name="selfie_cluster_evidence_order_chk",
            ),
        ]
        indexes = [
            models.Index(fields=["result", "source_order"], name="selfie_cl_evidence_order_idx"),
        ]

    def save(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Selfie cluster evidence is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> tuple[int, dict[str, int]]:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Selfie cluster evidence is immutable.")
        return super().delete(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors = {}
        try:
            representative_distance = float(self.representative_distance)
        except (TypeError, ValueError):
            representative_distance = None
        if (
            representative_distance is None
            or not isfinite(representative_distance)
            or not 0 <= representative_distance <= 2
        ):
            errors["representative_distance"] = (
                "Representative distance must be finite and between 0 and 2."
            )
        if self.source_order is None or self.source_order < 1:
            errors["source_order"] = "Source order must be positive."
        if self.result_id and self.anchor_result_id:
            if self.anchor_result.search_id != self.result.search_id:
                errors["anchor_result"] = "The anchor result must belong to the same search."
            if self.anchor_result.primary_source != SelfieSearchResult.PrimarySource.DIRECT:
                errors["anchor_result"] = "The anchor result must be direct-primary."
            try:
                anchor_evidence_detection_id = self.anchor_result.direct_evidence.detection_id
            except SelfieSearchDirectEvidence.DoesNotExist:
                anchor_evidence_detection_id = None
            if anchor_evidence_detection_id != self.anchor_detection_id:
                errors["anchor_detection"] = "The anchor detection must match direct evidence."
        if self.result_id and self.corpus_id:
            if self.corpus.event_id != self.result.search.event_id:
                errors["corpus"] = "The evidence corpus must belong to the search event."
            if self.corpus.status != FaceClusterCorpus.Status.PUBLISHED:
                errors["corpus"] = "Cluster evidence requires a published corpus."
        if self.cluster_id and self.corpus_id:
            if self.cluster.corpus_id != self.corpus_id:
                errors["cluster"] = "The evidence cluster must belong to the corpus."
            if self.cluster.event_id != self.corpus.event_id:
                errors["cluster"] = "The evidence cluster must belong to the corpus event."
        if self.result_id and self.anchor_detection_id:
            if self.anchor_detection.attempt.event_id != self.result.search.event_id:
                errors["anchor_detection"] = "The anchor detection must belong to the search event."
        if self.result_id and self.member_detection_id:
            member_detection = self.member_detection
            if member_detection.attempt.event_id != self.result.search.event_id:
                errors["member_detection"] = "The member detection must belong to the search event."
            if member_detection.attempt.photo_id != self.result.photo_id:
                errors["member_detection"] = "The member detection must belong to the result photo."
        if self.corpus_id and self.cluster_id and self.member_detection_id:
            member = FaceClusterMember.objects.filter(
                corpus_id=self.corpus_id,
                cluster_id=self.cluster_id,
                detection_id=self.member_detection_id,
            ).first()
            if member is None:
                errors["member_detection"] = "The member detection must be in the selected cluster."
            elif representative_distance is not None:
                member_distance = float(member.distance_to_representative)
                if (
                    not isfinite(member_distance)
                    or abs(member_distance - representative_distance) > 1e-9
                ):
                    errors["representative_distance"] = (
                        "The representative distance must match the frozen member evidence."
                    )
        if self.corpus_id and self.cluster_id and self.anchor_detection_id:
            if not FaceClusterMember.objects.filter(
                corpus_id=self.corpus_id,
                cluster_id=self.cluster_id,
                detection_id=self.anchor_detection_id,
            ).exists():
                errors["anchor_detection"] = "The anchor detection must be in the selected cluster."
        if errors:
            raise ValidationError(errors)

    @property
    def strong_anchor_result(self) -> SelfieSearchResult:
        return self.anchor_result

    @property
    def strong_anchor_detection(self) -> PhotoFaceDetection:
        return self.anchor_detection

    @property
    def expanded_member_detection(self) -> PhotoFaceDetection:
        return self.member_detection


class SelfieSearchFeedback(models.Model):  # noqa: DJ008
    class Variant(models.TextChoices):
        PROBLEM = "problem", "Problem"
        RESULT_LABELS = "result_labels", "Result labels"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    search = models.OneToOneField(
        SelfieSearch,
        on_delete=models.PROTECT,
        related_name="feedback",
    )
    variant = models.CharField(max_length=16, choices=Variant)
    contact = models.CharField(max_length=254)
    personal_data_consent = models.BooleanField(default=False)
    consent_text_version = models.CharField(max_length=32)
    consented_at = models.DateTimeField()
    source_status = models.CharField(max_length=24, choices=SelfieSearch.Status)
    source_matched_photo_count = models.PositiveIntegerField(default=0)
    source_visible_result_count = models.PositiveIntegerField(default=0)
    source_configuration = models.JSONField(default=dict, validators=[validate_bounded_json])
    object_key = models.CharField(max_length=255, unique=True)
    object_content_type = models.CharField(max_length=100)
    object_size = models.PositiveBigIntegerField()
    object_uploaded_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions = [
            (
                "view_sensitive_feedback",
                "Can view sensitive selfie search feedback",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(variant__in=("problem", "result_labels")),
                name="selfie_feedback_variant_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(personal_data_consent=True),
                name="selfie_feedback_consent_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(source_status__in=_TERMINAL_SEARCH_STATUSES),
                name="selfie_feedback_source_status_chk",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        variant="problem",
                        source_status__in=(
                            "no_face",
                            "multiple_faces",
                            "quality_rejected",
                            "search_unavailable",
                            "failed",
                        ),
                    )
                    | models.Q(
                        variant="problem",
                        source_status="ready",
                        source_visible_result_count=0,
                    )
                    | models.Q(
                        variant="result_labels",
                        source_status="ready",
                        source_visible_result_count__gt=0,
                    )
                ),
                name="selfie_feedback_variant_source_chk",
            ),
            models.CheckConstraint(
                condition=~models.Q(contact=""),
                name="selfie_feedback_contact_nonempty_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(object_content_type__in=("image/jpeg", "image/png")),
                name="selfie_feedback_object_type_chk",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    object_size__gt=0,
                    object_size__lte=FEEDBACK_OBJECT_MAX_BYTES,
                ),
                name="selfie_feedback_object_size_chk",
            ),
        ]

    def __str__(self) -> str:
        return f"Selfie search feedback {self.pk}"

    def save(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Selfie search feedback is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Selfie search feedback is immutable.")
        super().delete(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.contact is not None:
            self.contact = self.contact.strip()
            if not self.contact:
                errors["contact"] = "Contact is required."
            elif any(ord(char) < 32 or ord(char) == 127 for char in self.contact):
                errors["contact"] = "Contact must not contain control characters."
        if not self.personal_data_consent:
            errors["personal_data_consent"] = "Personal-data consent is required."
        if not self.consent_text_version:
            errors["consent_text_version"] = "Consent text version is required."
        if self.consented_at is None:
            errors["consented_at"] = "Consent timestamp is required."
        if self.source_status not in _TERMINAL_SEARCH_STATUSES:
            errors["source_status"] = "Feedback source must be terminal."
        if self.variant == self.Variant.RESULT_LABELS and (
            self.source_status != SelfieSearch.Status.READY or self.source_visible_result_count <= 0
        ):
            errors["variant"] = "Result labels require a non-empty ready result."
        if self.variant == self.Variant.PROBLEM and self.source_status == SelfieSearch.Status.READY:
            if self.source_visible_result_count > 0:
                errors["variant"] = "Problem feedback cannot describe visible results."
        if errors:
            raise ValidationError(errors)


class SelfieSearchFeedbackLabel(models.Model):  # noqa: DJ008
    class Value(models.TextChoices):
        PRESENT = "present", "Present"
        ABSENT = "absent", "Absent"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    feedback = models.ForeignKey(
        SelfieSearchFeedback,
        on_delete=models.PROTECT,
        related_name="labels",
    )
    result = models.ForeignKey(
        SelfieSearchResult,
        on_delete=models.PROTECT,
        related_name="feedback_labels",
    )
    value = models.CharField(max_length=7, choices=Value)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("feedback", "result"),
                name="selfie_feedback_label_membership_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(value__in=("present", "absent")),
                name="selfie_feedback_label_value_chk",
            ),
        ]

    def __str__(self) -> str:
        return f"Feedback label {self.pk}"

    def save(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Selfie search feedback labels are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Selfie search feedback labels are immutable.")
        super().delete(*args, **kwargs)

    def clean(self) -> None:
        super().clean()
        errors = {}
        if self.feedback_id and self.feedback.variant != SelfieSearchFeedback.Variant.RESULT_LABELS:
            errors["feedback"] = "Only result-label feedback accepts result labels."
        if self.feedback_id and self.result_id and self.feedback.search_id != self.result.search_id:
            errors["result"] = "The labelled result must belong to the feedback search."
        if errors:
            raise ValidationError(errors)


class SelfieSearchFeedbackAccessAudit(models.Model):  # noqa: DJ008
    class Action(models.TextChoices):
        CONTACT_VIEW = "contact_view", "Contact view"
        SELFIE_VIEW = "selfie_view", "Selfie view"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    feedback = models.ForeignKey(
        SelfieSearchFeedback,
        on_delete=models.PROTECT,
        related_name="access_audits",
    )
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="selfie_search_feedback_access_audits",
    )
    action = models.CharField(max_length=16, choices=Action)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(action__in=("contact_view", "selfie_view")),
                name="selfie_feedback_audit_action_chk",
            )
        ]

    def __str__(self) -> str:
        return f"Feedback access audit {self.pk}"

    def save(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Selfie search feedback access audits are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        if self.pk and self.__class__.objects.filter(pk=self.pk).exists():
            raise ValidationError("Selfie search feedback access audits are append-only.")
        super().delete(*args, **kwargs)

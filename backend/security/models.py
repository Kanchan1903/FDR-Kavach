import base64
import hashlib
import json
import uuid
from datetime import timedelta

from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import connection, models, transaction
from django.utils import timezone


class UUIDPublicModel(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)

    class Meta:
        abstract = True


class Institution(UUIDPublicModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    registration_number = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    def __str__(self):
        return f"{self.code} — {self.name}"


class EncryptedTextField(models.CharField):
    """Fernet-encrypted database storage for TOTP seeds."""

    @staticmethod
    def _cipher():
        secret = settings.MFA_ENCRYPTION_KEY or settings.SECRET_KEY
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        return Fernet(key)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if not value:
            return value
        return self._cipher().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        return self._cipher().decrypt(value.encode()).decode()


class EncryptedPrivateStorage(FileSystemStorage):
    """Encrypt uploaded evidence and bank batches at rest; decrypt only through authenticated API reads."""

    @staticmethod
    def _cipher():
        secret = settings.DOCUMENT_ENCRYPTION_KEY or settings.SECRET_KEY
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        return Fernet(key)

    def _save(self, name, content):
        plaintext = b"".join(content.chunks())
        encrypted = ContentFile(self._cipher().encrypt(plaintext))
        return super()._save(name, encrypted)

    def _open(self, name, mode="rb"):
        encrypted = super()._open(name, mode)
        try:
            plaintext = self._cipher().decrypt(encrypted.read())
        finally:
            encrypted.close()
        return ContentFile(plaintext, name=name)


class User(AbstractUser):
    class Role(models.TextChoices):
        INSTITUTION_USER = "INSTITUTION_USER", "Institution user"
        REVIEWER = "REVIEWER", "Reviewer"
        APPROVER = "APPROVER", "Approver"
        SENIOR_OFFICER = "SENIOR_OFFICER", "Senior officer"
        ADMIN = "ADMIN", "Administrator"
        AUDITOR = "AUDITOR", "Auditor"

    role = models.CharField(max_length=24, choices=Role.choices, default=Role.INSTITUTION_USER)
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    institution = models.ForeignKey(Institution, null=True, blank=True, on_delete=models.PROTECT, related_name="users")
    totp_secret = EncryptedTextField(max_length=255, blank=True)
    totp_enabled = models.BooleanField(default=False)
    failed_login_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    @property
    def is_locked(self):
        return bool(self.locked_until and self.locked_until > timezone.now())


class AuditEvent(UUIDPublicModel):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_events"
    )
    event_type = models.CharField(max_length=100)
    occurred_at = models.DateTimeField(default=timezone.now, editable=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    payload = models.JSONField(default=dict)
    previous_hash = models.CharField(max_length=64, blank=True)
    event_hash = models.CharField(max_length=64, unique=True, editable=False)

    class Meta:
        ordering = ["id"]

    @transaction.atomic
    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Audit events are append-only")
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [728264265])
        previous = AuditEvent.objects.order_by("-id").values_list("event_hash", flat=True).first() or "0" * 64
        self.previous_hash = previous
        canonical = json.dumps(
            {
                "actor_id": self.actor_id,
                "event_type": self.event_type,
                "occurred_at": self.occurred_at.isoformat(),
                "ip_address": self.ip_address,
                "user_agent": self.user_agent,
                "payload": self.payload,
                "previous_hash": previous,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self.event_hash = hashlib.sha256(canonical.encode()).hexdigest()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Audit events are append-only")


class FDR(UUIDPublicModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        UNDER_REVIEW = "UNDER_REVIEW", "Under review"
        NEEDS_REVERIFICATION = "NEEDS_REVERIFICATION", "Needs re-verification"
        RENEWED = "RENEWED", "Renewed"

    institution = models.ForeignKey(Institution, on_delete=models.PROTECT, related_name="fdrs")
    renewal_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="renewals")
    fdr_number = models.CharField(max_length=100)
    bank_name = models.CharField(max_length=255)
    branch_name = models.CharField(max_length=255, blank=True)
    principal_amount = models.DecimalField(max_digits=16, decimal_places=2)
    issue_date = models.DateField()
    maturity_date = models.DateField()
    purpose = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    reverification_required = models.BooleanField(default=False)
    verified_snapshot = models.JSONField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_fdrs")
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "id"]
        indexes = [models.Index(fields=["fdr_number"]), models.Index(fields=["institution", "status"])]

    def __str__(self):
        return f"{self.fdr_number} — {self.institution.code}"


def document_upload_path(instance, filename):
    suffix = filename.rsplit(".", 1)[-1].lower()[:8]
    return f"fdr-evidence/{instance.fdr_id}/{uuid.uuid4().hex}.{suffix}"


def bank_batch_upload_path(instance, filename):
    return f"bank-batches/{uuid.uuid4().hex}.csv"


def default_case_due_at():
    return timezone.now() + timedelta(days=5)


class FDRDocument(UUIDPublicModel):
    fdr = models.ForeignKey(FDR, on_delete=models.PROTECT, related_name="documents")
    file = models.FileField(upload_to=document_upload_path, storage=EncryptedPrivateStorage(), max_length=512)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, default="application/pdf")
    size_bytes = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fdr_documents")
    uploaded_at = models.DateTimeField(default=timezone.now, editable=False)
    extracted_fields = models.JSONField(default=dict, blank=True)
    extracted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at", "id"]

    def delete(self, *args, **kwargs):
        raise ValueError("Supporting documents are retained for the record history")


class FDRVersion(models.Model):
    fdr = models.ForeignKey(FDR, on_delete=models.PROTECT, related_name="versions")
    version_number = models.PositiveIntegerField()
    snapshot = models.JSONField()
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fdr_versions")
    change_reason = models.CharField(max_length=255, blank=True)
    valid_from = models.DateTimeField(default=timezone.now)
    valid_to = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-version_number"]
        constraints = [models.UniqueConstraint(fields=["fdr", "version_number"], name="uniq_fdr_version_number")]


class FDREvidence(UUIDPublicModel):
    class Kind(models.TextChoices):
        DECLARED = "DECLARED", "Declared"
        DOCUMENT_OBSERVED = "DOCUMENT_OBSERVED", "Document observed"
        BANK_CONFIRMED = "BANK_CONFIRMED", "Bank confirmed"

    fdr = models.ForeignKey(FDR, on_delete=models.PROTECT, related_name="evidence")
    document = models.ForeignKey(FDRDocument, null=True, blank=True, on_delete=models.PROTECT, related_name="evidence")
    evidence_type = models.CharField(max_length=24, choices=Kind.choices)
    field_name = models.CharField(max_length=64)
    value = models.JSONField()
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    corrected_by_user = models.BooleanField(default=False)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fdr_evidence")
    recorded_at = models.DateTimeField(default=timezone.now, editable=False)
    superseded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["field_name", "-recorded_at"]
        indexes = [models.Index(fields=["fdr", "evidence_type", "field_name", "superseded_at"])]


class BankBatch(UUIDPublicModel):
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bank_batches")
    original_filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=bank_batch_upload_path, storage=EncryptedPrivateStorage(), max_length=512)
    sha256 = models.CharField(max_length=64)
    uploaded_at = models.DateTimeField(default=timezone.now, editable=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    row_count = models.PositiveIntegerField(default=0)


class BankBatchRow(models.Model):
    batch = models.ForeignKey(BankBatch, on_delete=models.PROTECT, related_name="rows")
    row_number = models.PositiveIntegerField()
    fdr_number = models.CharField(max_length=100)
    bank_name = models.CharField(max_length=255, blank=True)
    branch_name = models.CharField(max_length=255, blank=True)
    principal_amount = models.DecimalField(max_digits=16, decimal_places=2)
    issue_date = models.DateField(null=True, blank=True)
    maturity_date = models.DateField(null=True, blank=True)
    raw_data = models.JSONField(default=dict)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["batch", "row_number"], name="uniq_batch_row_number")]


class ReconciliationResult(UUIDPublicModel):
    class Classification(models.TextChoices):
        MATCHED = "MATCHED", "Matched"
        AMOUNT_MISMATCH = "AMOUNT_MISMATCH", "Amount mismatch"
        DATE_MISMATCH = "DATE_MISMATCH", "Date mismatch"
        MISSING_IN_BANK_BATCH = "MISSING_IN_BANK_BATCH", "Missing in bank batch"
        UNMAPPED_IN_PORTAL = "UNMAPPED_IN_PORTAL", "Unmapped in portal"
        AMBIGUOUS = "AMBIGUOUS", "Ambiguous"

    batch = models.ForeignKey(BankBatch, on_delete=models.PROTECT, related_name="results")
    bank_row = models.ForeignKey(BankBatchRow, null=True, blank=True, on_delete=models.PROTECT, related_name="results")
    fdr = models.ForeignKey(FDR, null=True, blank=True, on_delete=models.PROTECT, related_name="reconciliation_results")
    classification = models.CharField(max_length=32, choices=Classification.choices)
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["classification", "id"]


class FDRCase(UUIDPublicModel):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        CLOSED = "CLOSED", "Closed"

    class Severity(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    case_type = models.CharField(max_length=48)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    fdr = models.ForeignKey(FDR, null=True, blank=True, on_delete=models.PROTECT, related_name="cases")
    reconciliation_result = models.ForeignKey(
        ReconciliationResult, null=True, blank=True, on_delete=models.PROTECT, related_name="cases"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="owned_fdr_cases"
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="opened_fdr_cases")
    severity = models.CharField(max_length=12, choices=Severity.choices, default=Severity.MEDIUM)
    due_at = models.DateTimeField(default=default_case_due_at)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    closure_evidence = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    escalation_count = models.PositiveSmallIntegerField(default=0)
    last_escalated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["due_at", "-created_at"]
        indexes = [models.Index(fields=["status", "due_at"])]


class CaseEscalationPolicy(models.Model):
    severity = models.CharField(max_length=12, choices=FDRCase.Severity.choices, unique=True)
    after_minutes = models.PositiveIntegerField(default=1440)
    escalate_to_role = models.CharField(max_length=24, choices=User.Role.choices, default=User.Role.SENIOR_OFFICER)
    enabled = models.BooleanField(default=True)


class CaseAlert(UUIDPublicModel):
    class Kind(models.TextChoices):
        DUE_REMINDER = "DUE_REMINDER", "Due reminder"
        OVERDUE_ESCALATION = "OVERDUE_ESCALATION", "Overdue escalation"

    case = models.ForeignKey(FDRCase, on_delete=models.PROTECT, related_name="alerts")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="fdr_case_alerts")
    kind = models.CharField(max_length=24, choices=Kind.choices)
    message = models.CharField(max_length=500)
    dedupe_key = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    read_at = models.DateTimeField(null=True, blank=True)


class CoverageRuleSet(UUIDPublicModel):
    name = models.CharField(max_length=120, unique=True)
    required_amount = models.DecimalField(max_digits=16, decimal_places=2)
    source = models.CharField(max_length=500, blank=True, default="")
    effective_from = models.DateField(null=True, blank=True)
    approving_authority = models.CharField(max_length=255, blank=True, default="")
    shortfall_tolerance = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    active = models.BooleanField(default=False)
    checklist_items = models.JSONField(default=list)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-active", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["active"], condition=models.Q(active=True), name="uniq_active_coverage_rules"
            )
        ]

    def clean(self):
        super().clean()
        if self.active and (not self.source.strip() or not self.effective_from or not self.approving_authority.strip()):
            from django.core.exceptions import ValidationError

            raise ValidationError("Active rule sets require a source, effective date, and approving authority.")
        allowed = {"matured", "bank_confirmed", "no_open_cases", "verified", "supporting_document", "coverage"}
        if (
            not self.checklist_items
            or not isinstance(self.checklist_items, list)
            or set(self.checklist_items) - allowed
        ):
            from django.core.exceptions import ValidationError

            raise ValidationError({"checklist_items": "Configure at least one supported readiness item."})


class CoverageDay(models.Model):
    institution = models.ForeignKey(Institution, on_delete=models.PROTECT, related_name="coverage_days")
    rule_set = models.ForeignKey(CoverageRuleSet, on_delete=models.PROTECT, related_name="coverage_days")
    day = models.DateField()
    required_amount = models.DecimalField(max_digits=16, decimal_places=2)
    submitted_amount = models.DecimalField(max_digits=16, decimal_places=2)
    bank_confirmed_amount = models.DecimalField(max_digits=16, decimal_places=2)
    shortfall_amount = models.DecimalField(max_digits=16, decimal_places=2)
    calculated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["institution", "rule_set", "day"], name="uniq_coverage_day")]
        indexes = [models.Index(fields=["institution", "day"])]


class ReleaseRequest(UUIDPublicModel):
    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        UNDER_SCRUTINY = "UNDER_SCRUTINY", "Under scrutiny"
        APPROVED = "APPROVED_FOR_RELEASE", "Approved for release"
        DISCHARGED = "RELEASE_RECORDED", "Release/discharge recorded"
        CLOSED = "CLOSED", "Closed"
        INVALIDATED = "INVALIDATED", "Invalidated after material change"

    fdr = models.ForeignKey(FDR, on_delete=models.PROTECT, related_name="release_requests")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="release_requests_made"
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="release_reviews"
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="release_approvals"
    )
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.REQUESTED)
    checklist = models.JSONField(default=dict)
    material_signature = models.CharField(max_length=64)
    review_note = models.TextField(blank=True)
    discharge_evidence = models.TextField(blank=True)
    requested_at = models.DateTimeField(default=timezone.now)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]


class ImportJob(UUIDPublicModel):
    class Status(models.TextChoices):
        DRY_RUN = "DRY_RUN", "Dry run"
        IMPORTED = "IMPORTED", "Imported"
        INVALID = "INVALID", "Invalid"

    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    filename = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices)
    dry_run = models.BooleanField(default=True)
    validation_report = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

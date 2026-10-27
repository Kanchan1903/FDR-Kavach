import hashlib
import json
from datetime import date

from django.db import transaction
from django.utils import timezone

from .models import FDR, FDRCase, FDREvidence, FDRVersion, User

STAFF_ROLES = {
    User.Role.REVIEWER,
    User.Role.APPROVER,
    User.Role.SENIOR_OFFICER,
    User.Role.ADMIN,
    User.Role.AUDITOR,
}
MATERIAL_FIELDS = (
    "fdr_number",
    "bank_name",
    "branch_name",
    "principal_amount",
    "issue_date",
    "maturity_date",
    "purpose",
)


def user_can_manage_all_records(user):
    return user.is_authenticated and user.role in {User.Role.ADMIN, User.Role.SENIOR_OFFICER}


def scoped_fdrs(user):
    if not user.is_authenticated:
        return FDR.objects.none()
    if user.role == User.Role.INSTITUTION_USER:
        return FDR.objects.filter(institution_id=user.institution_id) if user.institution_id else FDR.objects.none()
    if user.role in STAFF_ROLES:
        return FDR.objects.all()
    return FDR.objects.none()


def snapshot_fdr(fdr):
    values = {field: getattr(fdr, field) for field in MATERIAL_FIELDS}
    values.update(
        {
            "institution_id": fdr.institution_id,
            "renewal_of_id": fdr.renewal_of_id,
            "status": fdr.status,
            "reverification_required": fdr.reverification_required,
            "documents": list(fdr.documents.order_by("id").values_list("sha256", flat=True)),
            "captured_at": timezone.now().isoformat(),
        }
    )
    for key, value in values.items():
        if isinstance(value, date):
            values[key] = value.isoformat()
        elif hasattr(value, "as_tuple"):
            values[key] = str(value)
    return values


@transaction.atomic
def create_version(fdr, actor, reason=""):
    current = fdr.versions.filter(valid_to__isnull=True).first()
    if current:
        current.valid_to = timezone.now()
        current.save(update_fields=["valid_to"])
    number = (fdr.versions.order_by("-version_number").values_list("version_number", flat=True).first() or 0) + 1
    return FDRVersion.objects.create(
        fdr=fdr,
        version_number=number,
        snapshot=snapshot_fdr(fdr),
        changed_by=actor,
        change_reason=reason,
    )


@transaction.atomic
def set_evidence(*, fdr, actor, evidence_type, field_name, value, document=None, confidence=None, corrected=False):
    FDREvidence.objects.filter(
        fdr=fdr,
        evidence_type=evidence_type,
        field_name=field_name,
        superseded_at__isnull=True,
    ).update(superseded_at=timezone.now())
    return FDREvidence.objects.create(
        fdr=fdr,
        document=document,
        evidence_type=evidence_type,
        field_name=field_name,
        value=value,
        confidence=confidence,
        corrected_by_user=corrected,
        recorded_by=actor,
    )


def declared_values(fdr):
    return {
        "fdr_number": fdr.fdr_number,
        "bank_name": fdr.bank_name,
        "branch_name": fdr.branch_name,
        "principal_amount": str(fdr.principal_amount),
        "issue_date": fdr.issue_date.isoformat(),
        "maturity_date": fdr.maturity_date.isoformat(),
        "purpose": fdr.purpose,
    }


def default_case_owner(actor):
    staff_owner = (
        User.objects.filter(
            is_active=True,
            role__in=[User.Role.SENIOR_OFFICER, User.Role.ADMIN],
        )
        .order_by("role", "id")
        .first()
    )
    if staff_owner:
        return staff_owner
    return actor


def make_case(*, actor, case_type, title, description="", fdr=None, result=None, severity=FDRCase.Severity.MEDIUM):
    owner = default_case_owner(actor)
    return FDRCase.objects.create(
        case_type=case_type,
        title=title[:255],
        description=description,
        fdr=fdr,
        reconciliation_result=result,
        owner=owner,
        created_by=actor,
        severity=severity,
    )


def can_write_fdr(user, fdr):
    if user_can_manage_all_records(user):
        return True
    return user.role == User.Role.INSTITUTION_USER and user.institution_id == fdr.institution_id


def release_signature(fdr):
    data = {key: str(getattr(fdr, key)) for key in MATERIAL_FIELDS}
    data["documents"] = list(fdr.documents.order_by("id").values_list("sha256", flat=True))
    data["status"] = fdr.status
    data["renewal_of_id"] = fdr.renewal_of_id
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def invalidate_release_requests(fdr):
    from .models import ReleaseRequest

    ReleaseRequest.objects.filter(
        fdr=fdr,
        status__in=[
            ReleaseRequest.Status.REQUESTED,
            ReleaseRequest.Status.UNDER_SCRUTINY,
            ReleaseRequest.Status.APPROVED,
        ],
    ).exclude(material_signature=release_signature(fdr)).update(status=ReleaseRequest.Status.INVALIDATED)


def visible_cases(user):
    if user.role == User.Role.INSTITUTION_USER:
        return (
            FDRCase.objects.filter(fdr__institution_id=user.institution_id)
            if user.institution_id
            else FDRCase.objects.none()
        )
    if user.role in STAFF_ROLES:
        return FDRCase.objects.all()
    return FDRCase.objects.none()

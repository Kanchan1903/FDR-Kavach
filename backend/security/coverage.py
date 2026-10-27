from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from .models import FDR, CoverageDay, CoverageRuleSet, FDREvidence


def active_rule_set():
    return CoverageRuleSet.objects.filter(active=True).first()


def calculate_day(institution, day, rule_set=None, *, persist=True):
    rule_set = rule_set or active_rule_set()
    if not rule_set:
        raise ValueError("No active coverage rule set is configured.")
    records = FDR.objects.filter(
        institution=institution,
        status__in=[FDR.Status.SUBMITTED, FDR.Status.UNDER_REVIEW],
        issue_date__lte=day,
        maturity_date__gte=day,
    )
    submitted = sum(records.values_list("principal_amount", flat=True), Decimal("0.00"))
    bank_confirmed = Decimal("0.00")
    for fdr in records:
        evidence = (
            fdr.evidence.filter(
                evidence_type=FDREvidence.Kind.BANK_CONFIRMED, field_name="principal_amount", superseded_at__isnull=True
            )
            .order_by("-recorded_at")
            .first()
        )
        if evidence:
            try:
                bank_confirmed += Decimal(str(evidence.value))
            except (InvalidOperation, TypeError, ValueError):
                continue
    available = min(submitted, bank_confirmed)
    shortfall = max(Decimal("0.00"), rule_set.required_amount - available)
    values = {
        "institution": institution,
        "rule_set": rule_set,
        "day": day,
        "required_amount": rule_set.required_amount,
        "submitted_amount": submitted,
        "bank_confirmed_amount": bank_confirmed,
        "shortfall_amount": shortfall,
        "calculated_at": timezone.now(),
    }
    if not persist:
        return CoverageDay(**values)
    return CoverageDay.objects.update_or_create(
        institution=institution, rule_set=rule_set, day=day, defaults=values
    )[0]


@transaction.atomic
def calculate_range(institution, start, end, rule_set=None, *, persist=True):
    if end < start or (end - start).days > 366:
        raise ValueError("Coverage range must be ordered and no longer than 367 days.")
    rule_set = rule_set or active_rule_set()
    if not rule_set:
        raise ValueError("No active coverage rule set is configured.")
    rows = []
    cursor = start
    while cursor <= end:
        rows.append(calculate_day(institution, cursor, rule_set, persist=persist))
        cursor += timedelta(days=1)
    return rows

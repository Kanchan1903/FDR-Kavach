from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .audit import record_event
from .models import CaseAlert, CaseEscalationPolicy, CoverageRuleSet, FDRCase, Institution, User


@shared_task(name="security.tasks.dispatch_case_alerts")
def dispatch_case_alerts():
    now = timezone.now()
    reminder_cutoff = now + timedelta(hours=24)
    sent = {"reminders": 0, "escalations": 0}
    due_cases = FDRCase.objects.filter(
        status__in=[FDRCase.Status.OPEN, FDRCase.Status.IN_PROGRESS],
        due_at__gt=now,
        due_at__lte=reminder_cutoff,
        owner__isnull=False,
    ).select_related("owner")
    for case in due_cases.iterator():
        key = f"due:{case.pk}:{case.due_at.isoformat()}:{case.owner_id}"
        _, created = CaseAlert.objects.get_or_create(
            dedupe_key=key,
            defaults={
                "case": case,
                "recipient": case.owner,
                "kind": CaseAlert.Kind.DUE_REMINDER,
                "message": f"Case {case.pk} is due within 24 hours: {case.title}",
            },
        )
        sent["reminders"] += int(created)

    policies = CaseEscalationPolicy.objects.filter(enabled=True)
    for case in FDRCase.objects.filter(
        status__in=[FDRCase.Status.OPEN, FDRCase.Status.IN_PROGRESS], due_at__lte=now
    ).iterator():
        for policy in policies:
            if case.severity != policy.severity or case.due_at + timedelta(minutes=policy.after_minutes) > now:
                continue
            recipient = User.objects.filter(is_active=True, role=policy.escalate_to_role).order_by("id").first()
            if not recipient:
                recipient = User.objects.filter(is_active=True, role=User.Role.ADMIN).order_by("id").first()
            if not recipient:
                continue
            key = f"escalation:{case.pk}:{policy.pk}:{case.due_at.isoformat()}"
            with transaction.atomic():
                _, created = CaseAlert.objects.get_or_create(
                    dedupe_key=key,
                    defaults={
                        "case": case,
                        "recipient": recipient,
                        "kind": CaseAlert.Kind.OVERDUE_ESCALATION,
                        "message": f"Overdue case {case.pk} escalated to {recipient.role}: {case.title}",
                    },
                )
                if created:
                    case.owner = recipient
                    case.escalation_count += 1
                    case.last_escalated_at = now
                    case.save(update_fields=["owner", "escalation_count", "last_escalated_at", "updated_at"])
                    record_event(
                        actor=None,
                        event_type="case.escalated",
                        payload={"case_id": case.pk, "policy_id": policy.pk, "recipient_id": recipient.pk},
                    )
                    sent["escalations"] += 1
    return sent


@shared_task(name="security.tasks.calculate_daily_coverage")
def calculate_daily_coverage():
    from .coverage import calculate_day

    rule_set = CoverageRuleSet.objects.filter(active=True).first()
    if not rule_set:
        return {"calculated": 0, "reason": "no_active_rule_set"}
    count = 0
    day = timezone.localdate()
    for institution in Institution.objects.filter(is_active=True).iterator():
        calculate_day(institution, day, rule_set)
        count += 1
    return {"calculated": count, "day": day.isoformat(), "rule_set_id": rule_set.pk}

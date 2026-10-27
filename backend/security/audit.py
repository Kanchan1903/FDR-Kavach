import hashlib
import json

from django.db import transaction

from .models import AuditEvent


@transaction.atomic
def record_event(*, actor, event_type, request=None, payload=None):
    return AuditEvent.objects.create(
        actor=actor,
        event_type=event_type,
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:512] if request else "",
        payload=payload or {},
    )


def verify_chain():
    previous = "0" * 64
    checked = 0
    for row in AuditEvent.objects.order_by("id").iterator():
        if row.previous_hash != previous:
            return {"valid": False, "checked": checked, "failed_event_id": str(row.public_id)}
        canonical = json.dumps(
            {
                "actor_id": row.actor_id,
                "event_type": row.event_type,
                "occurred_at": row.occurred_at.isoformat(),
                "ip_address": row.ip_address,
                "user_agent": row.user_agent,
                "payload": row.payload,
                "previous_hash": previous,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        actual = hashlib.sha256(canonical.encode()).hexdigest()
        if actual != row.event_hash:
            return {"valid": False, "checked": checked, "failed_event_id": str(row.public_id)}
        previous, checked = actual, checked + 1
    return {"valid": True, "checked": checked, "head_hash": previous}

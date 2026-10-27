from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import record_event
from .models import CaseAlert, FDRCase, User
from .record_services import STAFF_ROLES, user_can_manage_all_records, visible_cases
from .serializers import FDRCaseUpdateSerializer


def case_data(case, user):
    return {
        "id": str(case.public_id),
        "case_type": case.case_type,
        "title": case.title,
        "description": case.description,
        "fdr_id": str(case.fdr.public_id) if case.fdr_id else None,
        "fdr_number": case.fdr.fdr_number if case.fdr_id else None,
        "reconciliation_result_id": str(case.reconciliation_result.public_id)
        if case.reconciliation_result_id
        else None,
        "owner": {"id": str(case.owner.public_id), "username": case.owner.username} if case.owner_id else None,
        "severity": case.severity,
        "due_at": case.due_at.isoformat(),
        "status": case.status,
        "closure_evidence": case.closure_evidence,
        "escalation_count": case.escalation_count,
        "can_update": can_update_case(user, case),
        "created_at": case.created_at.isoformat(),
    }


def can_update_case(user, case):
    return user_can_manage_all_records(user) or case.owner_id == user.pk


class CaseListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = visible_cases(request.user).select_related("fdr", "owner", "reconciliation_result")
        return Response([case_data(case, request.user) for case in rows])


class CaseDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def patch(self, request, pk):
        case = get_object_or_404(visible_cases(request.user), public_id=pk)
        if not can_update_case(request.user, case):
            return Response({"detail": "Only the case owner or a senior officer can update this case."}, status=403)
        data = request.data.copy()
        if "owner_id" in data:
            if not user_can_manage_all_records(request.user):
                return Response({"detail": "Only an administrator or senior officer can reassign cases."}, status=403)
            target_id = data.get("owner_id")
            if target_id:
                target = User.objects.filter(public_id=target_id, is_active=True).first()
                if not target or target.role not in STAFF_ROLES:
                    return Response({"detail": "Case owner must be an active staff user."}, status=400)
                data["owner_id"] = str(target.public_id)
        serializer = FDRCaseUpdateSerializer(case, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        old_status = case.status
        case = serializer.save()
        if case.status == FDRCase.Status.CLOSED and old_status != FDRCase.Status.CLOSED:
            case.closed_at = timezone.now()
            case.save(update_fields=["closed_at", "updated_at"])
        elif case.status != FDRCase.Status.CLOSED and old_status == FDRCase.Status.CLOSED:
            case.closed_at = None
            case.save(update_fields=["closed_at", "updated_at"])
        record_event(
            actor=request.user,
            event_type="case.updated",
            request=request,
            payload={"case_id": case.pk, "status": case.status, "owner_id": case.owner_id},
        )
        return Response(case_data(case, request.user))


class CaseAlertListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        alerts = CaseAlert.objects.filter(recipient=request.user).select_related("case").order_by("-created_at")[:200]
        return Response(
            [
                {
                    "id": str(alert.public_id),
                    "case_id": str(alert.case.public_id),
                    "kind": alert.kind,
                    "message": alert.message,
                    "created_at": alert.created_at.isoformat(),
                    "read": alert.read_at is not None,
                }
                for alert in alerts
            ]
        )


class CaseAlertReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        alert = get_object_or_404(CaseAlert, public_id=pk, recipient=request.user)
        if not alert.read_at:
            alert.read_at = timezone.now()
            alert.save(update_fields=["read_at"])
        return Response({"id": str(alert.public_id), "read": True})


class StaffDirectoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not user_can_manage_all_records(request.user):
            return Response({"detail": "Not authorized."}, status=403)
        staff = User.objects.filter(is_active=True, role__in=STAFF_ROLES).order_by("role", "username")
        return Response([{"id": str(user.public_id), "username": user.username, "role": user.role} for user in staff])

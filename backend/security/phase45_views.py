import csv
import hashlib
import io
import zipfile
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Avg, DurationField, ExpressionWrapper, F
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import record_event
from .coverage import active_rule_set, calculate_range
from .models import (
    FDR,
    CoverageRuleSet,
    FDRCase,
    FDREvidence,
    ImportJob,
    Institution,
    ReleaseRequest,
    User,
)
from .record_services import can_write_fdr, release_signature, scoped_fdrs
from .serializers import FDRInputSerializer

STAFF = {User.Role.REVIEWER, User.Role.APPROVER, User.Role.SENIOR_OFFICER, User.Role.ADMIN, User.Role.AUDITOR}
MANAGERS = {User.Role.ADMIN, User.Role.SENIOR_OFFICER}


def coverage_data(row):
    return {
        "date": row.day.isoformat(),
        "required": str(row.required_amount),
        "submitted": str(row.submitted_amount),
        "bank_confirmed": str(row.bank_confirmed_amount),
        "shortfall": str(row.shortfall_amount),
        "risk": row.shortfall_amount > row.rule_set.shortfall_tolerance,
    }


def release_data(row, user):
    return {
        "id": str(row.public_id),
        "fdr_id": str(row.fdr.public_id),
        "fdr_number": row.fdr.fdr_number,
        "status": row.status,
        "requested_by": row.requested_by.username,
        "reviewer": row.reviewer.username if row.reviewer else None,
        "approver": row.approver.username if row.approver else None,
        "checklist": row.checklist,
        "review_note": row.review_note,
        "discharge_evidence": row.discharge_evidence,
        "requested_at": row.requested_at.isoformat(),
        "can_act": row.fdr.institution_id == user.institution_id
        if user.role == User.Role.INSTITUTION_USER
        else user.role in STAFF,
    }


def visible_release_requests(user):
    rows = ReleaseRequest.objects.all()
    if user.role == User.Role.INSTITUTION_USER:
        return rows.filter(fdr__institution_id=user.institution_id) if user.institution_id else rows.none()
    return rows if user.role in STAFF else rows.none()


def readiness(fdr, rule):
    latest = FDREvidence.objects.filter(
        fdr=fdr,
        evidence_type=FDREvidence.Kind.BANK_CONFIRMED,
        field_name="principal_amount",
        superseded_at__isnull=True,
    ).exists()
    coverage_passed = False
    if rule:
        from .coverage import calculate_day

        coverage_passed = (
            calculate_day(fdr.institution, timezone.localdate(), rule).shortfall_amount <= rule.shortfall_tolerance
        )
    items = {
        "matured": fdr.maturity_date <= timezone.localdate(),
        "bank_confirmed": latest,
        "no_open_cases": not fdr.cases.exclude(status=FDRCase.Status.CLOSED).exists(),
        "verified": not fdr.reverification_required and fdr.verified_at is not None,
        "supporting_document": fdr.documents.exists(),
        "coverage": coverage_passed,
    }
    configured = rule.checklist_items if rule else list(items)
    return {key: {"passed": items.get(key, False), "required": True} for key in configured if key in items}


class CoverageRuleSetView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in STAFF:
            return Response({"detail": "Not authorized."}, status=403)
        rows = CoverageRuleSet.objects.all()
        return Response(
            [
                {
                    "id": str(x.public_id),
                    "name": x.name,
                    "required_amount": str(x.required_amount),
                    "source": x.source,
                    "effective_from": x.effective_from.isoformat() if x.effective_from else None,
                    "approving_authority": x.approving_authority,
                    "shortfall_tolerance": str(x.shortfall_tolerance),
                    "active": x.active,
                    "checklist_items": x.checklist_items,
                }
                for x in rows
            ]
        )

    def post(self, request):
        if request.user.role != User.Role.ADMIN:
            return Response({"detail": "Only administrators can configure coverage rules."}, status=403)
        try:
            amount = Decimal(str(request.data.get("required_amount")))
            tolerance = Decimal(str(request.data.get("shortfall_tolerance", "0")))
            if amount <= 0 or tolerance < 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError):
            return Response({"detail": "Provide a positive required_amount and non-negative tolerance."}, status=400)
        source = str(request.data.get("source", "")).strip()[:500]
        approving_authority = str(request.data.get("approving_authority", "")).strip()[:255]
        try:
            effective_from = date.fromisoformat(str(request.data.get("effective_from", "")))
        except ValueError:
            effective_from = None
        if not source or not approving_authority or not effective_from:
            return Response({"detail": "Provide source, effective_from, and approving_authority."}, status=400)
        items = request.data.get(
            "checklist_items",
            ["matured", "bank_confirmed", "no_open_cases", "verified", "supporting_document", "coverage"],
        )
        allowed = {"matured", "bank_confirmed", "no_open_cases", "verified", "supporting_document", "coverage"}
        if not isinstance(items, list) or set(items) - allowed:
            return Response({"detail": "Checklist contains unsupported items."}, status=400)
        with transaction.atomic():
            if request.data.get("active"):
                CoverageRuleSet.objects.filter(active=True).update(active=False)
            rule = CoverageRuleSet.objects.create(
                name=str(request.data.get("name", "Active coverage rules"))[:120],
                required_amount=amount,
                source=source,
                effective_from=effective_from,
                approving_authority=approving_authority,
                shortfall_tolerance=tolerance,
                active=bool(request.data.get("active", True)),
                checklist_items=items,
                updated_by=request.user,
            )
        record_event(
            actor=request.user,
            event_type="coverage.rule_set_configured",
            request=request,
            payload={"rule_set_id": rule.pk},
        )
        return Response({"id": str(rule.public_id), "active": rule.active}, status=201)


class CoverageTimelineView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        institution_id = request.query_params.get("institution_id")
        if request.user.role == User.Role.INSTITUTION_USER:
            institution_id = request.user.institution.public_id if request.user.institution_id else None
        elif request.user.role not in STAFF:
            return Response({"detail": "Not authorized."}, status=403)
        institution = get_object_or_404(Institution, public_id=institution_id, is_active=True)
        try:
            start = date.fromisoformat(request.query_params.get("start", date.today().isoformat()))
            end = date.fromisoformat(request.query_params.get("end", (date.today().replace(day=1)).isoformat()))
            if end < start:
                start, end = end, start
            rows = calculate_range(institution, start, end, persist=False)
        except (ValueError, TypeError) as exc:
            return Response({"detail": str(exc)}, status=400)
        rule = active_rule_set()
        return Response(
            {"institution": institution.code, "rule_set": rule.name, "days": [coverage_data(x) for x in rows]}
        )


class ReleaseRequestListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in STAFF and request.user.role != User.Role.INSTITUTION_USER:
            return Response({"detail": "Not authorized."}, status=403)
        rows = visible_release_requests(request.user).select_related("fdr", "requested_by", "reviewer", "approver")
        return Response([release_data(x, request.user) for x in rows[:500]])

    @transaction.atomic
    def post(self, request):
        fdr = get_object_or_404(scoped_fdrs(request.user), public_id=request.data.get("fdr_id"))
        if not can_write_fdr(request.user, fdr):
            return Response(
                {"detail": "Only the institution or authorized record manager can request release."}, status=403
            )
        if not active_rule_set():
            return Response(
                {"detail": "An active coverage Rule Set must be configured before requesting release."}, status=409
            )
        if ReleaseRequest.objects.filter(
            fdr=fdr,
            status__in=[
                ReleaseRequest.Status.REQUESTED,
                ReleaseRequest.Status.UNDER_SCRUTINY,
                ReleaseRequest.Status.APPROVED,
            ],
        ).exists():
            return Response({"detail": "An active release request already exists."}, status=409)
        rule = active_rule_set()
        checks = readiness(fdr, rule)
        record = ReleaseRequest.objects.create(
            fdr=fdr, requested_by=request.user, checklist=checks, material_signature=release_signature(fdr)
        )
        record_event(
            actor=request.user,
            event_type="release.requested",
            request=request,
            payload={"release_request_id": record.pk, "fdr_id": fdr.pk},
        )
        return Response(release_data(record, request.user), status=201)


class ReleaseRequestActionView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        row = get_object_or_404(
            visible_release_requests(request.user).select_related("fdr", "requested_by", "reviewer"), public_id=pk
        )
        action = request.data.get("action")
        if row.material_signature != release_signature(row.fdr):
            row.status = ReleaseRequest.Status.INVALIDATED
            row.save(update_fields=["status", "updated_at"])
            return Response({"detail": "Material record changes invalidated this release approval."}, status=409)
        if action == "review":
            if (
                request.user.role not in {User.Role.REVIEWER, User.Role.SENIOR_OFFICER, User.Role.ADMIN}
                or row.status != ReleaseRequest.Status.REQUESTED
            ):
                return Response({"detail": "Not authorized for this transition."}, status=403)
            if row.requested_by_id == request.user.id:
                return Response({"detail": "Requester cannot review their own release request."}, status=400)
            row.reviewer = request.user
            row.review_note = str(request.data.get("note", ""))[:4000]
            row.reviewed_at = timezone.now()
            row.status = ReleaseRequest.Status.UNDER_SCRUTINY
        elif action == "approve":
            if (
                request.user.role not in {User.Role.APPROVER, User.Role.SENIOR_OFFICER, User.Role.ADMIN}
                or row.status != ReleaseRequest.Status.UNDER_SCRUTINY
            ):
                return Response({"detail": "Not authorized for this transition."}, status=403)
            if row.requested_by_id == request.user.id or row.reviewer_id == request.user.id:
                return Response({"detail": "Approver must differ from both requester and reviewer."}, status=400)
            checks = readiness(row.fdr, active_rule_set())
            row.checklist = checks
            if not all(item["passed"] for item in checks.values()):
                return Response(
                    {"detail": "Release readiness checklist is incomplete.", "checklist": checks}, status=409
                )
            row.approver = request.user
            row.approved_at = timezone.now()
            row.status = ReleaseRequest.Status.APPROVED
        elif action == "discharge":
            if request.user.role not in MANAGERS or row.status != ReleaseRequest.Status.APPROVED:
                return Response({"detail": "Only a senior officer or administrator can record discharge."}, status=403)
            evidence = str(request.data.get("evidence", "")).strip()
            if len(evidence) < 10:
                return Response({"detail": "Discharge evidence must be at least 10 characters."}, status=400)
            checks = readiness(row.fdr, active_rule_set())
            if not all(item["passed"] for item in checks.values()):
                return Response(
                    {"detail": "Release readiness is no longer satisfied.", "checklist": checks}, status=409
                )
            row.discharge_evidence = evidence[:4000]
            row.released_at = timezone.now()
            row.status = ReleaseRequest.Status.DISCHARGED
        elif action == "close":
            if request.user.role not in MANAGERS or row.status != ReleaseRequest.Status.DISCHARGED:
                return Response(
                    {"detail": "Only a senior officer or administrator can close a discharged request."}, status=403
                )
            row.closed_at = timezone.now()
            row.status = ReleaseRequest.Status.CLOSED
        else:
            return Response({"detail": "Unsupported release action."}, status=400)
        row.save()
        record_event(
            actor=request.user,
            event_type=f"release.{action}",
            request=request,
            payload={"release_request_id": row.pk, "fdr_id": row.fdr_id},
        )
        return Response(release_data(row, request.user))


class FDRHistoryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        fdr = get_object_or_404(scoped_fdrs(request.user), public_id=pk)
        valid_on = request.query_params.get("valid_on")
        known_on = request.query_params.get("known_on")
        rows = fdr.versions.all()
        try:
            if valid_on:
                value = timezone.datetime.fromisoformat(valid_on)
                rows = rows.filter(valid_from__lte=value).filter(models_q_valid_to(value))
            if known_on:
                value = timezone.datetime.fromisoformat(known_on)
                rows = rows.filter(created_at__lte=value)
        except ValueError:
            return Response({"detail": "Dates must be ISO-8601 timestamps."}, status=400)
        return Response(
            [
                {
                    "version": x.version_number,
                    "snapshot": x.snapshot,
                    "valid_from": x.valid_from,
                    "valid_to": x.valid_to,
                    "known_at": x.created_at,
                }
                for x in rows
            ]
        )


def models_q_valid_to(value):
    from django.db.models import Q

    return Q(valid_to__isnull=True) | Q(valid_to__gt=value)


class DashboardView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        fdrs = scoped_fdrs(request.user)
        today = timezone.localdate()
        total = fdrs.count()
        rule = active_rule_set()
        coverage_rows = []
        if rule:
            from .coverage import calculate_day

            institution_ids = fdrs.values_list("institution_id", flat=True).distinct()
            for institution in Institution.objects.filter(pk__in=institution_ids):
                coverage_rows.append(calculate_day(institution, today, rule, persist=False))
        coverage_gaps = sum(row.shortfall_amount > row.rule_set.shortfall_tolerance for row in coverage_rows)
        turnaround = fdrs.filter(verified_at__isnull=False).aggregate(
            average=Avg(ExpressionWrapper(F("verified_at") - F("created_at"), output_field=DurationField()))
        )["average"]
        data = {
            "active": fdrs.filter(maturity_date__gt=today).count(),
            "expiring_30_days": fdrs.filter(
                maturity_date__gte=today, maturity_date__lte=today + timedelta(days=30)
            ).count(),
            "matured": fdrs.filter(maturity_date__lt=today).count(),
            "renewed": fdrs.filter(renewal_of__isnull=False).count(),
            "pending": fdrs.filter(
                status__in=[FDR.Status.DRAFT, FDR.Status.SUBMITTED, FDR.Status.UNDER_REVIEW]
            ).count(),
            "pending_verification": fdrs.filter(reverification_required=True).count(),
            "released": ReleaseRequest.objects.filter(fdr__in=fdrs, status=ReleaseRequest.Status.CLOSED).count(),
            "release_recorded": ReleaseRequest.objects.filter(
                fdr__in=fdrs, status=ReleaseRequest.Status.DISCHARGED
            ).count(),
            "pending_release": ReleaseRequest.objects.filter(
                fdr__in=fdrs,
                status__in=[
                    ReleaseRequest.Status.REQUESTED,
                    ReleaseRequest.Status.UNDER_SCRUTINY,
                    ReleaseRequest.Status.APPROVED,
                ],
            ).count(),
            "open_cases": FDRCase.objects.filter(fdr__in=fdrs).exclude(status=FDRCase.Status.CLOSED).count(),
            "total": total,
            "coverage_gaps": coverage_gaps,
            "coverage_shortfall_amount": str(sum((row.shortfall_amount for row in coverage_rows), Decimal("0.00"))),
            "verification_turnaround_avg_hours": round(turnaround.total_seconds() / 3600, 2) if turnaround else None,
            "outstanding_risks": coverage_gaps
            + FDRCase.objects.filter(fdr__in=fdrs, severity__in=[FDRCase.Severity.HIGH, FDRCase.Severity.CRITICAL])
            .exclude(status=FDRCase.Status.CLOSED)
            .count()
            + fdrs.filter(reverification_required=True).count(),
            "prioritization": [
                {"rule": "overdue case", "priority": 1},
                {"rule": "coverage shortfall", "priority": 2},
                {"rule": "maturity within 30 days", "priority": 3},
                {"rule": "re-verification required", "priority": 4},
            ],
        }
        data["evidence_levels"] = {
            kind: FDREvidence.objects.filter(fdr__in=fdrs, evidence_type=kind, superseded_at__isnull=True).count()
            for kind, _ in FDREvidence.Kind.choices
        }
        data["sla_breaches"] = (
            FDRCase.objects.filter(fdr__in=fdrs, due_at__lt=timezone.now())
            .exclude(status=FDRCase.Status.CLOSED)
            .count()
        )
        data["case_severity"] = {
            severity: FDRCase.objects.filter(fdr__in=fdrs, severity=severity)
            .exclude(status=FDRCase.Status.CLOSED)
            .count()
            for severity, _ in FDRCase.Severity.choices
        }
        priorities = []
        for case in FDRCase.objects.filter(fdr__in=fdrs).exclude(status=FDRCase.Status.CLOSED):
            if case.due_at < timezone.now():
                priorities.append(
                    {
                        "priority": 1,
                        "type": "CASE",
                        "id": str(case.public_id),
                        "title": case.title,
                        "reason": "Overdue case",
                    }
                )
        for row in coverage_rows:
            if row.shortfall_amount > row.rule_set.shortfall_tolerance:
                priorities.append(
                    {
                        "priority": 2,
                        "type": "COVERAGE",
                        "institution": row.institution.code,
                        "title": f"{row.day.isoformat()} coverage shortfall",
                        "reason": str(row.shortfall_amount),
                    }
                )
        for record in fdrs:
            if today <= record.maturity_date <= today + timedelta(days=30):
                priorities.append(
                    {
                        "priority": 3,
                        "type": "FDR",
                        "id": str(record.public_id),
                        "title": record.fdr_number,
                        "reason": "Maturity within 30 days",
                    }
                )
            if record.reverification_required:
                priorities.append(
                    {
                        "priority": 4,
                        "type": "FDR",
                        "id": str(record.public_id),
                        "title": record.fdr_number,
                        "reason": "Re-verification required",
                    }
                )
        data["priority_queue"] = sorted(priorities, key=lambda item: item["priority"])
        return Response(data)


class ReportExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in STAFF:
            return Response({"detail": "Not authorized."}, status=403)
        fdrs = scoped_fdrs(request.user).select_related("institution").order_by("institution__code", "fdr_number")
        rows = list(
            fdrs.values_list(
                "institution__code",
                "fdr_number",
                "bank_name",
                "principal_amount",
                "issue_date",
                "maturity_date",
                "status",
            )
        )
        fmt = request.query_params.get("export_format", "csv").lower()
        if fmt == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(
                ["institution", "fdr_number", "bank_name", "principal_amount", "issue_date", "maturity_date", "status"]
            )
            writer.writerows(
                [
                    f"'{value}" if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value
                    for value in row
                ]
                for row in rows
            )
            response = HttpResponse(output.getvalue(), content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="fdr-report.csv"'
        elif fmt == "pdf":
            from reportlab.pdfgen import canvas

            output = io.BytesIO()
            pdf = canvas.Canvas(output, pagesize=(842, 595))
            pdf.setFont("Helvetica", 8)
            y = 570
            pdf.drawString(24, y, "FDR Kavach - FDR report")
            y -= 20
            for row in rows:
                line = " | ".join(str(x) for x in row)[:150]
                pdf.drawString(24, y, line)
                y -= 12
                if y < 30:
                    pdf.showPage()
                    pdf.setFont("Helvetica", 8)
                    y = 570
            pdf.save()
            response = HttpResponse(output.getvalue(), content_type="application/pdf")
            response["Content-Disposition"] = 'attachment; filename="fdr-report.pdf"'
        else:
            return Response({"detail": "format must be csv or pdf."}, status=400)
        record_event(
            actor=request.user,
            event_type="report.exported",
            request=request,
            payload={"format": fmt, "row_count": len(rows)},
        )
        return response


class LegacyImportView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        if request.user.role not in MANAGERS:
            return Response(
                {"detail": "Only senior officers and administrators can import legacy records."}, status=403
            )
        file = request.FILES.get("file")
        dry_run = str(request.data.get("dry_run", "true")).lower() != "false"
        if not file or file.size > 20 * 1024 * 1024:
            return Response({"detail": "Upload a supported CSV/XLSX file up to 20 MB."}, status=400)
        content = file.read()
        digest = hashlib.sha256(content).hexdigest()
        try:
            if file.name.lower().endswith(".csv"):
                table = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
            elif file.name.lower().endswith(".xlsx"):
                from openpyxl import load_workbook

                with zipfile.ZipFile(io.BytesIO(content)) as archive:
                    if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                        return Response({"detail": "Expanded XLSX content exceeds the 100 MB limit."}, status=413)
                workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
                sheet = workbook.active
                values = list(sheet.values)
                if len(values) > 50001 or (values and len(values[0]) > 64):
                    return Response({"detail": "XLSX imports are limited to 50,000 rows and 64 columns."}, status=413)
                headers = [str(x).strip() for x in values[0]]
                table = [dict(zip(headers, row, strict=False)) for row in values[1:] if any(x is not None for x in row)]
            else:
                return Response({"detail": "File must be CSV or XLSX."}, status=400)
        except Exception as exc:
            return Response({"detail": f"Could not parse import file: {exc}"}, status=400)
        if len(table) > 50000:
            return Response({"detail": "Imports are limited to 50,000 data rows."}, status=413)
        valid, errors = [], []
        for index, source in enumerate(table, 2):
            row = {str(k).strip().lower(): v for k, v in source.items() if k is not None}
            data = {
                "institution_id": row.get("institution_id"),
                "fdr_number": row.get("fdr_number"),
                "bank_name": row.get("bank_name"),
                "branch_name": row.get("branch_name", ""),
                "principal_amount": row.get("principal_amount"),
                "issue_date": row.get("issue_date"),
                "maturity_date": row.get("maturity_date"),
                "purpose": row.get("purpose", ""),
            }
            serializer = FDRInputSerializer(data=data)
            if (
                serializer.is_valid()
                and Institution.objects.filter(
                    public_id=serializer.validated_data["institution_id"], is_active=True
                ).exists()
            ):
                valid.append(serializer.validated_data)
            else:
                errors.append(
                    {
                        "row": index,
                        "errors": serializer.errors or {"institution_id": ["Unknown or inactive institution."]},
                    }
                )
        if not dry_run and errors:
            return Response(
                {"detail": "Import contains validation errors; no records were written.", "errors": errors}, status=400
            )
        if not dry_run:
            with transaction.atomic():
                for item in valid:
                    institution = Institution.objects.get(public_id=item.pop("institution_id"))
                    item.pop("renewal_of", None)
                    fdr = FDR.objects.create(institution=institution, created_by=request.user, **item)
                    from .record_services import create_version, declared_values, set_evidence

                    for field, value in declared_values(fdr).items():
                        set_evidence(
                            fdr=fdr,
                            actor=request.user,
                            evidence_type=FDREvidence.Kind.DECLARED,
                            field_name=field,
                            value=value,
                        )
                    create_version(fdr, request.user, "Legacy import")
        job = ImportJob.objects.create(
            uploaded_by=request.user,
            filename=file.name[:255],
            sha256=digest,
            status=ImportJob.Status.DRY_RUN if dry_run else ImportJob.Status.IMPORTED,
            dry_run=dry_run,
            validation_report={"rows": len(table), "valid": len(valid), "errors": errors},
        )
        record_event(
            actor=request.user,
            event_type="legacy_import.completed",
            request=request,
            payload={"import_id": job.pk, "dry_run": dry_run, "rows": len(valid), "errors": len(errors)},
        )
        return Response(
            {"id": job.pk, "dry_run": dry_run, "rows": len(table), "valid": len(valid), "errors": errors}, status=201
        )

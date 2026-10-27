import hashlib
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath

import pymupdf
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import record_event
from .models import (
    FDR,
    FDRCase,
    FDRDocument,
    FDREvidence,
    Institution,
    User,
)
from .pdf_extraction import extract_fdr_fields
from .record_services import (
    MATERIAL_FIELDS,
    can_write_fdr,
    create_version,
    declared_values,
    invalidate_release_requests,
    make_case,
    scoped_fdrs,
    set_evidence,
)
from .serializers import FDRInputSerializer

MAX_DOCUMENT_SIZE = 15 * 1024 * 1024
FIELD_NAMES = set(MATERIAL_FIELDS)


def fdr_data(fdr, include_evidence=False):
    data = {
        "id": str(fdr.public_id),
        "institution": {
            "id": str(fdr.institution.public_id),
            "code": fdr.institution.code,
            "name": fdr.institution.name,
        },
        "fdr_number": fdr.fdr_number,
        "bank_name": fdr.bank_name,
        "branch_name": fdr.branch_name,
        "principal_amount": str(fdr.principal_amount),
        "issue_date": fdr.issue_date.isoformat(),
        "maturity_date": fdr.maturity_date.isoformat(),
        "purpose": fdr.purpose,
        "status": fdr.status,
        "reverification_required": fdr.reverification_required,
        "created_at": fdr.created_at.isoformat(),
        "updated_at": fdr.updated_at.isoformat(),
        "document_count": fdr.documents.count(),
    }
    data["verified_at"] = fdr.verified_at.isoformat() if fdr.verified_at else None
    if include_evidence:
        data["evidence"] = list(
            fdr.evidence.filter(superseded_at__isnull=True)
            .values(
                "public_id", "evidence_type", "field_name", "value", "confidence", "corrected_by_user", "recorded_at"
            )
            .order_by("evidence_type", "field_name")
        )
    return data


def normalize_fdr_number(value):
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def version_if_changed(fdr, actor, reason, material_changed):
    if material_changed:
        invalidate_release_requests(fdr)
        if fdr.status not in {FDR.Status.DRAFT, FDR.Status.NEEDS_REVERIFICATION}:
            fdr.status = FDR.Status.NEEDS_REVERIFICATION
        fdr.reverification_required = fdr.status == FDR.Status.NEEDS_REVERIFICATION
        fdr.save(update_fields=["status", "reverification_required", "updated_at"])
        create_version(fdr, actor, reason=reason)


class InstitutionListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = Institution.objects.filter(is_active=True).order_by("name")
        if request.user.role == User.Role.INSTITUTION_USER:
            rows = rows.filter(pk=request.user.institution_id) if request.user.institution_id else rows.none()
        elif request.user.role not in {
            User.Role.REVIEWER,
            User.Role.APPROVER,
            User.Role.SENIOR_OFFICER,
            User.Role.ADMIN,
            User.Role.AUDITOR,
        }:
            return Response({"detail": "Not authorized."}, status=403)
        return Response([{"id": str(row.public_id), "code": row.code, "name": row.name} for row in rows])

    def post(self, request):
        if request.user.role != User.Role.ADMIN:
            return Response({"detail": "Not authorized."}, status=403)
        code = str(request.data.get("code", "")).strip().upper()
        name = str(request.data.get("name", "")).strip()
        registration = str(request.data.get("registration_number", "")).strip()
        if not code or len(code) > 32 or not name or len(name) > 255:
            return Response({"detail": "Valid institution code and name are required."}, status=400)
        institution = Institution.objects.create(code=code, name=name, registration_number=registration[:64])
        record_event(
            actor=request.user,
            event_type="institution.created",
            request=request,
            payload={"institution_id": institution.pk},
        )
        return Response(
            {"id": str(institution.public_id), "code": institution.code, "name": institution.name}, status=201
        )


class FDRListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response([fdr_data(row) for row in scoped_fdrs(request.user).select_related("institution")])

    @transaction.atomic
    def post(self, request):
        if request.user.role not in {User.Role.INSTITUTION_USER, User.Role.ADMIN, User.Role.SENIOR_OFFICER}:
            return Response({"detail": "Not authorized to submit FDR records."}, status=403)
        serializer = FDRInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        institution_id = serializer.validated_data["institution_id"]
        institution = get_object_or_404(Institution, public_id=institution_id, is_active=True)
        if request.user.role == User.Role.INSTITUTION_USER and request.user.institution_id != institution.pk:
            return Response({"detail": "You can only create records for your assigned institution."}, status=403)
        renewal = serializer.validated_data.pop("renewal_of", None)
        if renewal:
            if renewal.institution_id != institution.pk:
                return Response({"detail": "An FDR can only renew a record at the same institution."}, status=400)
            if renewal.status != FDR.Status.SUBMITTED or renewal.maturity_date > timezone.localdate():
                return Response({"detail": "Only a matured submitted FDR can be renewed."}, status=400)
            renewal.status = FDR.Status.RENEWED
            renewal.save(update_fields=["status", "updated_at"])
            create_version(renewal, request.user, reason="Renewal issued")
            invalidate_release_requests(renewal)
        values = {key: value for key, value in serializer.validated_data.items() if key != "institution_id"}
        values["renewal_of"] = renewal
        fdr = FDR.objects.create(institution=institution, created_by=request.user, **values)
        for field_name, value in declared_values(fdr).items():
            set_evidence(
                fdr=fdr, actor=request.user, evidence_type=FDREvidence.Kind.DECLARED, field_name=field_name, value=value
            )
        create_version(fdr, request.user, reason="Initial submission")
        previous = [
            row
            for row in FDR.objects.exclude(pk=fdr.pk).only("id", "fdr_number")
            if normalize_fdr_number(row.fdr_number) == normalize_fdr_number(fdr.fdr_number)
        ]
        if previous:
            make_case(
                actor=request.user,
                case_type="DUPLICATE_FDR_NUMBER",
                title=f"Potential duplicate FDR number: {fdr.fdr_number}",
                description=(
                    f"The number is already used on record(s): {', '.join(str(row.pk) for row in previous[:20])}."
                ),
                fdr=fdr,
                severity=FDRCase.Severity.HIGH,
            )
            record_event(
                actor=request.user,
                event_type="fdr.duplicate_number_detected",
                request=request,
                payload={"fdr_id": fdr.pk},
            )
        record_event(
            actor=request.user,
            event_type="fdr.created",
            request=request,
            payload={"fdr_id": fdr.pk, "institution_id": institution.pk},
        )
        if renewal:
            record_event(
                actor=request.user,
                event_type="fdr.renewed",
                request=request,
                payload={"fdr_id": fdr.pk, "renewed_fdr_id": renewal.pk},
            )
        return Response(fdr_data(fdr, include_evidence=True), status=201)


class FDRDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get_record(self, request, pk):
        return get_object_or_404(scoped_fdrs(request.user).select_related("institution"), public_id=pk)

    def get(self, request, pk):
        return Response(fdr_data(self.get_record(request, pk), include_evidence=True))

    @transaction.atomic
    def patch(self, request, pk):
        fdr = self.get_record(request, pk)
        if not can_write_fdr(request.user, fdr):
            return Response({"detail": "Not authorized to edit this record."}, status=403)
        if {"institution_id", "renewal_of"} & set(request.data):
            return Response({"detail": "Institution and renewal links cannot be changed after creation."}, status=400)
        serializer = FDRInputSerializer(fdr, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        changed = [key for key, value in serializer.validated_data.items() if getattr(fdr, key) != value]
        fdr = serializer.save()
        if changed:
            for field_name, value in declared_values(fdr).items():
                set_evidence(
                    fdr=fdr,
                    actor=request.user,
                    evidence_type=FDREvidence.Kind.DECLARED,
                    field_name=field_name,
                    value=value,
                )
            version_if_changed(fdr, request.user, "FDR material fields updated", bool(set(changed) & FIELD_NAMES))
            record_event(
                actor=request.user,
                event_type="fdr.updated",
                request=request,
                payload={"fdr_id": fdr.pk, "fields": changed},
            )
        return Response(fdr_data(fdr, include_evidence=True))


class FDRSubmitView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk):
        fdr = get_object_or_404(scoped_fdrs(request.user), public_id=pk)
        if not can_write_fdr(request.user, fdr):
            return Response({"detail": "Not authorized to submit this record."}, status=403)
        if fdr.status not in {FDR.Status.DRAFT, FDR.Status.NEEDS_REVERIFICATION}:
            return Response({"detail": "Only draft or re-verification records can be submitted."}, status=409)
        if not fdr.documents.exists():
            return Response({"detail": "Upload at least one supporting PDF before submission."}, status=400)
        latest_document = fdr.documents.order_by("-uploaded_at", "-id").first()
        observed_rows = fdr.evidence.filter(
            evidence_type=FDREvidence.Kind.DOCUMENT_OBSERVED,
            superseded_at__isnull=True,
            document=latest_document,
            field_name__in=FIELD_NAMES,
        )
        observed = {row.field_name: str(row.value) for row in observed_rows}
        expected = declared_values(fdr)
        required = {"fdr_number", "principal_amount"}
        if fdr.verified_snapshot:
            required.update(
                field
                for field in MATERIAL_FIELDS
                if field in fdr.verified_snapshot and fdr.verified_snapshot[field] != expected[field]
            )
        missing = {field for field in required if field not in observed or observed[field] != expected[field]}
        if missing:
            return Response(
                {
                    "detail": "Confirm document observations for FDR number and amount before submission.",
                    "missing_fields": sorted(missing),
                },
                status=400,
            )
        fdr.status = FDR.Status.SUBMITTED
        fdr.submitted_at = timezone.now()
        fdr.reverification_required = False
        fdr.verified_at = timezone.now()
        fdr.verified_snapshot = {
            **expected,
            "documents": list(fdr.documents.order_by("id").values_list("sha256", flat=True)),
        }
        fdr.save(
            update_fields=[
                "status",
                "submitted_at",
                "reverification_required",
                "verified_at",
                "verified_snapshot",
                "updated_at",
            ]
        )
        create_version(fdr, request.user, reason="Record submitted")
        record_event(actor=request.user, event_type="fdr.submitted", request=request, payload={"fdr_id": fdr.pk})
        return Response(fdr_data(fdr, include_evidence=True))


class FDRVersionListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        fdr = get_object_or_404(scoped_fdrs(request.user), public_id=pk)
        rows = fdr.versions.select_related("changed_by").order_by("-version_number")
        return Response(
            [
                {
                    "version_number": row.version_number,
                    "snapshot": row.snapshot,
                    "changed_by": row.changed_by.username,
                    "change_reason": row.change_reason,
                    "valid_from": row.valid_from.isoformat(),
                    "valid_to": row.valid_to.isoformat() if row.valid_to else None,
                }
                for row in rows
            ]
        )


class FDRDocumentListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_fdr(self, request, pk):
        return get_object_or_404(scoped_fdrs(request.user), public_id=pk)

    def get(self, request, pk):
        fdr = self.get_fdr(request, pk)
        return Response(
            [
                {
                    "id": str(row.public_id),
                    "filename": row.original_filename,
                    "size_bytes": row.size_bytes,
                    "sha256": row.sha256,
                    "uploaded_at": row.uploaded_at.isoformat(),
                    "extracted_at": row.extracted_at.isoformat() if row.extracted_at else None,
                }
                for row in fdr.documents.all()
            ]
        )

    @transaction.atomic
    def post(self, request, pk):
        fdr = self.get_fdr(request, pk)
        if not can_write_fdr(request.user, fdr):
            return Response({"detail": "Not authorized to upload evidence for this record."}, status=403)
        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response({"detail": "A PDF file is required."}, status=400)
        if PurePosixPath(uploaded.name.replace("\\", "/")).suffix.lower() != ".pdf":
            return Response({"detail": "Supporting evidence must use the .pdf extension."}, status=400)
        if uploaded.size > MAX_DOCUMENT_SIZE:
            return Response({"detail": "PDF files are limited to 15 MB."}, status=413)
        uploaded.seek(0)
        signature = uploaded.read(5)
        uploaded.seek(0)
        if signature != b"%PDF-":
            return Response({"detail": "The uploaded file is not a valid PDF."}, status=400)
        uploaded.seek(0)
        content = uploaded.read()
        if any(marker in content.lower() for marker in (b"/javascript", b"/openaction", b"/launch", b"/embeddedfile")):
            return Response({"detail": "PDFs containing active content are not accepted."}, status=400)
        try:
            parsed = pymupdf.open(stream=content, filetype="pdf")
            try:
                if parsed.is_encrypted or parsed.page_count > 100:
                    return Response({"detail": "Encrypted PDFs and PDFs over 100 pages are not accepted."}, status=400)
            finally:
                parsed.close()
        except pymupdf.FileDataError:
            return Response({"detail": "The uploaded file is not a parseable PDF."}, status=400)
        uploaded.seek(0)
        digest = hashlib.sha256()
        for chunk in uploaded.chunks():
            digest.update(chunk)
        uploaded.seek(0)
        document = FDRDocument.objects.create(
            fdr=fdr,
            file=uploaded,
            original_filename=PurePosixPath(uploaded.name.replace("\\", "/")).name[:255],
            content_type="application/pdf",
            size_bytes=uploaded.size,
            sha256=digest.hexdigest(),
            uploaded_by=request.user,
        )
        if fdr.status in {FDR.Status.SUBMITTED, FDR.Status.UNDER_REVIEW}:
            fdr.status = FDR.Status.NEEDS_REVERIFICATION
            fdr.reverification_required = True
            fdr.save(update_fields=["status", "reverification_required", "updated_at"])
        create_version(fdr, request.user, reason="Supporting document added")
        invalidate_release_requests(fdr)
        duplicates = FDRDocument.objects.exclude(pk=document.pk).filter(sha256=document.sha256).select_related("fdr")
        for duplicate in duplicates[:20]:
            make_case(
                actor=request.user,
                case_type="DUPLICATE_DOCUMENT_HASH",
                title="Potential duplicate supporting document",
                description=(
                    f"SHA-256 {document.sha256} is also attached to FDR "
                    f"{duplicate.fdr.fdr_number} (record {duplicate.fdr_id})."
                ),
                fdr=fdr,
                severity=FDRCase.Severity.HIGH,
            )
        record_event(
            actor=request.user,
            event_type="fdr.document_uploaded",
            request=request,
            payload={"fdr_id": fdr.pk, "document_id": document.pk, "sha256": document.sha256},
        )
        return Response(
            {
                "id": str(document.public_id),
                "filename": document.original_filename,
                "size_bytes": document.size_bytes,
                "sha256": document.sha256,
            },
            status=201,
        )


class FDRDocumentExtractView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, document_id):
        fdr = get_object_or_404(scoped_fdrs(request.user), public_id=pk)
        if not can_write_fdr(request.user, fdr):
            return Response({"detail": "Not authorized to process documents for this record."}, status=403)
        document = get_object_or_404(FDRDocument, public_id=document_id, fdr=fdr)
        try:
            fields = extract_fdr_fields(document)
        except (pymupdf.FileDataError, ValueError) as exc:
            return Response({"detail": str(exc) or "This PDF could not be read."}, status=422)
        document.extracted_fields = fields
        document.extracted_at = timezone.now()
        document.save(update_fields=["extracted_fields", "extracted_at"])
        record_event(
            actor=request.user,
            event_type="fdr.document_extracted",
            request=request,
            payload={"fdr_id": fdr.pk, "document_id": document.pk},
        )
        return Response({"document_id": str(document.public_id), "fields": fields})


class FDRDocumentConfirmView(APIView):
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request, pk, document_id):
        fdr = get_object_or_404(scoped_fdrs(request.user), public_id=pk)
        if not can_write_fdr(request.user, fdr):
            return Response({"detail": "Not authorized to confirm document evidence."}, status=403)
        document = get_object_or_404(FDRDocument, public_id=document_id, fdr=fdr)
        fields = request.data.get("fields")
        if not isinstance(fields, dict) or not fields or set(fields) - FIELD_NAMES:
            return Response({"detail": "Provide one or more supported FDR fields."}, status=400)
        changes = {}
        observations = []
        for field_name, value in fields.items():
            if not isinstance(value, (str, int, float)) or len(str(value)) > 255:
                return Response({"detail": f"Invalid value for {field_name}."}, status=400)
            text_value = str(value).strip()
            if field_name == "principal_amount":
                text_value = text_value.encode("ascii", "ignore").decode("ascii")
                text_value = re.sub(r"(?i)\b(?:INR|RS)\.?\s*", "", text_value).replace(",", "").strip()
                try:
                    parsed = Decimal(text_value.replace(",", "").replace("₹", "").replace("INR", "").strip())
                    if parsed <= 0:
                        raise InvalidOperation
                    text_value = str(parsed.quantize(Decimal("0.01")))
                except InvalidOperation:
                    return Response({"detail": "Principal amount must be a positive number."}, status=400)
                changes[field_name] = parsed
            elif field_name in {"issue_date", "maturity_date"}:
                try:
                    parsed_date = date.fromisoformat(text_value)
                except ValueError:
                    return Response({"detail": f"{field_name} must use YYYY-MM-DD format."}, status=400)
                changes[field_name] = parsed_date
                text_value = parsed_date.isoformat()
            elif field_name in {"fdr_number", "bank_name"} and not text_value:
                return Response({"detail": f"{field_name} cannot be blank."}, status=400)
            else:
                changes[field_name] = text_value
            extraction = document.extracted_fields.get(field_name, {})
            observations.append((field_name, text_value, extraction))
        changed = [key for key, value in changes.items() if getattr(fdr, key) != value]
        for key, value in changes.items():
            setattr(fdr, key, value)
        if fdr.maturity_date <= fdr.issue_date:
            return Response({"detail": "Maturity date must be after issue date."}, status=400)
        for field_name, text_value, extraction in observations:
            set_evidence(
                fdr=fdr,
                actor=request.user,
                evidence_type=FDREvidence.Kind.DOCUMENT_OBSERVED,
                field_name=field_name,
                value=text_value,
                document=document,
                confidence=extraction.get("confidence"),
                corrected=text_value != str(extraction.get("value", "")),
            )
        if changed:
            fdr.save()
            for field_name, value in declared_values(fdr).items():
                set_evidence(
                    fdr=fdr,
                    actor=request.user,
                    evidence_type=FDREvidence.Kind.DECLARED,
                    field_name=field_name,
                    value=value,
                )
            version_if_changed(fdr, request.user, "Document observation corrected FDR fields", True)
        else:
            create_version(fdr, request.user, "Document observations confirmed")
        record_event(
            actor=request.user,
            event_type="fdr.document_observations_confirmed",
            request=request,
            payload={"fdr_id": fdr.pk, "document_id": document.pk, "fields": list(fields)},
        )
        return Response(fdr_data(fdr, include_evidence=True))


class FDRDocumentDownloadView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk, document_id):
        fdr = get_object_or_404(scoped_fdrs(request.user), public_id=pk)
        document = get_object_or_404(FDRDocument, public_id=document_id, fdr=fdr)
        document.file.open("rb")
        return FileResponse(
            document.file, as_attachment=True, filename=document.original_filename, content_type="application/pdf"
        )

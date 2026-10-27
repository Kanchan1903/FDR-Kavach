import hashlib
from collections import defaultdict

from django.core.files.base import ContentFile
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import record_event
from .models import BankBatch, BankBatchRow, FDRDocument, ReconciliationResult, User
from .reconciliation import BankSimulator, normalized_number, process_bank_batch
from .record_services import scoped_fdrs

MAX_BANK_BATCH_SIZE = 10 * 1024 * 1024
IMPORT_ROLES = {User.Role.ADMIN, User.Role.SENIOR_OFFICER}


def result_data(result):
    row = result.bank_row
    return {
        "id": str(result.public_id),
        "batch_id": str(result.batch.public_id),
        "bank_row_number": row.row_number if row else None,
        "fdr_id": str(result.fdr.public_id) if result.fdr_id else None,
        "fdr_number": result.fdr.fdr_number
        if result.fdr_id
        else (row.fdr_number if row else result.details.get("fdr_number")),
        "classification": result.classification,
        "details": result.details,
    }


class BankBatchListCreateView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        if request.user.role not in IMPORT_ROLES | {User.Role.REVIEWER, User.Role.APPROVER, User.Role.AUDITOR}:
            return Response({"detail": "Not authorized."}, status=403)
        batches = BankBatch.objects.select_related("uploaded_by").order_by("-uploaded_at")[:100]
        return Response(
            [
                {
                    "id": str(batch.public_id),
                    "filename": batch.original_filename,
                    "sha256": batch.sha256,
                    "row_count": batch.row_count,
                    "uploaded_by": batch.uploaded_by.username,
                    "uploaded_at": batch.uploaded_at.isoformat(),
                    "completed_at": batch.completed_at.isoformat() if batch.completed_at else None,
                }
                for batch in batches
            ]
        )

    @transaction.atomic
    def post(self, request):
        if request.user.role not in IMPORT_ROLES:
            return Response({"detail": "Only administrators and senior officers can import bank batches."}, status=403)
        uploaded = request.FILES.get("file")
        if not uploaded or not uploaded.name.lower().endswith(".csv"):
            return Response({"detail": "Upload a CSV file."}, status=400)
        if uploaded.size > MAX_BANK_BATCH_SIZE:
            return Response({"detail": "Bank batches are limited to 10 MB."}, status=413)
        content = uploaded.read()
        digest = hashlib.sha256(content).hexdigest()
        rows = BankSimulator().parse(content)
        batch = BankBatch(
            uploaded_by=request.user,
            original_filename=uploaded.name.replace("\\", "/").rsplit("/", 1)[-1][:255],
            sha256=digest,
            row_count=len(rows),
        )
        safe_name = f"bank-batch-{digest[:20]}.csv"
        batch.file.save(safe_name, ContentFile(content), save=False)
        batch.save()
        BankBatchRow.objects.bulk_create([BankBatchRow(batch=batch, **row) for row in rows])
        counts = process_bank_batch(batch=batch, actor=request.user)
        record_event(
            actor=request.user,
            event_type="bank_batch.uploaded",
            request=request,
            payload={"batch_id": batch.pk, "sha256": digest},
        )
        return Response(
            {
                "id": str(batch.public_id),
                "filename": batch.original_filename,
                "row_count": batch.row_count,
                "classifications": counts,
            },
            status=201,
        )


class BankBatchResultsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        if request.user.role not in IMPORT_ROLES | {User.Role.REVIEWER, User.Role.APPROVER, User.Role.AUDITOR}:
            return Response({"detail": "Not authorized."}, status=403)
        batch = get_object_or_404(BankBatch, public_id=pk)
        results = batch.results.select_related("fdr", "bank_row").all()
        return Response({"batch_id": str(batch.public_id), "results": [result_data(row) for row in results]})


class DuplicateFindingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        fdr_rows = scoped_fdrs(request.user).select_related("institution")
        number_groups = defaultdict(list)
        for fdr in fdr_rows:
            number_groups[normalized_number(fdr.fdr_number)].append(fdr)
        duplicates = [
            {
                "type": "FDR_NUMBER",
                "normalized_value": key,
                "records": [
                    {"id": str(row.public_id), "fdr_number": row.fdr_number, "institution": row.institution.code}
                    for row in rows
                ],
            }
            for key, rows in number_groups.items()
            if key and len(rows) > 1
        ]
        visible_ids = [row.pk for group in number_groups.values() for row in group]
        doc_groups = defaultdict(list)
        documents = FDRDocument.objects.filter(fdr_id__in=visible_ids).select_related("fdr__institution")
        for doc in documents:
            doc_groups[doc.sha256].append(doc)
        duplicates.extend(
            {
                "type": "DOCUMENT_SHA256",
                "sha256": digest,
                "records": [
                    {
                        "document_id": str(doc.public_id),
                        "fdr_id": str(doc.fdr.public_id),
                        "fdr_number": doc.fdr.fdr_number,
                        "institution": doc.fdr.institution.code,
                    }
                    for doc in docs
                ],
            }
            for digest, docs in doc_groups.items()
            if len({doc.fdr_id for doc in docs}) > 1
        )
        return Response(duplicates)


class ReconciliationResultListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if request.user.role not in IMPORT_ROLES | {User.Role.REVIEWER, User.Role.APPROVER, User.Role.AUDITOR}:
            return Response({"detail": "Not authorized."}, status=403)
        rows = ReconciliationResult.objects.select_related("batch", "fdr", "bank_row").order_by("-created_at")[:1000]
        return Response([result_data(row) for row in rows])

import csv
import re
from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO

from django.db import transaction
from django.utils import timezone

from .audit import record_event
from .models import FDR, FDRCase, FDREvidence, ReconciliationResult
from .record_services import make_case, set_evidence


class BankVerifier(ABC):
    """Adapter contract for normalizing and verifying bank-provided records."""

    @abstractmethod
    def parse(self, content: bytes) -> list[dict]:
        raise NotImplementedError


class BankSimulator(BankVerifier):
    """CSV adapter used during development until a bank API is connected."""

    ALIASES = {
        "fdr_number": {"fdrnumber", "fdrno", "receiptno", "depositnumber"},
        "principal_amount": {"principalamount", "amount", "depositamount"},
        "bank_name": {"bankname", "bank"},
        "branch_name": {"branchname", "branch"},
        "issue_date": {"issuedate", "dateofissue", "depositdate"},
        "maturity_date": {"maturitydate", "dateofmaturity", "duedate"},
    }

    @staticmethod
    def _key(value):
        return re.sub(r"[^a-z0-9]", "", (value or "").lower())

    @staticmethod
    def _date(value, row_number, field_name):
        if not value:
            return None
        value = value.strip()
        for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
            try:
                return datetime.strptime(value, pattern).date()
            except ValueError:
                continue
        raise ValueError(f"Row {row_number}: invalid {field_name} date.")

    def parse(self, content: bytes) -> list[dict]:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("CSV must use UTF-8 encoding.") from exc
        reader = csv.DictReader(StringIO(text))
        if not reader.fieldnames:
            raise ValueError("CSV must include a header row.")
        header_map = {}
        for header in reader.fieldnames:
            normalized = self._key(header)
            for field_name, aliases in self.ALIASES.items():
                if normalized in aliases:
                    header_map[field_name] = header
        if not {"fdr_number", "principal_amount"}.issubset(header_map):
            raise ValueError("CSV requires FDR number and principal amount columns.")
        rows = []
        for row_number, raw in enumerate(reader, start=2):
            if not any((value or "").strip() for value in raw.values()):
                continue
            fdr_number = (raw.get(header_map["fdr_number"]) or "").strip()
            if not fdr_number:
                raise ValueError(f"Row {row_number}: FDR number is required.")
            try:
                amount = Decimal((raw.get(header_map["principal_amount"]) or "").replace(",", "").strip())
            except (InvalidOperation, AttributeError) as exc:
                raise ValueError(f"Row {row_number}: invalid principal amount.") from exc
            if amount <= 0:
                raise ValueError(f"Row {row_number}: principal amount must be greater than zero.")
            rows.append(
                {
                    "row_number": row_number,
                    "fdr_number": fdr_number[:100],
                    "principal_amount": amount,
                    "bank_name": (raw.get(header_map.get("bank_name"), "") or "").strip()[:255],
                    "branch_name": (raw.get(header_map.get("branch_name"), "") or "").strip()[:255],
                    "issue_date": self._date(raw.get(header_map.get("issue_date"), ""), row_number, "issue")
                    if "issue_date" in header_map
                    else None,
                    "maturity_date": self._date(raw.get(header_map.get("maturity_date"), ""), row_number, "maturity")
                    if "maturity_date" in header_map
                    else None,
                    "raw_data": {str(key)[:100]: str(value or "")[:500] for key, value in raw.items()},
                }
            )
            if len(rows) > 10000:
                raise ValueError("CSV is limited to 10,000 data rows.")
        if not rows:
            raise ValueError("CSV contains no data rows.")
        return rows


def normalized_number(value):
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


@transaction.atomic
def process_bank_batch(*, batch, actor):
    candidates = [fdr for fdr in FDR.objects.exclude(status=FDR.Status.DRAFT).select_related("institution")]
    portal_by_number = {}
    for fdr in candidates:
        portal_by_number.setdefault(normalized_number(fdr.fdr_number), []).append(fdr)
    seen_numbers = set()
    seen_fdr_ids = set()
    result_counts = {}

    for bank_row in batch.rows.all().order_by("row_number"):
        key = normalized_number(bank_row.fdr_number)
        seen_numbers.add(key)
        matched = portal_by_number.get(key, [])
        fdr = matched[0] if len(matched) == 1 else None
        if len(matched) > 1:
            classification = ReconciliationResult.Classification.AMBIGUOUS
            details = {"candidate_fdr_ids": [record.pk for record in matched]}
        elif not matched:
            classification = ReconciliationResult.Classification.UNMAPPED_IN_PORTAL
            details = {"reason": "No submitted portal record has this FDR number."}
        else:
            seen_fdr_ids.add(fdr.pk)
            amount_differs = fdr.principal_amount != bank_row.principal_amount
            date_diffs = {}
            if bank_row.issue_date and fdr.issue_date != bank_row.issue_date:
                date_diffs["issue_date"] = {
                    "portal": fdr.issue_date.isoformat(),
                    "bank": bank_row.issue_date.isoformat(),
                }
            if bank_row.maturity_date and fdr.maturity_date != bank_row.maturity_date:
                date_diffs["maturity_date"] = {
                    "portal": fdr.maturity_date.isoformat(),
                    "bank": bank_row.maturity_date.isoformat(),
                }
            details = {
                "portal_amount": str(fdr.principal_amount),
                "bank_amount": str(bank_row.principal_amount),
                "date_mismatches": date_diffs,
            }
            if amount_differs:
                classification = ReconciliationResult.Classification.AMOUNT_MISMATCH
            elif date_diffs:
                classification = ReconciliationResult.Classification.DATE_MISMATCH
            else:
                classification = ReconciliationResult.Classification.MATCHED
            bank_values = {
                "fdr_number": bank_row.fdr_number,
                "principal_amount": str(bank_row.principal_amount),
            }
            if bank_row.bank_name:
                bank_values["bank_name"] = bank_row.bank_name
            if bank_row.branch_name:
                bank_values["branch_name"] = bank_row.branch_name
            if bank_row.issue_date:
                bank_values["issue_date"] = bank_row.issue_date.isoformat()
            if bank_row.maturity_date:
                bank_values["maturity_date"] = bank_row.maturity_date.isoformat()
            for field_name, value in bank_values.items():
                set_evidence(
                    fdr=fdr,
                    actor=actor,
                    evidence_type=FDREvidence.Kind.BANK_CONFIRMED,
                    field_name=field_name,
                    value=value,
                    confidence=Decimal("1.000"),
                )
        result = ReconciliationResult.objects.create(
            batch=batch,
            bank_row=bank_row,
            fdr=fdr,
            classification=classification,
            details=details,
        )
        result_counts[classification] = result_counts.get(classification, 0) + 1
        if classification != ReconciliationResult.Classification.MATCHED:
            make_case(
                actor=actor,
                case_type=classification,
                title=f"Bank reconciliation: {classification.replace('_', ' ').lower()}",
                description=f"Bank batch {batch.pk}, row {bank_row.row_number}, FDR {bank_row.fdr_number}.",
                fdr=fdr,
                result=result,
                severity=FDRCase.Severity.HIGH
                if classification
                in {ReconciliationResult.Classification.AMBIGUOUS, ReconciliationResult.Classification.AMOUNT_MISMATCH}
                else FDRCase.Severity.MEDIUM,
            )

    for key, records in portal_by_number.items():
        for fdr in records:
            if key in seen_numbers:
                continue
            result = ReconciliationResult.objects.create(
                batch=batch,
                fdr=fdr,
                classification=ReconciliationResult.Classification.MISSING_IN_BANK_BATCH,
                details={"fdr_number": fdr.fdr_number, "institution": fdr.institution.code},
            )
            result_counts[result.classification] = result_counts.get(result.classification, 0) + 1
            make_case(
                actor=actor,
                case_type=result.classification,
                title=f"FDR missing from bank batch: {fdr.fdr_number}",
                description=f"FDR {fdr.fdr_number} was not present in bank batch {batch.pk}.",
                fdr=fdr,
                result=result,
                severity=FDRCase.Severity.HIGH,
            )

    batch.completed_at = timezone.now()
    batch.save(update_fields=["completed_at"])
    record_event(
        actor=actor,
        event_type="bank_batch.reconciled",
        payload={"batch_id": batch.pk, "row_count": batch.row_count, "classifications": result_counts},
    )
    return result_counts

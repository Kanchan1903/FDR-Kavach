import re
from datetime import datetime

import pymupdf

LABELS = {
    "fdr_number": r"(?:FDR\s*(?:No\.?|Number)|Receipt\s*(?:No\.?|Number)|Deposit\s*(?:No\.?|Number))",
    "principal_amount": r"(?:Principal\s+Amount|Deposit\s+Amount|Amount)",
    "issue_date": r"(?:Issue\s+Date|Date\s+of\s+Issue|Deposit\s+Date)",
    "maturity_date": r"(?:Maturity\s+Date|Date\s+of\s+Maturity|Due\s+Date)",
    "bank_name": r"(?:Bank\s+Name|Bank)",
    "branch_name": r"(?:Branch\s+Name|Branch)",
}


def _labeled_value(text, label, value_pattern):
    match = re.search(rf"{label}\s*[:#\-]?\s*({value_pattern})", text, re.IGNORECASE)
    if not match:
        return None
    return match.group(1).strip(" \t\r\n:;,")


def _date_value(value):
    if not value:
        return None
    for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value.strip(), pattern).date().isoformat()
        except ValueError:
            continue
    return value


def _field(value, confidence):
    return {"value": value or "", "confidence": confidence if value else 0.0}


def extract_fdr_fields(document):
    document.file.open("rb")
    try:
        source = document.file.read()
    finally:
        document.file.close()
    pdf = pymupdf.open(stream=source, filetype="pdf")
    try:
        if pdf.page_count > 100:
            raise ValueError("PDF exceeds the 100 page extraction limit.")
        text_parts = []
        size = 0
        for page in pdf:
            part = page.get_text("text")
            size += len(part)
            if size > 1_000_000:
                raise ValueError("PDF text exceeds the extraction size limit.")
            text_parts.append(part)
    finally:
        pdf.close()
    text = "\n".join(text_parts)
    number = _labeled_value(text, LABELS["fdr_number"], r"[A-Z0-9][A-Z0-9/\-]{3,79}")
    amount = _labeled_value(text, LABELS["principal_amount"], r"(?:INR|Rs\.?|\u20b9)?\s*[0-9][0-9,]*(?:\.\d{1,2})?")
    issue = _labeled_value(text, LABELS["issue_date"], r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}")
    maturity = _labeled_value(text, LABELS["maturity_date"], r"\d{1,4}[./-]\d{1,2}[./-]\d{1,4}")
    bank = _labeled_value(text, LABELS["bank_name"], r"[^\n]{2,100}")
    branch = _labeled_value(text, LABELS["branch_name"], r"[^\n]{2,100}")
    purpose = _labeled_value(text, r"(?:Security\s+)?Purpose|Subject", r"[^\n]{2,255}")
    clean_amount = re.sub(r"(?i)^(?:INR|RS)\.?\s*|\u20b9", "", amount).replace(",", "").strip() if amount else ""
    return {
        "fdr_number": _field(number, 0.96),
        "principal_amount": _field(clean_amount, 0.94),
        "issue_date": _field(_date_value(issue), 0.93),
        "maturity_date": _field(_date_value(maturity), 0.93),
        "bank_name": _field(bank, 0.86),
        "branch_name": _field(branch, 0.86),
        "purpose": _field(purpose, 0.80),
    }

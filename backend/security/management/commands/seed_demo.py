import hashlib
from datetime import date, timedelta
from decimal import Decimal

import pyotp
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from security.audit import record_event
from security.models import (
    FDR,
    BankBatch,
    BankBatchRow,
    CoverageRuleSet,
    FDREvidence,
    FDRVersion,
    Institution,
    User,
)
from security.reconciliation import BankSimulator, process_bank_batch
from security.record_services import set_evidence, snapshot_fdr

DEMO_PASSWORD = "DemoKavach2026!"


class Command(BaseCommand):
    help = "Create a deterministic, development-only FDR Kavach demo dataset and demo credentials."

    def handle(self, *args, **options):
        if not settings.DEBUG or not settings.DEMO_MODE:
            raise CommandError("Demo seeding requires both DJANGO_DEBUG=1 and DEMO_MODE=true.")
        def ensure_user(username, role, institution=None):
            user, created = User.objects.get_or_create(
                username=username,
                defaults={"role": role, "institution": institution, "is_staff": role == User.Role.ADMIN},
            )
            if not created and (user.role != role or user.institution_id != getattr(institution, "id", None)):
                raise CommandError(f"Existing demo user {username!r} has a different role or institution.")
            user.set_password(DEMO_PASSWORD)
            if not user.totp_secret:
                user.totp_secret = pyotp.random_base32()
            user.totp_enabled = True
            user.save(update_fields=["password", "totp_secret", "totp_enabled"])
            return user

        institutions = []
        for code, name in [
            ("DEMO-001", "Demo Institute North"),
            ("DEMO-002", "Demo Institute Central"),
            ("DEMO-003", "Demo Institute South"),
        ]:
            institution, _ = Institution.objects.get_or_create(code=code, defaults={"name": name})
            institutions.append(institution)

        institution_users = [
            ensure_user(f"demo-{institution.code.lower()}", User.Role.INSTITUTION_USER, institution)
            for institution in institutions
        ]
        senior = ensure_user("demo-senior", User.Role.SENIOR_OFFICER)
        admin = ensure_user("demo-admin", User.Role.ADMIN)
        ensure_user("demo-reviewer", User.Role.REVIEWER)
        ensure_user("demo-approver", User.Role.APPROVER)
        ensure_user("demo-auditor", User.Role.AUDITOR)

        with transaction.atomic():
            rule = CoverageRuleSet.objects.filter(name="DEMO RULE - illustrative only").first()
            if rule is None:
                CoverageRuleSet.objects.filter(active=True).update(active=False)
                rule = CoverageRuleSet.objects.create(
                    name="DEMO RULE - illustrative only",
                    required_amount=Decimal("1000000.00"),
                    source="DEMO RULE - seeded sample value; not an official AICTE amount or rule",
                    effective_from=date.today(),
                    approving_authority="DEMO RULE - sample authority",
                    shortfall_tolerance=Decimal("0.00"),
                    active=True,
                    checklist_items=[
                        "matured", "bank_confirmed", "no_open_cases", "verified", "supporting_document", "coverage"
                    ],
                    updated_by=admin,
                )
            else:
                CoverageRuleSet.objects.exclude(pk=rule.pk).filter(active=True).update(active=False)
                if not rule.active:
                    rule.active = True
                    rule.save(update_fields=["active", "updated_at"])

        fdrs = []
        base_date = timezone.localdate()
        for number in range(1, 31):
            institution = institutions[(number - 1) % len(institutions)]
            creator = institution_users[(number - 1) % len(institution_users)]
            issue = base_date - timedelta(days=365 + number)
            maturity = base_date + timedelta(days=(number - 15) * 20)
            fdr, created = FDR.objects.get_or_create(
                fdr_number=f"DEMO-FDR-{number:03d}",
                defaults={
                    "institution": institution,
                    "bank_name": "Demo Cooperative Bank",
                    "branch_name": "Sample Branch",
                    "principal_amount": Decimal("120000.00") if number % 3 else Decimal("80000.00"),
                    "issue_date": issue,
                    "maturity_date": maturity,
                    "purpose": "DEMO RULE dataset",
                    "status": FDR.Status.SUBMITTED,
                    "created_by": creator,
                    "submitted_at": timezone.now(),
                },
            )
            fdrs.append(fdr)
            if created:
                fields = {
                    "fdr_number": fdr.fdr_number,
                    "bank_name": fdr.bank_name,
                    "branch_name": fdr.branch_name,
                    "principal_amount": str(fdr.principal_amount),
                    "issue_date": fdr.issue_date.isoformat(),
                    "maturity_date": fdr.maturity_date.isoformat(),
                    "purpose": fdr.purpose,
                }
                for field, value in fields.items():
                    set_evidence(
                        fdr=fdr,
                        actor=creator,
                        evidence_type=FDREvidence.Kind.DECLARED,
                        field_name=field,
                        value=value,
                    )
                FDRVersion.objects.create(fdr=fdr, version_number=1, snapshot=snapshot_fdr(fdr), changed_by=creator)

        headers = "fdr_number,principal_amount,bank_name,branch_name,issue_date,maturity_date\n"
        rows = []
        for index, fdr in enumerate(fdrs[:27]):
            amount = fdr.principal_amount + Decimal("500.00") if index == 0 else fdr.principal_amount
            maturity = fdr.maturity_date + timedelta(days=10) if index == 1 else fdr.maturity_date
            rows.append(
                f"{fdr.fdr_number},{amount},Demo Cooperative Bank,Sample Branch,{fdr.issue_date},{maturity}"
            )
        rows.append("DEMO-UNMAPPED-001,50000.00,Demo Cooperative Bank,Unknown Branch,2025-01-01,2030-01-01")
        csv_content = (headers + "\n".join(rows) + "\n").encode()
        parsed = BankSimulator().parse(csv_content)
        batch, created = BankBatch.objects.get_or_create(
            sha256=hashlib.sha256(csv_content).hexdigest(),
            defaults={"uploaded_by": senior, "original_filename": "demo-bank-batch.csv", "row_count": len(parsed)},
        )
        if created:
            batch.file.save("demo-bank-batch.csv", ContentFile(csv_content), save=True)
            BankBatchRow.objects.bulk_create([BankBatchRow(batch=batch, **row) for row in parsed])
            process_bank_batch(batch=batch, actor=senior)
            record_event(
                actor=senior,
                event_type="demo.seeded",
                payload={"fdr_count": len(fdrs), "bank_batch": str(batch.public_id)},
            )

        self.stdout.write(
            self.style.SUCCESS(f"Seeded {len(institutions)} institutions, {len(fdrs)} FDR records, and a bank batch.")
        )
        self.stdout.write("Demo accounts use the public demo password shown on the website sign-in page.")

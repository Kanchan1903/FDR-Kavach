import tempfile
from datetime import timedelta
from decimal import Decimal

import pymupdf
import pyotp
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .audit import record_event, verify_chain
from .models import (
    FDR,
    AuditEvent,
    CaseAlert,
    CaseEscalationPolicy,
    CoverageDay,
    CoverageRuleSet,
    FDRCase,
    FDRDocument,
    FDREvidence,
    FDRVersion,
    Institution,
    ReleaseRequest,
    User,
)
from .tasks import dispatch_case_alerts

TEST_PASSWORD = "Very" + "StrongPass" + "123!"


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class SecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient(enforce_csrf_checks=True)
        self.csrf = self.client.get("/api/csrf/").data["csrfToken"]

    def login(self, username, password):
        return self.client.post(
            "/api/auth/login/", {"username": username, "password": password}, format="json", HTTP_X_CSRFTOKEN=self.csrf
        )

    def test_institution_login_rotates_session_and_logout_clears_it(self):
        User.objects.create_user(username="institution", password=TEST_PASSWORD, role="INSTITUTION_USER")
        preauth_session = self.client.session
        preauth_session["preauth_marker"] = True
        preauth_session.save()
        previous_session_key = preauth_session.session_key
        response = self.login("institution", TEST_PASSWORD)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["authenticated"])
        self.assertNotEqual(previous_session_key, self.client.session.session_key)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)
        identity = self.client.get("/api/auth/me/")
        self.assertFalse(identity.data["mfa_required"])
        self.assertFalse(identity.data["mfa_enabled"])
        self.csrf = self.client.get("/api/csrf/").data["csrfToken"]
        logout = self.client.post("/api/auth/logout/", {}, format="json", HTTP_X_CSRFTOKEN=self.csrf)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_staff_requires_mfa_and_session_rotation(self):
        user = User.objects.create_user(
            username="reviewer",
            password=TEST_PASSWORD,
            role="REVIEWER",
            totp_enabled=True,
            totp_secret=pyotp.random_base32(),
        )
        response = self.login("reviewer", TEST_PASSWORD)
        self.assertTrue(response.data["mfa_required"])
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)
        response = self.client.post(
            "/api/auth/mfa/verify/",
            {"code": pyotp.TOTP(user.totp_secret).now()},
            format="json",
            HTTP_X_CSRFTOKEN=self.csrf,
        )
        self.assertTrue(response.data["authenticated"])
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)
        identity = self.client.get("/api/auth/me/")
        self.assertTrue(identity.data["mfa_required"])
        self.assertTrue(identity.data["mfa_enabled"])

    @override_settings(DEBUG=True, DEMO_MODE=True)
    def test_demo_totp_autofill_requires_a_successful_password_challenge(self):
        secret = pyotp.random_base32()
        user = User.objects.create_user(
            username="demo-reviewer",
            password=TEST_PASSWORD,
            role=User.Role.REVIEWER,
            totp_enabled=True,
            totp_secret=secret,
        )
        unchallenged = self.client.get("/api/auth/demo-totp/?username=demo-reviewer")
        self.assertEqual(unchallenged.status_code, 404)
        session = self.client.session
        session["mfa_user_id"] = user.pk
        session.save()
        response = self.client.get("/api/auth/demo-totp/?username=demo-reviewer")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(pyotp.TOTP(secret).verify(response.data["code"], valid_window=0))
        self.assertNotIn("secret", response.data)

    @override_settings(DEBUG=True, DEMO_MODE=True)
    def test_demo_totp_autofill_supports_staff_enrollment_challenge(self):
        secret = pyotp.random_base32()
        user = User.objects.create_user(
            username="demo-reviewer",
            password=TEST_PASSWORD,
            role=User.Role.REVIEWER,
            totp_enabled=False,
            totp_secret=secret,
        )
        session = self.client.session
        session["mfa_enroll_user_id"] = user.pk
        session.save()

        response = self.client.get("/api/auth/demo-totp/?username=demo-reviewer")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(pyotp.TOTP(secret).verify(response.data["code"], valid_window=0))
        self.assertNotIn("secret", response.data)

    def test_totp_secret_is_encrypted_for_database_storage(self):
        secret = pyotp.random_base32()
        field = User._meta.get_field("totp_secret")
        stored_value = field.get_prep_value(secret)
        self.assertNotEqual(stored_value, secret)
        self.assertEqual(field.from_db_value(stored_value, None, None), secret)

    def test_staff_enrolls_totp_before_first_session(self):
        user = User.objects.create_user(username="newreviewer", password=TEST_PASSWORD, role="REVIEWER")
        response = self.login(user.username, TEST_PASSWORD)
        self.assertTrue(response.data["mfa_enrollment_required"])
        enrollment = self.client.get("/api/auth/mfa/enroll/")
        secret = enrollment.data["secret"]
        response = self.client.post(
            "/api/auth/mfa/enroll/",
            {"code": pyotp.TOTP(secret).now()},
            format="json",
            HTTP_X_CSRFTOKEN=self.csrf,
        )
        self.assertTrue(response.data["authenticated"])
        user.refresh_from_db()
        self.assertTrue(user.totp_enabled)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)

    def test_lockout_after_configured_failures(self):
        user = User.objects.create_user(username="locked", password=TEST_PASSWORD)
        with override_settings(LOGIN_MAX_ATTEMPTS=2):
            self.login("locked", "bad")
            self.login("locked", "bad")
        user.refresh_from_db()
        self.assertTrue(user.is_locked)

    def test_audit_chain_and_tamper_detection(self):
        user = User.objects.create_user(
            username="auditor",
            password=TEST_PASSWORD,
            role="AUDITOR",
            totp_enabled=True,
            totp_secret=pyotp.random_base32(),
        )
        record_event(actor=user, event_type="test.one", payload={"a": 1})
        record_event(actor=user, event_type="test.two", payload={"b": 2})
        self.assertTrue(verify_chain()["valid"])
        AuditEvent.objects.filter(pk=1).update(payload={"a": 99})
        self.assertFalse(verify_chain()["valid"])

    def test_audit_endpoint_is_role_restricted(self):
        user = User.objects.create_user(username="institution2", password=TEST_PASSWORD, role="INSTITUTION_USER")
        self.login(user.username, TEST_PASSWORD)
        self.assertEqual(self.client.get("/api/audit/verify/").status_code, 403)


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class FDRWorkflowTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.temp_media.name)
        self.media_override.enable()
        self.institution = Institution.objects.create(code="INS-A", name="Institute A")
        self.other_institution = Institution.objects.create(code="INS-B", name="Institute B")
        self.user = User.objects.create_user(
            username="institution-a",
            password=TEST_PASSWORD,
            role=User.Role.INSTITUTION_USER,
            institution=self.institution,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def tearDown(self):
        self.media_override.disable()
        self.temp_media.cleanup()

    def fdr_payload(self, **changes):
        return {
            "institution_id": str(self.institution.public_id),
            "fdr_number": "FDR-A-001",
            "bank_name": "Test Bank",
            "branch_name": "Main Branch",
            "principal_amount": "500000.00",
            "issue_date": "2025-01-01",
            "maturity_date": "2030-01-01",
            "purpose": "Security deposit",
            **changes,
        }

    @staticmethod
    def pdf_bytes():
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text(
            (40, 50),
            "\n".join(
                [
                    "FDR No: FDR-A-001",
                    "Principal Amount: INR 499,000.00",
                    "Issue Date: 01/01/2025",
                    "Maturity Date: 01/01/2030",
                    "Bank Name: Test Bank",
                    "Branch: Main Branch",
                ]
            ),
        )
        content = doc.tobytes()
        doc.close()
        return content

    def test_document_extraction_correction_and_submission(self):
        response = self.client.post("/api/fdrs/", self.fdr_payload(), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        fdr_id = response.data["id"]
        source_pdf = self.pdf_bytes()
        upload = SimpleUploadedFile("fdr.pdf", source_pdf, content_type="application/pdf")
        response = self.client.post(f"/api/fdrs/{fdr_id}/documents/", {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        document_id = response.data["id"]
        self.assertEqual(len(response.data["sha256"]), 64)
        response = self.client.post(f"/api/fdrs/{fdr_id}/documents/{document_id}/extract/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        fields = response.data["fields"]
        self.assertEqual(fields["fdr_number"]["value"], "FDR-A-001")
        self.assertGreater(fields["principal_amount"]["confidence"], 0.9)
        response = self.client.post(
            f"/api/fdrs/{fdr_id}/documents/{document_id}/confirm/",
            {"fields": {"fdr_number": "FDR-A-001", "principal_amount": "500000.00"}},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(
            any(
                item["evidence_type"] == "DOCUMENT_OBSERVED" and item["corrected_by_user"]
                for item in response.data["evidence"]
            )
        )
        response = self.client.post(f"/api/fdrs/{fdr_id}/submit/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], FDR.Status.SUBMITTED)
        self.assertFalse(response.data["reverification_required"])
        self.assertEqual(len(FDRDocument.objects.get(public_id=document_id).sha256), 64)
        self.assertEqual(response.data["document_count"], 1)
        self.assertFalse(
            FDREvidence.objects.filter(fdr__public_id=fdr_id, evidence_type=FDREvidence.Kind.BANK_CONFIRMED).exists()
        )
        document = FDRDocument.objects.get(public_id=document_id)
        with open(document.file.path, "rb") as stored:
            self.assertNotEqual(stored.read(), self.pdf_bytes())
        with document.file.open("rb") as verified:
            self.assertEqual(verified.read(), source_pdf)

    def test_malicious_upload_extensions_and_pdf_actions_are_rejected(self):
        response = self.client.post("/api/fdrs/", self.fdr_payload(), format="json")
        fdr_id = response.data["id"]
        invalid_extension = SimpleUploadedFile("payload.txt", b"%PDF-1.4 fake", content_type="application/pdf")
        response = self.client.post(f"/api/fdrs/{fdr_id}/documents/", {"file": invalid_extension}, format="multipart")
        self.assertEqual(response.status_code, 400)
        active_pdf = self.pdf_bytes() + b"\n/OpenAction /JavaScript /JS (app.alert(1))"
        malicious_pdf = SimpleUploadedFile("active.pdf", active_pdf, content_type="application/pdf")
        response = self.client.post(f"/api/fdrs/{fdr_id}/documents/", {"file": malicious_pdf}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(FDRDocument.objects.count(), 0)

    def test_malicious_uploads_are_rejected_before_storage(self):
        response = self.client.post("/api/fdrs/", self.fdr_payload(), format="json")
        fdr_id = response.data["id"]
        fake_pdf = SimpleUploadedFile("payload.txt", b"%PDF-1.7 not really a PDF", content_type="application/pdf")
        rejected_extension = self.client.post(
            f"/api/fdrs/{fdr_id}/documents/", {"file": fake_pdf}, format="multipart"
        )
        self.assertEqual(rejected_extension.status_code, 400)
        active_pdf = self.pdf_bytes() + b"/OpenAction /JavaScript"
        rejected_active = self.client.post(
            f"/api/fdrs/{fdr_id}/documents/",
            {"file": SimpleUploadedFile("active.pdf", active_pdf, content_type="application/pdf")},
            format="multipart",
        )
        self.assertEqual(rejected_active.status_code, 400)
        self.assertFalse(FDRDocument.objects.filter(fdr__public_id=fdr_id).exists())

    def test_institution_object_scope_hides_other_institution_fdr(self):
        other_user = User.objects.create_user(
            username="institution-b",
            password=TEST_PASSWORD,
            role=User.Role.INSTITUTION_USER,
            institution=self.other_institution,
        )
        other_fdr = FDR.objects.create(
            institution=self.other_institution,
            created_by=other_user,
            fdr_number="FDR-B-001",
            bank_name="Other Bank",
            principal_amount=Decimal("1000.00"),
            issue_date="2025-01-01",
            maturity_date="2026-01-01",
        )
        response = self.client.get(f"/api/fdrs/{other_fdr.public_id}/")
        self.assertEqual(response.status_code, 404)
        response = self.client.patch(f"/api/fdrs/{other_fdr.public_id}/", {"purpose": "changed"}, format="json")
        self.assertEqual(response.status_code, 404)

    def test_upload_reverifies_submitted_record_and_duplicate_hash_is_reported(self):
        fdr = FDR.objects.create(
            institution=self.institution,
            created_by=self.user,
            fdr_number="FDR-A-002",
            bank_name="Test Bank",
            principal_amount=Decimal("10.00"),
            issue_date="2025-01-01",
            maturity_date="2026-01-01",
            status=FDR.Status.SUBMITTED,
            verified_snapshot={"fdr_number": "FDR-A-002"},
            verified_at=timezone.now(),
        )
        pdf = self.pdf_bytes()
        for number in ("FDR-A-002", "FDR-A-003"):
            current = (
                fdr
                if number == "FDR-A-002"
                else FDR.objects.create(
                    institution=self.institution,
                    created_by=self.user,
                    fdr_number=number,
                    bank_name="Test Bank",
                    principal_amount=Decimal("10.00"),
                    issue_date="2025-01-01",
                    maturity_date="2026-01-01",
                )
            )
            response = self.client.post(
                f"/api/fdrs/{current.public_id}/documents/",
                {"file": SimpleUploadedFile(f"{number}.pdf", pdf, content_type="application/pdf")},
                format="multipart",
            )
            self.assertEqual(response.status_code, 201, response.data)
        fdr.refresh_from_db()
        self.assertTrue(fdr.reverification_required)
        self.assertEqual(fdr.status, FDR.Status.NEEDS_REVERIFICATION)
        response = self.client.get("/api/reconciliation/duplicates/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(item["type"] == "DOCUMENT_SHA256" for item in response.data))


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class ReconciliationTests(TestCase):
    def setUp(self):
        self.temp_media = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.temp_media.name)
        self.media_override.enable()
        self.institution = Institution.objects.create(code="BANK-TEST", name="Bank Test")
        self.admin = User.objects.create_user(username="ops-admin", password=TEST_PASSWORD, role=User.Role.ADMIN)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.portal = {}
        for number in ("PORTAL-1", "PORTAL-2", "PORTAL-3", "PORTAL-4", "PORTAL-5"):
            self.portal[number] = FDR.objects.create(
                institution=self.institution,
                created_by=self.admin,
                fdr_number=number,
                bank_name="Test Bank",
                principal_amount=Decimal("1000.00"),
                issue_date="2025-01-01",
                maturity_date="2030-01-01",
                status=FDR.Status.SUBMITTED,
            )

    def tearDown(self):
        self.media_override.disable()
        self.temp_media.cleanup()

    def test_bank_batch_classifies_all_reconciliation_outcomes_and_opens_cases(self):
        content = (
            "fdr_number,principal_amount,bank_name,issue_date,maturity_date\n"
            "PORTAL-1,1000.00,Test Bank,2025-01-01,2030-01-01\n"
            "PORTAL-2,999.00,Test Bank,2025-01-01,2030-01-01\n"
            "PORTAL-3,1000.00,Test Bank,2025-02-01,2030-01-01\n"
            "PORTAL-4,1000.00,Test Bank,2025-01-01,2030-01-01\n"
            "UNKNOWN-1,2000.00,Other Bank,2025-01-01,2030-01-01\n"
        ).encode()
        response = self.client.post(
            "/api/bank-batches/",
            {"file": SimpleUploadedFile("bank.csv", content, content_type="text/csv")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["classifications"]["MATCHED"], 2)
        self.assertEqual(response.data["classifications"]["AMOUNT_MISMATCH"], 1)
        self.assertEqual(response.data["classifications"]["DATE_MISMATCH"], 1)
        self.assertEqual(response.data["classifications"]["UNMAPPED_IN_PORTAL"], 1)
        self.assertEqual(response.data["classifications"]["MISSING_IN_BANK_BATCH"], 1)
        self.assertTrue(FDRCase.objects.filter(case_type="AMOUNT_MISMATCH").exists())
        self.assertTrue(FDRCase.objects.filter(case_type="UNMAPPED_IN_PORTAL").exists())
        self.assertTrue(self.portal["PORTAL-1"].evidence.filter(evidence_type="BANK_CONFIRMED").exists())

    def test_overdue_escalation_and_due_reminder_are_persisted(self):
        due_case = FDRCase.objects.create(
            case_type="TEST",
            title="Due soon",
            created_by=self.admin,
            owner=self.admin,
            due_at=timezone.now() + timedelta(hours=12),
            severity=FDRCase.Severity.MEDIUM,
        )
        overdue = FDRCase.objects.create(
            case_type="TEST",
            title="Overdue",
            created_by=self.admin,
            owner=self.admin,
            due_at=timezone.now() - timedelta(hours=1),
            severity=FDRCase.Severity.HIGH,
        )
        senior = User.objects.create_user(username="senior", password=TEST_PASSWORD, role=User.Role.SENIOR_OFFICER)
        CaseEscalationPolicy.objects.update_or_create(
            severity=FDRCase.Severity.HIGH,
            defaults={"after_minutes": 30, "escalate_to_role": User.Role.SENIOR_OFFICER, "enabled": True},
        )
        result = dispatch_case_alerts.run()
        self.assertGreaterEqual(result["reminders"], 1)
        self.assertGreaterEqual(result["escalations"], 1)
        self.assertTrue(CaseAlert.objects.filter(case=due_case, kind=CaseAlert.Kind.DUE_REMINDER).exists())
        overdue.refresh_from_db()
        self.assertEqual(overdue.owner_id, senior.pk)

    def test_duplicate_fdr_number_in_bank_batch_is_ambiguous(self):
        FDR.objects.create(
            institution=self.institution,
            created_by=self.admin,
            fdr_number="PORTAL 1",
            bank_name="Test Bank",
            principal_amount=Decimal("1000.00"),
            issue_date="2025-01-01",
            maturity_date="2030-01-01",
            status=FDR.Status.SUBMITTED,
        )
        content = b"fdr_number,principal_amount\nPORTAL-1,1000.00\n"
        response = self.client.post(
            "/api/bank-batches/",
            {"file": SimpleUploadedFile("ambiguous.csv", content, content_type="text/csv")},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["classifications"]["AMBIGUOUS"], 1)
        self.assertTrue(FDRCase.objects.filter(case_type="AMBIGUOUS").exists())

    def test_closing_case_requires_evidence_and_is_persisted(self):
        case = FDRCase.objects.create(case_type="TEST", title="Needs review", created_by=self.admin, owner=self.admin)
        response = self.client.patch(f"/api/cases/{case.public_id}/", {"status": "CLOSED"}, format="json")
        self.assertEqual(response.status_code, 400)
        response = self.client.patch(
            f"/api/cases/{case.public_id}/",
            {"status": "CLOSED", "closure_evidence": "Verified with the issuing bank."},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        case.refresh_from_db()
        self.assertEqual(case.status, FDRCase.Status.CLOSED)
        self.assertIsNotNone(case.closed_at)


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class Phase45Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.institution = Institution.objects.create(code="PH45", name="Phase 4/5 Institute")
        self.maker = User.objects.create_user(
            username="maker45", password=TEST_PASSWORD, role=User.Role.INSTITUTION_USER, institution=self.institution
        )
        self.reviewer = User.objects.create_user(
            username="reviewer45", password=TEST_PASSWORD, role=User.Role.SENIOR_OFFICER
        )
        self.approver = User.objects.create_user(username="approver45", password=TEST_PASSWORD, role=User.Role.ADMIN)
        self.fdr = FDR.objects.create(
            institution=self.institution,
            created_by=self.maker,
            fdr_number="REL-45",
            bank_name="Test Bank",
            principal_amount=Decimal("1000.00"),
            issue_date="2020-01-01",
            maturity_date=timezone.localdate() + timedelta(days=1),
            status=FDR.Status.SUBMITTED,
            verified_at=timezone.now(),
            verified_snapshot={"principal_amount": "1000.00"},
        )
        self.rule = CoverageRuleSet.objects.create(
            name="Phase 45 rules", required_amount=Decimal("1500.00"), active=True, checklist_items=[]
        )
        self.client.force_authenticate(self.maker)

    def test_daily_coverage_timeline_computes_shortfall_without_writing_during_read(self):
        FDREvidence.objects.create(
            fdr=self.fdr,
            evidence_type=FDREvidence.Kind.BANK_CONFIRMED,
            field_name="principal_amount",
            value="800.00",
            recorded_by=self.approver,
        )
        day = timezone.localdate()
        response = self.client.get(
            f"/api/coverage/timeline/?institution_id={self.institution.public_id}&start={day}&end={day}"
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["days"][0]["submitted"], "1000.00")
        self.assertEqual(response.data["days"][0]["bank_confirmed"], "800.00")
        self.assertEqual(response.data["days"][0]["shortfall"], "700.00")
        self.assertFalse(CoverageDay.objects.filter(institution=self.institution, day=day).exists())

    def test_release_maker_checker_and_discharge_workflow(self):
        created = self.client.post("/api/release-requests/", {"fdr_id": str(self.fdr.public_id)}, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        release_id = created.data["id"]
        self.client.force_authenticate(self.reviewer)
        review = self.client.post(f"/api/release-requests/{release_id}/action/", {"action": "review"}, format="json")
        self.assertEqual(review.status_code, 200, review.data)
        same_person = self.client.post(
            f"/api/release-requests/{release_id}/action/", {"action": "approve"}, format="json"
        )
        self.assertEqual(same_person.status_code, 400)
        self.client.force_authenticate(self.approver)
        approval = self.client.post(f"/api/release-requests/{release_id}/action/", {"action": "approve"}, format="json")
        self.assertEqual(approval.status_code, 200, approval.data)
        discharge = self.client.post(
            f"/api/release-requests/{release_id}/action/",
            {"action": "discharge", "evidence": "Bank discharge certificate 45"},
            format="json",
        )
        self.assertEqual(discharge.status_code, 200, discharge.data)
        close = self.client.post(f"/api/release-requests/{release_id}/action/", {"action": "close"}, format="json")
        self.assertEqual(close.status_code, 200, close.data)
        self.assertEqual(close.data["status"], ReleaseRequest.Status.CLOSED)

    def test_material_change_invalidates_active_release_request(self):
        response = self.client.post("/api/release-requests/", {"fdr_id": str(self.fdr.public_id)}, format="json")
        self.assertEqual(response.status_code, 201)
        release = ReleaseRequest.objects.get(public_id=response.data["id"])
        self.fdr.principal_amount = Decimal("1200.00")
        self.fdr.save()
        from .record_services import invalidate_release_requests

        invalidate_release_requests(self.fdr)
        release.refresh_from_db()
        self.assertEqual(release.status, ReleaseRequest.Status.INVALIDATED)

    def test_record_edit_endpoint_invalidates_approval_and_requester_cannot_review(self):
        self.client.force_authenticate(self.reviewer)
        staff_request = self.client.post("/api/release-requests/", {"fdr_id": str(self.fdr.public_id)}, format="json")
        self.assertEqual(staff_request.status_code, 201, staff_request.data)
        attempted_review = self.client.post(
            f"/api/release-requests/{staff_request.data['id']}/action/", {"action": "review"}, format="json"
        )
        self.assertEqual(attempted_review.status_code, 400)
        self.client.force_authenticate(self.maker)
        release_id = staff_request.data["id"]
        updated = self.client.patch(f"/api/fdrs/{self.fdr.public_id}/", {"principal_amount": "1100.00"}, format="json")
        self.assertEqual(updated.status_code, 200, updated.data)
        release = ReleaseRequest.objects.get(public_id=release_id)
        self.assertEqual(release.status, ReleaseRequest.Status.INVALIDATED)

    def test_dashboard_export_and_bitemporal_history_are_audited(self):
        self.client.force_authenticate(self.approver)
        dashboard = self.client.get("/api/dashboard/")
        self.assertEqual(dashboard.status_code, 200, dashboard.data)
        export = self.client.get("/api/reports/export/?export_format=csv")
        self.assertEqual(export.status_code, 200, export.content)
        pdf_export = self.client.get("/api/reports/export/?export_format=pdf")
        self.assertEqual(pdf_export.status_code, 200, pdf_export.content)
        self.assertTrue(AuditEvent.objects.filter(event_type="report.exported").exists())
        version = FDRVersion.objects.create(
            fdr=self.fdr,
            version_number=1,
            snapshot={"test": 1},
            changed_by=self.approver,
            valid_from=timezone.now() - timedelta(days=2),
            created_at=timezone.now() - timedelta(days=1),
        )
        known_on = timezone.now().isoformat().replace("+00:00", "Z")
        history = self.client.get(f"/api/fdrs/{self.fdr.public_id}/history/?known_on={known_on}")
        self.assertEqual(history.status_code, 200, history.data)
        self.assertTrue(any(row["version"] == version.version_number for row in history.data))

    def test_legacy_csv_dry_run_does_not_create_records(self):
        self.client.force_authenticate(self.approver)
        content = b"institution_id,fdr_number,bank_name,principal_amount,issue_date,maturity_date\n"
        content += f"{self.institution.public_id},LEGACY-45,Test Bank,500.00,2024-01-01,2030-01-01\n".encode()
        response = self.client.post(
            "/api/imports/legacy/",
            {"file": SimpleUploadedFile("legacy.csv", content), "dry_run": "true"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["valid"], 1)
        self.assertFalse(FDR.objects.filter(fdr_number="LEGACY-45").exists())
        committed = self.client.post(
            "/api/imports/legacy/",
            {"file": SimpleUploadedFile("legacy.csv", content), "dry_run": "false"},
            format="multipart",
        )
        self.assertEqual(committed.status_code, 201, committed.data)
        self.assertTrue(FDR.objects.filter(fdr_number="LEGACY-45").exists())

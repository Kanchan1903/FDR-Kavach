from datetime import timedelta

import pyotp
from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.utils import timezone
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .audit import record_event, verify_chain
from .models import User
from .permissions import CanVerifyAudit
from .serializers import LoginSerializer, MfaSerializer

STAFF_ROLES = {"REVIEWER", "APPROVER", "SENIOR_OFFICER", "ADMIN", "AUDITOR"}
DEMO_USERNAMES = {
    "demo-demo-001",
    "demo-demo-002",
    "demo-demo-003",
    "demo-senior",
    "demo-reviewer",
    "demo-approver",
    "demo-admin",
    "demo-auditor",
}


class CsrfView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        from django.middleware.csrf import get_token

        return Response({"csrfToken": get_token(request)})


class LoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"

    def post(self, request):
        data = LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        username = data.validated_data["username"]
        user = User.objects.filter(username__iexact=username).first()
        if user and user.is_locked:
            return Response({"detail": "Invalid credentials or account temporarily unavailable."}, status=423)
        authenticated = authenticate(request, username=username, password=data.validated_data["password"])
        if not authenticated:
            if user:
                user.failed_login_attempts += 1
                if user.failed_login_attempts >= settings.LOGIN_MAX_ATTEMPTS:
                    user.locked_until = timezone.now() + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
                user.save(update_fields=["failed_login_attempts", "locked_until"])
            record_event(actor=user, event_type="auth.login_failed", request=request, payload={"username": username})
            return Response({"detail": "Invalid credentials or account temporarily unavailable."}, status=401)
        authenticated.failed_login_attempts = 0
        authenticated.locked_until = None
        authenticated.save(update_fields=["failed_login_attempts", "locked_until"])
        must_mfa = authenticated.role in STAFF_ROLES or (
            authenticated.role == "INSTITUTION_USER" and settings.INSTITUTION_MFA_REQUIRED
        )
        if must_mfa:
            if not authenticated.totp_enabled or not authenticated.totp_secret:
                request.session.flush()
                request.session["mfa_enroll_user_id"] = authenticated.pk
                request.session.set_expiry(300)
                record_event(actor=authenticated, event_type="auth.mfa_enrollment_challenge", request=request)
                return Response({"mfa_enrollment_required": True, "detail": "MFA enrollment required."})
            request.session.flush()
            request.session["mfa_user_id"] = authenticated.pk
            request.session.set_expiry(300)
            record_event(actor=authenticated, event_type="auth.mfa_challenge", request=request)
            return Response({"mfa_required": True})
        login(request, authenticated)
        record_event(actor=authenticated, event_type="auth.login", request=request)
        return Response({"authenticated": True, "role": authenticated.role})


class MfaEnrollView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"

    def get(self, request):
        user = User.objects.filter(pk=request.session.get("mfa_enroll_user_id")).first()
        if not user:
            return Response({"detail": "Enrollment session expired."}, status=401)
        if not user.totp_secret:
            user.totp_secret = pyotp.random_base32()
            user.save(update_fields=["totp_secret"])
        uri = pyotp.TOTP(user.totp_secret).provisioning_uri(name=user.username, issuer_name=settings.TOTP_ISSUER)
        return Response({"secret": user.totp_secret, "provisioning_uri": uri})

    def post(self, request):
        data = MfaSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = User.objects.filter(pk=request.session.get("mfa_enroll_user_id")).first()
        if (
            not user
            or not user.totp_secret
            or not pyotp.TOTP(user.totp_secret).verify(data.validated_data["code"], valid_window=1)
        ):
            record_event(actor=user, event_type="auth.mfa_enrollment_failed", request=request)
            return Response({"detail": "Invalid or expired verification code."}, status=401)
        user.totp_enabled = True
        user.save(update_fields=["totp_enabled"])
        request.session.flush()
        login(request, user)
        record_event(actor=user, event_type="auth.mfa_enrolled", request=request)
        return Response({"authenticated": True, "role": user.role})


class MfaVerifyView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"

    def post(self, request):
        data = MfaSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        user = User.objects.filter(pk=request.session.get("mfa_user_id"), is_active=True).first()
        if (
            not user
            or not user.totp_secret
            or not pyotp.TOTP(user.totp_secret).verify(data.validated_data["code"], valid_window=1)
        ):
            record_event(actor=user, event_type="auth.mfa_failed", request=request)
            return Response({"detail": "Invalid or expired verification code."}, status=401)
        request.session.flush()
        login(request, user)
        record_event(actor=user, event_type="auth.login_mfa", request=request)
        return Response({"authenticated": True, "role": user.role})


class LogoutView(APIView):
    def post(self, request):
        record_event(actor=request.user, event_type="auth.logout", request=request)
        logout(request)
        return Response({"authenticated": False})


class MeView(APIView):
    def get(self, request):
        user = request.user
        return Response(
            {
                "id": str(user.public_id),
                "username": user.username,
                "role": user.role,
                "mfa_enabled": user.totp_enabled,
                "mfa_required": user.role in STAFF_ROLES or settings.INSTITUTION_MFA_REQUIRED,
                "institution": (
                    {"code": user.institution.code, "name": user.institution.name}
                    if user.institution_id
                    else None
                ),
            }
        )


class DemoTotpCodeView(APIView):
    """Expose a live TOTP only for seeded accounts in the local demo environment."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "login"

    def get(self, request):
        if not settings.DEBUG or not settings.DEMO_MODE:
            return Response({"detail": "Not found."}, status=404)
        username = request.query_params.get("username", "")
        if username not in DEMO_USERNAMES:
            return Response({"detail": "Not found."}, status=404)
        challenged_user_id = request.session.get("mfa_user_id")
        enrollment_user_id = request.session.get("mfa_enroll_user_id")
        if challenged_user_id:
            user = User.objects.filter(
                pk=challenged_user_id,
                username=username,
                is_active=True,
                totp_enabled=True,
            ).first()
        else:
            user = User.objects.filter(
                pk=enrollment_user_id,
                username=username,
                is_active=True,
                role__in=STAFF_ROLES,
                totp_enabled=False,
            ).first()
        if not user or not user.totp_secret:
            return Response({"detail": "Not found."}, status=404)
        totp = pyotp.TOTP(user.totp_secret)
        interval = totp.interval
        remaining = interval - (int(timezone.now().timestamp()) % interval)
        return Response({"code": totp.now(), "valid_for_seconds": remaining})


class AuditVerifyView(APIView):
    permission_classes = [IsAuthenticated, CanVerifyAudit]

    def get(self, request):
        return Response(verify_chain())

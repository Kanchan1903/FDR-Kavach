import pyotp
from django.conf import settings
from django.db import transaction

from .models import Institution, User

DEMO_PASSWORD = "DemoKavach2026!"
DEMO_INSTITUTIONS = (
    ("DEMO-001", "Demo Institute North"),
    ("DEMO-002", "Demo Institute Central"),
    ("DEMO-003", "Demo Institute South"),
)
DEMO_STAFF_ROLES = {
    User.Role.REVIEWER,
    User.Role.APPROVER,
    User.Role.SENIOR_OFFICER,
    User.Role.ADMIN,
    User.Role.AUDITOR,
}


@transaction.atomic
def ensure_demo_accounts():
    """Create missing public demo accounts without changing existing users."""
    if not settings.DEMO_MODE:
        raise RuntimeError("Demo accounts may only be seeded when DEMO_MODE=true.")

    institutions = {
        code: Institution.objects.get_or_create(code=code, defaults={"name": name})[0]
        for code, name in DEMO_INSTITUTIONS
    }
    definitions = (
        ("demo-demo-001", User.Role.INSTITUTION_USER, institutions["DEMO-001"]),
        ("demo-demo-002", User.Role.INSTITUTION_USER, institutions["DEMO-002"]),
        ("demo-demo-003", User.Role.INSTITUTION_USER, institutions["DEMO-003"]),
        ("demo-reviewer", User.Role.REVIEWER, None),
        ("demo-approver", User.Role.APPROVER, None),
        ("demo-senior", User.Role.SENIOR_OFFICER, None),
        ("demo-admin", User.Role.ADMIN, None),
        ("demo-auditor", User.Role.AUDITOR, None),
    )
    users = {}
    conflicts = []
    created_count = 0
    for username, role, institution in definitions:
        user, created = User.objects.get_or_create(
            username=username,
            defaults={"role": role, "institution": institution, "is_staff": role == User.Role.ADMIN},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            if role in DEMO_STAFF_ROLES:
                user.totp_secret = pyotp.random_base32()
                user.totp_enabled = True
            user.save(update_fields=["password", "totp_secret", "totp_enabled"])
            created_count += 1
        elif user.role != role or user.institution_id != getattr(institution, "id", None):
            conflicts.append(username)
        users[username] = user

    return institutions, users, created_count, conflicts


def demo_accounts_ready():
    """Return whether expected demo identities are present, active, and role-consistent."""
    if not settings.DEMO_MODE:
        return False
    institution_codes = [code for code, _ in DEMO_INSTITUTIONS]
    institutions = dict(Institution.objects.filter(code__in=institution_codes).values_list("code", "id"))
    usernames = (
        "demo-demo-001",
        "demo-demo-002",
        "demo-demo-003",
        "demo-reviewer",
        "demo-approver",
        "demo-senior",
        "demo-admin",
        "demo-auditor",
    )
    users = {user.username: user for user in User.objects.filter(username__in=usernames)}
    expected = (
        ("demo-demo-001", User.Role.INSTITUTION_USER, "DEMO-001"),
        ("demo-demo-002", User.Role.INSTITUTION_USER, "DEMO-002"),
        ("demo-demo-003", User.Role.INSTITUTION_USER, "DEMO-003"),
        ("demo-reviewer", User.Role.REVIEWER, None),
        ("demo-approver", User.Role.APPROVER, None),
        ("demo-senior", User.Role.SENIOR_OFFICER, None),
        ("demo-admin", User.Role.ADMIN, None),
        ("demo-auditor", User.Role.AUDITOR, None),
    )
    for username, role, institution_code in expected:
        user = users.get(username)
        expected_institution_id = institutions.get(institution_code) if institution_code else None
        if (
            user is None
            or not user.is_active
            or user.is_locked
            or user.role != role
            or user.institution_id != expected_institution_id
        ):
            return False
    return True

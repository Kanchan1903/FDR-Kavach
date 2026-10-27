from rest_framework.permissions import BasePermission


class CanVerifyAudit(BasePermission):
    allowed_roles = {"ADMIN", "AUDITOR"}

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.role in self.allowed_roles)

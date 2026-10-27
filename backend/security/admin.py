from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db import transaction

from .models import (
    FDR,
    AuditEvent,
    BankBatch,
    CaseAlert,
    CaseEscalationPolicy,
    CoverageDay,
    CoverageRuleSet,
    FDRCase,
    FDRDocument,
    FDREvidence,
    FDRVersion,
    ImportJob,
    Institution,
    ReconciliationResult,
    ReleaseRequest,
    User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ("FDR Kavach", {"fields": ("role", "institution", "totp_enabled", "failed_login_attempts", "locked_until")}),
    )


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("id", "occurred_at", "actor", "event_type", "event_hash")
    readonly_fields = tuple(field.name for field in AuditEvent._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "registration_number", "is_active")
    search_fields = ("code", "name", "registration_number")


class ReadOnlyRecordAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in (FDR, FDRDocument, FDRVersion, FDREvidence, BankBatch, ReconciliationResult, FDRCase, CaseAlert):
    admin.site.register(model, ReadOnlyRecordAdmin)

for model in (CoverageDay, ReleaseRequest, ImportJob):
    admin.site.register(model, ReadOnlyRecordAdmin)


@admin.register(CoverageRuleSet)
class CoverageRuleSetAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "required_amount",
        "source",
        "effective_from",
        "approving_authority",
        "active",
        "updated_at",
    )
    readonly_fields = ("updated_at",)

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            if obj.active:
                CoverageRuleSet.objects.exclude(pk=obj.pk).update(active=False)
            obj.updated_by = request.user
            super().save_model(request, obj, form, change)


@admin.register(CaseEscalationPolicy)
class CaseEscalationPolicyAdmin(admin.ModelAdmin):
    list_display = ("severity", "after_minutes", "escalate_to_role", "enabled")

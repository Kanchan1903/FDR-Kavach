from django.db import migrations


def create_default_policies(apps, schema_editor):
    Policy = apps.get_model("security", "CaseEscalationPolicy")
    defaults = (
        ("LOW", 10080, "SENIOR_OFFICER"),
        ("MEDIUM", 4320, "SENIOR_OFFICER"),
        ("HIGH", 1440, "SENIOR_OFFICER"),
        ("CRITICAL", 60, "ADMIN"),
    )
    for severity, after_minutes, role in defaults:
        Policy.objects.get_or_create(
            severity=severity,
            defaults={"after_minutes": after_minutes, "escalate_to_role": role, "enabled": True},
        )


class Migration(migrations.Migration):
    dependencies = [("security", "0004_fdr_verified_at_fdr_verified_snapshot")]
    operations = [migrations.RunPython(create_default_policies, migrations.RunPython.noop)]

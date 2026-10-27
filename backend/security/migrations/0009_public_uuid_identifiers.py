import uuid

from django.db import migrations, models

MODEL_NAMES = [
    "auditevent",
    "bankbatch",
    "casealert",
    "coverageruleset",
    "fdr",
    "fdrcase",
    "fdrdocument",
    "importjob",
    "institution",
    "reconciliationresult",
    "releaserequest",
    "user",
]


def populate_public_ids(apps, schema_editor):
    for model_name in MODEL_NAMES:
        model = apps.get_model("security", model_name)
        for row in model.objects.filter(public_id__isnull=True).iterator():
            model.objects.filter(pk=row.pk).update(public_id=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [("security", "0008_coverageruleset_uniq_active_coverage_rules")]
    operations = []
    for name in MODEL_NAMES:
        operations.append(
            migrations.AddField(
                model_name=name,
                name="public_id",
                field=models.UUIDField(default=uuid.uuid4, editable=False, null=True),
            )
        )
    operations.append(migrations.RunPython(populate_public_ids, migrations.RunPython.noop))
    for name in MODEL_NAMES:
        operations.append(
            migrations.AlterField(
                model_name=name,
                name="public_id",
                field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True),
            )
        )

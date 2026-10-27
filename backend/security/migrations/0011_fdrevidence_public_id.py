import uuid

from django.db import migrations, models


def populate(apps, schema_editor):
    model = apps.get_model("security", "FDREvidence")
    for row in model.objects.filter(public_id__isnull=True).iterator():
        model.objects.filter(pk=row.pk).update(public_id=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [("security", "0010_alter_bankbatch_file_alter_fdrdocument_file")]
    operations = [
        migrations.AddField(
            model_name="fdrevidence",
            name="public_id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, null=True),
        ),
        migrations.RunPython(populate, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="fdrevidence",
            name="public_id",
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True),
        ),
    ]

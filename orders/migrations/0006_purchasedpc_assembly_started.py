from django.db import migrations, models
from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Cast


def record_existing_assembly(apps, schema_editor):
    pcs = apps.get_model("orders", "PurchasedPC").objects.using(schema_editor.connection.alias)
    staff_actions = apps.get_model("accounts", "StaffAction")
    checks = apps.get_model("orders", "QualityCheck")
    proposals = apps.get_model("orders", "SubstitutionProposal")
    started_states = ("assembling", "quality_checking", "corrective_work", "quality_passed")
    pcs.filter(
        Q(status__in=started_states)
        | Exists(staff_actions.objects.filter(
            action_type="pc_assembly_started",
            object_ref=Cast(OuterRef("pk"), models.CharField()),
        ))
        | Exists(checks.objects.filter(purchased_pc_id=OuterRef("pk")))
        | Exists(proposals.objects.filter(purchased_pc_id=OuterRef("pk"), previous_pc_status__in=started_states))
    ).update(assembly_started=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_customer_customer_email_case_insensitive_unique"),
        ("orders", "0005_substitutionproposal_performance_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="purchasedpc",
            name="assembly_started",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(record_existing_assembly, migrations.RunPython.noop),
    ]

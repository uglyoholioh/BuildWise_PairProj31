from django.db import migrations, models


def mark_cpus_requiring_cooling(apps, schema_editor):
    Component = apps.get_model("catalogue", "Component")
    Configuration = apps.get_model("catalogue", "Configuration")
    database = schema_editor.connection.alias
    Component.objects.using(database).filter(
        category="cpu", model__iexact="Ryzen 7 7800X3D",
    ).update(requires_cpu_cooler=True)
    presets = Configuration.objects.using(database).filter(
        status="published", items__component__category="cpu",
        items__component__requires_cpu_cooler=True,
    ).distinct()
    for preset in presets:
        if not preset.items.using(database).filter(component__category="cooler").exists():
            preset.status = "draft"
            preset.save(using=database, update_fields=("status",))


class Migration(migrations.Migration):
    dependencies = [
        ("catalogue", "0006_accessory_image_accessory_source_url_component_image_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="component", name="image_file",
            field=models.ImageField(blank=True, upload_to="catalogue/components/"),
        ),
        migrations.AddField(
            model_name="accessory", name="image_file",
            field=models.ImageField(blank=True, upload_to="catalogue/accessories/"),
        ),
        migrations.AddField(
            model_name="configuration", name="image_file",
            field=models.ImageField(blank=True, upload_to="catalogue/presets/"),
        ),
        migrations.AddField(
            model_name="component", name="requires_cpu_cooler",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_cpus_requiring_cooling, migrations.RunPython.noop),
    ]

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from catalogue.models import Accessory, Component, Configuration, ConfigurationItem


class Command(BaseCommand):
    help = "Load the sourced BuildWise demonstration catalogue. Safe to rerun."

    @transaction.atomic
    def handle(self, *args, **options):
        data_path = Path(__file__).resolve().parents[2] / "data" / "demo_catalogue.json"
        data = json.loads(data_path.read_text())
        components = {}
        for row in data["components"]:
            identity = {key: row[key] for key in ("category", "brand", "model")}
            components[row["key"]], _ = Component.objects.get_or_create(
                **identity,
                defaults={key: value for key, value in row.items() if key != "key"},
            )
        for row in data["accessories"]:
            Accessory.objects.get_or_create(
                name=row["name"], defaults={key: value for key, value in row.items() if key != "name"}
            )
        for row in data["configurations"]:
            configuration, _ = Configuration.objects.update_or_create(
                name=row["name"],
                defaults={key: value for key, value in row.items() if key not in ("name", "components")},
            )
            for key in row["components"]:
                ConfigurationItem.objects.update_or_create(
                    configuration=configuration,
                    component=components[key],
                    defaults={"quantity": 2 if components[key].category == Component.Category.MEMORY else 1},
                )
        self.stdout.write(self.style.SUCCESS("Demo catalogue loaded; presets remain drafts until staff review."))

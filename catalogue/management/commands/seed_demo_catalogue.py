import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from catalogue.models import Accessory, Component, Configuration, ConfigurationItem
from recommendations.models import Game, GameSettingSupport


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
            if not components[row["key"]].image and row.get("image"):
                components[row["key"]].image = row["image"]
                components[row["key"]].save(update_fields=["image"])
        for row in data["accessories"]:
            accessory, _ = Accessory.objects.get_or_create(
                name=row["name"], defaults={key: value for key, value in row.items() if key != "name"}
            )
            if not accessory.image and row.get("image"):
                accessory.image = row["image"]
                accessory.save(update_fields=["image"])
        for row in data["configurations"]:
            configuration, created = Configuration.objects.get_or_create(
                name=row["name"],
                defaults={key: value for key, value in row.items() if key not in ("name", "components")},
            )
            if not created:
                continue
            for key in row["components"]:
                ConfigurationItem.objects.create(
                    configuration=configuration,
                    component=components[key],
                    quantity=1,
                )
        for row in data["games"]:
            game, _ = Game.objects.get_or_create(slug=row["slug"], defaults={"name": row["name"]})
            GameSettingSupport.objects.get_or_create(game=game, resolution="1920x1080", graphics_preset="high")
        self.stdout.write(self.style.SUCCESS("Demo catalogue and game settings loaded. New presets are drafts."))

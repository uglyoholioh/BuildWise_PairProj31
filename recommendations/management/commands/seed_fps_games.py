from django.core.management.base import BaseCommand
from django.db import transaction

from recommendations.ml.schema import GAMES, RESOLUTIONS, GRAPHICS_PRESETS
from recommendations.models import Game, GameSettingSupport


class Command(BaseCommand):
    help = 'Add supported synthetic FPS prototype games/settings without changing existing records.'

    @transaction.atomic
    def handle(self, *args, **options):
        for slug, name in GAMES.items():
            game, _ = Game.objects.get_or_create(slug=slug, defaults={'name': name})
            for resolution in RESOLUTIONS:
                for preset in GRAPHICS_PRESETS:
                    GameSettingSupport.objects.get_or_create(game=game, resolution=resolution, graphics_preset=preset)
        self.stdout.write(self.style.SUCCESS('Synthetic FPS prototype game settings are available.'))

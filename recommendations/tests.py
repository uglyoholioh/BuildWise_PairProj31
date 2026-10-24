from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Customer
from catalogue.models import Component, Configuration, ConfigurationItem
from .forms import QuestionnaireForm
from .models import Game, GameSettingSupport, QuestionnaireGameSetting, QuestionnaireResponse


class PresetGamingDetailTests(TestCase):
    def setUp(self):
        component = Component.objects.create(
            category=Component.Category.CPU,
            brand="Example",
            model="CPU",
            socket="AM5",
            price=Decimal("100.00"),
        )
        self.configuration = Configuration.objects.create(
            name="Test Build", status=Configuration.Status.PUBLISHED
        )
        ConfigurationItem.objects.create(configuration=self.configuration, component=component, quantity=1)
        self.game = Game.objects.create(name="Example Game", slug="example-game")

    def test_guest_without_questionnaire_is_invited_to_questionnaire(self):
        response = self.client.get(reverse("catalogue:preset_detail", args=[self.configuration.pk]))

        self.assertContains(response, "Complete the questionnaire")
        self.assertContains(response, "Socket: AM5")

    def test_customer_sees_targets_and_unsupported_estimate_state(self):
        user = User.objects.create_user(username="buyer", password="safe-pass-123")
        customer = Customer.objects.create(user=user, name="Buyer", email="buyer@example.com")
        response = QuestionnaireResponse.objects.create(
            customer=customer, perf_level="balanced", max_spend=Decimal("1000.00")
        )
        QuestionnaireGameSetting.objects.create(
            response=response,
            game=self.game,
            fps_goal=60,
            resolution="1920x1080",
            graphics_preset="high",
        )
        self.client.force_login(user)

        page = self.client.get(reverse("catalogue:preset_detail", args=[self.configuration.pk]))

        self.assertContains(page, "Example Game")
        self.assertContains(page, "60 FPS")
        self.assertContains(page, "Unavailable for these settings")

    def test_supported_inputs_show_estimate_unavailable_until_predictor_exists(self):
        user = User.objects.create_user(username="buyer", password="safe-pass-123")
        customer = Customer.objects.create(user=user, name="Buyer", email="buyer@example.com")
        response = QuestionnaireResponse.objects.create(
            customer=customer, perf_level="balanced", max_spend=Decimal("1000.00")
        )
        QuestionnaireGameSetting.objects.create(
            response=response,
            game=self.game,
            fps_goal=60,
            resolution="1920x1080",
            graphics_preset="high",
        )
        GameSettingSupport.objects.create(
            game=self.game, resolution="1920x1080", graphics_preset="high"
        )
        self.client.force_login(user)

        page = self.client.get(reverse("catalogue:preset_detail", args=[self.configuration.pk]))

        self.assertContains(page, "Estimate unavailable")


class QuestionnaireTests(TestCase):
    def setUp(self):
        self.game = Game.objects.create(name="Example Game", slug="example-game")
        GameSettingSupport.objects.create(
            game=self.game, resolution="1920x1080", graphics_preset="high"
        )
        GameSettingSupport.objects.create(
            game=self.game, resolution="2560x1440", graphics_preset="ultra"
        )

    def test_guest_saves_selected_game_settings_and_session_for_detail_page(self):
        response = self.client.post(
            reverse("recommendations:questionnaire"),
            {
                "games": [str(self.game.pk)],
                "perf_level": "balanced",
                "max_spend": "1200.00",
                f"game_{self.game.pk}_fps_goal": "90",
                f"game_{self.game.pk}_resolution": "2560x1440",
                f"game_{self.game.pk}_graphics_preset": "ultra",
            },
        )

        self.assertRedirects(response, reverse("recommendations:recommendations"))
        questionnaire = QuestionnaireResponse.objects.get(customer__isnull=True)
        setting = questionnaire.game_settings.get()
        self.assertEqual(setting.fps_goal, 90)
        self.assertEqual(setting.resolution, "2560x1440")
        self.assertEqual(setting.graphics_preset, "ultra")
        self.assertEqual(self.client.session["questionnaire_response_id"], questionnaire.pk)

        component = Component.objects.create(
            category=Component.Category.CPU, brand="Example", model="CPU", socket="AM5", price="100.00"
        )
        config = Configuration.objects.create(name="Visible Build", status=Configuration.Status.PUBLISHED)
        ConfigurationItem.objects.create(configuration=config, component=component, quantity=1)
        detail = self.client.get(reverse("catalogue:preset_detail", args=[config.pk]))
        self.assertContains(detail, "Example Game")
        self.assertContains(detail, "90 FPS")

    def test_rejects_unsupported_resolution_and_graphics_pair(self):
        form = QuestionnaireForm({
            "games": [str(self.game.pk)],
            "perf_level": "balanced",
            "max_spend": "1200.00",
            f"game_{self.game.pk}_fps_goal": "60",
            f"game_{self.game.pk}_resolution": "1920x1080",
            f"game_{self.game.pk}_graphics_preset": "ultra",
        })

        self.assertFalse(form.is_valid())
        self.assertIn(f"game_{self.game.pk}_graphics_preset", form.errors)

    def test_rejects_more_than_three_games(self):
        games = [self.game]
        for index in range(3):
            game = Game.objects.create(name=f"Game {index}", slug=f"game-{index}")
            GameSettingSupport.objects.create(
                game=game, resolution="1920x1080", graphics_preset="high"
            )
            games.append(game)

        form = QuestionnaireForm({
            "games": [str(game.pk) for game in games],
            "perf_level": "balanced",
            "max_spend": "1000.00",
        })

        self.assertFalse(form.is_valid())
        self.assertIn("no more than three games", str(form.errors).lower())

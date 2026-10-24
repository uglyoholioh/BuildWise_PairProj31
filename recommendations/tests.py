from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Customer
from catalogue.models import Component, Configuration, ConfigurationItem
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

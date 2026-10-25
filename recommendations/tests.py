from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import Customer, Employee
from catalogue.models import AssemblyFeeSetting, Component, Configuration, ConfigurationItem
from inventory.models import StockAdjustment
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


class RecommendationProfileTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="manager", password="safe-pass-123")
        self.employee = Employee.objects.create(user=user, role=Employee.Role.MANAGER)
        AssemblyFeeSetting.objects.create(fee_amount=Decimal("25.00"), updated_by=self.employee)
        self.game = Game.objects.create(name="Example Game", slug="example-game")
        GameSettingSupport.objects.create(
            game=self.game, resolution="1920x1080", graphics_preset="high"
        )

    def save_needs(self, budget, target=60):
        response = QuestionnaireResponse.objects.create(
            perf_level="balanced", max_spend=Decimal(str(budget))
        )
        QuestionnaireGameSetting.objects.create(
            response=response,
            game=self.game,
            fps_goal=target,
            resolution="1920x1080",
            graphics_preset="high",
        )
        session = self.client.session
        session["questionnaire_response_id"] = response.pk
        session.save()
        return response

    def create_build(self, name, tier, gpu_price=Decimal("200.00")):
        specs = {
            Component.Category.CPU: {"socket": "AM5"},
            Component.Category.MOTHERBOARD: {"socket": "AM5", "memory_type": "DDR5", "form_factor": "ATX"},
            Component.Category.MEMORY: {"memory_type": "DDR5"},
            Component.Category.GPU: {"ml_tier": tier},
            Component.Category.STORAGE: {},
            Component.Category.POWER_SUPPLY: {},
            Component.Category.CASE: {"form_factor": "ATX"},
        }
        config = Configuration.objects.create(name=name, status=Configuration.Status.PUBLISHED)
        for category, fields in specs.items():
            component = Component.objects.create(
                category=category,
                brand="Example",
                model=f"{name} {category}",
                price=gpu_price if category == Component.Category.GPU else Decimal("100.00"),
                **fields,
            )
            ConfigurationItem.objects.create(configuration=config, component=component, quantity=1)
            StockAdjustment.objects.create(
                item_type="component", component=component, delta=2,
                reason="Opening stock", created_by=self.employee,
            )
        return config

    def test_within_budget_profiles_are_ranked_and_same_build_labels_are_combined(self):
        self.save_needs("1000.00")
        build = self.create_build("High Build", "high")
        self.create_build("Unsupported Tier", "unlisted")

        response = self.client.get(reverse("recommendations:recommendations"))

        self.assertContains(response, "Lowest cost meeting all goals")
        self.assertContains(response, "Best performance within budget")
        self.assertContains(response, "Best value")
        self.assertContains(response, "High Build")
        self.assertEqual(len(response.context["profiles"]), 1)
        self.assertEqual(response.context["profiles"][0]["row"]["configuration"], build)
        self.assertEqual(len(response.context["profiles"][0]["labels"]), 3)

    def test_alternatives_show_shortfalls_and_cheapest_qualifying_build_extra_cost(self):
        self.save_needs("750.00")
        affordable = self.create_build("Affordable Build", "medium", gpu_price=Decimal("100.00"))
        qualifying = self.create_build("Qualifying Build", "high", gpu_price=Decimal("200.00"))

        response = self.client.get(reverse("recommendations:recommendations"))

        self.assertContains(response, "Affordable alternatives")
        self.assertContains(response, "short by 9 FPS")
        self.assertContains(response, affordable.name)
        self.assertContains(response, qualifying.name)
        self.assertEqual(response.context["over_budget"]["configuration"], qualifying)
        self.assertEqual(response.context["extra_cost"], Decimal("75.00"))

    def test_no_supported_available_compatible_results_explains_next_step(self):
        self.save_needs("1000.00")
        self.create_build("Unknown Tier Build", "tier-not-mapped")

        response = self.client.get(reverse("recommendations:recommendations"))

        self.assertContains(response, "No available, compatible published build")
        self.assertContains(response, "Revise gaming needs")

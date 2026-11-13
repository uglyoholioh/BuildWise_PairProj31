from decimal import Decimal

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Customer, Employee
from catalogue.models import AssemblyFeeSetting, Component, Configuration, ConfigurationItem
from inventory.models import StockAdjustment
from .forms import QuestionnaireForm
from .models import Game, GameSettingSupport, QuestionnaireGameSetting, QuestionnaireResponse
from .predictor import load_fps_model, predict_fps


class PresetGamingDetailTests(TestCase):
    def setUp(self):
        component = Component.objects.create(
            category=Component.Category.CPU,
            brand="Example",
            model="Ryzen 5 7500F",
            socket="AM5",
            price=Decimal("100.00"),
        )
        self.configuration = Configuration.objects.create(
            name="Test Build", status=Configuration.Status.PUBLISHED
        )
        ConfigurationItem.objects.create(configuration=self.configuration, component=component, quantity=1)
        self.game = Game.objects.create(name="Example Game", slug="cyberpunk-2077")

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

    def test_supported_inputs_show_trained_synthetic_estimate(self):
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
        gpu = Component.objects.create(
            category=Component.Category.GPU,
            brand="Example",
            model="GeForce RTX 4070 12GB",
            ml_tier="high",
            price=Decimal("200.00"),
        )
        ConfigurationItem.objects.create(configuration=self.configuration, component=gpu, quantity=1)
        self.client.force_login(user)

        page = self.client.get(reverse("catalogue:preset_detail", args=[self.configuration.pk]))

        self.assertContains(page, "103 FPS (estimate)")


class QuestionnaireTests(TestCase):
    def setUp(self):
        self.game = Game.objects.create(name="Example Game", slug="cyberpunk-2077")
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
        self.game = Game.objects.create(name="Example Game", slug="cyberpunk-2077")
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
                model=("Ryzen 5 7500F" if category == Component.Category.CPU else
                       {"high": "GeForce RTX 4070 12GB", "medium": "GeForce RTX 3060 12GB"}.get(tier, "Unsupported GPU")
                       if category == Component.Category.GPU else f"{name} {category}"),
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
        self.assertContains(response, "short by 8 FPS")
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

    def test_trained_predictions_change_lowest_cost_recommendation_for_different_games(self):
        questionnaire = self.save_needs("1000.00")
        affordable = self.create_build("Affordable Build", "medium", gpu_price=Decimal("100.00"))
        powerful = self.create_build("Powerful Build", "high")
        page = self.client.get(reverse("recommendations:recommendations"))
        self.assertEqual(page.context["profiles"][0]["row"]["configuration"], powerful)
        self.assertContains(page, "synthetic data, not measured benchmarks")

        game = Game.objects.create(name="Counter-Strike 2", slug="counter-strike-2")
        GameSettingSupport.objects.create(game=game, resolution="1920x1080", graphics_preset="high")
        questionnaire.game_settings.update(game=game)
        page = self.client.get(reverse("recommendations:recommendations"))
        self.assertEqual(page.context["profiles"][0]["row"]["configuration"], affordable)
        self.assertIn("Lowest cost meeting all goals", page.context["profiles"][0]["labels"])

    def test_same_trained_estimate_is_used_in_configurator_comparison_and_preset(self):
        self.save_needs("1000.00")
        build = self.create_build("Supported Build", "high")
        page = self.client.get(reverse("catalogue:configure"), {"preset": build.pk})
        self.assertContains(page, "103 FPS (estimate)")
        page = self.client.get(reverse("catalogue:preset_detail", args=[build.pk]))
        self.assertContains(page, "103 FPS (estimate)")
        session = self.client.session
        session["comparison_items"] = [{"kind": "preset", "id": build.pk}]
        session.save()
        page = self.client.get(reverse("recommendations:compare"))
        self.assertEqual(page.context["game_rows"][0]["values"], [{"fps": 103, "shortfall": 0}])
        self.assertContains(page, "Meets target")


class BuildComparisonTests(TestCase):
    def test_incomplete_build_does_not_show_a_partial_price_as_pc_price(self):
        user = User.objects.create_user(username="manager", password="safe-pass-123")
        employee = Employee.objects.create(user=user, role=Employee.Role.MANAGER)
        AssemblyFeeSetting.objects.create(fee_amount=Decimal("25.00"), updated_by=employee)
        cpu = Component.objects.create(
            category=Component.Category.CPU, brand="Example", model="CPU", socket="AM5", price="100.00"
        )
        configuration = Configuration.objects.create(
            name="Incomplete build", status=Configuration.Status.PUBLISHED
        )
        ConfigurationItem.objects.create(configuration=configuration, component=cpu)
        session = self.client.session
        session["comparison_items"] = [{"kind": "preset", "id": configuration.pk}]
        session.save()

        response = self.client.get(reverse("recommendations:compare"))

        self.assertEqual(response.context["overview_rows"][0]["values"], ["Unavailable"])


class TrainedPredictorTests(SimpleTestCase):
    def setUp(self):
        self.cpu = Component(category='cpu', model='Ryzen 5 7500F')
        self.gpu = Component(category='gpu', model='GeForce RTX 4070 12GB')
        self.items = [ConfigurationItem(component=self.cpu, quantity=1), ConfigurationItem(component=self.gpu, quantity=1)]
        self.setting = QuestionnaireGameSetting(game=Game(slug='cyberpunk-2077'), resolution='1920x1080', graphics_preset='high')
        load_fps_model.cache_clear()
        self.addCleanup(load_fps_model.cache_clear)

    def test_loaded_pipeline_changes_predictions_for_game_hardware_and_settings(self):
        baseline = predict_fps(self.items, self.setting)
        self.assertEqual(baseline, 103)
        self.setting.resolution = '3840x2160'
        self.assertLess(predict_fps(self.items, self.setting), baseline)
        self.setting.resolution = '1920x1080'
        self.setting.game.slug = 'counter-strike-2'
        cpu_bound = predict_fps(self.items, self.setting)
        self.assertGreater(cpu_bound, baseline)
        self.cpu.model = 'Ryzen 7 7800X3D'
        upgraded = predict_fps(self.items, self.setting)
        self.assertGreater(upgraded, cpu_bound)
        self.gpu.model = 'GeForce RTX 3060 12GB'
        self.assertLess(predict_fps(self.items, self.setting), upgraded)

    def test_unknown_or_missing_inputs_return_no_estimate_without_tier_fallback(self):
        for obj, attr, value in [(self.cpu, 'model', 'Unknown CPU'), (self.gpu, 'model', 'Unknown GPU'),
                                 (self.setting.game, 'slug', 'unknown-game'), (self.setting, 'resolution', '1280x720'),
                                 (self.setting, 'graphics_preset', 'ray-tracing')]:
            with self.subTest(field=attr, value=value):
                original = getattr(obj, attr)
                setattr(obj, attr, value)
                self.assertIsNone(predict_fps(self.items, self.setting))
                setattr(obj, attr, original)
        self.assertIsNone(predict_fps(self.items[:1], self.setting))
        self.items[1].quantity = 2
        self.assertIsNone(predict_fps(self.items, self.setting))

    def test_missing_artifact_fails_gracefully_and_successful_load_is_cached(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        with TemporaryDirectory() as directory, patch('recommendations.predictor.MODEL_PATH', Path(directory) / 'missing.joblib'):
            with self.assertLogs('recommendations.predictor', level='ERROR'):
                self.assertIsNone(predict_fps(self.items, self.setting))
        load_fps_model.cache_clear()
        first = load_fps_model()
        self.assertIs(first, load_fps_model())
        self.assertEqual(load_fps_model.cache_info().misses, 1)

    def test_evaluated_artifact_matches_disjoint_held_out_data(self):
        import csv
        import hashlib
        import json
        from recommendations.ml.schema import FEATURES, MODEL_PATH
        directory = MODEL_PATH.parent
        report = json.loads((directory / 'evaluation.json').read_text())
        data = directory / 'synthetic_fps.csv'
        self.assertEqual(hashlib.sha256(data.read_bytes()).hexdigest(), report['dataset_sha256'])
        splits = {key: set(value['hardware_pairs']) for key, value in report['splits'].items()}
        self.assertFalse(splits['train'] & splits['test'] or splits['train'] & splits['validation'] or splits['validation'] & splits['test'])
        with data.open() as stream:
            rows = [row for row in csv.DictReader(stream) if row['cpu'] + '|' + row['gpu'] in splits['test']]
        self.assertEqual(len(rows), report['splits']['test']['rows'])
        predictions = load_fps_model()['pipeline'].predict([{key: row[key] for key in FEATURES} for row in rows])
        mae = sum(abs(float(row['average_fps']) - value) for row, value in zip(rows, predictions)) / len(rows)
        self.assertAlmostEqual(mae, report['test_mae_fps'])
        self.assertLess(mae, report['baseline_test_mae_fps'])

"""Optional browser smoke test: install Playwright and a local Chromium first."""
import shutil
import unittest

from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from accounts.models import Customer
from catalogue.models import Component
from .models import Game, GameSettingSupport, QuestionnaireResponse, ShortlistItem
from .test_accessories import PurchaseHistoryFixture

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(sync_playwright and shutil.which('chromium'), 'Requires Playwright and Chromium')
class QuestionnaireBrowserTests(StaticLiveServerTestCase):
    def test_search_selection_and_two_step_submission(self):
        first = Game.objects.create(name='Alpha Game', slug='alpha')
        second = Game.objects.create(name='Beta Game', slug='beta')
        for game in (first, second):
            GameSettingSupport.objects.create(game=game, resolution='1920x1080', graphics_preset='high')
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which('chromium'), headless=True,
                                                 args=['--no-sandbox'])
            page = browser.new_page()
            page.goto(self.live_server_url + '/questionnaire')
            page.get_by_label('Maximum PC budget').fill('1500')
            page.get_by_label('Alpha Game', exact=True).check()
            page.get_by_label('Search supported games').fill('Beta')
            self.assertFalse(page.locator('.game-option', has_text='Alpha Game').is_visible())
            self.assertTrue(page.locator('.game-option', has_text='Beta Game').is_visible())
            self.assertTrue(page.get_by_label('Alpha Game', exact=True).is_checked())
            page.get_by_role('button', name='Update games and settings').click()
            page.wait_for_load_state()
            self.assertEqual(page.get_by_label('FPS target', exact=True).input_value(), '60')
            page.get_by_label('FPS target', exact=True).fill('120')
            page.get_by_label('Beta Game', exact=True).check()
            page.get_by_role('button', name='Update games and settings').click()
            page.wait_for_load_state()
            self.assertEqual(page.locator(f'#id_game_{first.pk}_fps_goal').input_value(), '120')
            self.assertEqual(page.locator(f'#id_game_{second.pk}_fps_goal').input_value(), '60')
            page.get_by_role('button', name='See recommendations').click()
            page.wait_for_url('**/recommendations')
            browser.close()
        self.assertEqual(QuestionnaireResponse.objects.count(), 1)
        self.assertEqual(QuestionnaireResponse.objects.get().game_settings.count(), 2)


@unittest.skipUnless(sync_playwright and shutil.which('chromium'), 'Requires Playwright and Chromium')
class ShortlistBrowserTests(StaticLiveServerTestCase):
    def test_guest_signs_in_saves_named_drafts_reopens_compares_and_removes(self):
        user = User.objects.create_user("shortlistbuyer", password="BrowserTestPass!482")
        Customer.objects.create(user=user, name="Buyer", email="buyer@example.test")
        cpu = Component.objects.create(category=Component.Category.CPU, brand="Example", model="Draft CPU", socket="AM5", price=100)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which('chromium'), headless=True,
                                                 args=['--no-sandbox'])
            page = browser.new_page()
            page.goto(self.live_server_url + reverse("catalogue:configure"))
            page.get_by_label("Build name", exact=True).fill("Guest draft")
            page.get_by_label("CPU", exact=True).select_option(str(cpu.pk))
            page.get_by_role("button", name="Save to shortlist", exact=True).click()
            page.get_by_label("Username", exact=True).fill("shortlistbuyer")
            page.get_by_label("Password", exact=True).fill("BrowserTestPass!482")
            page.get_by_role("button", name="Sign in", exact=True).click()
            self.assertEqual(page.get_by_label("Saved build name:", exact=True).input_value(), "Guest draft")
            page.get_by_label("Saved build name:", exact=True).fill("Quiet PC option")
            page.get_by_role("button", name="Save named build", exact=True).click()
            self.assertTrue(page.get_by_role("heading", name="Quiet PC option", exact=True).is_visible())
            page.get_by_role("button", name="Reopen", exact=True).click()
            self.assertEqual(page.get_by_label("Build name", exact=True).input_value(), "Quiet PC option")
            self.assertEqual(page.get_by_label("CPU", exact=True).input_value(), str(cpu.pk))
            self.assertTrue(page.get_by_role("heading", name="Needs attention", exact=True).is_visible())
            page.get_by_label("Build name", exact=True).fill("Budget PC option")
            page.get_by_role("button", name="Save to shortlist", exact=True).click()
            page.get_by_role("button", name="Save named build", exact=True).click()
            quiet = page.locator("article", has=page.get_by_role("heading", name="Quiet PC option", exact=True))
            quiet.get_by_role("button", name="Add to comparison", exact=True).click()
            page.get_by_role("link", name="Choose from my shortlist", exact=True).click()
            budget = page.locator("article", has=page.get_by_role("heading", name="Budget PC option", exact=True))
            budget.get_by_role("button", name="Add to comparison", exact=True).click()
            self.assertIn("Quiet PC option", page.locator("table thead").inner_text())
            self.assertIn("Budget PC option", page.locator("table thead").inner_text())
            page.get_by_role("link", name="Choose from my shortlist", exact=True).click()
            quiet = page.locator("article", has=page.get_by_role("heading", name="Quiet PC option", exact=True))
            quiet.get_by_role("button", name="Remove saved build", exact=True).click()
            self.assertEqual(page.get_by_role("heading", name="Quiet PC option", exact=True).count(), 0)
            self.assertTrue(page.get_by_role("heading", name="Budget PC option", exact=True).is_visible())
            browser.close()
        self.assertEqual(list(ShortlistItem.objects.values_list("name", flat=True)), ["Budget PC option"])


@unittest.skipUnless(sync_playwright and shutil.which('chromium'), 'Requires Playwright and Chromium')
class AccessorySuggestionBrowserTests(PurchaseHistoryFixture, StaticLiveServerTestCase):
    def test_guest_sees_purchase_based_suggestions_and_adds_accessory(self):
        for unused in range(3):
            self.purchase()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which('chromium'), headless=True,
                                                 args=['--no-sandbox'])
            page = browser.new_page()
            page.goto(self.live_server_url + reverse("catalogue:preset_detail", args=[self.preset.pk]))
            card = page.locator(f"#suggested-accessory-{self.mouse.pk}")
            self.assertIn("Suggested mouse", card.inner_text())
            self.assertIn("$20.00", card.inner_text())
            self.assertIn("3 completed orders", card.inner_text())
            page.get_by_role("link", name="Customize this build", exact=True).click()
            self.assertTrue(page.locator(f"#suggested-accessory-{self.mouse.pk}").is_visible())
            page.locator(f"#suggested-accessory-{self.mouse.pk}").get_by_role("button", name="Add accessory to cart", exact=True).click()
            page.wait_for_url("**/cart")
            self.assertIn("Suggested mouse", page.locator("main").inner_text())
            self.assertIn("$20.00", page.locator("main").inner_text())
            browser.close()

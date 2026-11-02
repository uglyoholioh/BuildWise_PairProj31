"""Optional browser smoke test: install Playwright and a local Chromium first."""
import shutil
import unittest

from django.contrib.staticfiles.testing import StaticLiveServerTestCase

from .models import Game, GameSettingSupport, QuestionnaireResponse

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

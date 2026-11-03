from django.test import TestCase
from django.urls import reverse

from .models import Game, GameSettingSupport, QuestionnaireResponse


class QuestionnaireRepairTests(TestCase):
    def setUp(self):
        self.game = Game.objects.create(name='First Game', slug='first')
        self.second = Game.objects.create(name='Second Game', slug='second')
        for game in (self.game, self.second):
            GameSettingSupport.objects.create(game=game, resolution='1920x1080', graphics_preset='high')
        self.url = reverse('recommendations:questionnaire')
        self.data = {'games': [str(self.game.pk)], 'perf_level': 'balanced', 'max_spend': '1000'}

    def test_first_selection_shows_defaults_before_requiring_them(self):
        response = self.client.post(self.url, self.data)
        form = response.context['form']
        self.assertFalse(form.is_bound)
        self.assertEqual(form[f'game_{self.game.pk}_fps_goal'].value(), 60)
        self.assertFalse(QuestionnaireResponse.objects.exists())
        data = dict(self.data, **{f'game_{self.game.pk}_fps_goal': '60',
                                 f'game_{self.game.pk}_resolution': '1920x1080',
                                 f'game_{self.game.pk}_graphics_preset': 'high'})
        self.assertRedirects(self.client.post(self.url, data), reverse('recommendations:recommendations'))
        self.assertEqual(QuestionnaireResponse.objects.count(), 1)

    def test_malformed_missing_unknown_and_unsupported_choices_are_form_errors(self):
        unsupported = Game.objects.create(name='Unsupported', slug='unsupported')
        for ids in [['invalid'], ['9' * 100], ['9999'], [str(unsupported.pk)], []]:
            response = self.client.post(self.url, dict(self.data, games=ids))
            self.assertEqual(response.status_code, 200)
            self.assertIn('games', response.context['form'].errors)
        self.assertFalse(QuestionnaireResponse.objects.exists())

    def test_updates_preserve_settings_when_games_removed_and_readded(self):
        key = f'game_{self.game.pk}_fps_goal'
        data = dict(self.data, action='settings', **{key: '144'})
        response = self.client.post(self.url, data)
        self.assertEqual(response.context['form'][key].value(), '144')
        self.client.post(self.url, dict(self.data, action='settings', games=[str(self.second.pk)], **{key: '165'}))
        response = self.client.post(self.url, dict(self.data, action='settings', games=[str(self.game.pk), str(self.second.pk)]))
        self.assertEqual(response.context['form'][key].value(), '165')
        self.assertEqual(response.context['form'][f'game_{self.second.pk}_fps_goal'].value(), 60)
        self.assertFalse(QuestionnaireResponse.objects.exists())

    def test_blank_presented_setting_is_an_error(self):
        response = self.client.post(self.url, dict(self.data, **{
            f'game_{self.game.pk}_fps_goal': '', f'game_{self.game.pk}_resolution': '1920x1080',
            f'game_{self.game.pk}_graphics_preset': 'high',
        }))
        self.assertIn(f'game_{self.game.pk}_fps_goal', response.context['form'].errors)

from django.db import models


class Game(models.Model):
    name = models.CharField(max_length=160)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name


class GameSettingSupport(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="supported_settings")
    resolution = models.CharField(max_length=24)
    graphics_preset = models.CharField(max_length=40)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("game", "resolution", "graphics_preset"), name="unique_game_setting_support")]


class QuestionnaireResponse(models.Model):
    customer = models.ForeignKey("accounts.Customer", null=True, blank=True, on_delete=models.SET_NULL, related_name="questionnaires")
    perf_level = models.CharField(max_length=40)
    max_spend = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)


class QuestionnaireGameSetting(models.Model):
    response = models.ForeignKey(QuestionnaireResponse, on_delete=models.CASCADE, related_name="game_settings")
    game = models.ForeignKey(Game, on_delete=models.PROTECT)
    fps_goal = models.PositiveSmallIntegerField()
    resolution = models.CharField(max_length=24)
    graphics_preset = models.CharField(max_length=40)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("response", "game"), name="unique_questionnaire_game")]


class ShortlistItem(models.Model):
    customer = models.ForeignKey("accounts.Customer", on_delete=models.CASCADE, related_name="shortlist")
    configuration = models.ForeignKey("catalogue.Configuration", null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=160)
    custom_snapshot = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

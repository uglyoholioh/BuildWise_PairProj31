from decimal import Decimal

from django import forms

from .models import Game, GameSettingSupport, ShortlistItem


class ShortlistForm(forms.ModelForm):
    class Meta:
        model = ShortlistItem
        fields = ("name",)
        labels = {"name": "Saved build name"}
        widgets = {"name": forms.TextInput(attrs={"class": "form-control"})}


class SupportedGamesField(forms.ModelMultipleChoiceField):
    def clean(self, value):
        for game_id in value or []:
            text = str(game_id)
            if not text.isascii() or not text.isdecimal() or len(text) > 19:
                raise forms.ValidationError("Choose valid supported games.")
            # Reject values outside SQLite's signed 64-bit IDs before the ORM query.
            if not 0 < int(text) <= 9223372036854775807:
                raise forms.ValidationError("Choose valid supported games.")
        return super().clean(value)


class QuestionnaireForm(forms.Form):
    PERF_LEVELS = (("balanced", "Balanced (default)"),)
    games = SupportedGamesField(
        queryset=Game.objects.filter(supported_settings__isnull=False).distinct().order_by("name"),
        widget=forms.CheckboxSelectMultiple,
        required=True,
    )
    perf_level = forms.ChoiceField(choices=PERF_LEVELS, initial="balanced")
    max_spend = forms.DecimalField(min_value=Decimal("0.01"), max_digits=10, decimal_places=2)

    def __init__(self, *args, initial_response=None, include_settings=True, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound:
            selected = self.data.getlist("games") if hasattr(self.data, "getlist") else self.data.get("games", [])
            selected = selected if isinstance(selected, (list, tuple)) else [selected]
        else:
            previous_games = []
            if initial_response:
                previous_games = list(initial_response.game_settings.values_list("game_id", flat=True))
            selected = self.initial.get("games", previous_games)
        if initial_response:
            self.fields["max_spend"].initial = initial_response.max_spend
            self.fields["perf_level"].initial = initial_response.perf_level
        self.game_settings = []
        # Let ModelMultipleChoiceField validate IDs before querying dynamic settings.
        try:
            games = self.fields["games"].clean(selected)
        except forms.ValidationError:
            games = []
        if not include_settings or len(games) > 3:
            games = []
        for game in games:
            supported = list(game.supported_settings.order_by("resolution", "graphics_preset"))
            resolutions = sorted({row.resolution for row in supported})
            default_setting = supported[0]
            prefix = f"game_{game.pk}"
            previous = (
                initial_response.game_settings.filter(game=game).first()
                if initial_response else None
            )
            previous_supported = previous and any(
                row.resolution == previous.resolution and row.graphics_preset == previous.graphics_preset
                for row in supported
            )
            self.fields[f"{prefix}_fps_goal"] = forms.IntegerField(
                min_value=1, max_value=1000, initial=previous.fps_goal if previous else 60, label="FPS target"
            )
            self.fields[f"{prefix}_resolution"] = forms.ChoiceField(
                choices=[(value, value) for value in resolutions],
                initial=previous.resolution if previous_supported else default_setting.resolution,
                label="Resolution",
            )
            self.fields[f"{prefix}_graphics_preset"] = forms.ChoiceField(
                choices=[(value, value) for value in sorted({row.graphics_preset for row in supported})],
                initial=previous.graphics_preset if previous_supported else default_setting.graphics_preset,
                label="Graphics preset",
            )
            self.game_settings.append({
                "game": game,
                "fps_goal": self[f"{prefix}_fps_goal"],
                "resolution": self[f"{prefix}_resolution"],
                "graphics_preset": self[f"{prefix}_graphics_preset"],
                "pairs": ",".join(f"{row.resolution}|{row.graphics_preset}" for row in supported),
            })
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxSelectMultiple):
                continue
            field.widget.attrs["class"] = "form-select" if isinstance(field.widget, forms.Select) else "form-control"

    def clean_games(self):
        games = self.cleaned_data["games"]
        if games.count() > 3:
            raise forms.ValidationError("Choose no more than three games.")
        return games

    def clean(self):
        cleaned_data = super().clean()
        for setting in self.game_settings:
            game = setting["game"]
            prefix = f"game_{game.pk}"
            resolution = cleaned_data.get(f"{prefix}_resolution")
            preset = cleaned_data.get(f"{prefix}_graphics_preset")
            if resolution and preset and not GameSettingSupport.objects.filter(
                game=game, resolution=resolution, graphics_preset=preset
            ).exists():
                self.add_error(f"{prefix}_graphics_preset", "Choose settings supported for this game.")
        return cleaned_data

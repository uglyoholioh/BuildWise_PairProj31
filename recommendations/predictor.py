from catalogue.models import Component


# shortcut: fixed tier and setting multipliers, replace with the trained predictor in joint task #30.
TIER_FPS = {"entry": 30, "low": 45, "mid": 60, "medium": 60, "high": 90, "enthusiast": 120}
RESOLUTION_FACTOR = {"1280x720": 1.25, "1920x1080": 1.0, "2560x1440": 0.75, "3840x2160": 0.5}
GRAPHICS_FACTOR = {"low": 1.15, "medium": 1.0, "high": 0.85, "ultra": 0.7}


def predict_temporary_fps(components, game_setting):
    """Return an explicitly provisional tier-based estimate, or None when unsupported."""
    gpu = next((item.component for item in components if item.component.category == Component.Category.GPU), None)
    if gpu is None:
        return None
    base = TIER_FPS.get(gpu.ml_tier.strip().casefold())
    resolution = RESOLUTION_FACTOR.get(game_setting.resolution.casefold())
    graphics = GRAPHICS_FACTOR.get(game_setting.graphics_preset.strip().casefold())
    if base is None or resolution is None or graphics is None:
        return None
    return round(base * resolution * graphics)

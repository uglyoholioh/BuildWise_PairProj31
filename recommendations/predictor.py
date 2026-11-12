"""Load the trusted offline model and validate storefront inputs before inference."""
import logging
import pickle
from functools import lru_cache

import joblib
import numpy as np

from catalogue.models import Component
from .ml.schema import CPU_ALIASES, GPU_ALIASES, FEATURES, MODEL_PATH, MODEL_VERSION, hardware_key

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def load_fps_model():
    # Only load this repository-owned artifact. joblib files must never come from customer uploads.
    try:
        artifact = joblib.load(MODEL_PATH)
    except (OSError, ValueError, EOFError, pickle.UnpicklingError) as error:
        logger.error('FPS model could not be loaded: %s', error)
        return None
    if not isinstance(artifact, dict) or artifact.get('version') != MODEL_VERSION or tuple(artifact.get('features', ())) != FEATURES:
        logger.error('FPS model version or feature schema does not match this application.')
        return None
    return artifact


def predict_fps(components, game_setting):
    """Return a synthetic-model FPS estimate, or None for unsupported/missing inputs."""
    components = list(components)
    cpus = [item for item in components if item.component.category == Component.Category.CPU]
    gpus = [item for item in components if item.component.category == Component.Category.GPU]
    if len(cpus) != 1 or len(gpus) != 1 or cpus[0].quantity != 1 or gpus[0].quantity != 1:
        return None
    features = {
        'cpu': hardware_key(cpus[0].component.model, CPU_ALIASES),
        'gpu': hardware_key(gpus[0].component.model, GPU_ALIASES),
        'game': game_setting.game.slug,
        'resolution': game_setting.resolution.strip().casefold(),
        'graphics_preset': game_setting.graphics_preset.strip().casefold(),
    }
    artifact = load_fps_model()
    if artifact is None or any(features[key] not in artifact['supported_inputs'][key] for key in FEATURES):
        return None
    estimate = float(artifact['pipeline'].predict([features])[0])
    return max(1, round(estimate)) if np.isfinite(estimate) and estimate > 0 else None

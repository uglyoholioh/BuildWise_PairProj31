"""Explicit supported inputs; simulation values are assumptions, not benchmarks."""
from pathlib import Path

MODEL_PATH = Path(__file__).resolve().parent / 'fps_model.joblib'
MODEL_VERSION = 'synthetic-fps-v1'
MODEL_METHOD = 'Random Forest regression — synthetic prototype v1'
MODEL_LIMITATIONS = (
    'Trained on synthetic data, not measured benchmarks. CPU, GPU, game, resolution and '
    'graphics preset are modelled. RAM, cooling, drivers, ray tracing, upscaling and frame '
    'generation are not modelled. FPS values are illustrative estimates, not guarantees.'
)
FEATURES = ('cpu', 'gpu', 'game', 'resolution', 'graphics_preset')
# Each alias must identify a specific catalogue model; do not infer hardware from its price or tier.
CPU_ALIASES = {
    'ryzen-5-7500f': ('Ryzen 5 7500F',),
    'ryzen-7-7800x3d': ('Ryzen 7 7800X3D',),
    'core-i5-12400f': ('Core i5-12400F', 'i5-12400F'),
    'core-i7-13700k': ('Core i7-13700K', 'i7-13700K'),
}
GPU_ALIASES = {
    'rtx-3060': ('GeForce RTX 3060 12GB', 'RTX 3060 12GB'),
    'rtx-4060': ('GeForce RTX 4060 8GB', 'RTX 4060 8GB'),
    'rtx-5060': ('GeForce RTX 5060 OC 8G Low Profile', 'RTX 5060 8GB'),
    'rtx-4070': ('GeForce RTX 4070 12GB', 'RTX 4070 12GB'),
    'rx-7800-xt': ('Radeon RX 7800 XT 16GB', 'RX 7800 XT 16GB'),
    'rx-9070-xt': ('Swift Radeon RX 9070 XT 16GB', 'RX 9070 XT 16GB'),
}
GAMES = {'counter-strike-2': 'Counter-Strike 2', 'fortnite': 'Fortnite', 'cyberpunk-2077': 'Cyberpunk 2077'}
RESOLUTIONS = ('1920x1080', '2560x1440', '3840x2160')
GRAPHICS_PRESETS = ('low', 'medium', 'high', 'ultra')
SUPPORTED_INPUTS = {
    'cpu': tuple(CPU_ALIASES), 'gpu': tuple(GPU_ALIASES), 'game': tuple(GAMES),
    'resolution': RESOLUTIONS, 'graphics_preset': GRAPHICS_PRESETS,
}


def hardware_key(model, aliases):
    name = ' '.join(model.casefold().split())
    for key, names in aliases.items():
        if name in (' '.join(alias.casefold().split()) for alias in names):
            return key
    return None

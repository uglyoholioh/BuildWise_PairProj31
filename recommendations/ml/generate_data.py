"""Generate a reproducible synthetic dataset. Run: python -m recommendations.ml.generate_data"""
import csv
import itertools
import random
from pathlib import Path

from .schema import FEATURES, SUPPORTED_INPUTS

SEED = 2108
# Assumed performance capacities solely for the simulation; these are not sourced FPS measurements.
CPU_CAPACITY = {'ryzen-5-7500f': 190, 'ryzen-7-7800x3d': 285, 'core-i5-12400f': 155, 'core-i7-13700k': 235}
GPU_CAPACITY = {'rtx-3060': 75, 'rtx-4060': 95, 'rtx-5060': 115, 'rtx-4070': 155, 'rx-7800-xt': 165, 'rx-9070-xt': 205}
GAME_LOAD = {'counter-strike-2': (0.75, 0.55), 'fortnite': (0.95, 0.85), 'cyberpunk-2077': (1.15, 1.45)}
RESOLUTION_LOAD = {'1920x1080': 1.0, '2560x1440': 1.6, '3840x2160': 2.8}
GRAPHICS_LOAD = {'low': 0.65, 'medium': 0.85, 'high': 1.0, 'ultra': 1.3}


def generate_rows():
    rng = random.Random(SEED)
    for values in itertools.product(*(SUPPORTED_INPUTS[name] for name in FEATURES)):
        features = dict(zip(FEATURES, values))
        cpu_load, gpu_load = GAME_LOAD[features['game']]
        cpu_limit = CPU_CAPACITY[features['cpu']] / cpu_load
        gpu_limit = GPU_CAPACITY[features['gpu']] / (
            gpu_load * RESOLUTION_LOAD[features['resolution']] * GRAPHICS_LOAD[features['graphics_preset']]
        )
        # A smooth bottleneck represents CPU/GPU interaction, plus independent simulated run variation.
        expected = (cpu_limit ** -4 + gpu_limit ** -4) ** -0.25
        for run in range(1, 4):
            yield {**features, 'run': run, 'average_fps': round(max(1, expected * rng.uniform(0.95, 1.05)), 2)}


def main():
    path = Path(__file__).resolve().parent / 'synthetic_fps.csv'
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=(*FEATURES, 'run', 'average_fps'), lineterminator='\n')
        writer.writeheader()
        writer.writerows(generate_rows())
    print(f'Saved synthetic prototype data: {path}')


if __name__ == '__main__':
    main()

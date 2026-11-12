"""Train offline: python -m recommendations.ml.train. Never called by Django requests."""
import csv
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline

from .schema import FEATURES, MODEL_LIMITATIONS, MODEL_METHOD, MODEL_PATH, MODEL_VERSION, SUPPORTED_INPUTS

SEED = 2108


def main():
    directory = Path(__file__).resolve().parent
    data_path = directory / 'synthetic_fps.csv'
    with data_path.open(encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    x = [{key: row[key] for key in FEATURES} for row in rows]
    y = np.array([float(row['average_fps']) for row in rows])
    if not len(y) or not np.isfinite(y).all() or (y <= 0).any():
        raise ValueError('Training targets must be finite, positive FPS values.')
    for row in x:
        if any(row[key] not in SUPPORTED_INPUTS[key] for key in FEATURES):
            raise ValueError('Dataset contains an unsupported input.')
    # Hold out whole CPU/GPU pairs: repeated runs and all settings for a pair stay in one split.
    groups = np.array([f"{row['cpu']}|{row['gpu']}" for row in x])
    train_validation, test = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=SEED).split(x, y, groups))
    train_part, validation_part = next(GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=SEED).split(
        train_validation, groups=groups[train_validation]))
    train, validation = train_validation[train_part], train_validation[validation_part]
    for key in FEATURES:
        if {x[i][key] for i in train} != set(SUPPORTED_INPUTS[key]):
            raise ValueError(f'Training split must include every supported {key}.')
    splits = {'train': train, 'validation': validation, 'test': test}
    report = {
        'model_version': MODEL_VERSION, 'data_source': 'AI-assisted synthetic simulation; no measured benchmarks',
        'limitations': MODEL_LIMITATIONS, 'seed': SEED, 'features': list(FEATURES),
        'target': 'average_fps', 'dataset_sha256': hashlib.sha256(data_path.read_bytes()).hexdigest(),
        'split_strategy': 'Disjoint CPU/GPU pairs; no repeated-run or pair overlap',
        'splits': {name: {'rows': len(indices), 'hardware_pairs': sorted(set(groups[indices]))} for name, indices in splits.items()},
        'versions': {'scikit-learn': sklearn.__version__, 'joblib': joblib.__version__, 'numpy': np.__version__},
        'validation_candidates': [],
    }
    baseline = DummyRegressor(strategy='mean').fit(np.zeros((len(train), 1)), y[train])
    report['baseline_test_mae_fps'] = float(mean_absolute_error(y[test], baseline.predict(np.zeros((len(test), 1)))))
    candidates = []
    for depth in (8, 16):
        model = Pipeline([
            ('features', DictVectorizer(sparse=False)),
            ('regressor', RandomForestRegressor(n_estimators=120, max_depth=depth, min_samples_leaf=2, random_state=SEED, n_jobs=1)),
        ])
        model.fit([x[i] for i in train], y[train])
        mae = float(mean_absolute_error(y[validation], model.predict([x[i] for i in validation])))
        candidates.append((mae, depth, model))
        report['validation_candidates'].append({'max_depth': depth, 'mae_fps': mae})
    validation_mae, depth, model = min(candidates, key=lambda item: item[0])
    predictions = model.predict([x[i] for i in test])
    report.update(selected_max_depth=depth, validation_mae_fps=validation_mae,
                  test_mae_fps=float(mean_absolute_error(y[test], predictions)), test_r2=float(r2_score(y[test], predictions)),
                  persisted_training_rows=len(train), algorithm='RandomForestRegressor', n_estimators=120)
    # Persist the exact evaluated pipeline; do not refit on the held-out test set.
    joblib.dump({'version': MODEL_VERSION, 'method': MODEL_METHOD, 'limitations': MODEL_LIMITATIONS,
                 'features': FEATURES, 'supported_inputs': SUPPORTED_INPUTS, 'pipeline': model}, MODEL_PATH, compress=3)
    (directory / 'evaluation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('validation_mae_fps', 'test_mae_fps', 'test_r2', 'baseline_test_mae_fps')}, indent=2))
    print(f'Saved evaluated synthetic model: {MODEL_PATH}')


if __name__ == '__main__':
    main()

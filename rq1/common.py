"""Shared helpers for the RQ1 analyses."""
from __future__ import annotations

import csv
import json
import logging
import warnings
from collections import defaultdict
from pathlib import Path
from typing import Any

warnings.filterwarnings('ignore')
logging.disable(logging.WARNING)

ROOT = Path(__file__).resolve().parent.parent
FEATURE_OPS = {'CoreFeatures', 'DeadFeatures', 'FalseOptionalFeatures', 'VariantFeatures',
               'PureOptionalFeatures', 'UniqueFeatures'}


def load_values(result_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """(model_id, operation) -> {tool: value}, using only committed attempts."""
    out: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
    for done_file in result_dir.glob('*.done'):
        committed = {tuple(l.split('\t')) for l in done_file.read_text().splitlines() if l.strip()}
        committed_attempts = {a for _, a in committed}
        values_file = done_file.with_suffix('.values.jsonl')
        if not values_file.exists():
            continue
        for line in values_file.read_text().splitlines():
            v = json.loads(line)
            if v['attempt'] in committed_attempts:
                out[(v['model_id'], v['operation'])][v['tool']] = v['value']
    return out


def load_status(result_dir: Path) -> dict[tuple[str, str, str], tuple[str, str]]:
    """(model_id, operation, tool) -> (status, error) of repetition 0 (first restart that ran it)."""
    out: dict[tuple[str, str, str], tuple[str, str]] = {}
    for csv_file in result_dir.glob('*.csv'):
        done = result_dir / (csv_file.stem + '.done')
        attempts = {l.split('\t')[1] for l in done.read_text().splitlines() if l.strip()} if done.exists() else set()
        with open(csv_file, newline='') as fh:
            for r in csv.DictReader(fh):
                if r['attempt'] in attempts and r['step'] == 'op' and r['rep'] == '0':
                    out.setdefault((r['model_id'], r['operation'], r['tool']), (r['status'], r['error']))
    return out


_feature_cache: dict[str, set[str]] = {}


def feature_names(uvl_path: Path) -> set[str]:
    """Names of the features declared in the model (flamapy's reading)."""
    key = str(uvl_path)
    if key not in _feature_cache:
        from flamapy.metamodels.fm_metamodel.transformations import UVLReader
        fm = UVLReader(key).transform()
        _feature_cache[key] = {f.name for f in fm.get_features()}
    return _feature_cache[key]


def normalise(op: str, value: Any, features: set[str] | None) -> Any:
    """Make values from different tools comparable.

    FeatureIDE's UVL import adds helper features for UVL group syntax (e.g. `_r_2_MANDATORY_0`).
    Feature lists and configurations are therefore restricted to the features of the model.
    """
    if value is None:
        return None
    if op in FEATURE_OPS and isinstance(value, list):
        return sorted(v for v in value if features is None or v in features)
    if op == 'Configurations' and isinstance(value, list):
        return sorted(sorted(f for f in conf if features is None or f in features) for conf in value)
    if op == 'ConfigurationsNumber':
        return str(value)
    return value


def manifest(path: Path) -> dict[str, dict[str, str]]:
    with open(path, newline='') as fh:
        return {r['model_id']: r for r in csv.DictReader(fh)}

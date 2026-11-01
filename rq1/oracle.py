"""RQ1 (oracle): exhaustive, solver-free reference results for small Boolean models.

For every unique Boolean-level model small enough to enumerate (default: at most 60 features and
200,000 configurations), this script enumerates all configurations directly from the feature
tree and cross-tree constraints, without any solver or flamapy analysis code (flamapy's UVL parser
is used only to read the file), and derives each operation's result from its definition:

  Satisfiable            at least one configuration
  ConfigurationsNumber   |C|
  Configurations         C (sets of selected feature names)
  CoreFeatures           features in every configuration          (void model: [])
  DeadFeatures           features in no configuration             (void model: all features)
  VariantFeatures        neither core nor dead (0 < FIP < 1)
  FalseOptionalFeatures  non-root, non-mandatory features selected in every configuration that
                         selects their parent. Two variants are stored:
                           *_vacuous: also counts features whose parent is dead (vacuously true)
                           *_live:    only features whose parent is in some configuration
  UniqueFeatures         features in exactly one configuration
  FeatureInclusionProbability  f -> |{c : f in c}| / |C|
  PureOptionalFeatures   FIP == 0.5
  ProductDistribution    k -> |{c : |c| = k}|, k = 0..n
  Homogeneity            mean FIP over all features
  Variability            (|C| / (2^n - 1), |C| / (2^v - 1)) with n features, v variant features
                         (second value undefined when v = 0)

Writes rq1/oracle_values.jsonl (one model per line) and prepared/oracle_ids.json (the models it
could enumerate), which campaign/configs/rq1_oracle.json reads.

    .venv/bin/python rq1/oracle.py [--max-features 60] [--max-configs 200000] [--jobs 8]
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


class TooMany(Exception):
    pass


def enumerate_configs(fm: Any, cap: int) -> tuple[list[frozenset[str]], list[str]]:
    """All configurations of the feature tree (cross-tree constraints applied afterwards)."""

    def sub(feature: Any) -> list[frozenset[str]]:
        partial: list[frozenset[str]] = [frozenset({feature.name})]
        for rel in feature.get_relations():
            children = list(rel.children)
            lo = rel.card_min
            hi = rel.card_max if rel.card_max >= 0 else len(children)
            child_opts = [sub(c) for c in children]
            options: list[frozenset[str]] = []
            for k in range(lo, min(hi, len(children)) + 1):
                for chosen in itertools.combinations(range(len(children)), k):
                    combos: list[frozenset[str]] = [frozenset()]
                    for i in chosen:
                        combos = [a | b for a in combos for b in child_opts[i]]
                        if len(combos) > cap:
                            raise TooMany()
                    options.extend(combos)
                    if len(options) > cap:
                        raise TooMany()
            partial = [a | b for a in partial for b in options]
            if len(partial) > cap:
                raise TooMany()
        return partial

    names = [f.name for f in fm.get_features()]
    return sub(fm.root), names


def evaluate(node: Any, conf: frozenset[str]) -> bool:
    from flamapy.core.models.ast import ASTOperation as Op
    d = node.data
    if not isinstance(d, Op):
        return str(d) in conf
    if d == Op.NOT:
        return not evaluate(node.left, conf)
    a = evaluate(node.left, conf)
    b = evaluate(node.right, conf)
    if d == Op.AND:
        return a and b
    if d == Op.OR:
        return a or b
    if d in (Op.IMPLIES, Op.REQUIRES):
        return (not a) or b
    if d == Op.EXCLUDES:
        return not (a and b)
    if d == Op.EQUIVALENCE:
        return a == b
    if d == Op.XOR:
        return a != b
    raise ValueError(f'non-Boolean operator {d}')


def oracle_one(path: str, max_configs: int) -> dict[str, Any]:
    import logging
    import warnings
    warnings.filterwarnings('ignore')
    logging.disable(logging.WARNING)
    from flamapy.metamodels.fm_metamodel.transformations import UVLReader
    fm = UVLReader(path).transform()
    tree_confs, names = enumerate_configs(fm, max_configs)
    ctcs = [c.ast.root for c in fm.get_constraints()]
    confs = [c for c in tree_confs if all(evaluate(n, c) for n in ctcs)]
    n = len(confs)
    count = {f: 0 for f in names}
    dist = [0] * (len(names) + 1)
    for c in confs:
        dist[len(c)] += 1
        for f in c:
            count[f] += 1
    fip = {f: (count[f] / n if n else 0.0) for f in names}
    core = sorted(f for f in names if n and count[f] == n)
    dead = sorted(names) if n == 0 else sorted(f for f in names if count[f] == 0)
    variant = sorted(f for f in names if n and 0 < count[f] < n)
    parent = {f.name: f.get_parent().name for f in fm.get_features() if f.get_parent() is not None}
    non_mandatory = {f.name for f in fm.get_features() if not f.is_root() and not f.is_mandatory()}
    fo_vacuous, fo_live = [], []
    for f in sorted(non_mandatory):
        p = parent[f]
        if all(f in c for c in confs if p in c):
            fo_vacuous.append(f)
            if count[p] > 0:
                fo_live.append(f)
    v = len(variant)
    return {
        'Satisfiable': n > 0,
        'ConfigurationsNumber': str(n),
        'Configurations': sorted(sorted(c) for c in confs),
        'CoreFeatures': core,
        'DeadFeatures': dead,
        'VariantFeatures': variant,
        'FalseOptionalFeatures_vacuous': fo_vacuous,
        'FalseOptionalFeatures_live': fo_live,
        'UniqueFeatures': sorted(f for f in names if count[f] == 1),
        'FeatureInclusionProbability': fip,
        'PureOptionalFeatures': sorted(f for f in names if n and count[f] * 2 == n),
        'ProductDistribution': [str(x) for x in dist],
        'Homogeneity': (sum(fip.values()) / len(names)) if names else 0.0,
        'Variability': [n / (2 ** len(names) - 1), (n / (2 ** v - 1)) if v else None],
        '_n_features': len(names),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--max-features', type=int, default=60)
    ap.add_argument('--max-configs', type=int, default=200_000)
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--one', nargs=2, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.one:
        try:
            print(json.dumps({'ok': True, 'values': oracle_one(args.one[0], int(args.one[1]))}))
        except TooMany:
            print(json.dumps({'ok': False, 'reason': 'too many configurations'}))
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({'ok': False, 'reason': f'{type(exc).__name__}: {exc}'[:200]}))
        return

    with open(ROOT / 'prepared/manifest.csv', newline='') as fh:
        rows = [r for r in csv.DictReader(fh)
                if not r['excluded'] and not r['duplicate_of'] and r['language_level'].startswith('Major: BOOLEAN')
                and int(r['n_features'] or 0) <= args.max_features]
    print(f'{len(rows)} Boolean models with <= {args.max_features} features', flush=True)

    results: dict[str, dict[str, Any]] = {}

    def run(r: dict[str, str]) -> None:
        try:
            p = subprocess.run([sys.executable, str(Path(__file__)), '--one', str(ROOT / r['path']), str(args.max_configs)],
                               capture_output=True, text=True, timeout=600, cwd=str(ROOT))
            out = json.loads((p.stdout.strip().splitlines() or ['{"ok": false, "reason": "no output"}'])[-1])
        except subprocess.TimeoutExpired:
            out = {'ok': False, 'reason': 'enumeration exceeded 600 s'}
        results[r['model_id']] = out

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(run, rows))

    ok = {m: v for m, v in results.items() if v.get('ok')}
    with open(ROOT / 'rq1/oracle_values.jsonl', 'w') as fh:
        for m in sorted(ok):
            fh.write(json.dumps({'model_id': m, **ok[m]['values']}) + '\n')
    (ROOT / 'prepared/oracle_ids.json').write_text(json.dumps(sorted(ok), indent=0))
    reasons: dict[str, int] = {}
    for v in results.values():
        if not v.get('ok'):
            reasons[v['reason'][:60]] = reasons.get(v['reason'][:60], 0) + 1
    print(f'{len(ok)} models enumerated; skipped: {reasons}')


if __name__ == '__main__':
    main()

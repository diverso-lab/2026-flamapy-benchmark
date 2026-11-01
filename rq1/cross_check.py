"""RQ1 (differential testing): do the tools agree with each other on the UVLHub models?

For every (model, operation) where at least two tools produced a result, the results are
normalised (rq1.common.normalise) and compared. A tool "agrees" when its result equals the
majority result among the tools that answered; ties are reported as such.

    .venv/bin/python rq1/cross_check.py results/rq2_main [--reference flamapy-bdd]

Writes rq1/report_cross_check_<campaign>.md and rq1/disagreements_<campaign>.csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rq1.common import ROOT, feature_names, load_values, manifest, normalise  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('results')
    args = ap.parse_args()
    res = Path(args.results).resolve()
    cfg = json.loads((res / 'config.json').read_text())
    tools = cfg['tools']
    models = manifest(ROOT / cfg['models']['manifest'])
    values = load_values(res)

    pair_stats: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)   # (op, t1, t2)
    majority_stats: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)    # (op, tool)
    rows = []
    helper_features = Counter()
    for (mid, op), by_tool in sorted(values.items()):
        if mid not in models:
            continue
        try:
            feats = feature_names(ROOT / models[mid]['path'])
        except Exception:  # noqa: BLE001
            feats = None
        norm = {}
        for t, v in by_tool.items():
            n = normalise(op, v, feats)
            if isinstance(v, list) and isinstance(n, list) and op != 'Configurations' and len(n) < len(v):
                helper_features[t] += len(v) - len(n)
            norm[t] = json.dumps(n, sort_keys=True)
        if len(norm) < 2:
            continue
        for i, t1 in enumerate(tools):
            for t2 in tools[i + 1:]:
                if t1 in norm and t2 in norm:
                    pair_stats[(op, t1, t2)]['agree' if norm[t1] == norm[t2] else 'disagree'] += 1
        counts = Counter(norm.values())
        top, top_n = counts.most_common(1)[0]
        tie = len(counts) > 1 and list(counts.values()).count(top_n) > 1
        for t, h in norm.items():
            if len(counts) == 1:
                majority_stats[(op, t)]['agree'] += 1
            elif tie:
                majority_stats[(op, t)]['tie'] += 1
            else:
                majority_stats[(op, t)]['agree' if h == top else 'minority'] += 1
        if len(counts) > 1:
            groups = defaultdict(list)
            for t, h in norm.items():
                groups[h].append(t)
            rows.append({'model_id': mid, 'operation': op, 'n_features': models[mid].get('n_features', ''),
                         'groups': ' || '.join(f"{'+'.join(ts)} = {h[:100]}" for h, ts in groups.items())})

    name = res.name
    lines = [f'# RQ1 differential testing: {name}', '',
             f'Results: `{res.relative_to(ROOT) if res.is_relative_to(ROOT) else res}`. A result counts only when the tool finished '
             'within the timeout. Feature lists are restricted to the features declared in the model '
             f'(FeatureIDE helper features removed: {dict(helper_features) or "none"}).', '',
             '## Agreement with the majority answer', '',
             'agree / compared (minority = differs from a strict majority; tie = no majority).', '',
             '| Operation | ' + ' | '.join(tools) + ' |', '|---|' + '---|' * len(tools)]
    ops = list(dict.fromkeys(op for (_, op) in values))
    for op in ops:
        cells = []
        for t in tools:
            c = majority_stats.get((op, t))
            if not c:
                cells.append('')
                continue
            total = sum(c.values())
            extra = ', '.join(f'{k} {v}' for k, v in c.items() if k != 'agree')
            cells.append(f"{c['agree']}/{total}" + (f' ({extra})' if extra else ''))
        lines.append(f'| {op} | ' + ' | '.join(cells) + ' |')
    lines += ['', '## Pairwise agreement', '', '| Operation | Tool A | Tool B | agree | disagree |', '|---|---|---|---|---|']
    for (op, t1, t2), c in sorted(pair_stats.items()):
        lines.append(f"| {op} | {t1} | {t2} | {c['agree']} | {c['disagree']} |")
    lines += ['', f'{len(rows)} (model, operation) pairs with any disagreement: '
              f'`rq1/disagreements_{name}.csv`.']
    (ROOT / f'rq1/report_cross_check_{name}.md').write_text('\n'.join(lines) + '\n')
    with open(ROOT / f'rq1/disagreements_{name}.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=['model_id', 'operation', 'n_features', 'groups'])
        w.writeheader()
        w.writerows(rows)
    print('\n'.join(lines[:12 + len(ops)]))
    print(f'\n{len(rows)} disagreeing (model, operation) pairs')


if __name__ == '__main__':
    main()

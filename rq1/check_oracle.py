"""RQ1 (oracle): compare every tool with the exhaustive oracle (rq1/oracle.py).

    .venv/bin/python rq1/check_oracle.py [results/rq1_oracle]

Writes rq1/report_oracle.md and rq1/oracle_mismatches.csv.

FalseOptionalFeatures is compared with both oracle variants (vacuous / live parent) and the table
reports which definition each tool follows. Floats are compared with a relative tolerance of 1e-9.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rq1.common import ROOT, load_status, load_values, manifest  # noqa: E402

FEATURE_LIST_OPS = {'CoreFeatures', 'DeadFeatures', 'VariantFeatures', 'PureOptionalFeatures', 'UniqueFeatures'}


def close(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(close(a[k], b[k]) for k in a)
    return a == b


def main() -> None:
    res = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'results/rq1_oracle'
    cfg = json.loads((res / 'config.json').read_text())
    tools, ops = cfg['tools'], cfg['ops']
    models = manifest(ROOT / cfg['models']['manifest'])
    oracle = {}
    for line in (ROOT / 'rq1/oracle_values.jsonl').read_text().splitlines():
        d = json.loads(line)
        oracle[d['model_id']] = d
    values = load_values(res)
    status = load_status(res)

    table: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    mismatches = []
    for mid, ref in oracle.items():
        names = set(ref['FeatureInclusionProbability'])
        for op in ops:
            for tool in tools:
                got = values.get((mid, op), {}).get(tool)
                st, err = status.get((mid, op, tool), ('missing', ''))
                if got is None:
                    if 'not implemented' in err or 'unknown operation' in err:
                        table[(op, tool)]['n/a'] += 1
                    elif op == 'Variability' and ref['Variability'][1] is None and 'ZeroDivision' in err:
                        table[(op, tool)]['error (v=0)'] += 1
                        mismatches.append([mid, op, tool, 'error', err[:100], json.dumps(ref['Variability'])])
                    else:
                        table[(op, tool)][st] += 1
                        if st not in ('timeout',):
                            mismatches.append([mid, op, tool, st, err[:100], ''])
                    continue
                if isinstance(got, list) and op in FEATURE_LIST_OPS | {'FalseOptionalFeatures'}:
                    got = sorted(x for x in got if x in names)          # drop FeatureIDE helper features
                if op == 'Configurations':
                    got = sorted(sorted(x for x in c if x in names) for c in got)
                if op == 'FeatureInclusionProbability' and isinstance(got, dict):
                    got = {k: v for k, v in got.items() if k in names}
                if op == 'FalseOptionalFeatures':
                    vac, live = ref['FalseOptionalFeatures_vacuous'], ref['FalseOptionalFeatures_live']
                    if got == vac and got == live:
                        table[(op, tool)]['ok'] += 1
                    elif got == vac:
                        table[(op, tool)]['ok (vacuous def.)'] += 1
                    elif got == live:
                        table[(op, tool)]['ok (live-parent def.)'] += 1
                    else:
                        table[(op, tool)]['MISMATCH'] += 1
                        mismatches.append([mid, op, tool, 'mismatch', json.dumps(got)[:150], json.dumps(vac)[:150]])
                    continue
                expected = ref[op]
                if op == 'Variability' and expected[1] is None:
                    # oracle files written before FLAME's definition was applied: partial
                    # variability is 0 when there are no variant features
                    expected = [expected[0], 0.0]
                if close(got, expected):
                    table[(op, tool)]['ok'] += 1
                else:
                    table[(op, tool)]['MISMATCH'] += 1
                    mismatches.append([mid, op, tool, 'mismatch', json.dumps(got)[:150], json.dumps(expected)[:150]])

    lines = ['# RQ1 oracle: exhaustive enumeration on small Boolean models', '',
             f'{len(oracle)} models (Boolean language level, at most {max(o["_n_features"] for o in oracle.values())} '
             'features, enumerable). Oracle: `rq1/oracle.py` (no solver, definitions in its docstring).', '',
             '| Operation | ' + ' | '.join(tools) + ' |', '|---|' + '---|' * len(tools)]
    for op in ops:
        cells = []
        for tool in tools:
            c = table.get((op, tool), Counter())
            if not c:
                cells.append('')
            elif set(c) == {'n/a'}:
                cells.append('n/a')
            else:
                cells.append(', '.join(f'{k} {v}' for k, v in sorted(c.items(), key=lambda kv: -kv[1]) if k != 'n/a'))
        lines.append(f'| {op} | ' + ' | '.join(cells) + ' |')
    lines += ['', f'{len(mismatches)} mismatches/errors listed in `rq1/oracle_mismatches.csv`.']
    (ROOT / 'rq1/report_oracle.md').write_text('\n'.join(lines) + '\n')
    with open(ROOT / 'rq1/oracle_mismatches.csv', 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['model_id', 'operation', 'tool', 'kind', 'got', 'expected'])
        w.writerows(mismatches)
    print('\n'.join(lines))


if __name__ == '__main__':
    main()

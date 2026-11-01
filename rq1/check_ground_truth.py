"""RQ1 (ground truth): compare every tool with the FaMa test suite's expected results.

    .venv/bin/python rq1/check_ground_truth.py [results/rq1_fama_suite]

Writes rq1/report_ground_truth.md and prints the summary table.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rq1.common import ROOT, feature_names, load_status, load_values, manifest, normalise  # noqa: E402


def main() -> None:
    res = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'results/rq1_fama_suite'
    cfg = json.loads((res / 'config.json').read_text())
    tools = cfg['tools']
    expected = json.loads((ROOT / 'rq1/fama_suite_expected.json').read_text())
    models = manifest(ROOT / cfg['models']['manifest'])
    values = load_values(res)
    status = load_status(res)

    table: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    failures: list[str] = []
    for key, ops in sorted(expected.items()):
        mid = key.split('fama_test_suite/', 1)[1]
        feats = feature_names(ROOT / models[mid]['path'])
        for op, exp in ops.items():
            if isinstance(exp, dict) and 'conflict' in exp:
                continue
            exp_n = normalise(op, exp, feats)
            for tool in tools:
                got = values.get((mid, op), {}).get(tool)
                st, err = status.get((mid, op, tool), ('missing', ''))
                if got is None:
                    kind = 'unsupported' if ('not implemented' in err or 'unknown operation' in err) else st
                    table[(op, tool)][kind] += 1
                    if kind not in ('unsupported',):
                        failures.append(f'| {mid} | {op} | {tool} | {kind}: {err[:80]} | `{json.dumps(exp_n)}` |')
                    continue
                got_n = normalise(op, got, feats)
                if got_n == exp_n:
                    table[(op, tool)]['pass'] += 1
                else:
                    table[(op, tool)]['FAIL'] += 1
                    failures.append(f'| {mid} | {op} | {tool} | got `{json.dumps(got_n)[:120]}` | `{json.dumps(exp_n)[:120]}` |')

    ops_order = ['Satisfiable', 'CoreFeatures', 'DeadFeatures', 'FalseOptionalFeatures', 'ConfigurationsNumber', 'Configurations']
    lines = ['# RQ1 ground truth: FaMa test suite', '',
             f'Results: `{res.relative_to(ROOT)}`. Expected values: `rq1/fama_suite_expected.json` '
             '(extracted from flamapy/tests/test_fama_suite_*.py).', '',
             'Cells: passed / cases (FAIL = wrong answer; n/a = operation not offered by the tool).', '',
             '| Operation | ' + ' | '.join(tools) + ' |', '|---|' + '---|' * len(tools)]
    for op in ops_order:
        cells = []
        for tool in tools:
            c = table.get((op, tool), Counter())
            total = sum(c.values())
            if not total:
                cells.append('')
            elif c['unsupported'] == total:
                cells.append('n/a')
            else:
                extra = ', '.join(f'{k} {v}' for k, v in c.items() if k not in ('pass',))
                cells.append(f"{c['pass']}/{total}" + (f' ({extra})' if extra else ''))
        lines.append(f'| {op} | ' + ' | '.join(cells) + ' |')
    lines += ['', '## Disagreements and errors', '']
    if failures:
        lines += ['| Model | Operation | Tool | Got | Expected |', '|---|---|---|---|---|'] + failures
    else:
        lines.append('None.')
    out = ROOT / 'rq1/report_ground_truth.md'
    out.write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines[:len(ops_order) + 8]))
    print(f'\n{len(failures)} disagreements/errors; report: {out}')


if __name__ == '__main__':
    main()

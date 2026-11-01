"""RQ2 analysis: tables and statistical tests from a campaign result directory.

    .venv/bin/python rq2/analyze.py results/rq2_main [--baseline flamapy-sat:glucose3]

Writes rq2/report_<campaign>.md plus CSV tables in rq2/tables_<campaign>/:

  coverage.csv     success / timeout / error / crash / not_run per tool and operation
  phases.csv       start-up, setup, parse and transform times per tool (medians, by size bin)
  op_times.csv     per tool, operation and size bin: n solved, median first-call (rep 0) and
                   median warm time (median over reps 1..R-1 of each model, then median over models)
  paired.csv       each tool vs the baseline, per operation, on models both solved:
                   n, median ratio, Wilcoxon signed-rank p (Holm-corrected), Vargha-Delaney A12
  oneshot.csv      end-to-end cost of a single call (start-up + setup + parse + transform + rep 0),
                   i.e. what a user pays when invoking a tool once per question

Definitions (as stated in the paper's revised setup section):
  * time = wall-clock inside the worker (perf_counter / System.nanoTime) around one step;
    process start-up is measured by the runner from spawn to the worker's first message.
  * A model counts as solved by a tool for an operation when repetition 0 finished with success
    within the timeout. Timeouts and errors are reported, never imputed.
  * A12 = P(time_tool > time_baseline) + 0.5 P(equal); 0.5 = no difference, > 0.5 = the tool is
    slower than the baseline. Magnitude thresholds (Vargha & Delaney 2000): negligible < 0.56,
    small < 0.64, medium < 0.71, large otherwise (symmetrically below 0.5).
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BINS = [(1, 50), (51, 100), (101, 200), (201, 500), (501, 1000), (1001, 5000), (5001, 10 ** 9)]


def bin_of(n: int) -> str:
    for lo, hi in BINS:
        if lo <= n <= hi:
            return f'{lo}-{hi}' if hi < 10 ** 9 else f'>{lo - 1}'
    return '?'


def load(res: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for csv_file in sorted(res.glob('*.csv')):
        done = res / (csv_file.stem + '.done')
        if not done.exists():
            continue
        attempts = {l.split('\t')[1] for l in done.read_text().splitlines() if l.strip()}
        with open(csv_file, newline='') as fh:
            rows.extend(r for r in csv.DictReader(fh) if r['attempt'] in attempts)
    return rows


def a12(xs: list[float], ys: list[float]) -> float:
    """Vargha-Delaney A12: probability that a value from xs is larger than one from ys."""
    from bisect import bisect_left, bisect_right
    ys_sorted = sorted(ys)
    gt = eq = 0.0
    for x in xs:
        lo = bisect_left(ys_sorted, x)
        hi = bisect_right(ys_sorted, x)
        gt += lo
        eq += hi - lo
    return (gt + 0.5 * eq) / (len(xs) * len(ys))


def magnitude(a: float) -> str:
    d = abs(a - 0.5) + 0.5
    return 'negligible' if d < 0.56 else 'small' if d < 0.64 else 'medium' if d < 0.71 else 'large'


def holm(pvals: list[float]) -> list[float]:
    order = sorted(range(len(pvals)), key=lambda i: pvals[i])
    adj = [0.0] * len(pvals)
    running = 0.0
    m = len(pvals)
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvals[i]))
        adj[i] = running
    return adj


def fmt_ms(x: float | None) -> str:
    if x is None:
        return '---'
    ms = x * 1000
    return f'{ms:.2f}' if ms < 10 else f'{ms:.1f}' if ms < 1000 else f'{ms:,.0f}'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('results')
    ap.add_argument('--baseline', default='flamapy-sat:glucose3')
    args = ap.parse_args()
    res = Path(args.results).resolve()
    cfg = json.loads((res / 'config.json').read_text())
    tools, ops = cfg['tools'], cfg['ops']
    rows = load(res)
    name = res.name
    out_dir = ROOT / f'rq2/tables_{name}'
    out_dir.mkdir(parents=True, exist_ok=True)

    nf: dict[str, int] = {}
    first: dict[tuple[str, str, str], dict[str, str]] = {}           # (tool, model, op) -> rep0 row
    warm: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    phase: dict[tuple[str, str, str], float] = {}                     # (tool, model, phase) restart 0
    for r in rows:
        if r['n_features']:
            nf[r['model_id']] = int(r['n_features'])
        if r['step'] == 'op':
            key = (r['tool'], r['model_id'], r['operation'])
            if r['rep'] == '0':
                first.setdefault(key, r)
            elif r['status'] == 'success':
                warm[key].append(float(r['time_s']))
        elif r['restart'] == '0' and r['status'] == 'success' and r['time_s']:
            phase.setdefault((r['tool'], r['model_id'], r['step']), float(r['time_s']))

    # ---------------------------------------------------------------- coverage
    cov: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for (t, _, op), r in first.items():
        cov[(t, op)][r['status']] += 1
    statuses = ['success', 'timeout', 'error', 'crash', 'oom', 'not_run']
    with open(out_dir / 'coverage.csv', 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['tool', 'operation', 'models', *statuses])
        for t in tools:
            for op in ops:
                c = cov.get((t, op), Counter())
                w.writerow([t, op, sum(c.values()), *(c.get(s, 0) for s in statuses)])

    # ---------------------------------------------------------------- phases
    phases = ['startup', 'setup', 'parse', 'transform']
    with open(out_dir / 'phases.csv', 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['tool', 'phase', 'size_bin', 'n', 'median_s', 'p90_s'])
        for t in tools:
            for ph in phases:
                by_bin: dict[str, list[float]] = defaultdict(list)
                for (tt, m, p), v in phase.items():
                    if tt == t and p == ph:
                        by_bin[bin_of(nf.get(m, 0))].append(v)
                        by_bin['all'].append(v)
                for b, vs in sorted(by_bin.items()):
                    vs.sort()
                    w.writerow([t, ph, b, len(vs), f'{st.median(vs):.6f}', f'{vs[int(0.9 * (len(vs) - 1))]:.6f}'])

    # ---------------------------------------------------------------- per-op times
    def solved(t: str, op: str) -> dict[str, float]:
        return {m: float(r['time_s']) for (tt, m, o), r in first.items()
                if tt == t and o == op and r['status'] == 'success'}

    def warm_med(t: str, m: str, op: str) -> float | None:
        vs = warm.get((t, m, op))
        return st.median(vs) if vs else None

    bins = [bin_of(lo) for lo, _ in BINS]
    with open(out_dir / 'op_times.csv', 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['tool', 'operation', 'size_bin', 'n_solved', 'n_attempted', 'median_first_s', 'median_warm_s'])
        for t in tools:
            for op in ops:
                s = solved(t, op)
                for b in bins + ['all']:
                    ms = [m for m in s if b == 'all' or bin_of(nf.get(m, 0)) == b]
                    att = sum(1 for (tt, m, o) in first if tt == t and o == op and (b == 'all' or bin_of(nf.get(m, 0)) == b))
                    wm = [x for x in (warm_med(t, m, op) for m in ms) if x is not None]
                    w.writerow([t, op, b, len(ms), att, f'{st.median([s[m] for m in ms]):.6f}' if ms else '',
                                f'{st.median(wm):.6f}' if wm else ''])

    # ---------------------------------------------------------------- one-shot cost
    with open(out_dir / 'oneshot.csv', 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['tool', 'operation', 'n', 'median_oneshot_s'])
        for t in tools:
            for op in ops:
                vals = []
                for m, x in solved(t, op).items():
                    parts = [phase.get((t, m, p)) for p in phases]
                    vals.append(x + sum(p for p in parts if p is not None))
                w.writerow([t, op, len(vals), f'{st.median(vals):.6f}' if vals else ''])

    # ---------------------------------------------------------------- paired tests
    try:
        from scipy.stats import wilcoxon
    except ImportError:  # pragma: no cover
        wilcoxon = None
    tests = []
    for t in tools:
        if t == args.baseline:
            continue
        for op in ops:
            a, b = solved(t, op), solved(args.baseline, op)
            common = sorted(set(a) & set(b))
            if len(common) < 5:
                tests.append({'tool': t, 'operation': op, 'n': len(common)})
                continue
            xs = [a[m] for m in common]
            ys = [b[m] for m in common]
            p = wilcoxon(xs, ys).pvalue if wilcoxon and any(x != y for x, y in zip(xs, ys)) else 1.0
            ratio = st.median([x / y for x, y in zip(xs, ys) if y > 0])
            A = a12(xs, ys)
            tests.append({'tool': t, 'operation': op, 'n': len(common), 'median_tool_s': st.median(xs),
                          'median_baseline_s': st.median(ys), 'median_ratio': ratio, 'p': p, 'a12': A,
                          'magnitude': magnitude(A),
                          'tool_faster_share': sum(1 for x, y in zip(xs, ys) if x < y) / len(common)})
    idx = [i for i, r in enumerate(tests) if 'p' in r]
    for i, p_adj in zip(idx, holm([tests[i]['p'] for i in idx])):
        tests[i]['p_holm'] = p_adj
    fields = ['tool', 'operation', 'n', 'median_tool_s', 'median_baseline_s', 'median_ratio', 'p', 'p_holm', 'a12',
              'magnitude', 'tool_faster_share']
    with open(out_dir / 'paired.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(tests)

    # ---------------------------------------------------------------- markdown report
    L = [f'# RQ2 report: {name}', '',
         f"Timeout {cfg['timeout_s']} s for every tool, {cfg['reps']} repetitions, baseline for paired tests: "
         f'`{args.baseline}`. Tables in `rq2/tables_{name}/`.', '', '## Coverage (repetition 0)', '',
         '| Tool | Operation | models | success | timeout | error/crash | not run |', '|---|---|---|---|---|---|---|']
    for t in tools:
        for op in ops:
            c = cov.get((t, op), Counter())
            if c:
                L.append(f"| {t} | {op} | {sum(c.values())} | {c['success']} | {c['timeout']} | "
                         f"{c['error'] + c['crash'] + c['oom']} | {c['not_run']} |")
    L += ['', '## Median first-call time (ms) by number of features', '']
    for op in ops:
        L += [f'### {op}', '', '| Features | ' + ' | '.join(tools) + ' |', '|---|' + '---|' * len(tools)]
        for b in bins:
            cells = []
            for t in tools:
                s = solved(t, op)
                ms = [s[m] for m in s if bin_of(nf.get(m, 0)) == b]
                cells.append(f'{fmt_ms(st.median(ms))} (n={len(ms)})' if ms else '---')
            L.append(f'| {b} | ' + ' | '.join(cells) + ' |')
        L.append('')
    L += ['## Paired comparison with the baseline', '',
          '| Tool | Operation | n | median ratio | Holm p | A12 | magnitude | tool faster on |', '|---|---|---|---|---|---|---|---|']
    for r in tests:
        if 'p' in r:
            L.append(f"| {r['tool']} | {r['operation']} | {r['n']} | {r['median_ratio']:.3g} | {r['p_holm']:.2g} | "
                     f"{r['a12']:.2f} | {r['magnitude']} | {100 * r['tool_faster_share']:.0f}% |")
        else:
            L.append(f"| {r['tool']} | {r['operation']} | {r['n']} | (too few paired models) | | | | |")
    (ROOT / f'rq2/report_{name}.md').write_text('\n'.join(L) + '\n')
    print(f'report: rq2/report_{name}.md; tables: {out_dir.relative_to(ROOT)}')


if __name__ == '__main__':
    main()

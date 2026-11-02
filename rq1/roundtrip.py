"""RQ1 (transformations): round-trip every model through each format flamapy can write and read.

For each unique model in prepared/manifest.csv and each format F in {UVL, FeatureIDE XML,
Glencoe JSON, AFM, flamapy JSON, FaMa XML}:

    UVL --UVLReader--> FM --F writer--> file --F reader--> FM'

and FM' is compared with FM on:
  * the set of feature names,
  * the number of cross-tree constraints,
  * semantics: logical equivalence, checked with SAT on flamapy's CNF of both models (feature
    variables only): every clause of each model must follow from the other, i.e. both models
    have exactly the same configurations. Exact on every model, no counting needed.

Statuses per (model, format): ok | differs (with what differs) | write_error | read_error |
unsupported (the writer rejects the model's language level, e.g. typed features in AFM) | timeout.

    .venv/bin/python rq1/roundtrip.py [--jobs 8] [--timeout 300] [--max-features 0]

Writes rq1/roundtrip.csv and rq1/report_roundtrip.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORMATS = {
    'uvl': ('UVLWriter', 'UVLReader', '.uvl'),
    'featureide': ('FeatureIDEWriter', 'FeatureIDEReader', '.xml'),
    'glencoe': ('GlencoeWriter', 'GlencoeReader', '.gfm.json'),
    'afm': ('AFMWriter', 'AFMReader', '.afm'),
    'json': ('JSONWriter', 'JSONReader', '.json'),
    'fama_xml': ('XMLWriter', 'XMLReader', '.xml'),
}


def clauses_by_name(fm) -> frozenset:
    """The model's CNF (flamapy's default distributive encoding, feature variables only), with
    every literal written as (feature name, polarity), so two models can be compared by name."""
    from flamapy.metamodels.pysat_metamodel.transformations import FmToPysat
    sat = FmToPysat(fm).transform()
    if sat.auxiliary_variables:
        raise ValueError('CNF has auxiliary variables')
    return frozenset(frozenset((sat.features[abs(lit)], lit > 0) for lit in clause)
                     for clause in sat.get_all_clauses())


def entails(premise: frozenset, conclusion: frozenset) -> bool:
    """True when every clause of `conclusion` follows from `premise` (one SAT call per clause:
    the premise plus the negated clause must be unsatisfiable)."""
    from pysat.solvers import Solver
    ids: dict[str, int] = {}

    def lit(name: str, positive: bool) -> int:
        var = ids.setdefault(name, len(ids) + 1)
        return var if positive else -var

    with Solver(name='glucose3') as solver:
        for clause in premise:
            solver.add_clause([lit(n, pos) for n, pos in clause])
        for clause in conclusion:
            if not solver.solve(assumptions=[-lit(n, pos) for n, pos in clause]):
                continue
            return False
    return True


def one(path: str) -> dict:
    import logging
    import warnings
    warnings.filterwarnings('ignore')
    logging.disable(logging.WARNING)
    import flamapy.metamodels.fm_metamodel.transformations as t
    fm = t.UVLReader(path).transform()
    ref_names = sorted(f.name for f in fm.get_features())
    ref_ctcs = len(fm.get_constraints())
    ref_cnf = clauses_by_name(fm)
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for fmt, (w, r, ext) in FORMATS.items():
            target = str(Path(tmp) / f'model{ext}')
            try:
                getattr(t, w)(target, fm).transform()
            except Exception as exc:  # noqa: BLE001
                msg = f'{type(exc).__name__}: {exc}'[:150]
                kind = 'unsupported' if any(k in msg.lower() for k in ('not supported', 'unsupported', 'cannot be represented', 'cannot be written')) else 'write_error'
                out[fmt] = {'status': kind, 'detail': msg}
                continue
            try:
                fm2 = getattr(t, r)(target).transform()
            except Exception as exc:  # noqa: BLE001
                out[fmt] = {'status': 'read_error', 'detail': f'{type(exc).__name__}: {exc}'[:150]}
                continue
            diffs = []
            names = sorted(f.name for f in fm2.get_features())
            if names != ref_names:
                diffs.append(f'features (+{len(set(names) - set(ref_names))}/-{len(set(ref_names) - set(names))})')
            if len(fm2.get_constraints()) != ref_ctcs:
                diffs.append(f'constraints {ref_ctcs}->{len(fm2.get_constraints())}')
            equivalence = 'not checked (features differ)'
            if names == ref_names:
                try:
                    cnf = clauses_by_name(fm2)
                except Exception as exc:  # noqa: BLE001
                    out[fmt] = {'status': 'analysis_error', 'detail': f'{type(exc).__name__}: {exc}'[:150]}
                    continue
                forward, backward = entails(ref_cnf, cnf), entails(cnf, ref_cnf)
                equivalence = 'equivalent' if forward and backward else \
                    'NOT equivalent: ' + ('read-back allows configurations the original forbids' if not backward
                                          else 'read-back forbids configurations the original allows')
                if not (forward and backward):
                    diffs.append('configurations')
            out[fmt] = {'status': 'differs' if diffs else 'ok', 'detail': '; '.join(diffs),
                        'equivalence': equivalence}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--timeout', type=int, default=300, help='per model, all formats')
    ap.add_argument('--max-features', type=int, default=0)
    ap.add_argument('--one', nargs=1, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.one:
        try:
            print(json.dumps(one(args.one[0])))
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({'_error': f'{type(exc).__name__}: {exc}'[:200]}))
        return

    with open(ROOT / 'prepared/manifest.csv', newline='') as fh:
        rows = [r for r in csv.DictReader(fh) if not r['excluded'] and not r['duplicate_of']
                and (not args.max_features or int(r['n_features'] or 0) <= args.max_features)]
    print(f'{len(rows)} models x {len(FORMATS)} formats', flush=True)
    results: dict[str, dict] = {}

    def run(r):
        try:
            p = subprocess.run([sys.executable, str(Path(__file__)), '--one', str(ROOT / r['path'])],
                               capture_output=True, text=True, timeout=args.timeout, cwd=str(ROOT))
            line = (p.stdout.strip().splitlines() or ['{"_error": "no output"}'])[-1]
            results[r['model_id']] = json.loads(line)
        except subprocess.TimeoutExpired:
            results[r['model_id']] = {'_error': f'timeout after {args.timeout} s'}

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for i, _ in enumerate(pool.map(run, rows), 1):
            if i % 200 == 0:
                print(f'  {i}/{len(rows)}', flush=True)

    table: dict[str, Counter] = defaultdict(Counter)
    details: dict[tuple[str, str], Counter] = defaultdict(Counter)
    with open(ROOT / 'rq1/roundtrip.csv', 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['model_id', 'n_features', 'language_level', 'format', 'status', 'detail', 'equivalence'])
        nf = {r['model_id']: r for r in rows}
        for mid, res in sorted(results.items()):
            if '_error' in res:
                for fmt in FORMATS:
                    table[fmt]['model_error'] += 1
                    w.writerow([mid, nf[mid]['n_features'], nf[mid]['language_level'], fmt, 'model_error', res['_error'], ''])
                continue
            for fmt, v in res.items():
                table[fmt][v['status']] += 1
                if v['status'] != 'ok':
                    details[(fmt, v['status'])][v['detail'].split(':')[0][:60]] += 1
                w.writerow([mid, nf[mid]['n_features'], nf[mid]['language_level'], fmt, v['status'], v['detail'],
                            v.get('equivalence', '')])
    statuses = ['ok', 'differs', 'unsupported', 'write_error', 'read_error', 'analysis_error', 'model_error']
    lines = ['# RQ1 transformations: format round-trips', '',
             f'{len(rows)} unique models; UVL -> format -> flamapy reader, compared on features, number of '
             'constraints and logical equivalence (SAT: each model entails every CNF clause of the other).', '',
             '| Format | ' + ' | '.join(statuses) + ' |', '|---|' + '---|' * len(statuses)]
    for fmt in FORMATS:
        lines.append(f'| {fmt} | ' + ' | '.join(str(table[fmt].get(s, 0)) for s in statuses) + ' |')
    lines += ['', '## Most frequent non-ok details', '']
    for (fmt, st), c in sorted(details.items()):
        for d, n in c.most_common(4):
            lines.append(f'- {fmt} / {st}: {n} x `{d}`')
    (ROOT / 'rq1/report_roundtrip.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()

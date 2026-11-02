"""Report for rq1/verify_cnf_methods.py: do tseytin/auto give the same answers as distributive?

    python rq1/check_cnf_methods.py results/cnf_methods/<run> [--title "..."]

Writes <run>/report.md and <run>/failures.csv. For every operation and method:

  same     equal to the distributive result (and to the reference where one exists)
  DIFF     differs from distributive / the reference (a correctness failure)
  ERROR    the method raised while distributive did not
  n/a      not run (timeout of that phase, or the operation does not apply to the model)

Sample-producing operations (Sampling, TWiseSampling, CoveringArray) are nondeterministic in
their exact output across encodings, so they pass when every configuration is valid, no auxiliary
variable leaks into the result, and (t-wise) coverage is complete; sizes are reported as info.
ApproxMC (sharpsat) is compared with the exact count within its (epsilon=0.8) tolerance.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
METHODS = ('distributive', 'tseytin', 'auto')
SAMPLE_OPS = {'Sampling', 'TWiseSampling', 'CoveringArray', 'sharpsat:Sampling'}


def digest(configs: list[list[str]]) -> str:
    return hashlib.sha256(json.dumps(configs).encode()).hexdigest()[:16]


def load_oracle() -> dict[str, dict[str, Any]]:
    oracle = {}
    for line in (ROOT / 'rq1/oracle_values.jsonl').read_text().splitlines():
        d = json.loads(line)
        oracle[d['model_id']] = d
    return oracle


def sample_ok(v: Any) -> tuple[bool, str]:
    if not isinstance(v, dict):
        return False, f'unexpected {v!r}'[:80]
    problems = []
    if v.get('invalid'):
        problems.append(f"{v['invalid']} invalid")
    if v.get('leaks'):
        problems.append(f"leaks {v['leaks'][:3]}")
    if 'coverage' in v and v['coverage'] != 1.0:
        problems.append(f"coverage {v['coverage']:.3f}")
    return not problems, ', '.join(problems)


def reference_value(op: str, model: dict[str, Any], ref: dict[str, Any] | None, values: dict[str, Any]) -> Any:
    """Expected value from the oracle / BDD, or a sentinel when there is none."""
    if ref is None:
        return NotImplemented
    base = op.split(':', 1)[1] if op.startswith(('sdd:', 'sharpsat:')) else op
    if ref.get('_kind') == 'bdd':
        if base in ('Satisfiable', 'ConfigurationsNumber', 'CoreFeatures', 'DeadFeatures') and base in ref:
            return ref[base]
        return NotImplemented
    # exhaustive oracle
    if base in ('Satisfiable', 'ConfigurationsNumber', 'CoreFeatures', 'DeadFeatures'):
        return ref[base]
    if base == 'Configurations':
        return {'n': len(ref['Configurations']), 'sha': digest(ref['Configurations'])}
    if base == 'Filter':
        f = (values.get('distributive') or {}).get('feature')
        if f is None:
            return NotImplemented
        sel = [c for c in ref['Configurations'] if f in c]
        return {'n': len(sel), 'sha': digest(sel)}
    if base == 'Commonality':
        f = model.get('_filter_feature')
        return ref['FeatureInclusionProbability'].get(f, NotImplemented) if f else NotImplemented
    if base == 'MinimumConfiguration':
        return min((len(c) for c in ref['Configurations']), default=None)
    if base == 'FalseOptionalFeatures':
        return ('either', ref['FalseOptionalFeatures_vacuous'], ref['FalseOptionalFeatures_live'])
    return NotImplemented


def matches(op: str, got: Any, want: Any) -> bool:
    base = op.split(':', 1)[1] if ':' in op else op
    if isinstance(want, tuple) and want and want[0] == 'either':
        return got in (want[1], want[2])
    if base in ('Configurations', 'Filter'):
        return isinstance(got, dict) and got.get('n') == want['n'] and got.get('sha') == want['sha'] \
            and not got.get('leaks') and not got.get('leaked_elements')
    if base == 'MinimumConfiguration':
        if want is None:
            return got is None
        return isinstance(got, dict) and got['size'] == want and got['valid'] and not got['leaks']
    if base == 'Commonality':
        return isinstance(got, float) and abs(got - want) < 1e-9
    if op == 'sharpsat:ConfigurationsNumber':
        g, w = int(got), int(want)
        return (g == w == 0) or (w > 0 and w / 1.8 <= g <= w * 1.8)
    return got == want


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('run')
    ap.add_argument('--title', default='')
    args = ap.parse_args()
    run = Path(args.run)
    oracle = load_oracle()

    table: dict[tuple[str, str, str], Counter[str]] = defaultdict(Counter)   # (set, op, method)
    failures: list[list[Any]] = []
    phase_status: dict[str, Counter[str]] = defaultdict(Counter)
    ctc = Counter()
    ctc_failures: list[list[Any]] = []
    sizes: dict[tuple[str, str], list[int]] = defaultdict(list)
    models_per_set = Counter()
    aux_stats: dict[tuple[str, str], list[int]] = defaultdict(list)
    unchecked: list[tuple[str, str, str]] = []

    records: dict[tuple[str, str], dict[str, Any]] = {}
    for f in sorted(run.glob('*.jsonl')):
        for line in f.read_text().splitlines():
            rec = json.loads(line)
            key = (rec['set'], rec['model_id'])
            prev = records.get(key)
            if rec.get('partial') and prev is not None:
                # a --phases rerun replaces only those phases of the previous record
                redo = set(rec['partial'])
                rec = {**prev,
                       'status': {**{k: v for k, v in prev['status'].items()
                                     if k.split('@')[0] not in redo}, **rec['status']},
                       'events': [e for e in prev['events'] if e.get('phase', '').split('@')[0] not in redo]
                       + rec['events']}
            records[key] = rec   # a full rerun (--force) supersedes
    for rec in records.values():
        s, mid = rec['set'], rec['model_id']
        models_per_set[s] += 1
        for ph, st in rec['status'].items():
            phase_status[f'{s}/{ph}'][st.split(':')[0]] += 1
            if st.startswith('crash') and ph == 'sat':
                unchecked.append((s, mid, st[:160]))
        vals: dict[str, dict[str, Any]] = defaultdict(dict)
        errs: dict[str, dict[str, str]] = defaultdict(dict)
        bdd = None
        model: dict[str, Any] = {}
        for e in rec['events']:
            if e['ev'] == 'op':
                if 'error' in e:
                    errs[e['op']][e['method']] = e['error']
                else:
                    vals[e['op']][e['method']] = e['value']
            elif e['ev'] == 'bdd' and 'error' not in e:
                bdd = {**e, '_kind': 'bdd'}
            elif e['ev'] == 'ctc_equivalence' and 'error' not in e:
                ctc['checked'] += e['checked']
                ctc['failed'] += e['failed']
                ctc['skipped_wide'] += e['skipped_wide']
                for x in e['failures']:
                    ctc_failures.append([mid, x])
            elif e['ev'] == 'method' and 'error' not in e:
                aux_stats[(s, e['method'])].append(e['aux'])
            elif e['ev'] == 'method':
                errs['<transform>'][e['method']] = e['error']
        filt = vals.get('Filter', {}).get('distributive')
        model['_filter_feature'] = filt.get('feature') if isinstance(filt, dict) else None
        ref = oracle.get(mid) if s == 'oracle' else bdd
        if ref is None and s != 'oracle':
            # no BDD: the distributive PySAT answers (exact) are the reference for the compilers
            pysat = {k: vals[k]['distributive'] for k in ('Satisfiable', 'CoreFeatures', 'DeadFeatures')
                     if 'distributive' in vals.get(k, {})}
            ref = {**pysat, '_kind': 'bdd'} if pysat else None

        for op in sorted(set(vals) | set(errs)):
            dist_v = vals[op].get('distributive', NotImplemented)
            want = reference_value(op, model, ref, vals[op])
            row = re.sub(r'\[.*\]$', '[*]', op)   # one row per explanation operation
            for method in METHODS:
                if method in errs[op]:
                    if method != 'distributive' and 'distributive' in errs[op]:
                        table[(s, row, method)]['error (also distributive)'] += 1
                    else:
                        table[(s, row, method)]['ERROR'] += 1
                        failures.append([s, mid, op, method, 'error', errs[op][method][:200], ''])
                    continue
                if method not in vals[op]:
                    table[(s, row, method)]['n/a'] += 1
                    continue
                got = vals[op][method]
                if op in SAMPLE_OPS:
                    ok, why = sample_ok(got)
                    if isinstance(got, dict) and 'n' in got:
                        sizes[(op, method)].append(got['n'])
                    if op == 'Sampling' and isinstance(got, dict) and isinstance(dist_v, dict) \
                            and got.get('n') != dist_v.get('n'):
                        ok, why = False, f"size {got.get('n')} vs {dist_v.get('n')}"
                else:
                    ok, why = True, ''
                    if want is not NotImplemented and not matches(op, got, want):
                        ok, why = False, 'reference'
                    elif method != 'distributive' and dist_v is not NotImplemented \
                            and op != 'sharpsat:ConfigurationsNumber' and got != dist_v:
                        ok, why = False, 'distributive'
                    if isinstance(got, dict) and (got.get('leaks') or got.get('leaked_elements')):
                        ok, why = False, f"aux leak ({got.get('leaked_elements') or got.get('leaks')})"
                if ok:
                    table[(s, row, method)]['same'] += 1
                else:
                    table[(s, row, method)]['DIFF'] += 1
                    exp = want if want is not NotImplemented else dist_v
                    failures.append([s, mid, op, method, why, json.dumps(got)[:200], json.dumps(exp, default=str)[:200]])

    lines = [f'# ClauseSet CNF methods: verification{" — " + args.title if args.title else ""}', '',
             'Generated by `rq1/check_cnf_methods.py` from `rq1/verify_cnf_methods.py` events.', '',
             '## Models and phases', '']
    for s, n in sorted(models_per_set.items()):
        lines.append(f'* **{s}**: {n} models')
    for k, c in sorted(phase_status.items()):
        lines.append(f'  * phase {k}: ' + ', '.join(f'{a} {b}' for a, b in c.most_common()))
    lines += ['', '## Auxiliary variables per model', '', '| set | method | models with aux | mean aux |', '|---|---|---|---|']
    for (s, m), xs in sorted(aux_stats.items()):
        nz = [x for x in xs if x]
        lines.append(f'| {s} | {m} | {len(nz)}/{len(xs)} | {(sum(xs) / len(xs)) if xs else 0:.1f} |')
    if unchecked:
        lines += ['', '## Models that could not be checked (fail before any CNF method)', '']
        lines += [f'* {s_}/{m}: {why}' for s_, m, why in unchecked]
    lines += ['', '## Per-constraint projected equivalence (Tseytin vs distributive)', '',
              f"{ctc['checked']} constraints checked exhaustively, {ctc['failed']} not equivalent, "
              f"{ctc['skipped_wide']} skipped (more than 16 features).", '']
    for s in sorted(models_per_set):
        ops = sorted({op for (ss, op, _m) in table if ss == s})
        lines += [f'## Operations — {s}', '', '| operation | ' + ' | '.join(METHODS) + ' |', '|---|---|---|---|']
        for op in ops:
            cells = []
            for m in METHODS:
                c = table.get((s, op, m), Counter())
                cells.append(', '.join(f'{k} {v}' for k, v in c.most_common()) or '')
            lines.append(f'| {op} | ' + ' | '.join(cells) + ' |')
        lines.append('')
    lines += ['## Sample sizes (info)', '', '| operation | ' + ' | '.join(METHODS) + ' |', '|---|---|---|---|']
    for op in sorted({o for (o, _m) in sizes}):
        cells = []
        for m in METHODS:
            xs = sizes.get((op, m), [])
            cells.append(f'mean {sum(xs) / len(xs):.2f} (n={len(xs)})' if xs else '')
        lines.append(f'| {op} | ' + ' | '.join(cells) + ' |')
    by_kind = Counter((f[2], f[3], f[4].split(' (')[0]) for f in failures)
    lines += ['', f'## Failures: {len(failures)} (details in failures.csv)', '']
    for (op, m, why), n in by_kind.most_common(40):
        lines.append(f'* {op} / {m}: {why} × {n}')
    if ctc_failures:
        lines += ['', '### Non-equivalent constraints', ''] + [f'* {a}: `{b}`' for a, b in ctc_failures[:20]]
    (run / 'report.md').write_text('\n'.join(lines) + '\n')
    with open(run / 'failures.csv', 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['set', 'model_id', 'operation', 'method', 'why', 'got', 'expected'])
        w.writerows(failures)
    print('\n'.join(lines))


if __name__ == '__main__':
    main()

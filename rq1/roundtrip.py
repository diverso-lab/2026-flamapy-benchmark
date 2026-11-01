"""RQ1 (transformations): round-trip every model through each format flamapy can write and read.

For each unique model in prepared/manifest.csv and each format F in {UVL, FeatureIDE XML,
Glencoe JSON, AFM, flamapy JSON, FaMa XML}:

    UVL --UVLReader--> FM --F writer--> file --F reader--> FM'

and FM' is compared with FM on:
  * the set of feature names,
  * the number of cross-tree constraints,
  * semantics: number of configurations (BDD; only when both compile within the time limit) and
    core and dead features (SAT), which together detect lost, added or altered constraints.

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


def semantics(fm, limit_s: int):
    """(feature names, #ctcs, configuration count or None, core, dead)."""
    import signal
    from flamapy.metamodels.pysat_metamodel.operations import PySATCoreFeatures, PySATDeadFeatures
    from flamapy.metamodels.pysat_metamodel.transformations import FmToPysat
    names = sorted(f.name for f in fm.get_features())
    sat = FmToPysat(fm).transform()
    core = sorted(map(str, PySATCoreFeatures().execute(sat).get_result()))
    dead = sorted(map(str, PySATDeadFeatures().execute(sat).get_result()))
    count = None

    def _alarm(*_):
        raise TimeoutError()
    old = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(limit_s)
    try:
        from flamapy.metamodels.bdd_metamodel.operations import BDDConfigurationsNumber
        from flamapy.metamodels.bdd_metamodel.transformations import FmToBDD
        count = str(BDDConfigurationsNumber().execute(FmToBDD(fm).transform()).get_result())
    except Exception:  # noqa: BLE001 - count stays unknown (timeout or BDD limitation)
        count = None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    return names, len(fm.get_constraints()), count, core, dead


def one(path: str, limit_s: int) -> dict:
    import logging
    import warnings
    warnings.filterwarnings('ignore')
    logging.disable(logging.WARNING)
    import flamapy.metamodels.fm_metamodel.transformations as t
    fm = t.UVLReader(path).transform()
    ref = semantics(fm, limit_s)
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for fmt, (w, r, ext) in FORMATS.items():
            target = str(Path(tmp) / f'model{ext}')
            try:
                getattr(t, w)(target, fm).transform()
            except Exception as exc:  # noqa: BLE001
                msg = f'{type(exc).__name__}: {exc}'[:150]
                kind = 'unsupported' if any(k in msg.lower() for k in ('not supported', 'unsupported', 'cannot be represented')) else 'write_error'
                out[fmt] = {'status': kind, 'detail': msg}
                continue
            try:
                fm2 = getattr(t, r)(target).transform()
            except Exception as exc:  # noqa: BLE001
                out[fmt] = {'status': 'read_error', 'detail': f'{type(exc).__name__}: {exc}'[:150]}
                continue
            try:
                got = semantics(fm2, limit_s)
            except Exception as exc:  # noqa: BLE001
                out[fmt] = {'status': 'analysis_error', 'detail': f'{type(exc).__name__}: {exc}'[:150]}
                continue
            diffs = []
            if got[0] != ref[0]:
                diffs.append(f'features (+{len(set(got[0]) - set(ref[0]))}/-{len(set(ref[0]) - set(got[0]))})')
            if got[1] != ref[1]:
                diffs.append(f'constraints {ref[1]}->{got[1]}')
            if ref[2] is not None and got[2] is not None and got[2] != ref[2]:
                diffs.append(f'configurations {ref[2]}->{got[2]}')
            if got[3] != ref[3]:
                diffs.append('core')
            if got[4] != ref[4]:
                diffs.append('dead')
            out[fmt] = {'status': 'differs' if diffs else 'ok', 'detail': '; '.join(diffs),
                        'count_checked': ref[2] is not None and got[2] is not None}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--timeout', type=int, default=300, help='per model, all formats')
    ap.add_argument('--bdd-limit', type=int, default=20, help='seconds for each BDD count')
    ap.add_argument('--max-features', type=int, default=0)
    ap.add_argument('--one', nargs=2, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.one:
        try:
            print(json.dumps(one(args.one[0], int(args.one[1]))))
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
            p = subprocess.run([sys.executable, str(Path(__file__)), '--one', str(ROOT / r['path']), str(args.bdd_limit)],
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
        w.writerow(['model_id', 'n_features', 'language_level', 'format', 'status', 'detail', 'count_checked'])
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
                            v.get('count_checked', '')])
    statuses = ['ok', 'differs', 'unsupported', 'write_error', 'read_error', 'analysis_error', 'model_error']
    lines = ['# RQ1 transformations: format round-trips', '',
             f'{len(rows)} unique models; UVL -> format -> flamapy reader, compared on features, number of '
             'constraints, configuration count (BDD, when computable) and core/dead features (SAT).', '',
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

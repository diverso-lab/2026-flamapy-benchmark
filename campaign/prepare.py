"""Build prepared/manifest.csv: the model list every campaign reads.

For each .uvl in the UVLHub archive it records:
  * model_id      path inside the archive (the old runner used the file name only, and 6 names
                  occur more than once in the archive)
  * sha256        content hash; byte-identical files point to the first one via duplicate_of
  * composed      the file uses UVL `imports`; excluded from the campaign (decision 2026-11-01:
                  flamapy's FlatFM does not resolve the Alias.Root.Feature references these
                  models use, so they are reported as a known limitation instead)
  * parse         whether flamapy's UVLReader reads it, plus feature/constraint counts and the
                  UVL language level
  * fama          whether the model converts to FaMa XML without dropping constraints; if so the
                  XML is written to prepared/fama/<model_id>.fama

    python -m campaign.prepare [--zip uvlhub_bulk_2026_03_13.zip] [--jobs 8]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / '.models'
PREPARED = ROOT / 'prepared'

FIELDS = ['model_id', 'path', 'sha256', 'duplicate_of', 'composed', 'parse_status', 'parse_error',
          'n_features', 'n_constraints', 'language_level', 'fama_status', 'fama_dropped', 'fama_path',
          'excluded', 'exclusion_reason']


def _inspect_one(path: str, fama_out: str) -> dict[str, str]:
    """Runs in a child process (python -m campaign.prepare --one ...)."""
    import logging
    import warnings
    warnings.filterwarnings('ignore')
    logging.disable(logging.WARNING)
    sys.path.insert(0, str(ROOT))
    out: dict[str, str] = {}
    try:
        from flamapy.metamodels.fm_metamodel.transformations import UVLReader
        fm = UVLReader(path).transform()
        out['parse_status'] = 'ok'
        out['n_features'] = str(len(fm.get_features()))
        out['n_constraints'] = str(len(fm.get_constraints()))
    except Exception as exc:  # noqa: BLE001
        out['parse_status'] = 'error'
        out['parse_error'] = f'{type(exc).__name__}: {exc}'[:200]
        return out
    try:
        from flamapy.metamodels.fm_metamodel.operations import FMLanguageLevel
        level = FMLanguageLevel().execute(fm).get_result()
        out['language_level'] = str(level)[:120]
    except Exception as exc:  # noqa: BLE001
        out['language_level'] = f'error: {type(exc).__name__}'
    try:
        from scripts.fama_xml import fm_to_fama_xml
        Path(fama_out).parent.mkdir(parents=True, exist_ok=True)
        dropped = fm_to_fama_xml(fm, fama_out)
        if dropped:
            Path(fama_out).unlink(missing_ok=True)
            out['fama_status'] = 'lossy'
            out['fama_dropped'] = str(len(dropped))
        else:
            out['fama_status'] = 'lossless'
            out['fama_path'] = str(Path(fama_out).relative_to(ROOT))
    except Exception as exc:  # noqa: BLE001
        Path(fama_out).unlink(missing_ok=True)
        out['fama_status'] = f'error: {type(exc).__name__}: {exc}'[:120]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--zip', default=str(ROOT / 'uvlhub_bulk_2026_03_13.zip'))
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--timeout', type=int, default=300, help='per-model inspection timeout (s)')
    ap.add_argument('--one', nargs=2, metavar=('UVL', 'FAMA_OUT'), help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.one:
        print(json.dumps(_inspect_one(*args.one)))
        return

    if not MODELS.exists() or not any(MODELS.rglob('*.uvl')):
        with zipfile.ZipFile(args.zip) as zf:
            members = [m for m in zf.namelist() if m.lower().endswith('.uvl') and not m.startswith('__MACOSX')]
            for m in members:
                zf.extract(m, MODELS)
    paths = sorted(MODELS.rglob('*.uvl'))
    print(f'{len(paths)} UVL files', flush=True)

    rows: list[dict[str, str]] = []
    first_by_hash: dict[str, str] = {}
    for p in paths:
        raw = p.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        model_id = str(p.relative_to(MODELS))
        text = raw.decode('utf-8', errors='replace').lstrip('﻿')
        composed = any(line.strip() == 'imports' for line in text.splitlines()[:5])
        rows.append({'model_id': model_id, 'path': str(p.relative_to(ROOT)), 'sha256': sha,
                     'duplicate_of': first_by_hash.get(sha, ''), 'composed': 'yes' if composed else ''})
        first_by_hash.setdefault(sha, model_id)

    def inspect(row: dict[str, str]) -> None:
        fama_out = str(PREPARED / 'fama' / (row['model_id'] + '.fama'))
        try:
            res = subprocess.run([sys.executable, '-m', 'campaign.prepare', '--one', str(ROOT / row['path']), fama_out],
                                 cwd=str(ROOT), capture_output=True, text=True, timeout=args.timeout)
            last = (res.stdout.strip().splitlines() or ['{}'])[-1]
            row.update(json.loads(last) if last.startswith('{') else
                       {'parse_status': 'error', 'parse_error': (res.stderr.strip().splitlines() or ['?'])[-1][:200]})
        except subprocess.TimeoutExpired:
            row.update({'parse_status': 'timeout', 'parse_error': f'inspection exceeded {args.timeout} s'})

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for i, _ in enumerate(pool.map(inspect, rows), 1):
            if i % 100 == 0:
                print(f'  inspected {i}/{len(rows)}', flush=True)

    for row in rows:
        reasons = []
        if row['composed']:
            reasons.append('composed (UVL imports)')
        if row.get('parse_status') != 'ok':
            reasons.append(f"flamapy parse {row.get('parse_status')}")
        row['excluded'] = 'yes' if reasons else ''
        row['exclusion_reason'] = '; '.join(reasons)

    PREPARED.mkdir(exist_ok=True)
    with open(PREPARED / 'manifest.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction='ignore')
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, '') for k in FIELDS})

    n = len(rows)
    excl = sum(1 for r in rows if r['excluded'])
    dups = sum(1 for r in rows if r['duplicate_of'] and not r['excluded'])
    fama = sum(1 for r in rows if r.get('fama_status') == 'lossless' and not r['excluded'] and not r['duplicate_of'])
    print(f'{n} files: {excl} excluded, {dups} duplicates of another file, '
          f'{n - excl - dups} unique analysable models, {fama} of them lossless in FaMa XML')
    print(f'manifest: {PREPARED / "manifest.csv"}')


if __name__ == '__main__':
    main()

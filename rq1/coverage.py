"""RQ1 (code coverage): statement and branch coverage of each flamapy repository's own test suite.

Runs `coverage run --branch -m pytest` inside every repository listed in
campaign/flamapy_commits.txt (checked out under .build/flamapy by scripts/setup_env.sh, or another
directory given with --src), without writing anything into the repositories (coverage data and
pytest cache go to a temporary directory).

    .venv/bin/python rq1/coverage.py [--src .build/flamapy]

Writes rq1/coverage.csv and rq1/report_coverage.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', default=str(ROOT / '.build/flamapy'))
    args = ap.parse_args()
    src = Path(args.src).expanduser()
    repos = [l.split()[0] for l in (ROOT / 'campaign/flamapy_commits.txt').read_text().splitlines()
             if l.strip() and not l.startswith('#')]
    py = str(ROOT / '.venv/bin/python')
    rows = []
    for repo in repos:
        d = src / repo
        if not (d / 'tests').exists() and not any(d.glob('test*')):
            rows.append({'repo': repo, 'status': 'no tests'})
            continue
        commit = subprocess.run(['git', '-C', str(d), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, COVERAGE_FILE=str(Path(tmp) / '.coverage'), PYTHONDONTWRITEBYTECODE='1')
            test = subprocess.run([py, '-m', 'coverage', 'run', '--branch', f'--source={d / "flamapy"}', '-m', 'pytest', '-q',
                                   '-p', 'no:cacheprovider', '-o', f'cache_dir={tmp}'],
                                  cwd=str(d), env=env, capture_output=True, text=True, timeout=3600)
            summary = (test.stdout.strip().splitlines() or [''])[-1]
            rep = subprocess.run([py, '-m', 'coverage', 'json', '-o', str(Path(tmp) / 'cov.json')],
                                 cwd=str(d), env=env, capture_output=True, text=True)
            if rep.returncode != 0:
                rows.append({'repo': repo, 'commit': commit, 'status': 'no coverage data', 'pytest': summary})
                continue
            totals = json.loads((Path(tmp) / 'cov.json').read_text())['totals']
        rows.append({
            'repo': repo, 'commit': commit, 'status': 'ok' if test.returncode == 0 else f'pytest exit {test.returncode}',
            'pytest': summary, 'statements': totals['num_statements'], 'covered': totals['covered_lines'],
            'branches': totals.get('num_branches', ''), 'covered_branches': totals.get('covered_branches', ''),
            'line_pct': round(100 * totals['covered_lines'] / max(totals['num_statements'], 1), 1),
            'branch_pct': round(100 * totals['covered_branches'] / max(totals['num_branches'], 1), 1)
            if totals.get('num_branches') else '',
        })
        print(f"{repo:18s} {rows[-1].get('line_pct', '')}% lines  {rows[-1].get('branch_pct', '')}% branches  {summary}", flush=True)
    fields = ['repo', 'commit', 'status', 'pytest', 'statements', 'covered', 'line_pct', 'branches', 'covered_branches', 'branch_pct']
    with open(ROOT / 'rq1/coverage.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in fields})
    lines = ['# RQ1 code coverage of the flamapy test suites', '',
             '| Repository | commit | line coverage | branch coverage | pytest |', '|---|---|---|---|---|']
    for r in rows:
        lines.append(f"| {r['repo']} | {r.get('commit', '')} | {r.get('line_pct', '')}% ({r.get('covered', '')}/{r.get('statements', '')}) "
                     f"| {r.get('branch_pct', '')}% | {r.get('pytest', r['status'])} |")
    (ROOT / 'rq1/report_coverage.md').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    sys.exit(main())

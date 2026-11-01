"""Prepare the FaMa test suite models for the runner (prepared/fama_suite/manifest.csv).

FaMa reads the original FaMa XML. flamapy and FeatureIDE read a UVL version produced with
flamapy's XMLReader -> UVLWriter (the round-trip study checks that this conversion is exact).

    .venv/bin/python rq1/prepare_fama_suite.py [path/to/fm_metamodel]
"""
import csv, hashlib, json, logging, shutil, sys, warnings
from pathlib import Path
warnings.filterwarnings('ignore'); logging.disable(logging.WARNING)
ROOT = Path(__file__).resolve().parent.parent
FM = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / '.build/flamapy/fm_metamodel'
from flamapy.metamodels.fm_metamodel.transformations import XMLReader, UVLWriter

expected = json.loads((ROOT / 'rq1/fama_suite_expected.json').read_text())
out = ROOT / 'prepared/fama_suite'
out.mkdir(parents=True, exist_ok=True)
rows = []
for rel in sorted(expected):
    src = FM / rel
    mid = rel.split('fama_test_suite/', 1)[1]
    xml_dst = out / 'xml' / mid
    uvl_dst = (out / 'uvl' / mid).with_suffix('.uvl')
    xml_dst.parent.mkdir(parents=True, exist_ok=True); uvl_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, xml_dst)
    row = {'model_id': mid, 'path': str(uvl_dst.relative_to(ROOT)), 'sha256': hashlib.sha256(src.read_bytes()).hexdigest(),
           'duplicate_of': '', 'composed': '', 'fama_path': str(xml_dst.relative_to(ROOT)), 'excluded': '', 'exclusion_reason': ''}
    try:
        fm = XMLReader(str(src)).transform()
        UVLWriter(str(uvl_dst), fm).transform()
        row.update(parse_status='ok', n_features=len(fm.get_features()), n_constraints=len(fm.get_constraints()))
    except Exception as exc:
        row.update(parse_status='error', parse_error=str(exc)[:200], excluded='yes', exclusion_reason='conversion failed')
    rows.append(row)
fields = ['model_id','path','sha256','duplicate_of','composed','parse_status','parse_error','n_features','n_constraints',
          'language_level','fama_status','fama_dropped','fama_path','excluded','exclusion_reason']
with open(out / 'manifest.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=fields); w.writeheader()
    for r in rows: w.writerow({k: r.get(k, '') for k in fields})
print(f"{len(rows)} suite models, {sum(1 for r in rows if r['excluded'])} failed conversion -> {out/'manifest.csv'}")

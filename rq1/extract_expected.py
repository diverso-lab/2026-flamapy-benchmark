"""Extract the FaMa test suite's expected results from flamapy's pytest files.

The FaMa test suite (Segura et al.) ships 48 small FaMa-XML models whose expected analysis
results exist only as pytest parameters in flamapy/tests/test_fama_suite_{sat,bdd}.py. This
script reads those files with `ast` (nothing is imported or executed) and writes
rq1/fama_suite_expected.json:

    {"<model path relative to fm_metamodel>": {"CoreFeatures": [...], "ConfigurationsNumber": "4", ...}}

Only parameter-free operations are extracted. When the SAT and BDD test files disagree on an
expected value, both are kept and the conflict is reported.

    .venv/bin/python rq1/extract_expected.py [path/to/flamapy/tests]
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / '.build/flamapy/flamapy/tests'

OPS = {
    'test_false_optional_operation': 'FalseOptionalFeatures',
    'test_core_features_operation': 'CoreFeatures',
    'test_dead_features_operation': 'DeadFeatures',
    'test_number_of_configurations_operation': 'ConfigurationsNumber',
    'test_configurations_operation': 'Configurations',
    'test_valid_model_operation': 'Satisfiable',
}


def normalise(op: str, value):
    if op == 'Satisfiable':
        return bool(value)
    if op == 'ConfigurationsNumber':
        return str(int(value))
    if op == 'Configurations':
        return sorted(sorted(c) for c in value)
    return sorted(value)


def main() -> None:
    expected: dict[str, dict[str, object]] = {}
    sources: dict[tuple[str, str], list[tuple[str, object]]] = {}
    for test_file in sorted(TESTS.glob('test_fama_suite_*.py')):
        tree = ast.parse(test_file.read_text())
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef) or fn.name not in OPS:
                continue
            op = OPS[fn.name]
            for deco in fn.decorator_list:
                if not (isinstance(deco, ast.Call) and getattr(deco.func, 'attr', '') == 'parametrize'):
                    continue
                for case in deco.args[1].elts:
                    if not isinstance(case, ast.Tuple) or len(case.elts) != 2:
                        continue
                    try:
                        path = ast.literal_eval(case.elts[0])
                        value = normalise(op, ast.literal_eval(case.elts[1]))
                    except (ValueError, TypeError):
                        continue
                    key = path.removeprefix('./')
                    sources.setdefault((key, op), []).append((test_file.name, value))
    conflicts = 0
    for (path, op), vals in sorted(sources.items()):
        distinct = {json.dumps(v, sort_keys=True) for _, v in vals}
        if len(distinct) > 1:
            conflicts += 1
            print(f'CONFLICT {path} {op}: ' + '; '.join(f'{f}={v}' for f, v in vals))
            expected.setdefault(path, {})[op] = {'conflict': {f: v for f, v in vals}}
        else:
            expected.setdefault(path, {})[op] = vals[0][1]
    out = ROOT / 'rq1/fama_suite_expected.json'
    out.write_text(json.dumps(expected, indent=1, sort_keys=True))
    n_cases = sum(len(v) for v in expected.values())
    print(f'{len(expected)} models, {n_cases} expected results, {conflicts} SAT/BDD conflicts -> {out}')


if __name__ == '__main__':
    main()

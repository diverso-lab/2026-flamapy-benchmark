"""Verification gate for ClauseSet's CNF encodings (distributive / tseytin / auto).

ClauseSet (fm_metamodel) can encode cross-tree constraints with Tseytin auxiliary variables
(`cnf_method='tseytin'`) or pick per constraint (`'auto'`). The Tseytin gates are full
biconditionals, so every assignment of the features extends to exactly one assignment of the
auxiliary variables: the feature-level semantics, and the model count, are unchanged. This
script checks that every consumer of ClauseSet really behaves the same under the three methods:

  pysat       Satisfiable, CoreFeatures, DeadFeatures, FalseOptionalFeatures,
              ConfigurationsNumber, Configurations, Filter, SatisfiableConfiguration (partial and
              full), Commonality, Sampling, MinimumConfiguration, TWiseSampling, CoveringArray,
              ConfigurationConflict / ConfigurationRepair
  diagnosis   HSDAG diagnoses and conflicts (model and configuration tasks), ExplainVoidModel,
              ExplainDeadFeature, ExplainFalseOptional
  sdd         Satisfiable, ConfigurationsNumber, CoreFeatures, DeadFeatures
  sharpsat    ConfigurationsNumber (ApproxMC, projected on the features), Sampling
  dnnf        only when the d4 binary is available

Three model sets:

  oracle      the 1,047 small Boolean models of rq1/oracle.py; reference = exhaustive oracle
  auto        the benchmark models where `auto` picks Tseytin for at least one constraint
              (ClauseSet.use_tseytin); reference = distributive results + BDD when it compiles
  resources   the UVL models shipped with the flamapy plugins (resources/, tests/resources/)

Checks, per model and method:
  * every result equals the distributive one (the existing behaviour), and the reference;
  * no result mentions an auxiliary variable (None, or a name that is not a feature);
  * every configuration returned (samples, t-wise, minimum, filter) is a valid configuration;
  * per constraint, the Tseytin clauses projected on the features are equivalent to the
    distributive clauses (exhaustive over the constraint's features).

    <python> rq1/verify_cnf_methods.py [--set oracle,auto,resources] [--jobs 8] [--timeout 300]

`<python>` must have the flamapy develop plugins installed (the session venv `venv_verify`).
Writes results/cnf_methods/<set>.jsonl and rq1/report_cnf_methods.md.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import shutil
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
METHODS = ('distributive', 'tseytin', 'auto')
FLAMAPY = Path.home() / 'Documents/Projects/Work/Research/Software/flamapy/distribution'


# --------------------------------------------------------------------------- worker side

def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, default=str) + '\n')
    sys.stdout.flush()


def names_of(value: Any) -> Any:
    """Canonical JSON for operation results (feature names, sorted where order is irrelevant)."""
    if value is None or isinstance(value, (bool, float, str)):
        return value
    if isinstance(value, int):
        return str(value)
    elements = getattr(value, 'elements', None)
    if isinstance(elements, dict):
        return sorted(str(getattr(k, 'name', k)) for k, sel in elements.items() if sel)
    if isinstance(value, dict):
        return {str(getattr(k, 'name', k)): names_of(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [names_of(getattr(v, 'name', v)) if not isinstance(v, (list, tuple)) else names_of(v)
                 for v in value]
        if isinstance(value, (set, frozenset)):
            items.sort(key=lambda x: json.dumps(x, sort_keys=True))
        return items
    name = getattr(value, 'name', None)
    return name if isinstance(name, str) else str(value)


def digest(configs: list[list[str]], names: set[str]) -> dict[str, Any]:
    """Count + SHA-256 of a sorted list of configurations (full lists would be gigabytes)."""
    import hashlib
    return {'n': len(configs), 'sha': hashlib.sha256(json.dumps(configs).encode()).hexdigest()[:16],
            'leaks': leaks(configs, names)[:5]}


def sorted_configs(value: Any) -> list[list[str]]:
    return sorted(sorted(str(x) for x in c) for c in names_of(value))


class Checker:
    """Feature-level validity checks against the distributive CNF."""

    def __init__(self, sat_model: Any) -> None:
        from pysat.solvers import Solver
        self.model = sat_model
        self.solver = Solver(name='glucose3', bootstrap_with=sat_model.get_all_clauses().clauses)
        self.names = set(sat_model.variables)

    def valid(self, selected: list[str]) -> bool:
        chosen = set(selected)
        assumptions = [v if n in chosen else -v for n, v in self.model.variables.items()]
        return bool(self.solver.solve(assumptions=assumptions))

    def consistent(self, literals: list[int]) -> bool:
        return bool(self.solver.solve(assumptions=literals))


def leaks(value: Any, names: set[str]) -> list[Any]:
    """Elements of a result that are not feature names (None for an auxiliary variable)."""
    bad: list[Any] = []

    def walk(v: Any) -> None:
        if v is None:
            bad.append(None)
        elif isinstance(v, str):
            if v not in names:
                bad.append(v)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(value)
    return bad


def run_model(path: str, set_name: str, with_configs: bool, phase: str) -> None:
    import logging
    import warnings
    warnings.filterwarnings('ignore')
    logging.disable(logging.WARNING)

    from flamapy.metamodels.configuration_metamodel.models import Configuration
    from flamapy.metamodels.fm_metamodel.models.clause_set import ClauseSet, use_tseytin
    from flamapy.metamodels.fm_metamodel.transformations import UVLReader
    from flamapy.metamodels.pysat_metamodel import operations as po
    from flamapy.metamodels.pysat_metamodel.transformations import FmToPysat
    from flamapy.metamodels.pysat_diagnosis_metamodel import operations as do
    from flamapy.metamodels.pysat_diagnosis_metamodel.transformations import FmToDiagPysat

    # a phase may name one method ('sdd@tseytin'), so each compilation gets its own budget
    phase, _, only_method = phase.partition('@')
    methods = (only_method,) if only_method else METHODS
    fm = UVLReader(path).transform()
    feature_names = [f.name for f in fm.get_features()]
    names = set(feature_names)
    if phase == 'bdd':
        bdd_reference(fm)
        return
    emit({'ev': 'model', 'features': len(feature_names), 'constraints': len(fm.get_constraints()),
          'auto_tseytin_ctcs': sum(1 for c in fm.get_constraints() if use_tseytin(c.ast))})

    # ---- per-constraint projected equivalence (encoding level) ----------------------------
    t0 = time.perf_counter()
    try:
        if phase != 'sat':
            raise _Skip()
        emit({'ev': 'ctc_equivalence', **constraint_equivalence(fm), 't': time.perf_counter() - t0})
    except _Skip:
        pass
    except Exception as exc:  # noqa: BLE001
        emit({'ev': 'ctc_equivalence', 'error': f'{type(exc).__name__}: {exc}'[:300]})

    reference_model = FmToPysat(fm).transform()
    checker = Checker(reference_model)

    # inputs derived once from the distributive model, so every method gets the same ones
    # (only the 'sat' phase needs them; the compiler phases only need `satisfiable`)
    satisfiable = bool(po.PySATSatisfiable().execute(reference_model).get_result())
    sat_phase = phase == 'sat'
    core = set(names_of(po.PySATCoreFeatures().execute(reference_model).get_result())) \
        if sat_phase else set()
    dead = set(names_of(po.PySATDeadFeatures().execute(reference_model).get_result())) \
        if sat_phase else set()
    variant = [n for n in feature_names if n not in core and n not in dead]
    full_true = {n: True for n in feature_names}
    first_config = None
    if satisfiable:
        from pysat.solvers import Solver
        with Solver(name='glucose3', bootstrap_with=reference_model.get_all_clauses().clauses) as s:
            s.solve()
            m = s.get_model()
            first_config = sorted(reference_model.features[v] for v in m
                                  if v > 0 and v in reference_model.features)
    false_optional = names_of(po.PySATFalseOptionalFeatures().execute(reference_model).get_result()) \
        if sat_phase else []

    def partial(d: dict[str, bool], full: bool = False) -> Any:
        c = Configuration(dict(d))
        if full:
            c.set_full(True)
        return c

    ops: dict[str, Callable[[Any, Any], Any]] = {}

    def op(name: str) -> Callable[[Callable[[Any, Any], Any]], Callable[[Any, Any], Any]]:
        def deco(fn: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
            ops[name] = fn
            return fn
        return deco

    @op('Satisfiable')
    def _(m: Any, d: Any) -> Any:
        return bool(po.PySATSatisfiable().execute(m).get_result())

    @op('CoreFeatures')
    def _(m: Any, d: Any) -> Any:
        return sorted(names_of(po.PySATCoreFeatures().execute(m).get_result()))

    @op('DeadFeatures')
    def _(m: Any, d: Any) -> Any:
        return sorted(names_of(po.PySATDeadFeatures().execute(m).get_result()))

    @op('FalseOptionalFeatures')
    def _(m: Any, d: Any) -> Any:
        return sorted(names_of(po.PySATFalseOptionalFeatures().execute(m).get_result()))

    if with_configs:
        @op('ConfigurationsNumber')
        def _(m: Any, d: Any) -> Any:
            return str(po.PySATConfigurationsNumber().execute(m).get_result())

        @op('Configurations')
        def _(m: Any, d: Any) -> Any:
            return digest(sorted_configs(po.PySATConfigurations().execute(m).get_result()), names)

        if variant:
            @op('Filter')
            def _(m: Any, d: Any) -> Any:
                o = po.PySATFilter()
                o.set_configuration(partial({variant[0]: True}))
                products = o.execute(m).get_result()
                bad = sum(1 for p in products for x in p if x is None or str(x) not in names)
                clean = sorted(sorted(str(x) for x in p if x is not None and str(x) in names) for p in products)
                return {**digest(clean, names), 'feature': variant[0], 'leaked_elements': bad}

            @op('Commonality')
            def _(m: Any, d: Any) -> Any:
                o = po.PySATCommonality()
                o.set_configuration(partial({variant[0]: True}))
                return o.execute(m).get_result()

        @op('TWiseSampling')
        def _(m: Any, d: Any) -> Any:
            sample = names_of(po.PySATTWiseSampling(2).execute(m).get_result())
            return {'n': len(sample), 'coverage': coverage(m, sample),
                    'invalid': sum(1 for c in sample if not checker.valid(c)),
                    'leaks': leaks(sample, names)}

        @op('CoveringArray')
        def _(m: Any, d: Any) -> Any:
            sample = names_of(po.PySATCoveringArray(2, 1).execute(m).get_result())
            return {'n': len(sample), 'coverage': coverage(m, sample),
                    'invalid': sum(1 for c in sample if not checker.valid(c)),
                    'leaks': leaks(sample, names)}

    @op('SatisfiableConfiguration.partial')
    def _(m: Any, d: Any) -> Any:
        conf = {variant[0]: True, variant[-1]: False} if len(variant) > 1 else {feature_names[0]: True}
        o = po.PySATSatisfiableConfiguration()
        o.set_configuration(partial(conf))
        return bool(o.execute(m).get_result())

    if first_config is not None:
        @op('SatisfiableConfiguration.full')
        def _(m: Any, d: Any) -> Any:
            out = []
            for conf in (first_config, feature_names):   # a valid one and all-true
                o = po.PySATSatisfiableConfiguration()
                o.set_configuration(partial({n: (n in conf) for n in feature_names}, full=True))
                out.append(bool(o.execute(m).get_result()))
            return out

    @op('Sampling')
    def _(m: Any, d: Any) -> Any:
        o = po.PySATSampling()
        o.set_sample_size(10)
        sample = names_of(o.execute(m).get_result())
        return {'n': len(sample), 'invalid': sum(1 for c in sample if not checker.valid(c)),
                'leaks': leaks(sample, names)}

    @op('MinimumConfiguration')
    def _(m: Any, d: Any) -> Any:
        r = po.PySATMinimumConfiguration().execute(m).get_result()
        if r is None:
            return None
        c = names_of(r)
        return {'size': len(c), 'valid': checker.valid(c), 'leaks': leaks(c, names)}

    @op('ConfigurationConflict')
    def _(m: Any, d: Any) -> Any:
        o = do.PySATConfigurationConflict()
        o.set_configuration(partial(full_true))
        return sorted(map(list, o.execute(m).get_result()))

    @op('ConfigurationRepair')
    def _(m: Any, d: Any) -> Any:
        o = do.PySATConfigurationRepair()
        o.set_configuration(partial(full_true))
        return sorted(map(list, o.execute(m).get_result()))

    # diagnosis operations take a DiagnosisModel built with the same method
    @op('Diagnosis.model')
    def _(m: Any, d: Any) -> Any:
        return hsdag(do.PySATDiagnosis(), d(), None, cap=20)

    @op('Conflict.model')
    def _(m: Any, d: Any) -> Any:
        return hsdag(do.PySATConflict(), d(), None, cap=20)

    @op('Diagnosis.configuration')
    def _(m: Any, d: Any) -> Any:
        return hsdag(do.PySATDiagnosis(), d(), partial(full_true), cap=20)

    if not satisfiable:
        @op('ExplainVoidModel')
        def _(m: Any, d: Any) -> Any:
            return do.PySATExplainVoidModel().execute(d()).get_result().descriptions()

    for name in sorted(dead)[:3] if satisfiable else []:
        @op(f'ExplainDeadFeature[{name}]')
        def _(m: Any, d: Any, name: str = name) -> Any:
            o = do.PySATExplainDeadFeature()
            o.set_feature(name)
            return o.execute(d()).get_result().descriptions()

    for name in list(false_optional)[:3]:
        @op(f'ExplainFalseOptional[{name}]')
        def _(m: Any, d: Any, name: str = name) -> Any:
            o = do.PySATExplainFalseOptional()
            o.set_feature(name)
            return o.execute(d()).get_result().descriptions()

    # ---- other ClauseSet consumers ---------------------------------------------------------
    extra: dict[str, Callable[[str], Any]] = {}
    try:
        from flamapy.metamodels.sdd_metamodel import operations as so
        from flamapy.metamodels.sdd_metamodel.transformations import FmToSDD

        def sdd(method: str) -> Any:
            m = FmToSDD(fm, cnf_method=method).transform()
            return {'Satisfiable': bool(so.SDDSatisfiable().execute(m).get_result()),
                    'ConfigurationsNumber': str(so.SDDConfigurationsNumber().execute(m).get_result()),
                    'CoreFeatures': sorted(names_of(so.SDDCoreFeatures().execute(m).get_result())),
                    'DeadFeatures': sorted(names_of(so.SDDDeadFeatures().execute(m).get_result()))}
        extra['sdd'] = sdd
    except ImportError:
        pass
    try:
        from flamapy.metamodels.sharpsat_metamodel import operations as sho
        from flamapy.metamodels.sharpsat_metamodel.transformations import FmToSharpSAT

        def sharpsat(method: str) -> Any:
            m = FmToSharpSAT(fm, cnf_method=method).transform()
            out: dict[str, Any] = {
                'ConfigurationsNumber': str(sho.SharpSATConfigurationsNumber(seed=1).execute(m).get_result())}
            if satisfiable:
                o = sho.SharpSATSampling(seed=1)
                o.set_sample_size(10)
                sample = names_of(o.execute(m).get_result())
                out['Sampling'] = {'n': len(sample), 'invalid': sum(1 for c in sample if not checker.valid(c)),
                                   'leaks': leaks(sample, names)}
            return out
        extra['sharpsat'] = sharpsat
    except ImportError:
        pass
    if shutil.which('d4'):
        try:
            from flamapy.metamodels.dnnf_metamodel import operations as dno
            from flamapy.metamodels.dnnf_metamodel.transformations import FmToDDNNF as FmToDNNF

            def dnnf(method: str) -> Any:
                m = FmToDNNF(fm, cnf_method=method).transform()
                return {'ConfigurationsNumber': str(dno.DDNNFConfigurationsNumber().execute(m).get_result())}
            extra['dnnf'] = dnnf
        except ImportError:
            pass

    for method in methods if phase == 'sat' else ():
        t0 = time.perf_counter()
        try:
            sat_model = FmToPysat(fm, cnf_method=method).transform()
        except Exception as exc:  # noqa: BLE001
            emit({'ev': 'method', 'method': method, 'error': f'{type(exc).__name__}: {exc}'[:300]})
            continue
        info = {'clauses': len(sat_model.get_all_clauses().clauses),
                'aux': len(getattr(sat_model, 'auxiliary_variables', ()))}
        emit({'ev': 'method', 'method': method, **info, 't': time.perf_counter() - t0})
        diag = (lambda method=method: FmToDiagPysat(fm, cnf_method=method).transform())
        for op_name, fn in ops.items():
            t0 = time.perf_counter()
            try:
                value = fn(sat_model, diag)
                emit({'ev': 'op', 'method': method, 'op': op_name, 'value': value, 't': time.perf_counter() - t0})
            except Exception as exc:  # noqa: BLE001
                emit({'ev': 'op', 'method': method, 'op': op_name, 't': time.perf_counter() - t0,
                      'error': f'{type(exc).__name__}: {exc}'[:300],
                      'trace': traceback.format_exc().splitlines()[-3:]})

    # knowledge compilers after every PySAT result, so a slow compilation loses nothing else
    # phase 'kc' runs every compiler; phases 'sdd' / 'sharpsat' / 'dnnf' run just that one
    for tool, fn in extra.items() if phase in ('kc', 'sdd', 'sharpsat', 'dnnf') else ():
        if phase != 'kc' and tool != phase:
            continue
        for method in methods:
            t0 = time.perf_counter()
            try:
                for op_name, value in fn(method).items():
                    emit({'ev': 'op', 'method': method, 'op': f'{tool}:{op_name}', 'value': value,
                          't': time.perf_counter() - t0})
            except Exception as exc:  # noqa: BLE001
                emit({'ev': 'op', 'method': method, 'op': f'{tool}:*', 'error': f'{type(exc).__name__}: {exc}'[:300]})

    emit({'ev': 'done'})


class _Skip(Exception):
    pass


def bdd_reference(fm: Any) -> None:
    t0 = time.perf_counter()
    try:
        from flamapy.metamodels.bdd_metamodel import operations as bo
        from flamapy.metamodels.bdd_metamodel.transformations import FmToBDD
        b = FmToBDD(fm).transform()
        emit({'ev': 'bdd', 't': time.perf_counter() - t0,
              'Satisfiable': bool(bo.BDDSatisfiable().execute(b).get_result()),
              'ConfigurationsNumber': str(bo.BDDConfigurationsNumber().execute(b).get_result()),
              'CoreFeatures': sorted(names_of(bo.BDDCoreFeatures().execute(b).get_result())),
              'DeadFeatures': sorted(names_of(bo.BDDDeadFeatures().execute(b).get_result()))})
    except Exception as exc:  # noqa: BLE001
        emit({'ev': 'bdd', 'error': f'{type(exc).__name__}: {exc}'[:300]})
    emit({'ev': 'done'})


def coverage(m: Any, sample: list[list[str]]) -> float:
    """Pairwise coverage of a sample (feature names) over the satisfiable pairs of `m`."""
    from flamapy.metamodels.configuration_metamodel.models import Configuration
    from flamapy.metamodels.pysat_metamodel import operations as po
    o = po.PySATCoverage(2)
    o.set_configurations([Configuration({n: True for n in c}) for c in sample])
    return o.execute(m).get_result()


def hsdag(operation: Any, model: Any, configuration: Any, cap: int) -> Any:
    """Diagnoses and conflicts as constraint descriptions (HSDAG, at most `cap` of each)."""
    if configuration is not None:
        operation.set_configuration(configuration)
    if hasattr(operation, 'set_max_diagnoses'):
        operation.set_max_diagnoses(cap)
    if hasattr(operation, 'set_max_conflicts'):
        operation.set_max_conflicts(cap)
    checker, tree = operation.prepare_hsdag(model)
    try:
        tree.construct()
        cmap = model.constraint_assumption_map

        def desc(sets: list[list[int]]) -> list[list[str]]:
            return sorted(sorted(cmap[i] for i in s) for s in sets)
        return {'diagnoses': desc(tree.get_diagnoses()), 'conflicts': desc(tree.get_conflicts())}
    finally:
        checker.delete()


def constraint_equivalence(fm: Any) -> dict[str, Any]:
    """Per constraint: Tseytin clauses projected on the features == distributive clauses.

    Exhaustive over the constraint's own features (skipped above 16 of them); the Tseytin
    side is decided with a SAT call per assignment, so the auxiliary variables are projected out.
    """
    from pysat.solvers import Solver
    checked = skipped = failed = 0
    failures: list[str] = []
    for ctc in fm.get_constraints():
        dist = ctc.ast.get_clauses()
        tsey, aux = ctc.ast.get_clauses_with_aux(method='tseytin')
        if not aux:
            continue
        feats = sorted({t.lstrip('-') for c in dist for t in c} | {t.lstrip('-') for c in tsey for t in c} - set(aux))
        if len(feats) > 16:
            skipped += 1
            continue
        ids = {n: i + 1 for i, n in enumerate(feats + list(aux))}

        def lit(t: str) -> int:
            return -ids[t[1:]] if t.startswith('-') else ids[t]
        with Solver(name='glucose3', bootstrap_with=[[lit(t) for t in c] for c in tsey]) as s:
            ok = True
            for bits in itertools.product((False, True), repeat=len(feats)):
                val = dict(zip(feats, bits))
                d_ok = all(any((not val[t[1:]]) if t.startswith('-') else val[t] for t in c) for c in dist)
                t_ok = s.solve(assumptions=[ids[f] if b else -ids[f] for f, b in val.items()])
                if d_ok != t_ok:
                    ok = False
                    break
        checked += 1
        if not ok:
            failed += 1
            failures.append(str(ctc)[:200])
    return {'checked': checked, 'skipped_wide': skipped, 'failed': failed, 'failures': failures[:5]}


# --------------------------------------------------------------------------- driver

def model_sets(which: list[str]) -> list[tuple[str, str, str, bool]]:
    """(set, model_id, path, enumerate_configurations)."""
    out: list[tuple[str, str, str, bool]] = []
    with open(ROOT / 'prepared/manifest.csv', newline='') as fh:
        manifest = {r['model_id']: r for r in csv.DictReader(fh)}
    if 'oracle' in which:
        for mid in json.loads((ROOT / 'prepared/oracle_ids.json').read_text()):
            out.append(('oracle', mid, str(ROOT / manifest[mid]['path']), True))
    if 'auto' in which:
        from flamapy.metamodels.fm_metamodel.models.clause_set import use_tseytin
        from flamapy.metamodels.fm_metamodel.transformations import UVLReader
        cache = ROOT / 'prepared/auto_tseytin_ids.json'
        if cache.exists():
            ids = json.loads(cache.read_text())
        else:
            ids = []
            for mid, r in manifest.items():
                if r['excluded'] or r['duplicate_of'] or r['parse_status'] != 'ok':
                    continue
                try:
                    fm = UVLReader(str(ROOT / r['path'])).transform()
                except Exception:  # noqa: BLE001
                    continue
                if any(use_tseytin(c.ast) for c in fm.get_constraints()):
                    ids.append(mid)
            cache.write_text(json.dumps(sorted(ids), indent=0))
        for mid in ids:
            out.append(('auto', mid, str(ROOT / manifest[mid]['path']), False))
    if 'resources' in which:
        paths = sorted(set(FLAMAPY.glob('*/resources/**/*.uvl')) | set(FLAMAPY.glob('*/tests/resources/**/*.uvl')))
        from flamapy.metamodels.fm_metamodel.transformations import UVLReader
        for p in paths:
            try:   # enumerate configurations only on models as small as the oracle ones
                small = len(UVLReader(str(p)).transform().get_features()) <= 60
            except Exception:  # noqa: BLE001 - the worker reports the parse error
                small = True
            out.append(('resources', str(p.relative_to(FLAMAPY)), str(p), small))
    return out


def run_phase(path: str, set_name: str, configs: bool, phase: str, timeout: int) -> tuple[str, list[dict[str, Any]], float]:
    events: list[dict[str, Any]] = []
    status = 'ok'
    t0 = time.perf_counter()
    try:
        p = subprocess.run([sys.executable, str(Path(__file__)), '--one', path, set_name, str(int(configs)), phase],
                           capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))
        out = p.stdout
        if p.returncode != 0:
            status = f'crash rc={p.returncode}: {p.stderr.strip().splitlines()[-1:]}'
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or '')
        status = 'timeout'
    for line in out.splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    if status == 'ok' and not any(e.get('ev') == 'done' for e in events):
        status = 'incomplete'
    return status, events, time.perf_counter() - t0


def run_one(item: tuple[str, str, str, bool], timeout: int, aux_timeout: int,
            only_phases: frozenset[str] = frozenset()) -> dict[str, Any]:
    """All phases of one model, each in its own process with its own timeout.

    On the small models one 'kc' phase runs SDD and sharpSAT; on the large ones each
    compiler gets one phase per method ('sdd@tseytin') and the BDD reference its own, each
    with the aux budget, so a compilation that cannot finish does not cost the others.
    `only_phases` (base names) reruns just those phases; the record is then marked partial
    and check_cnf_methods.py merges it into the previous one.
    """
    set_name, mid, path, configs = item
    if set_name == 'oracle':
        phases = ['sat', 'kc']
    elif configs:
        phases = ['sat', 'kc', 'bdd']
    else:
        compilers = ['sdd', 'sharpsat', 'dnnf'] if shutil.which('d4') else ['sdd', 'sharpsat']
        phases = ['sat', *(f'{c}@{m}' for c in compilers for m in METHODS), 'bdd']
    if only_phases:
        phases = [p for p in phases if p.split('@')[0] in only_phases]
    out: dict[str, Any] = {'set': set_name, 'model_id': mid, 'status': {}, 't': {}, 'events': []}
    if only_phases:
        out['partial'] = sorted(only_phases)
    for phase in phases:
        budget = timeout if phase in ('sat', 'kc') else aux_timeout
        status, events, t = run_phase(path, set_name, configs, phase, budget)
        out['status'][phase] = status
        out['t'][phase] = round(t, 2)
        out['events'].extend({**e, 'phase': phase} for e in events)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true', help='rerun models already in the output')
    ap.add_argument('--only', default='', help='file with model ids to (re)run, one per line')
    ap.add_argument('--set', default='oracle,auto,resources')
    ap.add_argument('--jobs', type=int, default=8)
    ap.add_argument('--timeout', type=int, default=300, help='per phase (sat, kc), seconds')
    ap.add_argument('--aux-timeout', type=int, default=120,
                    help='per phase for sdd / sharpsat / dnnf / bdd on large models')
    ap.add_argument('--out', default=str(ROOT / 'results/cnf_methods'))
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--phases', default='',
                    help='rerun only these phases (e.g. sdd,sharpsat,bdd); implies --force')
    ap.add_argument('--one', nargs=4, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.one:
        run_model(args.one[0], args.one[1], args.one[2] == '1', args.one[3])
        return
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = model_sets(args.set.split(','))
    if args.only:
        wanted = set(Path(args.only).read_text().split('\n')) - {''}
        items = [i for i in items if i[1] in wanted]
    if args.limit:
        items = items[:args.limit]
    done: set[tuple[str, str]] = set()
    for s in {i[0] for i in items}:
        f = out_dir / f'{s}.jsonl'
        if f.exists():
            for line in f.read_text().splitlines():
                d = json.loads(line)
                done.add((d['set'], d['model_id']))
    only_phases = frozenset(args.phases.split(',')) - {''}
    if only_phases:
        # skip models already rerun with these phases (resumable), not the first full record
        done = set()
        for s in {i[0] for i in items}:
            f = out_dir / f'{s}.jsonl'
            for line in (f.read_text().splitlines() if f.exists() else ()):
                d = json.loads(line)
                if set(d.get('partial', ())) == only_phases:
                    done.add((d['set'], d['model_id']))
    todo = [i for i in items if (args.force and not only_phases) or (i[0], i[1]) not in done]
    print(f'{len(items)} models, {len(todo)} to run', flush=True)
    handles = {s: open(out_dir / f'{s}.jsonl', 'a') for s in {i[0] for i in items}}
    n = 0

    def work(item: tuple[str, str, str, bool]) -> None:
        nonlocal n
        r = run_one(item, args.timeout, args.aux_timeout, only_phases)
        handles[item[0]].write(json.dumps(r) + '\n')
        handles[item[0]].flush()
        n += 1
        bad = {k: v for k, v in r['status'].items() if v != 'ok'}
        if n % 25 == 0 or bad:
            print(f'[{n}/{len(todo)}] {item[0]} {item[1]} {bad or "ok"} {r["t"]}', flush=True)

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(work, todo))
    for h in handles.values():
        h.close()
    print('done', flush=True)


if __name__ == '__main__':
    main()

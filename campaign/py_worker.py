"""Worker process for flamapy backends (PySAT, BDD, Z3).

Speaks the campaign JSON-lines protocol on stdout (see campaign/PROTOCOL.md), exactly like the
Java harnesses for FaMa and FeatureIDE. The parent (campaign.runner) owns the timeout: it kills
this process when a step does not end in time, so nothing in here needs to be interruptible.

Usage:
    python -m campaign.py_worker MODEL.uvl {pysat|bdd|z3} VARIANT REPS OP1,OP2,...

VARIANT is the PySAT solver name (glucose3, cadical153, ...) or 'n/a'.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import warnings
from typing import Any, Callable

warnings.filterwarnings('ignore')
logging.disable(logging.WARNING)


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, default=str) + '\n')
    sys.stdout.flush()


def step(name: str, body: Callable[[], Any]) -> tuple[bool, Any]:
    """Run one timed step and report it. Returns (ok, raw_result)."""
    emit({'ev': 'begin', 'step': name})
    c0 = time.process_time()
    t0 = time.perf_counter()
    try:
        raw = body()
        t1 = time.perf_counter()
        c1 = time.process_time()
        value = canonical(raw) if name.startswith('op:') else raw
        emit({'ev': 'end', 'step': name, 'status': 'success', 't': t1 - t0, 'cpu': c1 - c0, 'value': value})
        return True, raw
    except Exception as exc:  # noqa: BLE001 - every failure is data here
        t1 = time.perf_counter()
        emit({'ev': 'end', 'step': name, 'status': 'error', 't': t1 - t0,
              'error': f'{type(exc).__name__}: {exc}'[:300]})
        return False, None


def canonical(value: Any) -> Any:
    """Normalise an operation result so results from different tools compare as JSON.

    Booleans stay booleans, integers become strings (counts exceed the JSON double range),
    floats are kept as floats, and collections of features become sorted lists of names.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return value
    elements = getattr(value, 'elements', None)
    if isinstance(elements, dict):  # a flamapy Configuration: the sorted selected feature names
        return sorted(str(_name(k)) for k, selected in elements.items() if selected)
    if isinstance(value, dict):
        return {str(_name(k)): canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [canonical(_name(v)) for v in value]
        try:
            return sorted(items, key=lambda x: json.dumps(x, sort_keys=True))
        except TypeError:
            return items
    return str(_name(value))


def _name(x: Any) -> Any:
    """Feature objects -> their name; anything else unchanged."""
    name = getattr(x, 'name', None)
    return name if isinstance(name, str) else x


# --------------------------------------------------------------------------- backends

def _use_pysat_solver(variant: str) -> None:
    """Make every PySAT operation use `variant`.

    In flamapy-sat (develop, 2.9.0.dev0) most operations create `Solver(name='glucose3')`
    themselves, and CoreFeatures/DeadFeatures go through pysat_backbone, which also hard-codes
    glucose3. We rebind the `Solver` symbol in each operations module so that the requested
    solver is really used everywhere. This only changes which SAT solver is instantiated.
    """
    import importlib
    import pkgutil

    from pysat.solvers import Solver as _Solver

    import flamapy.metamodels.pysat_metamodel.operations as ops_pkg

    def factory(*args: Any, **kwargs: Any) -> Any:
        kwargs['name'] = variant
        if args:
            args = args[1:]  # positional `name`
        return _Solver(*args, **kwargs)

    for info in pkgutil.iter_modules(ops_pkg.__path__):
        mod = importlib.import_module(f'{ops_pkg.__name__}.{info.name}')
        if getattr(mod, 'Solver', None) is _Solver:
            mod.Solver = factory


def load_backend(tool: str, variant: str) -> tuple[Callable[[Any], Any], dict[str, type]]:
    if tool == 'pysat':
        from flamapy.metamodels.pysat_metamodel import operations as o
        from flamapy.metamodels.pysat_metamodel.transformations import FmToPysat
        _use_pysat_solver(variant)
        ops = {
            'Satisfiable': o.PySATSatisfiable,
            'CoreFeatures': o.PySATCoreFeatures,
            'DeadFeatures': o.PySATDeadFeatures,
            'FalseOptionalFeatures': o.PySATFalseOptionalFeatures,
            'ConfigurationsNumber': o.PySATConfigurationsNumber,
            'Configurations': o.PySATConfigurations,
        }
        return (lambda fm: FmToPysat(fm).transform()), ops
    if tool == 'bdd':
        from flamapy.metamodels.bdd_metamodel import operations as o
        from flamapy.metamodels.bdd_metamodel.transformations import FmToBDD
        ops = {
            'Satisfiable': o.BDDSatisfiable,
            'CoreFeatures': o.BDDCoreFeatures,
            'DeadFeatures': o.BDDDeadFeatures,
            'FalseOptionalFeatures': o.BDDFalseOptionalFeatures,
            'ConfigurationsNumber': o.BDDConfigurationsNumber,
            'Configurations': o.BDDConfigurations,
            'VariantFeatures': o.BDDVariantFeatures,
            'PureOptionalFeatures': o.BDDPureOptionalFeatures,
            'UniqueFeatures': o.BDDUniqueFeatures,
            'Variability': o.BDDVariability,
            'Homogeneity': o.BDDHomogeneity,
            'ProductDistribution': o.BDDProductDistribution,
            'FeatureInclusionProbability': o.BDDFeatureInclusionProbability,
        }
        return (lambda fm: FmToBDD(fm).transform()), ops
    if tool == 'z3':
        from flamapy.metamodels.z3_metamodel import operations as o
        from flamapy.metamodels.z3_metamodel.transformations import FmToZ3
        ops = {
            'Satisfiable': o.Z3Satisfiable,
            'CoreFeatures': o.Z3CoreFeatures,
            'DeadFeatures': o.Z3DeadFeatures,
            'FalseOptionalFeatures': o.Z3FalseOptionalFeatures,
            'ConfigurationsNumber': o.Z3ConfigurationsNumber,
            'Configurations': o.Z3Configurations,
        }
        return (lambda fm: FmToZ3(fm).transform()), ops
    raise SystemExit(f'unknown tool {tool}')


def _watch_parent() -> None:
    """Exit if the runner dies: an orphaned worker would otherwise run with no timeout."""
    import os
    import threading

    parent = os.getppid()

    def loop() -> None:
        while True:
            time.sleep(1.0)
            if os.getppid() != parent:
                os._exit(3)

    threading.Thread(target=loop, daemon=True).start()


def main(argv: list[str]) -> None:
    _watch_parent()
    emit({'ev': 'ready', 'python': sys.version.split()[0]})
    if len(argv) != 5:
        emit({'ev': 'fatal', 'msg': 'usage: py_worker MODEL TOOL VARIANT REPS OPS'})
        raise SystemExit(1)
    model_path, tool, variant, reps_s, ops_s = argv
    reps = int(reps_s)
    wanted = [o for o in ops_s.split(',') if o]

    holder: dict[str, Any] = {}

    def _setup() -> dict[str, Any]:
        from flamapy.metamodels.fm_metamodel.transformations import UVLReader
        holder['reader'] = UVLReader
        holder['transform'], holder['ops'] = load_backend(tool, variant)
        return {'variant': variant}

    ok, _ = step('setup', _setup)
    if not ok:
        emit({'ev': 'done'})
        return

    def _parse() -> dict[str, Any]:
        fm = holder['reader'](model_path).transform()
        holder['fm'] = fm
        return {'features': len(fm.get_features()), 'constraints': len(fm.get_constraints())}

    ok, _ = step('parse', _parse)
    if not ok:
        emit({'ev': 'done'})
        return

    def _transform() -> dict[str, Any]:
        holder['model'] = holder['transform'](holder['fm'])
        info: dict[str, Any] = {}
        model = holder['model']
        if hasattr(model, 'get_all_clauses'):
            info['clauses'] = len(list(model.get_all_clauses()))
        if hasattr(model, 'variables'):
            info['variables'] = len(model.variables)
        return info

    ok, _ = step('transform', _transform)
    if not ok:
        emit({'ev': 'done'})
        return

    for op_name in wanted:
        op_cls = holder['ops'].get(op_name)
        if op_cls is None:
            emit({'ev': 'begin', 'step': f'op:{op_name}:0'})
            emit({'ev': 'end', 'step': f'op:{op_name}:0', 'status': 'error', 't': 0.0,
                  'error': f'operation not implemented by {tool}'})
            continue
        for rep in range(reps):
            # The operation object is created inside the timed region: several flamapy
            # operations build their solver in __init__.
            ok, _ = step(f'op:{op_name}:{rep}',
                         lambda c=op_cls: c().execute(holder['model']).get_result())
            if not ok:
                break
    emit({'ev': 'done'})


if __name__ == '__main__':
    main(sys.argv[1:])

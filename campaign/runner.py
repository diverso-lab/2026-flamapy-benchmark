"""Campaign runner: resumable, kill-on-timeout benchmark orchestration.

Every (model, tool) pair is a *task*. A task runs one worker process (Python worker for the
flamapy backends, a JVM for FaMa and FeatureIDE) that executes setup -> parse -> transform ->
each operation x repetitions, reporting every step on stdout (campaign/PROTOCOL.md).

The runner owns the timeout. When a step runs longer than `timeout_s`, the whole process group
is killed (this also stops native solver code, which SIGALRM in the old runner could not), the
step is recorded as `timeout`, and the task continues in a fresh process with the operations
that had not started yet.

Results are committed per task: rows and values are appended to the tool's files and only then
is the task marked done. A crash, reboot or Ctrl-C loses at most the tasks that were running;
`run` again with the same output directory continues where it stopped.

    python -m campaign.runner run    --config campaign/configs/rq2_main.json --out results/rq2_main
    python -m campaign.runner status --out results/rq2_main
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import platform
import queue
import random
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent

ROW_FIELDS = [
    'campaign', 'tool', 'model_id', 'model_sha256', 'n_features', 'n_constraints',
    'step', 'operation', 'rep', 'restart', 'status', 'time_s', 'cpu_s',
    'value_hash', 'value_short', 'error', 'attempt', 'host', 'finished_at',
]

PHASES = ('startup', 'setup', 'parse', 'transform')


# ============================================================================ tools

@dataclass
class Tool:
    name: str
    kind: str                      # 'python' | 'java'
    backend: str = ''              # python: pysat|bdd|z3
    variant: str = 'n/a'
    java_main: str = ''
    java_cp: str = ''
    java_args: list[str] = field(default_factory=list)   # extra args between model and reps
    input: str = 'uvl'             # 'uvl' | 'fama'

    def command(self, model: dict[str, str], ops: list[str], reps: int, cfg: dict[str, Any]) -> list[str]:
        if self.kind == 'python':
            py = cfg.get('python') or sys.executable
            return [py, '-m', 'campaign.py_worker', model['abs_path'], self.backend, self.variant,
                    str(reps), ','.join(ops)]
        java = cfg.get('java') or os.environ.get('CAMPAIGN_JAVA') or shutil.which('java') or 'java'
        jvm = [f"-Xmx{cfg.get('java_xmx', '8g')}", '-Djava.awt.headless=true']
        model_path = model['fama_abs_path'] if self.input == 'fama' else model['abs_path']
        args = [a.replace('{timeout_ms}', str(int(cfg['timeout_s']) * 1000)) for a in self.java_args]
        return [java, *jvm, '-cp', self.java_cp, self.java_main, model_path, *args, str(reps), ','.join(ops)]


def tool_registry() -> dict[str, Tool]:
    fama_jar = str(ROOT / 'fama_cli/target/fama-cli-1.0.0-jar-with-dependencies.jar')
    fide_cp = f"{ROOT / 'featureide_cli/build'}{os.pathsep}{ROOT / 'featureide_cli/lib'}/*"
    tools = {
        'flamapy-bdd': Tool('flamapy-bdd', 'python', backend='bdd'),
        'flamapy-z3': Tool('flamapy-z3', 'python', backend='z3'),
        'fama-sat4j': Tool('fama-sat4j', 'java', java_main='es.us.isa.fama.cli.FamaHarness', java_cp=fama_jar,
                           java_args=['Sat4j'], input='fama'),
        'fama-choco': Tool('fama-choco', 'java', java_main='es.us.isa.fama.cli.FamaHarness', java_cp=fama_jar,
                           java_args=['Choco'], input='fama'),
        'featureide': Tool('featureide', 'java', java_main='FideHarness', java_cp=fide_cp,
                           java_args=['{timeout_ms}']),
    }
    for solver in ('glucose3', 'glucose4', 'minisat22', 'lingeling', 'maplesat', 'cadical153'):
        tools[f'flamapy-sat:{solver}'] = Tool(f'flamapy-sat:{solver}', 'python', backend='pysat', variant=solver)
    return tools


# ============================================================================ process supervision

@dataclass
class StepRecord:
    step: str
    status: str
    time_s: Optional[float]
    cpu_s: Optional[float] = None
    value: Any = None
    error: str = ''
    restart: int = 0


def _preexec(cpu: Optional[int], mem_limit_gb: Optional[float]):
    def fn() -> None:
        if sys.platform.startswith('linux'):
            try:  # SIGKILL the worker if the runner thread that started it dies
                import ctypes
                ctypes.CDLL('libc.so.6', use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
            except Exception:  # noqa: BLE001
                pass
        if cpu is not None and hasattr(os, 'sched_setaffinity'):
            os.sched_setaffinity(0, {cpu})
        if mem_limit_gb and sys.platform.startswith('linux'):
            import resource
            limit = int(mem_limit_gb * 1024 ** 3)
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    return fn


def supervise(cmd: list[str], timeout_s: float, cwd: Path, cpu: Optional[int],
              mem_limit_gb: Optional[float], restart: int, stop: threading.Event) -> tuple[list[StepRecord], str, str]:
    """Run one worker process. Returns (records, outcome, failing_step).

    outcome: 'done' | 'timeout' | 'crash' | 'stopped'
    """
    env = dict(os.environ, PYTHONHASHSEED='0', PYTHONDONTWRITEBYTECODE='1')
    t_spawn = time.perf_counter()
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        start_new_session=True, env=env, preexec_fn=_preexec(cpu, mem_limit_gb),
    )
    lines: 'queue.Queue[Optional[str]]' = queue.Queue()
    err_tail: deque[str] = deque(maxlen=20)

    def _out() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)
        lines.put(None)

    def _err() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            err_tail.append(line.rstrip())

    threading.Thread(target=_out, daemon=True).start()
    threading.Thread(target=_err, daemon=True).start()

    records: list[StepRecord] = []
    current = 'startup'
    step_start = t_spawn
    outcome, failing = 'done', ''

    def kill() -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    while True:
        if stop.is_set():
            kill()
            outcome, failing = 'stopped', current or ''
            break
        now = time.perf_counter()
        remaining = step_start + timeout_s - now
        if remaining <= 0:
            kill()
            records.append(StepRecord(current or 'between-steps', 'timeout', now - step_start, restart=restart))
            outcome, failing = 'timeout', current or 'between-steps'
            break
        try:
            line = lines.get(timeout=min(remaining, 1.0))
        except queue.Empty:
            continue
        if line is None:  # stdout closed: process ended
            proc.wait()
            if outcome == 'done' and current is not None:
                code = proc.returncode
                reason = 'oom' if code in (-9, 137) and mem_limit_gb else 'crash'
                records.append(StepRecord(current, reason, time.perf_counter() - step_start,
                                          error=f'exit {code}: ' + ' | '.join(list(err_tail)[-3:])[:300],
                                          restart=restart))
                outcome, failing = 'crash', current
            break
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = ev.get('ev')
        now = time.perf_counter()
        if kind == 'ready':
            records.append(StepRecord('startup', 'success', now - t_spawn, restart=restart))
            current, step_start = None, now
        elif kind == 'begin':
            current, step_start = ev['step'], now
        elif kind == 'end':
            records.append(StepRecord(ev['step'], ev.get('status', 'error'), ev.get('t'), ev.get('cpu'),
                                      ev.get('value'), ev.get('error', ''), restart=restart))
            current, step_start = None, now
        elif kind == 'done':
            current = None
        elif kind == 'fatal':
            records.append(StepRecord('setup', 'error', 0.0, error=str(ev.get('msg'))[:300], restart=restart))
            current = None
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kill()
    return records, outcome, failing


# ============================================================================ task execution

def _hash(value: Any) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _short(value: Any) -> str:
    if isinstance(value, list):
        return f'[{len(value)} items]'
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)[:80]
    return str(value)[:80]


def run_task(model: dict[str, str], tool: Tool, cfg: dict[str, Any], cpu: Optional[int],
             stop: threading.Event) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Run every operation of one (model, tool) task. Returns (rows, values, complete)."""
    ops: list[str] = list(cfg['ops'])
    reps = int(cfg['reps'])
    attempt = uuid.uuid4().hex[:12]
    records: list[StepRecord] = []
    restart = 0
    pending = list(ops)
    blocked_reason = ''
    while pending:
        cmd = tool.command(model, pending, reps, cfg)
        recs, outcome, failing = supervise(cmd, float(cfg['timeout_s']), ROOT, cpu,
                                           cfg.get('py_mem_limit_gb') if tool.kind == 'python' else None,
                                           restart, stop)
        records.extend(recs)
        if outcome == 'stopped':
            return [], [], False
        started = {r.step.split(':')[1] for r in recs if r.step.startswith('op:')}
        if outcome == 'done':
            break
        if not failing.startswith('op:'):
            blocked_reason = f'{failing} {outcome}'
            break
        # An operation timed out or crashed: continue with the operations that never started.
        pending = [o for o in pending if o not in started]
        restart += 1

    if not blocked_reason:
        failed_phase = next((r for r in records if not r.step.startswith('op:') and r.status != 'success'), None)
        if failed_phase is not None:
            blocked_reason = f'{failed_phase.step} {failed_phase.status}: {failed_phase.error}'[:300]

    rows: list[dict[str, Any]] = []
    values: list[dict[str, Any]] = []
    host = socket.gethostname()
    finished = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    seen_ops: set[str] = set()
    for r in records:
        if r.step.startswith('op:'):
            _, op, rep = r.step.split(':')
            operation, rep_i = op, int(rep)
            seen_ops.add(op)
            step = 'op'
        else:
            operation, rep_i, step = '', 0, r.step
        vh = _hash(r.value) if (r.status == 'success' and step == 'op') else ''
        rows.append({
            'campaign': cfg['name'], 'tool': tool.name, 'model_id': model['model_id'],
            'model_sha256': model['sha256'], 'n_features': model.get('n_features', ''),
            'n_constraints': model.get('n_constraints', ''), 'step': step, 'operation': operation,
            'rep': rep_i, 'restart': r.restart, 'status': r.status,
            'time_s': '' if r.time_s is None else f'{r.time_s:.9f}',
            'cpu_s': '' if r.cpu_s is None else f'{r.cpu_s:.9f}',
            'value_hash': vh, 'value_short': _short(r.value) if step == 'op' and r.status == 'success' else '',
            'error': r.error, 'attempt': attempt, 'host': host, 'finished_at': finished,
        })
        if step == 'op' and rep_i == 0 and r.status == 'success':
            values.append({'model_id': model['model_id'], 'tool': tool.name, 'operation': operation,
                           'value_hash': vh, 'value': r.value, 'attempt': attempt})
    for op in ops:
        if op not in seen_ops:
            rows.append({
                'campaign': cfg['name'], 'tool': tool.name, 'model_id': model['model_id'],
                'model_sha256': model['sha256'], 'n_features': model.get('n_features', ''),
                'n_constraints': model.get('n_constraints', ''), 'step': 'op', 'operation': op, 'rep': 0,
                'restart': restart, 'status': 'not_run', 'time_s': '', 'cpu_s': '', 'value_hash': '',
                'value_short': '', 'error': blocked_reason or 'not started', 'attempt': attempt, 'host': host,
                'finished_at': finished,
            })
    return rows, values, True


# ============================================================================ campaign bookkeeping

class Store:
    """Per-tool append-only files. Writes are serialised by one lock."""

    def __init__(self, out: Path) -> None:
        self.out = out
        self.lock = threading.Lock()
        out.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def safe(tool: str) -> str:
        return tool.replace(':', '_')

    def done(self, tool: str) -> set[str]:
        path = self.out / f'{self.safe(tool)}.done'
        if not path.exists():
            return set()
        return {line.split('\t')[0] for line in path.read_text().splitlines() if line.strip()}

    def commit(self, tool: str, model_id: str, rows: list[dict[str, Any]], values: list[dict[str, Any]]) -> None:
        with self.lock:
            csv_path = self.out / f'{self.safe(tool)}.csv'
            new = not csv_path.exists()
            with open(csv_path, 'a', newline='') as fh:
                w = csv.DictWriter(fh, fieldnames=ROW_FIELDS)
                if new:
                    w.writeheader()
                w.writerows(rows)
                fh.flush()
                os.fsync(fh.fileno())
            with open(self.out / f'{self.safe(tool)}.values.jsonl', 'a') as fh:
                for v in values:
                    fh.write(json.dumps(v) + '\n')
                fh.flush()
                os.fsync(fh.fileno())
            attempt = rows[0]['attempt'] if rows else ''
            with open(self.out / f'{self.safe(tool)}.done', 'a') as fh:
                fh.write(f'{model_id}\t{attempt}\n')
                fh.flush()
                os.fsync(fh.fileno())


def load_models(cfg: dict[str, Any]) -> list[dict[str, str]]:
    spec = cfg['models']
    manifest = ROOT / spec.get('manifest', 'prepared/manifest.csv')
    with open(manifest, newline='') as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        if r['excluded']:
            continue
        if not spec.get('include_duplicates', False) and r['duplicate_of']:
            continue
        nf = int(r['n_features'] or 0)
        if spec.get('max_features') and nf > int(spec['max_features']):
            continue
        if spec.get('min_features') and nf < int(spec['min_features']):
            continue
        r['abs_path'] = str(ROOT / r['path'])
        r['fama_abs_path'] = str(ROOT / r['fama_path']) if r['fama_path'] else ''
        out.append(r)
    if spec.get('sample'):
        # Stratified by size: sort by features and take evenly spaced models.
        out.sort(key=lambda r: (int(r['n_features'] or 0), r['model_id']))
        k = int(spec['sample'])
        if k < len(out):
            step = len(out) / k
            out = [out[int(i * step)] for i in range(k)]
    if spec.get('ids_file'):
        spec = dict(spec, ids=json.loads((ROOT / spec['ids_file']).read_text()))
    if spec.get('ids'):
        wanted = set(spec['ids'])
        out = [r for r in out if r['model_id'] in wanted]
    return out


def host_fingerprint() -> dict[str, Any]:
    info: dict[str, Any] = {'hostname': socket.gethostname(), 'platform': platform.platform(),
                            'machine': platform.machine(), 'python': sys.version.split()[0],
                            'cpu_count': os.cpu_count()}
    try:
        if sys.platform == 'darwin':
            info['cpu'] = subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True,
                                         text=True).stdout.strip()
            info['mem_bytes'] = int(subprocess.run(['sysctl', '-n', 'hw.memsize'], capture_output=True,
                                                   text=True).stdout.strip())
        else:
            cpuinfo = Path('/proc/cpuinfo').read_text()
            info['cpu'] = next((l.split(':', 1)[1].strip() for l in cpuinfo.splitlines()
                                if l.startswith('model name')), '')
            mem = Path('/proc/meminfo').read_text().splitlines()[0]
            info['mem_bytes'] = int(mem.split()[1]) * 1024
    except Exception:  # noqa: BLE001
        pass
    return info


def environment_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    snap: dict[str, Any] = {}
    py = cfg.get('python') or sys.executable
    probe = (
        'import json, importlib.metadata as m\n'
        'pk = {d.metadata["Name"]: d.version for d in m.distributions()}\n'
        'try:\n import dd.cudd; cudd = True\nexcept Exception:\n cudd = False\n'
        'print(json.dumps({"packages": pk, "dd_cudd": cudd}))\n'
    )
    try:
        snap.update(json.loads(subprocess.run([py, '-c', probe], capture_output=True, text=True,
                                              timeout=120).stdout))
    except Exception as exc:  # noqa: BLE001
        snap['packages_error'] = str(exc)
    repos = {}
    for repo in cfg.get('flamapy_repos', []):
        p = Path(repo).expanduser()
        try:
            head = subprocess.run(['git', '-C', str(p), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            branch = subprocess.run(['git', '-C', str(p), 'branch', '--show-current'], capture_output=True, text=True).stdout.strip()
            dirty = bool(subprocess.run(['git', '-C', str(p), 'status', '--porcelain', '--untracked-files=no'],
                                        capture_output=True, text=True).stdout.strip())
            repos[p.name] = {'commit': head, 'branch': branch, 'dirty': dirty}
        except Exception as exc:  # noqa: BLE001
            repos[p.name] = {'error': str(exc)}
    snap['flamapy_repos'] = repos
    jv = ROOT / '.build/java_versions.txt'
    if jv.exists():
        snap['java_build'] = dict(l.split('=', 1) for l in jv.read_text().splitlines() if '=' in l)
    java = cfg.get('java') or os.environ.get('CAMPAIGN_JAVA') or shutil.which('java')
    if java:
        snap['java_runtime'] = subprocess.run([java, '-version'], capture_output=True, text=True).stderr.strip()
    snap['benchmark_commit'] = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                              capture_output=True, text=True).stdout.strip()
    return snap


# ============================================================================ commands

def cmd_run(args: argparse.Namespace) -> None:
    cfg = json.loads(Path(args.config).read_text())
    if args.workers:
        cfg['workers'] = args.workers
    if cfg.get('python') and not Path(cfg['python']).is_absolute():
        cfg['python'] = str(ROOT / cfg['python'])
    out = Path(args.out)
    store = Store(out)

    # Never mix timings from different machines in one result directory.
    host = host_fingerprint()
    runs_file = out / 'runs.jsonl'
    if runs_file.exists() and not args.allow_other_host:
        first = json.loads(runs_file.read_text().splitlines()[0])
        if first['host'].get('hostname') != host['hostname'] or first['host'].get('cpu') != host.get('cpu'):
            sys.exit(f"refusing to resume: {out} was started on {first['host'].get('hostname')} "
                     f"({first['host'].get('cpu')}); use another --out or --allow-other-host")
    (out / 'config.json').write_text(json.dumps(cfg, indent=2))

    tools_all = tool_registry()
    tools = [tools_all[t] for t in cfg['tools']]
    models = load_models(cfg)
    if args.limit:
        models = models[:args.limit]

    tasks = []
    for tool in tools:
        done = store.done(tool.name)
        for m in models:
            if m['model_id'] in done:
                continue
            if tool.input == 'fama' and not m['fama_abs_path']:
                continue  # model not representable losslessly in FaMa XML
            tasks.append((m, tool))
    random.Random(cfg.get('seed', 2026)).shuffle(tasks)

    with open(runs_file, 'a') as fh:
        fh.write(json.dumps({'started_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
                             'host': host, 'pending_tasks': len(tasks), 'models': len(models),
                             'argv': sys.argv, 'environment': environment_snapshot(cfg)}) + '\n')

    log = open(out / 'runner.log', 'a', buffering=1)

    def say(msg: str) -> None:
        line = f"{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        log.write(line + '\n')
        if not args.quiet:
            print(line, flush=True)

    say(f"campaign {cfg['name']}: {len(models)} models x {len(tools)} tools, {len(tasks)} pending tasks, "
        f"{cfg['workers']} workers, timeout {cfg['timeout_s']} s, {cfg['reps']} reps")

    stop = threading.Event()

    def on_signal(signum: int, _frame: Any) -> None:
        say(f'signal {signum}: stopping (running tasks are discarded and will be rerun on resume)')
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    cpus: list[Optional[int]] = list(cfg.get('cpus') or []) if sys.platform.startswith('linux') else []
    workers = int(cfg['workers'])
    slots: 'queue.Queue[Optional[int]]' = queue.Queue()
    for i in range(workers):
        slots.put(cpus[i] if i < len(cpus) else None)

    counters: Counter[str] = Counter()
    t0 = time.time()
    total = len(tasks)
    progress_lock = threading.Lock()

    def work(item: tuple[dict[str, str], Tool]) -> None:
        if stop.is_set():
            return
        model, tool = item
        cpu = slots.get()
        try:
            rows, values, complete = run_task(model, tool, cfg, cpu, stop)
        except Exception as exc:  # noqa: BLE001 - keep the campaign alive
            say(f"runner error on {tool.name} {model['model_id']}: {type(exc).__name__}: {exc}")
            slots.put(cpu)
            return
        slots.put(cpu)
        if not complete:
            return
        store.commit(tool.name, model['model_id'], rows, values)
        statuses = Counter(r['status'] for r in rows if r['step'] == 'op')
        with progress_lock:
            counters['done'] += 1
            for k, v in statuses.items():
                counters[k] += v
            done = counters['done']
            elapsed = time.time() - t0
            eta = elapsed / done * (total - done) if done else 0
            (out / 'progress.json').write_text(json.dumps({
                'done': done, 'total': total, 'elapsed_s': int(elapsed), 'eta_s': int(eta),
                'statuses': dict(counters), 'updated_at': dt.datetime.now().isoformat(timespec='seconds'),
            }, indent=2))
        say(f"[{done}/{total}] {tool.name:22s} {model['model_id'][:60]:60s} "
            + ' '.join(f'{k}={v}' for k, v in sorted(statuses.items()))
            + f"  eta {dt.timedelta(seconds=int(eta))}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, tasks))
    say('stopped' if stop.is_set() else 'campaign finished')


def cmd_status(args: argparse.Namespace) -> None:
    out = Path(args.out)
    cfg = json.loads((out / 'config.json').read_text())
    store = Store(out)
    models = load_models(cfg)
    tools = tool_registry()
    print(f"{cfg['name']}  ({out})")
    for t in cfg['tools']:
        n_models = sum(1 for m in models if tools[t].input != 'fama' or m['fama_path'])
        done = len(store.done(t) & {m['model_id'] for m in models})
        statuses: Counter[str] = Counter()
        csv_path = out / f'{Store.safe(t)}.csv'
        if csv_path.exists():
            with open(csv_path, newline='') as fh:
                for r in csv.DictReader(fh):
                    if r['step'] == 'op' and r['rep'] == '0':
                        statuses[r['status']] += 1
        print(f"  {t:24s} {done:5d}/{n_models:<5d} " + ' '.join(f'{k}={v}' for k, v in sorted(statuses.items())))
    prog = out / 'progress.json'
    if prog.exists():
        p = json.loads(prog.read_text())
        print(f"  this run: {p['done']}/{p['total']} tasks, eta {dt.timedelta(seconds=p['eta_s'])}, "
              f"updated {p['updated_at']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--config', required=True)
    r.add_argument('--out', required=True)
    r.add_argument('--workers', type=int)
    r.add_argument('--limit', type=int, help='only the first N models (smoke tests)')
    r.add_argument('--allow-other-host', action='store_true')
    r.add_argument('--quiet', action='store_true')
    s = sub.add_parser('status')
    s.add_argument('--out', required=True)
    args = ap.parse_args()
    {'run': cmd_run, 'status': cmd_status}[args.cmd](args)


if __name__ == '__main__':
    main()

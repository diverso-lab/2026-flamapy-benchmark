"""Tests for campaign.runner's supervision logic, using a fake worker.

    .venv/bin/python -m pytest tests/test_runner.py -q
"""
from __future__ import annotations

import sys
import textwrap
import threading
from pathlib import Path

import pytest

from campaign.runner import Tool, run_task

FAKE = textwrap.dedent('''
    import json, sys, time, os
    def emit(o): print(json.dumps(o), flush=True)
    emit({"ev": "ready"})
    ops = sys.argv[2].split(",")
    reps = int(sys.argv[1])
    for s in ("setup", "parse", "transform"):
        emit({"ev": "begin", "step": s}); emit({"ev": "end", "step": s, "status": "success", "t": 0.0})
    for op in ops:
        for r in range(reps):
            emit({"ev": "begin", "step": f"op:{op}:{r}"})
            if op == "Hang":
                time.sleep(60)
            if op == "Crash":
                os._exit(9)
            if op == "Fail":
                emit({"ev": "end", "step": f"op:{op}:{r}", "status": "error", "t": 0.0, "error": "boom"}); break
            emit({"ev": "end", "step": f"op:{op}:{r}", "status": "success", "t": 0.001, "value": [op]})
    emit({"ev": "done"})
''')


class FakeTool(Tool):
    def command(self, model, ops, reps, cfg):  # type: ignore[override]
        return [sys.executable, cfg['fake'], str(reps), ','.join(ops)]


@pytest.fixture()
def fake(tmp_path: Path) -> str:
    p = tmp_path / 'fake_worker.py'
    p.write_text(FAKE)
    return str(p)


def _run(fake: str, ops: list[str], timeout: float = 2.0):
    cfg = {'name': 't', 'ops': ops, 'reps': 2, 'timeout_s': timeout, 'fake': fake}
    model = {'model_id': 'm', 'sha256': 'x', 'abs_path': 'm.uvl'}
    rows, values, complete = run_task(model, FakeTool('fake', 'python'), cfg, None, threading.Event())
    assert complete
    return rows, values


def status(rows, op, rep='0'):
    return [r['status'] for r in rows if r['step'] == 'op' and r['operation'] == op and str(r['rep']) == rep]


def test_all_success(fake: str) -> None:
    rows, values = _run(fake, ['A', 'B'])
    assert status(rows, 'A') == ['success'] and status(rows, 'B', '1') == ['success']
    assert {v['operation'] for v in values} == {'A', 'B'}
    assert any(r['step'] == 'startup' for r in rows)


def test_timeout_in_the_middle_restarts_with_remaining_ops(fake: str) -> None:
    rows, _ = _run(fake, ['A', 'Hang', 'B'], timeout=1.5)
    assert status(rows, 'A') == ['success']
    assert status(rows, 'Hang') == ['timeout']
    assert status(rows, 'B') == ['success']            # ran in a restarted worker
    restarts = {r['restart'] for r in rows if r['step'] == 'setup'}
    assert restarts == {0, 1}


def test_crash_is_recorded_and_remaining_ops_run(fake: str) -> None:
    rows, _ = _run(fake, ['Crash', 'B'])
    assert status(rows, 'Crash') == ['crash']
    assert status(rows, 'B') == ['success']


def test_error_skips_remaining_reps_only(fake: str) -> None:
    rows, _ = _run(fake, ['Fail', 'B'])
    assert status(rows, 'Fail') == ['error']
    assert status(rows, 'Fail', '1') == []
    assert status(rows, 'B', '1') == ['success']

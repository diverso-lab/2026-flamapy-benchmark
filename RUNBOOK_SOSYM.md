# Runbook: SoSyM revision experiments (SOSYM-26-00005898)

Branch `sosym-revision`. This replaces the measurement setup of the submitted paper; the old
runner (`main.py`, `scripts/benchmark.py`) and its results (`results/flamapy_benchmark_2026.csv`)
are kept untouched for reference.

## What is measured

| Campaign | Config | Purpose | Where |
|---|---|---|---|
| `rq1_fama_suite` | `campaign/configs/rq1_fama_suite.json` | Ground truth: 44 FaMa test-suite models, 86 expected results, every tool | any machine |
| `pilot` | `campaign/configs/pilot.json` | 50 size-stratified models, every tool, 60 s timeout; checks the pipeline and calibrates cost | any machine |
| `rq2_main` | `campaign/configs/rq2_main.json` | **Main RQ2 run**: flamapy SAT/BDD/Z3, FaMa Sat4j/Choco, FeatureIDE; 1,551 models; 300 s for every tool; 5 repetitions | **lab i9 only** |
| `rq2_sat_solvers` | `campaign/configs/rq2_sat_solvers.json` | Six PySAT solvers, 60 s, 3 repetitions | **lab i9 only** |

RQ1 differential testing (`rq1/cross_check.py`) reuses the values recorded by `rq2_main`, so it
needs no separate run.

Timings that go into the paper must all come from the same machine. The runner refuses to resume a
result directory on a different host (`--allow-other-host` overrides; do not use it for `rq2_*`).

## 1. Set up the lab machine (once)

Needs git, curl, uv (or Python 3.12), JDK 17 with `javac`, Maven.

```bash
git clone -b sosym-revision https://github.com/diverso-lab/2026-flamapy-benchmark
cd 2026-flamapy-benchmark
bash scripts/setup_env.sh          # ~10 min: flamapy at pinned commits, venv, FaMa + FeatureIDE, manifest
```

`setup_env.sh` ends with a self-check and prints whether the BDD backend uses CUDD (`dd.cudd`) or
pure Python (`dd.autoref`). Note it in the paper's setup section either way.

Machine hygiene before `rq2_main`: no other heavy jobs, CPU governor `performance` if available
(`sudo cpupower frequency-set -g performance`), turbo left as is but stated in the paper.

## 2. Launch

```bash
scripts/run_campaign.sh campaign/configs/pilot.json results/pilot_i9        # ~20 min, sanity check on this host
scripts/run_campaign.sh campaign/configs/rq2_main.json results/rq2_main      # ~2 days
# after rq2_main finishes:
scripts/run_campaign.sh campaign/configs/rq2_sat_solvers.json results/rq2_sat_solvers
```

The runner is detached (`nohup`/`setsid`): closing the SSH session does not stop it. Workers are
pinned to CPUs 1–6 (`cpus` in the config) on Linux; CPU 0 and 7 stay free for the OS and the runner.

## 3. Monitor

```bash
.venv/bin/python -m campaign.runner status --out results/rq2_main   # per-tool progress and statuses
tail -f results/rq2_main/runner.log                                  # one line per finished task
cat results/rq2_main/progress.json                                   # done/total, ETA
```

## 4. If something goes wrong

* **Machine rebooted / runner killed / Ctrl-C**: run the same `run_campaign.sh` command again. Finished
  (model, tool) tasks are skipped; only the tasks that were running are redone.
* **One tool misbehaves** (e.g. FeatureIDE): edit nothing in the result directory; fix the tool,
  delete that tool's four files (`featureide.csv`, `.values.jsonl`, `.done`, and nothing else) and
  resume. Other tools' results are unaffected because every tool has its own files.
* **Machine overloaded / swapping**: stop (`kill $(cat results/rq2_main/runner.pid)`), lower
  `workers` in the config, resume.
* Every resume appends a line to `runs.jsonl` with host, environment and package versions.

## 5. Analyse

```bash
.venv/bin/python rq1/check_ground_truth.py results/rq1_fama_suite   # -> rq1/report_ground_truth.md
.venv/bin/python rq1/cross_check.py results/rq2_main                # -> rq1/report_cross_check_rq2_main.md
.venv/bin/python rq2/analyze.py results/rq2_main                    # -> rq2/report_rq2_main.md + tables
```

## Design decisions (to cite in the response letter)

* **One timeout for every tool (300 s)**, enforced by killing the worker's process group. The old
  runner used `SIGALRM`, which cannot interrupt native solver code: its "60 s" PySAT timeouts often
  ran to 900 s, and FaMa got 900 s while flamapy got 60 s.
* **Phases timed separately**: process start-up (runner: spawn to first message), setup (imports /
  library initialisation), parse, transform (FM to solver model), then each operation. A one-shot
  CLI-style cost is rebuilt as start-up + setup + parse + transform + first call.
* **Cold and warm**: repetition 0 is the first call in a fresh process; repetitions 1–4 are warm
  (JIT-compiled for the JVM tools). Each repetition creates a fresh operation / analyser object so
  no cached result is reused; the solver model built in the transform phase is shared.
* **Same inputs**: flamapy and FeatureIDE read the original UVL; FaMa reads FaMa XML converted from
  it, and only for the 1,060 models whose constraints FaMa XML can express exactly. (The old run
  also ran FaMa on 302 models whose conversion dropped constraints, contrary to the paper's text.)
* **Models**: 1,611 files in the UVLHub archive; 20 composed models (UVL `imports`) and 1 file with a
  syntax error excluded; byte-identical duplicates counted once → 1,551 models. Models are identified
  by their path in the archive (the old runner used the file name, which is not unique).
* **Results compared, not just timed**: every first-call result is stored (`*.values.jsonl`) and
  cross-checked between tools (`rq1/cross_check.py`). FeatureIDE adds helper features for UVL group
  syntax; comparisons are restricted to the model's declared features.
* **PySAT solver choice applies to every operation**: flamapy-sat hard-codes glucose3 inside
  CoreFeatures/DeadFeatures (via `pysat_backbone`) and in operation constructors; the worker rebinds
  the solver so the six-solver comparison really varies the solver for all operations.
* **FaMa operations**: Valid, #Products and CoreFeatures questions; dead and false-optional features
  through DetectErrors restricted to the corresponding observations. Sat4j has no CoreFeatures
  implementation (reported as not supported); Choco implements all five.
* **FeatureIDE**: library v3.12.0; solver timeout raised from its built-in 10 s to the campaign
  timeout; configuration counting is by enumeration (`CountSolutionsAnalysis`), as in flamapy's SAT
  backend.
* **Software versions**: flamapy repositories pinned in `campaign/flamapy_commits.txt` (develop
  branches), Python packages in `campaign/requirements-lock.txt`, Java builds in
  `.build/java_versions.txt`; all recorded again in each result directory's `runs.jsonl`.

## Output files (per result directory)

| File | Content |
|---|---|
| `<tool>.csv` | one row per step: phase or operation × repetition, status, wall and CPU time |
| `<tool>.values.jsonl` | normalised result of every first call (for the cross-check) |
| `<tool>.done` | committed tasks (`model_id`, attempt); rows from other attempts are ignored |
| `runs.jsonl` | one entry per start/resume: host, packages, git commits, Java versions |
| `config.json`, `runner.log`, `progress.json` | the configuration used, the log, live progress |

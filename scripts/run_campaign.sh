#!/usr/bin/env bash
# =============================================================================
# run_campaign.sh - start (or resume) a campaign detached from the terminal
#
#   scripts/run_campaign.sh campaign/configs/rq2_main.json results/rq2_main [runner args...]
#
# The runner keeps going after you log out or the SSH session drops. Re-running the same
# command resumes: finished (model, tool) tasks are skipped.
#
#   follow:  tail -f results/rq2_main/runner.log
#   status:  .venv/bin/python -m campaign.runner status --out results/rq2_main
#   stop:    kill "$(cat results/rq2_main/runner.pid)"   (running tasks are redone on resume)
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
[ $# -ge 2 ] || { echo "usage: $0 CONFIG OUTDIR [runner args...]" >&2; exit 1; }
CONFIG="$1"; OUT="$2"; shift 2
mkdir -p "${OUT}"

if [ -f "${OUT}/runner.pid" ] && kill -0 "$(cat "${OUT}/runner.pid")" 2>/dev/null; then
    echo "already running (pid $(cat "${OUT}/runner.pid")); see ${OUT}/runner.log" >&2
    exit 1
fi

# Java from Homebrew is keg-only on macOS.
if [ -d /opt/homebrew/opt/openjdk@17/bin ]; then export PATH="/opt/homebrew/opt/openjdk@17/bin:${PATH}"; fi

CMD=(.venv/bin/python -m campaign.runner run --config "${CONFIG}" --out "${OUT}" --quiet "$@")
if command -v setsid >/dev/null 2>&1; then
    nohup setsid "${CMD[@]}" >> "${OUT}/nohup.out" 2>&1 < /dev/null &
else
    nohup "${CMD[@]}" >> "${OUT}/nohup.out" 2>&1 < /dev/null &
fi
PID=$!
echo "${PID}" > "${OUT}/runner.pid"

# Keep a Mac awake while the runner lives (no-op on Linux).
if command -v caffeinate >/dev/null 2>&1; then
    nohup caffeinate -dimsu -w "${PID}" >/dev/null 2>&1 &
fi

echo "started pid ${PID}"
echo "  log:    tail -f ${OUT}/runner.log"
echo "  status: .venv/bin/python -m campaign.runner status --out ${OUT}"

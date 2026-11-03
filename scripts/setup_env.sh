#!/usr/bin/env bash
# =============================================================================
# setup_env.sh - build the exact software environment of the SoSyM campaign
#
#   1. clones the flamapy repositories at the commits pinned in
#      campaign/flamapy_commits.txt into .build/flamapy/<repo>
#   2. creates .venv (Python 3.12) with those repositories installed editable and the
#      third-party versions pinned in campaign/requirements-lock.txt
#   3. builds the Java baselines (scripts/build_java.sh)
#   4. builds prepared/manifest.csv and the FaMa XML models (campaign.prepare)
#   5. prints a short self-check
#
# Same script on the Mac and on the lab machine. Idempotent; FORCE=1 rebuilds the venv.
# Needs: git, curl, uv (preferred) or python3.12, JDK 11+ with javac, Maven 3.6+.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
SRC="${ROOT}/.build/flamapy"
FORCE="${FORCE:-0}"
info() { echo "[setup_env] $*"; }
die()  { echo "[setup_env] ERROR: $*" >&2; exit 1; }

# ----------------------------------------------------------------------------- 1. flamapy sources
mkdir -p "${SRC}"
grep -v '^#' campaign/flamapy_commits.txt | while read -r repo commit url; do
    [ -n "${repo}" ] || continue
    https_url="$(echo "${url}" | sed -e 's#^git@github.com:#https://github.com/#')"
    if [ ! -d "${SRC}/${repo}/.git" ]; then
        info "cloning ${repo}"
        git clone -q "${https_url}" "${SRC}/${repo}"
    fi
    git -C "${SRC}/${repo}" fetch -q origin
    git -C "${SRC}/${repo}" checkout -q "${commit}"
    info "${repo} at $(git -C "${SRC}/${repo}" rev-parse --short HEAD)"
done

# ----------------------------------------------------------------------------- 2. Python env
if [ "${FORCE}" = "1" ]; then rm -rf .venv; fi
if [ ! -x .venv/bin/python ]; then
    if command -v uv >/dev/null 2>&1; then
        uv venv -q --python 3.12 .venv
    elif command -v python3.12 >/dev/null 2>&1; then
        python3.12 -m venv .venv
    else
        die "need uv or python3.12"
    fi
fi
PIP_INSTALL=(.venv/bin/python -m pip install)
if command -v uv >/dev/null 2>&1; then PIP_INSTALL=(uv pip install --python .venv/bin/python); fi
info "installing flamapy (editable, pinned commits) and locked dependencies"
"${PIP_INSTALL[@]}" -q \
    -e "${SRC}/flamapy_fw" -e "${SRC}/fm_metamodel" -e "${SRC}/pysat_metamodel" \
    -e "${SRC}/bdd_metamodel" -e "${SRC}/z3_metamodel" -e "${SRC}/flamapy"
"${PIP_INSTALL[@]}" -q -r campaign/requirements-lock.txt

# dd's PyPI wheel is pure Python (dd.autoref); flamapy's BDD plugin uses CUDD (dd.cudd) when
# present, which is orders of magnitude faster. Rebuild the pinned dd from source with CUDD
# (dd's build downloads CUDD 3.0.0 and checks its SHA-256; needs a C compiler). NO_CUDD=1 skips.
if [ "${NO_CUDD:-0}" != "1" ] && ! .venv/bin/python -c 'import dd.cudd' 2>/dev/null; then
    DD_VERSION="$(.venv/bin/python -c 'import importlib.metadata as m; print(m.version("dd"))')"
    info "building dd ${DD_VERSION} with CUDD"
    "${PIP_INSTALL[@]}" -q cython setuptools wheel
    if [ "${PIP_INSTALL[0]}" = "uv" ]; then REINSTALL=(--reinstall --no-cache); else REINSTALL=(--force-reinstall --no-cache-dir); fi
    if ! DD_FETCH=1 DD_CUDD=1 DD_CUDD_ZDD=1 "${PIP_INSTALL[@]}" -q "${REINSTALL[@]}" --no-deps \
            --no-binary dd --no-build-isolation "dd==${DD_VERSION}"; then
        info "WARNING: building dd with CUDD failed; the BDD plugin will use pure-Python dd"
    fi
fi

# ----------------------------------------------------------------------------- 3. Java baselines
bash scripts/build_java.sh

# ----------------------------------------------------------------------------- 4. models
# manifest.csv is in git, but the models it points to (.models/, from the zip) and the FaMa XML
# (prepared/fama/) are not: a fresh clone needs this step too
if [ ! -f prepared/manifest.csv ] || [ ! -d .models ] || [ ! -d prepared/fama ] || [ "${FORCE}" = "1" ]; then
    info "extracting the models and building prepared/ (takes a few minutes)"
    .venv/bin/python -m campaign.prepare --jobs "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
fi

# ----------------------------------------------------------------------------- 5. self-check
info "self-check"
.venv/bin/python - <<'PYEOF'
import importlib.metadata as m
for p in ("flamapy-fw", "flamapy-fm", "flamapy-sat", "flamapy-bdd", "flamapy-z3", "python-sat", "dd", "z3-solver"):
    print(f"  {p:12s} {m.version(p)}")
try:
    import dd.cudd  # noqa: F401
    print("  dd backend   CUDD (dd.cudd)")
except Exception:
    print("  dd backend   pure Python (dd.autoref) - dd.cudd not available")
PYEOF
MODEL="$(.venv/bin/python -c "import csv; print(next(r['path'] for r in csv.DictReader(open('prepared/manifest.csv')) if not r['excluded'] and r['fama_path']))")"
# capture first: `cmd | grep -q` closes the pipe at the first match (BrokenPipeError + pipefail)
OUT_PY="$(.venv/bin/python -m campaign.py_worker "${MODEL}" bdd n/a 1 ConfigurationsNumber 2>&1 || true)"
grep -q '"status": "success"' <<< "${OUT_PY}" \
    && info "flamapy worker OK" || die "flamapy worker failed on ${MODEL}"
OUT_FIDE="$(java -cp "featureide_cli/build:featureide_cli/lib/*" FideHarness "${MODEL}" 60000 1 ConfigurationsNumber 2>&1 || true)"
grep -q '"status":"success"' <<< "${OUT_FIDE}" \
    && info "FeatureIDE harness OK" || die "FeatureIDE harness failed on ${MODEL}"
info "environment ready"

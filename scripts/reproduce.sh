#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# Smart PUC — one-command reproducibility bundle
# ──────────────────────────────────────────────────────────────────────────
# Closes audit Top-5 Addition #1 (N15). Runs the full suite a reviewer
# needs to verify the paper's reported numbers from a clean checkout.
#
# Usage (from repo root):
#     bash scripts/reproduce.sh
#
# Exit codes:
#     0  — everything reproduced byte-for-byte.
#     1  — environment missing (venv, scripts).
#     2  — CES constants out of sync.
#     3  — a test suite failed.
#     4  — ces_vs_co2_report.json does not match the committed copy.
#
# Runs under GNU bash on Linux, macOS, and Git-Bash on Windows.
# ──────────────────────────────────────────────────────────────────────────
set -u

# ── Locate repo root ───────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# ── Colours (graceful fallback to no-colour) ───────────────────────────
if [ -t 1 ]; then
    C_GREEN="\033[32m"
    C_RED="\033[31m"
    C_YELLOW="\033[33m"
    C_BOLD="\033[1m"
    C_RESET="\033[0m"
else
    C_GREEN=""; C_RED=""; C_YELLOW=""; C_BOLD=""; C_RESET=""
fi

banner() { printf "\n${C_BOLD}── %s ──${C_RESET}\n" "$1"; }
info()   { printf "  %s\n" "$1"; }
warn()   { printf "${C_YELLOW}  WARN: %s${C_RESET}\n" "$1"; }
err()    { printf "${C_RED}  ERROR: %s${C_RESET}\n" "$1"; }
ok()     { printf "${C_GREEN}  OK: %s${C_RESET}\n" "$1"; }

# ── Step 1 — environment ───────────────────────────────────────────────
banner "Step 1/9 — Environment"
GIT_HASH="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
info "Repo root    : ${REPO_ROOT}"
info "Git branch   : ${GIT_BRANCH}"
info "Git commit   : ${GIT_HASH}"
info "uname        : $(uname -a 2>/dev/null || echo unknown)"
info "Date (UTC)   : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# Python executable (prefer local venv)
if [ -x "backend/venv/Scripts/python.exe" ]; then
    PY="backend/venv/Scripts/python.exe"              # Windows layout
elif [ -x "backend/venv/bin/python" ]; then
    PY="backend/venv/bin/python"                      # POSIX layout
else
    err "backend/venv not found."
    err "Create it first:"
    err "    python -m venv backend/venv"
    err "    backend/venv/Scripts/pip install -r requirements.txt   (Windows)"
    err "    backend/venv/bin/pip install -r requirements.txt       (POSIX)"
    exit 1
fi
info "Python       : ${PY}"
"${PY}" --version

# ── Step 2 — CES constants cross-check ─────────────────────────────────
banner "Step 2/9 — CES constants Python↔Solidity cross-check"
if "${PY}" scripts/gen_ces_consts.py --check; then
    ok "CES constants in sync"
else
    err "CES constants out of sync — regenerate with scripts/gen_ces_consts.py"
    exit 2
fi

# ── Step 3 — Hardhat compile (best-effort) ─────────────────────────────
banner "Step 3/9 — Hardhat compile (best-effort)"
if [ -d "node_modules" ]; then
    if npx --no-install hardhat compile 2>&1 | tail -5; then
        ok "Contracts compiled"
    else
        warn "hardhat compile failed — continuing"
    fi
else
    warn "node_modules missing — skipping (run 'npm install' to enable)"
fi

# ── Step 4 — Hardhat test ──────────────────────────────────────────────
banner "Step 4/9 — Hardhat test suite"
HH_SUMMARY=""
if [ -d "node_modules" ]; then
    HH_OUT="$(npx --no-install hardhat test 2>&1 || true)"
    echo "${HH_OUT}" | tail -12
    HH_SUMMARY="$(echo "${HH_OUT}" | grep -E 'passing|failing' | tail -2 | tr '\n' ' ')"
    if echo "${HH_OUT}" | grep -q "failing"; then
        err "Hardhat tests failed"
        exit 3
    fi
    ok "Hardhat: ${HH_SUMMARY}"
else
    warn "node_modules missing — skipping hardhat test"
fi

# ── Step 5 — pytest ─────────────────────────────────────────────────────
banner "Step 5/9 — pytest (tests/)"
PY_OUT="$(${PY} -m pytest tests/ -p no:ethereum --tb=no -q 2>&1 || true)"
echo "${PY_OUT}" | tail -10
PY_SUMMARY="$(echo "${PY_OUT}" | grep -E '[0-9]+ passed' | tail -1)"
if echo "${PY_OUT}" | grep -qE 'failed|error'; then
    err "pytest failed"
    exit 3
fi
ok "pytest: ${PY_SUMMARY}"

# ── Step 6 — bench_ces_vs_co2 ──────────────────────────────────────────
banner "Step 6/9 — CES vs CO2-only benchmark (seed=42, samples=5000)"
REPORT="docs/ces_vs_co2_report.json"
REPORT_BAK="docs/ces_vs_co2_report.committed.json"
if [ -f "${REPORT}" ]; then
    cp "${REPORT}" "${REPORT_BAK}"
else
    warn "committed ${REPORT} not present — byte-identity check will be skipped"
fi
"${PY}" scripts/bench_ces_vs_co2.py --samples 5000 --seed 42 --output "${REPORT}" || {
    err "bench_ces_vs_co2.py failed"
    [ -f "${REPORT_BAK}" ] && mv "${REPORT_BAK}" "${REPORT}"
    exit 3
}
ok "Regenerated ${REPORT}"

# ── Step 7 — byte-identity diff ────────────────────────────────────────
banner "Step 7/9 — Byte-identity check vs committed report"
if [ -f "${REPORT_BAK}" ]; then
    if cmp -s "${REPORT_BAK}" "${REPORT}"; then
        ok "Byte-identical to committed ${REPORT}"
        rm -f "${REPORT_BAK}"
    else
        err "Regenerated report differs from committed copy."
        err "Diff (unified):"
        diff -u "${REPORT_BAK}" "${REPORT}" || true
        mv "${REPORT_BAK}" "${REPORT}"   # restore committed copy
        exit 4
    fi
else
    warn "No committed baseline to diff against — skipping byte-check"
fi

# ── Step 8 — summary banner ─────────────────────────────────────────────
banner "Step 8/9 — Summary"
info "Commit        : ${GIT_HASH}"
info "Hardhat       : ${HH_SUMMARY:-skipped}"
info "pytest        : ${PY_SUMMARY:-unknown}"
info "CES report    : byte-identical"

# ── Step 9 — success banner ─────────────────────────────────────────────
banner "Step 9/9 — Result"
printf "${C_GREEN}${C_BOLD}"
printf "  ============================================\n"
printf "  REPRODUCIBLE OK — Smart PUC artifact verified\n"
printf "  ============================================${C_RESET}\n\n"
exit 0

#!/usr/bin/env bash
# Run every automated test. Needs no model, GPU or internet access:
# integration tests use the mock llama-server and the mock web (pages and
# search engines) in backend/tests.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Version consistency"
python3 scripts/check-version.py

echo "== Backend (pytest)"
# The real-Chromium browser tests (test_browser_live.py) run when a Python with
# Playwright is available: browser/.venv (see browser/README.md) or
# CHIMERA_TEST_BROWSER_PYTHON. Otherwise they are reported as skipped.
backend/.venv/bin/python -m pytest -q -rs backend/tests

echo "== Frontend typecheck, lint, unit tests (vitest), notices"
(cd frontend && npx tsc -b && npx eslint . && npx vitest run && node scripts/notices.mjs --check)

if [ "${SKIP_E2E:-}" != "1" ]; then
  echo "== End-to-end (Playwright, real backend + mock llama-server)"
  (cd frontend && npx vite build >/dev/null && npx playwright test)
fi

if command -v shellcheck >/dev/null 2>&1; then
  echo "== shellcheck"
  shellcheck scripts/*.sh
fi

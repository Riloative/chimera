#!/usr/bin/env bash
# Run every automated test. Needs no model, GPU or internet access:
# integration tests use the mock llama-server in backend/tests.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Backend (pytest)"
backend/.venv/bin/python -m pytest -q backend/tests

echo "== Frontend typecheck + unit tests (vitest)"
(cd frontend && npx tsc -b && npx vitest run)

if [ "${SKIP_E2E:-}" != "1" ]; then
  echo "== End-to-end (Playwright, real backend + mock llama-server)"
  (cd frontend && npx vite build >/dev/null && npx playwright test)
fi

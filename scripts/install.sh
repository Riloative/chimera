#!/usr/bin/env bash
# Native (non-Docker) install: builds the web UI once and installs the backend
# into backend/.venv. After this, Chimera runs fully offline.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Python 3.11+ is required (found: $("$PYTHON" --version 2>&1))." >&2; exit 1; }
command -v npm >/dev/null || { echo "Node.js 20+ and npm are required to build the web UI." >&2; exit 1; }

echo "→ Building the web interface"
(cd frontend && npm ci --no-audit --no-fund && npm run build)

echo "→ Installing the backend into backend/.venv"
"$PYTHON" -m venv backend/.venv
backend/.venv/bin/pip install --upgrade pip >/dev/null
backend/.venv/bin/pip install -e "backend[test]"

[ -f .env ] || cp .env.example .env
echo
echo "Done. Start Chimera with:  ./scripts/start.sh"
echo "Then open http://127.0.0.1:8700"

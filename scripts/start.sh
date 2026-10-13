#!/usr/bin/env bash
# Start Chimera (native install). Reads ./.env if present.
set -euo pipefail
cd "$(dirname "$0")/.."
[ -x backend/.venv/bin/chimera ] || { echo "Run ./scripts/install.sh first." >&2; exit 1; }
[ -f frontend/dist/index.html ] || { echo "Web UI not built. Run ./scripts/install.sh." >&2; exit 1; }
export CHIMERA_STATIC_DIR="${CHIMERA_STATIC_DIR:-$PWD/frontend/dist}"
exec backend/.venv/bin/chimera "$@"

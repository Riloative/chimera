#!/usr/bin/env bash
# Development: backend on :8700, Vite dev server with hot reload on :5173.
#   ./scripts/dev.sh          # use your real llama-server
#   ./scripts/dev.sh --mock   # also start the mock llama-server on :8080
set -euo pipefail
cd "$(dirname "$0")/.."
pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

if [ "${1:-}" = "--mock" ]; then
  backend/.venv/bin/python backend/tests/mock_llama_server.py --port 8080 \
    --model qwen2.5-7b-instruct-q4_k_m.gguf --model llama-3.2-3b-instruct-q8_0.gguf &
  pids+=($!)
fi
CHIMERA_DATA_DIR="${CHIMERA_DATA_DIR:-$PWD/data-dev}" backend/.venv/bin/python -m chimera &
pids+=($!)
(cd frontend && npx vite --host 127.0.0.1) &
pids+=($!)
echo "Open http://127.0.0.1:5173"
wait

#!/usr/bin/env bash
# Start CAO server in WSL. Runs Cursor CLI login first, then starts the server.
# Usage (from WSL): bash scripts/start-server-wsl.sh
# Or: cd /mnt/d/Phani/projects/CAO/cli-agent-orchestrator && bash scripts/start-server-wsl.sh

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Load .env (e.g. CURSOR_API_KEY) if present
set -a
[[ -f .env ]] && source .env
set +a

echo "=== CAO server (WSL) ==="
echo "Repo: $REPO_ROOT"
echo ""

# Ensure venv and CAO are available
if [[ ! -d .venv ]]; then
  echo "Error: .venv not found. Run first: uv sync  (or bash scripts/setup-wsl-cursor-cli.sh)"
  exit 1
fi
source .venv/bin/activate
export PATH="$HOME/.local/bin:$PATH"

# Cursor CLI login (only if no API key)
if [[ -z "$CURSOR_API_KEY" ]]; then
  echo "[1/2] Cursor CLI login (no CURSOR_API_KEY set)..."
  if ! agent login; then
    echo "Error: agent login failed or was cancelled. Fix auth and run this script again."
    exit 1
  fi
  echo ""
else
  echo "[1/2] Using CURSOR_API_KEY from .env (skipping agent login)"
fi

# Start CAO server
echo "[2/2] Starting CAO server..."
exec cao-server

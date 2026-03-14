#!/usr/bin/env bash
# Launch supervisor agent in WSL (run this in Terminal 2 while server runs in Terminal 1).
# Usage: bash scripts/launch-agent-wsl.sh

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

source .venv/bin/activate
export PATH="$HOME/.local/bin:$PATH"

cao launch --agents code_supervisor --provider cursor_cli --yolo

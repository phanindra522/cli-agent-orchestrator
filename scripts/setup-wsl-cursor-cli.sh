#!/usr/bin/env bash
# One-time setup for running CAO with Cursor CLI inside WSL.
# Run from repo root: bash scripts/setup-wsl-cursor-cli.sh
# Or from anywhere: bash /mnt/d/Phani/projects/CAO/cli-agent-orchestrator/scripts/setup-wsl-cursor-cli.sh

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== CAO + Cursor CLI setup (WSL) ==="
echo "Repo root: $REPO_ROOT"
echo ""

# 1. Install tmux
echo "[1/5] Installing tmux..."
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq
sudo apt-get install -y tmux
tmux -V

# 2. Install Cursor Agent CLI
echo ""
echo "[2/5] Installing Cursor Agent CLI..."
if command -v agent &>/dev/null; then
  echo "  agent already installed: $(agent --version 2>/dev/null || true)"
else
  sudo apt-get install -y curl
  curl -fsSL https://cursor.com/install | bash
  # Ensure agent is on PATH (install script may add to ~/.local/bin)
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v agent &>/dev/null; then
    echo "  Add to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo "  Then run: source ~/.bashrc  (or ~/.zshrc)"
  else
    agent --version
  fi
fi

# 3. uv, Python, and CAO (uv sync)
echo ""
echo "[3/5] Ensuring uv and CAO..."
sudo apt-get install -y python3 curl
python3 --version
if ! command -v uv &>/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
uv --version
uv sync
source .venv/bin/activate
command -v cao && command -v cao-server

# 4. Agent profile
echo ""
echo "[4/5] Installing code_supervisor for cursor_cli..."
export PYTHONIOENCODING=utf-8
cao install code_supervisor --provider cursor_cli

# 5. Reminder: agent login
echo ""
echo "[5/5] Cursor CLI authentication"
if ! agent login --help &>/dev/null 2>&1; then
  echo "  Run once: agent login"
else
  echo "  If not logged in, run once: agent login"
fi

echo ""
echo "=== Setup done. Next steps ==="
echo "1. Terminal 1 (server): bash scripts/start-server-wsl.sh"
echo "   (Runs agent login first, then cao-server.)"
echo "2. Terminal 2 (agent):  cd $REPO_ROOT && source .venv/bin/activate && cao launch --agents code_supervisor --provider cursor_cli --yolo"
echo ""

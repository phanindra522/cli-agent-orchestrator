# Cursor CLI Provider

## Overview

The Cursor CLI provider enables CAO to work with [Cursor Agent CLI](https://cursor.com/docs/cli), Cursor's command-line agent. **On Windows, the recommended way is the [WSL approach](#wsl-approach-recommended-on-windows) below.** The CLI runs interactively in tmux with `--trust` and `--yolo` so workspace trust and command approval are handled without prompts.

## Setup Checklist (steps to make CAO work with Cursor CLI)

Execute in order:

1. **Install CAO**  
   Clone the repo and install dependencies (see [README](../README.md)):
   ```bash
   git clone https://github.com/awslabs/cli-agent-orchestrator.git
   cd cli-agent-orchestrator
   uv sync   # or: pip install -e ".[dev]"
   ```

2. **Install Cursor Agent CLI**  
   Follow [Cursor CLI installation](https://cursor.com/docs/cli). Example (Unix/macOS):
   ```bash
   curl https://cursor.com/install -fsSL | bash
   ```
   On Windows, use the installer or method described in Cursor’s docs.

3. **Authenticate Cursor CLI**  
   Run once:
   ```bash
   agent login
   ```
   Verify:
   ```bash
   agent --version
   ```

4. **Install tmux (3.3+)**  
   Required for CAO. On Windows use WSL or a native tmux build; on macOS/Linux use your package manager or the [project’s tmux install script](https://raw.githubusercontent.com/awslabs/cli-agent-orchestrator/main/tmux-install.sh).

5. **Start the CAO server**  
   In one terminal:
   ```bash
   cao-server
   ```

6. **Install an agent profile (optional)**  
   ```bash
   cao install code_supervisor --provider cursor_cli
   ```

7. **Launch with Cursor CLI**  
   In another terminal, from your project directory:
   ```bash
   cao launch --agents code_supervisor --provider cursor_cli
   ```
   Confirm workspace trust when prompted (or use `--yolo` to skip).

8. **Verify**  
   You should be attached to a tmux session with the Cursor CLI agent running. Interact in that window or use MCP handoff/assign from another agent.

**On Windows:**  
- **tmux** is required for CAO; use WSL or a native Windows tmux build and ensure `tmux` is on PATH.  
- **Cursor Agent CLI** may need to be installed via Cursor’s Windows instructions and `agent` added to PATH.  
- If `cao install` fails with a character-encoding error, run: `$env:PYTHONIOENCODING="utf-8"; cao install code_supervisor --provider cursor_cli` (PowerShell).

### WSL approach (recommended on Windows)

Use WSL2 so you have tmux and Cursor Agent CLI (Linux) in one environment.

1. **Install WSL2**  
   In **PowerShell as Administrator**:
   ```powershell
   wsl --install
   ```
   Reboot if prompted. After reboot, open **Ubuntu** (or your default WSL distro) from the Start menu.

2. **One-time setup inside WSL**  
   In the WSL terminal, run the project’s setup script (replace the path if your repo is elsewhere):
   ```bash
   cd /mnt/d/Phani/projects/CAO/cli-agent-orchestrator
   bash scripts/setup-wsl-cursor-cli.sh
   ```
   The script installs tmux, Cursor Agent CLI (`agent`), Python/pip, and CAO. You will be prompted for `agent login` once.

3. **Start CAO and launch with Cursor CLI**  
   Always use a **WSL terminal** (not PowerShell) for these steps.

   **Terminal 1 – login then start the server:**
   ```bash
   cd /mnt/d/Phani/projects/CAO/cli-agent-orchestrator
   bash scripts/start-server-wsl.sh
   ```
   This runs `agent login` first (if needed), then starts `cao-server`.

   **Terminal 2 – launch the agent:**
   ```bash
   cd /mnt/d/Phani/projects/CAO/cli-agent-orchestrator
   cao launch --agents code_supervisor --provider cursor_cli --yolo
   ```
   You will be attached to a tmux session with Cursor CLI. Detach with `Ctrl+b` then `d`.

4. **Optional: run setup script again**  
   If you open a new WSL distro or the script failed partway, run `bash scripts/setup-wsl-cursor-cli.sh` again; it is safe to re-run.

### Further steps (non-WSL Windows)

9. **Install tmux on Windows (pick one)**  
   - **MSYS2**: Install from [msys2.org](https://www.msys2.org/), then in the MSYS2 terminal run `pacman -S tmux`. Add the MSYS2 `usr/bin` to your PATH.  
   - **Portable**: Use [itmux](https://github.com/itefixnet/itmux) and add its bin folder to PATH.

10. **Install Cursor Agent CLI**  
    Official install is Unix/macOS only. On Windows without WSL, check [Cursor CLI docs](https://cursor.com/docs/cli) for a Windows build.

11. **Run launch**  
    From the project directory (PowerShell, UTF-8):
    ```powershell
    $env:PYTHONIOENCODING = "utf-8"
    cao launch --agents code_supervisor --provider cursor_cli --yolo
    ```
    Or use `.\scripts\run-cursor-cli.ps1` to check prerequisites and launch.

## Prerequisites

- **Cursor Agent CLI**: Install via `curl https://cursor.com/install -fsSL | bash` (see [Cursor CLI](https://cursor.com/docs/cli))
- **Authentication**: Run `agent login` or set `CURSOR_API_KEY` if supported
- **tmux 3.3+**

Verify installation:

```bash
agent --version
```

## Quick Start

```bash
# Launch with CAO
cao launch --agents code_supervisor --provider cursor_cli
```

## Status Detection

The provider infers Cursor CLI state from terminal output:

| Status | Description |
|--------|-------------|
| **IDLE** | Ready for input (prompt or "Type a message"–style text) |
| **PROCESSING** | Thinking / Running / Executing or spinner visible |
| **COMPLETED** | Idle prompt plus response content |
| **WAITING_USER_ANSWER** | Command approval prompt (y/n) — with `--yolo` this is bypassed |
| **ERROR** | Error/APIError/Traceback patterns |

Exact prompt format may vary by Cursor CLI version; patterns in `cursor_cli.py` can be tuned if detection is unreliable.

## Agent Profiles

Agent profile is optional. Cursor CLI reads `.cursor/rules` and `AGENTS.md` in the workspace. Use `cao install --provider cursor_cli` to copy agent context; you can also add project rules under `.cursor/rules` or an `AGENTS.md` at the project root.

## Exit

Cursor CLI exits with **Ctrl+D** (double-press per docs). The provider sends a single `C-d`; if the process does not exit, the caller may send it twice.

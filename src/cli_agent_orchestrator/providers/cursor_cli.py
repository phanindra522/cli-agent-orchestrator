"""Cursor CLI provider implementation.

Cursor Agent CLI (https://cursor.com/docs/cli) runs in the terminal and supports
Agent, Plan, and Ask modes. CAO runs it interactively in tmux with --trust and
--yolo so workspace trust and command approval are handled without prompts.

Key characteristics:
- Command: ``agent`` (or ``agent agent``). Use ``agent --trust --yolo`` for
  headless/CAO: --trust skips workspace trust prompt, --yolo auto-approves commands.
- Exit: Ctrl+D (double-press per docs; we send C-d once; caller may send twice if needed).
- Idle/Completed: Detected via prompt-like patterns (›, >, or "Type"/"Message" in footer).
  Cursor CLI exact prompt format is not fully documented; patterns below may need tuning.
- Processing: "Thinking", "Running", "Executing", spinner-like output.
- WAITING_USER_ANSWER: Command approval prompts (Run this command? y/n).
"""

import logging
import os
import re
import shlex
import time
from typing import Optional

from cli_agent_orchestrator.clients.tmux import tmux_client
from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.base import BaseProvider
from cli_agent_orchestrator.utils.agent_profiles import load_agent_profile
from cli_agent_orchestrator.utils.terminal import wait_for_shell, wait_until_status

logger = logging.getLogger(__name__)

# Seconds to wait for Cursor CLI to show a ready prompt (see get_status). Override with CAO_CURSOR_INIT_TIMEOUT.
def _cursor_init_timeout_seconds() -> float:
    raw = os.getenv("CAO_CURSOR_INIT_TIMEOUT", "120")
    try:
        return max(30.0, min(float(raw), 600.0))
    except ValueError:
        return 120.0


class ProviderError(Exception):
    """Exception raised for Cursor CLI provider-specific errors."""

    pass


# Strip ANSI escape codes for reliable text matching
ANSI_CODE_PATTERN = r"\x1b\[[0-9;]*m"

# Idle prompt: Cursor CLI may use › or > or similar. Also match common placeholder text.
IDLE_PROMPT_PATTERN = r"(?:^|\s)(?:›|❯|>)\s*$|Type a message|Enter your message|Ask anything"
IDLE_PROMPT_TAIL_LINES = 15
# For log-file quick check: any of these suggests the CLI is up and ready for input
IDLE_PROMPT_PATTERN_LOG = r"(?:›|❯|>)\s*$|Type a message|Ask anything"

# Cursor Agent main UI ready: input prompt "→", footer "/ commands · @ files · ! shell", or "▶︎ Auto-run"
# Check this before PROCESSING so "Claude 4.6 Opus (Thinking)" in the footer doesn't keep us in PROCESSING.
CURSOR_MAIN_READY_PATTERN = r"→|/ commands\s*·\s*@|▶︎\s*Auto-run|! shell"

# Processing: Cursor shows thinking/working state (but not the static "(Thinking)" in model name)
PROCESSING_PATTERN = r"\b(Thinking|Running|Executing|Working|Processing|Analyzing)\b"
# Spinner or progress (Braille, dots, etc.)
SPINNER_PATTERN = r"[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s|\.\.\.\s*$"

# Command approval: "Run this command?" or "Execute?" with y/n
WAITING_USER_ANSWER_PATTERN = r"(?:Run|Execute|Allow).*?(?:\?|:).*?\b(?:y/n|yes/no)\b"

# Assistant response: common markers (Cursor may use different; adjust if needed)
RESPONSE_MARKER_PATTERN = r"^(?:\s*[•●◦▪]\s|Assistant\s*:|\s{2,})"

# Errors
ERROR_PATTERN = r"^(?:Error:|ERROR:|Traceback|ConnectionError|APIError)"


class CursorCliProvider(BaseProvider):
    """Provider for Cursor Agent CLI integration."""

    def __init__(
        self,
        terminal_id: str,
        session_name: str,
        window_name: str,
        agent_profile: Optional[str] = None,
    ):
        super().__init__(terminal_id, session_name, window_name)
        self._initialized = False
        self._agent_profile = agent_profile

    def _build_cursor_command(self) -> str:
        """Build Cursor CLI command for CAO (tmux is interactive, so no --trust).

        --trust is only valid with --print/headless; in tmux we run interactively,
        so we use --yolo only (auto-approve commands). Workspace trust is confirmed
        at launch via cao launch --yolo.

        Loads .env from the current working directory (e.g. CURSOR_API_KEY) before
        running agent, so API key auth works in the tmux pane.
        """
        if self._agent_profile:
            try:
                load_agent_profile(self._agent_profile)
                # Profile loaded for validation; Cursor uses .cursor/rules / AGENTS.md
                # so no extra args needed here
            except Exception as e:
                raise ProviderError(f"Failed to load agent profile '{self._agent_profile}': {e}")

        # --model auto: use Cursor's automatic model selection instead of a fixed model (e.g. Claude 4.6 Opus)
        agent_cmd = shlex.join(["agent", "--yolo", "--model", "auto"])
        # Source .env so CURSOR_API_KEY (and other vars) are set in the tmux pane
        return f"source .env 2>/dev/null || true; {agent_cmd}"

    def initialize(self) -> bool:
        """Initialize Cursor CLI by starting agent --trust --yolo in the tmux window."""
        if not wait_for_shell(tmux_client, self.session_name, self.window_name, timeout=10.0):
            raise TimeoutError("Shell initialization timed out after 10 seconds")

        command = self._build_cursor_command()
        logger.info("Cursor CLI init: sending command to tmux (session=%s, window=%s)", self.session_name, self.window_name)
        tmux_client.send_keys(self.session_name, self.window_name, command)

        # Cursor CLI can show "Workspace Trust Required" in interactive mode (no --trust in non-headless).
        # Accept it once by sending 'a' (Trust this workspace) then Enter.
        trust_prompt_sent = False

        init_timeout = _cursor_init_timeout_seconds()
        # Cursor CLI can be slow to start (auth check, first load)
        start = time.monotonic()
        deadline = start + init_timeout
        last_log_at = 0.0
        while time.monotonic() < deadline:
            elapsed = time.monotonic() - start
            status = self.get_status()
            # If workspace trust prompt is showing, accept it (send 'a' then Enter)
            if not trust_prompt_sent:
                output = tmux_client.get_history(self.session_name, self.window_name, tail_lines=30) or ""
                clean_lower = re.sub(ANSI_CODE_PATTERN, "", output).lower()
                if "workspace trust" in clean_lower or "trust this workspace" in clean_lower:
                    logger.info("Cursor CLI init: accepting workspace trust prompt")
                    tmux_client.send_keys(self.session_name, self.window_name, "a", enter_count=1)
                    trust_prompt_sent = True
            if elapsed - last_log_at >= 10.0:
                logger.info(
                    "Cursor CLI init: waiting for idle (elapsed=%.0fs, status=%s)",
                    elapsed, status.value if status else "unknown",
                )
                last_log_at = elapsed
            if status in (TerminalStatus.IDLE, TerminalStatus.COMPLETED):
                logger.info("Cursor CLI init: ready (elapsed=%.0fs)", elapsed)
                break
            if status == TerminalStatus.ERROR:
                output = tmux_client.get_history(self.session_name, self.window_name) or ""
                clean = re.sub(ANSI_CODE_PATTERN, "", output).lower()
                if "command not found" in clean or "agent: not found" in clean:
                    raise ProviderError(
                        "Cursor CLI (agent) not found in tmux session. "
                        "Ensure ~/.local/bin is on PATH; restart cao-server and try again."
                    )
                if "login" in clean or "authenticate" in clean or "not logged" in clean:
                    raise ProviderError(
                        "Cursor CLI requires authentication. Run 'agent login' in a WSL terminal, then try again."
                    )
            time.sleep(1.0)
        else:
            output = tmux_client.get_history(self.session_name, self.window_name, tail_lines=50) or ""
            clean = re.sub(ANSI_CODE_PATTERN, "", output).lower()
            # Log last lines of terminal output for debugging (strip to avoid huge logs)
            last_lines = "\n".join(output.strip().splitlines()[-20:]) if output else "(no output)"
            logger.warning(
                "Cursor CLI init timeout. Last terminal output (last 20 lines):\n%s",
                last_lines[:2000],
            )
            if "command not found" in clean or "agent: not found" in clean:
                raise ProviderError(
                    "Cursor CLI (agent) not found in tmux session. "
                    "Ensure ~/.local/bin is on PATH; restart cao-server and try again."
                )
            if "login" in clean or "authenticate" in clean or "not logged" in clean:
                raise ProviderError(
                    "Cursor CLI requires authentication. Run 'agent login' in a WSL terminal, then try again."
                )
            raise TimeoutError(
                f"Cursor CLI initialization timed out after {int(init_timeout)} seconds. "
                "Check the tmux window for errors, or run 'agent --yolo' manually to see the prompt. "
                "If the UI is slow or first-run, set CAO_CURSOR_INIT_TIMEOUT (e.g. 300). "
                "Server log has the last terminal output."
            )

        self._initialized = True
        return True

    def get_status(self, tail_lines: Optional[int] = None) -> TerminalStatus:
        """Get Cursor CLI status by analyzing terminal output."""
        output = tmux_client.get_history(
            self.session_name, self.window_name, tail_lines=tail_lines or IDLE_PROMPT_TAIL_LINES
        )
        if not output:
            return TerminalStatus.ERROR

        clean = re.sub(ANSI_CODE_PATTERN, "", output)
        lines = clean.strip().splitlines()
        tail = lines[-IDLE_PROMPT_TAIL_LINES:] if len(lines) >= IDLE_PROMPT_TAIL_LINES else lines

        if re.search(WAITING_USER_ANSWER_PATTERN, clean, re.IGNORECASE):
            return TerminalStatus.WAITING_USER_ANSWER

        if re.search(ERROR_PATTERN, clean, re.MULTILINE):
            return TerminalStatus.ERROR

        # Cursor Agent main UI ready for input (check before PROCESSING so "(Thinking)" in model name doesn't block)
        if re.search(CURSOR_MAIN_READY_PATTERN, clean):
            return TerminalStatus.IDLE

        if re.search(PROCESSING_PATTERN, clean, re.IGNORECASE) or re.search(SPINNER_PATTERN, clean):
            return TerminalStatus.PROCESSING

        has_idle = any(re.search(IDLE_PROMPT_PATTERN, line) for line in tail)
        if not has_idle:
            return TerminalStatus.PROCESSING

        has_response = bool(re.search(RESPONSE_MARKER_PATTERN, clean, re.MULTILINE))
        if has_idle and has_response:
            return TerminalStatus.COMPLETED
        return TerminalStatus.IDLE

    def get_idle_pattern_for_log(self) -> str:
        """Return pattern used for quick IDLE detection in log files."""
        return IDLE_PROMPT_PATTERN_LOG

    def extract_last_message_from_script(self, script_output: str) -> str:
        """Extract the last assistant message from Cursor CLI output.

        Uses last block of content before an idle prompt or end of output.
        Cursor CLI exact format is not documented; this is a best-effort extraction.
        """
        clean = re.sub(ANSI_CODE_PATTERN, "", script_output)
        lines = clean.splitlines()

        # Find last line that looks like start of response (bullet or "Assistant:")
        start_idx = None
        for i, line in enumerate(lines):
            if re.search(RESPONSE_MARKER_PATTERN, line):
                start_idx = i

        if start_idx is None:
            # No clear marker: take last non-empty block before idle prompt
            end_idx = len(lines)
            for i in range(len(lines) - 1, -1, -1):
                if re.search(IDLE_PROMPT_PATTERN, lines[i]):
                    end_idx = i
                    break
            block = [ln.strip() for ln in lines[:end_idx] if ln.strip()]
            if not block:
                raise ValueError("No Cursor CLI response found in script output")
            return "\n".join(block[-30:]).strip()

        # Find end: next idle prompt or end
        end_idx = len(lines)
        for i in range(start_idx + 1, len(lines)):
            if re.search(IDLE_PROMPT_PATTERN, lines[i]):
                end_idx = i
                break

        result_lines = []
        for i in range(start_idx, end_idx):
            line = lines[i].strip()
            if not line or re.search(WAITING_USER_ANSWER_PATTERN, line, re.IGNORECASE):
                continue
            result_lines.append(line)

        if not result_lines:
            raise ValueError("No Cursor CLI response content found after marker")
        return "\n".join(result_lines).strip()

    def exit_cli(self) -> str:
        """Exit Cursor CLI via Ctrl+D (docs say double-press; we send once)."""
        return "C-d"

    def cleanup(self) -> None:
        """Clean up provider state."""
        self._initialized = False

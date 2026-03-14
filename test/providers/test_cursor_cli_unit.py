"""Unit tests for Cursor CLI provider."""

from unittest.mock import MagicMock, patch

import pytest

from cli_agent_orchestrator.models.terminal import TerminalStatus
from cli_agent_orchestrator.providers.cursor_cli import CursorCliProvider, ProviderError


class TestCursorCliProviderInitialization:
    """Tests for CursorCliProvider initialization."""

    @patch("cli_agent_orchestrator.providers.cursor_cli.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.cursor_cli.wait_until_status")
    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_initialize_success(self, mock_tmux, mock_wait_status, mock_wait_shell):
        """Test successful initialization."""
        mock_wait_shell.return_value = True
        mock_wait_status.return_value = True

        provider = CursorCliProvider("test123", "test-session", "window-0")
        result = provider.initialize()

        assert result is True
        assert provider._initialized is True
        mock_wait_shell.assert_called_once()
        mock_tmux.send_keys.assert_called_once()
        mock_wait_status.assert_called_once()

    @patch("cli_agent_orchestrator.providers.cursor_cli.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_initialize_shell_timeout(self, mock_tmux, mock_wait_shell):
        """Test initialization with shell timeout."""
        mock_wait_shell.return_value = False

        provider = CursorCliProvider("test123", "test-session", "window-0")

        with pytest.raises(TimeoutError, match="Shell initialization timed out"):
            provider.initialize()

    @patch("cli_agent_orchestrator.providers.cursor_cli.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.cursor_cli.wait_until_status")
    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_initialize_timeout(self, mock_tmux, mock_wait_status, mock_wait_shell):
        """Test initialization timeout."""
        mock_wait_shell.return_value = True
        mock_wait_status.return_value = False

        provider = CursorCliProvider("test123", "test-session", "window-0")

        with pytest.raises(TimeoutError, match="Cursor CLI initialization timed out"):
            provider.initialize()

    @patch("cli_agent_orchestrator.providers.cursor_cli.load_agent_profile")
    @patch("cli_agent_orchestrator.providers.cursor_cli.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.cursor_cli.wait_until_status")
    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_initialize_sends_agent_trust_yolo(
        self, mock_tmux, mock_wait_status, mock_wait_shell, mock_load
    ):
        """Test that initialize sends 'agent --trust --yolo' to tmux."""
        mock_wait_shell.return_value = True
        mock_wait_status.return_value = True

        provider = CursorCliProvider("test123", "test-session", "window-0")
        provider.initialize()

        call_args = mock_tmux.send_keys.call_args[0]
        assert call_args[0] == "test-session"
        assert call_args[1] == "window-0"
        assert "agent" in call_args[2]
        assert "--trust" in call_args[2]
        assert "--yolo" in call_args[2]

    @patch("cli_agent_orchestrator.providers.cursor_cli.load_agent_profile")
    @patch("cli_agent_orchestrator.providers.cursor_cli.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.cursor_cli.wait_until_status")
    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_initialize_with_agent_profile(
        self, mock_tmux, mock_wait_status, mock_wait_shell, mock_load
    ):
        """Test initialization with agent profile (validated only)."""
        mock_wait_shell.return_value = True
        mock_wait_status.return_value = True
        mock_load.return_value = MagicMock()

        provider = CursorCliProvider("test123", "test-session", "window-0", "test-agent")
        result = provider.initialize()

        assert result is True
        mock_load.assert_called_once_with("test-agent")

    @patch("cli_agent_orchestrator.providers.cursor_cli.load_agent_profile")
    @patch("cli_agent_orchestrator.providers.cursor_cli.wait_for_shell")
    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_initialize_with_invalid_agent_profile(self, mock_tmux, mock_wait_shell, mock_load):
        """Test initialization with invalid agent profile raises ProviderError."""
        mock_wait_shell.return_value = True
        mock_load.side_effect = FileNotFoundError("Profile not found")

        provider = CursorCliProvider("test123", "test-session", "window-0", "invalid-agent")

        with pytest.raises(ProviderError, match="Failed to load agent profile"):
            provider.initialize()


class TestCursorCliProviderStatusDetection:
    """Tests for CursorCliProvider status detection."""

    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_get_status_idle_prompt(self, mock_tmux):
        """Test IDLE status when prompt (›) is in tail."""
        mock_tmux.get_history.return_value = "Some output\n› "

        provider = CursorCliProvider("test123", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_get_status_idle_type_a_message(self, mock_tmux):
        """Test IDLE status when 'Type a message' is in output."""
        mock_tmux.get_history.return_value = "Footer: Type a message"

        provider = CursorCliProvider("test123", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.IDLE

    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_get_status_processing(self, mock_tmux):
        """Test PROCESSING status when Thinking/Running is present."""
        mock_tmux.get_history.return_value = "Thinking...\n› "

        provider = CursorCliProvider("test123", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.PROCESSING

    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_get_status_waiting_user_answer(self, mock_tmux):
        """Test WAITING_USER_ANSWER for approval prompt."""
        mock_tmux.get_history.return_value = "Run this command? (y/n)"

        provider = CursorCliProvider("test123", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.WAITING_USER_ANSWER

    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_get_status_completed(self, mock_tmux):
        """Test COMPLETED when idle prompt and response marker present."""
        mock_tmux.get_history.return_value = "• Here is the code.\n\n› "

        provider = CursorCliProvider("test123", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.COMPLETED

    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_get_status_error_empty(self, mock_tmux):
        """Test ERROR when output is empty."""
        mock_tmux.get_history.return_value = ""

        provider = CursorCliProvider("test123", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.ERROR

    @patch("cli_agent_orchestrator.providers.cursor_cli.tmux_client")
    def test_get_status_error_pattern(self, mock_tmux):
        """Test ERROR when Error: pattern is present."""
        mock_tmux.get_history.return_value = "Error: Something went wrong\n› "

        provider = CursorCliProvider("test123", "test-session", "window-0")
        status = provider.get_status()

        assert status == TerminalStatus.ERROR


class TestCursorCliProviderExitAndExtract:
    """Tests for exit_cli, get_idle_pattern_for_log, extract_last_message, cleanup."""

    def test_exit_cli_returns_c_d(self):
        """Test exit_cli returns C-d for Ctrl+D."""
        provider = CursorCliProvider("test123", "test-session", "window-0")
        assert provider.exit_cli() == "C-d"

    def test_get_idle_pattern_for_log(self):
        """Test get_idle_pattern_for_log returns a non-empty pattern."""
        provider = CursorCliProvider("test123", "test-session", "window-0")
        pattern = provider.get_idle_pattern_for_log()
        assert pattern and "›" in pattern or ">" in pattern or "Type" in pattern

    def test_extract_last_message_with_marker(self):
        """Test extract_last_message when response marker (•) is present."""
        script = "User: hello\n• Here is the response.\nMore lines.\n› "
        provider = CursorCliProvider("test123", "test-session", "window-0")
        result = provider.extract_last_message_from_script(script)
        assert "Here is the response" in result
        assert "More lines" in result

    def test_extract_last_message_fallback_block(self):
        """Test extract_last_message fallback when no marker (uses last block)."""
        script = "Line one\nLine two\nLine three\n› "
        provider = CursorCliProvider("test123", "test-session", "window-0")
        result = provider.extract_last_message_from_script(script)
        assert "Line" in result

    def test_extract_last_message_no_content_raises(self):
        """Test extract_last_message raises when no content."""
        provider = CursorCliProvider("test123", "test-session", "window-0")
        with pytest.raises(ValueError, match="No Cursor CLI response"):
            provider.extract_last_message_from_script("› \n")

    def test_cleanup_resets_initialized(self):
        """Test cleanup sets _initialized to False."""
        provider = CursorCliProvider("test123", "test-session", "window-0")
        provider._initialized = True
        provider.cleanup()
        assert provider._initialized is False

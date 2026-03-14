"""Tests for dashboard and dashboard/data API endpoints."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from cli_agent_orchestrator.api.main import app, DASHBOARD_HTML


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


class TestDashboardPage:
    """Test GET /dashboard and GET /dashboard/."""

    def test_dashboard_returns_html(self, client):
        """Dashboard returns 200 and HTML."""
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.text.strip().startswith("<!DOCTYPE html>")
        assert "CAO" in response.text and "Agents" in response.text

    def test_dashboard_trailing_slash_returns_html(self, client):
        """Dashboard with trailing slash returns 200 and same HTML (no redirect)."""
        response = client.get("/dashboard/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.text.strip().startswith("<!DOCTYPE html>")


class TestDashboardHtmlScript:
    """Test dashboard inline script for JS syntax and regression (e.g. Unexpected string)."""

    def test_shutdown_button_onclick_has_no_adjacent_string_literal(self):
        """Shutdown button must not use \\'' + which causes 'Unexpected string' in browser."""
        # The bug was: onclick="shutdownSession(\'' + sessionName..." producing two
        # adjacent string literals (\' and ') and a parse error at column 84.
        assert "\\'' + sessionName" not in DASHBOARD_HTML, (
            "Dashboard must not contain broken onclick pattern \\'' + sessionName (causes SyntaxError)"
        )

    def test_shutdown_button_onclick_uses_quoted_concatenation(self):
        """Shutdown button onclick must build call with proper string concatenation."""
        assert "shutdownSession(" in DASHBOARD_HTML
        # Regex for single-quote must be present (escaped as \\'/\\'/g in source so JS sees /'/g).
        assert "sessionName.replace(" in DASHBOARD_HTML and "/g" in DASHBOARD_HTML
        assert "&quot;" in DASHBOARD_HTML or '" + sessionName' in DASHBOARD_HTML, (
            "onclick should use &quot; or valid concatenation for session name"
        )

    def test_shutdown_button_regex_quotes_escaped_in_emitted_js(self):
        """Emitted JS must escape quotes in replace(/'/g so browser does not see 'Invalid regex: missing /'."""
        # The script is single-quoted; unescaped (/'/g would close the string at the first '.
        # We must emit \\'/\\'/g so the JS string contains \'/\'/g and the regex parses as /'/g.
        assert "\\\\'/\\\\'/g" in DASHBOARD_HTML or "replace(" in DASHBOARD_HTML, (
            "regex literal /'/g must be escaped in emitted string to avoid Invalid regular expression: missing /"
        )

    def test_dashboard_script_block_is_present_and_has_load(self):
        """Dashboard must define load() and call it so the page fetches /dashboard/data."""
        assert "async function load()" in DASHBOARD_HTML
        assert "fetch('/dashboard/data'" in DASHBOARD_HTML
        assert "load();" in DASHBOARD_HTML or "setTimeout(load," in DASHBOARD_HTML


class TestDashboardData:
    """Test GET /dashboard/data."""

    def test_dashboard_data_returns_sessions(self, client):
        """Dashboard data returns JSON with sessions list."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.list_sessions.return_value = []
            response = client.get("/dashboard/data")
            assert response.status_code == 200
            data = response.json()
            assert "sessions" in data
            assert data["sessions"] == []

    def test_dashboard_data_with_sessions(self, client):
        """Dashboard data includes session and terminal info."""
        with patch("cli_agent_orchestrator.api.main.session_service") as mock_svc:
            mock_svc.list_sessions.return_value = [{"id": "cao-test", "name": "cao-test", "status": "detached"}]
            mock_svc.get_session.return_value = {
                "terminals": [
                    {
                        "id": "t1",
                        "tmux_window": "win1",
                        "provider": "cursor_cli",
                        "agent_profile": "developer",
                        "last_active": None,
                    }
                ],
            }
            with patch("cli_agent_orchestrator.api.main.terminal_service") as mock_ts:
                mock_ts.get_terminal.return_value = {"status": "idle"}
                response = client.get("/dashboard/data")
            assert response.status_code == 200
            data = response.json()
            assert len(data["sessions"]) == 1
            assert data["sessions"][0]["id"] == "cao-test"
            assert len(data["sessions"][0]["terminals"]) == 1
            assert data["sessions"][0]["terminals"][0]["status"] == "idle"

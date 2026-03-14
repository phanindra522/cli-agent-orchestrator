"""Single FastAPI entry point for all HTTP routes."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Path, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from watchdog.observers.polling import PollingObserver

from cli_agent_orchestrator.clients.database import (
    create_inbox_message,
    get_inbox_messages,
    init_db,
)
from cli_agent_orchestrator.constants import (
    INBOX_POLLING_INTERVAL,
    LOG_DIR,
    SERVER_HOST,
    SERVER_PORT,
    SERVER_VERSION,
    TERMINAL_LOG_DIR,
)
from cli_agent_orchestrator.models.inbox import MessageStatus
from cli_agent_orchestrator.models.terminal import Terminal, TerminalId
from cli_agent_orchestrator.providers.manager import provider_manager
from cli_agent_orchestrator.services import (
    flow_service,
    inbox_service,
    session_service,
    terminal_service,
)
from cli_agent_orchestrator.providers.cursor_cli import ProviderError as CursorProviderError
from cli_agent_orchestrator.services.cleanup_service import cleanup_old_data
from cli_agent_orchestrator.services.inbox_service import LogFileHandler
from cli_agent_orchestrator.services.terminal_service import OutputMode
from cli_agent_orchestrator.utils.agent_profiles import resolve_provider
from cli_agent_orchestrator.utils.logging import setup_logging
from cli_agent_orchestrator.utils.terminal import generate_session_name

logger = logging.getLogger(__name__)


async def flow_daemon():
    """Background task to check and execute flows."""
    logger.info("Flow daemon started")
    while True:
        try:
            flows = flow_service.get_flows_to_run()
            for flow in flows:
                try:
                    executed = flow_service.execute_flow(flow.name)
                    if executed:
                        logger.info(f"Flow '{flow.name}' executed successfully")
                    else:
                        logger.info(f"Flow '{flow.name}' skipped (execute=false)")
                except Exception as e:
                    logger.error(f"Flow '{flow.name}' failed: {e}")
        except Exception as e:
            logger.error(f"Flow daemon error: {e}")

        await asyncio.sleep(60)


# Response Models
class TerminalOutputResponse(BaseModel):
    output: str
    mode: str


class WorkingDirectoryResponse(BaseModel):
    """Response model for terminal working directory."""

    working_directory: Optional[str] = Field(
        description="Current working directory of the terminal, or None if unavailable"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    logger.info("Starting CLI Agent Orchestrator server...")
    setup_logging()
    init_db()

    # Run cleanup in background
    asyncio.create_task(asyncio.to_thread(cleanup_old_data))

    # Start flow daemon as background task
    daemon_task = asyncio.create_task(flow_daemon())

    # Start inbox watcher
    inbox_observer = PollingObserver(timeout=INBOX_POLLING_INTERVAL)
    inbox_observer.schedule(LogFileHandler(), str(TERMINAL_LOG_DIR), recursive=False)
    inbox_observer.start()
    logger.info("Inbox watcher started (PollingObserver)")

    yield

    # Stop inbox observer
    inbox_observer.stop()
    inbox_observer.join()
    logger.info("Inbox watcher stopped")

    # Cancel daemon on shutdown
    daemon_task.cancel()
    try:
        await daemon_task
    except asyncio.CancelledError:
        pass

    logger.info("Shutting down CLI Agent Orchestrator server...")


app = FastAPI(
    title="CLI Agent Orchestrator",
    description="Simplified CLI Agent Orchestrator API",
    version=SERVER_VERSION,
    lifespan=lifespan,
)


def _root_response():
    return {
        "service": "CLI Agent Orchestrator",
        "version": SERVER_VERSION,
        "dashboard": "/dashboard",
        "docs": "/docs",
        "health": "/health",
        "sessions": "/sessions",
    }


@app.get("/", include_in_schema=True)
async def root():
    """Root route so browsers get a response instead of 404."""
    return _root_response()


@app.get("/dashboard/data")
async def dashboard_data() -> Dict:
    """Return all sessions and their terminals (with live status when available)."""
    try:
        sessions_raw = session_service.list_sessions()
        sessions_out = []
        for s in sessions_raw:
            sid = s.get("id") or s.get("name", "")
            try:
                detail = session_service.get_session(sid)
                terminals = detail.get("terminals") or []
                terminals_with_status = []
                for t in terminals:
                    tid = t.get("id")
                    row = {
                        "id": tid,
                        "name": t.get("tmux_window") or t.get("name") or tid,
                        "provider": t.get("provider", ""),
                        "agent_profile": t.get("agent_profile") or "",
                        "last_active": str(t.get("last_active")) if t.get("last_active") else None,
                        "status": None,
                    }
                    if tid:
                        try:
                            full = terminal_service.get_terminal(tid)
                            row["status"] = full.get("status")
                        except Exception:
                            pass
                    terminals_with_status.append(row)
                sessions_out.append(
                    {
                        "id": sid,
                        "name": sid,
                        "session_status": s.get("status", "detached"),
                        "terminals": terminals_with_status,
                    }
                )
            except Exception as e:
                logger.debug("Skip session %s: %s", sid, e)
                sessions_out.append(
                    {"id": sid, "name": sid, "session_status": s.get("status"), "terminals": []}
                )
        return {"sessions": sessions_out}
    except Exception as e:
        logger.exception("Dashboard data failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CAO · Agents Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; }
    :root {
      --bg: #0c0d0f;
      --surface: #141619;
      --surface-hover: #1a1c20;
      --border: #25282e;
      --text: #e8eaed;
      --text-muted: #8b909a;
      --accent: #3b82f6;
      --accent-hover: #2563eb;
      --success: #22c55e;
      --warning: #eab308;
      --error: #ef4444;
      --processing: #0ea5e9;
    }
    body {
      font-family: 'Outfit', system-ui, sans-serif;
      margin: 0;
      padding: 0;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
    }
    .layout {
      max-width: 1200px;
      margin: 0 auto;
      padding: 1.5rem;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 1rem;
      margin-bottom: 1.5rem;
      padding-bottom: 1rem;
      border-bottom: 1px solid var(--border);
    }
    .logo {
      font-size: 1.35rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      color: var(--text);
    }
    .logo span { color: var(--accent); }
    .nav {
      display: flex;
      align-items: center;
      gap: 1rem;
      font-size: 0.875rem;
    }
    .nav a {
      color: var(--text-muted);
      text-decoration: none;
    }
    .nav a:hover { color: var(--accent); }
    .stats {
      display: flex;
      gap: 1.5rem;
      margin-bottom: 1.5rem;
      flex-wrap: wrap;
    }
    .stat {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 0.75rem 1.25rem;
      font-family: 'JetBrains Mono', monospace;
    }
    .stat-num { font-size: 1.5rem; font-weight: 600; color: var(--accent); }
    .stat-label { font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
    .controls {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    .btn {
      font-family: inherit;
      font-size: 0.875rem;
      font-weight: 500;
      border: none;
      border-radius: 8px;
      padding: 0.5rem 1rem;
      cursor: pointer;
      transition: background 0.15s;
    }
    .btn-primary { background: var(--accent); color: white; }
    .btn-primary:hover { background: var(--accent-hover); }
    .btn-ghost { background: var(--surface); color: var(--text-muted); border: 1px solid var(--border); }
    .btn-ghost:hover { background: var(--surface-hover); color: var(--text); }
    .btn-danger { background: rgba(239,68,68,0.15); color: var(--error); }
    .btn-danger:hover { background: rgba(239,68,68,0.25); }
    .btn-sm { padding: 0.35rem 0.65rem; font-size: 0.8rem; }
    .refresh-dot {
      display: inline-block;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--success);
      margin-right: 0.35rem;
      animation: pulse 2s ease-in-out infinite;
    }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
    .sessions {
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }
    .session-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
    }
    .session-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 0.75rem;
      padding: 1rem 1.25rem;
      background: var(--surface-hover);
      border-bottom: 1px solid var(--border);
    }
    .session-name {
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.95rem;
      font-weight: 600;
    }
    .session-actions { display: flex; align-items: center; gap: 0.5rem; }
    .badge {
      display: inline-block;
      padding: 0.2rem 0.5rem;
      border-radius: 6px;
      font-size: 0.7rem;
      font-weight: 500;
      text-transform: uppercase;
      letter-spacing: 0.03em;
    }
    .badge-active { background: rgba(34,197,94,0.2); color: #4ade80; }
    .badge-detached { background: rgba(139,144,154,0.2); color: var(--text-muted); }
    .badge-idle { background: rgba(34,197,94,0.2); color: #4ade80; }
    .badge-processing { background: rgba(14,165,233,0.2); color: #38bdf8; }
    .badge-completed { background: rgba(139,92,246,0.2); color: #a78bfa; }
    .badge-error { background: rgba(239,68,68,0.2); color: #f87171; }
    .badge-waiting { background: rgba(234,179,8,0.2); color: #facc15; }
    .agents-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 1rem;
      padding: 1.25rem;
    }
    .agent-card {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1rem;
      transition: border-color 0.15s;
    }
    .agent-card:hover { border-color: var(--accent); }
    .agent-name {
      font-family: 'JetBrains Mono', monospace;
      font-weight: 600;
      font-size: 0.9rem;
      margin-bottom: 0.35rem;
    }
    .agent-meta {
      font-size: 0.8rem;
      color: var(--text-muted);
      margin-bottom: 0.5rem;
    }
    .agent-meta span { margin-right: 0.5rem; }
    .agent-status-row { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.5rem; }
    .agent-last { font-size: 0.75rem; color: var(--text-muted); }
    .empty-state {
      text-align: center;
      padding: 3rem 1.5rem;
      background: var(--surface);
      border: 1px dashed var(--border);
      border-radius: 12px;
      color: var(--text-muted);
    }
    .empty-state code {
      display: block;
      margin-top: 1rem;
      padding: 0.75rem 1rem;
      background: var(--bg);
      border-radius: 8px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 0.85rem;
      color: var(--text);
      text-align: left;
      overflow-x: auto;
    }
    .error-state { color: var(--error); }
    .attach-hint {
      font-size: 0.75rem;
      color: var(--text-muted);
    }
    .attach-hint code { font-family: 'JetBrains Mono', monospace; }
    .troubleshoot-box {
      margin-top: 1.5rem;
      padding: 1rem 1.25rem;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 10px;
      text-align: left;
      font-size: 0.875rem;
    }
    .troubleshoot-box p { margin: 0.5rem 0; }
    .troubleshoot-box p:first-of-type { margin-top: 0; }
    .troubleshoot-box a { color: var(--accent); }
    .troubleshoot-box a:hover { text-decoration: underline; }
  </style>
</head>
<body>
  <div class="layout">
    <header>
      <h1 class="logo">CAO <span>·</span> Agents</h1>
      <nav class="nav">
        <a href="/">API</a>
        <a href="/docs">Docs</a>
        <a href="/health">Health</a>
      </nav>
    </header>
    <div class="controls">
      <span class="refresh-dot" title="Auto-refresh every 5s"></span>
      <span style="font-size:0.8rem;color:var(--text-muted);">Auto-refresh 5s</span>
      <button class="btn btn-primary" onclick="load()">Refresh now</button>
    </div>
    <div id="stats" class="stats" style="display:none;"></div>
    <div id="content">
      <p class="empty-state">Loading…</p>
    </div>
  </div>
  <script>
    const REFRESH_MS = 5000;
    let refreshTimer = null;

    function statusClass(s) {
      if (!s) return '';
      const map = { idle: 'idle', processing: 'processing', completed: 'completed', error: 'error', waiting_user_answer: 'waiting' };
      return 'badge-' + (map[s] || 'idle');
    }
    function sessionClass(s) {
      return s === 'active' ? 'badge-active' : 'badge-detached';
    }
    function escapeHtml(str) {
      if (!str) return '';
      const div = document.createElement('div');
      div.textContent = str;
      return div.innerHTML;
    }
    function formatLastActive(ts) {
      if (!ts) return '—';
      try {
        const d = new Date(ts);
        const now = new Date();
        const diff = (now - d) / 1000;
        if (diff < 60) return 'just now';
        if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
        if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
        return d.toLocaleDateString();
      } catch (_) { return ts; }
    }
    async function shutdownSession(sessionName) {
      if (!confirm('Shutdown session "' + sessionName + '" and all its agents?')) return;
      try {
        const r = await fetch('/sessions/' + encodeURIComponent(sessionName), { method: 'DELETE' });
        if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
        load();
      } catch (e) {
        alert('Failed: ' + e.message);
      }
    }
    async function load() {
      const contentEl = document.getElementById('content');
      const statsEl = document.getElementById('stats');
      contentEl.innerHTML = '<p class="empty-state">Loading…</p>';
      statsEl.style.display = 'none';
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(function() { controller.abort(); }, 15000);
        const r = await fetch('/dashboard/data', { signal: controller.signal, cache: 'no-store' });
        clearTimeout(timeoutId);
        if (!r.ok) throw new Error(r.statusText);
        const data = await r.json();
        const sessions = data.sessions || [];
        const totalAgents = sessions.reduce((n, s) => n + (s.terminals || []).length, 0);

        if (sessions.length === 0) {
          let emptyHtml = '<div class="empty-state"><p>No agents running.</p><p>Start a session from your terminal (in the same environment as this server):</p><code>cao launch --agents code_supervisor --provider cursor_cli --yolo</code>';
          try {
            const dr = await fetch('/debug/sessions', { cache: 'no-store' });
            if (dr.ok) {
              const debug = await dr.json();
              const tmuxTotal = debug.tmux_sessions_total ?? '—';
              const caoCount = debug.cao_sessions_count ?? '—';
              const prefix = debug.session_prefix || 'cao-';
              emptyHtml += '<div class="troubleshoot-box"><p><strong>Troubleshooting</strong></p><p>This server sees <strong>' + tmuxTotal + '</strong> tmux session(s) and <strong>' + caoCount + '</strong> CAO session(s) (prefix <code>' + escapeHtml(prefix) + '</code>).</p>';
              if (tmuxTotal === 0) {
                emptyHtml += '<p>If you launched agents in <strong>WSL</strong>, start the CAO server inside WSL too so it sees the same tmux and database:<br><code>bash scripts/start-server-wsl.sh</code> or <code>cao-server</code></p>';
              }
              emptyHtml += '<p><a href="/debug/sessions" target="_blank" rel="noopener">View full debug (JSON)</a></p></div>';
            }
          } catch (_) {}
          emptyHtml += '</div>';
          contentEl.innerHTML = emptyHtml;
          if (refreshTimer) clearTimeout(refreshTimer);
          refreshTimer = setTimeout(load, REFRESH_MS);
          return;
        }

        statsEl.innerHTML = '<div class="stat"><div class="stat-num">' + sessions.length + '</div><div class="stat-label">Sessions</div></div><div class="stat"><div class="stat-num">' + totalAgents + '</div><div class="stat-label">Agents</div></div>';
        statsEl.style.display = 'flex';

        let html = '<div class="sessions">';
        for (const s of sessions) {
          const terms = s.terminals || [];
          const sessionName = escapeHtml(s.name || s.id);
          html += '<div class="session-card"><div class="session-header">';
          html += '<span class="session-name">' + sessionName + '</span>';
          html += '<span class="badge ' + sessionClass(s.session_status) + '">' + (s.session_status || 'detached') + '</span>';
          html += '<div class="session-actions">';
          html += '<span class="attach-hint">Attach: <code>tmux attach -t ' + sessionName + '</code></span>';
          html += '<button class="btn btn-danger btn-sm" onclick="shutdownSession(\\'' + sessionName.replace(/'/g, \"\\\\'\" ) + '\\')">Shutdown</button>';
          html += '</div></div>';
          html += '<div class="agents-grid">';
          for (const t of terms) {
            const status = t.status || null;
            const statusLabel = status || '—';
            const statusBadge = status ? '<span class="badge ' + statusClass(status) + '">' + escapeHtml(status) + '</span>' : '<span class="agent-last">—</span>';
            html += '<div class="agent-card">';
            html += '<div class="agent-name">' + escapeHtml(t.name || t.id) + '</div>';
            html += '<div class="agent-meta"><span>Provider: ' + escapeHtml(t.provider || '') + '</span><span>Profile: ' + escapeHtml(t.agent_profile || '') + '</span></div>';
            html += '<div class="agent-status-row">' + statusBadge + '<span class="agent-last">' + formatLastActive(t.last_active) + '</span></div>';
            html += '</div>';
          }
          html += '</div></div>';
        }
        html += '</div>';
        contentEl.innerHTML = html;
      } catch (e) {
        const msg = e.name === 'AbortError' ? 'Request timed out. Is the server running? Open from the same host (e.g. WSL) or try Refresh now.' : escapeHtml(e.message);
        contentEl.innerHTML = '<p class="empty-state error-state">Failed to load: ' + msg + '</p>';
      }
      if (refreshTimer) clearTimeout(refreshTimer);
      refreshTimer = setTimeout(load, REFRESH_MS);
    }
    load();
  </script>
</body>
</html>
"""


@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/dashboard/", response_class=HTMLResponse)
async def dashboard():
    """Serve the sessions and agents dashboard."""
    return DASHBOARD_HTML


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "cli-agent-orchestrator"}


@app.post("/sessions", response_model=Terminal, status_code=status.HTTP_201_CREATED)
async def create_session(
    provider: str,
    agent_profile: str,
    session_name: Optional[str] = None,
    working_directory: Optional[str] = None,
) -> Terminal:
    """Create a new session with exactly one terminal."""
    try:
        result = terminal_service.create_terminal(
            provider=provider,
            agent_profile=agent_profile,
            session_name=session_name,
            new_session=True,
            working_directory=working_directory,
        )
        return result

    except ValueError as e:
        logger.warning("Create session validation error: %s", e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except CursorProviderError as e:
        logger.warning("Create session provider error: %s", e)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except TimeoutError as e:
        logger.warning("Create session timeout: %s", e)
        log_hint = f" Check server log: {LOG_DIR}"
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(e) + log_hint,
        )
    except Exception as e:
        logger.exception("Failed to create session: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create session: {str(e)}",
        )


@app.get("/sessions")
async def list_sessions() -> List[Dict]:
    try:
        return session_service.list_sessions()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list sessions: {str(e)}",
        )


@app.get("/debug/sessions")
async def debug_sessions() -> Dict:
    """Return detailed session and tmux state for debugging (why no session visible)."""
    try:
        return session_service.list_sessions_debug()
    except Exception as e:
        logger.exception("debug_sessions failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@app.get("/sessions/{session_name}")
async def get_session(session_name: str) -> Dict:
    try:
        return session_service.get_session(session_name)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get session: {str(e)}",
        )


@app.delete("/sessions/{session_name}")
async def delete_session(session_name: str) -> Dict:
    try:
        success = session_service.delete_session(session_name)
        return {"success": success}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete session: {str(e)}",
        )


@app.post(
    "/sessions/{session_name}/terminals",
    response_model=Terminal,
    status_code=status.HTTP_201_CREATED,
)
async def create_terminal_in_session(
    session_name: str,
    provider: str,
    agent_profile: str,
    working_directory: Optional[str] = None,
) -> Terminal:
    """Create additional terminal in existing session."""
    try:
        resolved_provider = resolve_provider(agent_profile, fallback_provider=provider)

        result = terminal_service.create_terminal(
            provider=resolved_provider,
            agent_profile=agent_profile,
            session_name=session_name,
            new_session=False,
            working_directory=working_directory,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create terminal: {str(e)}",
        )


@app.get("/sessions/{session_name}/terminals")
async def list_terminals_in_session(session_name: str) -> List[Dict]:
    """List all terminals in a session."""
    try:
        from cli_agent_orchestrator.clients.database import list_terminals_by_session

        return list_terminals_by_session(session_name)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list terminals: {str(e)}",
        )


@app.get("/terminals/{terminal_id}", response_model=Terminal)
async def get_terminal(terminal_id: TerminalId) -> Terminal:
    try:
        terminal = terminal_service.get_terminal(terminal_id)
        return Terminal(**terminal)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get terminal: {str(e)}",
        )


@app.get("/terminals/{terminal_id}/working-directory", response_model=WorkingDirectoryResponse)
async def get_terminal_working_directory(terminal_id: TerminalId) -> WorkingDirectoryResponse:
    """Get the current working directory of a terminal's pane."""
    try:
        working_directory = terminal_service.get_working_directory(terminal_id)
        return WorkingDirectoryResponse(working_directory=working_directory)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get working directory: {str(e)}",
        )


@app.post("/terminals/{terminal_id}/input")
async def send_terminal_input(terminal_id: TerminalId, message: str) -> Dict:
    try:
        success = terminal_service.send_input(terminal_id, message)
        return {"success": success}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to send input: {str(e)}",
        )


@app.get("/terminals/{terminal_id}/output", response_model=TerminalOutputResponse)
async def get_terminal_output(
    terminal_id: TerminalId, mode: OutputMode = OutputMode.FULL
) -> TerminalOutputResponse:
    try:
        output = terminal_service.get_output(terminal_id, mode)
        return TerminalOutputResponse(output=output, mode=mode)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get output: {str(e)}",
        )


@app.post("/terminals/{terminal_id}/exit")
async def exit_terminal(terminal_id: TerminalId) -> Dict:
    """Send provider-specific exit command to terminal."""
    try:
        provider = provider_manager.get_provider(terminal_id)
        if provider is None:
            raise ValueError(f"Provider not found for terminal {terminal_id}")
        exit_command = provider.exit_cli()
        # Some providers use tmux key sequences (e.g., "C-d" for Ctrl+D) instead
        # of text commands (e.g., "/exit"). Key sequences must be sent via
        # send_special_key() to be interpreted by tmux, not as literal text.
        if exit_command.startswith(("C-", "M-")):
            terminal_service.send_special_key(terminal_id, exit_command)
        else:
            terminal_service.send_input(terminal_id, exit_command)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to exit terminal: {str(e)}",
        )


@app.delete("/terminals/{terminal_id}")
async def delete_terminal(terminal_id: TerminalId) -> Dict:
    """Delete a terminal."""
    try:
        success = terminal_service.delete_terminal(terminal_id)
        return {"success": success}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete terminal: {str(e)}",
        )


@app.post("/terminals/{receiver_id}/inbox/messages")
async def create_inbox_message_endpoint(
    receiver_id: TerminalId, sender_id: str, message: str
) -> Dict:
    """Create inbox message and attempt immediate delivery."""
    try:
        inbox_msg = create_inbox_message(sender_id, receiver_id, message)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create inbox message: {str(e)}",
        )

    # Best-effort immediate delivery. If the receiver terminal is idle, the
    # message is delivered now; otherwise the watchdog will deliver it when
    # the terminal becomes idle. Delivery failures must not cause the API
    # to report an error — the message was already persisted above.
    try:
        inbox_service.check_and_send_pending_messages(receiver_id)
    except Exception as e:
        logger.warning(f"Immediate delivery attempt failed for {receiver_id}: {e}")

    return {
        "success": True,
        "message_id": inbox_msg.id,
        "sender_id": inbox_msg.sender_id,
        "receiver_id": inbox_msg.receiver_id,
        "created_at": inbox_msg.created_at.isoformat(),
    }


@app.get("/terminals/{terminal_id}/inbox/messages")
async def get_inbox_messages_endpoint(
    terminal_id: TerminalId,
    limit: int = Query(default=10, le=100, description="Maximum number of messages to retrieve"),
    status_param: Optional[str] = Query(
        default=None, alias="status", description="Filter by message status"
    ),
) -> List[Dict]:
    """Get inbox messages for a terminal.

    Args:
        terminal_id: Terminal ID to get messages for
        limit: Maximum number of messages to return (default: 10, max: 100)
        status_param: Optional filter by message status ('pending', 'delivered', 'failed')

    Returns:
        List of inbox messages with sender_id, message, created_at, status
    """
    try:
        # Convert status filter if provided
        status_filter = None
        if status_param:
            try:
                status_filter = MessageStatus(status_param)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid status: {status_param}. Valid values: pending, delivered, failed",
                )

        # Get messages using existing database function
        messages = get_inbox_messages(terminal_id, limit=limit, status=status_filter)

        # Convert to response format
        result = []
        for msg in messages:
            result.append(
                {
                    "id": msg.id,
                    "sender_id": msg.sender_id,
                    "receiver_id": msg.receiver_id,
                    "message": msg.message,
                    "status": msg.status.value,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                }
            )

        return result

    except HTTPException:
        # Re-raise HTTPException (validation errors)
        raise
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve inbox messages: {str(e)}",
        )


def main():
    """Entry point for cao-server command."""
    import uvicorn

    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)


if __name__ == "__main__":
    main()

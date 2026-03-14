# Run CAO with Cursor CLI provider.
# Prerequisites: tmux (3.3+), Cursor Agent CLI (agent), and CAO installed.
# Start the CAO server in another terminal first: cao-server

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $ProjectRoot

# UTF-8 for cao install/launch output (avoids checkmark encoding errors)
$env:PYTHONIOENCODING = "utf-8"

function Test-Command($name) {
    $null -ne (Get-Command $name -ErrorAction SilentlyContinue)
}

Write-Host "Checking prerequisites..."

$missing = @()
if (-not (Test-Command tmux)) { $missing += "tmux (install via WSL, MSYS2, or itmux)" }
if (-not (Test-Command agent)) { $missing += "Cursor Agent CLI (agent)" }
if (-not (Test-Command cao))  { $missing += "CAO (pip install -e . or uv sync)" }

if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "Missing: $($missing -join ', ')" -ForegroundColor Yellow
    Write-Host "See docs/cursor-cli.md for install steps." -ForegroundColor Yellow
    exit 1
}

try {
    $r = Invoke-WebRequest -Uri "http://localhost:9889/sessions" -UseBasicParsing -TimeoutSec 3
} catch {
    Write-Host ""
    Write-Host "CAO server is not running. Start it in another terminal: cao-server" -ForegroundColor Yellow
    exit 1
}

Write-Host "Launching CAO with Cursor CLI (code_supervisor, --yolo)..."
& cao launch --agents code_supervisor --provider cursor_cli --yolo

#Requires -Version 5.1
<#
.SYNOPSIS
    Remote Executor MCP Server — PowerShell Deploy Script
    Works on Windows without requiring Python.

.DESCRIPTION
    Automates the full deployment: Docker check, WSL2 networking,
    data directory, encryption key, API key auth, docker compose up.

.PARAMETER NoDashboard
    Deploy MCP only; disable the web dashboard UI.

.PARAMETER Pull
    Pull the pre-built image from Docker Hub instead of building locally.

.PARAMETER Restart
    Restart the running container only.

.PARAMETER Status
    Check if the MCP server is running.

.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 -NoDashboard
    .\deploy.ps1 -Pull
    .\deploy.ps1 -Status
#>

param(
    [switch]$NoDashboard,
    [switch]$Pull,
    [switch]$Restart,
    [switch]$Status
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$DOCKER_IMAGE = "rajkob/mcp-remote-executor:latest"
$MCP_PORT     = 8765
$MCP_HOST     = "127.0.0.1"
$MCP_URL      = "http://${MCP_HOST}:${MCP_PORT}/sse"
$BASE_DIR     = Split-Path -Parent $MyInvocation.MyCommand.Definition
$DASHBOARD    = -not $NoDashboard

# ── Enable ANSI colours on Windows 10+ ───────────────────────────────────────
$kernel32 = Add-Type -MemberDefinition @"
[DllImport("kernel32.dll")]public static extern bool SetConsoleMode(IntPtr h, uint m);
[DllImport("kernel32.dll")]public static extern IntPtr GetStdHandle(int n);
"@ -Name "K32" -PassThru -ErrorAction SilentlyContinue
try { $kernel32::SetConsoleMode($kernel32::GetStdHandle(-11), 7) | Out-Null } catch {}

function ok($m)   { Write-Host "`e[92m✓`e[0m $m" }
function warn($m) { Write-Host "`e[93m⚠`e[0m  $m" }
function err($m)  { Write-Host "`e[91m✗`e[0m $m" }
function info($m) { Write-Host "`e[96m→`e[0m $m" }
function head($m) { Write-Host "`n`e[1m$m`e[0m" }

Write-Host "`n`e[1mRemote Executor MCP Server — Deployment`e[0m"
Write-Host "Platform : Windows $([System.Environment]::OSVersion.Version)"
Write-Host "Project  : $BASE_DIR"

# ── Status check ──────────────────────────────────────────────────────────────
if ($Status) {
    head "Server Status"
    try {
        $r = Invoke-WebRequest -Uri $MCP_URL -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        ok "MCP server is RUNNING on port $MCP_PORT"
    } catch {
        err "MCP server NOT reachable on port $MCP_PORT"
        Push-Location $BASE_DIR; docker compose ps; Pop-Location
    }
    exit 0
}

# ── Restart only ──────────────────────────────────────────────────────────────
if ($Restart) {
    head "Restarting container"
    Push-Location $BASE_DIR
    docker compose restart
    ok "Container restarted."
    Pop-Location
    exit 0
}

# ── Step 1: Prerequisites ─────────────────────────────────────────────────────
head "[ 1 / 5 ]  Prerequisites"

$dockerOk = $false
try {
    $r = & docker info 2>&1
    if ($LASTEXITCODE -eq 0) { ok "Docker is running."; $dockerOk = $true }
    else { err "Docker daemon not running. Start Docker Desktop and retry." }
} catch { err "Docker not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop" }

if (-not $dockerOk) { err "Deployment stopped."; exit 1 }

try {
    $v = & docker compose version 2>&1
    if ($LASTEXITCODE -eq 0) { ok "Docker Compose: $v" }
    else { err "Docker Compose plugin not found."; exit 1 }
} catch { err "Docker Compose not found."; exit 1 }

# ── WSL2 Mirrored Networking ──────────────────────────────────────────────────
head "[ 1.5 / 5 ]  WSL2 Networking"
$wslconfig = Join-Path $env:USERPROFILE ".wslconfig"
$content = if (Test-Path $wslconfig) { Get-Content $wslconfig -Raw } else { "" }

if ($content -match "(?im)^\s*networkingMode\s*=\s*mirrored") {
    ok "WSL2 mirrored networking already configured."
} else {
    warn "networkingMode=mirrored missing — applying..."
    if ($content -match "(?im)^\[wsl2\]") {
        $content = $content -replace "(?im)(\[wsl2\][^\n]*\n)", "`$1networkingMode=mirrored`n"
    } else {
        $content = $content.TrimEnd() + "`n`n[wsl2]`nnetworkingMode=mirrored`n"
    }
    Set-Content -Path $wslconfig -Value $content -Encoding utf8
    ok "Updated: $wslconfig"
    info "Shutting down WSL2..."
    & wsl --shutdown 2>&1 | Out-Null
    warn "Please RESTART Docker Desktop, then re-run this script."
    exit 0
}

# ── Step 2: Data directory + .env ─────────────────────────────────────────────
head "[ 2 / 5 ]  Initialisation"

$dataDir  = Join-Path $BASE_DIR "data"
$outputDir = Join-Path $dataDir "output"
$envFile  = Join-Path $BASE_DIR ".env"
$vmsFile  = Join-Path $dataDir "vms.yaml"
$credFile = Join-Path $dataDir "credentials"

New-Item -ItemType Directory -Force -Path $outputDir | Out-Null
ok "data/ directory ready."

if (-not (Test-Path $vmsFile) -or (Get-Item $vmsFile).Length -eq 0) {
    @"
defaults:
  user: root
  port: 22
  timeout: 30

templates:
  disk:            "df -h"
  memory:          "free -h"
  uptime:          "uptime"
  cpu:             "top -bn1 | grep 'Cpu(s)'"
  who:             "who"
  os-version:      "cat /etc/os-release"
  failed-services: "systemctl --failed"
  netstat:         "ss -tlnp"

projects: {}
"@ | Set-Content -Path $vmsFile -Encoding utf8
    ok "vms.yaml skeleton created."
} else {
    ok "vms.yaml already exists."
}

# Generate CRED_MASTER_KEY via Docker (no Python needed on host)
$envContent = if (Test-Path $envFile) { Get-Content $envFile -Raw } else { "" }
if ($envContent -match "(?m)^CRED_MASTER_KEY=.+") {
    ok "CRED_MASTER_KEY already in .env"
} else {
    info "Generating CRED_MASTER_KEY via Docker..."
    $credKey = & docker run --rm $DOCKER_IMAGE python -c `
        "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    if ($LASTEXITCODE -ne 0) {
        err "Failed to generate Fernet key via Docker."
        exit 1
    }
    Add-Content -Path $envFile -Value "CRED_MASTER_KEY=$credKey"
    ok "CRED_MASTER_KEY written to .env"
}

if (-not (Test-Path $credFile)) {
    Set-Content -Path $credFile -Value "{}" -NoNewline -Encoding Byte
    ok "credentials file initialised."
}

# ── Step 3: API Key ───────────────────────────────────────────────────────────
head "[ 3 / 5 ]  API Key Authentication"

$envContent = if (Test-Path $envFile) { Get-Content $envFile -Raw } else { "" }
$apiKey = ""

$existing = [regex]::Match($envContent, '(?m)^MCP_API_KEY=(.+)$')
if ($existing.Success -and $existing.Groups[1].Value.Trim()) {
    $apiKey = $existing.Groups[1].Value.Trim()
    ok "API key already configured (length: $($apiKey.Length))."
} else {
    Write-Host "  Enable API key authentication?"
    Write-Host "  Recommended for shared/remote deployments."
    $choice = Read-Host "  [Y] Generate key  [N] Skip (Y/n)"
    if ($choice -match "^[Nn]") {
        warn "Auth DISABLED — ensure port $MCP_PORT is firewalled."
    } else {
        # Generate cryptographically random 32-byte base64 key
        $bytes = [byte[]]::new(32)
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        $apiKey = [Convert]::ToBase64String($bytes)
        Add-Content -Path $envFile -Value "MCP_API_KEY=$apiKey"
        # Uncomment in docker-compose.yml
        $composePath = Join-Path $BASE_DIR "docker-compose.yml"
        (Get-Content $composePath -Raw) -replace '# - MCP_API_KEY=\$\{MCP_API_KEY\}', '- MCP_API_KEY=${MCP_API_KEY}' |
            Set-Content $composePath -Encoding utf8
        ok "API key written to .env"
        Write-Host "`n  `e[1mYour API key:`e[0m `e[96m$apiKey`e[0m"
        Write-Host "  `e[93mCopy this — you'll need it in your client config.`e[0m"
    }
}

# ── Dashboard toggle ──────────────────────────────────────────────────────────
$composePath = Join-Path $BASE_DIR "docker-compose.yml"
$composeContent = Get-Content $composePath -Raw
if ($DASHBOARD) {
    $composeContent = $composeContent -replace 'MCP_DASHBOARD=false', 'MCP_DASHBOARD=true'
    ok "Dashboard: ENABLED  (http://localhost:${MCP_PORT}/dashboard)"
} else {
    $composeContent = $composeContent -replace 'MCP_DASHBOARD=true', 'MCP_DASHBOARD=false'
    warn "Dashboard: DISABLED  (use without -NoDashboard to enable)"
}
Set-Content $composePath -Value $composeContent -Encoding utf8

# ── Step 4: Build / pull + start ──────────────────────────────────────────────
head "[ 4 / 5 ]  Docker Deploy"
Push-Location $BASE_DIR

if ($Pull) {
    info "Pulling $DOCKER_IMAGE ..."
    docker pull $DOCKER_IMAGE
    if ($LASTEXITCODE -ne 0) { err "docker pull failed."; Pop-Location; exit 1 }
    ok "Image pulled."
} else {
    info "Building image from source..."
    docker compose build
    if ($LASTEXITCODE -ne 0) { err "docker compose build failed."; Pop-Location; exit 1 }
    ok "Image built."
}

info "Starting container (docker compose up -d)..."
docker compose up -d
if ($LASTEXITCODE -ne 0) { err "docker compose up failed."; Pop-Location; exit 1 }
ok "Container started."
Pop-Location

# ── Step 5: Health check ──────────────────────────────────────────────────────
head "[ 5 / 5 ]  Health Check"
info "Waiting for MCP server at $MCP_URL (up to 30s)..."

$deadline = (Get-Date).AddSeconds(30)
$attempt  = 0
$up       = $false

while ((Get-Date) -lt $deadline) {
    $attempt++
    try {
        Invoke-WebRequest -Uri $MCP_URL -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop | Out-Null
        $up = $true; break
    } catch {
        if ($_.Exception.Response) { $up = $true; break }   # got HTTP response = server is up
        Write-Host "  Attempt $attempt — not ready yet..." -NoNewline
        Write-Host "`r" -NoNewline
        Start-Sleep -Seconds 2
    }
}

if (-not $up) {
    err "Server did not respond within 30s."
    err "Check logs: docker compose logs remote-executor"
    exit 1
}
ok "Server is UP — $MCP_URL"

# ── Integration info ──────────────────────────────────────────────────────────
$sep = "=" * 60
Write-Host ""
Write-Host $sep
Write-Host "`e[1m  MCP Server is ready — Integration Guide`e[0m"
Write-Host $sep

if ($apiKey) {
    Write-Host "`n`e[1mAPI Key:`e[0m  `e[96m$apiKey`e[0m  `e[93m<-- keep this safe`e[0m"
}

$headerSnippet = if ($apiKey) { ",`n        `"headers`": { `"X-MCP-Key`": `"$apiKey`" }" } else { "" }

Write-Host @"

`e[1mServer URL:`e[0m  `e[96mhttp://localhost:${MCP_PORT}/sse`e[0m
`e[1mDashboard:`e[0m   `e[96mhttp://localhost:${MCP_PORT}/dashboard`e[0m

`e[1m── VS Code (GitHub Copilot Agent) ──`e[0m
Add to User Settings JSON (Ctrl+Shift+P → Preferences: Open User Settings (JSON)):

  `e[96m"mcp": {
    "servers": {
      "remote-executor": {
        "type": "sse",
        "url": "http://localhost:${MCP_PORT}/sse"$headerSnippet
      }
    }
  }`e[0m

`e[1m── Claude Desktop ──`e[0m
Add to %APPDATA%\Claude\claude_desktop_config.json:

  `e[96m"mcpServers": {
    "remote-executor": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:${MCP_PORT}/sse"]
    }
  }`e[0m

`e[1m── Useful Docker commands ──`e[0m
  docker compose logs -f remote-executor   # live logs
  docker compose restart remote-executor   # restart
  docker compose down                      # stop
  docker compose up -d                     # start

`e[1m── Optional: AI / Ollama integration ──`e[0m
  Enables ai_analyze and ollama_status tools.
  See LOCAL_LLM_SETUP.md for setup instructions.
"@
Write-Host $sep

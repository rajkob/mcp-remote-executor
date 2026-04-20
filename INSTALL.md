# Quick Install Guide — Remote Executor MCP Server

## Prerequisites

- Windows 10/11 or Linux/macOS with **Docker Desktop** / Docker Engine installed
- VS Code with GitHub Copilot extension (or Claude Desktop / Continue.dev)
- **Python 3.9+** — only required if using `deploy.py` (not needed for `deploy.sh` / `deploy.ps1`)

---

## Deployment — Choose Your Method

| Script | Platform | Python needed? |
|---|---|---|
| `deploy.py` | Windows / Linux / macOS | ✅ Yes (3.9+) |
| `deploy.ps1` | Windows (PowerShell) | ❌ No |
| `deploy.sh` | Linux / macOS (Bash) | ❌ No |

All three scripts do the same thing: check Docker, create data dir, generate encryption key, configure API key auth, build/pull the image, and start the server.

---

## Option A — deploy.ps1 (Windows, no Python)

```powershell
cd mcp-remote-executor
.\deploy.ps1
```

**With dashboard disabled:**
```powershell
.\deploy.ps1 -NoDashboard
```

**Other options:**
```powershell
.\deploy.ps1 -Pull        # pull from Docker Hub instead of building
.\deploy.ps1 -Restart     # restart running container
.\deploy.ps1 -Status      # check if server is running
```

> **Note:** If PowerShell blocks execution, run once:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
> ```

---

## Option B — deploy.sh (Linux / macOS, no Python)

```bash
cd mcp-remote-executor
chmod +x deploy.sh
./deploy.sh
```

**With dashboard disabled:**
```bash
./deploy.sh --no-dashboard
```

**Other options:**
```bash
./deploy.sh --pull        # pull from Docker Hub instead of building
./deploy.sh --restart     # restart running container
./deploy.sh --status      # check if server is running
```

---

## Option C — deploy.py (all platforms, requires Python 3.9+)

```bash
cd mcp-remote-executor
python deploy.py
```

### Installing Python (if not installed)

**Windows:**
```powershell
winget install Python.Python.3.12
```
Or download from [python.org/downloads](https://www.python.org/downloads/) — tick **"Add Python to PATH"** during install.

**Linux (Ubuntu/Debian):**
```bash
sudo apt update && sudo apt install -y python3 python3-pip
```

**macOS:**
```bash
brew install python3
```

Verify: `python --version` or `python3 --version` — should show 3.9 or higher.

**deploy.py options:**
```bash
python deploy.py                 # full deploy (build from source, with dashboard)
python deploy.py --no-dashboard  # deploy without web dashboard
python deploy.py --pull          # use pre-built image from Docker Hub
python deploy.py --restart       # restart existing container only
python deploy.py --status        # check if server is running
python deploy.py --reset-key     # regenerate API key without full redeploy
python deploy.py --version       # print version and exit
```

---

## Web Dashboard

Once the server is running, open in your browser:

```
http://localhost:8765/dashboard
```

The dashboard shows metrics for **actively monitored hosts only** — nothing is polled by default:

| Panel | What it shows |
|---|---|
| Summary bar | Total watched hosts, online count, avg CPU, avg mem |
| Host cards | Status (OK / Unreachable / Error), CPU %, memory %, disk %, uptime |
| Auto-refresh | Toggle 30-second auto-refresh |
| API key field | Enter your `MCP_API_KEY` if auth is enabled |

To populate the dashboard, tell the agent:
```
Start monitoring all
```
or scope it to a project / tag / env / zone:
```
Start monitoring PROJECT_CORE
```
Use `Stop monitoring all` when done. Metrics are collected via SSH and cached for 30 seconds.

**API endpoints available at the same server:**
- `GET /api/status` — JSON metrics for all hosts
- `GET /api/status?refresh=1` — force-refresh (bypass cache)
- `GET /api/logs` — last 200 exec.log entries (all hosts)
- `GET /api/logs/{alias}` — exec.log filtered to one host
- `GET /api/logs/{alias}?n=50` — last N entries

> **Workflow tip:** Keep the dashboard open in a browser tab for monitoring, and use VS Code Agent mode for AI-assisted troubleshooting & fixes — both connect to the same MCP server.

**To disable the dashboard after deployment:**
```bash
# Edit docker-compose.yml: set MCP_DASHBOARD=false
docker compose restart remote-executor
```

---

## Tools

The server exposes **29 MCP tools** once connected. See the [Tools Reference in README.md](README.md#tools-reference-29-tools) for the full list.

---

## What the deploy scripts do (6 steps)

| Step | Action |
|---|---|
| 1 | Checks Docker is installed and running |
| 2 | Checks/fixes WSL2 mirrored networking in `~/.wslconfig` (Windows only) |
| 3 | Creates `data/`, generates `.env` with Fernet encryption key |
| 4 | **API key setup** — asks whether to enable auth, generates and saves key |
| 5 | Builds Docker image (or pulls from Hub) and starts the container |
| 6 | Health-checks `http://localhost:8765/sse` and confirms server is up |

At the end it prints the full integration guide **with your API key already filled in** and the dashboard URL.

---

## Manual Steps (if needed)

<details>
<summary>Expand manual steps</summary>

### WSL2 Mirrored Networking (Windows — VPN fix)

Open `C:\Users\<you>\.wslconfig` (create if missing) and add:

```ini
[wsl2]
networkingMode=mirrored
```

Then:
```powershell
wsl --shutdown
```

Restart Docker Desktop.

### First-Run Init

```bash
pip install cryptography
python init.py
```

### Build and Start

```bash
docker compose build
docker compose up -d
curl http://localhost:8765/sse
```

</details>

---

## Client Integration

### VS Code (GitHub Copilot Agent)

Open **User Settings JSON** (`Ctrl+Shift+P` → `Preferences: Open User Settings (JSON)`):

**Without auth:**
```json
{
  "mcp": {
    "servers": {
      "remote-executor": {
        "type": "sse",
        "url": "http://localhost:8765/sse"
      }
    }
  }
}
```

**With API key auth:**
```json
{
  "mcp": {
    "servers": {
      "remote-executor": {
        "type": "sse",
        "url": "http://localhost:8765/sse",
        "headers": { "X-MCP-Key": "<your-api-key>" }
      }
    }
  }
}
```

Restart VS Code → open Copilot Chat → switch to **Agent mode**.

### Claude Desktop

Add to `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "remote-executor": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://localhost:8765/sse"]
    }
  }
}
```

Restart Claude Desktop.

### Continue.dev

Add to `~/.continue/config.json`:

```json
{
  "experimental": {
    "modelContextProtocolServers": [{
      "transport": {
        "type": "sse",
        "url": "http://localhost:8765/sse"
      }
    }]
  }
}
```

---

## Authentication

`deploy.py` asks during setup whether to enable API key auth. If enabled:
- A random key is generated and saved to `.env` as `MCP_API_KEY`
- `docker-compose.yml` is updated automatically
- The integration guide printed at the end includes your key ready to paste

To enable/change the key after initial deployment:
```bash
# Edit .env — set MCP_API_KEY=<new-key>
# Then restart:
docker compose restart remote-executor
```

---

## First Commands (in Agent mode)

```
List all hosts
Add host web01 with IP <your-host-ip> to project CORE, user <your-user>
Save credential for web01
Check disk usage on web01
Ping all hosts
```

---

## Test SSH Connectivity (VPN check)

```bash
docker compose run --rm -it remote-executor python test_connectivity.py <your-host-ip> <your-user>
```

All 3 levels should pass:
```
Level 1 — ICMP ping              ✓ REACHABLE
Level 2 — TCP <your-host-ip>:22  ✓ PORT OPEN
Level 3 — SSH auth               ✓ SUCCESS
```

---

## Useful Docker Commands

```bash
docker compose logs -f remote-executor   # live logs
docker compose restart remote-executor   # restart
docker compose down                      # stop
docker compose up -d                     # start
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `deploy.py` stops at WSL2 step | Restart Docker Desktop, then re-run `deploy.py` |
| `curl` to `:8765` fails | `docker compose logs remote-executor` |
| Level 1/2 fails in container | Re-check `.wslconfig` mirrored networking |
| Auth failed (Level 3) | Wrong password — use `save_credential` again in Agent |
| `CRED_MASTER_KEY` error on start | `.env` missing — re-run `python init.py` |
| Image not found on `--pull` | Run without `--pull` to build from source instead |

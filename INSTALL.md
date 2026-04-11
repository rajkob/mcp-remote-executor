# Quick Install Guide — Remote Executor MCP Server

## Prerequisites

- Windows 10/11 or Linux with Docker Desktop / Docker Engine installed
- Python 3.9+ installed locally
- VS Code with GitHub Copilot extension (or Claude Desktop / Continue.dev)

---

## One-Command Deploy

Everything is automated via `deploy.py`. Run it once:

```bash
cd mcp-remote-executor
python deploy.py
```

That's it. The script handles all steps below automatically.

---

## What `deploy.py` Does (5 steps)

| Step | Action |
|---|---|
| 1 | Checks Docker is installed and running |
| 2 | Checks/fixes WSL2 mirrored networking in `~/.wslconfig` (Windows only) |
| 3 | Runs `init.py` — creates `data/`, generates `.env` with encryption key |
| 4 | Builds Docker image and starts the container |
| 5 | Health-checks `http://localhost:8765/sse` and confirms server is up |

At the end it prints the full integration guide for your LLM client.

---

## Deploy Options

```bash
python deploy.py              # full deploy (build from source)
python deploy.py --pull       # use pre-built image from Docker Hub (rajkob/mcp-remote-executor:11042026)
python deploy.py --restart    # restart existing container only
python deploy.py --status     # check if server is running
```

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

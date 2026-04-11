# Remote Executor MCP Server

A Dockerized Python MCP server that exposes SSH remote execution tools to any LLM client (VS Code Copilot, Claude Desktop, Continue.dev, etc.).

---

## Architecture

```
LLM Client (VS Code / Claude Desktop)
       │  HTTP/SSE  http://localhost:8765/sse
       ▼
┌─────────────────────────────────┐
│   Docker container              │
│   FastMCP server (server.py)    │
│   network_mode: host            │  ← inherits host VPN routes
│                                 │
│   paramiko SSH/SFTP             │
│   Fernet-encrypted credentials  │
└─────────────────────────────────┘
       │  SSH  port 22
       ▼
Remote hosts (private subnet / VPN)
```

---

## First-Run Setup

### 1. Prerequisites

- Docker Desktop (with WSL2 backend on Windows)
- Python 3.9+ (local, only for `init.py`)
- `cryptography` package: `pip install cryptography`

### 2. Generate master key and data skeleton

```bash
cd "mcp-remote-executor"
pip install cryptography
python init.py
```

This creates:
- `data/vms.yaml`  — empty host inventory
- `data/credentials` — empty encrypted store
- `.env` — auto-generated `CRED_MASTER_KEY`

### 3. Build and start

```bash
docker compose build
docker compose up -d
```

### 4. Verify

```bash
curl http://localhost:8765/sse
# Expected: SSE stream opened (text/event-stream response)
```

### 5. Test VPN connectivity (optional but recommended)

```bash
docker compose run --rm -it remote-executor python test_connectivity.py <host_ip> <user>
```

---

## Client Registration

### VS Code (GitHub Copilot)

Add to your **User Settings JSON** (`Ctrl+Shift+P` → "Open User Settings JSON"):

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

Then in Copilot Chat, switch to **Agent mode** and the 22 remote-executor tools will be available.

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

Restart Claude Desktop to load the new server.

### Continue.dev

Add to `~/.continue/config.json`:

```json
{
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type": "sse",
          "url": "http://localhost:8765/sse"
        }
      }
    ]
  }
}
```

---

## Usage Examples (Copilot Agent Mode)

```
"List all my hosts"
"Add host web01 with IP <your-host-ip> to project CORE"
"Save credential for web01"
"Check disk usage on web01"
"Run df -h on all CORE hosts in parallel"
"Ping all hosts"
"Show execution log"
"Run memory check on all production hosts"
```

---

## Tools Reference (22 tools)

| Category | Tool | Description |
|---|---|---|
| Host | `list_hosts` | List all hosts grouped by project |
| Host | `add_host` | Add new host to vms.yaml |
| Host | `remove_host` | Remove host (optionally delete credential) |
| Host | `update_host` | Update a single host field |
| Credentials | `save_credential` | Encrypt and store SSH password |
| Credentials | `check_credential` | Check if credential is stored |
| Credentials | `delete_credential` | Delete stored credential |
| Credentials | `audit_credentials` | Show credential status for all hosts |
| Execution | `run_command` | Run command on single host |
| Execution | `run_command_multi` | Run command on multiple hosts (sequential/parallel) |
| Execution | `upload_file` | Upload file via SFTP |
| Execution | `download_file` | Download file via SFTP |
| Connectivity | `ping_hosts` | Ping hosts to check reachability |
| Templates | `list_templates` | List command templates |
| Templates | `expand_template` | Preview template with alias substitution |
| Templates | `add_template` | Add/update command template |
| Templates | `remove_template` | Remove command template |
| Log | `read_exec_log` | Show last N execution log entries |
| Log | `clear_exec_log` | Clear execution log |
| Log | `save_output` | Save command output to timestamped file |

---

## Target Resolution

`run_command_multi` and `ping_hosts` accept a `target` that resolves in this order:

1. **Exact alias** — `web01`
2. **Project name** — `CORE`
3. **Tag** — `kubernetes`
4. **Env label** — `production`
5. **Zone** — `EU`
6. **"all"** — every host in vms.yaml

---

## VPN Troubleshooting (Windows + WSL2)

If `test_connectivity.py` fails inside the container but works from host:

**Option A** — Enable WSL2 mirrored networking (recommended):
```ini
# Add to C:\Users\<you>\.wslconfig
[wsl2]
networkingMode=mirrored
```
Then: `wsl --shutdown` and restart Docker Desktop.

**Option B** — Switch to bridge network mode:
In `docker-compose.yml`, comment out `network_mode: host` and add:
```yaml
network_mode: bridge
extra_hosts:
  - "host.docker.internal:host-gateway"
```

**Option C** — Deploy on a Linux VM on the same LAN as your remote hosts.

---

## Data Files

| File | Purpose |
|---|---|
| `data/vms.yaml` | Host inventory — projects, aliases, IPs, tags |
| `data/credentials` | Fernet-encrypted JSON — SSH passwords |
| `data/exec.log` | Append-only execution log |
| `data/output/` | Saved command output files |
| `.env` | `CRED_MASTER_KEY` — keep safe, never commit |

---

## Security Notes

- Credentials are encrypted with **Fernet (AES-128-CBC + HMAC-SHA256)**
- The `CRED_MASTER_KEY` is the only secret — back it up safely
- Server binds to `0.0.0.0:8765` — firewall port 8765 to trusted networks only
- All SSH host keys are auto-accepted (AutoAddPolicy) — suitable for internal/VPN networks
- `.env` and `data/` are in `.gitignore` — never committed

---

## API Key Authentication

The server supports optional API key authentication via the `MCP_API_KEY` env var.

**Enable during deployment** — `deploy.py` asks interactively and configures everything automatically.

**Enable manually:**
```ini
# .env
MCP_API_KEY=your-strong-random-key-here
```
Uncomment in `docker-compose.yml`:
```yaml
- MCP_API_KEY=${MCP_API_KEY}
```
Restart: `docker compose restart remote-executor`

**VS Code client config with auth:**
```json
"remote-executor": {
  "type": "sse",
  "url": "http://localhost:8765/sse",
  "headers": { "X-MCP-Key": "your-strong-random-key-here" }
}
```

Without `MCP_API_KEY` the server runs with auth disabled — safe for local-only deployments behind a firewall.

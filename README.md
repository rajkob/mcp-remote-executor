# Remote Executor MCP Server

[![Docker Pulls](https://img.shields.io/docker/pulls/rajkob/mcp-remote-executor?style=flat-square&logo=docker)](https://hub.docker.com/r/rajkob/mcp-remote-executor)
[![GitHub Stars](https://img.shields.io/github/stars/rajkob/mcp-remote-executor?style=flat-square&logo=github)](https://github.com/rajkob/mcp-remote-executor)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat-square)](LICENSE)

Give AI assistants **SSH access to your remote servers** — run commands, transfer files, monitor metrics, and get live health data, all from natural language in VS Code Copilot, Claude Desktop, or Continue.dev.

🚀 **[→ Quickstart — deploy in 5 minutes](QUICKSTART.md)**

---

## What it does

- **29 MCP tools** — run commands, upload/download files, check reachability, manage credentials, bulk-import hosts (CSV/JSON), on-demand monitoring, per-host command history, structured log export (JSON/CSV), health check; optional AI-assisted analysis via local Ollama
- **Web dashboard** — live CPU / memory / disk / uptime for **actively monitored** hosts at `http://localhost:8765/dashboard`; monitoring is off by default and activated per scope (project, env, zone, tag, or alias)
- **On-demand monitoring** — `start_monitoring`, `stop_monitoring`, `monitoring_status` let you focus on the hosts you care about right now, without polling everything continuously
- **Execution log panel** — recent SSH command history, filterable by host, live in the dashboard; also exportable as JSON or CSV via `export_exec_log`
- **Bulk host import** — `import_hosts` accepts CSV or JSON to onboard many hosts at once
- **Webhook notifications** — set `WEBHOOK_URL` to receive `command_failed` / `host_down` events to Slack, Teams, or any HTTP endpoint
- **Destructive command guard** — `rm -rf /`, `dd`, `mkfs`, `shutdown` and similar are blocked by default; `force=True` overrides
- **VPN-friendly** — Docker `network_mode: host` — private subnets reachable out of the box
- **Encrypted credentials** — Fernet (AES-128-CBC + HMAC-SHA256), never plaintext on disk
- **No Python on host** — deploy with `deploy.ps1` (Windows), `deploy.sh` (Linux/macOS), or `deploy.py` (cross-platform, supports `--pull` / `--restart` / `--status` / `--reset-key` / `--version`)
- **SSH connection pool** — transports are reused per host; fewer handshakes, lower latency on repeated commands
- **Works with** — VS Code Copilot, Claude Desktop, Continue.dev, any SSE MCP client

---

## Architecture

```
LLM Client (VS Code / Claude Desktop)    Browser (Dashboard)
       │  HTTP/SSE  :8765/sse                │  :8765/dashboard
       │                                     │  :8765/api/status
       └──────────────┬──────────────────────┘
                      ▼
┌─────────────────────────────────────────┐
│   Docker container (port 8765)          │
│   ├─ FastMCP SSE handler  /sse           │
│   └─ Dashboard + API      /dashboard     │  ← same port, path-routed
│   network_mode: host                    │  ← inherits host VPN routes
│                                         │
│   paramiko SSH/SFTP                     │
│   Fernet-encrypted credentials          │
│   monitor.py — SSH metric collection    │
│   TCP port check — no ICMP required     │
└─────────────────────────────────────────┘
       │  SSH  (configurable port per host)
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

Then in Copilot Chat, switch to **Agent mode** and the 29 remote-executor tools will be available.

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
"Import hosts from this CSV: ..."
"Save credential for web01"
"Check disk usage on web01"
"Run df -h on all CORE hosts in parallel"
"Ping all hosts"
"Start monitoring PROJECT_CORE"
"Monitoring status"
"Stop monitoring all"
"Command history for web01"
"Export exec log as JSON"
"Show execution log"
"Run memory check on all production hosts"
"Start monitoring tag:postgres in project DB"
```

---

## Tools Reference (29 tools)

| Category | Tool | Description |
|---|---|---|
| Host | `list_hosts` | List all hosts grouped by project |
| Host | `add_host` | Add new host to vms.yaml |
| Host | `remove_host` | Remove host (optionally delete credential) |
| Host | `update_host` | Update a single host field |
| Host | `import_hosts` | Bulk-import hosts from CSV or JSON content |
| Credentials | `save_credential` | Encrypt and store SSH password |
| Credentials | `check_credential` | Check if credential is stored |
| Credentials | `delete_credential` | Delete stored credential |
| Credentials | `audit_credentials` | Show credential status for all hosts |
| Execution | `run_command` | Run command on single host (destructive guard on by default) |
| Execution | `run_command_multi` | Run command on multiple hosts (sequential/parallel) |
| Execution | `upload_file` | Upload file via SFTP |
| Execution | `download_file` | Download file via SFTP |
| Connectivity | `ping_hosts` | TCP connect to SSH port — check reachability (works even when ICMP is blocked) |
| Connectivity | `health_check` | Full check: ping → SSH → disk/CPU/mem snapshot |
| Monitoring | `start_monitoring` | Activate metric collection for a target scope (alias/project/env/zone/tag/all) |
| Monitoring | `stop_monitoring` | Deactivate monitoring for a scope; use 'all' to stop everything |
| Monitoring | `monitoring_status` | Show active watch list with latest metrics |
| Templates | `list_templates` | List command templates |
| Templates | `expand_template` | Preview template — substitutes `{{alias}}`, `{{ip}}`, `{{user}}`, `{{env}}`, `{{zone}}`, `{{port}}` |
| Templates | `add_template` | Add/update command template |
| Templates | `remove_template` | Remove command template |
| Log | `read_exec_log` | Show last N execution log entries |
| Log | `clear_exec_log` | Clear execution log |
| Log | `save_output` | Save command output to timestamped file |
| Log | `command_history` | Show last N commands run on a specific host |
| Log | `export_exec_log` | Export log as JSON or CSV, optionally filtered by alias |
| AI | `ai_analyze` | SSH into a host, run diagnostics, analyse output with a local Ollama model |
| AI | `ollama_status` | Check which Ollama models are loaded in VRAM and GPU memory usage |

---

## Target Resolution

`run_command_multi`, `ping_hosts`, `start_monitoring`, and `stop_monitoring` accept a `target` that resolves in this order:

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
- Host reachability uses **TCP connect to the SSH port** — works on VMs where ICMP (ping) is firewalled
- `.env` and `data/` are in `.gitignore` — never committed

### Destructive command guard

`run_command` and `run_command_multi` block commands matching dangerous patterns by default:

| Pattern | Examples blocked |
|---|---|
| Recursive root delete | `rm -rf /`, `rm -rf /home` |
| Raw disk write | `dd of=/dev/sda`, `dd of=/dev/nvme0n1` |
| Filesystem format | `mkfs.ext4 /dev/sda`, `mkfs.xfs /dev/vda` |
| Shutdown / poweroff | `shutdown -h now`, `halt`, `poweroff` |
| Reboot | `reboot`, `init 6` |
| Fork bomb | `:(){ :|: & };:` |

To deliberately run one of these, pass `force=True`:
```
Run "reboot" on web01 with force=True
```

### Per-host concurrency limit

At most **3 concurrent SSH sessions per host** by default (configurable via `MAX_CONCURRENT_PER_HOST` env var). Prevents accidental SSH flooding during large multi-host runs.

### Webhook notifications

Set `WEBHOOK_URL` in `.env` / `docker-compose.yml` to receive HTTP POST alerts:
- `command_failed` — any non-zero exit code
- `host_down` — host unreachable during a ping sweep

```ini
# .env
WEBHOOK_URL=https://hooks.slack.com/services/...
```

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

---

## Web Dashboard

Open in your browser after deployment:

```
http://localhost:8765/dashboard
```

```
┌─────────────────────────────────────────────────────┐
│  ⚡ MCP Remote Executor — Dashboard        ⟳ Auto  │
├──────────┬──────────┬──────────┬──────────┬─────────┤
│ Hosts: 6 │ Online:5 │ Down: 1  │ CPU: 34% │ Mem:61% │
├──────────┴──────────┴──────────┴──────────┴─────────┤
│ ● web01  10.0.0.1   OK    CPU ██░░ 45%  MEM ███ 61% │
│ ● web02  10.0.0.2   OK    CPU █░░░ 12%  MEM ██░ 48% │
│ ✗ db01   10.0.0.10  UNREACHABLE                      │
└─────────────────────────────────────────────────────┘
```

**Features:**
- Host status grid (OK / Unreachable / Error) with colour-coded borders
- Progress bars for CPU %, memory %, disk % per host
- Uptime display
- Summary cards (total, online, avg CPU, avg mem)
- Auto-refresh every 30 seconds (toggle)
- API key input field (if auth is enabled)
- Metrics cached 30s server-side — parallel SSH collection
- **On-demand only** — dashboard shows metrics only for hosts you explicitly started monitoring; empty by default

**Workflow:** Use `start_monitoring(<target>)` in Agent mode to activate a scope, the dashboard populates automatically. Use `stop_monitoring(all)` when done. For always-on infrastructure monitoring use dedicated tools (Prometheus, Grafana Alloy, etc.) — this server is optimised for targeted debugging and remote deployments.

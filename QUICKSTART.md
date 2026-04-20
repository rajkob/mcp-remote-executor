# Quickstart — Remote Executor MCP Server

Get up and running in under 5 minutes. Only **Docker** required.

---

## Step 1 — Run the deploy script

**Windows (PowerShell):**
```powershell
cd mcp-remote-executor
.\deploy.ps1
```

**Linux / macOS (Bash):**
```bash
cd mcp-remote-executor
chmod +x deploy.sh && ./deploy.sh
```

**Or run the Python deploy script directly (any platform):**
```bash
python deploy.py              # full deploy
python deploy.py --pull       # pull pre-built image from Docker Hub instead of local build
python deploy.py --restart    # restart the existing container only
python deploy.py --status     # check if the server is running
python deploy.py --reset-key  # regenerate the API key without a full redeploy
python deploy.py --version    # print version and exit
```

The script will:
- Create `data/` and generate an encryption key
- Ask if you want API key auth (press **Y** — recommended)
- Build the Docker image and start the container
- Print your API key and integration config at the end

---

## Step 2 — Connect VS Code

Open **User Settings JSON** (`Ctrl+Shift+P` → `Preferences: Open User Settings (JSON)`) and paste:

**Without auth:**
```json
"mcp": {
  "servers": {
    "remote-executor": {
      "type": "sse",
      "url": "http://localhost:8765/sse"
    }
  }
}
```

**With auth (replace `<key>` with what the script printed):**
```json
"mcp": {
  "servers": {
    "remote-executor": {
      "type": "sse",
      "url": "http://localhost:8765/sse",
      "headers": { "X-MCP-Key": "<key>" }
    }
  }
}
```

Restart VS Code → open Copilot Chat → switch to **Agent** mode.

---

## Step 3 — Add your first host

In Agent chat:
```
Add host web01 with IP 10.0.0.10 to project CORE, user ubuntu
Save credential for web01
```

Test it:
```
Check disk usage on web01
Ping all hosts
Health check web01
Run "df -h" on all hosts in parallel
Start monitoring PROJECT_CORE
Monitoring status
Command history for web01
Export exec log as csv
Analyse disk usage on web01   (requires local Ollama — see LOCAL_LLM_SETUP.md)
```

---

## Step 4 — Open the dashboard (optional)

```
http://localhost:8765/dashboard
```

The dashboard shows metrics **only for hosts you are actively monitoring**. By default nothing is polled.
To populate it, tell the agent:
```
Start monitoring all
```
or scope it to a project:
```
Start monitoring PROJECT_CORE
```
Use `Stop monitoring all` to turn it off again.

---

## That's it

| What | Where |
|---|---|
| MCP SSE endpoint | `http://localhost:8765/sse` |
| Web dashboard | `http://localhost:8765/dashboard` |
| Host inventory | `data/vms.yaml` |
| Credentials (encrypted) | `data/credentials` |
| Execution log | `data/exec.log` |
| Env / API key | `.env` |

### Available MCP tools (29)

| Category | Tools |
|---|---|
| Host management | `list_hosts`, `add_host`, `remove_host`, `update_host`, `import_hosts` |
| Credentials | `save_credential`, `check_credential`, `delete_credential`, `audit_credentials` |
| Execution | `run_command`, `run_command_multi`, `upload_file`, `download_file` |
| Connectivity | `ping_hosts`, `health_check` |
| Monitoring | `start_monitoring`, `stop_monitoring`, `monitoring_status` |
| Templates | `list_templates`, `expand_template`, `add_template`, `remove_template` |
| Log | `read_exec_log`, `clear_exec_log`, `save_output`, `command_history`, `export_exec_log` |
| AI (optional) | `ai_analyze`, `ollama_status` (requires local Ollama) |

**Restart / stop:**
```bash
docker compose restart remote-executor   # restart
docker compose down                      # stop
docker compose up -d                     # start again
```

For full install options, Python instructions, and troubleshooting → see [INSTALL.md](INSTALL.md).

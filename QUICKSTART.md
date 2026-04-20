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
Analyse disk usage on web01   (requires local Ollama — see LOCAL_LLM_SETUP.md)
```

---

## Step 4 — Open the dashboard (optional)

```
http://localhost:8765/dashboard
```

Live CPU / memory / disk for all hosts. Auto-refreshes every 30 seconds.

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

**Restart / stop:**
```bash
docker compose restart remote-executor   # restart
docker compose down                      # stop
docker compose up -d                     # start again
```

For full install options, Python instructions, and troubleshooting → see [INSTALL.md](INSTALL.md).

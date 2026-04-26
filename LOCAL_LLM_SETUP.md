# Local LLM Setup Guide — mcp-remote-executor

> **Complete manual for integrating a local LLM (Ollama) with the Remote Executor MCP Server on a Lenovo Legion 5 Pro or any machine with an NVIDIA GPU (8GB+ VRAM).**

---

## Table of Contents

1. [Hardware Overview & Requirements](#1-hardware-overview--requirements)
2. [Understanding the Two Modes](#2-understanding-the-two-modes)
3. [Installing Ollama](#3-installing-ollama)
4. [Choosing the Right LLM Model](#4-choosing-the-right-llm-model)
5. [Ollama Resource Management — Start & Stop](#5-ollama-resource-management--start--stop)
6. [MODE 1 — Development with GitHub Copilot](#6-mode-1--development-with-github-copilot)
7. [MODE 2 — Operational with Local LLM (Continue.dev)](#7-mode-2--operational-with-local-llm-continuedev)
8. [Creating a Custom Model with Your System Prompt](#8-creating-a-custom-model-with-your-system-prompt)
9. [Integrating Ollama into server.py](#9-integrating-ollama-into-serverpy)
10. [Performance Tuning for 8GB VRAM](#10-performance-tuning-for-8gb-vram)
11. [Security Architecture](#11-security-architecture)
12. [Switching Between Modes — Daily Workflow](#12-switching-between-modes--daily-workflow)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Hardware Overview & Requirements

This guide is optimized for the following hardware profile, but applies to any Windows laptop with an NVIDIA GPU and 8GB+ VRAM:

| Component | Spec | AI Suitability |
|---|---|---|
| **Laptop** | Lenovo Legion 5 Pro | ✅ Excellent thermal headroom |
| **CPU** | Intel i9 12th Gen | ✅ Strong for MCP server processes |
| **RAM** | 32GB DDR5 | ✅ Runs MCP + VPN + agents simultaneously |
| **GPU** | NVIDIA RTX 3070 Ti | ✅ CUDA support, good for local inference |
| **VRAM** | 8GB GDDR6X | ✅ Fits 7B–13B quantized models |
| **Storage** | NVMe SSD (recommend 512GB+) | LLM models are 4–8GB each |

### What 8GB VRAM Can Run

| VRAM Used | Model Size | Examples | Agent Quality |
|---|---|---|---|
| ~4–5GB | 7B Q4 | Mistral 7B, Qwen2.5 7B | ✅ Good for agents |
| ~5–6GB | 8B Q4 | Llama 3.1 8B | ✅ Excellent reasoning |
| ~8GB | 14B Q4 | Phi-3 Medium (tight) | ✅ Best quality, push limits |

> **Rule of thumb:** Always use **Q4_K_M quantization** for 8GB VRAM — best balance of quality and speed.

---

## 2. Understanding the Two Modes

The MCP server and the LLM client are **completely independent**. The same `mcp-remote-executor` server runs at all times — you simply choose which AI client connects to it depending on your task:

```
┌──────────────────────────────────────────────────────────┐
│          mcp-remote-executor (:8765/sse)                 │
│          Always running — server never changes           │
└─────────────────────┬────────────────────────────────────┘
                      │ SSE connection
         ┌────────────┴────────────┐
         │                         │
  MODE 1: DEVELOPMENT         MODE 2: OPERATIONAL
  VS Code Copilot Chat         Continue.dev Chat
  (Ctrl+Shift+I → Agent)       (Ctrl+L)
  ☁️  GitHub Copilot LLM        🏠 Local Ollama LLM
  For: coding, testing,        For: real remote ops,
  debugging server.py          VPN work, offline, secure
```

**Nothing changes in `server.py` or Docker** — you just open a different chat panel in VS Code.

---

## 3. Installing Ollama

### Windows (Recommended)

```powershell
# Option A — winget (easiest)
winget install Ollama.Ollama

# Option B — download installer
# https://ollama.com/download/windows
```

Verify installation:
```powershell
ollama --version
```

### Linux

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Pull Your Models (do this while online)

```bash
ollama pull qwen2.5:7b        # Best tool-calling — primary recommendation
ollama pull llama3.1:8b       # Best reasoning + system prompt following
ollama pull mistral:7b-instruct  # Fastest — good fallback
```

> Models are stored at `C:\Users\<you>\.ollama\models` on Windows.  
> Once pulled, they work **100% offline** — no internet needed.

---

## 4. Choosing the Right LLM Model

The following models are recommended specifically for the `mcp-remote-executor` use case, which requires strong **tool calling**, **structured JSON output**, and **strict system prompt following**:

| Model | VRAM | Tool Calling | System Prompt | Speed | Verdict |
|---|---|---|---|---|---|
| **Qwen2.5 7B Instruct** | ~5GB | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🏆 **#1 Recommended** |
| **Llama 3.1 8B Instruct** | ~5.5GB | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 🥈 Best reasoning |
| **Mistral 7B Instruct** | ~4.5GB | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Fastest option |
| **DeepSeek-Coder 6.7B** | ~4.5GB | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | Best for command generation |
| **Phi-3 Medium 14B** | ~8GB | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | Push limits — tight on 8GB |

### Why Qwen2.5 7B is #1 for this use case

Your `server.py` exposes 21 MCP tools with strict routing defined in `system_prompt.md`. Qwen2.5 7B leads among 7B-class models in:
- Following intent → tool routing tables
- Producing consistent JSON output for tool parameters
- Respecting confirmation and pre-flight safety workflows
- Low temperature deterministic responses (important for SSH command execution)

---

## 5. Ollama Resource Management — Start & Stop

Ollama keeps models loaded in VRAM even when idle. Use these scripts to free your GPU when not doing AI work.

### PowerShell Scripts

Create these files in your project root. They are **not included in the repo** — copy the code blocks below and save them manually:

> 💡 Tip: Save them as `ollama-start.ps1`, `ollama-stop.ps1`, and `ollama-status.ps1` in the `mcp-remote-executor` folder so they're next to `deploy.ps1`.

**`ollama-start.ps1`**
```powershell
# Start Ollama service and pre-load primary model
Write-Host "Starting Ollama..." -ForegroundColor Green
Start-Process "ollama" -ArgumentList "serve" -WindowStyle Hidden
Start-Sleep -Seconds 3

# Pre-warm model (makes first query instant)
Write-Host "Loading model into VRAM..." -ForegroundColor Cyan
ollama run qwen2.5:7b --keepalive 60m "ready" 2>$null

Write-Host "Ollama ready at http://localhost:11434" -ForegroundColor Green
Write-Host "VRAM usage:" -ForegroundColor Yellow
ollama ps
```

**`ollama-stop.ps1`**
```powershell
# Unload model from VRAM first (frees GPU memory immediately)
Write-Host "Unloading models from VRAM..." -ForegroundColor Yellow
ollama stop qwen2.5:7b
ollama stop llama3.1:8b
ollama stop mistral:7b-instruct

# Stop the Ollama service
$proc = Get-Process -Name "ollama" -ErrorAction SilentlyContinue
if ($proc) {
    Stop-Process -Name "ollama" -Force
    Write-Host "Ollama stopped — VRAM freed" -ForegroundColor Green
} else {
    Write-Host "Ollama was not running" -ForegroundColor Gray
}
```

**`ollama-status.ps1`**
```powershell
# Check what is loaded in VRAM right now
Write-Host "Ollama loaded models:" -ForegroundColor Cyan
ollama ps

Write-Host "`nGPU VRAM usage:" -ForegroundColor Cyan
nvidia-smi --query-gpu=name,memory.used,memory.free,memory.total --format=csv,noheader
```

### Auto-Unload After Idle (Recommended)

Set `OLLAMA_KEEP_ALIVE` as a **host shell / system environment variable** (not in `.env` — that file is for the Docker container, not Ollama):

```powershell
# PowerShell — set for current session
$env:OLLAMA_KEEP_ALIVE = "10m"
ollama serve

# Or set permanently in Windows System Environment Variables:
# System Properties → Advanced → Environment Variables → New User Variable
# Name: OLLAMA_KEEP_ALIVE   Value: 10m
```

Recommended values by workflow:

| Workflow | OLLAMA_KEEP_ALIVE | Behaviour |
|---|---|---|
| Active AI ops session | `60m` | Stays hot, instant responses |
| Occasional use | `10m` | Auto-unloads after 10 min idle |
| Maximum VRAM savings | `0` | Unloads after every request |
| Gaming / other GPU work | `0` | Free VRAM immediately when done |

### Quick Reference Commands

```powershell
ollama serve                    # Start Ollama service
ollama ps                       # Show what is loaded in VRAM
ollama stop qwen2.5:7b          # Unload specific model from VRAM
ollama list                     # Show all downloaded models
nvidia-smi                      # Check overall GPU VRAM usage
```

---

## 6. MODE 1 — Development with GitHub Copilot

Use this mode when **developing, testing, and debugging** `server.py` and your MCP tools.

### VS Code Settings

Open **User Settings JSON** (`Ctrl+Shift+P` → `Preferences: Open User Settings (JSON)`) and ensure you have:

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

With API key authentication enabled:
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

### How to Use

1. Ensure your MCP server is running: `docker compose up -d`
2. Open **Copilot Chat** with `Ctrl+Shift+I`
3. Switch to **Agent mode** (dropdown at the bottom of the chat panel)
4. All 21 MCP tools from `remote-executor` are now available to GitHub Copilot

### What This Mode is Good For

- Writing and testing new MCP tools in `server.py`
- Asking Copilot to explain or refactor your code
- Debugging SSH connection issues with AI assistance
- Testing tool routing and responses during development
- Code review and suggestions with full codebase context

> **LLM:** ☁️ GitHub Copilot (cloud) — internet required

---

## 7. MODE 2 — Operational with Local LLM (Continue.dev)

Use this mode for **real operational work** — running commands on remote hosts over VPN, working offline, or in security-sensitive environments.

### Step 1 — Install Continue.dev

In VS Code Extensions (`Ctrl+Shift+X`), search for **Continue** and install the extension by `Continue.dev`.

> Continue.dev installs **alongside** GitHub Copilot — both extensions coexist with no conflict.

### Step 2 — Configure Continue.dev

Open or create `~/.continue/config.json` (Continue creates this on first launch):

```json
{
  "models": [
    {
      "title": "Qwen2.5 7B — Local MCP Ops",
      "provider": "ollama",
      "model": "qwen2.5:7b",
      "apiBase": "http://localhost:11434",
      "systemMessage": "You are a remote SSH execution assistant managing hosts via MCP tools. Always confirm before running commands on remote hosts."
    },
    {
      "title": "Llama 3.1 8B — Local",
      "provider": "ollama",
      "model": "llama3.1:8b",
      "apiBase": "http://localhost:11434"
    },
    {
      "title": "Mistral 7B — Fast Local",
      "provider": "ollama",
      "model": "mistral:7b-instruct",
      "apiBase": "http://localhost:11434"
    }
  ],
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

With API key authentication:
```json
{
  "models": [
    {
      "title": "Qwen2.5 7B — Local MCP Ops",
      "provider": "ollama",
      "model": "qwen2.5:7b",
      "apiBase": "http://localhost:11434"
    }
  ],
  "experimental": {
    "modelContextProtocolServers": [
      {
        "transport": {
          "type": "sse",
          "url": "http://localhost:8765/sse",
          "requestOptions": {
            "headers": { "X-MCP-Key": "<your-api-key>" }
          }
        }
      }
    ]
  }
}
```

### Step 3 — Start Ollama

```powershell
# Option A — use the script
.\ollama-start.ps1

# Option B — manual
ollama serve
ollama pull qwen2.5:7b   # if not already pulled
```

### Step 4 — Use Continue.dev Chat

1. Open **Continue Chat** with `Ctrl+L`
2. Select model **Qwen2.5 7B — Local MCP Ops** from the model dropdown
3. All 21 MCP tools are now available through your local LLM

### Example Prompts in Operational Mode

```
List all my hosts
Ping all hosts in project CORE
Check disk usage on web01
Run memory check on all production hosts
Health check db01
Show execution log
```

> **LLM:** 🏠 Local Ollama — no internet required, no data sent to cloud

---

## 8. Creating a Custom Model with Your System Prompt

Bake `system_prompt.md` directly into your Ollama model so it is always active without manual configuration:

### Create the Modelfile

A ready-to-use `Modelfile` is included in the project root. It bakes the full
`system_prompt.md` (including the Canonical Command Mapping and Multi-Host
Execution Rules) directly into the model so every response uses the same
deterministic tool routing and exact canonical commands.

> Use `temperature 0.1` — low temperature is critical for deterministic tool routing and safe command execution.

> **Rebuild after any `system_prompt.md` change:**
> ```bash
> ollama create mcp-executor -f Modelfile
> ```

### Build and Run the Custom Model

```bash
ollama create mcp-executor -f Modelfile
ollama run mcp-executor
```

### Use in Continue.dev

```json
{
  "models": [
    {
      "title": "MCP Executor (Custom)",
      "provider": "ollama",
      "model": "mcp-executor",
      "apiBase": "http://localhost:11434"
    }
  ]
}
```

---

## 9. Integrating Ollama into server.py

> **✅ Already built-in as of v2.1.1.** The `ai_analyze` and `ollama_status` tools ship with `server.py` — you do **not** need to add any code. The server always exposes **23 tools** (including the two AI tools). They gracefully return an error if Ollama is not running, so having Ollama installed is optional.

All you need to do is configure the environment variables so the container can reach Ollama on your host:

### Add to `.env` (Docker container vars)

```ini
# Local LLM integration — read by server.py inside the container
OLLAMA_URL=http://host.docker.internal:11434
OLLAMA_MODEL=qwen2.5:7b
```

> **Note:** `OLLAMA_KEEP_ALIVE` is an Ollama host process variable — set it in your Windows system environment or PowerShell session, **not** in `.env`. See Section 5 for details.

### Tool usage examples

Once Ollama is running and the env vars are set, you can use in both Copilot Agent mode and Continue.dev:

```
"Analyze disk usage on web01"
"Is there a memory leak on db01?"
"Check what services are failing on worker01"
"Explain the errors in logs on web02"
"Check if Ollama is ready"
```

---

## 10. Performance Tuning for 8GB VRAM

### Environment Variables (set before starting Ollama)

```powershell
# Windows PowerShell — force full GPU offload
$env:OLLAMA_NUM_GPU = "1"
$env:OLLAMA_GPU_LAYERS = "35"     # all layers on GPU for 7B models
$env:OLLAMA_KEEP_ALIVE = "10m"    # auto-unload after 10 min idle
```

Or set permanently in Windows System Environment Variables.

### Expected Performance on RTX 3070 Ti

| Model | Quantization | VRAM Used | Tokens/sec | Context |
|---|---|---|---|---|
| Qwen2.5 7B | Q4_K_M | ~5GB | ~35–45 t/s | 4096 |
| Llama 3.1 8B | Q4_K_M | ~5.5GB | ~30–40 t/s | 4096 |
| Mistral 7B | Q4_K_M | ~4.5GB | ~40–50 t/s | 4096 |
| Phi-3 Medium 14B | Q4_K_M | ~8GB | ~15–20 t/s | 4096 |

### Context Window Recommendation

```bash
# Safe — fast, leaves VRAM headroom for MCP server processes
num_ctx 4096

# Possible — slower, less headroom, use only if you need long outputs
num_ctx 8192

# Do not use on 8GB VRAM — will cause VRAM overflow to system RAM
num_ctx 16384+
```

### Running MCP Server + Ollama Together

Your 32GB system RAM means you can comfortably run everything simultaneously:

| Process | VRAM | System RAM |
|---|---|---|
| Ollama (Qwen2.5 7B Q4) | ~5GB | ~500MB |
| Docker (mcp-remote-executor) | 0 | ~300MB |
| VPN client | 0 | ~100MB |
| VS Code + Continue.dev | 0 | ~500MB |
| **Total** | **~5GB / 8GB** | **~1.5GB / 32GB** |

---

## 11. Security Architecture

This setup achieves full local inference with no data leaving your machine:

```
┌─────────────────────────────────────────────────────────┐
│                  Legion 5 Pro (Local)                   │
│                                                         │
│  Ollama (localhost:11434)  ←── never exposed externally │
│  mcp-remote-executor (:8765) ← firewall to LAN only    │
│  Continue.dev (VS Code)    ←── calls localhost only     │
│                                                         │
│  Fernet-encrypted SSH credentials (AES-128-CBC)         │
│  CRED_MASTER_KEY in .env — never committed to git       │
│  Optional MCP_API_KEY for server authentication         │
│                                                         │
│  Docker network_mode: host → inherits VPN routes        │
│  WSL2 mirrored networking → private subnets reachable   │
└──────────────────────────┬──────────────────────────────┘
                           │ SSH over VPN tunnel
                    Remote Hosts (private subnet)
```

### Security Checklist

- [ ] `OLLAMA_HOST` not set to `0.0.0.0` (keep default `localhost` only)
- [ ] Port `8765` firewalled to trusted networks only
- [ ] `MCP_API_KEY` set in `.env` for any non-local deployment
- [ ] `.env` and `data/` confirmed in `.gitignore` (never committed)
- [ ] `CRED_MASTER_KEY` backed up securely outside the repo
- [ ] Ollama model files stored locally — never uploaded anywhere

### Offline / Air-Gap Operation

Once models are pulled, the entire stack operates without internet:

```powershell
# Confirm all models are downloaded
ollama list

# From this point — full offline operation:
# - ollama serve → local inference, no outbound calls
# - docker compose up -d → MCP server, no cloud dependencies
# - Continue.dev → talks only to localhost:11434
# - VPN → your own corporate tunnel, not internet
```

---

## 12. Switching Between Modes — Daily Workflow

### Starting Your AI Ops Session

```powershell
# 1. Start MCP server (if not already running)
docker compose up -d

# 2. Start Ollama for local LLM work
.\ollama-start.ps1

# 3. Verify everything is up
curl http://localhost:8765/sse
ollama ps
```

### During Your Session

| Task | Action | Chat panel |
|---|---|---|
| Write / debug `server.py` | Open Copilot Chat | `Ctrl+Shift+I` → Agent mode |
| Test a new MCP tool | Open Copilot Chat | `Ctrl+Shift+I` → Agent mode |
| Run commands on remote hosts | Open Continue Chat | `Ctrl+L` |
| Work over VPN offline | Open Continue Chat | `Ctrl+L` |
| Analyze host metrics with AI | Open Continue Chat | `Ctrl+L` → `ai_analyze` tool |

> Both chat panels connect to the **same `http://localhost:8765/sse`** — only the LLM brain differs.

### Ending Your Session

```powershell
# Free VRAM when done with AI work
.\
ollama-stop.ps1

# Stop MCP server if not needed
docker compose down
```

### Quick Mode Toggle Summary

| | GitHub Copilot (MODE 1) | Local Ollama (MODE 2) |
|---|---|---|
| **Open with** | `Ctrl+Shift+I` | `Ctrl+L` |
| **LLM** | ☁️ GitHub cloud | 🏠 Local GPU |
| **Internet needed** | Yes | No |
| **Data privacy** | Sent to GitHub | Stays on machine |
| **VRAM used** | 0 (cloud) | ~5GB |
| **Best for** | Dev & testing | Ops & VPN work |
| **MCP tools** | ✅ 23 tools (ai_analyze + ollama_status built-in) | ✅ 23 tools (ai_analyze + ollama_status built-in) |

---

## 13. Troubleshooting

### Ollama Issues

| Problem | Cause | Fix |
|---|---|---|
| `connection refused` at `:11434` | Ollama not started | Run `ollama serve` or `.\ollama-start.ps1` |
| Model loads slowly | Not pre-warmed | Run `ollama run qwen2.5:7b "ready"` first |
| VRAM overflow / OOM | Model too large | Use Q4_K_M quantization, reduce `num_ctx` |
| Wrong GPU used | Multi-GPU system | Set `CUDA_VISIBLE_DEVICES=0` |
| Model not found | Not pulled | Run `ollama pull qwen2.5:7b` |

### Continue.dev Issues

| Problem | Cause | Fix |
|---|---|---|
| MCP tools not appearing | Server not running | `docker compose up -d` and verify `:8765/sse` |
| Model not responding | Ollama not started | Run `ollama serve` |
| 401 errors on MCP tools | API key mismatch | Add `X-MCP-Key` header in `config.json` |
| Config not loading | JSON syntax error | Validate `~/.continue/config.json` at jsonlint.com |

### VS Code Copilot Agent Issues

| Problem | Cause | Fix |
|---|---|---|
| MCP tools not listed | Server not running | `docker compose up -d` |
| Agent mode unavailable | Copilot plan | Requires GitHub Copilot subscription |
| Tools show but fail | Auth error | Add `X-MCP-Key` header in VS Code settings |

### MCP Server Issues

| Problem | Cause | Fix |
|---|---|---|
| Container fails to start | Missing `.env` | Run `python init.py` to regenerate |
| SSH fails inside Docker | VPN not in WSL2 | Add `networkingMode=mirrored` to `.wslconfig` |
| Dashboard not loading | Port conflict | Check `docker compose logs remote-executor` |

### Check Everything at Once

```powershell
# MCP server health
curl http://localhost:8765/sse

# Ollama health
curl http://localhost:11434/api/tags

# VRAM usage
ollama ps
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader

# Docker container status
docker compose ps
docker compose logs --tail=20 remote-executor
```

---

## Appendix — Full Stack Architecture

```
VS Code
├── GitHub Copilot Chat (Ctrl+Shift+I)
│   └── Agent mode → calls MCP tools via SSE
│       LLM: ☁️  GitHub Copilot cloud
│
└── Continue.dev Chat (Ctrl+L)
    └── Agent mode → calls MCP tools via SSE
        LLM: 🏠 Ollama localhost:11434
                └── qwen2.5:7b (Q4_K_M, ~5GB VRAM)

Both connect to ↓

mcp-remote-executor (Docker, port 8765)
├── FastMCP SSE handler  → /sse
├── Dashboard + API      → /dashboard
├── APIKeyMiddleware     → X-MCP-Key auth
├── 21 MCP tools         → server.py
├── ai_analyze tool      → SSH + Ollama analysis (optional)
├── ollama_status tool   → VRAM check (optional)
├── Fernet credentials   → data/credentials
├── Host inventory       → data/vms.yaml
└── network_mode: host   → inherits VPN routes

Ollama (localhost:11434)
├── qwen2.5:7b  (primary)
├── llama3.1:8b (alternative)
└── mistral:7b  (fast fallback)
    Models stored: C:\Users\<you>\.ollama\models
    Offline capable: ✅

VPN Tunnel → Remote Hosts (private subnet)
└── SSH via paramiko (port 22 per host)
```

---

*Generated for [rajkob/mcp-remote-executor](https://github.com/rajkob/mcp-remote-executor)*
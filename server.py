"""
Remote Executor MCP Server

Exposes SSH remote execution tools via FastMCP (HTTP/SSE transport).
Connect clients to: http://localhost:8765/sse

Tools are grouped by category:
  Host management  — list_hosts, add_host, remove_host, update_host
  Credentials      — save_credential, check_credential, delete_credential, audit_credentials
  Execution        — run_command, run_command_multi, upload_file, download_file
  Connectivity     — ping_hosts, health_check
  Templates        — list_templates, expand_template, add_template, remove_template
  Log              — read_exec_log, clear_exec_log, save_output
  AI (optional)    — ai_analyze, ollama_status  (require Ollama running locally)
"""
import json as _json
import os
import re
import hmac
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Literal

import uvicorn
from fastmcp import FastMCP

import credentials as creds
import dashboard as dash
import exec_log
import ping_tools
import ssh_tools
import vms

# Load system prompt (condensed SKILL.md) as LLM instructions
_PROMPT_FILE = Path(__file__).parent / "system_prompt.md"
_INSTRUCTIONS = _PROMPT_FILE.read_text(encoding="utf-8") if _PROMPT_FILE.exists() else ""

mcp = FastMCP("remote-executor", instructions=_INSTRUCTIONS)


# ─── API KEY MIDDLEWARE ───────────────────────────────────────────────────────

class APIKeyMiddleware:
    """
    Pure ASGI middleware — enforces X-MCP-Key header or ?api_key= query param.
    Only active when MCP_API_KEY env var is set and non-empty.
    Path /health is always allowed without auth.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        api_key = os.getenv("MCP_API_KEY", "").strip()

        # Auth disabled or non-HTTP scope (lifespan etc.) — pass through
        if not api_key or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Always allow health check
        if scope.get("path", "") == "/health":
            await self.app(scope, receive, send)
            return

        # Check X-MCP-Key header
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        provided = headers.get(b"x-mcp-key", b"").decode()

        # Fall back to ?api_key= query param
        if not provided:
            from urllib.parse import parse_qs
            qs = parse_qs(scope.get("query_string", b"").decode())
            provided = qs.get("api_key", [""])[0]

        if not hmac.compare_digest(provided, api_key):
            body = b"401 Unauthorized - X-MCP-Key header or ?api_key= param required"
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"text/plain"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


# ─── HOST MANAGEMENT ──────────────────────────────────────────────────────────

@mcp.tool()
def list_hosts() -> str:
    """List all hosts from vms.yaml grouped by project as a markdown table."""
    return vms.format_hosts_table()


@mcp.tool()
def add_host(
    project: str,
    alias: str,
    ip: str,
    port: int = 22,
    user: str = "",
    env: str = "",
    zone: str = "",
    tags: list[str] | None = None,
    auth: Literal["credential-manager", "keyFile", "prompt"] = "prompt",
    key_file: str = "",
) -> str:
    """
    Add a new host to vms.yaml.
    auth: 'credential-manager' | 'keyFile' | 'prompt'
    key_file: path to SSH private key (only needed when auth=keyFile)
    """
    host_dict: dict = {"alias": alias, "ip": ip, "port": port}
    if user:
        host_dict["user"] = user
    if env:
        host_dict["env"] = env
    if zone:
        host_dict["zone"] = zone
    if tags:
        host_dict["tags"] = tags
    if auth != "prompt":
        host_dict["auth"] = auth
    if auth == "keyFile" and key_file:
        host_dict["keyFile"] = key_file

    try:
        vms.write_host(project, host_dict)
        return f"✓ Host '{alias}' ({ip}:{port}) added to project '{project}'."
    except vms.DuplicateAlias as e:
        return f"❌ {e}"


@mcp.tool()
def remove_host(alias: str, also_delete_credential: bool = False) -> str:
    """
    Remove a host from vms.yaml.
    Set also_delete_credential=True to also delete the stored password.
    """
    try:
        host = vms.get_host(alias)
        project = vms.delete_host(alias)
        msg = f"✓ Host '{alias}' removed from project '{project}'."
        if also_delete_credential:
            deleted = creds.delete_credential(host["ip"], host.get("user", "root"))
            msg += " Credential deleted." if deleted else " No stored credential found."
        return msg
    except vms.HostNotFound as e:
        return f"❌ {e}"


@mcp.tool()
def update_host(alias: str, field: str, value: str) -> str:
    """
    Update a single field of an existing host in vms.yaml.
    Supported fields: ip, port, user, env, zone, auth, keyFile, timeout, tags
    For tags, pass comma-separated values: "kubernetes, database"
    """
    try:
        typed_value: object = value
        if field in ("port", "timeout"):
            typed_value = int(value) if value else None
        elif field == "tags":
            typed_value = [t.strip() for t in value.split(",") if t.strip()]
        vms.update_host(alias, field, typed_value)
        return f"✓ Updated '{alias}'.{field} = {value}"
    except vms.HostNotFound as e:
        return f"❌ {e}"
    except ValueError as e:
        return f"❌ Invalid value for field '{field}': {e}"


# ─── CREDENTIAL MANAGEMENT ────────────────────────────────────────────────────

@mcp.tool()
def save_credential(alias: str, password: str) -> str:
    """
    Save an SSH password for a host. Encrypted with Fernet (AES) — never stored in plaintext.
    Also updates the host's auth field to 'credential-manager' in vms.yaml.
    """
    try:
        host = vms.get_host(alias)
        creds.save_credential(host["ip"], host.get("user", "root"), password)
        vms.update_host(alias, "auth", "credential-manager")
        return f"✓ Credential saved for '{alias}' ({host['ip']})."
    except vms.HostNotFound as e:
        return f"❌ {e}"


@mcp.tool()
def check_credential(alias: str) -> str:
    """Check whether an SSH credential is stored for a host."""
    try:
        host = vms.get_host(alias)
        found = creds.credential_exists(host["ip"], host.get("user", "root"))
        if found:
            return f"✅ FOUND — credential is stored for '{alias}' ({host['ip']})."
        return f"❌ NOT FOUND — no credential stored for '{alias}'. Call save_credential first."
    except vms.HostNotFound as e:
        return f"❌ {e}"


@mcp.tool()
def delete_credential(alias: str) -> str:
    """Delete the stored SSH credential for a host. Resets auth to 'prompt' in vms.yaml."""
    try:
        host = vms.get_host(alias)
        deleted = creds.delete_credential(host["ip"], host.get("user", "root"))
        if deleted:
            vms.update_host(alias, "auth", "prompt")
            return f"✓ Credential deleted for '{alias}'."
        return f"No credential found for '{alias}'."
    except vms.HostNotFound as e:
        return f"❌ {e}"


@mcp.tool()
def audit_credentials() -> str:
    """
    Show credential status for all hosts — alias, IP, auth method, and whether stored.
    Highlights hosts with auth=credential-manager but missing credentials.
    """
    all_hosts = vms.get_all_hosts()
    if not all_hosts:
        return "No hosts configured."

    lines = ["| Alias | IP | Auth | Credential |", "|---|---|---|---|"]
    missing = []

    for host in sorted(all_hosts, key=lambda h: h.get("alias", "")):
        alias = host.get("alias", "")
        ip = host.get("ip", "")
        auth = host.get("auth", "prompt")

        if auth == "credential-manager":
            stored = creds.credential_exists(ip, host.get("user", "root"))
            status = "✅ Stored" if stored else "❌ MISSING"
            if not stored:
                missing.append(alias)
        elif auth == "keyFile":
            status = "🔑 Key file"
        else:
            status = "⌨️ Prompt"

        lines.append(f"| {alias} | {ip} | {auth} | {status} |")

    result = "\n".join(lines)
    if missing:
        result += f"\n\n⚠️ **{len(missing)} host(s) missing credentials:** {', '.join(missing)}"
    return result


# ─── EXECUTION ────────────────────────────────────────────────────────────────

@mcp.tool()
def run_command(alias: str, command: str) -> str:
    """
    Run a shell command on a single host by alias.
    Returns stdout, stderr, exit code, and elapsed time.
    Auto-logged to exec.log.
    """
    try:
        r = ssh_tools.ssh_exec(alias, command)
        icon = "✅" if r["exit_code"] == 0 else "❌"
        lines = [
            f"─── {r['alias']} ({r['ip']}) ── {icon} exit {r['exit_code']} ── {r['elapsed_s']}s ───"
        ]
        if r.get("stdout"):
            lines.append(r["stdout"].rstrip())
        if r.get("stderr"):
            lines.append(f"[stderr] {r['stderr'].rstrip()}")
        return "\n".join(lines)
    except ssh_tools.CredentialNotFound as e:
        return f"❌ {e}"
    except ssh_tools.HostUnreachable as e:
        return f"⚠️ {e}"
    except ssh_tools.AuthFailure as e:
        return f"🔐 {e}"
    except ssh_tools.CommandTimeout as e:
        return f"⏱ {e}"
    except vms.HostNotFound as e:
        return f"❌ {e}"


@mcp.tool()
def run_command_multi(
    target: str,
    command: str,
    mode: Literal["sequential", "parallel"] = "sequential",
) -> str:
    """
    Run a shell command on multiple hosts.
    target: alias | project name | tag | env label | zone label | "all"
    mode: 'sequential' (stream as each completes) | 'parallel' (all at once)
    Auto-logged to exec.log per host.
    """
    try:
        hosts = vms.resolve_target(target)
    except vms.HostNotFound as e:
        return f"❌ {e}"

    if not hosts:
        return f"No hosts found for target '{target}'."

    aliases = [h["alias"] for h in hosts]
    results = ssh_tools.ssh_exec_multi(aliases, command, mode=mode)

    lines = [f"**Ran `{command}` on {len(results)} host(s)** (mode: {mode})\n"]
    ok = failed = 0

    for r in results:
        exit_code = r.get("exit_code", -1)
        icon = "✅" if exit_code == 0 else "❌"
        header = f"─── {r['alias']} ({r.get('ip', '?')}) ── {icon} exit {exit_code}"
        if "elapsed_s" in r:
            header += f" ── {r['elapsed_s']}s"
        header += " ───"
        lines.append(header)
        if r.get("stdout"):
            lines.append(r["stdout"].rstrip())
        if r.get("stderr"):
            lines.append(f"[stderr] {r['stderr'].rstrip()}")
        if r.get("error"):
            lines.append(f"[error] {r['error']}")
        lines.append("")
        if exit_code == 0:
            ok += 1
        else:
            failed += 1

    lines.append(f"**📋 Summary: {ok} success / {failed} failed** out of {len(results)} host(s)")
    return "\n".join(lines)


@mcp.tool()
def upload_file(alias: str, local_path: str, remote_path: str) -> str:
    """
    Upload a local file to a remote host via SFTP.
    Auto-logged to exec.log.
    """
    try:
        r = ssh_tools.sftp_upload(alias, local_path, remote_path)
        return (
            f"✓ Uploaded {r['bytes_transferred']:,} bytes "
            f"to {alias}:{remote_path} in {r['elapsed_s']}s."
        )
    except Exception as e:
        return f"❌ Upload failed: {e}"


@mcp.tool()
def download_file(alias: str, remote_path: str, local_path: str) -> str:
    """
    Download a file from a remote host to a local path via SFTP.
    Auto-logged to exec.log.
    """
    try:
        r = ssh_tools.sftp_download(alias, remote_path, local_path)
        return (
            f"✓ Downloaded {r['bytes_transferred']:,} bytes "
            f"from {alias}:{remote_path} to {local_path} in {r['elapsed_s']}s."
        )
    except Exception as e:
        return f"❌ Download failed: {e}"


# ─── CONNECTIVITY ─────────────────────────────────────────────────────────────

@mcp.tool()
def ping_hosts(target: str = "all") -> str:
    """
    Ping hosts to check ICMP reachability.
    target: alias | project name | 'all'
    ⚠️ Always ensure VPN is connected before pinging private-subnet hosts.
    """
    try:
        hosts = vms.resolve_target(target)
    except vms.HostNotFound as e:
        return f"❌ {e}"

    if not hosts:
        return "No hosts to ping."

    aliases = [h["alias"] for h in hosts]
    results = ping_tools.ping_hosts(aliases)
    return ping_tools.format_ping_results(results)


# ─── TEMPLATES ────────────────────────────────────────────────────────────────

@mcp.tool()
def list_templates() -> str:
    """List all command templates defined in vms.yaml."""
    templates = vms.load_templates()
    if not templates:
        return "No templates defined. Use add_template to create one."
    lines = ["| Name | Command |", "|---|---|"]
    for name, cmd in templates.items():
        lines.append(f"| `{name}` | `{cmd}` |")
    return "\n".join(lines)


@mcp.tool()
def expand_template(name: str, alias: str) -> str:
    """
    Expand a template command string with {{alias}} substitution.
    Use this to preview the resolved command before running it.
    """
    try:
        return vms.expand_template(name, alias)
    except KeyError as e:
        return f"❌ {e}"


@mcp.tool()
def add_template(name: str, command: str) -> str:
    """Add or update a named command template in vms.yaml. Supports {{alias}} placeholder."""
    vms.write_template(name, command)
    return f"✓ Template '{name}' saved: `{command}`"


@mcp.tool()
def remove_template(name: str) -> str:
    """Remove a command template from vms.yaml."""
    try:
        vms.delete_template(name)
        return f"✓ Template '{name}' removed."
    except KeyError as e:
        return f"❌ {e}"


# ─── EXECUTION LOG ────────────────────────────────────────────────────────────

@mcp.tool()
def read_exec_log(n: int = 50) -> str:
    """Show the last N entries from the execution log as a markdown table."""
    entries = exec_log.read(n)
    return exec_log.format_log_table(entries)


@mcp.tool()
def clear_exec_log() -> str:
    """Clear all entries from the execution log (exec.log). Asks for confirmation via return value."""
    exec_log.clear()
    return "✓ Execution log cleared."


@mcp.tool()
def save_output(content: str, label: str, command: str) -> str:
    """
    Save command output to a timestamped file in /app/data/output/.
    label: alias or project name — used in the filename.
    Returns the full path of the saved file.
    """
    data_dir = Path(os.getenv("DATA_DIR", "/app/data"))
    output_dir = data_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d-%H%M")
    cmd_brief = re.sub(r"[^\w-]", "-", command.split()[0] if command else "output")[:20]
    filename = f"{label}_{cmd_brief}_{ts}.txt"
    path = output_dir / filename

    header = (
        f"# Remote Execution Output\n"
        f"# Timestamp: {datetime.now().isoformat()}\n"
        f"# Command:   {command}\n"
        f"# Label:     {label}\n"
        f"# {'─' * 40}\n\n"
    )
    path.write_text(header + content, encoding="utf-8")
    return f"✓ Output saved to /app/data/output/{filename}"


# ─── HEALTH CHECK ─────────────────────────────────────────────────────────────

@mcp.tool()
def health_check(alias: str) -> str:
    """
    Full health check for a host: ping → SSH → disk / CPU / memory snapshot.
    Returns a pass/fail report. Use this to quickly verify a host is reachable
    and accepting SSH connections before running commands.
    """
    try:
        host = vms.get_host(alias)
    except vms.HostNotFound as e:
        return f"❌ {e}"

    lines = [f"## Health Check: `{alias}` ({host['ip']})\n"]

    # 1. Ping
    ping_result = ping_tools.ping_host(host["ip"], port=host.get("port", 22))
    if not ping_result.get("up"):
        lines.append("| Check | Result |")
        lines.append("|---|---|")
        lines.append(f"| TCP:{host.get('port', 22)}  | ❌ UNREACHABLE |")
        lines.append("\n⚠️ Host SSH port is not reachable. Check VPN / firewall / that SSH is running.")
        return "\n".join(lines)

    lines.append("| Check | Result |")
    lines.append("|---|---|")
    lines.append(f"| TCP:{host.get('port', 22)}  | ✅ OK |")

    # 2. SSH + quick metrics
    try:
        import time as _time
        t0 = _time.monotonic()
        r = ssh_tools.ssh_exec(
            alias,
            "uptime && printf 'MEM: ' && free -m | awk '/Mem:/{printf \"%s/%s MB\\n\",$3,$2}' "
            "&& printf 'DISK: ' && df -h / | awk 'NR==2{print $3\"/\"$2\" (\"$5\")\"}'",
            timeout=10,
        )
        elapsed = round(_time.monotonic() - t0, 1)
        lines.append(f"| SSH   | ✅ OK (exit {r['exit_code']}, {elapsed}s) |")
        if r.get("stdout"):
            lines.append(f"\n```\n{r['stdout'].strip()}\n```")
    except ssh_tools.CredentialNotFound as e:
        lines.append(f"| SSH   | ❌ No credential stored — run `save_credential({alias!r}, ...)` |")
        lines.append(f"\n{e}")
    except ssh_tools.AuthFailure as e:
        lines.append(f"| SSH   | ❌ Authentication failed |")
        lines.append(f"\n{e}")
    except ssh_tools.HostUnreachable as e:
        lines.append(f"| SSH   | ❌ Port unreachable (firewall?) |")
        lines.append(f"\n{e}")
    except ssh_tools.CommandTimeout:
        lines.append(f"| SSH   | ⏱ Timed out (10s) |")
    except Exception as e:
        lines.append(f"| SSH   | ❌ {e} |")

    return "\n".join(lines)


# ─── OLLAMA / LOCAL LLM ───────────────────────────────────────────────────────

_OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://host.docker.internal:11434")
_OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")


def _ollama_available() -> bool:
    """Return True if Ollama is reachable at OLLAMA_URL."""
    try:
        urllib.request.urlopen(f"{_OLLAMA_URL}/api/tags", timeout=2)
        return True
    except Exception:
        return False


def _ollama_chat(prompt: str, system: str = "") -> str:
    """Send a chat message to the local Ollama model and return the response text."""
    payload = _json.dumps({
        "model": _OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system or "You are an expert DevOps/AIOps assistant."},
            {"role": "user",   "content": prompt},
        ],
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 4096},
    }).encode()
    req = urllib.request.Request(
        f"{_OLLAMA_URL}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = _json.loads(resp.read())
    return result["message"]["content"].strip()


@mcp.tool()
def ai_analyze(alias: str, question: str) -> str:
    """
    Run a diagnostic command on a remote host then use the local Ollama LLM to analyze the output.
    Examples: 'analyze disk usage on web01', 'explain errors in logs on db01'.
    Requires Ollama running at OLLAMA_URL (default: http://host.docker.internal:11434).
    Set OLLAMA_URL and OLLAMA_MODEL in docker-compose.yml to configure.
    """
    if not _ollama_available():
        return (
            f"❌ Ollama not reachable at {_OLLAMA_URL}\n"
            "Start Ollama on the host machine: `ollama serve`\n"
            "Then ensure OLLAMA_URL=http://host.docker.internal:11434 in docker-compose.yml"
        )

    q = question.lower()
    if any(w in q for w in ["disk", "space", "storage"]):
        command = "df -h && du -sh /* 2>/dev/null | sort -rh | head -20"
    elif any(w in q for w in ["memory", "mem", "ram"]):
        command = "free -m && ps aux --sort=-%mem | head -15"
    elif any(w in q for w in ["cpu", "load", "process"]):
        command = "uptime && ps aux --sort=-%cpu | head -15"
    elif any(w in q for w in ["log", "error", "fail"]):
        command = "journalctl -n 100 --no-pager -p err 2>/dev/null || tail -100 /var/log/syslog 2>/dev/null"
    elif any(w in q for w in ["network", "connection", "port"]):
        command = "ss -tulnp"
    elif any(w in q for w in ["service", "systemd", "running"]):
        command = "systemctl --failed"
    else:
        command = "uptime && free -m && df -h && ps aux --sort=-%cpu | head -10"

    try:
        r = ssh_tools.ssh_exec(alias, command)
    except ssh_tools.CredentialNotFound as e:
        return f"❌ {e}"
    except ssh_tools.HostUnreachable as e:
        return f"⚠️ {e}"
    except vms.HostNotFound as e:
        return f"❌ {e}"
    except Exception as e:
        return f"❌ SSH failed: {e}"

    raw_output = (r.get("stdout") or "") + (r.get("stderr") or "")
    if not raw_output.strip():
        return f"⚠️ No output from '{alias}' for command: {command}"

    system_prompt = (
        "You are an expert Linux sysadmin and AIOps engineer. "
        "Analyze the system output and answer the question concisely. "
        "Highlight any issues and suggest actionable fixes."
    )
    user_prompt = (
        f"Host: {alias} ({r.get('ip', '?')})\n"
        f"Question: {question}\n\n"
        f"Command: {command}\n\n"
        f"Output:\n{raw_output[:3000]}"
    )

    try:
        analysis = _ollama_chat(user_prompt, system_prompt)
    except Exception as e:
        return f"❌ Ollama analysis failed: {e}\n\nRaw output:\n{raw_output}"

    return (
        f"## 🤖 AI Analysis: `{alias}`\n"
        f"**Question:** {question}\n"
        f"**Command:** `{command}`\n\n"
        f"### Analysis\n{analysis}\n\n"
        f"---\n"
        f"*Model: {_OLLAMA_MODEL} @ {_OLLAMA_URL}*"
    )


@mcp.tool()
def ollama_status() -> str:
    """Check if the local Ollama LLM is running and show which models are loaded in VRAM."""
    if not _ollama_available():
        return (
            f"❌ Ollama not reachable at {_OLLAMA_URL}\n"
            "Start with: `ollama serve` on the host machine."
        )
    try:
        with urllib.request.urlopen(f"{_OLLAMA_URL}/api/ps", timeout=5) as resp:
            data = _json.loads(resp.read())
        models = data.get("models", [])
        if not models:
            return (
                f"✅ Ollama running at {_OLLAMA_URL}\n"
                f"⚠️  No models loaded in VRAM (idle)\n"
                f"Configured model: `{_OLLAMA_MODEL}`"
            )
        lines = [f"✅ Ollama running — {len(models)} model(s) in VRAM:\n"]
        for m in models:
            vram_gb = round(m.get("size", 0) / 1e9, 1)
            lines.append(f"- `{m['name']}` — {vram_gb} GB VRAM")
        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ Ollama running but status check failed: {e}"


# ─── ENTRYPOINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8765"))
    api_key = os.getenv("MCP_API_KEY", "").strip()
    dashboard_enabled = os.getenv("MCP_DASHBOARD", "true").lower() not in ("0", "false", "no")

    print(f"Starting Remote Executor MCP server on {host}:{port}")
    if api_key:
        print("Authentication: ENABLED (X-MCP-Key header required)")
    else:
        print("Authentication: DISABLED (set MCP_API_KEY in .env to enable)")
    print(f"Connect clients to: http://localhost:{port}/sse")
    if dashboard_enabled:
        print(f"Dashboard:          http://localhost:{port}/dashboard")
    else:
        print("Dashboard:          DISABLED (MCP_DASHBOARD=false)")

    # Wrap FastMCP SSE app with API key middleware + optional dashboard router
    try:
        sse_app = mcp.sse_app()
    except AttributeError:
        sse_app = mcp.get_asgi_app(transport="sse")

    if dashboard_enabled:
        app = dash.RouterApp(APIKeyMiddleware(sse_app))
    else:
        app = APIKeyMiddleware(sse_app)

    uvicorn.run(app, host=host, port=port)

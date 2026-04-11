"""
Remote Executor MCP Server

Exposes SSH remote execution tools via FastMCP (HTTP/SSE transport).
Connect clients to: http://localhost:8765/sse

Tools are grouped by category:
  Host management  — list_hosts, add_host, remove_host, update_host
  Credentials      — save_credential, check_credential, delete_credential, audit_credentials
  Execution        — run_command, run_command_multi, upload_file, download_file
  Connectivity     — ping_hosts
  Templates        — list_templates, expand_template, add_template, remove_template
  Log              — read_exec_log, clear_exec_log, save_output
"""
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import uvicorn
from fastmcp import FastMCP

import credentials as creds
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
            query = scope.get("query_string", b"").decode()
            for param in query.split("&"):
                if param.startswith("api_key="):
                    provided = param[8:]
                    break

        if provided != api_key:
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
    tags: list[str] = [],
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


# ─── ENTRYPOINT ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8765"))
    api_key = os.getenv("MCP_API_KEY", "").strip()

    print(f"Starting Remote Executor MCP server on {host}:{port}")
    if api_key:
        print("Authentication: ENABLED (X-MCP-Key header required)")
    else:
        print("Authentication: DISABLED (set MCP_API_KEY in .env to enable)")
    print(f"Connect clients to: http://localhost:{port}/sse")

    # Wrap FastMCP SSE app with API key middleware
    try:
        sse_app = mcp.sse_app()
    except AttributeError:
        sse_app = mcp.get_asgi_app(transport="sse")

    uvicorn.run(APIKeyMiddleware(sse_app), host=host, port=port)

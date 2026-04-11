#!/usr/bin/env python3
"""
Remote Executor MCP Server — Deployment Script
Supports Windows and Linux.

Usage:
  python deploy.py              # full deploy (init + build + start + verify)
  python deploy.py --pull       # use pre-built Docker Hub image instead of local build
  python deploy.py --restart    # restart existing container only
  python deploy.py --status     # check if server is running
"""
import argparse
import platform
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).parent
DOCKER_IMAGE = "rajkob/mcp-remote-executor:latest"
MCP_PORT = 8765
MCP_HOST = "127.0.0.1"
MCP_URL = f"http://{MCP_HOST}:{MCP_PORT}/sse"

IS_WINDOWS = platform.system() == "Windows"

# ── ANSI colours (disabled on Windows without ANSI support) ──────────────────
GREEN  = "\033[92m" if not IS_WINDOWS else ""
YELLOW = "\033[93m" if not IS_WINDOWS else ""
RED    = "\033[91m" if not IS_WINDOWS else ""
CYAN   = "\033[96m" if not IS_WINDOWS else ""
BOLD   = "\033[1m"  if not IS_WINDOWS else ""
RESET  = "\033[0m"  if not IS_WINDOWS else ""

# Enable ANSI on Windows 10+
if IS_WINDOWS:
    import ctypes
    kernel32 = ctypes.windll.kernel32
    if kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7):
        GREEN = "\033[92m"; YELLOW = "\033[93m"; RED = "\033[91m"
        CYAN  = "\033[96m"; BOLD   = "\033[1m";  RESET = "\033[0m"


def ok(msg):   print(f"{GREEN}✓{RESET} {msg}")
def warn(msg): print(f"{YELLOW}⚠{RESET}  {msg}")
def err(msg):  print(f"{RED}✗{RESET} {msg}")
def info(msg): print(f"{CYAN}→{RESET} {msg}")
def head(msg): print(f"\n{BOLD}{msg}{RESET}")


def run(cmd: list[str], capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
    )


# ── 1. Prerequisites ─────────────────────────────────────────────────────────

def check_prerequisites() -> bool:
    head("[ 1 / 5 ]  Prerequisites")
    all_ok = True

    # Docker
    try:
        r = run(["docker", "info"], capture=True, check=False)
        if r.returncode == 0:
            ok("Docker is running.")
        else:
            err("Docker daemon is not running. Start Docker Desktop and retry.")
            all_ok = False
    except FileNotFoundError:
        err("Docker not found. Install Docker Desktop from https://www.docker.com/products/docker-desktop")
        all_ok = False

    # Docker Compose
    try:
        r = run(["docker", "compose", "version"], capture=True, check=False)
        if r.returncode == 0:
            ok(f"Docker Compose: {r.stdout.strip()}")
        else:
            err("Docker Compose plugin not found.")
            all_ok = False
    except FileNotFoundError:
        err("Docker Compose not found.")
        all_ok = False

    return all_ok


# ── 2. WSL2 networking (Windows only) ────────────────────────────────────────

def check_wslconfig() -> bool:
    """Returns True if WSL was reconfigured and needs a restart."""
    if not IS_WINDOWS:
        return False

    wslconfig = Path.home() / ".wslconfig"
    content = wslconfig.read_text(encoding="utf-8") if wslconfig.exists() else ""

    if re.search(r"^\s*networkingMode\s*=\s*mirrored", content, re.MULTILINE | re.IGNORECASE):
        ok("WSL2 mirrored networking already configured.")
        return False

    warn("networkingMode=mirrored missing — applying...")
    if re.search(r"^\[wsl2\]", content, re.MULTILINE | re.IGNORECASE):
        content = re.sub(
            r"(\[wsl2\][^\n]*\n)",
            r"\1networkingMode=mirrored\n",
            content, count=1, flags=re.IGNORECASE,
        )
    else:
        content = content.rstrip() + "\n\n[wsl2]\nnetworkingMode=mirrored\n"

    wslconfig.write_text(content, encoding="utf-8")
    ok(f"Updated: {wslconfig}")

    # Shut down WSL2
    info("Shutting down WSL2 (wsl --shutdown)...")
    r = subprocess.run(["wsl", "--shutdown"], capture_output=True, text=True, timeout=15)
    if r.returncode == 0:
        ok("WSL2 shut down.")
        warn("Please restart Docker Desktop now, then re-run this script.")
    else:
        warn(f"wsl --shutdown failed (code {r.returncode}). Restart WSL2 manually.")
    return True


def wsl_check_step() -> bool:
    """Returns False if script should abort (WSL restart needed)."""
    head("[ 2 / 5 ]  WSL2 Networking (Windows)")
    if not IS_WINDOWS:
        ok("Linux host — WSL2 check skipped.")
        return True
    restarted = check_wslconfig()
    if restarted:
        err("Docker Desktop must be restarted before continuing.")
        err("Restart Docker Desktop, then run: python deploy.py")
        return False
    return True


# ── 3. Initialisation ─────────────────────────────────────────────────────────

def run_init() -> bool:
    head("[ 3 / 6 ]  Initialisation")
    try:
        from cryptography.fernet import Fernet  # noqa: F401
    except ImportError:
        warn("cryptography not installed locally — installing...")
        run([sys.executable, "-m", "pip", "install", "cryptography", "-q"])

    # Run init.py
    init_script = BASE_DIR / "init.py"
    if not init_script.exists():
        err(f"init.py not found in {BASE_DIR}")
        return False

    r = subprocess.run([sys.executable, str(init_script)], cwd=BASE_DIR)
    return r.returncode == 0


# ── 4. API Key Setup ──────────────────────────────────────────────────────────

def setup_api_key() -> str:
    """
    Interactively configure MCP_API_KEY in .env and docker-compose.yml.
    Returns the configured key (empty string = auth disabled).
    """
    head("[ 4 / 6 ]  API Key Authentication")

    env_file = BASE_DIR / ".env"
    compose_file = BASE_DIR / "docker-compose.yml"

    # Read current .env
    env_content = env_file.read_text(encoding="utf-8") if env_file.exists() else ""

    # Check if already configured
    existing = re.search(r"^MCP_API_KEY=(.+)$", env_content, re.MULTILINE)
    if existing and existing.group(1).strip():
        current_key = existing.group(1).strip()
        ok(f"API key already configured in .env (length: {len(current_key)} chars).")
        print(f"  → Auth is ENABLED. Use '{CYAN}python deploy.py --reset-key{RESET}' to regenerate.")
        return current_key

    # Ask user
    print(f"  Enable API key authentication?")
    print(f"  {YELLOW}Recommended{RESET} for shared/remote deployments. Safe to skip for local-only use.")
    print(f"  [Y] Generate a random key and enable auth")
    print(f"  [N] Skip — run without authentication")
    choice = input(f"\n  Your choice (Y/n): ").strip().lower()

    if choice in ("n", "no"):
        warn("Authentication DISABLED — ensure port 8765 is firewalled to trusted networks only.")
        return ""

    # Generate key
    api_key = secrets.token_urlsafe(32)

    # Write to .env
    if "MCP_API_KEY=" in env_content:
        env_content = re.sub(r"^MCP_API_KEY=.*$", f"MCP_API_KEY={api_key}", env_content, flags=re.MULTILINE)
    else:
        env_content = env_content.rstrip() + f"\nMCP_API_KEY={api_key}\n"
    env_file.write_text(env_content, encoding="utf-8")
    ok(f"API key written to .env")

    # Uncomment MCP_API_KEY line in docker-compose.yml
    if compose_file.exists():
        dc = compose_file.read_text(encoding="utf-8")
        dc = dc.replace(
            "      # - MCP_API_KEY=${MCP_API_KEY}",
            "      - MCP_API_KEY=${MCP_API_KEY}"
        )
        compose_file.write_text(dc, encoding="utf-8")
        ok("docker-compose.yml updated — MCP_API_KEY enabled.")

    print(f"\n  {BOLD}Your API key:{RESET} {CYAN}{api_key}{RESET}")
    print(f"  {YELLOW}Copy this — you'll need it in your client config.{RESET}")
    return api_key


# ── 5. Build / pull + start ───────────────────────────────────────────────────

def deploy(use_pull: bool = False) -> bool:
    head("[ 5 / 6 ]  Docker Deploy")

    compose_file = BASE_DIR / "docker-compose.yml"
    if not compose_file.exists():
        err(f"docker-compose.yml not found in {BASE_DIR}")
        return False

    if use_pull:
        info(f"Pulling image {DOCKER_IMAGE} ...")
        # Patch compose to use pre-built image instead of building
        r = run(
            ["docker", "pull", DOCKER_IMAGE],
            capture=False, check=False,
        )
        if r.returncode != 0:
            err("docker pull failed.")
            return False
        ok(f"Pulled {DOCKER_IMAGE}")
    else:
        info("Building image from source...")
        r = run(
            ["docker", "compose", "build"],
            capture=False, check=False,
        )
        if r.returncode != 0:
            err("docker compose build failed.")
            return False
        ok("Image built.")

    info("Starting container (docker compose up -d)...")
    r = run(
        ["docker", "compose", "up", "-d"],
        capture=False, check=False,
    )
    if r.returncode != 0:
        err("docker compose up failed.")
        return False

    ok("Container started.")
    return True


def restart_only() -> bool:
    head("Restarting container")
    r = run(["docker", "compose", "restart"], capture=False, check=False)
    return r.returncode == 0


# ── 5. Health check ───────────────────────────────────────────────────────────

def health_check(timeout: int = 30) -> bool:
    head("[ 6 / 6 ]  Health Check")
    info(f"Waiting for MCP server at {MCP_URL} (up to {timeout}s)...")

    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            # SSE endpoint returns 200 and keeps connection open
            # We just need to confirm the port accepts connections
            sock = socket.create_connection((MCP_HOST, MCP_PORT), timeout=2)
            sock.close()

            # Send a real HTTP GET to confirm FastMCP responds
            req = urllib.request.Request(MCP_URL, headers={"Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                status = resp.status
                ct = resp.headers.get("Content-Type", "")
                if status == 200 and "event-stream" in ct:
                    ok(f"Server is UP — {MCP_URL} responded with 200 text/event-stream")
                    return True
                else:
                    ok(f"Server is UP — HTTP {status} (Content-Type: {ct})")
                    return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            print(f"  Attempt {attempt} — not ready yet, retrying...", end="\r")
            time.sleep(2)
        except Exception as e:
            # HTTP error (e.g. 405) still means server is up
            if "urlopen error" not in str(e).lower():
                ok(f"Server is UP — responded ({e})")
                return True
            time.sleep(2)

    err(f"Server did not respond within {timeout}s.")
    err("Check logs: docker compose logs remote-executor")
    return False


def check_status() -> bool:
    head("Server Status")
    try:
        sock = socket.create_connection((MCP_HOST, MCP_PORT), timeout=3)
        sock.close()
        ok(f"MCP server is RUNNING on port {MCP_PORT}")
        return True
    except Exception:
        err(f"MCP server NOT reachable on port {MCP_PORT}")
        r = subprocess.run(
            ["docker", "compose", "ps"],
            cwd=BASE_DIR, capture_output=True, text=True,
        )
        print(r.stdout)
        return False


# ── Integration info ──────────────────────────────────────────────────────────

def print_integration_info(api_key: str = ""):
    w = 60
    print()
    print("=" * w)
    print(f"{BOLD}  MCP Server is ready — Integration Guide{RESET}")
    print("=" * w)

    auth_note = ""
    header_snippet = ""
    npx_key_note = ""
    if api_key:
        auth_note = f"\n{BOLD}API Key:{RESET}     {CYAN}{api_key}{RESET}  {YELLOW}← keep this safe{RESET}"
        header_snippet = f'\n         "headers": {{ "X-MCP-Key": "{api_key}" }}'
        npx_key_note = (
            f"\n       Note: mcp-remote supports headers via env var:\n"
            f"       MCP_REMOTE_HEADER_X_MCP_KEY={api_key}"
        )

    print(f"""
{BOLD}Server URL:{RESET}  {CYAN}http://localhost:{MCP_PORT}/sse{RESET}{auth_note}

{BOLD}── VS Code (GitHub Copilot Agent) ──{RESET}
1. Open Command Palette → "Preferences: Open User Settings (JSON)"
2. Add:

   {CYAN}"mcp": {{
     "servers": {{
       "remote-executor": {{
         "type": "sse",
         "url": "http://localhost:{MCP_PORT}/sse"{header_snippet}
       }}
     }}
   }}{RESET}

3. Restart VS Code.
4. Open Copilot Chat → switch to {BOLD}Agent mode{RESET}.
5. Type: {CYAN}List all hosts{RESET}

{BOLD}── Claude Desktop ──{RESET}
Add to %APPDATA%\\Claude\\claude_desktop_config.json:

   {CYAN}"mcpServers": {{
     "remote-executor": {{
       "command": "npx",
       "args": ["-y", "mcp-remote", "http://localhost:{MCP_PORT}/sse"]
     }}
   }}{RESET}{npx_key_note}

Restart Claude Desktop.

{BOLD}── Continue.dev ──{RESET}
Add to ~/.continue/config.json:

   {CYAN}"experimental": {{
     "modelContextProtocolServers": [{{
       "transport": {{
         "type": "sse",
         "url": "http://localhost:{MCP_PORT}/sse"
       }}
     }}]
   }}{RESET}

{BOLD}── First commands to try ──{RESET}
  "List all hosts"
  "Add host web01 with IP <your-host-ip> to project CORE, user <your-user>"
  "Save credential for web01"
  "Check disk usage on web01"
  "Ping all hosts"

{BOLD}── Useful Docker commands ──{RESET}
  docker compose logs -f remote-executor   {YELLOW}# live logs{RESET}
  docker compose restart remote-executor   {YELLOW}# restart{RESET}
  docker compose down                      {YELLOW}# stop{RESET}
  docker compose up -d                     {YELLOW}# start{RESET}

{BOLD}── Test VPN/SSH connectivity ──{RESET}
  docker compose run --rm -it remote-executor \\
    python test_connectivity.py <ip> <user>
""")
    print("=" * w)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Remote Executor MCP Server — Deploy")
    parser.add_argument("--pull",    action="store_true", help="Use pre-built Docker Hub image")
    parser.add_argument("--restart", action="store_true", help="Restart existing container only")
    parser.add_argument("--status",  action="store_true", help="Check if server is running")
    args = parser.parse_args()

    print(f"\n{BOLD}Remote Executor MCP Server — Deployment{RESET}")
    print(f"Platform: {platform.system()} {platform.release()}")
    print(f"Project : {BASE_DIR}")

    if args.status:
        check_status()
        return

    if args.restart:
        if restart_only():
            ok("Container restarted.")
            api_key = _read_api_key_from_env()
            health_check()
            print_integration_info(api_key)
        else:
            err("Restart failed. Run: docker compose logs remote-executor")
        return

    # Full deployment — run each step, collect api_key from step 4
    api_key = ""

    if not check_prerequisites():
        print(f"\n{RED}Deployment stopped — see errors above.{RESET}\n")
        sys.exit(1)

    if not wsl_check_step():
        print(f"\n{RED}Deployment stopped — see errors above.{RESET}\n")
        sys.exit(1)

    if not run_init():
        print(f"\n{RED}Deployment stopped — see errors above.{RESET}\n")
        sys.exit(1)

    api_key = setup_api_key()  # step 4 — may be empty if user skips auth

    if not deploy(use_pull=args.pull):
        print(f"\n{RED}Deployment stopped — see errors above.{RESET}\n")
        sys.exit(1)

    if not health_check(timeout=30):
        print(f"\n{RED}Deployment stopped — see errors above.{RESET}\n")
        sys.exit(1)

    print_integration_info(api_key)


def _read_api_key_from_env() -> str:
    """Read MCP_API_KEY from .env file if present."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return ""
    m = re.search(r"^MCP_API_KEY=(.+)$", env_file.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1).strip() if m else ""


if __name__ == "__main__":
    main()

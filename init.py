#!/usr/bin/env python3
"""
First-run setup helper for Remote Executor MCP Server.

Run this ONCE before starting Docker:
  python init.py

What it does:
  1. Checks and configures WSL2 mirrored networking (Windows only)
  2. Creates data/ directory structure
  3. Writes empty data/vms.yaml skeleton
  4. Writes empty data/credentials file
  5. Generates a random CRED_MASTER_KEY and writes it to .env
"""
import os
import platform
import re
import subprocess
from pathlib import Path
from cryptography.fernet import Fernet

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = DATA_DIR / "output"
VMS_FILE = DATA_DIR / "vms.yaml"
CRED_FILE = DATA_DIR / "credentials"
ENV_FILE = BASE_DIR / ".env"

VMS_SKELETON = """\
defaults:
  user: root
  port: 22
  timeout: 30

templates:
  disk:            "df -h"
  memory:          "free -h"
  uptime:          "uptime"
  cpu:             "top -bn1 | grep 'Cpu(s)'"
  who:             "who"
  os-version:      "cat /etc/os-release"
  failed-services: "systemctl --failed"
  netstat:         "ss -tlnp"

projects: {}
"""


def check_wslconfig() -> bool:
    """
    On Windows: ensure ~/.wslconfig has [wsl2] networkingMode=mirrored.
    Returns True if a restart is needed (change was made).
    """
    if platform.system() != "Windows":
        return False

    wslconfig = Path.home() / ".wslconfig"
    needs_restart = False

    if wslconfig.exists():
        content = wslconfig.read_text(encoding="utf-8")
    else:
        content = ""

    # Check if networkingMode=mirrored already present
    if re.search(r"^\s*networkingMode\s*=\s*mirrored", content, re.MULTILINE | re.IGNORECASE):
        print("✓ WSL2 mirrored networking already configured.")
        return False

    print("  networkingMode=mirrored not found in ~/.wslconfig — applying...")

    # Add [wsl2] section if missing, or append under existing [wsl2]
    if re.search(r"^\[wsl2\]", content, re.MULTILINE | re.IGNORECASE):
        # Insert after [wsl2] line
        content = re.sub(
            r"(\[wsl2\][^\n]*\n)",
            r"\1networkingMode=mirrored\n",
            content,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        # Append new section
        content = content.rstrip() + "\n\n[wsl2]\nnetworkingMode=mirrored\n"

    wslconfig.write_text(content, encoding="utf-8")
    print(f"✓ Updated: {wslconfig}")
    needs_restart = True
    return needs_restart


def restart_wsl() -> None:
    """Shut down WSL2 so the new .wslconfig takes effect."""
    print("  Shutting down WSL2 (wsl --shutdown) ...")
    try:
        result = subprocess.run(
            ["wsl", "--shutdown"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            print("✓ WSL2 shut down. Please restart Docker Desktop manually.")
        else:
            print(f"⚠️  wsl --shutdown returned code {result.returncode}: {result.stderr.strip()}")
            print("   Restart WSL2 manually: wsl --shutdown")
    except FileNotFoundError:
        print("⚠️  'wsl' command not found. Are you running on Windows with WSL2 installed?")
    except subprocess.TimeoutExpired:
        print("⚠️  wsl --shutdown timed out. Restart WSL2 manually.")


def main():
    print("Remote Executor MCP Server — first-run setup")
    print("=" * 55)

    # Step 1 — WSL2 mirrored networking (Windows only)
    print("\n[1/4] WSL2 networking check")
    wsl_changed = check_wslconfig()
    if wsl_changed:
        restart_wsl()
        print("  ⚠️  Restart Docker Desktop before running 'docker compose up'.")

    print("\n[2/4] Data directories")
    # Create data directories
    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"✓ Data directory: {DATA_DIR}")

    # Write vms.yaml skeleton
    print("\n[3/4] Host inventory")
    if not VMS_FILE.exists() or VMS_FILE.stat().st_size == 0:
        VMS_FILE.write_text(VMS_SKELETON)
        print(f"✓ Created: {VMS_FILE}")
    else:
        print(f"  Skipped (exists): {VMS_FILE}")

    # Write empty credentials file
    if not CRED_FILE.exists() or CRED_FILE.stat().st_size == 0:
        CRED_FILE.write_bytes(b"{}")
        print(f"✓ Created: {CRED_FILE}")
    else:
        print(f"  Skipped (exists): {CRED_FILE}")

    # Generate master key and write .env
    print("\n[4/4] Encryption key")
    key = Fernet.generate_key().decode()
    if ENV_FILE.exists():
        print(f"\n⚠️  .env already exists — not overwritten.")
        print(f"   If you need a new key, manually replace CRED_MASTER_KEY in: {ENV_FILE}")
        print(f"\n   Generated key (not saved):\n   CRED_MASTER_KEY={key}")
    else:
        ENV_FILE.write_text(f"CRED_MASTER_KEY={key}\n")
        print(f"\n✓ Created: {ENV_FILE}")
        print(f"  ⚠️  Keep this file safe — it encrypts all stored credentials.")
        print(f"  ⚠️  Never commit it to git (already in .gitignore).")

    print()
    print("=" * 55)
    print("Setup complete. Next steps:")
    if wsl_changed:
        print("  0. Restart Docker Desktop  ← required (WSL2 was reconfigured)")
    print("  1. docker compose build")
    print("  2. docker compose up -d")
    print("  3. curl http://localhost:8765/sse   (verify server is running)")
    print("  4. Add clients: see INSTALL.md for VS Code config")
    print()


if __name__ == "__main__":
    main()

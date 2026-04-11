#!/usr/bin/env python3
"""
VPN/SSH connectivity test — run inside Docker container after first start.

Tests three levels:
  Level 1: ICMP ping
  Level 2: TCP port 22 reachable
  Level 3: paramiko SSH authentication

Usage (run from host):
  docker compose run --rm -it remote-executor python test_connectivity.py <ip> <user> [port]

Examples:
  docker compose run --rm -it remote-executor python test_connectivity.py 192.168.1.100 admin
  docker compose run --rm -it remote-executor python test_connectivity.py 192.168.1.100 admin 22
"""
import getpass
import platform
import socket
import subprocess
import sys

import paramiko


def test_ping(host: str) -> bool:
    system = platform.system()
    cmd = ["ping", "-n" if system == "Windows" else "-c", "2", host]
    print(f"\nLevel 1 — ICMP ping {host} ...", end=" ", flush=True)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=8)
        if result.returncode == 0:
            print("✓ REACHABLE")
            return True
        else:
            print("✗ NO RESPONSE (host down, VPN not connected, or ICMP blocked)")
            return False
    except subprocess.TimeoutExpired:
        print("✗ TIMEOUT")
        return False


def test_port(host: str, port: int = 22, timeout: int = 5) -> bool:
    print(f"Level 2 — TCP {host}:{port} ...", end=" ", flush=True)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            print("✓ PORT OPEN")
            return True
    except Exception as e:
        print(f"✗ FAILED: {e}")
        return False


def test_ssh(host: str, user: str, port: int = 22) -> bool:
    password = getpass.getpass(f"\nLevel 3 — Enter SSH password for {user}@{host}: ")
    print(f"Connecting ...", end=" ", flush=True)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, port=port, username=user, password=password, timeout=10)
        _, stdout, _ = client.exec_command("uptime")
        output = stdout.read().decode().strip()
        print("✓ SUCCESS")
        print(f"Output: {output}")
        return True
    except paramiko.AuthenticationException:
        print("✗ Authentication failed (wrong password?)")
        return False
    except Exception as e:
        print(f"✗ FAILED: {e}")
        return False
    finally:
        client.close()


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else input("Host IP: ").strip()
    user = sys.argv[2] if len(sys.argv) > 2 else input("SSH user: ").strip()
    port = int(sys.argv[3]) if len(sys.argv) > 3 else 22

    print(f"\n{'=' * 50}")
    print(f"Connectivity Test: {user}@{host}:{port}")
    print(f"{'=' * 50}")

    ping_ok = test_ping(host)
    port_ok = test_port(host, port)

    if not ping_ok and not port_ok:
        print("\n✗ Both ping and TCP failed.")
        print("  → Check VPN connection (if host is on a private subnet)")
        print("  → Verify network_mode: host is working (see docker-compose.yml)")
        print("  → Try WSL2 mirrored networking: add networkingMode=mirrored to ~/.wslconfig")
        sys.exit(1)
    elif port_ok:
        test_ssh(host, user, port)
    else:
        print("\nLevel 2 failed — SSH port unreachable. Skipping Level 3.")
        print("  → ICMP works but TCP port 22 blocked — check firewall rules on host.")
        sys.exit(1)

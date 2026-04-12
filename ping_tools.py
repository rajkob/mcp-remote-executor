"""
Host reachability check — TCP connect to SSH port.

Uses a pure-Python TCP socket connect to the host's SSH port instead of ICMP
ping, because ICMP is disabled by firewall on most cloud VMs and hardened hosts.
A successful TCP handshake means the SSH port is open and the host is usable.

Falls back to ICMP ping only when explicitly requested via ping_hosts_icmp().
All checks run in parallel via ThreadPoolExecutor.
"""
import platform
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor

import vms as vms_module


# ─── TCP connect check (primary) ─────────────────────────────────────────────

def _tcp_check(alias: str, ip: str, port: int = 22, timeout: int = 5) -> dict:
    """Connect to tcp:ip:port. Returns {alias, ip, port, up: bool}."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            up = True
    except OSError:
        up = False
    return {"alias": alias, "ip": ip, "port": port, "up": up}


def ping_host(ip: str, port: int = 22) -> dict:
    """
    Check reachability of a single IP via TCP connect to port (default 22).
    Returns {ip, port, up: bool}.
    """
    return _tcp_check("", ip, port)


def ping_hosts(aliases: list[str]) -> list[dict]:
    """
    Check reachability of hosts by alias in parallel using TCP connect to
    each host's configured SSH port.
    Returns list of {alias, ip, port, up: bool}.
    """
    host_data = []
    for alias in aliases:
        try:
            host = vms_module.get_host(alias)
            host_data.append((alias, host["ip"], host.get("port", 22)))
        except Exception:
            host_data.append((alias, None, 22))

    valid = [(a, ip, p) for a, ip, p in host_data if ip]
    invalid = [(a, ip, p) for a, ip, p in host_data if not ip]

    results = []
    if valid:
        with ThreadPoolExecutor(max_workers=min(len(valid), 30)) as pool:
            futures = [pool.submit(_tcp_check, alias, ip, port) for alias, ip, port in valid]
            for future in futures:
                results.append(future.result())

    for alias, _, _ in invalid:
        results.append({"alias": alias, "ip": "unknown", "port": 22, "up": False,
                        "error": "Host not found in vms.yaml"})

    return results


# ─── ICMP ping (legacy / explicit use only) ───────────────────────────────────

def _ping_icmp(alias: str, ip: str, count: int = 2, timeout: int = 5) -> dict:
    """ICMP ping via subprocess. Only use when ICMP is explicitly needed."""
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", str(count), "-w", str(timeout * 1000), ip]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout), ip]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout + 3)
        up = result.returncode == 0
    except subprocess.TimeoutExpired:
        up = False
    except FileNotFoundError:
        up = False

    return {"alias": alias, "ip": ip, "up": up}


def ping_hosts_icmp(aliases: list[str]) -> list[dict]:
    """ICMP (subprocess) ping — use only if hosts block TCP but allow ICMP."""
    host_pairs = []
    for alias in aliases:
        try:
            host = vms_module.get_host(alias)
            host_pairs.append((alias, host["ip"]))
        except Exception:
            host_pairs.append((alias, None))

    valid = [(a, ip) for a, ip in host_pairs if ip]
    invalid = [(a, ip) for a, ip in host_pairs if not ip]

    results = []
    if valid:
        with ThreadPoolExecutor(max_workers=min(len(valid), 30)) as pool:
            futures = [pool.submit(_ping_icmp, alias, ip) for alias, ip in valid]
            for future in futures:
                results.append(future.result())

    for alias, _ in invalid:
        results.append({"alias": alias, "ip": "unknown", "up": False,
                        "error": "Host not found in vms.yaml"})

    return results


# ─── Formatting ───────────────────────────────────────────────────────────────

def format_ping_results(results: list[dict]) -> str:
    """Format reachability results as a markdown table with summary."""
    lines = ["| Status | Alias | IP | Port |", "|---|---|---|---|"]
    for r in sorted(results, key=lambda x: x["alias"]):
        icon = "✅ UP" if r["up"] else "❌ DOWN"
        port = r.get("port", "")
        lines.append(f"| {icon} | {r['alias']} | {r['ip']} | {port} |")

    up = sum(1 for r in results if r["up"])
    down = len(results) - up
    lines.append(f"\n**{up} UP / {down} DOWN** out of {len(results)} host(s)")

    if down > 0:
        lines.append(
            "\n⚠️ Some hosts unreachable. Possible causes:\n"
            "  • VPN not connected\n"
            "  • Host is powered off\n"
            "  • SSH port blocked by firewall\n"
            "  • SSH not running on the host"
        )
    return "\n".join(lines)

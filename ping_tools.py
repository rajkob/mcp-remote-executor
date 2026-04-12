"""
Parallel ICMP ping — host reachability check.

Uses subprocess ping — works on Linux (inside Docker) and Windows.
All pings run in parallel via ThreadPoolExecutor.
"""
import platform
import subprocess
from concurrent.futures import ThreadPoolExecutor

import vms as vms_module


def _ping_one(alias: str, ip: str, count: int = 2, timeout: int = 5) -> dict:
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", str(count), "-w", str(timeout * 1000), ip]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout), ip]

    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout + 3
        )
        up = result.returncode == 0
    except subprocess.TimeoutExpired:
        up = False
    except FileNotFoundError:
        up = False

    return {"alias": alias, "ip": ip, "up": up}


def ping_host(ip: str) -> dict:
    """Ping a single IP address directly. Returns {ip, up: bool}."""
    return _ping_one("", ip)


def ping_hosts(aliases: list[str]) -> list[dict]:
    """
    Ping a list of hosts by alias in parallel.
    Returns list of {alias, ip, up: bool}.
    """
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
            futures = [pool.submit(_ping_one, alias, ip) for alias, ip in valid]
            for future in futures:
                results.append(future.result())

    for alias, _ in invalid:
        results.append({"alias": alias, "ip": "unknown", "up": False,
                        "error": "Host not found in vms.yaml"})

    return results


def format_ping_results(results: list[dict]) -> str:
    """Format ping results as a markdown table with summary."""
    lines = ["| Status | Alias | IP |", "|---|---|---|"]
    for r in sorted(results, key=lambda x: x["alias"]):
        icon = "✅ UP" if r["up"] else "❌ DOWN"
        lines.append(f"| {icon} | {r['alias']} | {r['ip']} |")

    up = sum(1 for r in results if r["up"])
    down = len(results) - up
    lines.append(f"\n**{up} UP / {down} DOWN** out of {len(results)} host(s)")

    if down > 0:
        lines.append(
            "\n⚠️ Some hosts unreachable. Possible causes:\n"
            "  • VPN not connected\n"
            "  • Host is powered off\n"
            "  • Firewall blocking ICMP"
        )
    return "\n".join(lines)

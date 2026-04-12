"""
monitor.py — SSH-based metric collection for the dashboard.

Collects CPU, memory, disk, uptime and ping reachability per host.
Results are cached for CACHE_TTL seconds to avoid hammering hosts.
"""
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import ping_tools
import ssh_tools
import vms

CACHE_TTL = 30          # seconds before metrics are re-fetched
MAX_WORKERS = 10        # parallel SSH connections

_cache: dict = {}       # alias -> {"ts": float, "data": dict}
_lock = threading.Lock()


def _parse_cpu(raw: str) -> float | None:
    """Parse idle% from 'top -bn1' output and return used%."""
    for line in raw.splitlines():
        if "Cpu(s)" in line or "cpu" in line.lower():
            for part in line.replace(",", " ").split():
                try:
                    val = float(part)
                    if "id" in line[line.find(part):line.find(part) + 20]:
                        return round(100 - val, 1)
                except ValueError:
                    continue
    return None


def _parse_mem(raw: str) -> dict | None:
    """Parse free -m output → {total, used, free, pct}."""
    for line in raw.splitlines():
        if line.lower().startswith("mem:"):
            parts = line.split()
            try:
                total = int(parts[1])
                used = int(parts[2])
                return {"total_mb": total, "used_mb": used,
                        "free_mb": int(parts[3]),
                        "pct": round(used / total * 100, 1) if total else 0}
            except (IndexError, ValueError):
                return None
    return None


def _parse_disk(raw: str) -> dict | None:
    """Parse df -h / output → {size, used, avail, pct}."""
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[-1] == "/":
            return {"size": parts[1], "used": parts[2],
                    "avail": parts[3], "pct": parts[4]}
    return None


def _parse_uptime(raw: str) -> str:
    """Return the uptime string from uptime output."""
    raw = raw.strip()
    if "up" in raw:
        return raw[raw.index("up") + 3:].split(",")[0].strip()
    return raw


def _collect_host(host: dict) -> dict:
    """Collect all metrics for a single host via SSH."""
    alias = host.get("alias", host.get("ip", "?"))
    result = {
        "alias": alias,
        "ip": host.get("ip", ""),
        "project": host.get("_project", ""),
        "env": host.get("env", ""),
        "zone": host.get("zone", ""),
        "status": "unknown",
        "cpu_pct": None,
        "mem": None,
        "disk": None,
        "uptime": None,
        "error": None,
    }

    # Ping first — fast reachability check
    ping_result = ping_tools.ping_host(host["ip"])
    if not ping_result.get("up"):
        result["status"] = "unreachable"
        return result

    # SSH metrics
    commands = {
        "cpu":    "top -bn1 | grep -i 'cpu'",
        "mem":    "free -m",
        "disk":   "df -h /",
        "uptime": "uptime",
    }
    # Use ';' not '&&' — ensures all sections run even if one command fails
    combined = " ; ".join(
        f"echo '==={k}==='; {v}" for k, v in commands.items()
    )

    try:
        raw = ssh_tools.ssh_exec(host["alias"], combined, timeout=15)
        out = raw["stdout"]
        sections: dict[str, str] = {}
        current = None
        for line in out.splitlines():
            if line.startswith("===") and line.endswith("==="):
                current = line.strip("=")
                sections[current] = ""
            elif current:
                sections[current] += line + "\n"

        result["cpu_pct"] = _parse_cpu(sections.get("cpu", ""))
        result["mem"] = _parse_mem(sections.get("mem", ""))
        result["disk"] = _parse_disk(sections.get("disk", ""))
        result["uptime"] = _parse_uptime(sections.get("uptime", ""))
        result["status"] = "ok"
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)[:120]

    return result


def get_all_metrics(force: bool = False) -> list[dict]:
    """
    Return metrics for every host in vms.yaml.
    Results are cached for CACHE_TTL seconds.
    Set force=True to bypass cache.
    """
    now = time.time()
    all_hosts = _flatten_hosts()

    results = []
    to_fetch = []

    with _lock:
        for host in all_hosts:
            alias = host.get("alias", host.get("ip"))
            cached = _cache.get(alias)
            if cached and not force and (now - cached["ts"]) < CACHE_TTL:
                results.append(cached["data"])
            else:
                to_fetch.append(host)

    if to_fetch:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_collect_host, h): h for h in to_fetch}
            for future in as_completed(futures):
                data = future.result()
                alias = data["alias"]
                with _lock:
                    _cache[alias] = {"ts": now, "data": data}
                results.append(data)

    return sorted(results, key=lambda h: (h["project"], h["alias"]))


def _flatten_hosts() -> list[dict]:
    """Flatten vms.yaml projects → list of host dicts with _project injected."""
    inventory = vms.load_hosts()
    hosts = []
    for project, proj_data in inventory.get("projects", {}).items():
        for host in proj_data.get("hosts", []):
            h = dict(host)
            h["_project"] = project
            hosts.append(h)
    return hosts

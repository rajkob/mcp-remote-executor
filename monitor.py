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
            parts = line.replace(",", " ").split()
            for i, part in enumerate(parts):
                try:
                    val = float(part)
                    # idle field is labelled 'id' in the token immediately after the value
                    if i + 1 < len(parts) and parts[i + 1].rstrip(",").lower() == "id":
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

    # TCP connect to SSH port — works even when ICMP is firewalled
    ping_result = ping_tools.ping_host(host["ip"], port=host.get("port", 22))
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
        raw = ssh_tools.ssh_exec(host["alias"], combined, timeout=15, _log=False)
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

    current_aliases = {h.get("alias", h.get("ip")) for h in all_hosts}

    with _lock:
        # Evict cache entries for hosts that no longer exist in vms.yaml
        stale = [a for a in _cache if a not in current_aliases]
        for a in stale:
            del _cache[a]

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


def _flatten_hosts_for(aliases: set) -> list[dict]:
    """Flatten vms.yaml → only hosts whose alias is in *aliases*."""
    inventory = vms.load_hosts()
    hosts = []
    for project, proj_data in inventory.get("projects", {}).items():
        for host in proj_data.get("hosts", []):
            if host.get("alias") in aliases:
                h = dict(host)
                h["_project"] = project
                hosts.append(h)
    return hosts


# ── Watch set (on-demand monitoring) ─────────────────────────────────────────

_watch_set: set = set()   # aliases currently being actively monitored
_watch_lock = threading.Lock()


def watch_add(aliases: list) -> None:
    """Add aliases to the active monitoring watch set."""
    with _watch_lock:
        _watch_set.update(aliases)


def watch_remove(aliases: list) -> None:
    """Remove aliases from the active monitoring watch set."""
    with _watch_lock:
        for a in aliases:
            _watch_set.discard(a)


def watch_clear() -> None:
    """Stop all active monitoring (clear the watch set and evict cache)."""
    with _watch_lock:
        _watch_set.clear()


def list_watched() -> list:
    """Return a sorted list of currently watched aliases."""
    with _watch_lock:
        return sorted(_watch_set)


def _fetch_for_aliases(aliases: set, force: bool = False) -> list[dict]:
    """
    Collect metrics for a specific set of aliases.
    Respects the cache; updates cache entries on fetch.
    Does NOT modify _watch_set.
    """
    if not aliases:
        return []

    to_fetch_hosts = _flatten_hosts_for(aliases)
    now = time.time()
    results = []
    to_fetch = []

    with _lock:
        for host in to_fetch_hosts:
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


def get_watched_metrics(force: bool = False) -> list[dict]:
    """
    Return metrics ONLY for hosts in the active watch set.
    Returns an empty list when nothing is being monitored (default state).
    """
    with _watch_lock:
        aliases = set(_watch_set)
    return _fetch_for_aliases(aliases, force=force)

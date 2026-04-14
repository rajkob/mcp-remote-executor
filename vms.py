"""
vms.yaml CRUD — host inventory management.

All operations target /app/data/vms.yaml (or DATA_DIR env var).
Hosts are resolved by alias, project, tag, env label, or zone label.
"""
import os
import threading
import yaml
from pathlib import Path
from typing import Any

# ─── mtime-based in-memory cache ─────────────────────────────────────────────
_vms_cache: dict | None = None
_vms_mtime: float = 0.0
_vms_lock = threading.Lock()
# ─────────────────────────────────────────────────────────────────────────────


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


class HostNotFound(Exception):
    pass


class DuplicateAlias(Exception):
    pass


def _vms_file() -> Path:
    return Path(os.getenv("DATA_DIR", "/app/data")) / "vms.yaml"


def _load() -> dict:
    global _vms_cache, _vms_mtime
    path = _vms_file()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return {"defaults": {"user": "root", "port": 22}, "templates": {}, "projects": {}}

    with _vms_lock:
        if _vms_cache is not None and mtime == _vms_mtime:
            return _vms_cache
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        data.setdefault("defaults", {"user": "root", "port": 22})
        data.setdefault("templates", {})
        data.setdefault("projects", {})
        _vms_cache = data
        _vms_mtime = mtime
        return data


def _save(data: dict) -> None:
    global _vms_cache, _vms_mtime
    path = _vms_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    # Invalidate cache — next _load() will re-read the newly written file
    with _vms_lock:
        _vms_cache = None
        _vms_mtime = 0.0


def _resolve(host: dict, defaults: dict) -> dict:
    """Return host dict with default user/port filled in."""
    resolved = dict(host)
    resolved.setdefault("user", defaults.get("user", "root"))
    resolved.setdefault("port", defaults.get("port", 22))
    return resolved


def init_empty() -> None:
    """Write skeleton vms.yaml if file does not exist or is empty."""
    path = _vms_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        path.write_text(VMS_SKELETON)


def load_hosts() -> dict:
    return _load()


def get_host(alias: str) -> dict:
    """Return host dict with resolved user/port and _project set. Raises HostNotFound."""
    data = _load()
    defaults = data.get("defaults", {})
    for project, proj_data in data.get("projects", {}).items():
        for host in proj_data.get("hosts", []):
            if host.get("alias") == alias:
                resolved = _resolve(host, defaults)
                resolved["_project"] = project
                return resolved
    raise HostNotFound(f"Host '{alias}' not found in vms.yaml")


def get_all_hosts() -> list[dict]:
    """Return all hosts with resolved fields and _project set."""
    data = _load()
    defaults = data.get("defaults", {})
    result = []
    for project, proj_data in data.get("projects", {}).items():
        for host in proj_data.get("hosts", []):
            resolved = _resolve(host, defaults)
            resolved["_project"] = project
            result.append(resolved)
    return result


def get_hosts_by_project(project: str) -> list[dict]:
    data = _load()
    defaults = data.get("defaults", {})
    proj_data = data.get("projects", {}).get(project, {})
    return [_resolve(h, defaults) for h in proj_data.get("hosts", [])]


def get_hosts_by_tag(tag: str) -> list[dict]:
    return [h for h in get_all_hosts() if tag in (h.get("tags") or [])]


def get_hosts_by_env(env: str) -> list[dict]:
    return [h for h in get_all_hosts() if h.get("env") == env]


def get_hosts_by_zone(zone: str) -> list[dict]:
    return [h for h in get_all_hosts() if (h.get("zone") or "").upper() == zone.upper()]


def resolve_target(target: str) -> list[dict]:
    """
    Resolve a target string to a list of hosts.
    Tries in order: 'all' > alias > project name > tag > env > zone.
    Loads vms.yaml exactly once regardless of how many filters are checked.
    Raises HostNotFound if nothing matches.
    """
    all_hosts = get_all_hosts()  # single _load() + stat() for all branches below

    if target.lower() == "all":
        return all_hosts

    # Exact alias
    exact = [h for h in all_hosts if h.get("alias") == target]
    if exact:
        return exact

    # Project name
    by_project = [h for h in all_hosts if h.get("_project") == target]
    if by_project:
        return by_project

    # Tag
    by_tag = [h for h in all_hosts if target in (h.get("tags") or [])]
    if by_tag:
        return by_tag

    # Env
    by_env = [h for h in all_hosts if h.get("env") == target]
    if by_env:
        return by_env

    # Zone (case-insensitive)
    by_zone = [h for h in all_hosts if (h.get("zone") or "").upper() == target.upper()]
    if by_zone:
        return by_zone

    raise HostNotFound(f"No hosts found for target '{target}'")


def write_host(project: str, host_dict: dict) -> None:
    """Append a new host to the given project. Raises DuplicateAlias."""
    data = _load()
    alias = host_dict.get("alias")

    for proj_data in data.get("projects", {}).values():
        for h in proj_data.get("hosts", []):
            if h.get("alias") == alias:
                raise DuplicateAlias(f"Alias '{alias}' already exists in vms.yaml")

    data["projects"].setdefault(project, {"hosts": []})
    data["projects"][project].setdefault("hosts", [])
    clean = {k: v for k, v in host_dict.items() if not k.startswith("_") and v is not None}
    data["projects"][project]["hosts"].append(clean)
    _save(data)


def delete_host(alias: str) -> str:
    """Remove host by alias. Returns project name it was in."""
    data = _load()
    for project, proj_data in data.get("projects", {}).items():
        hosts = proj_data.get("hosts", [])
        for i, h in enumerate(hosts):
            if h.get("alias") == alias:
                hosts.pop(i)
                _save(data)
                return project
    raise HostNotFound(f"Host '{alias}' not found")


def update_host(alias: str, field: str, value: Any) -> None:
    """Update a single field of an existing host in vms.yaml."""
    data = _load()
    for proj_data in data.get("projects", {}).values():
        for host in proj_data.get("hosts", []):
            if host.get("alias") == alias:
                if value is None and field in host:
                    del host[field]
                else:
                    host[field] = value
                _save(data)
                return
    raise HostNotFound(f"Host '{alias}' not found")


def load_templates() -> dict:
    return _load().get("templates", {})


def write_template(name: str, command: str) -> None:
    data = _load()
    data["templates"][name] = command
    _save(data)


def delete_template(name: str) -> None:
    data = _load()
    if name not in data.get("templates", {}):
        raise KeyError(f"Template '{name}' not found")
    del data["templates"][name]
    _save(data)


def expand_template(name: str, alias: str) -> str:
    """Resolve template command with {{alias}} substitution."""
    templates = load_templates()
    if name not in templates:
        raise KeyError(f"Template '{name}' not found")
    return templates[name].replace("{{alias}}", alias)


def format_hosts_table() -> str:
    """Return markdown table of all hosts grouped by project."""
    data = _load()
    if not data.get("projects"):
        return "No hosts configured yet. Use `add_host` to add your first host."

    defaults = data.get("defaults", {})
    lines = []
    for project, proj_data in data["projects"].items():
        hosts = proj_data.get("hosts", [])
        if not hosts:
            continue
        lines.append(f"\n**📁 {project}** — {len(hosts)} host(s)\n")
        lines.append("| Alias | IP | Port | User | Env | Zone | Tags | Auth |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for h in hosts:
            resolved = _resolve(h, defaults)
            tags = ", ".join(resolved.get("tags") or [])
            lines.append(
                f"| {resolved.get('alias', '')} "
                f"| {resolved.get('ip', '')} "
                f"| {resolved.get('port', 22)} "
                f"| {resolved.get('user', '')} "
                f"| {resolved.get('env', '')} "
                f"| {resolved.get('zone', '')} "
                f"| {tags} "
                f"| {resolved.get('auth', 'prompt')} |"
            )
    return "\n".join(lines) if lines else "No hosts configured yet."

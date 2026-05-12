> **LLM INSTRUCTION:** Before refactoring ANY file, read this entire document.
> After changing any public function or data structure, update this document FIRST
> before moving to the next file. This is the single source of truth for cross-module interfaces.

# Cross-Module Contracts

## 1. Module dependency map

```text
credentials.py          exec_log.py
       ^                     ^
       |                     |
       |                 +---+------------------+
       |                 |                      |
vms.py +-------------> ssh_tools.py            dashboard.py
  ^  ^                    ^   ^                    ^
  |  |                    |   |                    |
  |  +-------------- ping_tools.py                |
  |                        ^                       |
  +------------------------+---- monitor.py -------+

server.py imports:
  vms.py, ssh_tools.py, credentials.py, exec_log.py,
  ping_tools.py, monitor.py, dashboard.py
```

## 2. Data contracts

### Host dict fields (`vms.py`, `server.py`, `ssh_tools.py`, `monitor.py`)

Persisted host records come from `vms.write_host()`, `vms.write_hosts_bulk()`, and `server.add_host()`. Resolved host records from `vms.get_host()` / `vms.get_all_hosts()` also inject `_project`, and `vms._resolve()` injects default `user` / `port`.

| Field | Type | Notes from real code |
|---|---|---|
| `alias` | `str` | Required by `vms.write_host()` and bulk import. Primary lookup key. |
| `ip` | `str` | Required by `vms.write_host()` and bulk import. |
| `port` | `int` | Optional in stored data; defaults to `22` via `vms._resolve()`. |
| `user` | `str` | Optional in stored data; defaults to `"root"` via `vms._resolve()`. |
| `env` | `str` | Optional; used by `get_hosts_by_env()` and monitoring output. |
| `zone` | `str` | Optional; used by `get_hosts_by_zone()` and monitoring output. |
| `tags` | `list[str]` | Optional; used by tag filtering and CSV/JSON import. |
| `auth` | `str` | Optional; observed values in real code: `"prompt"`, `"credential-manager"`, `"keyFile"`. |
| `keyFile` | `str` | Optional; used only when `auth == "keyFile"` in `ssh_tools._connect()`. |
| `timeout` | `int` | Optional per-host SSH timeout override. |
| `_project` | `str` | Injected by `vms.get_host()`, `vms.get_all_hosts()`, and monitor flatten helpers; not persisted. |

### SSH exec result fields (`ssh_tools.ssh_exec`)

| Field | Type | Notes |
|---|---|---|
| `alias` | `str` | Requested alias. |
| `ip` | `str` | Host IP from `vms.get_host()`. |
| `stdout` | `str` | Decoded stdout text. |
| `stderr` | `str` | Decoded stderr text. |
| `exit_code` | `int` | SSH command exit status. |
| `elapsed_s` | `float` | Rounded wall-clock duration. |

`ssh_tools.ssh_exec_multi()` returns the same success dicts, but failed items use `{"alias": <alias>, "error": <str>, "exit_code": -1}`.

### Exec log entry fields (`exec_log.read`, `exec_log.read_by_alias`)

`exec_log.append()` writes lines as:

```text
ISO timestamp | alias | ip:port | user | exit_code | command
```

Read APIs parse those lines into:

| Field | Type | Notes |
|---|---|---|
| `timestamp` | `str` | UTC timestamp formatted as `%Y-%m-%dT%H:%M:%S`. |
| `alias` | `str` | Host alias. |
| `host` | `str` | Stored as `ip:port`. |
| `user` | `str` | SSH username. |
| `exit` | `str` | Exit code read back from the text log. |
| `command` | `str` | Command text after `MAX_COMMAND_LEN` trimming. |

### Ping result fields (`ping_tools.py`)

| Field | Type | Notes |
|---|---|---|
| `alias` | `str` | Present for `_tcp_check()`, `ping_hosts()`, and ICMP helpers; `ping_host()` sets it to `""`. |
| `ip` | `str` | Target IP, or `"unknown"` for unresolved aliases. |
| `port` | `int` | Present in TCP results only (`_tcp_check`, `ping_host`, `ping_hosts`). |
| `up` | `bool` | Reachability result. |
| `error` | `str` | Optional; added for aliases not found in `vms.yaml`. |

### Monitor metric fields (`monitor._collect_host`)

| Field | Type | Notes |
|---|---|---|
| `alias` | `str` | Host alias, or IP fallback. |
| `ip` | `str` | Host IP. |
| `project` | `str` | Project name from `_project`. |
| `env` | `str` | Host environment label. |
| `zone` | `str` | Host zone label. |
| `status` | `str` | Observed values: `"unknown"`, `"unreachable"`, `"ok"`, `"error"`. |
| `cpu_pct` | `float \| None` | Parsed from `top -bn1` output. |
| `mem` | `dict \| None` | From `_parse_mem()`: `total_mb`, `used_mb`, `free_mb`, `pct`. |
| `disk` | `dict \| None` | From `_parse_disk()`: `size`, `used`, `avail`, `pct`. |
| `uptime` | `str \| None` | Parsed uptime summary. |
| `error` | `str \| None` | Trimmed exception message on collection failure. |

## 3. Public function signatures

### `vms.py`

- `init_empty() -> None`
- `load_hosts() -> dict`
- `get_host(alias: str) -> dict`
- `get_all_hosts() -> list[dict]`
- `get_hosts_by_project(project: str) -> list[dict]`
- `get_hosts_by_tag(tag: str) -> list[dict]`
- `get_hosts_by_env(env: str) -> list[dict]`
- `get_hosts_by_zone(zone: str) -> list[dict]`
- `resolve_target(target: str) -> list[dict]`
- `write_host(project: str, host_dict: dict) -> None`
- `delete_host(alias: str) -> str`
- `update_host(alias: str, field: str, value: Any) -> None`
- `load_templates() -> dict`
- `write_template(name: str, command: str) -> None`
- `delete_template(name: str) -> None`
- `expand_template(name: str, alias: str) -> str`
- `write_hosts_bulk(entries: list[tuple]) -> dict`
- `format_hosts_table() -> str`

### `credentials.py`

- `save_credential(ip: str, user: str, password: str) -> None`
- `get_credential(ip: str, user: str) -> str | None`
- `delete_credential(ip: str, user: str) -> bool`
- `credential_exists(ip: str, user: str) -> bool`
- `list_stored() -> list[dict]`

### `exec_log.py`

- `append(alias: str, ip: str, port: int, user: str, exit_code: int, command: str) -> None`
- `read(n: int = 50) -> list[dict]`
- `clear() -> None`
- `read_by_alias(alias: str, n: int = 20) -> list[dict]`
- `to_json(entries: list[dict]) -> str`
- `to_csv(entries: list[dict]) -> str`
- `format_log_table(entries: list[dict]) -> str`

### `ssh_tools.py`

- `close_all_connections() -> int`
- `ssh_exec(alias: str, command: str, timeout: int | None = None, _log: bool = True, force: bool = False) -> dict`
- `ssh_exec_multi(aliases: list[str], command: str, mode: str = "sequential", force: bool = False) -> list[dict]`
- `sftp_upload(alias: str, local_path: str, remote_path: str) -> dict`
- `sftp_download(alias: str, remote_path: str, local_path: str) -> dict`

### `ping_tools.py`

- `ping_host(ip: str, port: int = 22) -> dict`
- `ping_hosts(aliases: list[str]) -> list[dict]`
- `ping_hosts_icmp(aliases: list[str]) -> list[dict]`
- `format_ping_results(results: list[dict]) -> str`

### `monitor.py`

- `get_all_metrics(force: bool = False) -> list[dict]`
- `watch_add(aliases: list) -> None`
- `watch_remove(aliases: list) -> None`
- `watch_clear() -> None`
- `list_watched() -> list`
- `get_watched_metrics(force: bool = False) -> list[dict]`

### `dashboard.py`

This module exposes no public top-level functions. Its public callable surface is class-based:

- `async DashboardApp.__call__(self, scope, receive, send)`
- `RouterApp.__init__(self, mcp_app)`
- `async RouterApp.__call__(self, scope, receive, send)`

### `server.py`

- `list_hosts() -> str`
- `add_host(project: str, alias: str, ip: str, port: int = 22, user: str = "", env: str = "", zone: str = "", tags: list[str] | None = None, auth: Literal["credential-manager", "keyFile", "prompt"] = "prompt", key_file: str = "") -> str`
- `remove_host(alias: str, also_delete_credential: bool = False) -> str`
- `update_host(alias: str, field: str, value: str) -> str`
- `import_hosts(format: str, content: str) -> str`
- `save_credential(alias: str, password: str) -> str`
- `check_credential(alias: str) -> str`
- `delete_credential(alias: str) -> str`
- `audit_credentials() -> str`
- `run_command(alias: str, command: str, force: bool = False) -> str`
- `run_command_multi(target: str, command: str, mode: Literal["sequential", "parallel"] = "sequential", force: bool = False) -> str`
- `upload_file(alias: str, local_path: str, remote_path: str) -> str`
- `download_file(alias: str, remote_path: str, local_path: str) -> str`
- `ping_hosts(target: str = "all") -> str`
- `list_templates() -> str`
- `expand_template(name: str, alias: str) -> str`
- `add_template(name: str, command: str) -> str`
- `remove_template(name: str) -> str`
- `read_exec_log(n: int = 50) -> str`
- `clear_exec_log() -> str`
- `command_history(alias: str, n: int = 20) -> str`
- `export_exec_log(alias: str = "", format: str = "json") -> str`
- `save_output(content: str, label: str, command: str) -> str`
- `health_check(alias: str) -> str`
- `start_monitoring(target: str) -> str`
- `stop_monitoring(target: str) -> str`
- `monitoring_status() -> str`
- `ai_analyze(alias: str, question: str) -> str`
- `ollama_status() -> str`

## 4. Exception classes defined in `vms.py` and `ssh_tools.py`

### `vms.py`

- `HostNotFound`
- `DuplicateAlias`

### `ssh_tools.py`

- `CredentialNotFound`
- `HostUnreachable`
- `AuthFailure`
- `CommandTimeout`
- `DestructiveCommandBlocked`

## 5. Safe refactoring order

1. `credentials.py`
2. `exec_log.py`
3. `vms.py`
4. `ssh_tools.py`
5. `ping_tools.py`
6. `monitor.py`
7. `dashboard.py`
8. `server.py` **always last**

Rule: after any public contract change, update this file before moving to the next file in the order above.

## 6. Change log

| Date | Change | Author |
|---|---|---|
| 2026-05-12 | Initial structured multi-file refactoring protocol added (`CONTRACTS.md`, `.llm/refactor_plan.md`, `Makefile`, `.gitignore` note). | Copilot |

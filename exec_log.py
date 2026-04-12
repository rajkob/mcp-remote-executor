"""
Execution log — append / read / clear.

Every SSH command and file transfer is logged to /app/data/exec.log.
Format: ISO timestamp | alias | ip:port | user | exit_code | command

Log rotation: the file is trimmed to MAX_LOG_LINES every ROTATE_EVERY writes
so it never grows without bound, without reading the full file on every append.
"""
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

MAX_LOG_LINES = int(os.getenv("MAX_LOG_LINES", "10000"))
_ROTATE_EVERY = 100   # only run rotation check every N writes

_write_count = 0
_rotate_lock = threading.Lock()


def _log_file() -> Path:
    return Path(os.getenv("DATA_DIR", "/app/data")) / "exec.log"


def append(alias: str, ip: str, port: int, user: str, exit_code: int, command: str) -> None:
    """Append one log entry. Rotates the file every ROTATE_EVERY writes."""
    global _write_count

    path = _log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    line = f"{ts} | {alias} | {ip}:{port} | {user} | {exit_code} | {command}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)

    with _rotate_lock:
        _write_count += 1
        do_rotate = (_write_count % _ROTATE_EVERY == 0)

    if do_rotate:
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > MAX_LOG_LINES:
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-MAX_LOG_LINES:])
        except OSError:
            pass  # non-fatal


def read(n: int = 50) -> list[dict]:
    """Return last n log entries as list of dicts."""
    path = _log_file()
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    result = []
    for line in lines[-n:]:
        parts = line.strip().split(" | ", 5)
        if len(parts) == 6:
            result.append({
                "timestamp": parts[0],
                "alias": parts[1],
                "host": parts[2],
                "user": parts[3],
                "exit": parts[4],
                "command": parts[5],
            })
    return result


def clear() -> None:
    """Delete the execution log file."""
    path = _log_file()
    if path.exists():
        path.unlink()


def format_log_table(entries: list[dict]) -> str:
    """Format log entries as a markdown table."""
    if not entries:
        return "Execution log is empty."
    lines = [
        "| Timestamp | Alias | Host | User | Exit | Command |",
        "|---|---|---|---|---|---|",
    ]
    for e in entries:
        lines.append(
            f"| {e['timestamp']} | {e['alias']} | {e['host']} "
            f"| {e['user']} | {e['exit']} | `{e['command']}` |"
        )
    return "\n".join(lines)

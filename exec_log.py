"""
Execution log — append / read / clear.

Every SSH command and file transfer is logged to /app/data/exec.log.
Format: ISO timestamp | alias | ip:port | user | exit_code | command
"""
import os
from datetime import datetime, timezone
from pathlib import Path


def _log_file() -> Path:
    return Path(os.getenv("DATA_DIR", "/app/data")) / "exec.log"


def append(alias: str, ip: str, port: int, user: str, exit_code: int, command: str) -> None:
    """Append one log entry. Creates the file if it does not exist."""
    path = _log_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    line = f"{ts} | {alias} | {ip}:{port} | {user} | {exit_code} | {command}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


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

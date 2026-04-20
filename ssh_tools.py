"""
SSH and SFTP operations via paramiko.

Replaces plink/pscp — pure Python, works on any OS inside Docker.
All operations auto-log to exec.log on completion.
"""
import os
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from pathlib import Path

import paramiko

import credentials
import vms

# ── Connection pool ───────────────────────────────────────────────────────────
# Keeps one reusable SSHClient per (ip, port, user).
# Entries are evicted automatically when the underlying transport closes.

_pool: dict[tuple, paramiko.SSHClient] = {}
_pool_lock = threading.Lock()
_MAX_POOL_SIZE = int(os.getenv("MAX_POOL_SIZE", "50"))  # cap to prevent unbounded growth


def _pool_key(host: dict) -> tuple:
    return (host["ip"], host.get("port", 22), host.get("user", "root"))


def _get_pooled(host: dict) -> paramiko.SSHClient | None:
    """Return a healthy pooled client, or None if absent/stale."""
    key = _pool_key(host)
    with _pool_lock:
        client = _pool.get(key)
        if client is None:
            return None
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            del _pool[key]
            return None
        return client


def _store_pooled(host: dict, client: paramiko.SSHClient) -> None:
    key = _pool_key(host)
    with _pool_lock:
        if len(_pool) >= _MAX_POOL_SIZE:
            # FIFO eviction: close and remove the oldest entry
            oldest_key = next(iter(_pool))
            try:
                _pool[oldest_key].close()
            except Exception:
                pass
            del _pool[oldest_key]
        _pool[key] = client


def close_all_connections() -> int:
    """Close every pooled connection and clear the pool. Returns count closed."""
    with _pool_lock:
        count = len(_pool)
        for c in _pool.values():
            try:
                c.close()
            except Exception:
                pass
        _pool.clear()
    return count


# ── Per-host concurrency semaphores ──────────────────────────────────────────
# Limits concurrent SSH commands per (ip, port) to MAX_CONCURRENT_PER_HOST.
_MAX_CONCURRENT_PER_HOST = int(os.getenv("MAX_CONCURRENT_PER_HOST", "3"))
_host_semaphores: dict[tuple, threading.Semaphore] = {}
_semaphore_lock = threading.Lock()


def _get_host_semaphore(host: dict) -> threading.Semaphore:
    """Return (creating if absent) a semaphore for (ip, port) of this host."""
    key = (host["ip"], host.get("port", 22))
    with _semaphore_lock:
        if key not in _host_semaphores:
            _host_semaphores[key] = threading.Semaphore(_MAX_CONCURRENT_PER_HOST)
        return _host_semaphores[key]


class CredentialNotFound(Exception):
    pass


class HostUnreachable(Exception):
    pass


class AuthFailure(Exception):
    pass


class CommandTimeout(Exception):
    pass


class DestructiveCommandBlocked(Exception):
    pass


# ── Destructive command patterns ──────────────────────────────────────────────
# Checked in ssh_exec unless force=True is passed.
_DESTRUCTIVE_PATTERNS: list[tuple] = [
    (re.compile(r"rm\s+-[^\s]*r[^\s]*\s+(\/|~|/root|/home|\$HOME)"),
     "recursive remove on root/home"),
    (re.compile(r"\bdd\b.*\bof=/dev/"),
     "dd write to raw device"),
    (re.compile(r"\bmkfs\b"),
     "filesystem format"),
    (re.compile(r"\b(shutdown|halt|poweroff)\b"),
     "system shutdown/halt/poweroff"),
    (re.compile(r"\breboot\b"),
     "system reboot"),
    (re.compile(r"\binit\s+[06]\b"),
     "init runlevel 0/6 (shutdown/reboot)"),
    (re.compile(r":\(\)\s*\{.*\|.*:.*&\s*\}"),
     "fork bomb"),
    (re.compile(r">\s*/dev/(sd[a-z]+|nvme[0-9]+|hd[a-z]+)"),
     "write to raw disk device"),
]


def _check_destructive(command: str) -> str | None:
    """Return a reason string if command matches a destructive pattern, else None."""
    for pattern, reason in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return reason
    return None


def _connect(host: dict, use_pool: bool = True) -> paramiko.SSHClient:
    """Return an authenticated SSHClient, reusing a pooled connection when available."""
    if use_pool:
        cached = _get_pooled(host)
        if cached is not None:
            return cached

    ip = host["ip"]
    port = host.get("port", 22)
    user = host.get("user", "root")
    auth = host.get("auth", "prompt")
    timeout = host.get("timeout") or 30

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        if auth == "keyFile" and host.get("keyFile"):
            key_path = str(host["keyFile"]).replace("~", str(Path.home()))
            client.connect(ip, port=port, username=user,
                           key_filename=key_path, timeout=timeout)
        else:
            password = credentials.get_credential(ip, user)
            if password is None:
                raise CredentialNotFound(
                    f"No credential stored for {user}@{ip}. "
                    f"Call save_credential(alias, password) first."
                )
            client.connect(ip, port=port, username=user,
                           password=password, timeout=timeout)

    except paramiko.AuthenticationException as e:
        client.close()
        raise AuthFailure(f"Authentication failed for {user}@{ip}: {e}")
    except (paramiko.SSHException, OSError) as e:
        client.close()
        raise HostUnreachable(f"Cannot connect to {ip}:{port}: {e}")

    if use_pool:
        _store_pooled(host, client)
    return client


def _read_channel_output(stdout_ch, stderr_ch) -> tuple:
    """Read stdout/stderr and get exit status from an SSH channel. Blocking."""
    try:
        stdout = stdout_ch.read().decode(errors="replace")
        stderr = stderr_ch.read().decode(errors="replace")
        exit_code = stdout_ch.channel.recv_exit_status()
        return stdout, stderr, exit_code
    except EOFError:
        raise HostUnreachable("Connection lost mid-command")


def ssh_exec(alias: str, command: str, timeout: int | None = None,
             _log: bool = True, force: bool = False) -> dict:
    """
    Run a shell command on a host. Returns dict:
      {alias, ip, stdout, stderr, exit_code, elapsed_s}
    Auto-appends to exec.log unless _log=False.
    Set force=True to bypass the destructive command guard.
    """
    import exec_log

    # ── Destructive command guard ─────────────────────────────────────────────
    if not force:
        blocked = _check_destructive(command)
        if blocked:
            raise DestructiveCommandBlocked(
                f"Command blocked ({blocked}). Pass force=True to override."
            )

    host = vms.get_host(alias)
    effective_timeout = timeout or host.get("timeout") or 30
    start = time.monotonic()

    # ── Per-host rate limit ───────────────────────────────────────────────────
    sem = _get_host_semaphore(host)
    with sem:
        client = _connect(host)
        try:
            _, stdout_ch, stderr_ch = client.exec_command(command)
            with ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(_read_channel_output, stdout_ch, stderr_ch)
                try:
                    stdout, stderr, exit_code = _fut.result(timeout=effective_timeout)
                except (FuturesTimeoutError, socket.timeout):
                    # FuturesTimeoutError: wall-clock timeout exceeded
                    # socket.timeout: paramiko channel-level read timeout
                    stdout_ch.channel.close()
                    with _pool_lock:
                        evicted = _pool.pop(_pool_key(host), None)
                    if evicted:
                        try:
                            evicted.close()  # release the SSH socket
                        except Exception:
                            pass
                    raise CommandTimeout(
                        f"Command timed out after {effective_timeout}s on {alias} ({host['ip']})"
                    )
        except (HostUnreachable, CommandTimeout, AuthFailure, DestructiveCommandBlocked):
            raise
        except Exception:
            # Any other transport-level failure — evict from pool
            with _pool_lock:
                _pool.pop(_pool_key(host), None)
            raise

    elapsed = round(time.monotonic() - start, 1)
    if _log:
        exec_log.append(alias, host["ip"], host.get("port", 22),
                        host.get("user", "root"), exit_code, command)

    return {
        "alias": alias,
        "ip": host["ip"],
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "elapsed_s": elapsed,
    }


def ssh_exec_multi(aliases: list[str], command: str, mode: str = "sequential",
                   force: bool = False) -> list[dict]:
    """
    Run command on multiple hosts.
    mode: 'sequential' | 'parallel'
    force: bypass the destructive command guard
    Returns list of result dicts. Failed hosts include an 'error' key.
    """
    results = []

    if mode == "parallel":
        with ThreadPoolExecutor(max_workers=min(len(aliases), 20)) as pool:
            futures = {
                pool.submit(ssh_exec, alias, command, force=force): alias
                for alias in aliases
            }
            for future in as_completed(futures):
                alias = futures[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"alias": alias, "error": str(e), "exit_code": -1})
    else:
        for alias in aliases:
            try:
                results.append(ssh_exec(alias, command, force=force))
            except Exception as e:
                results.append({"alias": alias, "error": str(e), "exit_code": -1})

    return results


def sftp_upload(alias: str, local_path: str, remote_path: str) -> dict:
    """Upload a local file to the remote host via SFTP. Auto-logs."""
    import exec_log

    host = vms.get_host(alias)
    start = time.monotonic()

    client = _connect(host)
    try:
        sftp = client.open_sftp()
        try:
            sftp.put(local_path, remote_path)
            size = sftp.stat(remote_path).st_size
        finally:
            sftp.close()
    except Exception:
        with _pool_lock:
            _pool.pop(_pool_key(host), None)
        raise

    elapsed = round(time.monotonic() - start, 1)
    exec_log.append(alias, host["ip"], host.get("port", 22),
                    host.get("user", "root"), 0,
                    f"UPLOAD {local_path} -> {remote_path}")

    return {"success": True, "bytes_transferred": size, "elapsed_s": elapsed}


def sftp_download(alias: str, remote_path: str, local_path: str) -> dict:
    """Download a file from the remote host via SFTP. Auto-logs."""
    import exec_log

    host = vms.get_host(alias)
    start = time.monotonic()

    client = _connect(host)
    try:
        sftp = client.open_sftp()
        try:
            sftp.get(remote_path, local_path)
            size = Path(local_path).stat().st_size
        finally:
            sftp.close()
    except Exception:
        with _pool_lock:
            _pool.pop(_pool_key(host), None)
        raise

    elapsed = round(time.monotonic() - start, 1)
    exec_log.append(alias, host["ip"], host.get("port", 22),
                    host.get("user", "root"), 0,
                    f"DOWNLOAD {remote_path} -> {local_path}")

    return {"success": True, "bytes_transferred": size, "elapsed_s": elapsed}

"""
SSH and SFTP operations via paramiko.

Replaces plink/pscp — pure Python, works on any OS inside Docker.
All operations auto-log to exec.log on completion.
"""
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import paramiko

import credentials
import vms


class CredentialNotFound(Exception):
    pass


class HostUnreachable(Exception):
    pass


class AuthFailure(Exception):
    pass


class CommandTimeout(Exception):
    pass


def _connect(host: dict) -> paramiko.SSHClient:
    """Open and return an authenticated paramiko SSHClient."""
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

    return client


def ssh_exec(alias: str, command: str, timeout: int | None = None) -> dict:
    """
    Run a shell command on a host. Returns dict:
      {alias, ip, stdout, stderr, exit_code, elapsed_s}
    Auto-appends to exec.log.
    """
    import exec_log

    host = vms.get_host(alias)
    effective_timeout = timeout or host.get("timeout") or 30
    start = time.monotonic()

    client = _connect(host)
    try:
        _, stdout_ch, stderr_ch = client.exec_command(command, timeout=effective_timeout)
        try:
            stdout = stdout_ch.read().decode(errors="replace")
            stderr = stderr_ch.read().decode(errors="replace")
            exit_code = stdout_ch.channel.recv_exit_status()
        except (socket.timeout, EOFError):
            # socket.timeout: channel read timed out
            # EOFError: paramiko raises this on unexpected channel close / disconnect
            raise CommandTimeout(
                f"Command timed out after {effective_timeout}s on {alias} ({host['ip']})"
            )
    finally:
        client.close()

    elapsed = round(time.monotonic() - start, 1)
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


def ssh_exec_multi(aliases: list[str], command: str, mode: str = "sequential") -> list[dict]:
    """
    Run command on multiple hosts.
    mode: 'sequential' | 'parallel'
    Returns list of result dicts. Failed hosts include an 'error' key.
    """
    results = []

    if mode == "parallel":
        with ThreadPoolExecutor(max_workers=min(len(aliases), 20)) as pool:
            futures = {pool.submit(ssh_exec, alias, command): alias for alias in aliases}
            for future in as_completed(futures):
                alias = futures[future]
                try:
                    results.append(future.result())
                except Exception as e:
                    results.append({"alias": alias, "error": str(e), "exit_code": -1})
    else:
        for alias in aliases:
            try:
                results.append(ssh_exec(alias, command))
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
        sftp.put(local_path, remote_path)
        size = Path(local_path).stat().st_size
        sftp.close()
    finally:
        client.close()

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
        sftp.get(remote_path, local_path)
        size = Path(local_path).stat().st_size
        sftp.close()
    finally:
        client.close()

    elapsed = round(time.monotonic() - start, 1)
    exec_log.append(alias, host["ip"], host.get("port", 22),
                    host.get("user", "root"), 0,
                    f"DOWNLOAD {remote_path} -> {local_path}")

    return {"success": True, "bytes_transferred": size, "elapsed_s": elapsed}

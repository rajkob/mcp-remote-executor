"""
Unit tests for ssh_tools.py — SSH/SFTP operations.

All SSH I/O is mocked via unittest.mock; no real SSH connections are made.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent))
from cryptography.fernet import Fernet
os.environ["CRED_MASTER_KEY"] = Fernet.generate_key().decode()

import ssh_tools


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_paramiko_client(stdout_text="output", stderr_text="", exit_code=0,
                           read_side_effect=None):
    """Return a mock paramiko.SSHClient with preset exec_command behaviour."""
    client = MagicMock()

    stdout_ch = MagicMock()
    if read_side_effect:
        stdout_ch.read.side_effect = read_side_effect
    else:
        stdout_ch.read.return_value = stdout_text.encode()
    stdout_ch.channel.recv_exit_status.return_value = exit_code

    stderr_ch = MagicMock()
    stderr_ch.read.return_value = stderr_text.encode()

    client.exec_command.return_value = (MagicMock(), stdout_ch, stderr_ch)

    return client


def _host(alias="web01", ip="10.0.0.1", port=22, user="root"):
    return {"alias": alias, "ip": ip, "port": port, "user": user, "auth": "credential"}


# ── _connect ──────────────────────────────────────────────────────────────────

class TestConnect(unittest.TestCase):
    def test_credential_not_found_raises(self):
        host = _host()
        with patch("credentials.get_credential", return_value=None):
            with self.assertRaises(ssh_tools.CredentialNotFound):
                ssh_tools._connect(host)

    def test_auth_failure_raises(self):
        import paramiko
        host = _host()
        with patch("credentials.get_credential", return_value="pw"):
            with patch("paramiko.SSHClient") as MockSSH:
                inst = MockSSH.return_value
                inst.connect.side_effect = paramiko.AuthenticationException("bad creds")
                with self.assertRaises(ssh_tools.AuthFailure):
                    ssh_tools._connect(host)

    def test_connect_success(self):
        host = _host()
        with patch("credentials.get_credential", return_value="pw"):
            with patch("paramiko.SSHClient") as MockSSH:
                inst = MockSSH.return_value
                result = ssh_tools._connect(host)
                self.assertIs(result, inst)
                inst.connect.assert_called_once_with(
                    "10.0.0.1", port=22, username="root", password="pw", timeout=30
                )

    def test_unreachable_raises(self):
        import paramiko
        host = _host()
        with patch("credentials.get_credential", return_value="pw"):
            with patch("paramiko.SSHClient") as MockSSH:
                inst = MockSSH.return_value
                inst.connect.side_effect = paramiko.SSHException("No route to host")
                with self.assertRaises(ssh_tools.HostUnreachable):
                    ssh_tools._connect(host)

    def test_keyfile_auth(self):
        host = _host()
        host["auth"] = "keyFile"
        host["keyFile"] = "/home/user/.ssh/id_rsa"
        with patch("paramiko.SSHClient") as MockSSH:
            inst = MockSSH.return_value
            ssh_tools._connect(host)
            inst.connect.assert_called_once_with(
                "10.0.0.1", port=22, username="root",
                key_filename="/home/user/.ssh/id_rsa", timeout=30
            )


# ── ssh_exec ──────────────────────────────────────────────────────────────────

class TestSshExec(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self._tmp.name
        import vms
        vms._vms_cache = None
        vms._vms_mtime = 0.0
        vms.init_empty()
        vms.write_host("CORE", {"alias": "web01", "ip": "10.0.0.1", "port": 22})

    def tearDown(self):
        self._tmp.cleanup()

    def _exec(self, stdout="ok", stderr="", exit_code=0):
        client = _make_paramiko_client(stdout, stderr, exit_code)
        with patch.object(ssh_tools, "_connect", return_value=client):
            with patch("exec_log.append"):
                return ssh_tools.ssh_exec("web01", "uptime")

    def test_returns_expected_keys(self):
        result = self._exec()
        for key in ("alias", "ip", "stdout", "stderr", "exit_code", "elapsed_s"):
            self.assertIn(key, result)

    def test_stdout_captured(self):
        result = self._exec(stdout="load: 0.5")
        self.assertEqual(result["stdout"], "load: 0.5")

    def test_exit_code_captured(self):
        result = self._exec(exit_code=1)
        self.assertEqual(result["exit_code"], 1)

    def test_log_written(self):
        client = _make_paramiko_client()
        with patch.object(ssh_tools, "_connect", return_value=client):
            with patch("exec_log.append") as mock_log:
                ssh_tools.ssh_exec("web01", "whoami")
                mock_log.assert_called_once()

    def test_log_skipped_when_flag_false(self):
        client = _make_paramiko_client()
        with patch.object(ssh_tools, "_connect", return_value=client):
            with patch("exec_log.append") as mock_log:
                ssh_tools.ssh_exec("web01", "whoami", _log=False)
                mock_log.assert_not_called()

    def test_socket_timeout_raises_command_timeout(self):
        import socket
        client = _make_paramiko_client(read_side_effect=socket.timeout("timed out"))
        with patch.object(ssh_tools, "_connect", return_value=client):
            with self.assertRaises(ssh_tools.CommandTimeout):
                ssh_tools.ssh_exec("web01", "sleep 999", _log=False)

    def test_eoferror_raises_host_unreachable(self):
        client = _make_paramiko_client(read_side_effect=EOFError("lost connection"))
        with patch.object(ssh_tools, "_connect", return_value=client):
            with self.assertRaises(ssh_tools.HostUnreachable):
                ssh_tools.ssh_exec("web01", "uptime", _log=False)

    def test_client_closed_on_success(self):
        client = _make_paramiko_client()
        with patch.object(ssh_tools, "_connect", return_value=client):
            with patch("exec_log.append"):
                ssh_tools.ssh_exec("web01", "uptime")
        client.close.assert_called()

    def test_client_closed_on_error(self):
        client = _make_paramiko_client(read_side_effect=EOFError())
        with patch.object(ssh_tools, "_connect", return_value=client):
            with self.assertRaises(ssh_tools.HostUnreachable):
                ssh_tools.ssh_exec("web01", "uptime", _log=False)
        client.close.assert_called()


# ── ssh_exec_multi ────────────────────────────────────────────────────────────

class TestSshExecMulti(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self._tmp.name
        import vms
        vms._vms_cache = None
        vms._vms_mtime = 0.0
        vms.init_empty()
        vms.write_host("CORE", {"alias": "web01", "ip": "10.0.0.1", "port": 22})
        vms.write_host("CORE", {"alias": "web02", "ip": "10.0.0.2", "port": 22})

    def tearDown(self):
        self._tmp.cleanup()

    def _mock_exec(self, alias, command, **kwargs):
        return {"alias": alias, "ip": "10.0.0.x", "stdout": "ok",
                "stderr": "", "exit_code": 0, "elapsed_s": 0.1}

    def test_sequential_returns_all(self):
        with patch.object(ssh_tools, "ssh_exec", side_effect=self._mock_exec):
            results = ssh_tools.ssh_exec_multi(["web01", "web02"], "uptime", mode="sequential")
        self.assertEqual(len(results), 2)

    def test_parallel_returns_all(self):
        with patch.object(ssh_tools, "ssh_exec", side_effect=self._mock_exec):
            results = ssh_tools.ssh_exec_multi(["web01", "web02"], "uptime", mode="parallel")
        self.assertEqual(len(results), 2)

    def test_error_captured_not_raised(self):
        def boom(alias, *a, **kw):
            if alias == "web02":
                raise ssh_tools.HostUnreachable("down")
            return {"alias": alias, "stdout": "ok", "exit_code": 0, "elapsed_s": 0}

        with patch.object(ssh_tools, "ssh_exec", side_effect=boom):
            results = ssh_tools.ssh_exec_multi(["web01", "web02"], "uptime")
        errors = [r for r in results if "error" in r]
        self.assertEqual(len(errors), 1)
        self.assertIn("down", errors[0]["error"])


# ── sftp_upload / sftp_download ───────────────────────────────────────────────

class TestSftp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self._tmp.name
        import vms
        vms._vms_cache = None
        vms._vms_mtime = 0.0
        vms.init_empty()
        vms.write_host("CORE", {"alias": "web01", "ip": "10.0.0.1", "port": 22})

    def tearDown(self):
        self._tmp.cleanup()

    def test_upload_returns_bytes_transferred(self):
        local = Path(self._tmp.name) / "test.txt"
        local.write_text("hello world")
        client = MagicMock()
        sftp = MagicMock()
        client.open_sftp.return_value = sftp
        # stat() is called after put() to get remote file size
        sftp.stat.return_value.st_size = 11

        with patch.object(ssh_tools, "_connect", return_value=client):
            with patch("exec_log.append"):
                result = ssh_tools.sftp_upload("web01", str(local), "/tmp/test.txt")

        self.assertTrue(result["success"])
        self.assertEqual(result["bytes_transferred"], 11)
        sftp.put.assert_called_once_with(str(local), "/tmp/test.txt")
        sftp.stat.assert_called_once_with("/tmp/test.txt")

    def test_download_returns_bytes(self):
        local = Path(self._tmp.name) / "downloaded.txt"
        client = MagicMock()
        sftp = MagicMock()
        client.open_sftp.return_value = sftp

        # side_effect writes the file so stat().st_size works
        def fake_get(remote, local_path):
            Path(local_path).write_text("content from remote")

        sftp.get.side_effect = fake_get

        with patch.object(ssh_tools, "_connect", return_value=client):
            with patch("exec_log.append"):
                result = ssh_tools.sftp_download("web01", "/remote/file.txt", str(local))

        self.assertTrue(result["success"])
        self.assertGreater(result["bytes_transferred"], 0)


if __name__ == "__main__":
    unittest.main()

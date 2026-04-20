"""
Unit tests for server.py MCP tool wrappers.

Tests cover: run_command, run_command_multi, upload_file, download_file,
health_check, save_output, host management, credential management, templates,
exec log tools.

All external I/O (SSH, filesystem, vms) is mocked or redirected to temp dirs.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from cryptography.fernet import Fernet
os.environ.setdefault("CRED_MASTER_KEY", Fernet.generate_key().decode())
os.environ.setdefault("DATA_DIR", "/tmp/test_server_tools")

import server
import ssh_tools
import vms as vms_module


def _setup_vms(tmp_dir: str):
    """Point vms at a fresh temp dir and add a test host."""
    os.environ["DATA_DIR"] = tmp_dir
    vms_module._vms_cache = None
    vms_module._vms_mtime = 0.0
    vms_module.init_empty()
    vms_module.write_host("CORE", {"alias": "web01", "ip": "10.0.0.1", "port": 22, "user": "root"})


# ── run_command ───────────────────────────────────────────────────────────────

class TestRunCommand(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _setup_vms(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_success_returns_output(self):
        r = {"alias": "web01", "ip": "10.0.0.1", "exit_code": 0,
             "stdout": "hello\n", "stderr": "", "elapsed_s": 0.1}
        with patch("server.ssh_tools.ssh_exec", return_value=r):
            result = server.run_command("web01", "echo hello")
        self.assertIn("hello", result)
        self.assertIn("exit 0", result)

    def test_nonzero_exit_shows_error_icon(self):
        r = {"alias": "web01", "ip": "10.0.0.1", "exit_code": 1,
             "stdout": "", "stderr": "not found\n", "elapsed_s": 0.2}
        with patch("server.ssh_tools.ssh_exec", return_value=r):
            result = server.run_command("web01", "badcmd")
        self.assertIn("❌", result)

    def test_credential_not_found(self):
        with patch("server.ssh_tools.ssh_exec",
                   side_effect=ssh_tools.CredentialNotFound("web01")):
            result = server.run_command("web01", "ls")
        self.assertIn("❌", result)

    def test_host_unreachable(self):
        with patch("server.ssh_tools.ssh_exec",
                   side_effect=ssh_tools.HostUnreachable("web01", "10.0.0.1", 22)):
            result = server.run_command("web01", "ls")
        self.assertIn("⚠️", result)

    def test_auth_failure(self):
        with patch("server.ssh_tools.ssh_exec",
                   side_effect=ssh_tools.AuthFailure("web01", "10.0.0.1")):
            result = server.run_command("web01", "ls")
        self.assertIn("🔐", result)

    def test_command_timeout(self):
        with patch("server.ssh_tools.ssh_exec",
                   side_effect=ssh_tools.CommandTimeout("web01", "ls", 30)):
            result = server.run_command("web01", "ls")
        self.assertIn("⏱", result)

    def test_host_not_found(self):
        with patch("server.ssh_tools.ssh_exec",
                   side_effect=vms_module.HostNotFound("ghost")):
            result = server.run_command("ghost", "ls")
        self.assertIn("❌", result)


# ── run_command_multi ─────────────────────────────────────────────────────────

class TestRunCommandMulti(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _setup_vms(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_success_summary(self):
        multi_results = [
            {"alias": "web01", "ip": "10.0.0.1", "exit_code": 0,
             "stdout": "up 1 day\n", "stderr": "", "elapsed_s": 0.5},
        ]
        with patch("server.ssh_tools.ssh_exec_multi", return_value=multi_results):
            result = server.run_command_multi("all", "uptime")
        self.assertIn("1 success", result)
        self.assertIn("up 1 day", result)

    def test_host_not_found_returns_error(self):
        with patch("server.vms.resolve_target",
                   side_effect=vms_module.HostNotFound("badtarget")):
            result = server.run_command_multi("badtarget", "ls")
        self.assertIn("❌", result)

    def test_empty_host_list(self):
        with patch("server.vms.resolve_target", return_value=[]):
            result = server.run_command_multi("emptyproject", "ls")
        self.assertIn("No hosts", result)


# ── upload_file / download_file ───────────────────────────────────────────────

class TestFileTransfer(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _setup_vms(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_upload_success(self):
        with patch("server.ssh_tools.sftp_upload",
                   return_value={"bytes_transferred": 1024, "elapsed_s": 0.3}):
            result = server.upload_file("web01", "/local/f.txt", "/remote/f.txt")
        self.assertIn("1,024", result)
        self.assertIn("web01", result)

    def test_upload_failure(self):
        with patch("server.ssh_tools.sftp_upload",
                   side_effect=Exception("connection refused")):
            result = server.upload_file("web01", "/local/f.txt", "/remote/f.txt")
        self.assertIn("❌", result)

    def test_download_success(self):
        with patch("server.ssh_tools.sftp_download",
                   return_value={"bytes_transferred": 512, "elapsed_s": 0.2}):
            result = server.download_file("web01", "/remote/f.txt", "/local/f.txt")
        self.assertIn("512", result)

    def test_download_failure(self):
        with patch("server.ssh_tools.sftp_download",
                   side_effect=Exception("no such file")):
            result = server.download_file("web01", "/remote/f.txt", "/local/f.txt")
        self.assertIn("❌", result)


# ── save_output ───────────────────────────────────────────────────────────────

class TestSaveOutput(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_file_and_returns_path(self):
        result = server.save_output("Filesystem: 80%", "web01", "df -h")
        self.assertIn("✓", result)
        self.assertIn("web01", result)
        # Check file actually exists
        output_dir = Path(self._tmp.name) / "output"
        files = list(output_dir.glob("web01_*.txt"))
        self.assertEqual(len(files), 1)
        content = files[0].read_text(encoding="utf-8")
        self.assertIn("Filesystem: 80%", content)
        self.assertIn("df -h", content)


# ── health_check ──────────────────────────────────────────────────────────────

class TestHealthCheck(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _setup_vms(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_host_not_found(self):
        result = server.health_check("ghost")
        self.assertIn("❌", result)

    def test_port_unreachable(self):
        with patch("server.ping_tools.ping_host", return_value={"up": False}):
            result = server.health_check("web01")
        self.assertIn("UNREACHABLE", result)

    def test_full_success(self):
        ssh_result = {
            "alias": "web01", "ip": "10.0.0.1", "exit_code": 0,
            "stdout": "load: 0.1\nMEM: 512/2048 MB\nDISK: 5G/20G (25%)\n",
            "stderr": "",
        }
        with patch("server.ping_tools.ping_host", return_value={"up": True}), \
             patch("server.ssh_tools.ssh_exec", return_value=ssh_result):
            result = server.health_check("web01")
        self.assertIn("✅", result)
        self.assertIn("web01", result)

    def test_ssh_no_credential(self):
        with patch("server.ping_tools.ping_host", return_value={"up": True}), \
             patch("server.ssh_tools.ssh_exec",
                   side_effect=ssh_tools.CredentialNotFound("web01")):
            result = server.health_check("web01")
        self.assertIn("credential", result.lower())

    def test_ssh_auth_failure(self):
        with patch("server.ping_tools.ping_host", return_value={"up": True}), \
             patch("server.ssh_tools.ssh_exec",
                   side_effect=ssh_tools.AuthFailure("web01", "10.0.0.1")):
            result = server.health_check("web01")
        self.assertIn("Authentication", result)


# ── host management tools ─────────────────────────────────────────────────────

class TestHostManagement(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _setup_vms(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_host_success(self):
        result = server.add_host("CORE", "db01", "10.0.0.2", port=22, user="admin")
        self.assertIn("✓", result)
        self.assertIn("db01", result)

    def test_add_host_duplicate(self):
        result = server.add_host("CORE", "web01", "10.0.0.1")
        self.assertIn("❌", result)

    def test_remove_host_success(self):
        result = server.remove_host("web01")
        self.assertIn("✓", result)

    def test_remove_host_not_found(self):
        result = server.remove_host("ghost")
        self.assertIn("❌", result)

    def test_update_host_success(self):
        result = server.update_host("web01", "user", "deploy")
        self.assertIn("✓", result)

    def test_update_host_not_found(self):
        result = server.update_host("ghost", "user", "deploy")
        self.assertIn("❌", result)

    def test_update_host_invalid_port(self):
        result = server.update_host("web01", "port", "notanumber")
        self.assertIn("❌", result)

    def test_list_hosts_returns_table(self):
        result = server.list_hosts()
        self.assertIn("web01", result)


# ── credential tools ──────────────────────────────────────────────────────────

class TestCredentialTools(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _setup_vms(self._tmp.name)
        import credentials
        credentials._invalidate()

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_and_check_credential(self):
        server.save_credential("web01", "s3cr3t")
        result = server.check_credential("web01")
        self.assertIn("✅", result)

    def test_check_credential_missing(self):
        result = server.check_credential("web01")
        self.assertIn("NOT FOUND", result)

    def test_delete_credential(self):
        server.save_credential("web01", "s3cr3t")
        result = server.delete_credential("web01")
        self.assertIn("✓", result)
        self.assertIn("NOT FOUND", server.check_credential("web01"))

    def test_save_credential_host_not_found(self):
        result = server.save_credential("ghost", "pass")
        self.assertIn("❌", result)

    def test_audit_credentials(self):
        server.save_credential("web01", "pass")
        result = server.audit_credentials()
        self.assertIn("web01", result)
        self.assertIn("10.0.0.1", result)


# ── template tools ────────────────────────────────────────────────────────────

class TestTemplateTools(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _setup_vms(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_and_list_template(self):
        server.add_template("my-cmd", "echo {{alias}}")
        result = server.list_templates()
        self.assertIn("my-cmd", result)

    def test_expand_template(self):
        server.add_template("greet", "echo {{alias}}")
        result = server.expand_template("greet", "web01")
        self.assertIn("web01", result)

    def test_remove_template(self):
        server.add_template("tmp", "uptime")
        result = server.remove_template("tmp")
        self.assertIn("✓", result)

    def test_remove_nonexistent_template(self):
        result = server.remove_template("ghost-tpl")
        self.assertIn("❌", result)


# ── exec log tools ────────────────────────────────────────────────────────────

class TestExecLogTools(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self._tmp.name
        import exec_log
        exec_log._LOG_FILE = Path(self._tmp.name) / "exec.log"

    def tearDown(self):
        self._tmp.cleanup()

    def test_read_exec_log_empty(self):
        result = server.read_exec_log()
        self.assertIsInstance(result, str)

    def test_clear_exec_log(self):
        result = server.clear_exec_log()
        self.assertIn("✓", result)


# ── Phase 1: destructive guard + force param ──────────────────────────────────

class TestRunCommandDestructiveGuard(unittest.TestCase):
    """Tests for the 🚫 guard and force=True bypass in run_command / run_command_multi."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _setup_vms(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    # --- run_command -----------------------------------------------------------

    def test_blocked_command_returns_blocked_icon(self):
        result = server.run_command("web01", "rm -rf /")
        self.assertIn("🚫", result)
        self.assertIn("blocked", result.lower())

    def test_blocked_command_does_not_call_ssh(self):
        with patch("server.ssh_tools.ssh_exec") as mock_exec:
            server.run_command("web01", "shutdown -h now")
            mock_exec.assert_not_called()

    def test_force_true_passes_through_to_ssh_exec(self):
        r = {"alias": "web01", "ip": "10.0.0.1", "exit_code": 0,
             "stdout": "done\n", "stderr": "", "elapsed_s": 0.1}
        with patch("server.ssh_tools.ssh_exec", return_value=r) as mock_exec:
            result = server.run_command("web01", "reboot", force=True)
        mock_exec.assert_called_once_with("web01", "reboot", force=True)
        self.assertIn("exit 0", result)

    def test_safe_command_never_blocked(self):
        r = {"alias": "web01", "ip": "10.0.0.1", "exit_code": 0,
             "stdout": "ok\n", "stderr": "", "elapsed_s": 0.1}
        with patch("server.ssh_tools.ssh_exec", return_value=r):
            result = server.run_command("web01", "df -h")
        self.assertNotIn("🚫", result)
        self.assertIn("exit 0", result)

    def test_destructive_blocked_exception_mapped_to_blocked_icon(self):
        """If ssh_tools raises DestructiveCommandBlocked, server returns 🚫."""
        with patch("server.ssh_tools.ssh_exec",
                   side_effect=ssh_tools.DestructiveCommandBlocked("blocked (test)")):
            result = server.run_command("web01", "rm -rf /", force=True)
        self.assertIn("🚫", result)

    # --- run_command_multi ----------------------------------------------------

    def test_multi_blocked_pre_check_returns_blocked_icon(self):
        result = server.run_command_multi("all", "mkfs.ext4 /dev/sda")
        self.assertIn("🚫", result)

    def test_multi_blocked_does_not_call_resolve_target(self):
        with patch("server.vms.resolve_target") as mock_resolve:
            server.run_command_multi("all", "dd if=/dev/zero of=/dev/sda")
            mock_resolve.assert_not_called()

    def test_multi_force_true_reaches_ssh_exec_multi(self):
        multi_results = [
            {"alias": "web01", "ip": "10.0.0.1", "exit_code": 0,
             "stdout": "rebooted\n", "stderr": "", "elapsed_s": 0.2},
        ]
        with patch("server.ssh_tools.ssh_exec_multi", return_value=multi_results) as mock_multi:
            result = server.run_command_multi("all", "reboot", force=True)
        mock_multi.assert_called_once_with(["web01"], "reboot", mode="sequential", force=True)
        self.assertNotIn("🚫", result)

    def test_multi_safe_command_not_blocked(self):
        multi_results = [
            {"alias": "web01", "ip": "10.0.0.1", "exit_code": 0,
             "stdout": "up\n", "stderr": "", "elapsed_s": 0.1},
        ]
        with patch("server.ssh_tools.ssh_exec_multi", return_value=multi_results):
            result = server.run_command_multi("all", "uptime")
        self.assertNotIn("🚫", result)
        self.assertIn("1 success", result)


# ── Phase 2: import_hosts ─────────────────────────────────────────────────────

class TestImportHosts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _setup_vms(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    # --- CSV ------------------------------------------------------------------

    def test_csv_imports_valid_rows(self):
        csv_content = (
            "project,alias,ip,port,user,env,zone\n"
            "CORE,srv01,10.0.1.1,22,ubuntu,staging,LAN\n"
            "CORE,srv02,10.0.1.2,22,ubuntu,staging,LAN\n"
        )
        result = server.import_hosts("csv", csv_content)
        self.assertIn("srv01", result)
        self.assertIn("srv02", result)
        self.assertIn("Added (2)", result)

    def test_csv_skips_duplicate_alias(self):
        csv_content = (
            "project,alias,ip\n"
            "CORE,web01,10.0.0.99\n"  # web01 already exists from _setup_vms
        )
        result = server.import_hosts("csv", csv_content)
        self.assertIn("Skipped (1)", result)
        self.assertIn("web01", result)

    def test_csv_reports_parse_error_on_missing_required(self):
        csv_content = "project,ip\nCORE,10.0.0.5\n"  # no alias column
        result = server.import_hosts("csv", csv_content)
        self.assertIn("❌", result)

    def test_csv_invalid_port_reports_error(self):
        csv_content = "project,alias,ip,port\nCORE,bad01,10.0.0.5,notanumber\n"
        result = server.import_hosts("csv", csv_content)
        self.assertIn("invalid port", result)

    def test_csv_tags_parsed_as_list(self):
        csv_content = (
            "project,alias,ip,tags\n"
            "CORE,tagged01,10.0.0.7,\"kubernetes,web\"\n"
        )
        server.import_hosts("csv", csv_content)
        import vms as vms_mod
        host = vms_mod.get_host("tagged01")
        self.assertIn("kubernetes", host.get("tags", []))

    # --- JSON -----------------------------------------------------------------

    def test_json_imports_valid_items(self):
        import json
        payload = json.dumps([
            {"project": "DB", "alias": "db01", "ip": "10.0.2.1", "user": "postgres"},
            {"project": "DB", "alias": "db02", "ip": "10.0.2.2"},
        ])
        result = server.import_hosts("json", payload)
        self.assertIn("db01", result)
        self.assertIn("db02", result)
        self.assertIn("Added (2)", result)

    def test_json_skips_item_missing_required_fields(self):
        import json
        payload = json.dumps([
            {"alias": "noproj", "ip": "10.0.0.9"},   # no project
        ])
        result = server.import_hosts("json", payload)
        self.assertIn("❌", result)

    def test_json_not_array_returns_error(self):
        import json
        result = server.import_hosts("json", json.dumps({"alias": "x", "ip": "1.1.1.1"}))
        self.assertIn("❌", result)
        self.assertIn("array", result)

    def test_json_malformed_returns_error(self):
        result = server.import_hosts("json", "{not valid json")
        self.assertIn("JSON parse error", result)

    # --- General --------------------------------------------------------------

    def test_unsupported_format_returns_error(self):
        result = server.import_hosts("xml", "<hosts/>")
        self.assertIn("❌", result)
        self.assertIn("Unsupported format", result)

    def test_empty_csv_returns_warning(self):
        result = server.import_hosts("csv", "project,alias,ip\n")  # header only, no rows
        self.assertIn("No hosts found", result)


# ── Phase 2: webhook notifications ───────────────────────────────────────────

class TestWebhookNotifications(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _setup_vms(self._tmp.name)
        # Ensure WEBHOOK_URL is set for tests that verify it fires
        os.environ["WEBHOOK_URL"] = "http://webhook.test/hook"
        server._WEBHOOK_URL = "http://webhook.test/hook"

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("WEBHOOK_URL", None)
        server._WEBHOOK_URL = ""

    def test_webhook_fired_on_nonzero_exit(self):
        r = {"alias": "web01", "ip": "10.0.0.1", "exit_code": 1,
             "stdout": "", "stderr": "error\n", "elapsed_s": 0.1}
        with patch("server.ssh_tools.ssh_exec", return_value=r):
            with patch("server._send_webhook") as mock_wh:
                server.run_command("web01", "badcmd")
        mock_wh.assert_called_once()
        payload = mock_wh.call_args[0][0]
        self.assertEqual(payload["event"], "command_failed")
        self.assertEqual(payload["alias"], "web01")
        self.assertEqual(payload["exit_code"], 1)

    def test_webhook_not_fired_on_success(self):
        r = {"alias": "web01", "ip": "10.0.0.1", "exit_code": 0,
             "stdout": "ok\n", "stderr": "", "elapsed_s": 0.1}
        with patch("server.ssh_tools.ssh_exec", return_value=r):
            with patch("server._send_webhook") as mock_wh:
                server.run_command("web01", "df -h")
        mock_wh.assert_not_called()

    def test_webhook_fired_for_down_host_in_ping(self):
        down = [{"alias": "web01", "ip": "10.0.0.1", "port": 22, "up": False}]
        with patch("server.ping_tools.ping_hosts", return_value=down):
            with patch("server.ping_tools.format_ping_results", return_value="table"):
                with patch("server._send_webhook") as mock_wh:
                    server.ping_hosts("all")
        mock_wh.assert_called_once()
        payload = mock_wh.call_args[0][0]
        self.assertEqual(payload["event"], "host_down")
        self.assertEqual(payload["alias"], "web01")

    def test_webhook_not_fired_for_up_hosts(self):
        up = [{"alias": "web01", "ip": "10.0.0.1", "port": 22, "up": True}]
        with patch("server.ping_tools.ping_hosts", return_value=up):
            with patch("server.ping_tools.format_ping_results", return_value="table"):
                with patch("server._send_webhook") as mock_wh:
                    server.ping_hosts("all")
        mock_wh.assert_not_called()

    def test_send_webhook_skipped_when_url_empty(self):
        server._WEBHOOK_URL = ""
        with patch("urllib.request.urlopen") as mock_open:
            server._send_webhook({"event": "test"})
        mock_open.assert_not_called()

    def test_send_webhook_silent_on_http_error(self):
        import urllib.error
        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("connection refused")):
            # Must not raise
            server._send_webhook({"event": "test"})


if __name__ == "__main__":
    unittest.main()

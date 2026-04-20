"""
Unit tests for ping_tools.py — TCP reachability checks.

No real network connections: socket.create_connection is patched throughout.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
from cryptography.fernet import Fernet
os.environ["CRED_MASTER_KEY"] = Fernet.generate_key().decode()

import ping_tools


# ── _tcp_check ────────────────────────────────────────────────────────────────

class TestTcpCheck(unittest.TestCase):
    def test_up_when_connection_succeeds(self):
        with patch("socket.create_connection", return_value=MagicMock()):
            result = ping_tools._tcp_check("web01", "10.0.0.1", 22)
        self.assertTrue(result["up"])
        self.assertEqual(result["alias"], "web01")
        self.assertEqual(result["ip"], "10.0.0.1")
        self.assertEqual(result["port"], 22)

    def test_down_when_connection_refused(self):
        with patch("socket.create_connection", side_effect=OSError("refused")):
            result = ping_tools._tcp_check("web01", "10.0.0.1", 22)
        self.assertFalse(result["up"])

    def test_custom_port_used(self):
        with patch("socket.create_connection") as mock_conn:
            mock_conn.return_value.__enter__ = lambda s: s
            mock_conn.return_value.__exit__ = MagicMock(return_value=False)
            ping_tools._tcp_check("web01", "10.0.0.1", port=2222)
            args = mock_conn.call_args[0]
            self.assertEqual(args[0], ("10.0.0.1", 2222))


# ── ping_host ─────────────────────────────────────────────────────────────────

class TestPingHost(unittest.TestCase):
    def test_up(self):
        with patch("socket.create_connection", return_value=MagicMock()):
            result = ping_tools.ping_host("10.0.0.5")
        self.assertTrue(result["up"])

    def test_down(self):
        with patch("socket.create_connection", side_effect=OSError):
            result = ping_tools.ping_host("10.0.0.5")
        self.assertFalse(result["up"])


# ── ping_hosts (multi-host) ───────────────────────────────────────────────────

class TestPingHosts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self._tmp.name
        import vms
        vms._vms_cache = None
        vms._vms_mtime = 0.0
        vms.init_empty()
        vms.write_host("WEB", {"alias": "web01", "ip": "10.0.0.1", "port": 22})
        vms.write_host("WEB", {"alias": "web02", "ip": "10.0.0.2", "port": 22})

    def tearDown(self):
        self._tmp.cleanup()

    def test_all_up(self):
        with patch("socket.create_connection", return_value=MagicMock()):
            results = ping_tools.ping_hosts(["web01", "web02"])
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r["up"] for r in results))

    def test_one_down(self):
        call_count = [0]

        def selective_fail(addr, timeout=5):
            call_count[0] += 1
            if addr[0] == "10.0.0.2":
                raise OSError("refused")
            return MagicMock()

        with patch("socket.create_connection", side_effect=selective_fail):
            results = ping_tools.ping_hosts(["web01", "web02"])

        up = [r for r in results if r["up"]]
        down = [r for r in results if not r["up"]]
        self.assertEqual(len(up), 1)
        self.assertEqual(len(down), 1)

    def test_unknown_alias_returns_error(self):
        results = ping_tools.ping_hosts(["ghost-host"])
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["up"])
        self.assertIn("error", results[0])

    def test_returns_alias_and_ip(self):
        with patch("socket.create_connection", return_value=MagicMock()):
            results = ping_tools.ping_hosts(["web01"])
        self.assertEqual(results[0]["alias"], "web01")
        self.assertEqual(results[0]["ip"], "10.0.0.1")


# ── format_ping_results ───────────────────────────────────────────────────────

class TestFormatPingResults(unittest.TestCase):
    def test_empty(self):
        result = ping_tools.format_ping_results([])
        self.assertIsInstance(result, str)

    def test_contains_alias(self):
        rows = [{"alias": "web01", "ip": "10.0.0.1", "port": 22, "up": True}]
        table = ping_tools.format_ping_results(rows)
        self.assertIn("web01", table)

    def test_up_and_down_shown(self):
        rows = [
            {"alias": "web01", "ip": "10.0.0.1", "port": 22, "up": True},
            {"alias": "db01",  "ip": "10.0.0.2", "port": 22, "up": False},
        ]
        table = ping_tools.format_ping_results(rows)
        self.assertIn("web01", table)
        self.assertIn("db01", table)


if __name__ == "__main__":
    unittest.main()

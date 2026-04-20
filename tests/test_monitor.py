"""
Unit tests for monitor.py — SSH metric collection and dashboard cache.

SSH calls and ping are fully mocked; no real network I/O.
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))
from cryptography.fernet import Fernet
os.environ["CRED_MASTER_KEY"] = Fernet.generate_key().decode()

import monitor


# ── Parser unit tests (no I/O) ────────────────────────────────────────────────

class TestParseCpu(unittest.TestCase):
    def test_standard_top_output(self):
        # Standard 'top -bn1 | grep Cpu' format — idle is 90.0, used should be 10.0
        raw = "%Cpu(s):  5.0 us,  2.0 sy,  0.0 ni, 90.0 id,  3.0 wa"
        pct = monitor._parse_cpu(raw)
        self.assertAlmostEqual(pct, 10.0)

    def test_returns_none_on_garbage(self):
        self.assertIsNone(monitor._parse_cpu("no cpu data here"))

    def test_100_percent_used(self):
        # idle = 0.0 → used = 100.0
        raw = "%Cpu(s):  100.0 us,  0.0 id"
        pct = monitor._parse_cpu(raw)
        self.assertAlmostEqual(pct, 100.0)


class TestParseMem(unittest.TestCase):
    def test_standard_free_output(self):
        raw = "Mem:           8000        3000        5000"
        mem = monitor._parse_mem(raw)
        self.assertEqual(mem["total_mb"], 8000)
        self.assertEqual(mem["used_mb"], 3000)
        self.assertEqual(mem["free_mb"], 5000)
        self.assertAlmostEqual(mem["pct"], 37.5)

    def test_returns_none_on_no_mem_line(self):
        self.assertIsNone(monitor._parse_mem("nothing here"))

    def test_zero_total_gives_zero_pct(self):
        raw = "Mem:              0           0           0"
        mem = monitor._parse_mem(raw)
        self.assertEqual(mem["pct"], 0)


class TestParseDisk(unittest.TestCase):
    def test_standard_df_output(self):
        raw = (
            "Filesystem      Size  Used Avail Use% Mounted on\n"
            "/dev/sda1        50G   20G   30G  40% /\n"
        )
        disk = monitor._parse_disk(raw)
        self.assertEqual(disk["size"], "50G")
        self.assertEqual(disk["used"], "20G")
        self.assertEqual(disk["pct"], "40%")

    def test_returns_none_on_no_root_mount(self):
        raw = "tmpfs  500M  100M  400M  20% /tmp\n"
        self.assertIsNone(monitor._parse_disk(raw))


class TestParseUptime(unittest.TestCase):
    def test_extracts_uptime_segment(self):
        raw = " 14:22:01 up 3 days,  2:15,  2 users,  load average: 0.10"
        result = monitor._parse_uptime(raw)
        self.assertIn("3 days", result)

    def test_returns_raw_when_no_up(self):
        raw = "some weird string"
        self.assertEqual(monitor._parse_uptime(raw), "some weird string")


# ── _collect_host ─────────────────────────────────────────────────────────────

class TestCollectHost(unittest.TestCase):
    def _host(self):
        return {"alias": "web01", "ip": "10.0.0.1", "port": 22,
                "user": "root", "_project": "CORE"}

    def test_unreachable_when_ping_down(self):
        with patch("ping_tools.ping_host", return_value={"up": False}):
            result = monitor._collect_host(self._host())
        self.assertEqual(result["status"], "unreachable")

    def test_ok_when_ssh_succeeds(self):
        ssh_output = (
            "===cpu===\n%Cpu(s):  5.0 us,  2.0 sy,  0.0 ni, 90.0 id\n"
            "===mem===\nMem:  8000  3000  5000\n"
            "===disk===\n/dev/sda1  50G  20G  30G  40% /\n"
            "===uptime===\n 10:00:00 up 2 days,  1:00\n"
        )
        with patch("ping_tools.ping_host", return_value={"up": True}):
            with patch("ssh_tools.ssh_exec", return_value={"stdout": ssh_output, "exit_code": 0}):
                result = monitor._collect_host(self._host())
        self.assertEqual(result["status"], "ok")
        self.assertIsNotNone(result["cpu_pct"])
        self.assertIsNotNone(result["mem"])
        self.assertIsNotNone(result["disk"])
        self.assertIsNotNone(result["uptime"])

    def test_error_when_ssh_raises(self):
        with patch("ping_tools.ping_host", return_value={"up": True}):
            with patch("ssh_tools.ssh_exec", side_effect=Exception("SSH failed")):
                result = monitor._collect_host(self._host())
        self.assertEqual(result["status"], "error")
        self.assertIn("SSH failed", result["error"])

    def test_result_has_required_keys(self):
        with patch("ping_tools.ping_host", return_value={"up": False}):
            result = monitor._collect_host(self._host())
        for key in ("alias", "ip", "project", "status", "cpu_pct", "mem", "disk", "uptime"):
            self.assertIn(key, result)


# ── Cache behaviour ───────────────────────────────────────────────────────────

class TestMonitorCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self._tmp.name
        import vms
        vms._vms_cache = None
        vms._vms_mtime = 0.0
        vms.init_empty()
        vms.write_host("CORE", {"alias": "web01", "ip": "10.0.0.1", "port": 22})
        # Clear the monitor cache
        with monitor._lock:
            monitor._cache.clear()

    def tearDown(self):
        self._tmp.cleanup()
        with monitor._lock:
            monitor._cache.clear()

    def test_cache_hit_prevents_second_ssh_call(self):
        fake = {"alias": "web01", "ip": "10.0.0.1", "project": "CORE",
                "status": "ok", "cpu_pct": 10.0, "mem": None,
                "disk": None, "uptime": None, "env": "", "zone": "", "error": None}

        with patch.object(monitor, "_collect_host", return_value=fake) as mock_collect:
            monitor.get_all_metrics()
            monitor.get_all_metrics()   # second call — should use cache
        mock_collect.assert_called_once()

    def test_force_bypasses_cache(self):
        fake = {"alias": "web01", "ip": "10.0.0.1", "project": "CORE",
                "status": "ok", "cpu_pct": 10.0, "mem": None,
                "disk": None, "uptime": None, "env": "", "zone": "", "error": None}

        with patch.object(monitor, "_collect_host", return_value=fake) as mock_collect:
            monitor.get_all_metrics()
            monitor.get_all_metrics(force=True)
        self.assertEqual(mock_collect.call_count, 2)

    def test_stale_cache_evicted_when_host_deleted(self):
        import vms
        # Seed the cache with a host that will be deleted
        with monitor._lock:
            monitor._cache["ghost"] = {"ts": time.time(), "data": {"alias": "ghost"}}

        # Delete the host and run get_all_metrics — ghost should be evicted
        fake = {"alias": "web01", "ip": "10.0.0.1", "project": "CORE",
                "status": "unreachable", "cpu_pct": None, "mem": None,
                "disk": None, "uptime": None, "env": "", "zone": "", "error": None}

        with patch.object(monitor, "_collect_host", return_value=fake):
            monitor.get_all_metrics()

        with monitor._lock:
            self.assertNotIn("ghost", monitor._cache)


if __name__ == "__main__":
    unittest.main()

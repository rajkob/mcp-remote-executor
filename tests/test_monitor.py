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


# ── TestWatchSet ──────────────────────────────────────────────────────────────

class TestWatchSet(unittest.TestCase):
    def setUp(self):
        monitor.watch_clear()

    def tearDown(self):
        monitor.watch_clear()

    def test_add_and_list(self):
        monitor.watch_add(["web01", "db01"])
        self.assertEqual(monitor.list_watched(), ["db01", "web01"])

    def test_remove_one(self):
        monitor.watch_add(["web01", "db01", "cache01"])
        monitor.watch_remove(["db01"])
        self.assertNotIn("db01", monitor.list_watched())
        self.assertIn("web01", monitor.list_watched())

    def test_remove_nonexistent_is_safe(self):
        monitor.watch_add(["web01"])
        monitor.watch_remove(["ghost"])  # must not raise
        self.assertIn("web01", monitor.list_watched())

    def test_clear_empties_set(self):
        monitor.watch_add(["web01", "db01"])
        monitor.watch_clear()
        self.assertEqual(monitor.list_watched(), [])

    def test_add_duplicates_deduped(self):
        monitor.watch_add(["web01"])
        monitor.watch_add(["web01"])
        self.assertEqual(monitor.list_watched().count("web01"), 1)

    def test_empty_watch_set_by_default(self):
        self.assertEqual(monitor.list_watched(), [])


# ── TestGetWatchedMetrics ─────────────────────────────────────────────────────

class TestGetWatchedMetrics(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self._tmp.name
        import vms
        vms._vms_cache = None
        vms._vms_mtime = 0.0
        vms.init_empty()
        vms.write_host("CORE", {"alias": "web01", "ip": "10.0.0.1", "port": 22})
        vms.write_host("CORE", {"alias": "db01",  "ip": "10.0.0.2", "port": 22})
        with monitor._lock:
            monitor._cache.clear()
        monitor.watch_clear()

    def tearDown(self):
        monitor.watch_clear()
        self._tmp.cleanup()

    def _fake_metric(self, alias, ip):
        return {"alias": alias, "ip": ip, "project": "CORE",
                "status": "ok", "cpu_pct": 5.0, "mem": None,
                "disk": None, "uptime": None, "env": "", "zone": "", "error": None}

    def test_returns_empty_when_nothing_watched(self):
        with patch.object(monitor, "_collect_host") as mock_collect:
            result = monitor.get_watched_metrics()
        mock_collect.assert_not_called()
        self.assertEqual(result, [])

    def test_returns_only_watched_hosts(self):
        monitor.watch_add(["web01"])
        fakes = {
            "web01": self._fake_metric("web01", "10.0.0.1"),
            "db01":  self._fake_metric("db01",  "10.0.0.2"),
        }
        with patch.object(monitor, "_collect_host", side_effect=lambda h: fakes[h["alias"]]):
            result = monitor.get_watched_metrics()
        aliases = [r["alias"] for r in result]
        self.assertIn("web01", aliases)
        self.assertNotIn("db01", aliases)

    def test_adding_second_host_expands_results(self):
        monitor.watch_add(["web01", "db01"])
        fake = self._fake_metric("web01", "10.0.0.1")
        fake2 = self._fake_metric("db01", "10.0.0.2")
        with patch.object(monitor, "_collect_host", side_effect=lambda h: self._fake_metric(h["alias"], h["ip"])):
            result = monitor.get_watched_metrics()
        self.assertEqual(len(result), 2)

    def test_stop_watch_removes_from_results(self):
        monitor.watch_add(["web01", "db01"])
        monitor.watch_remove(["db01"])
        with patch.object(monitor, "_collect_host", side_effect=lambda h: self._fake_metric(h["alias"], h["ip"])):
            result = monitor.get_watched_metrics()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["alias"], "web01")

    def test_fetch_for_aliases_does_not_modify_watch_set(self):
        monitor._fetch_for_aliases({"web01"})
        self.assertEqual(monitor.list_watched(), [])


# ── TestOnDemandMCPTools ──────────────────────────────────────────────────────

class TestOnDemandMCPTools(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self._tmp.name
        import vms
        vms._vms_cache = None
        vms._vms_mtime = 0.0
        vms.init_empty()
        vms.write_host("CORE", {"alias": "web01", "ip": "10.0.0.1", "port": 22})
        with monitor._lock:
            monitor._cache.clear()
        monitor.watch_clear()

        import server
        self._server = server

    def tearDown(self):
        monitor.watch_clear()
        self._tmp.cleanup()

    def _fake(self, alias="web01"):
        return {"alias": alias, "ip": "10.0.0.1", "project": "CORE",
                "status": "ok", "cpu_pct": 10.0,
                "mem": {"pct": 50.0, "total_mb": 8000, "used_mb": 4000, "free_mb": 4000},
                "disk": {"size": "50G", "used": "20G", "avail": "30G", "pct": "40%"},
                "uptime": "2 days", "env": "", "zone": "", "error": None}

    def test_start_monitoring_adds_to_watch_set(self):
        with patch.object(monitor, "_fetch_for_aliases", return_value=[self._fake()]):
            self._server.start_monitoring("web01")
        self.assertIn("web01", monitor.list_watched())

    def test_start_monitoring_unknown_target_returns_error(self):
        result = self._server.start_monitoring("ghost_project_xyz")
        self.assertIn("❌", result)
        self.assertEqual(monitor.list_watched(), [])

    def test_start_monitoring_returns_snapshot_table(self):
        with patch.object(monitor, "_fetch_for_aliases", return_value=[self._fake()]):
            result = self._server.start_monitoring("web01")
        self.assertIn("web01", result)
        self.assertIn("ok", result)

    def test_stop_monitoring_removes_from_watch_set(self):
        monitor.watch_add(["web01"])
        self._server.stop_monitoring("web01")
        self.assertNotIn("web01", monitor.list_watched())

    def test_stop_monitoring_all_clears_everything(self):
        monitor.watch_add(["web01"])
        result = self._server.stop_monitoring("all")
        self.assertEqual(monitor.list_watched(), [])
        self.assertIn("Stopped", result)

    def test_monitoring_status_when_empty(self):
        result = self._server.monitoring_status()
        self.assertIn("No hosts", result)
        self.assertIn("start_monitoring", result)

    def test_monitoring_status_shows_watched_hosts(self):
        monitor.watch_add(["web01"])
        with patch.object(monitor, "get_watched_metrics", return_value=[self._fake()]):
            result = self._server.monitoring_status()
        self.assertIn("web01", result)
        self.assertIn("ok", result)


if __name__ == "__main__":
    unittest.main()

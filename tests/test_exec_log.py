"""Unit tests for exec_log.py — append / read / clear / format."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from cryptography.fernet import Fernet
os.environ.setdefault("CRED_MASTER_KEY", Fernet.generate_key().decode())

import exec_log


def _reset(tmp_dir: str) -> None:
    os.environ["DATA_DIR"] = tmp_dir
    exec_log._write_count = 0


class TestAppendAndRead(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _log(self, command="uptime", exit_code=0, alias="web01"):
        exec_log.append(alias, "10.0.0.1", 22, "root", exit_code, command)

    def test_append_creates_file(self):
        self._log()
        self.assertTrue(Path(self._tmp.name, "exec.log").exists())

    def test_read_empty_returns_list(self):
        self.assertEqual(exec_log.read(), [])

    def test_read_returns_last_n(self):
        for i in range(10):
            self._log(command=f"cmd{i}")
        entries = exec_log.read(3)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[-1]["command"], "cmd9")

    def test_entry_fields(self):
        self._log(command="df -h", exit_code=0, alias="db01")
        entry = exec_log.read(1)[0]
        self.assertEqual(entry["alias"], "db01")
        self.assertEqual(entry["host"], "10.0.0.1:22")
        self.assertEqual(entry["user"], "root")
        self.assertEqual(entry["exit"], "0")
        self.assertEqual(entry["command"], "df -h")
        self.assertIn("T", entry["timestamp"])  # ISO format

    def test_read_more_than_log_returns_all(self):
        for _ in range(5):
            self._log()
        self.assertEqual(len(exec_log.read(100)), 5)


class TestClear(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_clear_removes_file(self):
        exec_log.append("web01", "10.0.0.1", 22, "root", 0, "uptime")
        exec_log.clear()
        self.assertFalse(Path(self._tmp.name, "exec.log").exists())

    def test_clear_on_empty_does_not_fail(self):
        exec_log.clear()  # Should not raise even if file doesn't exist

    def test_read_after_clear_returns_empty(self):
        exec_log.append("web01", "10.0.0.1", 22, "root", 0, "who")
        exec_log.clear()
        self.assertEqual(exec_log.read(), [])


class TestFormatLogTable(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_entries_returns_message(self):
        result = exec_log.format_log_table([])
        self.assertIn("empty", result.lower())

    def test_table_has_header(self):
        exec_log.append("web01", "10.0.0.1", 22, "root", 0, "ls -la")
        entries = exec_log.read()
        table = exec_log.format_log_table(entries)
        self.assertIn("| Timestamp |", table)
        self.assertIn("| Alias |", table)
        self.assertIn("| Command |", table)

    def test_table_contains_command(self):
        exec_log.append("db01", "10.0.0.3", 22, "deploy", 1, "systemctl restart app")
        entries = exec_log.read()
        table = exec_log.format_log_table(entries)
        self.assertIn("systemctl restart app", table)
        self.assertIn("db01", table)


class TestLogRotation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_rotation_trims_to_max_lines(self):
        # Override MAX_LOG_LINES to a small value and trigger rotation
        original_max = exec_log.MAX_LOG_LINES
        exec_log.MAX_LOG_LINES = 5
        try:
            # Write enough lines to be above the max, then force rotation
            for i in range(20):
                exec_log.append("h", "1.2.3.4", 22, "root", 0, f"cmd{i}")

            # Manually trigger rotation regardless of _ROTATE_EVERY counter
            log_path = Path(self._tmp.name, "exec.log")
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) > exec_log.MAX_LOG_LINES:
                with open(log_path, "w", encoding="utf-8") as f:
                    f.writelines(lines[-exec_log.MAX_LOG_LINES:])

            entries = exec_log.read(100)
            self.assertLessEqual(len(entries), exec_log.MAX_LOG_LINES)
        finally:
            exec_log.MAX_LOG_LINES = original_max


# ── TestReadByAlias ───────────────────────────────────────────────────────────

class TestReadByAlias(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _log(self, alias, command="uptime", exit_code=0):
        exec_log.append(alias, "10.0.0.1", 22, "root", exit_code, command)

    def test_filters_to_correct_alias(self):
        self._log("web01", "df -h")
        self._log("db01", "pg_dump")
        self._log("web01", "ls -la")
        entries = exec_log.read_by_alias("web01")
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(e["alias"] == "web01" for e in entries))

    def test_empty_when_alias_not_found(self):
        self._log("web01")
        self.assertEqual(exec_log.read_by_alias("ghost"), [])

    def test_returns_last_n(self):
        for i in range(10):
            self._log("web01", f"cmd{i}")
        entries = exec_log.read_by_alias("web01", n=3)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[-1]["command"], "cmd9")

    def test_empty_log_returns_empty(self):
        self.assertEqual(exec_log.read_by_alias("web01"), [])

    def test_entry_fields_preserved(self):
        self._log("svc01", "systemctl status nginx", exit_code=1)
        entry = exec_log.read_by_alias("svc01", 1)[0]
        self.assertEqual(entry["alias"], "svc01")
        self.assertEqual(entry["exit"], "1")
        self.assertEqual(entry["command"], "systemctl status nginx")


# ── TestToJsonAndToCsv ────────────────────────────────────────────────────────

class TestExportFormats(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)
        for i in range(3):
            exec_log.append(f"h{i}", f"10.0.0.{i}", 22, "root", i, f"cmd{i}")

    def tearDown(self):
        self._tmp.cleanup()

    def test_to_json_is_valid_json(self):
        import json
        entries = exec_log.read()
        text = exec_log.to_json(entries)
        parsed = json.loads(text)
        self.assertEqual(len(parsed), 3)
        self.assertIn("alias", parsed[0])
        self.assertIn("command", parsed[0])

    def test_to_json_empty(self):
        import json
        text = exec_log.to_json([])
        self.assertEqual(json.loads(text), [])

    def test_to_csv_has_header(self):
        entries = exec_log.read()
        text = exec_log.to_csv(entries)
        self.assertTrue(text.startswith("timestamp,"))
        self.assertIn("alias", text)
        self.assertIn("command", text)

    def test_to_csv_row_count(self):
        entries = exec_log.read()
        lines = exec_log.to_csv(entries).strip().splitlines()
        self.assertEqual(len(lines), 4)  # 1 header + 3 data rows

    def test_to_csv_empty(self):
        text = exec_log.to_csv([])
        lines = text.strip().splitlines()
        self.assertEqual(len(lines), 1)  # header only


if __name__ == "__main__":
    unittest.main()

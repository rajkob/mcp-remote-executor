"""Unit tests for vms.py — host inventory CRUD and target resolution."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Allow importing project modules without installing them
sys.path.insert(0, str(Path(__file__).parent.parent))
from cryptography.fernet import Fernet
os.environ.setdefault("CRED_MASTER_KEY", Fernet.generate_key().decode())

import vms


def _temp_vms(tmp_dir: str) -> None:
    """Point DATA_DIR at a temp directory and initialise an empty vms.yaml."""
    os.environ["DATA_DIR"] = tmp_dir
    # Reset module-level cache so each test starts clean
    vms._vms_cache = None
    vms._vms_mtime = 0.0
    vms.init_empty()


class TestInitEmpty(unittest.TestCase):
    def test_creates_file(self):
        with tempfile.TemporaryDirectory() as d:
            _temp_vms(d)
            self.assertTrue(Path(d, "vms.yaml").exists())

    def test_does_not_overwrite_existing(self):
        with tempfile.TemporaryDirectory() as d:
            _temp_vms(d)
            path = Path(d, "vms.yaml")
            path.write_text("custom: true\n")
            _temp_vms(d)
            self.assertIn("custom", path.read_text())


class TestWriteAndGetHost(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _temp_vms(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_add_and_get(self):
        vms.write_host("WEB", {"alias": "web01", "ip": "10.0.0.1", "port": 22})
        host = vms.get_host("web01")
        self.assertEqual(host["ip"], "10.0.0.1")
        self.assertEqual(host["_project"], "WEB")

    def test_duplicate_alias_raises(self):
        vms.write_host("WEB", {"alias": "web01", "ip": "10.0.0.1", "port": 22})
        with self.assertRaises(vms.DuplicateAlias):
            vms.write_host("WEB", {"alias": "web01", "ip": "10.0.0.2", "port": 22})

    def test_not_found_raises(self):
        with self.assertRaises(vms.HostNotFound):
            vms.get_host("nonexistent")

    def test_defaults_applied(self):
        vms.write_host("DB", {"alias": "db01", "ip": "10.0.0.5", "port": 22})
        host = vms.get_host("db01")
        # default user from VMS_SKELETON is 'root'
        self.assertEqual(host["user"], "root")
        self.assertEqual(host["port"], 22)


class TestDeleteHost(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _temp_vms(self._tmp.name)
        vms.write_host("WEB", {"alias": "web01", "ip": "10.0.0.1", "port": 22})

    def tearDown(self):
        self._tmp.cleanup()

    def test_delete_existing(self):
        project = vms.delete_host("web01")
        self.assertEqual(project, "WEB")
        with self.assertRaises(vms.HostNotFound):
            vms.get_host("web01")

    def test_delete_nonexistent_raises(self):
        with self.assertRaises(vms.HostNotFound):
            vms.delete_host("ghost")


class TestUpdateHost(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _temp_vms(self._tmp.name)
        vms.write_host("WEB", {"alias": "web01", "ip": "10.0.0.1", "port": 22})

    def tearDown(self):
        self._tmp.cleanup()

    def test_update_ip(self):
        vms.update_host("web01", "ip", "10.0.0.99")
        self.assertEqual(vms.get_host("web01")["ip"], "10.0.0.99")

    def test_update_port_as_int(self):
        vms.update_host("web01", "port", 2222)
        self.assertEqual(vms.get_host("web01")["port"], 2222)

    def test_update_nonexistent_raises(self):
        with self.assertRaises(vms.HostNotFound):
            vms.update_host("ghost", "ip", "1.2.3.4")


class TestResolveTarget(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _temp_vms(self._tmp.name)
        vms.write_host("CORE", {"alias": "web01", "ip": "10.0.0.1", "port": 22,
                                 "env": "production", "zone": "LAN", "tags": ["kubernetes"]})
        vms.write_host("CORE", {"alias": "web02", "ip": "10.0.0.2", "port": 22,
                                 "env": "staging", "zone": "DMZ", "tags": ["kubernetes"]})
        vms.write_host("DB",   {"alias": "db01",  "ip": "10.0.0.3", "port": 22})

    def tearDown(self):
        self._tmp.cleanup()

    def test_all(self):
        self.assertEqual(len(vms.resolve_target("all")), 3)

    def test_exact_alias(self):
        result = vms.resolve_target("web01")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["alias"], "web01")

    def test_project(self):
        result = vms.resolve_target("CORE")
        self.assertEqual(len(result), 2)

    def test_tag(self):
        result = vms.resolve_target("kubernetes")
        self.assertEqual(len(result), 2)

    def test_env(self):
        result = vms.resolve_target("production")
        self.assertEqual(len(result), 1)

    def test_zone_case_insensitive(self):
        result = vms.resolve_target("lan")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["alias"], "web01")

    def test_no_match_raises(self):
        with self.assertRaises(vms.HostNotFound):
            vms.resolve_target("does-not-exist")


class TestTemplates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _temp_vms(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_and_load(self):
        vms.write_template("disk", "df -h")
        templates = vms.load_templates()
        self.assertIn("disk", templates)
        self.assertEqual(templates["disk"], "df -h")

    def test_expand_placeholder(self):
        vms.write_template("drain", "kubectl drain {{alias}} --force")
        vms.write_host("K8S", {"alias": "master", "ip": "10.0.0.1", "port": 22})
        result = vms.expand_template("drain", "master")
        self.assertIn("master", result)
        self.assertNotIn("{{alias}}", result)

    def test_delete_template(self):
        vms.write_template("mem", "free -h")
        vms.delete_template("mem")
        self.assertNotIn("mem", vms.load_templates())

    def test_delete_nonexistent_raises(self):
        with self.assertRaises(KeyError):
            vms.delete_template("ghost-template")


if __name__ == "__main__":
    unittest.main()

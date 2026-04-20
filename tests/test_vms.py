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


# ── TestExpandTemplateVars ────────────────────────────────────────────────────

class TestExpandTemplateVars(unittest.TestCase):
    """Tests for extended {{ip}}, {{user}}, {{env}}, {{zone}}, {{port}} substitution."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _temp_vms(self._tmp.name)
        vms.write_host("K8S", {
            "alias": "master",
            "ip": "10.0.0.5",
            "port": 2222,
            "user": "deploy",
            "env": "production",
            "zone": "DMZ",
        })

    def tearDown(self):
        self._tmp.cleanup()

    def test_alias_substitution(self):
        vms.write_template("drain", "kubectl drain {{alias}} --force")
        self.assertEqual(vms.expand_template("drain", "master"),
                         "kubectl drain master --force")

    def test_ip_substitution(self):
        vms.write_template("ssh-ip", "ssh {{user}}@{{ip}} -p {{port}}")
        result = vms.expand_template("ssh-ip", "master")
        self.assertIn("10.0.0.5", result)
        self.assertIn("deploy", result)
        self.assertIn("2222", result)

    def test_env_substitution(self):
        vms.write_template("cfg", "cat /etc/{{env}}/config.yaml")
        result = vms.expand_template("cfg", "master")
        self.assertIn("production", result)
        self.assertNotIn("{{env}}", result)

    def test_zone_substitution(self):
        vms.write_template("fw", "ufw allow from {{zone}}")
        result = vms.expand_template("fw", "master")
        self.assertIn("DMZ", result)

    def test_all_vars_in_one_template(self):
        vms.write_template("full", "{{alias}} {{ip}} {{port}} {{user}} {{env}} {{zone}}")
        result = vms.expand_template("full", "master")
        self.assertEqual(result, "master 10.0.0.5 2222 deploy production DMZ")

    def test_unknown_alias_falls_back_gracefully(self):
        vms.write_template("disk", "df -h  # {{alias}}")
        # alias that doesn't exist — falls back to alias string for {{alias}},
        # empty string for host-specific fields
        result = vms.expand_template("disk", "ghost")
        self.assertIn("ghost", result)

    def test_no_placeholders_unchanged(self):
        vms.write_template("plain", "df -h")
        self.assertEqual(vms.expand_template("plain", "master"), "df -h")

    def test_missing_template_raises_key_error(self):
        with self.assertRaises(KeyError):
            vms.expand_template("nonexistent", "master")


# ── TestWriteHostsBulk ────────────────────────────────────────────────────────

class TestWriteHostsBulk(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _temp_vms(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_bulk_adds_all_hosts(self):
        entries = [
            ("WEB", {"alias": "web01", "ip": "10.0.0.1"}),
            ("WEB", {"alias": "web02", "ip": "10.0.0.2"}),
            ("DB",  {"alias": "db01",  "ip": "10.0.0.3"}),
        ]
        result = vms.write_hosts_bulk(entries)
        self.assertEqual(sorted(result["added"]), ["db01", "web01", "web02"])
        self.assertEqual(result["skipped"], [])

    def test_bulk_skips_duplicates(self):
        vms.write_host("WEB", {"alias": "web01", "ip": "10.0.0.1"})
        entries = [
            ("WEB", {"alias": "web01", "ip": "10.0.0.9"}),  # duplicate
            ("WEB", {"alias": "web02", "ip": "10.0.0.2"}),
        ]
        result = vms.write_hosts_bulk(entries)
        self.assertIn("web02", result["added"])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["alias"], "web01")
        self.assertIn("duplicate", result["skipped"][0]["reason"])

    def test_bulk_empty_list(self):
        result = vms.write_hosts_bulk([])
        self.assertEqual(result["added"], [])
        self.assertEqual(result["skipped"], [])


if __name__ == "__main__":
    unittest.main()

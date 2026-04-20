"""Unit tests for credentials.py — Fernet-encrypted credential store."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cryptography.fernet import Fernet

# Generate a valid 32-byte Fernet key for this test session
_TEST_KEY = Fernet.generate_key().decode()
os.environ["CRED_MASTER_KEY"] = _TEST_KEY


import credentials


def _reset(tmp_dir: str) -> None:
    """Point DATA_DIR at tmp_dir and invalidate the credential cache."""
    os.environ["DATA_DIR"] = tmp_dir
    credentials._invalidate()


class TestSaveAndGet(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_and_retrieve(self):
        credentials.save_credential("10.0.0.1", "root", "s3cr3t")
        result = credentials.get_credential("10.0.0.1", "root")
        self.assertEqual(result, "s3cr3t")

    def test_missing_returns_none(self):
        result = credentials.get_credential("192.168.1.1", "admin")
        self.assertIsNone(result)

    def test_overwrite(self):
        credentials.save_credential("10.0.0.1", "root", "first")
        credentials.save_credential("10.0.0.1", "root", "second")
        self.assertEqual(credentials.get_credential("10.0.0.1", "root"), "second")

    def test_multiple_users_same_ip(self):
        credentials.save_credential("10.0.0.1", "root", "pass-root")
        credentials.save_credential("10.0.0.1", "deploy", "pass-deploy")
        self.assertEqual(credentials.get_credential("10.0.0.1", "root"), "pass-root")
        self.assertEqual(credentials.get_credential("10.0.0.1", "deploy"), "pass-deploy")


class TestCredentialExists(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_exists_after_save(self):
        credentials.save_credential("10.0.0.5", "ubuntu", "pw")
        self.assertTrue(credentials.credential_exists("10.0.0.5", "ubuntu"))

    def test_not_exists_before_save(self):
        self.assertFalse(credentials.credential_exists("10.0.0.5", "ubuntu"))


class TestDeleteCredential(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_delete_existing(self):
        credentials.save_credential("10.0.0.1", "root", "pw")
        deleted = credentials.delete_credential("10.0.0.1", "root")
        self.assertTrue(deleted)
        self.assertIsNone(credentials.get_credential("10.0.0.1", "root"))

    def test_delete_nonexistent_returns_false(self):
        deleted = credentials.delete_credential("10.0.0.9", "nobody")
        self.assertFalse(deleted)

    def test_delete_does_not_affect_others(self):
        credentials.save_credential("10.0.0.1", "root", "pw1")
        credentials.save_credential("10.0.0.2", "root", "pw2")
        credentials.delete_credential("10.0.0.1", "root")
        self.assertEqual(credentials.get_credential("10.0.0.2", "root"), "pw2")


class TestListStored(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty(self):
        self.assertEqual(credentials.list_stored(), [])

    def test_lists_without_passwords(self):
        credentials.save_credential("10.0.0.1", "root", "secret")
        entries = credentials.list_stored()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["ip"], "10.0.0.1")
        self.assertEqual(entries[0]["user"], "root")
        self.assertNotIn("password", entries[0])

    def test_multiple_entries(self):
        credentials.save_credential("10.0.0.1", "root", "a")
        credentials.save_credential("10.0.0.2", "admin", "b")
        self.assertEqual(len(credentials.list_stored()), 2)


class TestCacheInvalidation(unittest.TestCase):
    """Ensure the in-memory cache is correctly invalidated on writes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_save_invalidates_cache(self):
        credentials.save_credential("10.0.0.1", "root", "first")
        # Prime the cache
        credentials.get_credential("10.0.0.1", "root")
        # Write again — cache must be invalidated so next read is fresh
        credentials.save_credential("10.0.0.1", "root", "second")
        self.assertEqual(credentials.get_credential("10.0.0.1", "root"), "second")

    def test_delete_invalidates_cache(self):
        credentials.save_credential("10.0.0.1", "root", "pw")
        # Prime the cache
        credentials.get_credential("10.0.0.1", "root")
        credentials.delete_credential("10.0.0.1", "root")
        self.assertIsNone(credentials.get_credential("10.0.0.1", "root"))


class TestWrongKey(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        _reset(self._tmp.name)

    def tearDown(self):
        # Restore the test key so other tests are not affected
        os.environ["CRED_MASTER_KEY"] = _TEST_KEY
        credentials._invalidate()
        self._tmp.cleanup()

    def test_missing_key_raises(self):
        credentials.save_credential("10.0.0.1", "root", "pw")
        del os.environ["CRED_MASTER_KEY"]
        credentials._invalidate()
        with self.assertRaises(RuntimeError):
            credentials.get_credential("10.0.0.1", "root")


if __name__ == "__main__":
    unittest.main()

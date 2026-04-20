"""
Unit tests for init.py — first-run setup helper.

Filesystem and subprocess calls are mocked or redirected to temp dirs.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import init


# ── check_wslconfig ───────────────────────────────────────────────────────────

class TestCheckWslconfig(unittest.TestCase):

    @patch("init.platform.system", return_value="Linux")
    def test_non_windows_returns_false(self, _plat):
        result = init.check_wslconfig()
        self.assertFalse(result)

    def test_windows_already_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            wslconfig = Path(tmp) / ".wslconfig"
            wslconfig.write_text("[wsl2]\nnetworkingMode=mirrored\n", encoding="utf-8")
            with patch("init.platform.system", return_value="Windows"), \
                 patch("init.Path.home", return_value=Path(tmp)):
                result = init.check_wslconfig()
        self.assertFalse(result)

    def test_windows_wsl2_section_exists_adds_mirrored(self):
        with tempfile.TemporaryDirectory() as tmp:
            wslconfig = Path(tmp) / ".wslconfig"
            wslconfig.write_text("[wsl2]\nmemory=4GB\n", encoding="utf-8")
            with patch("init.platform.system", return_value="Windows"), \
                 patch("init.Path.home", return_value=Path(tmp)):
                result = init.check_wslconfig()
            content = wslconfig.read_text(encoding="utf-8")
        self.assertTrue(result)
        self.assertIn("networkingMode=mirrored", content)

    def test_windows_no_wsl2_section_appends_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            wslconfig = Path(tmp) / ".wslconfig"
            wslconfig.write_text("[user]\ndefaultUser=rajko\n", encoding="utf-8")
            with patch("init.platform.system", return_value="Windows"), \
                 patch("init.Path.home", return_value=Path(tmp)):
                result = init.check_wslconfig()
            content = wslconfig.read_text(encoding="utf-8")
        self.assertTrue(result)
        self.assertIn("[wsl2]", content)
        self.assertIn("networkingMode=mirrored", content)

    def test_windows_no_wslconfig_file_creates_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch("init.platform.system", return_value="Windows"), \
                 patch("init.Path.home", return_value=Path(tmp)):
                result = init.check_wslconfig()
            content = (Path(tmp) / ".wslconfig").read_text(encoding="utf-8")
        self.assertTrue(result)
        self.assertIn("[wsl2]", content)
        self.assertIn("networkingMode=mirrored", content)


# ── restart_wsl ───────────────────────────────────────────────────────────────

class TestRestartWsl(unittest.TestCase):

    @patch("init.subprocess.run", return_value=MagicMock(returncode=0))
    def test_success(self, mock_run):
        init.restart_wsl()  # should not raise
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("--shutdown", args)

    @patch("init.subprocess.run", side_effect=FileNotFoundError)
    def test_wsl_not_found(self, _run):
        init.restart_wsl()  # should not raise

    @patch("init.subprocess.run", return_value=MagicMock(returncode=1, stderr="error"))
    def test_nonzero_exit(self, _run):
        init.restart_wsl()  # should not raise


# ── main ──────────────────────────────────────────────────────────────────────

class TestMain(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _run_main(self):
        """Run init.main() with BASE_DIR, DATA_DIR etc. redirected to a temp dir."""
        with patch.object(init, "BASE_DIR", self._base), \
             patch.object(init, "DATA_DIR", self._base / "data"), \
             patch.object(init, "OUTPUT_DIR", self._base / "data" / "output"), \
             patch.object(init, "VMS_FILE", self._base / "data" / "vms.yaml"), \
             patch.object(init, "CRED_FILE", self._base / "data" / "credentials"), \
             patch.object(init, "ENV_FILE", self._base / ".env"), \
             patch("init.check_wslconfig", return_value=False):
            init.main()

    def test_creates_data_directories(self):
        self._run_main()
        self.assertTrue((self._base / "data").is_dir())
        self.assertTrue((self._base / "data" / "output").is_dir())

    def test_creates_vms_yaml(self):
        self._run_main()
        vms_file = self._base / "data" / "vms.yaml"
        self.assertTrue(vms_file.exists())
        self.assertIn("projects:", vms_file.read_text(encoding="utf-8"))

    def test_skips_existing_vms_yaml(self):
        (self._base / "data").mkdir(parents=True, exist_ok=True)
        vms = self._base / "data" / "vms.yaml"
        vms.write_text("custom: content\n", encoding="utf-8")
        self._run_main()
        self.assertEqual(vms.read_text(encoding="utf-8"), "custom: content\n")

    def test_creates_credentials_file(self):
        self._run_main()
        cred_file = self._base / "data" / "credentials"
        self.assertTrue(cred_file.exists())
        self.assertEqual(cred_file.read_bytes(), b"{}")

    def test_skips_existing_credentials_file(self):
        (self._base / "data").mkdir(parents=True, exist_ok=True)
        cred = self._base / "data" / "credentials"
        cred.write_bytes(b"existing")
        self._run_main()
        self.assertEqual(cred.read_bytes(), b"existing")

    def test_creates_env_file_with_key(self):
        self._run_main()
        env_file = self._base / ".env"
        self.assertTrue(env_file.exists())
        content = env_file.read_text(encoding="utf-8")
        self.assertIn("CRED_MASTER_KEY=", content)

    def test_does_not_overwrite_existing_env(self):
        env_file = self._base / ".env"
        env_file.write_text("CRED_MASTER_KEY=original\n", encoding="utf-8")
        self._run_main()
        self.assertEqual(env_file.read_text(encoding="utf-8"), "CRED_MASTER_KEY=original\n")

    def test_env_key_is_valid_fernet_key(self):
        self._run_main()
        from cryptography.fernet import Fernet
        content = (self._base / ".env").read_text(encoding="utf-8")
        key = content.split("=", 1)[1].strip()
        # Should not raise
        f = Fernet(key.encode())
        token = f.encrypt(b"test")
        self.assertEqual(f.decrypt(token), b"test")


if __name__ == "__main__":
    unittest.main()

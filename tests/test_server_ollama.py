"""
Unit tests for server.py — Ollama AI tools: ai_analyze, ollama_status.

All external I/O (Ollama HTTP, SSH) is mocked; no real connections are made.
"""
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

# Provide required env vars before importing server
from cryptography.fernet import Fernet
os.environ.setdefault("CRED_MASTER_KEY", Fernet.generate_key().decode())
os.environ.setdefault("DATA_DIR", "/tmp/test_data")

import server
import ssh_tools


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ssh_result(stdout="ok", stderr="", exit_code=0, ip="10.0.0.1"):
    return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code, "ip": ip}


def _mock_urlopen(response_body: dict | None = None):
    """Return a context-manager mock for urllib.request.urlopen."""
    cm = MagicMock()
    if response_body is not None:
        cm.__enter__.return_value.read.return_value = json.dumps(response_body).encode()
    cm.__enter__.return_value.status = 200
    return cm


# ── ollama_status ─────────────────────────────────────────────────────────────

class TestOllamaStatus(unittest.TestCase):

    @patch("server._ollama_available", return_value=False)
    def test_not_available(self, _mock):
        result = server.ollama_status()
        self.assertIn("not reachable", result)

    @patch("server.urllib.request.urlopen")
    @patch("server._ollama_available", return_value=True)
    def test_idle_no_models(self, _avail, mock_open):
        mock_open.return_value = _mock_urlopen({"models": []})
        result = server.ollama_status()
        self.assertIn("running", result.lower())
        self.assertIn("idle", result.lower())

    @patch("server.urllib.request.urlopen")
    @patch("server._ollama_available", return_value=True)
    def test_model_loaded(self, _avail, mock_open):
        mock_open.return_value = _mock_urlopen({
            "models": [{"name": "qwen2.5:7b", "size": 4_500_000_000}]
        })
        result = server.ollama_status()
        self.assertIn("qwen2.5:7b", result)
        self.assertIn("4.5", result)

    @patch("server.urllib.request.urlopen")
    @patch("server._ollama_available", return_value=True)
    def test_api_ps_exception(self, _avail, mock_open):
        mock_open.side_effect = Exception("timeout")
        result = server.ollama_status()
        self.assertIn("failed", result.lower())


# ── ai_analyze ────────────────────────────────────────────────────────────────

class TestAiAnalyze(unittest.TestCase):

    @patch("server._ollama_available", return_value=False)
    def test_ollama_not_available(self, _mock):
        result = server.ai_analyze("web01", "check disk")
        self.assertIn("not reachable", result.lower())

    @patch("server._ollama_chat", return_value="Disk is fine.")
    @patch("server.ssh_tools.ssh_exec", return_value=_ssh_result("Filesystem 80%"))
    @patch("server._ollama_available", return_value=True)
    def test_disk_question_routes_df(self, _avail, mock_ssh, _chat):
        server.ai_analyze("web01", "check disk usage")
        cmd = mock_ssh.call_args[0][1]
        self.assertIn("df -h", cmd)

    @patch("server._ollama_chat", return_value="RAM OK")
    @patch("server.ssh_tools.ssh_exec", return_value=_ssh_result("Mem: 1000MB"))
    @patch("server._ollama_available", return_value=True)
    def test_memory_question_routes_free(self, _avail, mock_ssh, _chat):
        server.ai_analyze("web01", "check memory usage")
        cmd = mock_ssh.call_args[0][1]
        self.assertIn("free -m", cmd)

    @patch("server._ollama_chat", return_value="CPU normal")
    @patch("server.ssh_tools.ssh_exec", return_value=_ssh_result("load: 0.1"))
    @patch("server._ollama_available", return_value=True)
    def test_cpu_question_routes_ps(self, _avail, mock_ssh, _chat):
        server.ai_analyze("web01", "high cpu")
        cmd = mock_ssh.call_args[0][1]
        self.assertIn("ps aux", cmd)

    @patch("server._ollama_chat", return_value="No errors found.")
    @patch("server.ssh_tools.ssh_exec", return_value=_ssh_result("No entries."))
    @patch("server._ollama_available", return_value=True)
    def test_log_question_routes_journalctl(self, _avail, mock_ssh, _chat):
        server.ai_analyze("web01", "check errors in logs")
        cmd = mock_ssh.call_args[0][1]
        self.assertIn("journalctl", cmd)

    @patch("server._ollama_chat", return_value="All good.")
    @patch("server.ssh_tools.ssh_exec", return_value=_ssh_result("up 2 days"))
    @patch("server._ollama_available", return_value=True)
    def test_unknown_question_uses_default_command(self, _avail, mock_ssh, _chat):
        server.ai_analyze("web01", "general health")
        cmd = mock_ssh.call_args[0][1]
        self.assertIn("uptime", cmd)

    @patch("server._ollama_chat", return_value="Analysis here.")
    @patch("server.ssh_tools.ssh_exec", return_value=_ssh_result("some output"))
    @patch("server._ollama_available", return_value=True)
    def test_returns_formatted_analysis(self, _avail, mock_ssh, mock_chat):
        result = server.ai_analyze("web01", "check disk")
        self.assertIn("AI Analysis", result)
        self.assertIn("Analysis here.", result)

    @patch("server.ssh_tools.ssh_exec", side_effect=ssh_tools.CredentialNotFound("web01"))
    @patch("server._ollama_available", return_value=True)
    def test_credential_not_found(self, _avail, _ssh):
        result = server.ai_analyze("web01", "check disk")
        self.assertIn("❌", result)

    @patch("server.ssh_tools.ssh_exec", side_effect=ssh_tools.HostUnreachable("web01", "10.0.0.1", 22))
    @patch("server._ollama_available", return_value=True)
    def test_host_unreachable(self, _avail, _ssh):
        result = server.ai_analyze("web01", "check disk")
        self.assertIn("⚠️", result)

    @patch("server.ssh_tools.ssh_exec", side_effect=server.vms.HostNotFound("web01"))
    @patch("server._ollama_available", return_value=True)
    def test_host_not_found(self, _avail, _ssh):
        result = server.ai_analyze("web01", "check disk")
        self.assertIn("❌", result)

    @patch("server.ssh_tools.ssh_exec", return_value=_ssh_result(""))
    @patch("server._ollama_available", return_value=True)
    def test_empty_ssh_output(self, _avail, _ssh):
        result = server.ai_analyze("web01", "check disk")
        self.assertIn("No output", result)

    @patch("server._ollama_chat", side_effect=Exception("model not loaded"))
    @patch("server.ssh_tools.ssh_exec", return_value=_ssh_result("raw output here"))
    @patch("server._ollama_available", return_value=True)
    def test_ollama_chat_failure_returns_raw_output(self, _avail, _ssh, _chat):
        result = server.ai_analyze("web01", "check disk")
        self.assertIn("Ollama analysis failed", result)
        self.assertIn("raw output here", result)


if __name__ == "__main__":
    unittest.main()

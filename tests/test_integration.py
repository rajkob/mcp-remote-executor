"""
Integration smoke tests — verify server.py loads cleanly and exposes the
expected number of MCP tools without requiring a running server or real hosts.
"""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cryptography.fernet import Fernet
os.environ.setdefault("CRED_MASTER_KEY", Fernet.generate_key().decode())

# Use a fresh temp dir so vms.yaml and exec.log don't pollute the real data dir
_tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = _tmp

import server  # noqa: E402  must come after env setup

EXPECTED_TOOLS = {
    "list_hosts", "add_host", "remove_host", "update_host", "import_hosts",
    "save_credential", "check_credential", "delete_credential", "audit_credentials",
    "run_command", "run_command_multi", "upload_file", "download_file",
    "ping_hosts", "health_check",
    "start_monitoring", "stop_monitoring", "monitoring_status",
    "list_templates", "expand_template", "add_template", "remove_template",
    "read_exec_log", "clear_exec_log", "save_output",
    "command_history", "export_exec_log",
    "ai_analyze", "ollama_status",
}


class TestServerImport(unittest.TestCase):
    """server.py imported cleanly and the FastMCP instance is present."""

    def test_mcp_instance_exists(self):
        self.assertIsNotNone(server.mcp)

    def test_mcp_name(self):
        self.assertEqual(server.mcp.name, "remote-executor")

    def test_tool_count(self):
        """Exactly 24 tools must be registered — update EXPECTED_TOOLS if new tools are added."""
        tools = asyncio.run(server.mcp.list_tools())
        self.assertEqual(
            len(tools),
            29,
            f"Expected 29 tools, found {len(tools)}: {[t.name for t in tools]}",
        )

    def test_expected_tool_names(self):
        """All expected tool names must be present."""
        tools = asyncio.run(server.mcp.list_tools())
        names = {t.name for t in tools}
        missing = EXPECTED_TOOLS - names
        self.assertFalse(missing, f"Missing expected tools: {missing}")


if __name__ == "__main__":
    unittest.main()

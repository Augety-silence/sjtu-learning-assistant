"""两种 stdio 启动入口共享的本地工作区契约。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine

from sjtu_learning_assistant.local_settings import LocalSettings, SettingsStore
from sjtu_learning_assistant.mcp_server import MCPServer, create_stdio_registry


class MCPWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.config = base / "config"
        self.config.mkdir()
        self.root = base / "workspace"
        self.root.mkdir()
        self.archive = base / "archive"
        self.archive.mkdir()
        self.store = SettingsStore(self.config / "settings.json")
        self.store.save(LocalSettings(archive_root=str(self.archive)))
        self.engine = create_engine("sqlite://")
        self.addCleanup(self.engine.dispose)

    def registry(self):
        with patch("sjtu_learning_assistant.mcp_server.native_calendar_service", return_value=object()):
            return create_stdio_registry(self.engine, settings_store=self.store)

    def configure(self, payload):
        (self.config / "ai_workspace.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_selected_workspace_read_and_proposal_annotations(self):
        self.configure({"version": 1, "root": str(self.root)})
        (self.root / "note.md").write_text("工作区读取验收", encoding="utf-8")
        server = MCPServer(self.registry())
        tools = server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        by_name = {tool["name"]: tool for tool in tools}
        self.assertTrue(by_name["read_workspace_file"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["propose_file_change"]["annotations"]["readOnlyHint"])
        self.assertNotIn("apply_file_change", by_name)
        result = server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read_workspace_file", "arguments": {"path": "note.md"}}})
        self.assertFalse(result["result"]["isError"])
        self.assertIn("工作区读取验收", json.dumps(result, ensure_ascii=False))
        for path in ("../config/settings.json", str(self.config / "settings.json")):
            denied = server.handle_request({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "read_workspace_file", "arguments": {"path": path}}})
            self.assertTrue("error" in denied or denied.get("result", {}).get("isError"))

    def test_unselected_invalid_and_protected_roots_do_not_register_files(self):
        self.assertNotIn("read_workspace_file", self.registry().names)
        for root in (str(self.config), str(self.archive), str(Path.home()), str(self.root / "missing")):
            self.configure({"version": 1, "root": root})
            self.assertNotIn("read_workspace_file", self.registry().names)
        for payload in ({"root": ""}, {"root": "  "}, {"root": 12}, [], {}):
            self.configure(payload)
            self.assertNotIn("read_workspace_file", self.registry().names)
        (self.config / "ai_workspace.json").write_text("invalid json", encoding="utf-8")
        self.assertNotIn("read_workspace_file", self.registry().names)

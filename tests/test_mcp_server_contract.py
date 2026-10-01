from __future__ import annotations

import io
import json
import unittest
from dataclasses import dataclass

from sjtu_learning_assistant.agent_runtime.tools import AgentToolError, ToolExecution
from sjtu_learning_assistant.mcp_server import MCPServer


@dataclass
class FakeRegistry:
    fail_with_secret: bool = False

    @property
    def names(self):
        return frozenset({"safe_query"})

    def definitions(self, allowed_tools):
        return [
            {
                "type": "function",
                "function": {
                    "name": "safe_query",
                    "description": "只读查询",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            }
            for name in allowed_tools
            if name == "safe_query"
        ]

    def execute(self, name, arguments, allowed_tools):
        if self.fail_with_secret:
            raise AgentToolError(
                "token=super-secret path=/Users/alice/private and Bearer abc.def"
            )
        if name != "safe_query" or name not in allowed_tools:
            raise AgentToolError("未知工具。")
        return ToolExecution(name, {"items": [{"title": arguments.get("query")}]}, {}, {})


class MCPServerContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = MCPServer(FakeRegistry())

    def request(self, method, params=None, request_id=1):
        request = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        return self.server.handle_request(request)

    def test_initialize_list_call_and_ping_contract(self) -> None:
        initialized = self.request(
            "initialize", {"protocolVersion": "2024-11-05"}
        )
        self.assertEqual("2024-11-05", initialized["result"]["protocolVersion"])
        self.assertEqual(False, initialized["result"]["capabilities"]["tools"]["listChanged"])

        listed = self.request("tools/list")
        self.assertEqual("safe_query", listed["result"]["tools"][0]["name"])
        self.assertIn("inputSchema", listed["result"]["tools"][0])

        called = self.request(
            "tools/call", {"name": "safe_query", "arguments": {"query": "课程"}}
        )
        self.assertFalse(called["result"]["isError"])
        self.assertEqual("课程", called["result"]["structuredContent"]["items"][0]["title"])
        self.assertEqual({}, self.request("ping")["result"])

    def test_jsonrpc_errors_notifications_and_secret_redaction(self) -> None:
        unknown = self.request("unknown/method", request_id="x")
        self.assertEqual(-32601, unknown["error"]["code"])
        invalid = self.server.handle_request({"jsonrpc": "1.0", "id": 2, "method": "ping"})
        self.assertEqual(-32600, invalid["error"]["code"])
        self.assertIsNone(
            self.server.handle_request(
                {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
        )

        failed = MCPServer(FakeRegistry(fail_with_secret=True)).handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "safe_query", "arguments": {}},
            }
        )
        encoded = json.dumps(failed, ensure_ascii=False)
        self.assertTrue(failed["result"]["isError"])
        self.assertNotIn("super-secret", encoded)
        self.assertNotIn("/Users/alice", encoded)
        self.assertNotIn("abc.def", encoded)

    def test_stdio_emits_only_protocol_responses(self) -> None:
        source = io.StringIO(
            "not-json\n"
            + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"})
            + "\n"
        )
        sink = io.StringIO()
        self.server.serve(source, sink)
        lines = sink.getvalue().splitlines()
        self.assertEqual(2, len(lines))
        self.assertEqual(-32700, json.loads(lines[0])["error"]["code"])
        self.assertEqual({}, json.loads(lines[1])["result"])

    def test_invalid_params_and_batch(self) -> None:
        response = self.request("tools/call", [])
        self.assertEqual(-32602, response["error"]["code"])
        batch = self.server.handle_message(
            [
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            ]
        )
        self.assertEqual(1, len(batch))
        self.assertEqual({}, batch[0]["result"])


if __name__ == "__main__":
    unittest.main()

"""通过 stdio 暴露本地只读学习工具的最小 MCP/JSON-RPC 服务。

协议消息逐行写入 stdout；诊断信息（如有）只能写入 stderr。模块不会在导入时
创建数据库连接，因而既适合桌面打包，也便于使用内存流进行契约测试。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Any, IO, Mapping, Sequence

from sjtu_learning_assistant import __version__
from sjtu_learning_assistant.agent_runtime.tools import AgentToolError, ReadOnlyToolRegistry

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {"2024-11-05", "2025-03-26", "2025-06-18"}
)
SERVER_NAME = "sjtu-learning-assistant-readonly"

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603

_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9_.~+/=-]+"),
    re.compile(r"(?i)\b(token|password|secret|api[_ -]?key)\s*[=:]\s*[^\s,;]+"),
    re.compile(r"(?:/Users|/home)/[^\s]+"),
    re.compile(r"\b[A-Za-z]:\\[^\s]+"),
    re.compile(r"\w+://[^\s/:]+:[^\s]+@[^\s]+"),
)


@dataclass(frozen=True)
class JsonRpcError(Exception):
    """只携带可安全返回给客户端的 JSON-RPC 错误。"""

    code: int
    message: str
    data: Mapping[str, Any] | None = None


def redact_error_message(value: object, *, fallback: str = "请求处理失败。") -> str:
    """截断并隐藏常见凭据、用户目录和带认证信息的 URL。"""

    text = " ".join(str(value or "").split())
    if not text:
        return fallback
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[已隐藏]", text)
    # 错误是协议的一部分，限制长度可避免异常对象意外输出大块本地数据。
    return text[:300] + ("…" if len(text) > 300 else "")


def _error_response(
    request_id: object,
    code: int,
    message: str,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = dict(data)
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def _result_response(request_id: object, result: object) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _valid_id(value: object) -> bool:
    return value is None or (
        type(value) in {str, int}  # bool 是 int 的子类，但不是合法的请求 ID。
    )


class MCPServer:
    """将 :class:`ReadOnlyToolRegistry` 适配为 MCP tools 服务。"""

    def __init__(
        self,
        registry: ReadOnlyToolRegistry,
        *,
        allowed_tools: Sequence[str] | None = None,
        server_name: str = SERVER_NAME,
        server_version: str = __version__,
    ) -> None:
        self.registry = registry
        known = registry.names
        selected = tuple(sorted(known)) if allowed_tools is None else tuple(allowed_tools)
        if len(selected) != len(set(selected)) or any(name not in known for name in selected):
            raise ValueError("allowed_tools 包含重复或未知工具。")
        self.allowed_tools = selected
        self.server_name = server_name
        self.server_version = server_version

    def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        protocol_version = (
            requested
            if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS
            else MCP_PROTOCOL_VERSION
        )
        return {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": self.server_name,
                "version": self.server_version,
            },
        }

    def _list_tools(self) -> dict[str, Any]:
        definitions = self.registry.definitions(self.allowed_tools)
        tools: list[dict[str, Any]] = []
        for definition in definitions:
            function = definition.get("function", {})
            tools.append(
                {
                    "name": function["name"],
                    "description": function.get("description", ""),
                    "inputSchema": function.get(
                        "parameters",
                        {"type": "object", "additionalProperties": False},
                    ),
                }
            )
        return {"tools": tools}

    def _call_tool(self, params: Mapping[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if type(name) is not str or not name or type(arguments) is not dict:
            raise JsonRpcError(_INVALID_PARAMS, "工具名称或参数格式无效。")
        try:
            execution = self.registry.execute(name, arguments, self.allowed_tools)
        except AgentToolError as exc:
            message = redact_error_message(exc, fallback="工具调用失败。")
            return {
                "content": [{"type": "text", "text": message}],
                "isError": True,
            }
        except Exception:
            # 数据库驱动、文件系统或第三方库异常不得跨越协议边界。
            return {
                "content": [{"type": "text", "text": "工具调用失败。"}],
                "isError": True,
            }
        encoded = json.dumps(
            execution.result,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        return {
            "content": [{"type": "text", "text": encoded}],
            "structuredContent": execution.result,
            "isError": False,
        }

    @staticmethod
    def _require_params(params: object) -> Mapping[str, Any]:
        if params is None:
            return {}
        if type(params) is not dict:
            raise JsonRpcError(_INVALID_PARAMS, "params 必须是对象。")
        return params

    def _dispatch(self, method: str, params: object) -> object:
        mapped = self._require_params(params)
        if method == "initialize":
            return self._initialize(mapped)
        if method == "ping":
            return {}
        if method == "tools/list":
            return self._list_tools()
        if method == "tools/call":
            return self._call_tool(mapped)
        raise JsonRpcError(_METHOD_NOT_FOUND, "Method not found")

    def handle_request(self, request: object) -> dict[str, Any] | None:
        """处理一个已解析的 JSON-RPC 消息；通知返回 ``None``。"""

        if type(request) is not dict:
            return _error_response(None, _INVALID_REQUEST, "Invalid Request")
        request_id = request.get("id")
        if "id" in request and not _valid_id(request_id):
            return _error_response(None, _INVALID_REQUEST, "Invalid Request")
        is_notification = "id" not in request
        method = request.get("method")
        if (
            request.get("jsonrpc") != JSONRPC_VERSION
            or type(method) is not str
            or not method
            or set(request) - {"jsonrpc", "id", "method", "params"}
        ):
            return _error_response(None, _INVALID_REQUEST, "Invalid Request")

        # 标准生命周期通知没有响应；其他未知通知也按 JSON-RPC 规则静默处理。
        if is_notification:
            if method in {"notifications/initialized", "notifications/cancelled"}:
                return None
            try:
                self._dispatch(method, request.get("params"))
            except Exception:
                pass
            return None

        try:
            result = self._dispatch(method, request.get("params"))
            return _result_response(request_id, result)
        except JsonRpcError as exc:
            return _error_response(request_id, exc.code, exc.message, exc.data)
        except Exception:
            return _error_response(request_id, _INTERNAL_ERROR, "Internal error")

    def handle_message(self, message: object) -> dict[str, Any] | list[dict[str, Any]] | None:
        """处理单条请求或 JSON-RPC batch。"""

        if type(message) is list:
            if not message:
                return _error_response(None, _INVALID_REQUEST, "Invalid Request")
            responses = [self.handle_request(item) for item in message]
            visible = [item for item in responses if item is not None]
            return visible or None
        return self.handle_request(message)

    def process_line(self, line: str | bytes) -> str | None:
        """解析并处理一行输入，返回不含换行符的协议 JSON。"""

        try:
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            message = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            response: object = _error_response(None, _PARSE_ERROR, "Parse error")
        else:
            response = self.handle_message(message)
        if response is None:
            return None
        return json.dumps(response, ensure_ascii=False, separators=(",", ":"))

    def serve(
        self,
        input_stream: IO[str] | None = None,
        output_stream: IO[str] | None = None,
    ) -> None:
        """阻塞运行换行分隔的 stdio 传输。"""

        source = input_stream or sys.stdin
        sink = output_stream or sys.stdout
        for line in source:
            response = self.process_line(line)
            if response is not None:
                sink.write(response + "\n")
                sink.flush()


# 兼容常见的类名拼写，同时保持首选名称与 MCP 官方缩写一致。
McpServer = MCPServer
JsonRpcMCPServer = MCPServer


def handle_jsonrpc_message(
    registry: ReadOnlyToolRegistry,
    message: object,
    *,
    allowed_tools: Sequence[str] | None = None,
) -> dict[str, Any] | list[dict[str, Any]] | None:
    """无状态调用入口，便于嵌入式宿主和契约测试复用。"""

    return MCPServer(registry, allowed_tools=allowed_tools).handle_message(message)


def run_stdio_server(
    registry: ReadOnlyToolRegistry,
    *,
    allowed_tools: Sequence[str] | None = None,
    input_stream: IO[str] | None = None,
    output_stream: IO[str] | None = None,
) -> None:
    MCPServer(registry, allowed_tools=allowed_tools).serve(input_stream, output_stream)


serve_stdio = run_stdio_server


def main() -> int:
    """使用应用默认数据库启动独立服务。"""

    try:
        from sjtu_learning_assistant.database import create_database_engine

        engine = create_database_engine()
        run_stdio_server(ReadOnlyToolRegistry(engine))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        # 绝不污染 stdout；启动错误也只给出脱敏后的 stderr 诊断。
        sys.stderr.write(redact_error_message(exc, fallback="MCP 服务启动失败。") + "\n")
        return 1
    finally:
        if "engine" in locals():
            engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

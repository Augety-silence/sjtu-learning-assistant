"""Shared request-size limits for AI chat validation and agent context assembly."""

MAX_CHAT_MESSAGES = 64
MAX_CHAT_MESSAGE_CHARS = 14_000
MAX_CHAT_CONTEXT_CHARS = 60_000
AGENT_CONTEXT_CHAR_BUDGET = 54_000
SYSTEM_MESSAGE_CHAR_BUDGET = 13_500
MAX_SSE_RESPONSE_BYTES = 16 * 1024 * 1024
# 单次流式响应的总时长上限：4096 token 的长回答或推理模型在网关上可超过 2 分钟。
MAX_SSE_STREAM_SECONDS = 300.0
# 流式读超时（两次收到数据之间的最长间隔，含首包）。网关偶发“建立连接后不再返回任何数据”，
# 用较短的读超时尽快失败，由 Agent 在尚未输出任何内容时自动重试。
STREAM_IDLE_TIMEOUT_SECONDS = 60.0

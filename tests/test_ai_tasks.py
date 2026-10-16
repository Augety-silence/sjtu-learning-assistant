from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import create_engine

from sjtu_learning_assistant.ai_classifier import AIClassificationError, OpenAIClassificationClient
from sjtu_learning_assistant.ai_tasks import (
    AIBudgetExceeded,
    AITaskError,
    AITaskLedger,
    BudgetedAIClient,
    normalize_page_context,
)
from sjtu_learning_assistant.models import Base


def ledger() -> AITaskLedger:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return AITaskLedger(engine)


def test_page_context_is_bounded_and_task_budget_is_persisted():
    value = normalize_page_context(
        {
            "page_id": "calendar",
            "source": "page-header",
            "title": "日程",
            "entity_ids": [1, "event-2", 1],
            "selection": {"week": "current"},
        }
    )
    assert value["entity_ids"] == ["1", "event-2"]
    store = ledger()
    created = store.create(value, model="deepseek-chat")
    task = created["task"]
    assert task["page_id"] == "calendar"
    assert task["budget"] == {
        "input_tokens": 4000,
        "output_tokens": 1000,
        "max_model_calls": 1,
    }
    store.start(task["id"])
    call_index, allowed = store.reserve_call(task["id"], 1400)
    assert (call_index, allowed) == (1, 1000)
    store.record_call(
        task["id"],
        call_index,
        model="deepseek-chat",
        requested_output_tokens=allowed,
        response={
            "usage": {"prompt_tokens": 31, "completion_tokens": 7, "total_tokens": 38},
            "response_metadata": {"finish_reason": "stop"},
        },
    )
    with pytest.raises(AIBudgetExceeded):
        store.reserve_call(task["id"], 1)
    snapshot = store.get(task["id"])
    assert snapshot["task"]["usage"]["total_tokens"] == 38
    assert [event["kind"] for event in snapshot["events"]] == [
        "queued",
        "running",
        "usage",
    ]


def test_unknown_usage_is_not_reported_as_zero_and_consumes_reserved_output():
    store = ledger()
    task_id = store.create(None, model="deepseek-chat")["task"]["id"]
    store.start(task_id)

    class FailingClient:
        model = "deepseek-chat"

        def chat_completion(self, messages, **kwargs):
            raise RuntimeError("provider disconnected")

    client = BudgetedAIClient(FailingClient(), store, task_id)
    with pytest.raises(RuntimeError):
        client.chat_completion([{"role": "user", "content": "hi"}], max_tokens=800)
    usage = store.get(task_id)["task"]["usage"]
    assert usage["unknown_usage_calls"] == 1
    assert usage["completion_tokens"] == 0
    assert usage["charged_output_tokens"] == 800


def test_invalid_context_never_accepts_arbitrary_renderer_data():
    with pytest.raises(AITaskError):
        normalize_page_context({"page_id": "calendar", "raw_page": "secret"})
    with pytest.raises(AITaskError):
        normalize_page_context({"page_id": "unknown"})


def test_real_sse_parser_emits_deltas_and_preserves_provider_usage():
    body = "".join(
        [
            'data: {"choices":[{"delta":{"content":"你"},"finish_reason":null}]}\n\n',
            'data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}],',
            '"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n',
            "data: [DONE]\n\n",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    client = OpenAIClassificationClient(
        api_key="fake",
        transport=httpx.MockTransport(handler),
        limiter=lambda: None,
    )
    deltas: list[tuple[str, str]] = []
    try:
        result = client.chat_completion_stream(
            [{"role": "user", "content": "问候"}],
            on_delta=lambda kind, value: deltas.append((kind, value)),
        )
    finally:
        client.close()
    assert deltas == [("content", "你"), ("content", "好")]
    assert result["content"] == "你好"
    assert result["usage"] == {
        "prompt_tokens": 5,
        "completion_tokens": 2,
        "total_tokens": 7,
    }


def test_real_sse_parser_assembles_streamed_tool_call_fragments():
    body = "".join(
        [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",',
            '"type":"function","function":{"name":"list_","arguments":"{\\"li"}}]}}]}\n\n',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,',
            '"function":{"name":"courses","arguments":"mit\\":2}"}}]},',
            '"finish_reason":"tool_calls"}]}\n\n',
            "data: [DONE]\n\n",
        ]
    )
    definitions = [
        {
            "type": "function",
            "function": {
                "name": "list_courses",
                "description": "列出课程",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["tools"] == definitions
        assert payload["tool_choice"] == "auto"
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    client = OpenAIClassificationClient(
        api_key="fake",
        transport=httpx.MockTransport(handler),
        limiter=lambda: None,
    )
    try:
        result = client.chat_completion_stream(
            [{"role": "user", "content": "课程"}],
            tools=definitions,
            tool_choice="auto",
        )
    finally:
        client.close()
    assert result["content"] == ""
    assert result["tool_calls"] == [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "list_courses", "arguments": '{"limit":2}'},
        }
    ]


def test_stream_validates_assistant_tool_and_tool_result_messages_like_nonstream():
    definitions = [{
        "type": "function",
        "function": {"name": "list_courses", "description": "列出课程", "parameters": {"type": "object", "properties": {}}},
    }]
    messages = [
        {"role": "user", "content": "课程"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "list_courses", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "list_courses", "content": "[]"},
    ]
    accepted_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        accepted_requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"完成"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    client = OpenAIClassificationClient(
        api_key="fake", transport=httpx.MockTransport(handler), limiter=lambda: None
    )
    try:
        result = client.chat_completion_stream(messages, tools=definitions, tool_choice="auto")
    finally:
        client.close()
    assert result["content"] == "完成"
    assert accepted_requests[0]["messages"][-2:] == messages[1:]
    assert accepted_requests[0]["tools"] == definitions

    invalid_messages = [
        [*messages[:-1], {**messages[-1], "extra": "reject"}],
        [*messages[:-1], {"role": "tool", "name": "list_courses", "content": "[]"}],
        [messages[0], {"role": "function", "content": "reject"}],
        [{**messages[0], "name": "not-a-tool"}],
        [{"role": "assistant", "content": "answer", "tool_call_id": "call-1"}],
        [messages[0], {**messages[2], "tool_calls": []}],
        [*messages[:-1], {**messages[-1], "tool_call_id": "x" * 129}],
        [*messages[:-1], {**messages[-1], "name": "x" * 81}],
        [
            messages[0],
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "", "type": "function",
                "function": {"name": "list_courses", "arguments": "{}"},
            }]},
        ],
    ]
    for invalid in invalid_messages:
        no_request = []

        def must_not_send(request: httpx.Request) -> httpx.Response:
            no_request.append(request)
            return handler(request)

        invalid_client = OpenAIClassificationClient(
            api_key="fake", transport=httpx.MockTransport(must_not_send), limiter=lambda: None
        )
        try:
            with pytest.raises(AIClassificationError):
                invalid_client.chat_completion_stream(invalid, tools=definitions)
            assert no_request == []
        finally:
            invalid_client.close()


def test_sse_parser_rejects_oversized_or_unknown_tool_delta_fields():
    import copy

    base_call = {
        "index": 0,
        "id": "call-1",
        "type": "function",
        "function": {"name": "list_courses", "arguments": "{}"},
    }
    invalid_calls = []
    for key, value in (("id", "x" * 129),):
        call = copy.deepcopy(base_call)
        call[key] = value
        invalid_calls.append(call)
    call = copy.deepcopy(base_call)
    call["function"]["name"] = "x" * 81
    invalid_calls.append(call)
    call = copy.deepcopy(base_call)
    call["function"]["arguments"] = "x" * 8_001
    invalid_calls.append(call)
    call = copy.deepcopy(base_call)
    call["unexpected"] = "reject"
    invalid_calls.append(call)

    for tool_call in invalid_calls:
        payload = {"choices": [{"delta": {"tool_calls": [tool_call]}, "finish_reason": "tool_calls"}]}
        body = "data: " + json.dumps(payload) + "\n\ndata: [DONE]\n\n"

        def handler(request: httpx.Request, *, response_body=body) -> httpx.Response:
            return httpx.Response(
                200,
                text=response_body,
                headers={"content-type": "text/event-stream"},
                request=request,
            )

        client = OpenAIClassificationClient(
            api_key="fake", transport=httpx.MockTransport(handler), limiter=lambda: None
        )
        try:
            with pytest.raises(AIClassificationError) as raised:
                client.chat_completion_stream([{"role": "user", "content": "课程"}])
            assert raised.value.category == "response_format"
        finally:
            client.close()


def test_stream_options_unsupported_falls_back_without_changing_tool_messages():
    requests = []
    definitions = [{
        "type": "function",
        "function": {"name": "list_courses", "description": "列出课程", "parameters": {"type": "object", "properties": {}}},
    }]
    messages = [
        {"role": "user", "content": "列出课程"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "list_courses", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call-1", "name": "list_courses", "content": "[]"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if "stream_options" in payload:
            return httpx.Response(400, json={"error": "stream_options unsupported"}, request=request)
        return httpx.Response(
            200,
            text='data: {"choices":[{"delta":{"content":"fallback"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
            headers={"content-type": "text/event-stream"},
            request=request,
        )

    client = OpenAIClassificationClient(
        api_key="fake", transport=httpx.MockTransport(handler), limiter=lambda: None
    )
    try:
        result = client.chat_completion_stream(messages, tools=definitions, tool_choice="auto")
    finally:
        client.close()
    assert result["content"] == "fallback"
    assert len(requests) == 2
    assert "stream_options" in requests[0]
    assert "stream_options" not in requests[1]
    assert requests[0]["messages"] == requests[1]["messages"]
    assert requests[0]["messages"][-3:] == [
        messages[0],
        {**messages[1], "content": ""},
        messages[2],
    ]
    assert requests[0]["tools"] == requests[1]["tools"] == definitions
    assert requests[0]["tool_choice"] == requests[1]["tool_choice"] == "auto"

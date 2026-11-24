"""模拟资料与本地 HTTP：请求即时中断、会话隔离和已付费证据复用。"""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import httpx
import pytest

from sjtu_learning_assistant.agent_runtime import AgentLoop, AgentPreset
from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient, AIClassificationError
from sjtu_learning_assistant.chat_steering import ChatSteeringInterrupt
from sjtu_learning_assistant.ai_tasks import AITaskCancelled
from sjtu_learning_assistant.ai_runtime import SharedAIRuntime


@pytest.fixture
def endpoint():
    state = SimpleNamespace(started=threading.Event(), release=threading.Event(),
                            partial=threading.Event(), connections=[])

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            text = body['messages'][-1]['content']
            state.connections.append(self.client_address)
            if text == '等响应头':
                state.started.set()
                state.release.wait(8)
            if body.get('stream'):
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Connection', 'close')
                self.end_headers()
                if text == '等首 token':
                    state.started.set()
                    state.release.wait(8)
                try:
                    if text == '部分响应':
                        frame = {'choices': [{'delta': {'content': '已付费片段'}}],
                                 'usage': {'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 14}}
                        self.wfile.write(('data: ' + json.dumps(frame) + '\n\n').encode())
                        self.wfile.flush()
                        state.partial.set()
                        state.release.wait(8)
                    self.wfile.write(b'data: {"choices":[{"delta":{"content":"B"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
                    self.wfile.flush()
                except (OSError, ValueError):
                    pass
                self.close_connection = True
            else:
                payload = json.dumps({'choices': [{'message': {'content': '独立回复'}}],
                                      'usage': {'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 14}}).encode()
                try:
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (OSError, ValueError):
                    pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = OpenAIClassificationClient(api_key='local-test-only', base_url=f'http://127.0.0.1:{server.server_port}',
                                        limiter=lambda: None, timeout=10)
    yield client, state
    state.release.set()
    client.close()
    server.shutdown()
    server.server_close()
    thread.join(2)


@pytest.mark.parametrize('mode,text', [('stream', '等响应头'), ('stream', '等首 token'), ('post', '等响应头')])
def test_interrupt_silent_request_leaves_shared_client_and_other_session_usable(endpoint, mode, text):
    client, state = endpoint
    interrupted, done = threading.Event(), threading.Event()
    errors = []
    # 先成功调用同一客户端，随后仍须能中断等待响应头的请求。
    assert client.chat_completion([{'role': 'user', 'content': '预热'}])['content'] == '独立回复'

    def check():
        if interrupted.is_set():
            raise ChatSteeringInterrupt('新补充')

    def run():
        try:
            method = client.chat_completion_stream if mode == 'stream' else client.chat_completion
            method([{'role': 'user', 'content': text}], request_check=check)
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    worker = threading.Thread(target=run)
    worker.start()
    assert state.started.wait(2)
    # B 在 A 仍阻塞时完成，无须等 A 的任务队列。
    assert client.chat_completion([{'role': 'user', 'content': 'B会话'}])['content'] == '独立回复'
    started = time.monotonic()
    interrupted.set()
    assert done.wait(1.5), '必须在模型没有输出时也能即时中断'
    assert time.monotonic() - started < 1.5
    assert len(errors) == 1 and isinstance(errors[0], ChatSteeringInterrupt)
    assert not client._client.is_closed
    assert client.chat_completion([{'role': 'user', 'content': 'B继续'}])['content'] == '独立回复'
    worker.join(1)


def test_cancel_keeps_received_paid_content_and_usage(endpoint):
    client, state = endpoint
    cancelled, visible = threading.Event(), threading.Event()
    errors = []

    def check():
        if cancelled.is_set():
            raise AITaskCancelled('停止 A')

    def run():
        try:
            client.chat_completion_stream([{'role': 'user', 'content': '部分响应'}],
                                           request_check=check, on_delta=lambda kind, text: visible.set())
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert state.partial.wait(2) and visible.wait(2)
    cancelled.set()
    worker.join(1.5)
    assert not worker.is_alive()
    assert isinstance(errors[0], AITaskCancelled)
    assert errors[0].partial_response['content'] == '已付费片段'
    assert errors[0].partial_response['usage']['total_tokens'] == 14


def test_network_timeout_keeps_paid_partial_and_actual_usage():
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"paid partial"}}],"usage":{"total_tokens":14}}\n\n'
            raise httpx.ReadTimeout('模拟断流')

    client = OpenAIClassificationClient(api_key='local-test-only', limiter=lambda: None,
                                        transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Stream())))
    try:
        with pytest.raises(AIClassificationError) as caught:
            client.chat_completion_stream([{'role': 'user', 'content': '模拟资料'}], on_delta=lambda *args: None)
        assert caught.value.category == 'timeout' and caught.value.emitted_output
        assert caught.value.partial_response['content'] == 'paid partial'
        assert caught.value.partial_response['usage']['total_tokens'] == 14
    finally:
        client.close()


def test_full_paid_post_is_kept_when_supplement_arrives_at_response_boundary():
    arrived = threading.Event()

    def respond(request):
        arrived.set()
        return httpx.Response(200, json={'choices': [{'message': {'content': '已付费完整回答'}}],
                                        'usage': {'prompt_tokens': 11, 'completion_tokens': 3, 'total_tokens': 14}})

    def check():
        if arrived.is_set():
            raise ChatSteeringInterrupt('补充')

    client = OpenAIClassificationClient(api_key='local-test-only', transport=httpx.MockTransport(respond), limiter=lambda: None)
    try:
        with pytest.raises(ChatSteeringInterrupt) as caught:
            client.chat_completion([{'role': 'user', 'content': '测试'}], request_check=check)
        assert caught.value.partial_response['raw_response']['choices'][0]['message']['content'] == '已付费完整回答'
        assert caught.value.partial_response['usage']['total_tokens'] == 14
    finally:
        client.close()


@pytest.mark.parametrize('stream', [False, True])
def test_supplement_during_shared_rate_queue_finishes_reservation(tmp_path, stream):
    pending = threading.Event()
    transport_calls = []
    runtime = SharedAIRuntime(tmp_path / 'runtime.sqlite3', 'test', rpm=10, tpm=300000,
                              max_inflight=1, sleeper=lambda seconds: pending.set())
    active = runtime.reserve({'messages': [{'role': 'user', 'content': '占据请求位'}]})
    client = OpenAIClassificationClient(api_key='local-test-only', limiter=lambda: None,
        transport=httpx.MockTransport(lambda request: transport_calls.append(request)))
    client._runtime = runtime

    def check():
        if pending.is_set():
            raise ChatSteeringInterrupt('排队时的新补充')

    try:
        method = client.chat_completion_stream if stream else client.chat_completion
        with pytest.raises(ChatSteeringInterrupt):
            method([{'role': 'user', 'content': '等待请求位'}], request_check=check)
        with sqlite3.connect(runtime.path) as db:
            queued = db.execute('SELECT started,ended,outcome FROM requests WHERE id<>?', (active,)).fetchall()
        assert len(queued) == 1 and queued[0][0] is None and queued[0][1] is not None
        assert queued[0][2] == 'cancelled' and not transport_calls
        runtime.finish(active, {}, 'test', time.time())
        next_id = runtime.reserve({'messages': [{'role': 'user', 'content': '其它会话'}]})
        runtime.finish(next_id, {}, 'test', time.time())
    finally:
        client.close()


class Control:
    def __init__(self):
        self.pending, self.responses, self.events = [], [], []
        self.sealed = False
        self.task_id = 'test-task'
        self.ledger = SimpleNamespace(append_event=lambda *args: self.events.append(args))

    def has_pending(self):
        return bool(self.pending)

    def take_pending(self):
        result, self.pending = self.pending, []
        return result

    def check_request(self):
        if self.pending:
            raise ChatSteeringInterrupt('补充')

    def record_response(self, response):
        self.responses.append(deepcopy(response))

    def seal_if_idle(self):
        if self.pending:
            return False
        self.sealed = True
        return True

    def finish_pending(self):
        self.sealed = True
        return self.take_pending()

    def add(self):
        self.pending.append(dict(content='使用原页核实结论', request_id='supplement-1'))


def registry(execute=lambda name, args, allowed: None):
    return SimpleNamespace(names=frozenset({'read'}), definitions=lambda allowed: [], execute=execute)


def make_loop(client, tools, **kwargs):
    return AgentLoop(client, tools, AgentPreset('test', '测试', '测试', ('read',), '仅依据模拟证据。'), **kwargs)


def test_supplement_reuses_executed_evidence_skips_stale_tools_and_keeps_task():
    control, executed, requests = Control(), [], []

    def execute(name, args, allowed):
        executed.append(name)
        control.add()
        return SimpleNamespace(name=name, result={'text': '原页证据，第6页'}, arguments_summary={}, result_summary={})

    class Client:
        def chat_completion(self, messages, **kwargs):
            requests.append(deepcopy(messages))
            if len(requests) == 1:
                return {'content': '读取证据', 'tool_calls': [
                    {'id': f'read-{i}', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}} for i in range(2)]}
            return {'content': '按新要求引用第6页'}

    result = make_loop(Client(), registry(execute)).run([{'role': 'user', 'content': '原问题'}], user_text='原问题',
                                                       max_tokens=100, temperature=0, steering_control=control)
    assert executed == ['read']
    assert len(requests) == 2 and result.content == '按新要求引用第6页'
    assert any('原页证据' in str(item.get('content')) for item in requests[1])
    assert any('使用原页核实' in str(item.get('content')) for item in requests[1])
    assert [item['tool_call_id'] for item in requests[1] if item['role'] == 'tool'] == ['read-0', 'read-1']
    assert control.sealed and len(control.responses) == 2


def test_repeated_supplement_cannot_reset_original_time_budget():
    control, calls = Control(), []
    clock = [0]

    class Client:
        def chat_completion(self, messages, **kwargs):
            calls.append(deepcopy(messages))
            clock[0] = 11
            control.add()
            raise ChatSteeringInterrupt('补充到达')

    result = make_loop(Client(), registry(), timeout_seconds=10, clock=lambda: clock[0]).run(
        [{'role': 'user', 'content': '原问题'}], user_text='原问题', max_tokens=100, temperature=0, steering_control=control)
    assert len(calls) == 1 and result.status == 'timeout' and control.sealed
    assert '补充消息已保存' in result.content


def test_repeated_supplement_cannot_bypass_context_limit():
    control, requests = Control(), []
    class Client:
        def chat_completion(self, messages, **kwargs):
            requests.append(deepcopy(messages))
            control.pending.append({'content': '补' * 4000, 'request_id': str(len(requests)),
                                    'attachment_context': [{'text': '资' * 12000}]})
            raise ChatSteeringInterrupt('补充附件')
    result = make_loop(Client(), registry()).run([{'role': 'user', 'content': '原问题'}],
        user_text='原问题', max_tokens=100, temperature=0, steering_control=control)
    assert result.status == 'max_steps' and control.sealed and '容量' in result.content
    assert len(requests) <= 9
    assert all(sum(len(str(m['content'])) for m in req) <= 108000 for req in requests)


@pytest.mark.parametrize('terminal', ['truncated', 'steps', 'timeout'])
def test_every_bounded_terminal_path_seals_and_reports_late_accepted_supplement(terminal, monkeypatch):
    control = Control()
    clock = [0]
    if terminal == 'truncated':
        monkeypatch.setattr('sjtu_learning_assistant.agent_runtime.loop.MAX_CONTINUATIONS', 0)

    class Client:
        def chat_completion(self, messages, **kwargs):
            return {'content': '片段', 'response_metadata': {'finish_reason': 'length' if terminal == 'truncated' else 'stop'},
                    'tool_calls': [] if terminal == 'truncated' else [
                        {'id': 'call-1', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]}

    # 模拟补充在“最后一次 has_pending 检查后、终态封闭前”到达。
    original_finish = control.finish_pending
    def finish():
        control.add()
        return original_finish()
    control.finish_pending = finish
    loop = make_loop(Client(), registry(lambda *args: SimpleNamespace(name='read', result={}, arguments_summary={}, result_summary={})),
                     max_steps=1, clock=lambda: clock[0])
    if terminal == 'timeout':
        original_take = control.take_pending
        control.take_pending = lambda: (clock.__setitem__(0, 1000) or original_take())
    result = loop.run([{'role': 'user', 'content': '原问题'}], user_text='原问题', max_tokens=100,
                      temperature=0, steering_control=control)
    assert control.sealed and '补充尚未完成执行' in result.content
    assert control.events[-1][1] == 'steering_deferred'

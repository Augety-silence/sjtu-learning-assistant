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


def test_interrupt_observer_only_shutdowns_and_request_owner_closes():
    from sjtu_learning_assistant.ai_classifier import _DeltaCallbackAbort, _RequestWatch
    from socket import SHUT_RDWR

    cancel, ready, read_woken = threading.Event(), threading.Event(), threading.Event()
    operations, errors, owner_ids = list(), list(), list()

    def check():
        if cancel.is_set():
            raise ChatSteeringInterrupt('synthetic cancellation')

    def shutdown(how):
        operations.append(('shutdown', threading.get_ident(), how))
        read_woken.set()

    def close(kind):
        operations.append((kind, threading.get_ident(), None))

    stream = SimpleNamespace(
        get_extra_info=lambda name: SimpleNamespace(shutdown=shutdown) if name == 'socket' else None,
        close=lambda: close('stream_close'),
    )
    response = SimpleNamespace(close=lambda: close('response_close'))

    def request_owner():
        owner_ids.append(threading.get_ident())
        try:
            with _RequestWatch(check) as watch:
                watch.trace('connection.connect_tcp.complete', dict(return_value=stream))
                watch.response(response, check=False)
                ready.set()
                assert read_woken.wait(2)
                watch.check()
        except _DeltaCallbackAbort as abort:
            errors.append(abort.original)
        finally:
            # Represents the existing request-owned httpx/httpcore cleanup.
            response.close()
            stream.close()

    worker = threading.Thread(target=request_owner)
    worker.start()
    try:
        assert ready.wait(2)
        cancel.set()
        worker.join(2)
        assert not worker.is_alive()
        assert len(errors) == 1 and isinstance(next(iter(errors)), ChatSteeringInterrupt)
        owner = next(iter(owner_ids))
        shutdowns = tuple(item for item in operations if item[0] == 'shutdown')
        closes = tuple(item for item in operations if item[0] != 'shutdown')
        assert len(shutdowns) == 1 and shutdowns[0][1] != owner
        assert shutdowns[0][2] == SHUT_RDWR
        assert len(closes) == 2 and all(item[1] == owner for item in closes)
        assert set(item[0] for item in closes) == set(('stream_close', 'response_close'))
    finally:
        cancel.set()
        read_woken.set()
        worker.join(2)


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


@pytest.mark.parametrize('mode,text', (
    ('stream', '等响应头'), ('stream', '等首 token'),
    ('post', '等响应头'), ('stream', '部分响应'),
))
def test_interrupt_when_shutdown_does_not_wake_timed_read(endpoint, monkeypatch, mode, text):
    from sjtu_learning_assistant.ai_classifier import _RequestWatch

    # Model Windows select/recv remaining asleep after the observer's shutdown.
    # Keep the real HTTP transport, existing budgets and 1.5-second assertions.
    monkeypatch.setattr(_RequestWatch, '_interrupt_stream', lambda self: None)
    if text == '部分响应':
        test_cancel_keeps_received_paid_content_and_usage(endpoint)
    else:
        test_interrupt_silent_request_leaves_shared_client_and_other_session_usable(endpoint, mode, text)


@pytest.mark.parametrize('event', ('connection.connect_tcp.complete', 'connection.start_tls.complete'))
def test_interruptible_read_retains_original_timeout_deadline(monkeypatch, event):
    import httpcore
    import sjtu_learning_assistant.ai_classifier as classifier

    clock, timeouts = SimpleNamespace(now=0.0), list()
    monkeypatch.setattr(classifier, 'time', SimpleNamespace(monotonic=lambda: clock.now))

    def read(max_bytes, timeout=None):
        assert max_bytes == 64
        timeouts.append(timeout)
        clock.now += timeout
        raise httpcore.ReadTimeout('synthetic idle read')

    stream = SimpleNamespace(read=read)
    with classifier._RequestWatch(lambda: None) as watch:
        watch.trace(event, dict(return_value=stream))
        with pytest.raises(httpcore.ReadTimeout):
            stream.read(64, timeout=0.35)
    assert len(timeouts) == 4
    assert all(0 < value <= 0.1 for value in timeouts)
    assert sum(timeouts) == pytest.approx(0.35)
    assert clock.now == pytest.approx(0.35)


@pytest.mark.parametrize('timeout', (None, 1.0))
def test_interruptible_read_resumes_same_read_without_retrying_request(monkeypatch, timeout):
    import httpcore
    import sjtu_learning_assistant.ai_classifier as classifier

    clock, timeouts = SimpleNamespace(now=0.0), list()
    monkeypatch.setattr(classifier, 'time', SimpleNamespace(monotonic=lambda: clock.now))

    def read(max_bytes, timeout=None):
        timeouts.append(timeout)
        clock.now += timeout
        if len(timeouts) < 4:
            raise httpcore.ReadTimeout('synthetic poll')
        return b'paid bytes'

    stream = SimpleNamespace(read=read)
    with classifier._RequestWatch(lambda: None) as watch:
        watch.trace('connection.start_tls.complete', dict(return_value=stream))
        assert stream.read(64, timeout=timeout) == b'paid bytes'
    assert len(timeouts) == 4 and tuple(timeouts) == (0.1, 0.1, 0.1, 0.1)


def test_requests_without_interrupt_callback_leave_backend_read_unchanged():
    from sjtu_learning_assistant.ai_classifier import _RequestWatch

    def read(max_bytes, timeout=None):
        return b'normal bytes'

    stream = SimpleNamespace(read=read)
    with _RequestWatch(None) as watch:
        watch.trace('connection.connect_tcp.complete', dict(return_value=stream))
        assert stream.read is read


@pytest.mark.parametrize('terminal', ('normal', 'cancelled'))
def test_keepalive_reuse_never_calls_previous_request_callback(endpoint, monkeypatch, terminal):
    from sjtu_learning_assistant.ai_classifier import _RequestWatch

    client, state = endpoint
    # Exercise real HTTP/1.1 pooling. Production clients still disable idle reuse
    # to capture their own sockets before waiting for response headers.
    monkeypatch.setattr(client._client._transport._pool, '_max_keepalive_connections', 10)
    callbacks, streams, errors = list(), list(), list()
    retired, cancel = threading.Event(), threading.Event()
    trace = _RequestWatch.trace

    def capture(watch, event, info):
        if event in ('connection.connect_tcp.complete', 'connection.start_tls.complete'):
            stream = info.get('return_value')
            streams.append((stream, stream.read))
        return trace(watch, event, info)

    monkeypatch.setattr(_RequestWatch, 'trace', capture)

    def check():
        callbacks.append(threading.get_ident())
        assert not retired.is_set(), 'previous request callback was retained'
        if cancel.is_set():
            raise AITaskCancelled('synthetic cancel')

    messages = list((dict(role='user', content='normal A'),))
    if terminal == 'normal':
        assert client.chat_completion(messages, request_check=check).get('content') == '独立回复'
    else:
        state.started.clear()
        def run():
            try:
                client.chat_completion_stream(list((dict(role='user', content='等首 token'),)), request_check=check)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=run)
        worker.start()
        try:
            assert state.started.wait(2)
            cancel.set()
            worker.join(1.5)
            assert not worker.is_alive()
            assert len(errors) == 1 and isinstance(next(iter(errors)), AITaskCancelled)
        finally:
            state.release.set()
            worker.join(2)
    retired.set()
    callback_count = len(callbacks)
    assert all(stream.read == original for stream, original in streams)
    for text in ('next B', 'next C'):
        assert client.chat_completion(list((dict(role='user', content=text),))).get('content') == '独立回复'
    assert len(callbacks) == callback_count
    # B/C share the same accepted socket. Cancelled A is discarded; normal A
    # also shares that socket, rather than simply opening a replacement.
    a_connection, b_connection, c_connection = state.connections
    assert b_connection == c_connection
    assert len(set(state.connections)) == (1 if terminal == 'normal' else 2)
    assert all(stream.read == original for stream, original in streams)


def test_cancel_reused_keepalive_stream_uses_only_current_callback(endpoint, monkeypatch):
    client, state = endpoint
    monkeypatch.setattr(client._client._transport._pool, '_max_keepalive_connections', 10)
    old_calls, current_calls, errors = list(), list(), list()
    retired, cancel = threading.Event(), threading.Event()

    def old_check():
        old_calls.append(threading.get_ident())
        assert not retired.is_set(), 'reused stream retained old callback'

    assert client.chat_completion(list((dict(role='user', content='warmup'),)), request_check=old_check).get('content') == '独立回复'
    retired.set()
    old_count = len(old_calls)

    def current_check():
        current_calls.append(threading.get_ident())
        if cancel.is_set():
            raise AITaskCancelled('current request only')

    def run():
        try:
            client.chat_completion_stream(list((dict(role='user', content='等首 token'),)), request_check=current_check)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert state.started.wait(2)
        assert len(state.connections) == 2 and len(set(state.connections)) == 1
        cancel.set()
        worker.join(1.5)
        assert not worker.is_alive()
        assert len(errors) == 1 and isinstance(next(iter(errors)), AITaskCancelled)
        assert len(old_calls) == old_count and current_calls
        assert client.chat_completion(list((dict(role='user', content='after cancellation'),))).get('content') == '独立回复'
        assert len(old_calls) == old_count
    finally:
        state.release.set()
        worker.join(2)


def test_pool_handoff_restores_before_owner_exit_and_cannot_shutdown_next_request(endpoint, monkeypatch):
    from sjtu_learning_assistant.ai_classifier import _RequestWatch

    client, state = endpoint
    monkeypatch.setattr(client._client._transport._pool, '_max_keepalive_connections', 10)
    cleanup_ready, cleanup_release = threading.Event(), threading.Event()
    next_done, cancel_previous, observed_cancel = threading.Event(), threading.Event(), threading.Event()
    errors, stale_reader_calls, next_results = list(), list(), list()
    next_owner = SimpleNamespace(ident=None)
    exit_watch = _RequestWatch.__exit__

    def old_check():
        if threading.get_ident() == next_owner.ident:
            stale_reader_calls.append(threading.get_ident())
        if cancel_previous.is_set():
            observed_cancel.set()
            raise ChatSteeringInterrupt('late previous-request supplement')

    def delayed_exit(watch, kind, error, traceback):
        if watch.callback is old_check:
            cleanup_ready.set()
            assert cleanup_release.wait(2)
        return exit_watch(watch, kind, error, traceback)

    monkeypatch.setattr(_RequestWatch, '__exit__', delayed_exit)

    def first():
        try:
            client.chat_completion(list((dict(role='user', content='normal A'),)), request_check=old_check)
        except Exception as exc:
            errors.append(exc)

    def second():
        next_owner.ident = threading.get_ident()
        try:
            next_results.append(client.chat_completion(list((dict(role='user', content='等响应头'),))))
        except Exception as exc:
            errors.append(exc)
        finally:
            next_done.set()

    a, b = threading.Thread(target=first), threading.Thread(target=second)
    a.start()
    try:
        assert cleanup_ready.wait(2)
        b.start()
        assert state.started.wait(2)
        assert len(state.connections) == 2 and len(set(state.connections)) == 1
        cancel_previous.set()
        assert observed_cancel.wait(1.5)
        cleanup_release.set()
        a.join(1.5)
        assert not a.is_alive()
        assert not next_done.is_set(), 'previous observer shut down the next pooled request'
        state.release.set()
        b.join(1.5)
        assert not b.is_alive()
        assert not stale_reader_calls
        assert len(next_results) == 1 and next(iter(next_results)).get('content') == '独立回复'
        assert len(errors) == 1 and isinstance(next(iter(errors)), ChatSteeringInterrupt)
    finally:
        cleanup_release.set()
        state.release.set()
        a.join(2)
        if b.ident is not None:
            b.join(2)


@pytest.mark.parametrize('same_stream', (False, True))
def test_tls_trace_does_not_double_wrap_and_owner_restores_reads(same_stream):
    from sjtu_learning_assistant.ai_classifier import _RequestWatch

    calls, checks = list(), list()
    def read(max_bytes, timeout=None):
        calls.append(timeout)
        return b'bytes'

    tcp = SimpleNamespace(read=read)
    tls = tcp if same_stream else SimpleNamespace(read=read)
    with _RequestWatch(lambda: checks.append(threading.get_ident())) as watch:
        watch.trace('connection.connect_tcp.complete', dict(return_value=tcp))
        watch.trace('connection.start_tls.complete', dict(return_value=tls))
        if not same_stream:
            assert tcp.read is read
        before = len(checks)
        assert tls.read(64, timeout=1.0) == b'bytes'
        assert len(checks) == before + 1 and tuple(calls) == (0.1,)
        watch.trace('http11.response_closed.started', dict())
        assert tcp.read is read and tls.read is read
        assert watch._stream is None
    assert tcp.read is read and tls.read is read


def test_owner_exit_restores_read_when_no_response_close_trace():
    from sjtu_learning_assistant.ai_classifier import _RequestWatch

    def read(max_bytes, timeout=None):
        return b'bytes'

    stream = SimpleNamespace(read=read)
    with _RequestWatch(lambda: None) as watch:
        watch.trace('connection.connect_tcp.complete', dict(return_value=stream))
        assert stream.read is not read
    assert stream.read is read


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

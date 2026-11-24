"""流式任务的补充消息持久化、幂等、序号、隔离和预算验证。"""
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.ai_tasks import AITaskCancelled, AITaskError
from sjtu_learning_assistant.ai_classifier import AIClassificationError
from sjtu_learning_assistant.chat_steering import ChatSteeringInterrupt
from sjtu_learning_assistant.dashboard_service import DashboardError, DashboardService
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.models import AIChatMessage, AITaskEvent


@pytest.fixture
def service(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "app.db"}')
    bootstrap_sqlite(engine)
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.update(dict(ai_key_saved=True))
    result = DashboardService(engine, archive_root=tmp_path / 'archive', settings_store=settings,
                              ai_key_loader=lambda: 'fake', ai_client_factory=lambda **kwargs: None)
    actual_start = threading.Thread.start
    with patch('sjtu_learning_assistant.dashboard_service.threading.Thread.start', autospec=True,
               side_effect=lambda thread: None if thread.name.startswith('ai-chat-') else actual_start(thread)):
        yield result
    result.close()
    engine.dispose()


def start(service, text='原始问题'):
    session = service.ai_chat_new('auto', 'standard', 'general')
    task = service.ai_chat_stream_start(session['id'], text, preset_id='general')['task']
    return session['id'], task['id'], service._chat_steering_controls[task['id']]


def test_original_input_is_saved_before_model_and_task_is_bound(service):
    session_id, task_id, control = start(service)
    history = service.ai_chat_session(session_id)['messages']
    assert [item['content'] for item in history] == ['原始问题']
    assert int(history[0]['id']) == control.initial_message_id
    binding = next(event for event in service.ai_tasks.get(task_id)['events'] if event['kind'] == 'chat_binding')
    assert binding['payload'] == dict(session_id=session_id, user_message_id=control.initial_message_id)
    with pytest.raises(DashboardError, match='已有任务'):
        service.ai_chat_stream_start(session_id, '不能另开同一会话任务')
    other_session, other_task, _ = start(service, '另一会话')
    assert other_session != session_id and other_task != task_id


def test_supplement_is_same_task_fifo_and_idempotent_even_after_completion(service):
    session_id, task_id, control = start(service)
    first = service.ai_chat_steer(session_id, task_id, '改用中文', 'request-1')
    again = service.ai_chat_steer(session_id, task_id, '改用中文', 'request-1')
    second = service.ai_chat_steer(session_id, task_id, '只保留来源明确的部分', 'request-2')
    assert first == again
    with pytest.raises(ChatSteeringInterrupt):
        control.check_request()
    pending = control.take_pending()
    assert [item['request_id'] for item in pending] == ['request-1', 'request-2']
    assert [item['content'] for item in pending] == ['改用中文', '只保留来源明确的部分']
    assert [item['content'] for item in service.ai_chat_session(session_id)['messages']] == [
        '原始问题', '改用中文', '只保留来源明确的部分']
    assert service.ai_tasks.get(task_id)['task']['usage']['model_calls'] == 0
    service.ai_tasks.start(task_id)
    service.ai_tasks.complete(task_id, {'test': True})
    control.close()
    service._chat_steering_controls.pop(task_id)
    assert service.ai_chat_steer(session_id, task_id, '改用中文', 'request-1') == first
    assert service.ai_chat_steer(session_id, task_id, '只保留来源明确的部分', 'request-2') == second
    with pytest.raises(DashboardError, match='已结束'):
        service.ai_chat_steer(session_id, task_id, '全新补充', 'new-request')
    assert len(service.ai_chat_session(session_id)['messages']) == 3


def test_wrong_session_changed_payload_and_bad_fields_never_enter_inbox(service):
    session_id, task_id, control = start(service)
    other = service.ai_chat_new('auto', 'standard', 'general')['id']
    with pytest.raises(DashboardError, match='不属于'):
        service.ai_chat_steer(other, task_id, '跨会话输入', 'foreign')
    service.ai_chat_steer(session_id, task_id, '原补充', 'same')
    with pytest.raises(DashboardError, match='不一致'):
        service.ai_chat_steer(session_id, task_id, '改掉已接收的补充', 'same')
    for content, request, attachments in (('', 'x', None), ('正常', '', None), ('甲' * 4001, 'x', None),
                                           ('正常', ' ', None), ('正常', 'x', [True]), ('正常', 'x', [-1]),
                                           ('正常', 'x', [2 ** 80])):
        with pytest.raises(DashboardError):
            service.ai_chat_steer(session_id, task_id, content, request, attachments)
    assert [item['request_id'] for item in control.take_pending()] == ['same']
    assert len(service.ai_chat_session(session_id)['messages']) == 2


def test_cancel_takes_priority_and_other_session_remains_runnable(service):
    session_id, task_id, control = start(service)
    other_session, other_task, other_control = start(service, '独立执行')
    service.ai_chat_steer(session_id, task_id, '补充', 'before-cancel')
    service.ai_task_cancel(task_id)
    with pytest.raises(AITaskCancelled):
        control.check_request()
    with pytest.raises(DashboardError, match='已结束'):
        service.ai_chat_steer(session_id, task_id, '取消后的新补充', 'after-cancel')
    assert not service.ai_tasks.get(other_task)['task']['cancel_requested']
    other_control.check_request()
    assert other_session != session_id


def test_sealing_checks_pending_atomically_and_rejects_late_new_inputs(service):
    session_id, task_id, control = start(service)
    accepted = service.ai_chat_steer(session_id, task_id, '及时补充', 'accepted')
    assert control.seal_if_idle() is False
    control.take_pending()
    assert control.seal_if_idle() is True
    assert service.ai_chat_steer(session_id, task_id, '及时补充', 'accepted') == accepted
    with pytest.raises(DashboardError, match='已结束'):
        service.ai_chat_steer(session_id, task_id, '晚到补充', 'late')
    assert len(service.ai_chat_session(session_id)['messages']) == 2


def test_text_attachment_scope_expands_and_paid_responses_are_append_only(service, tmp_path):
    session_id, task_id, control = start(service)
    source = tmp_path / '补充资料.txt'
    source.write_text('已核查来源内容。')
    attachment = service.ai_attachment_ingest(source)
    registry = SimpleNamespace(attachment_ids=frozenset())
    control.attach_registry(registry)
    ack = service.ai_chat_steer(session_id, task_id, '结合新资料', 'text-attachment', [attachment['id']])
    pending = control.take_pending()
    assert pending[0]['attachment_context']
    assert registry.attachment_ids == {attachment['id']}
    assert ack['user_message']['attachments'][0]['id'] == attachment['id']
    assert str(tmp_path) not in str(ack)
    control.record_response(dict(content='中断前已收到的付费片段'))
    control.record_response(dict(content='补充后的完整回答'))
    saved = [event['payload']['response'] for event in service.ai_tasks.get(task_id)['events'] if event['kind'] == 'paid_response']
    assert saved == [dict(content='中断前已收到的付费片段'), dict(content='补充后的完整回答')]


def test_steering_limit_does_not_reset_existing_task_budget(service):
    session_id, task_id, control = start(service)
    service.ai_tasks.start(task_id)
    for index in range(12):
        service.ai_chat_steer(session_id, task_id, f'补充 {index}', f'request-{index}')
        control.take_pending()
    with pytest.raises(DashboardError, match='次数已达上限'):
        service.ai_chat_steer(session_id, task_id, '不能重置', 'over-limit')
    before = service.ai_tasks.get(task_id)['task']['budget']
    assert before['max_model_calls'] >= 7
    service.ai_tasks.reserve_call(task_id, 100)
    assert service.ai_tasks.get(task_id)['task']['usage']['model_calls'] == 1


def test_concurrent_starts_create_only_one_task_per_session(service):
    session_id = service.ai_chat_new('auto', 'standard', 'general')['id']
    barrier = threading.Barrier(5)

    def attempt(index):
        barrier.wait()
        try:
            return service.ai_chat_stream_start(session_id, f'并发原输入 {index}')['task']['id']
        except DashboardError as exc:
            assert '已有任务' in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=5) as pool:
        tasks = list(pool.map(attempt, range(5)))
    assert len([task for task in tasks if task]) == 1
    assert len(service.ai_chat_session(session_id)['messages']) == 1


def test_concurrent_supplements_have_unique_sequences_and_one_ack_per_request(service):
    session_id, task_id, control = start(service)
    with ThreadPoolExecutor(max_workers=5) as pool:
        accepted = list(pool.map(lambda _: service.ai_chat_steer(session_id, task_id, '相同补充', 'shared-id'), range(5)))
    assert all(item == accepted[0] for item in accepted)
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda index: service.ai_chat_steer(session_id, task_id, f'并发补充 {index}', f'unique-{index}'), range(5)))
    pending = control.take_pending()
    with Session(service.engine) as session:
        rows = session.scalars(select(AIChatMessage).where(AIChatMessage.session_id == session_id)
                               .order_by(AIChatMessage.sequence)).all()
        assert [row.sequence for row in rows] == list(range(1, 8))
        assert [row.id for row in rows[1:]] == [item['message_id'] for item in pending]
    assert len(pending) == 6


@pytest.mark.parametrize('finish', ['failed', 'cancelled', 'completed'])
def test_terminal_tasks_reject_new_supplements_but_repeat_ack_remains_idempotent(service, finish):
    session_id, task_id, control = start(service)
    accepted = service.ai_chat_steer(session_id, task_id, '已收到补充', 'accepted')
    service.ai_tasks.start(task_id)
    if finish == 'failed':
        service.ai_tasks.fail(task_id, '模拟失败', category='internal', retryable=True)
    elif finish == 'cancelled':
        service.ai_task_cancel(task_id)
        service.ai_tasks.finish_cancelled(task_id)
    else:
        service.ai_tasks.complete(task_id, {})
    assert service.ai_chat_steer(session_id, task_id, '已收到补充', 'accepted') == accepted
    with pytest.raises(DashboardError, match='已结束'):
        service.ai_chat_steer(session_id, task_id, '终态后输入', 'late')
    assert len(service.ai_chat_session(session_id)['messages']) == 2


def test_cancel_notifies_control_only_after_cancel_is_durable(service):
    session_id, task_id, control = start(service)
    # 跳过下一次数据库轮询，必须仍立即感知服务主动唤醒。
    control._next_cancel_check = float('inf')
    actual_cancel = control.cancel

    def notify():
        assert service.ai_tasks.get(task_id)['task']['cancel_requested'] is True
        actual_cancel()

    with patch.object(control, 'cancel', side_effect=notify) as notification:
        service.ai_task_cancel(task_id)
    notification.assert_called_once()
    with pytest.raises(AITaskCancelled):
        control.check_request()


def test_cancel_persistence_failure_does_not_notify_or_lose_pending_input(service):
    session_id, task_id, control = start(service)
    service.ai_chat_steer(session_id, task_id, '仍需完成', 'before-failure')
    with patch.object(service.ai_tasks, 'cancel', side_effect=AITaskError('模拟保存失败')):
        with patch.object(control, 'cancel') as notification:
            with pytest.raises(DashboardError, match='保存失败'):
                service.ai_task_cancel(task_id)
            notification.assert_not_called()
    assert service.ai_tasks.get(task_id)['task']['cancel_requested'] is False
    assert [item['request_id'] for item in control.take_pending()] == ['before-failure']


def test_failure_keeps_original_and_supplements_and_closes_control(service):
    session_id, task_id, control = start(service)
    service.ai_chat_steer(session_id, task_id, '保留这条纠偏', 'before-failure')
    control.record_response({'content': '已经付费的片段'})
    with patch.object(service, 'ai_chat_send', side_effect=AIClassificationError('模拟超时', category='network')):
        service._run_ai_chat_task(task_id=task_id, session_id=session_id, content='原始问题', model='auto',
                                  depth='standard', preset_id='general', attachment_ids=None, page_context={},
                                  persisted_user_id=control.initial_message_id, steering_control=control)
    assert [item['content'] for item in service.ai_chat_session(session_id)['messages']] == ['原始问题', '保留这条纠偏']
    task = service.ai_tasks.get(task_id)
    assert task['task']['status'] == 'failed'
    assert [event['payload']['response'] for event in task['events'] if event['kind'] == 'paid_response'] == [
        {'content': '已经付费的片段'}]
    assert task_id not in service._chat_steering_controls
    with pytest.raises(AITaskError, match='已结束'):
        with control.submission_scope():
            pass


def test_model_completion_with_new_supplement_continues_same_task_without_duplicate_original(service):
    session_id, task_id, control = start(service)
    calls = []

    class Client:
        def chat_completion(self, messages, **kwargs):
            calls.append([dict(item) for item in messages])
            if len(calls) == 1:
                service.ai_chat_steer(session_id, task_id, '补充后再给答案', 'during-request')
                return {'content': '过时回答'}
            return {'content': '结合补充后的最终答案'}

        def close(self):
            pass

    service.ai_client_factory = lambda **kwargs: Client()
    service._run_ai_chat_task(task_id=task_id, session_id=session_id, content='原始问题', model='auto',
                              depth='standard', preset_id='general', attachment_ids=None, page_context={},
                              persisted_user_id=control.initial_message_id, steering_control=control)
    messages = service.ai_chat_session(session_id)['messages']
    assert [item['content'] for item in messages] == ['原始问题', '补充后再给答案', '结合补充后的最终答案']
    assert len(calls) == 2
    assert [item['content'] for item in calls[1] if item['role'] == 'user'].count('原始问题') == 1
    assert any('补充后再给答案' in str(item['content']) for item in calls[1] if item['role'] == 'user')
    task = service.ai_tasks.get(task_id)
    assert task['task']['status'] == 'completed'
    assert task['task']['usage']['model_calls'] == 2
    assert [event['payload']['response']['content'] for event in task['events'] if event['kind'] == 'paid_response'] == [
        '过时回答', '结合补充后的最终答案']
    assert len([event for event in task['events'] if event['kind'] == 'chat_binding']) == 1
    with Session(service.engine) as session:
        assert list(session.scalars(select(AIChatMessage.sequence).where(AIChatMessage.session_id == session_id)
                                    .order_by(AIChatMessage.sequence))) == [1, 2, 3]


def test_start_failure_preserves_original_and_releases_session_for_retry(service):
    session_id = service.ai_chat_new('auto', 'standard', 'general')['id']
    with patch('sjtu_learning_assistant.dashboard_service.threading.Thread.start', side_effect=RuntimeError('不能启动线程')):
        with pytest.raises(DashboardError, match='原始输入已保存'):
            service.ai_chat_stream_start(session_id, '不能丢失的原输入')
    assert [item['content'] for item in service.ai_chat_session(session_id)['messages']] == ['不能丢失的原输入']
    assert service._chat_steering_controls == {}
    retry = service.ai_chat_stream_start(session_id, '现在重试')
    assert retry['task']['status'] == 'queued'


def test_attachment_validation_and_failed_event_save_never_ack_or_queue(service, tmp_path):
    from PIL import Image
    session_id, task_id, control = start(service)
    with pytest.raises(DashboardError):
        service.ai_chat_steer(session_id, task_id, '不存在附件', 'missing', [999999])
    source = tmp_path / '图片.png'
    Image.new('RGB', (8, 8), 'white').save(source)
    image = service.ai_attachment_ingest(source)
    with pytest.raises(DashboardError, match='图片'):
        service.ai_chat_steer(session_id, task_id, '补充图片', 'image', [image['id']])
    with patch.object(service.ai_tasks, '_event', side_effect=RuntimeError('模拟数据库失败')):
        with pytest.raises(RuntimeError, match='数据库'):
            service.ai_chat_steer(session_id, task_id, '未保存补充', 'failed-write')
    assert control.take_pending() == []
    assert [item['content'] for item in service.ai_chat_session(session_id)['messages']] == ['原始问题']


def test_consumption_failure_retains_pending_for_next_attempt(service):
    session_id, task_id, control = start(service)
    service.ai_chat_steer(session_id, task_id, '已持久化输入', 'accepted')
    with patch.object(service.ai_tasks, 'append_event', side_effect=AITaskError('模拟持久化失败')):
        with pytest.raises(AITaskError):
            control.take_pending()
    assert control.has_pending()
    assert [item['request_id'] for item in control.take_pending()] == ['accepted']


def test_finish_pending_atomically_closes_inbox_and_returns_all_durable_inputs(service):
    session_id, task_id, control = start(service)
    accepted = service.ai_chat_steer(session_id, task_id, '必须说明尚未执行的补充', 'accepted')
    with ThreadPoolExecutor(max_workers=5) as pool:
        finished = list(pool.map(lambda _: control.finish_pending(), range(5)))
    assert sum(len(items) for items in finished) == 1
    pending = next(items for items in finished if items)
    assert pending[0]['request_id'] == 'accepted'
    assert not control.has_pending()
    assert service.ai_chat_steer(session_id, task_id, '必须说明尚未执行的补充', 'accepted') == accepted
    with pytest.raises(DashboardError, match='已结束'):
        service.ai_chat_steer(session_id, task_id, '收尾窗口的晚到补充', 'late')
    assert len(service.ai_chat_session(session_id)['messages']) == 2


def test_bridge_rejects_unsafe_fields_before_calling_service():
    from desktop_app import DesktopBridge
    service = Mock()
    service.ai_chat_steer.return_value = {'task_id': 'task-1', 'user_message': {'id': '1'}}
    bridge = DesktopBridge(service)
    valid = dict(session_id='session-1', task_id='task-1', content='补充', request_id='request-1', attachment_ids=[1])
    assert bridge.invoke('ai_chat_steer', valid)['ok'] is True
    service.ai_chat_steer.assert_called_once_with('session-1', 'task-1', '补充', 'request-1', [1])
    for payload in ({**valid, 'api_key': 'forbidden'}, {**valid, 'max_model_calls': 99},
                    {**valid, 'attachment_ids': [True]}, {**valid, 'attachment_ids': [2 ** 80]},
                    {**valid, 'task_id': []}, {**valid, 'request_id': ''}):
        assert bridge.invoke('ai_chat_steer', payload)['ok'] is False
    assert service.ai_chat_steer.call_count == 1

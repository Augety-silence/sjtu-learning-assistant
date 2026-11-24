"""同一会话的即时补充：先持久化、后唤醒，不重置任务或模型预算。"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import threading
import time
import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .ai_tasks import AITaskCancelled, AITaskError, AITaskLedger, _retry_write, task_spec_for_context
from .models import AITask, AITaskEvent, AIChatSession, AIChatMessage, AIChatMessageAttachment


class ChatSteeringInterrupt(RuntimeError):
    """当前模型请求已过时；任务应带着既有证据继续，而非失败或另开一轮。"""


def active_chat_task(session: Session, session_id: str) -> str | None:
    bindings = session.execute(
        select(AITaskEvent.task_id, AITaskEvent.payload)
        .join(AITask, AITask.id == AITaskEvent.task_id)
        .where(AITaskEvent.kind == 'chat_binding', AITask.status.in_(('queued', 'running')))
    )
    return next((task_id for task_id, payload in bindings if payload.get('session_id') == session_id), None)


def create_chat_task(ledger: AITaskLedger, context: dict, session_id: str, content: str,
                     model: str, attachment_ids: list[int]) -> tuple[dict, int]:
    """任务、会话绑定和原始输入原子保存，序号写门保证多窗口也不重复开任务。"""
    spec = task_spec_for_context(context, capability_page_id='ai-chat')
    task_id = str(uuid.uuid4())

    def write():
        with Session(ledger.engine) as session, session.begin():
            # SQLite 提前取得写锁；PostgreSQL 对对应会话取得行锁。冲突事务整体重读。
            matched = session.execute(update(AIChatSession).where(AIChatSession.id == session_id)
                                      .values(updated_at=AIChatSession.updated_at)).rowcount
            if not matched:
                raise AITaskError('对话不存在或已被删除。')
            if active_chat_task(session, session_id):
                raise AITaskError('这段对话已有任务在运行，请发送补充指令或新开对话。')
            task = AITask(id=task_id, page_id=context['page_id'], capability_id=spec['task_id'],
                          status='queued', model=model, context=context,
                          input_token_budget=spec['max_input_tokens'], output_token_budget=spec['max_output_tokens'],
                          max_model_calls=spec['max_model_calls'])
            session.add(task)
            ledger._event(session, task_id, 'queued', dict(page_id=task.page_id, capability_id=task.capability_id))
            sequence = int(session.scalar(select(func.max(AIChatMessage.sequence))
                                          .where(AIChatMessage.session_id == session_id)) or 0) + 1
            message = AIChatMessage(session_id=session_id, sequence=sequence, role='user',
                                    content=content, model=model, created_at=ledger.now())
            session.add(message)
            session.flush()
            session.add_all(AIChatMessageAttachment(message_id=message.id, managed_file_id=identity, position=index)
                            for index, identity in enumerate(attachment_ids))
            ledger._event(session, task_id, 'chat_binding', dict(session_id=session_id, user_message_id=message.id))
            chat = session.get(AIChatSession, session_id)
            chat.updated_at = ledger.now()
            if chat.title == '新对话':
                chat.title = content[:36] + ('…' if len(content) > 36 else '')
            return message.id

    message_id = _retry_write(write)
    return ledger.get(task_id), message_id


class ChatSteeringControl:
    def __init__(self, ledger: AITaskLedger, task_id: str, session_id: str, initial_message_id: int):
        self.ledger, self.task_id, self.session_id = ledger, task_id, session_id
        self.initial_message_id = initial_message_id
        self._lock = threading.RLock()
        self._pending: list[dict[str, Any]] = []
        self._sealed = False
        self._cancelled = threading.Event()
        self._registry = None
        self._next_cancel_check = 0.0

    @contextmanager
    def submission_scope(self):
        with self._lock:
            if self._sealed:
                raise AITaskError('当前任务已结束，补充未发送；请重新发送或新开对话。')
            yield

    def attach_registry(self, registry) -> None:
        self._registry = registry

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending)

    def check_request(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now >= self._next_cancel_check:
                self._next_cancel_check = now + 0.15
                if self.ledger.is_cancel_requested(self.task_id):
                    self._cancelled.set()
            if self._cancelled.is_set():
                raise AITaskCancelled('AI 任务已取消。')
            if self._pending:
                raise ChatSteeringInterrupt('已收到补充指令，当前请求中断后继续同一任务。')

    def queue_persisted(self, value: dict[str, Any]) -> None:
        # 只由持有 submission_scope 的服务调用，数据库事务已成功提交。
        with self._lock:
            self._pending.append(deepcopy(value))

    def take_pending(self) -> list[dict[str, Any]]:
        with self._lock:
            pending = self._pending
            if not pending:
                return []
            self.ledger.append_event(self.task_id, 'steering_consumed', dict(request_ids=[item['request_id'] for item in pending]))
            self._pending = []
            if self._registry is not None and self._registry.attachment_ids is not None:
                self._registry.attachment_ids = frozenset(self._registry.attachment_ids).union(
                    identity for item in pending for identity in item.get('attachment_ids', []))
            return pending

    def seal_if_idle(self) -> bool:
        with self._lock:
            if self._pending:
                return False
            self._sealed = True
            return True

    def finish_pending(self) -> list[dict[str, Any]]:
        """有界终止时原子关闭入口并收齐已持久化补充，交给结果说明未完成范围。"""
        with self._lock:
            self._sealed = True
            return self.take_pending()

    def record_response(self, response: dict[str, Any]) -> None:
        # 保存本任务付费响应，包括中断时已收到的片段；不覆盖旧事件。
        self.ledger.append_event(self.task_id, 'paid_response', dict(response=response))

    def cancel(self) -> None:
        self._cancelled.set()

    def close(self) -> None:
        with self._lock:
            self._sealed = True

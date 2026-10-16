"""Persistent AI task execution, budget admission and provider usage ledger."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.ai_capabilities import ai_task_catalog
from sjtu_learning_assistant.ai_classifier import extract_token_usage
from sjtu_learning_assistant.models import AITask, AITaskEvent, AIUsageRecord

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
PAGE_CONTEXT_KEYS = frozenset({"page_id", "source", "title", "entity_ids", "selection"})


class AITaskError(RuntimeError):
    """Safe task or budget failure that can be rendered to the user."""


class AITaskCancelled(AITaskError):
    pass


class AIBudgetExceeded(AITaskError):
    pass


def _json_scalar(value: object) -> str | int | float | bool | None:
    if value is None or type(value) in {str, int, float, bool}:
        return value  # type: ignore[return-value]
    raise AITaskError("页面上下文包含不支持的值。")


def normalize_page_context(value: object | None) -> dict[str, Any]:
    """Accept only bounded, renderer-safe context selectors; never raw page data."""
    if value is None:
        return {"page_id": "ai-chat", "source": "assistant"}
    if not isinstance(value, Mapping) or set(value) - PAGE_CONTEXT_KEYS:
        raise AITaskError("页面上下文格式无效。")
    page_id = value.get("page_id")
    valid_pages = {item["page_id"] for item in ai_task_catalog()["tasks"]}
    if type(page_id) is not str or page_id not in valid_pages:
        raise AITaskError("页面上下文来源无效。")
    result: dict[str, Any] = {"page_id": page_id}
    for key, limit in (("source", 80), ("title", 160)):
        field = value.get(key)
        if field is not None:
            if type(field) is not str or not field.strip() or len(field) > limit:
                raise AITaskError("页面上下文文字无效。")
            result[key] = field.strip()
    entity_ids = value.get("entity_ids", [])
    if type(entity_ids) is not list or len(entity_ids) > 50:
        raise AITaskError("页面上下文对象范围过大。")
    normalized_ids: list[str] = []
    for item in entity_ids:
        if type(item) not in {str, int}:
            raise AITaskError("页面上下文对象标识无效。")
        text = str(item).strip()
        if not text or len(text) > 128:
            raise AITaskError("页面上下文对象标识无效。")
        if text not in normalized_ids:
            normalized_ids.append(text)
    if normalized_ids:
        result["entity_ids"] = normalized_ids
    selection = value.get("selection", {})
    if not isinstance(selection, Mapping) or len(selection) > 20:
        raise AITaskError("页面选择范围无效。")
    clean_selection: dict[str, Any] = {}
    for raw_key, raw_value in selection.items():
        if type(raw_key) is not str or not raw_key or len(raw_key) > 64:
            raise AITaskError("页面选择字段无效。")
        if type(raw_value) is list:
            if len(raw_value) > 20:
                raise AITaskError("页面选择范围过大。")
            clean_selection[raw_key] = [_json_scalar(item) for item in raw_value]
        else:
            clean_selection[raw_key] = _json_scalar(raw_value)
    if clean_selection:
        result["selection"] = clean_selection
    if len(json.dumps(result, ensure_ascii=False)) > 8_000:
        raise AITaskError("页面上下文过长。")
    return result


def task_spec_for_context(context: Mapping[str, Any]) -> dict[str, Any]:
    tasks = ai_task_catalog(str(context["page_id"]))["tasks"]
    if len(tasks) != 1:
        raise AITaskError("页面 AI 能力定义无效。")
    return dict(tasks[0])


class AITaskLedger:
    def __init__(
        self,
        engine: Engine,
        *,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.engine = engine
        self.now = now_provider or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _event(session: Session, task_id: str, kind: str, payload: Mapping[str, Any]) -> AITaskEvent:
        sequence = int(
            session.scalar(
                select(func.max(AITaskEvent.sequence)).where(AITaskEvent.task_id == task_id)
            )
            or 0
        ) + 1
        row = AITaskEvent(
            task_id=task_id,
            sequence=sequence,
            kind=kind,
            payload=dict(payload),
        )
        session.add(row)
        return row

    def create(self, context: object | None, *, model: str | None = None) -> dict[str, Any]:
        normalized = normalize_page_context(context)
        spec = task_spec_for_context(normalized)
        task_id = str(uuid.uuid4())
        with Session(self.engine) as session, session.begin():
            row = AITask(
                id=task_id,
                page_id=str(spec["page_id"]),
                capability_id=str(spec["task_id"]),
                status="queued",
                model=model,
                context=normalized,
                input_token_budget=int(spec["max_input_tokens"]),
                output_token_budget=int(spec["max_output_tokens"]),
                max_model_calls=int(spec["max_model_calls"]),
            )
            session.add(row)
            self._event(session, task_id, "queued", {"page_id": row.page_id, "capability_id": row.capability_id})
        return self.get(task_id)

    def start(self, task_id: str) -> None:
        with Session(self.engine) as session, session.begin():
            row = session.get(AITask, task_id)
            if row is None:
                raise AITaskError("AI 任务不存在。")
            if row.cancel_requested:
                row.status = "cancelled"
                row.finished_at = self.now()
                self._event(session, task_id, "cancelled", {})
                raise AITaskCancelled("AI 任务已取消。")
            if row.status != "queued":
                raise AITaskError("AI 任务状态无效。")
            row.status = "running"
            row.started_at = self.now()
            self._event(session, task_id, "running", {})

    def append_event(self, task_id: str, kind: str, payload: Mapping[str, Any]) -> int:
        if type(kind) is not str or not kind or len(kind) > 32:
            raise AITaskError("AI 任务事件无效。")
        with Session(self.engine) as session, session.begin():
            if session.get(AITask, task_id) is None:
                raise AITaskError("AI 任务不存在。")
            event = self._event(session, task_id, kind, payload)
            session.flush()
            return event.sequence

    def reserve_call(self, task_id: str, requested_output_tokens: int) -> tuple[int, int]:
        requested = max(1, int(requested_output_tokens))
        with Session(self.engine) as session, session.begin():
            row = session.get(AITask, task_id)
            if row is None:
                raise AITaskError("AI 任务不存在。")
            if row.cancel_requested:
                raise AITaskCancelled("AI 任务已取消。")
            if row.status != "running":
                raise AITaskError("AI 任务尚未运行或已经结束。")
            if row.model_calls >= row.max_model_calls:
                raise AIBudgetExceeded("本任务已达到模型调用次数上限。")
            if row.prompt_tokens >= row.input_token_budget and row.model_calls > 0:
                raise AIBudgetExceeded("本任务已达到输入 Token 预算。")
            remaining = row.output_token_budget - row.charged_output_tokens
            if remaining <= 0:
                raise AIBudgetExceeded("本任务已达到输出 Token 预算。")
            allowed = min(requested, remaining)
            row.model_calls += 1
            return row.model_calls, allowed

    def record_call(
        self,
        task_id: str,
        call_index: int,
        *,
        model: str,
        requested_output_tokens: int,
        response: object | None,
    ) -> dict[str, Any]:
        usage = extract_token_usage(response)
        metadata = response.get("response_metadata") if isinstance(response, Mapping) else None
        finish_reason = metadata.get("finish_reason") if isinstance(metadata, Mapping) else None
        status = (
            "reported"
            if "prompt_tokens" in usage and "completion_tokens" in usage
            else "partial"
            if usage
            else "unknown"
        )
        charged = int(usage.get("completion_tokens", requested_output_tokens))
        with Session(self.engine) as session, session.begin():
            row = session.get(AITask, task_id)
            if row is None:
                raise AITaskError("AI 任务不存在。")
            if session.scalar(
                select(AIUsageRecord.id).where(
                    AIUsageRecord.task_id == task_id,
                    AIUsageRecord.call_index == call_index,
                )
            ) is not None:
                raise AITaskError("AI 用量记录重复。")
            prompt = usage.get("prompt_tokens")
            completion = usage.get("completion_tokens")
            total = usage.get("total_tokens")
            row.prompt_tokens += int(prompt or 0)
            row.completion_tokens += int(completion or 0)
            row.total_tokens += int(total or 0)
            row.charged_output_tokens += charged
            if status != "reported":
                row.unknown_usage_calls += 1
            session.add(
                AIUsageRecord(
                    task_id=task_id,
                    call_index=call_index,
                    model=model,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    total_tokens=total,
                    charged_output_tokens=charged,
                    usage_status=status,
                    finish_reason=finish_reason if type(finish_reason) is str else None,
                )
            )
            self._event(
                session,
                task_id,
                "usage",
                {
                    "call_index": call_index,
                    "status": status,
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "total_tokens": total,
                    "charged_output_tokens": charged,
                },
            )
        return usage

    def complete(self, task_id: str, result: Mapping[str, Any]) -> None:
        with Session(self.engine) as session, session.begin():
            row = session.get(AITask, task_id)
            if row is None:
                raise AITaskError("AI 任务不存在。")
            if row.status in TERMINAL_STATUSES:
                return
            if row.cancel_requested:
                row.status = "cancelled"
                row.finished_at = self.now()
                self._event(session, task_id, "cancelled", {})
                return
            row.status = "completed"
            row.result = dict(result)
            row.finished_at = self.now()
            self._event(session, task_id, "completed", {})

    def fail(
        self,
        task_id: str,
        message: str,
        *,
        category: str = "unknown",
        retryable: bool = True,
    ) -> None:
        safe = str(message).strip()[:500] or "AI 任务执行失败。"
        safe_category = str(category).strip()[:64] or "unknown"
        failure = {
            "message": safe,
            "category": safe_category,
            "retryable": bool(retryable),
        }
        with Session(self.engine) as session, session.begin():
            row = session.get(AITask, task_id)
            if row is None or row.status in TERMINAL_STATUSES:
                return
            if row.cancel_requested:
                row.status = "cancelled"
                row.finished_at = self.now()
                self._event(session, task_id, "cancelled", {})
                return
            row.status = "failed"
            row.error_text = safe
            row.result = {"failure": failure}
            row.finished_at = self.now()
            self._event(session, task_id, "failed", failure)

    def cancel(self, task_id: str) -> dict[str, Any]:
        with Session(self.engine) as session, session.begin():
            row = session.get(AITask, task_id)
            if row is None:
                raise AITaskError("AI 任务不存在。")
            row.cancel_requested = True
            if row.status == "queued":
                row.status = "cancelled"
                row.finished_at = self.now()
                self._event(session, task_id, "cancelled", {})
            elif row.status == "running":
                self._event(session, task_id, "cancel_requested", {})
        return self.get(task_id)

    def finish_cancelled(self, task_id: str) -> None:
        with Session(self.engine) as session, session.begin():
            row = session.get(AITask, task_id)
            if row is None or row.status in TERMINAL_STATUSES:
                return
            row.status = "cancelled"
            row.cancel_requested = True
            row.finished_at = self.now()
            self._event(session, task_id, "cancelled", {})

    def is_cancel_requested(self, task_id: str) -> bool:
        with Session(self.engine) as session:
            value = session.scalar(select(AITask.cancel_requested).where(AITask.id == task_id))
            if value is None:
                raise AITaskError("AI 任务不存在。")
            return bool(value)

    @staticmethod
    def _task_dto(row: AITask) -> dict[str, Any]:
        return {
            "id": row.id,
            "page_id": row.page_id,
            "capability_id": row.capability_id,
            "status": row.status,
            "model": row.model,
            "context": dict(row.context),
            "result": dict(row.result) if row.result else None,
            "error": row.error_text,
            "cancel_requested": row.cancel_requested,
            "budget": {
                "input_tokens": row.input_token_budget,
                "output_tokens": row.output_token_budget,
                "max_model_calls": row.max_model_calls,
            },
            "usage": {
                "model_calls": row.model_calls,
                "prompt_tokens": row.prompt_tokens,
                "completion_tokens": row.completion_tokens,
                "total_tokens": row.total_tokens,
                "charged_output_tokens": row.charged_output_tokens,
                "unknown_usage_calls": row.unknown_usage_calls,
                "status": "partial_or_unknown" if row.unknown_usage_calls else "reported",
            },
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        }

    def get(self, task_id: str, *, after_sequence: int = 0) -> dict[str, Any]:
        if type(after_sequence) is not int or after_sequence < 0:
            raise AITaskError("AI 任务事件游标无效。")
        with Session(self.engine) as session:
            row = session.get(AITask, task_id)
            if row is None:
                raise AITaskError("AI 任务不存在。")
            events = session.scalars(
                select(AITaskEvent)
                .where(
                    AITaskEvent.task_id == task_id,
                    AITaskEvent.sequence > after_sequence,
                )
                .order_by(AITaskEvent.sequence.asc())
                .limit(500)
            ).all()
            return {
                "task": self._task_dto(row),
                "events": [
                    {
                        "sequence": event.sequence,
                        "kind": event.kind,
                        "payload": dict(event.payload),
                        "created_at": event.created_at.isoformat() if event.created_at else None,
                    }
                    for event in events
                ],
                "next_sequence": events[-1].sequence if events else after_sequence,
            }

    def usage_summary(self, *, page_id: str | None = None) -> dict[str, Any]:
        with Session(self.engine) as session:
            statement = select(AITask)
            if page_id is not None:
                statement = statement.where(AITask.page_id == page_id)
            rows = session.scalars(statement.order_by(AITask.created_at.desc()).limit(500)).all()
        return {
            "page_id": page_id,
            "tasks": len(rows),
            "model_calls": sum(row.model_calls for row in rows),
            "prompt_tokens": sum(row.prompt_tokens for row in rows),
            "completion_tokens": sum(row.completion_tokens for row in rows),
            "total_tokens": sum(row.total_tokens for row in rows),
            "unknown_usage_calls": sum(row.unknown_usage_calls for row in rows),
        }


class BudgetedAIClient:
    """Wrap an OpenAI-compatible client with persistent call admission and usage."""

    def __init__(self, client: Any, ledger: AITaskLedger, task_id: str) -> None:
        self._client = client
        self._ledger = ledger
        self._task_id = task_id
        self.model = str(getattr(client, "model", "unknown"))
        self.supports_streaming = callable(getattr(client, "chat_completion_stream", None))

    def _run(self, method: str, messages: list[dict[str, Any]], kwargs: dict[str, Any]) -> dict[str, Any]:
        requested = int(kwargs.get("max_tokens", 1200))
        call_index, allowed = self._ledger.reserve_call(self._task_id, requested)
        kwargs["max_tokens"] = allowed
        response: dict[str, Any] | None = None
        try:
            response = getattr(self._client, method)(messages, **kwargs)
            return response
        finally:
            self._ledger.record_call(
                self._task_id,
                call_index,
                model=self.model,
                requested_output_tokens=allowed,
                response=response,
            )

    def chat_completion(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        return self._run("chat_completion", messages, dict(kwargs))

    def chat_completion_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        return self._run("chat_completion_stream", messages, dict(kwargs))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

"""本机模型请求用量：仅保存计数，不保存提示词、响应、地址或凭据。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from .models import ProviderUsage


class ProviderUsageLedger:
    def __init__(self, engine: Engine):
        self.engine = engine

    def record(self, model: str, usage: dict[str, int]) -> None:
        cache = usage.get("cached_tokens", usage.get("prompt_cache_hit_tokens", usage.get("cache_read_input_tokens")))
        prompt = usage.get("prompt_tokens")
        if cache is not None and prompt is not None and cache > prompt:
            cache = None  # 矛盾的缓存口径不伪装成可相加的已知数。
        with Session(self.engine) as session, session.begin():
            session.add(ProviderUsage(
                id=uuid4().hex, model=model,
                prompt_tokens=prompt, completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"), cached_tokens=cache,
                created_at=datetime.now(timezone.utc),
            ))

    def summary(self, *, now: datetime | None = None) -> dict:
        now = now or datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        result = {}
        with Session(self.engine) as session:
            for key, since in (("today", start), ("week", start - timedelta(days=6)), ("all", None)):
                columns = [ProviderUsage.prompt_tokens, ProviderUsage.completion_tokens,
                           ProviderUsage.cached_tokens, ProviderUsage.total_tokens]
                statement = select(func.count(ProviderUsage.id),
                    *[func.sum(column) for column in columns],
                    *[func.count(column) for column in columns])
                if since is not None:
                    statement = statement.where(ProviderUsage.created_at >= since.astimezone(timezone.utc))
                row = session.execute(statement).one()
                calls = row[0]
                names = ("input", "output", "cache", "total")
                result[key] = {"calls": calls, "metrics": {
                    name: {"tokens": row[index+1], "reported_calls": row[index+5],
                           "missing_calls": calls - row[index+5]}
                    for index, name in enumerate(names)}}
            first = session.scalar(select(func.min(ProviderUsage.created_at)))
        return {"periods": result, "first_recorded_at": first.isoformat() if first else None,
                "timezone": str(now.tzinfo), "scope": "local_app_since_upgrade"}

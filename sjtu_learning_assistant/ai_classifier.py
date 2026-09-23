"""OpenAI-compatible metadata-only course material classification client."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping

import httpx

from sjtu_learning_assistant.material_classifier import CATEGORY_ORDER

DEFAULT_AI_BASE_URL = "https://models.sjtu.edu.cn/api/v1"
DEFAULT_AI_MODEL = "deepseek-chat"
ALLOWED_AI_MODELS = frozenset(
    {
        "deepseek-chat",
        "deepseek-reasoner",
        "minimax",
        "minimax-m2.7",
        "qwen",
        "qwen3.8-27b",
    }
)
SYSTEM_PROMPT = (
    "You classify university course file metadata. Return JSON only with exactly "
    '{"classifications":[{"id":"input id","category":"assignments|courseware|other"}]}. '
    "Use other for supplementary/reference material. Return one item for every input id. "
    "Never infer or request file contents."
)


class AIClassificationError(RuntimeError):
    """A bounded, credential-safe AI classification failure."""


@dataclass(frozen=True)
class ClassificationInput:
    source_id: str
    course_name: str
    filename: str
    folder_names: tuple[str, ...] = ()
    module_names: tuple[str, ...] = ()
    module_item_names: tuple[str, ...] = ()
    source_updated_at: datetime | None = None

    def metadata(self) -> dict[str, object]:
        return {
            "id": self.source_id,
            "course_name": self.course_name,
            "filename": self.filename,
            "canvas_folders": list(self.folder_names),
            "module_names": list(self.module_names),
            "module_items": list(self.module_item_names),
        }


def classification_fingerprint(item: ClassificationInput) -> str:
    payload = {
        "source_id": item.source_id,
        "source_updated_at": item.source_updated_at.isoformat()
        if item.source_updated_at
        else None,
        **item.metadata(),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SixSecondRateLimiter:
    """Process-wide conservative limiter: at most one request per six seconds."""

    def __init__(
        self,
        interval_seconds: float = 6.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.clock = clock
        self.sleeper = sleeper
        self._lock = threading.Lock()
        self._last_request_at: float | None = None

    def wait(self) -> None:
        with self._lock:
            now = self.clock()
            if self._last_request_at is not None:
                delay = self.interval_seconds - (now - self._last_request_at)
                if delay > 0:
                    self.sleeper(delay)
            self._last_request_at = self.clock()


_GLOBAL_LIMITER = SixSecondRateLimiter()


class OpenAIClassificationClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_AI_BASE_URL,
        model: str = DEFAULT_AI_MODEL,
        transport: httpx.BaseTransport | None = None,
        limiter: Callable[[], None] | None = None,
        timeout: httpx.Timeout | float = httpx.Timeout(20.0, connect=5.0),
    ) -> None:
        if not api_key:
            raise AIClassificationError("AI API key 为空。")
        if model not in ALLOWED_AI_MODELS:
            raise AIClassificationError("AI 模型不受支持。")
        self.model = model
        self._limiter = limiter or _GLOBAL_LIMITER.wait
        self._client = httpx.Client(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def classify_many(
        self, items: Iterable[ClassificationInput]
    ) -> dict[str, str]:
        values = list(items)
        if not values:
            return {}
        expected_ids = [item.source_id for item in values]
        if len(set(expected_ids)) != len(expected_ids):
            raise AIClassificationError("分类输入标识重复。")
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"files": [item.metadata() for item in values]},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
        }
        try:
            self._limiter()
            response = self._client.post("chat/completions", json=body)
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except Exception:
            raise AIClassificationError("AI 分类请求失败。") from None
        return self._parse_result(parsed, expected_ids)

    @staticmethod
    def _parse_result(payload: object, expected_ids: list[str]) -> dict[str, str]:
        if type(payload) is not dict or set(payload) != {"classifications"}:
            raise AIClassificationError("AI 分类结果格式无效。")
        rows = payload.get("classifications")
        if type(rows) is not list:
            raise AIClassificationError("AI 分类结果格式无效。")
        result: dict[str, str] = {}
        for row in rows:
            if type(row) is not dict or set(row) != {"id", "category"}:
                raise AIClassificationError("AI 分类结果格式无效。")
            source_id = row.get("id")
            category = row.get("category")
            if (
                type(source_id) is not str
                or source_id not in expected_ids
                or source_id in result
                or type(category) is not str
                or category not in CATEGORY_ORDER
            ):
                raise AIClassificationError("AI 分类结果格式无效。")
            result[source_id] = category
        if set(result) != set(expected_ids):
            raise AIClassificationError("AI 分类结果不完整。")
        return result

    def test_connection(self) -> str:
        sample = ClassificationInput(
            source_id="connection-test",
            course_name="测试课程",
            filename="lecture-01.pdf",
            folder_names=("课程资料",),
            module_names=("第一周",),
            module_item_names=("第一讲",),
        )
        return self.classify_many((sample,))[sample.source_id]

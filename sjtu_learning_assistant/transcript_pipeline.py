from __future__ import annotations

import hashlib
import html
import inspect
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

PROMPT_VERSION = "transcript-v10-evidence-contract"
AUDIT_WIRE_VERSION = "audit-wire-v4-local-topic-alias-source-cue-refs"
PIPELINE_VERSION = "transcript-pipeline-v10-playback-priority"
TARGET_CHUNK_CHARS = 5200
MAX_CHUNK_CHARS = 6000
MAX_CHUNK_CUES = 120
MAX_MESSAGE_CHARS = 14000
MAX_REPAIR_FRAGMENT_CHARS = 6000
MAX_VALIDATED_JSON_CHARS = 10000
MAP_FALLBACK_WARNING = "AI 分块结果无效，已使用确定性字幕规整回退。"
REDUCE_FALLBACK_WARNING = "AI最终汇总未完成，已根据已规整分块生成结果"
SUMMARY_EMPTY_WARNING = "未提取出可验证的本节要点。"
LEARNING_PRODUCTS_INCOMPLETE_WARNING = "未提取出可验证知识点，已保留兼容摘要与基础字幕。"
AUDIT_FALLBACK_WARNING = "讲义审校未完成，已保留阶段1讲义并标记全部审校分块。"
SOURCE_TYPES = frozenset(("课堂明确讲述", "根据课堂内容归纳", "自编练习或例子", "字幕存疑"))
PLACEHOLDER_TERMS = frozenset(("要点", "兼容概念说明", "兼容案例、公式或结论"))
_TIMESTAMP = re.compile(r"^(?:(?P<h>\d{2,}):)?(?P<m>\d{2}):(?P<s>\d{2})[.,](?P<ms>\d{3})$")
_TIMING = re.compile(r"^(?P<start>\S+)\s+-->\s+(?P<end>\S+)(?:\s+.*)?$")
_TAG = re.compile(r"<[^>]+>")
_SENTENCE = re.compile(r"(?<=[。！？；.!?;])")

MAP_KEYS = frozenset((
    "cleaned_transcript",
    "topics",
    "emphasized_points",
    "concepts",
    "cases_formulas_conclusions",
    "review_questions",
    "knowledge_points",
    "classroom_examples",
    "practice_items",
))
SUMMARY_KEYS = frozenset((
    "lesson_topic",
    "learning_objectives",
    "emphasized_points",
    "concepts",
    "cases_formulas_conclusions",
    "review_questions",
    "timeline",
    "knowledge_points",
    "classroom_examples",
    "practice_items",
))
MAP_SCHEMA_HINT = (
    "必须包含且仅包含字段 cleaned_transcript、topics、emphasized_points、concepts、"
    "cases_formulas_conclusions、review_questions、knowledge_points、classroom_examples、"
    "practice_items。cleaned_transcript 项仅含 cue_id、start_ms、end_ms、text；"
    "知识点必须标明 kind（definition/principle/method/fact/formula/conclusion）、concept、"
    "statement 与逐字 evidence；课堂例子必须关联 related_knowledge_points；练习仅允许"
    " recall/application，并给 answer_key 或 rubric。evidence 项仅含 cue_id、start_ms、"
    "end_ms、quote。宁缺毋滥，不得把字幕纠错样本当作练习。"
)
SUMMARY_SCHEMA_HINT = (
    "必须包含且仅包含字段 lesson_topic、learning_objectives、emphasized_points、concepts、"
    "cases_formulas_conclusions、review_questions、timeline、knowledge_points、"
    "classroom_examples、practice_items。所有 evidence 和 timeline 项必须引用 cue_id；"
    "例子必须关联知识点，练习必须包含答案要点或评估标准。"
)


class TranscriptAIFormatError(ValueError):
    """A bounded error which never embeds model or transcript text."""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.diagnostics = dict(diagnostics or {})


class TranscriptPipelineCancelled(BaseException):
    """Cooperative cancellation, intentionally not caught as an AI failure."""


def _check_cancel(should_cancel: Callable[[], bool] | None) -> None:
    if should_cancel is not None and should_cancel():
        raise TranscriptPipelineCancelled("字幕整理已取消。")


class _PipelineCallTracker:
    """Track safe per-run call indexes while transparently forwarding a client."""

    def __init__(self, client: Any, *, json_mode: bool) -> None:
        self.client = client
        self.json_mode = json_mode
        self.call_count = 0
        self.last_call_index: int | None = None
        self.last_response: Any = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)

    def chat_completion(self, *args: Any, **kwargs: Any) -> Any:
        self.call_count += 1
        self.last_call_index = self.call_count
        self.last_response = self.client.chat_completion(*args, **kwargs)
        return self.last_response


def _supports_response_format(client: Any) -> bool:
    """Check wrapped client signatures so older strict FakeAI methods still work."""
    current = client
    seen: set[int] = set()
    for _ in range(6):
        if current is None or id(current) in seen:
            return False
        seen.add(id(current))
        method = getattr(current, "chat_completion", None)
        try:
            parameters = inspect.signature(method).parameters if callable(method) else {}
        except (TypeError, ValueError):
            parameters = {}
        if "response_format" in parameters:
            return True
        inner = getattr(current, "client", None)
        if inner is not None and inner is not current:
            current = inner
            continue
        return any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values())
    return False


def _response_format_option(client: Any, json_mode: bool) -> dict[str, Any]:
    if json_mode and _supports_response_format(client):
        return {"response_format": {"type": "json_object"}}
    return {}


def _safe_response_details(client: Any) -> dict[str, Any]:
    response = getattr(client, "last_response", None)
    content = response.get("content") if isinstance(response, Mapping) else None
    finish_reason = _response_metadata(response).get("finish_reason")
    details: dict[str, Any] = {}
    call_index = getattr(client, "last_call_index", None)
    if type(call_index) is int and call_index > 0:
        details["call_index"] = call_index
    if type(content) is str:
        details["response_chars"] = len(content)
    if finish_reason in {"stop", "length", "tool_calls", "content_filter"}:
        details["finish_reason"] = finish_reason
    return details


def _schema_diagnostic_fields(exc: Exception, client: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    details = _reduce_failure_diagnostics(exc)
    mismatch = details.get("schema_mismatch")
    if isinstance(mismatch, Mapping):
        for source, target in (("path", "validator_path"), ("expected", "validator_expected"),
                               ("actual_type", "validator_actual_type"), ("length", "validator_actual_length")):
            value = mismatch.get(source)
            if value is not None and type(value) in (str, int):
                result[target] = value
    for source, target in (("category", "category"), ("finish_reason", "finish_reason"),
                           ("parse_error_type", "parse_error_type"), ("content_length", "output_chars")):
        value = details.get(source)
        if value is not None and type(value) in (str, int):
            result[target] = value
    result.update(_safe_response_details(client))
    return result


class SchemaValidationError(ValueError):
    """A schema mismatch that records structure, never model text."""

    def __init__(self, path: str, expected: str, actual: object) -> None:
        super().__init__(f"{path} 类型或结构无效。")
        detail = dict(path=path[:160], expected=expected[:120], actual_type=type(actual).__name__)
        if isinstance(actual, (str, list, tuple, dict)):
            detail["length"] = len(actual)
        self.detail = detail


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str
    cue_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "cue_id": self.cue_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "text": self.text,
        }


@dataclass(frozen=True)
class TranscriptChunk:
    index: int
    cues: tuple[Cue, ...]

    def as_prompt_text(self) -> str:
        return "\n".join(
            f"[{cue.cue_id} {cue.start_ms}-{cue.end_ms}] {cue.text}"
            for cue in self.cues
        )


@dataclass(frozen=True)
class PipelineResult:
    cues: list[dict[str, Any]]
    chunks: list[dict[str, Any]]
    cleaned_markdown: str
    summary: dict[str, Any]
    summary_markdown: str
    partial_warnings: tuple[str, ...] = ()
    prompt_version: str = PROMPT_VERSION
    reduce_diagnostics: Mapping[str, Any] | None = None
    summary_empty: bool = False


def _timestamp_ms(value: str) -> int:
    match = _TIMESTAMP.fullmatch(value)
    if match is None:
        raise ValueError("WebVTT 时间戳格式无效。")
    hours = int(match.group("h") or 0)
    minutes = int(match.group("m"))
    seconds = int(match.group("s"))
    millis = int(match.group("ms"))
    if minutes >= 60 or seconds >= 60:
        raise ValueError("WebVTT 时间戳范围无效。")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def parse_vtt(value: str) -> list[Cue]:
    if type(value) is not str or len(value.encode("utf-8")) > 32 * 1024 * 1024:
        raise ValueError("字幕内容为空或超过安全限制。")
    text = value.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    lines = text.split("\n")
    if not lines or not lines[0].strip().startswith("WEBVTT"):
        raise ValueError("字幕不是标准 WebVTT。")
    cues: list[Cue] = []
    index = 1
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line:
            continue
        if line.startswith(("NOTE", "STYLE", "REGION")):
            while index < len(lines) and lines[index].strip():
                index += 1
            continue
        timing = _TIMING.fullmatch(line)
        if timing is None and index < len(lines):
            timing = _TIMING.fullmatch(lines[index].strip())
            if timing is not None:
                index += 1
        if timing is None:
            raise ValueError("WebVTT cue 缺少有效时间范围。")
        start = _timestamp_ms(timing.group("start"))
        end = _timestamp_ms(timing.group("end"))
        if end <= start:
            raise ValueError("WebVTT cue 时间范围无效。")
        body: list[str] = []
        while index < len(lines) and lines[index].strip():
            body.append(lines[index].strip())
            index += 1
        content = unicodedata.normalize(
            "NFC", html.unescape(_TAG.sub("", " ".join(body)))
        )
        content = " ".join(content.split())
        if content:
            cues.append(Cue(start, end, content, f"cue-{len(cues) + 1:06d}"))
    if not cues:
        raise ValueError("字幕中没有可用 cue。")
    return sorted(cues, key=lambda cue: (cue.start_ms, cue.end_ms))


def normalize_cues(cues: Sequence[Cue]) -> list[Cue]:
    result: list[Cue] = []
    for cue in cues:
        text = unicodedata.normalize("NFC", " ".join(cue.text.split()))
        if not text:
            continue
        cue_id = cue.cue_id or f"cue-{len(result) + 1:06d}"
        current = Cue(cue.start_ms, cue.end_ms, text, cue_id)
        if result:
            previous = result[-1]
            overlap = current.start_ms <= previous.end_ms + 250
            if overlap and current.text == previous.text:
                result[-1] = Cue(
                    previous.start_ms,
                    max(previous.end_ms, current.end_ms),
                    previous.text,
                    previous.cue_id,
                )
                continue
            if overlap and current.text.startswith(previous.text) and len(previous.text) >= 4:
                result[-1] = Cue(
                    previous.start_ms,
                    max(previous.end_ms, current.end_ms),
                    current.text,
                    previous.cue_id,
                )
                continue
            if overlap and previous.text.startswith(current.text) and len(current.text) >= 4:
                result[-1] = Cue(
                    previous.start_ms,
                    max(previous.end_ms, current.end_ms),
                    previous.text,
                    previous.cue_id,
                )
                continue
        result.append(current)
    return result


def _split_long_cue(cue: Cue, limit: int) -> list[Cue]:
    if len(cue.text) <= limit:
        return [cue]
    sentences = [piece for piece in _SENTENCE.split(cue.text) if piece]
    parts: list[str] = []
    buffer = ""
    for sentence in sentences:
        while len(sentence) > limit:
            if buffer:
                parts.append(buffer)
                buffer = ""
            parts.append(sentence[:limit])
            sentence = sentence[limit:]
        if buffer and len(buffer) + len(sentence) > limit:
            parts.append(buffer)
            buffer = sentence
        else:
            buffer += sentence
    if buffer:
        parts.append(buffer)
    duration = max(cue.end_ms - cue.start_ms, len(parts))
    return [
        Cue(
            cue.start_ms + duration * index // len(parts),
            cue.start_ms + duration * (index + 1) // len(parts),
            part,
            f"{cue.cue_id}-{index + 1:02d}",
        )
        for index, part in enumerate(parts)
    ]


def chunk_cues(
    cues: Sequence[Cue],
    target_chars: int = TARGET_CHUNK_CHARS,
    max_chars: int = MAX_CHUNK_CHARS,
    overlap_cues: int = 2,
    max_cues: int = MAX_CHUNK_CUES,
) -> list[TranscriptChunk]:
    if target_chars <= 0 or max_chars <= target_chars or max_chars >= MAX_MESSAGE_CHARS or max_cues <= 0:
        raise ValueError("字幕分块参数无效。")
    expanded: list[Cue] = []
    for cue in cues:
        expanded.extend(_split_long_cue(cue, max_chars - 120))
    chunks: list[TranscriptChunk] = []
    current: list[Cue] = []
    current_size = 0
    for cue in expanded:
        line_size = len(f"[{cue.cue_id} {cue.start_ms}-{cue.end_ms}] {cue.text}\n")
        if current and (current_size + line_size > target_chars or len(current) >= max_cues):
            chunks.append(TranscriptChunk(len(chunks), tuple(current)))
            current = current[-overlap_cues:] if overlap_cues else []
            current_size = sum(
                len(f"[{item.cue_id} {item.start_ms}-{item.end_ms}] {item.text}\n")
                for item in current
            )
            while current and (current_size + line_size >= max_chars or len(current) >= max_cues):
                current.pop(0)
                current_size = sum(
                    len(f"[{item.cue_id} {item.start_ms}-{item.end_ms}] {item.text}\n")
                    for item in current
                )
        current.append(cue)
        current_size += line_size
    if current:
        chunks.append(TranscriptChunk(len(chunks), tuple(current)))
    return chunks


def _response_metadata(response: object) -> dict[str, Any]:
    if not isinstance(response, Mapping):
        return {}
    value = response.get("response_metadata")
    metadata = dict(value) if isinstance(value, Mapping) else {}
    if "finish_reason" not in metadata and type(response.get("finish_reason")) is str:
        metadata["finish_reason"] = response["finish_reason"]
    return metadata


def _balanced_object_fragments(value: str) -> tuple[list[str], bool]:
    text = value.lstrip("\ufeff")
    fragments: list[str] = []
    unbalanced = False
    start = 0
    while start < len(text):
        start = text.find("{", start)
        if start < 0:
            break
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    fragments.append(text[start : index + 1])
                    start = index + 1
                    break
        else:
            unbalanced = True
            break
    return fragments, unbalanced


def _diagnostics(content: object, response: object, error_type: str) -> dict[str, Any]:
    text = content if type(content) is str else ""
    stripped = text.strip().lstrip("\ufeff")
    finish_reason = _response_metadata(response).get("finish_reason")
    _fragments, unbalanced = _balanced_object_fragments(text)
    return {
        "content_length": len(text),
        "first_char": stripped[:1],
        "last_char": stripped[-1:],
        "finish_reason": finish_reason if type(finish_reason) is str else None,
        "has_code_fence": "```" in text,
        "suspected_truncation": finish_reason == "length" or unbalanced,
        "parse_error_type": error_type,
    }


def _validated_json(
    content: object,
    validator: Callable[[object], dict[str, Any]],
    response: object,
) -> dict[str, Any]:
    if type(content) is not str or not content.strip():
        raise TranscriptAIFormatError(
            "AI 未返回可处理回复，请重试。",
            category="empty",
            diagnostics=_diagnostics(content, response, "TypeError"),
        )
    fragments, unbalanced = _balanced_object_fragments(content)
    had_json_object = False
    last_error_type = "JSONDecodeError"
    schema_mismatch: dict[str, Any] | None = None
    for fragment in fragments:
        try:
            parsed = json.loads(fragment)
        except (json.JSONDecodeError, TypeError):
            continue
        if type(parsed) is not dict:
            continue
        had_json_object = True
        try:
            validated = validator(parsed)
            encoded_length = len(
                json.dumps(
                    {key: item for key, item in validated.items() if key != "cleaned_transcript"},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            if encoded_length > MAX_VALIDATED_JSON_CHARS:
                raise ValueError("validated JSON too long")
            return validated
        except (TypeError, ValueError) as exc:
            last_error_type = type(exc).__name__
            if isinstance(exc, SchemaValidationError):
                schema_mismatch = dict(exc.detail)
    finish_reason = _response_metadata(response).get("finish_reason")
    if finish_reason == "length" or unbalanced:
        raise TranscriptAIFormatError(
            "AI 输出被长度限制截断，已保留该字幕块；请重试或更换模型。",
            category="truncated",
            diagnostics=_diagnostics(content, response, "JSONDecodeError"),
        )
    category = "schema" if had_json_object else "format"
    message = (
        "AI 返回字段不符合要求，请重试或更换模型。"
        if category == "schema"
        else "AI 返回不是可提取的完整 JSON 对象，请重试或更换模型。"
    )
    diagnostics = _diagnostics(content, response, last_error_type)
    if schema_mismatch is not None:
        diagnostics["schema_mismatch"] = schema_mismatch
    raise TranscriptAIFormatError(
        message,
        category=category,
        diagnostics=diagnostics,
    )


def _repair_fragment(content: object) -> str:
    if type(content) is not str:
        return ""
    fragments, _unbalanced = _balanced_object_fragments(content)
    for fragment in fragments:
        if len(fragment) <= MAX_REPAIR_FRAGMENT_CHARS:
            return fragment
    return ""


CLASSROOM_SOURCE = "课堂明确讲述"
CLASSROOM_QUESTION_MISMATCH = "classroom question must occur in source cues"
_QUESTION_SOURCE_PATH = re.compile(r"questions\[(\d{1,3})\]\.source")


def _guide_root(value: object) -> object:
    """Unwrap an optional {"study_guide": ...} envelope exactly as the guide validator does."""
    return value.get("study_guide") if type(value) is dict and "study_guide" in value else value


def _question_prompt_in_cues(prompt: str, cues: Sequence[Cue]) -> bool:
    # A classroom-source claim requires a complete prompt phrase inside one
    # cue. Preserve punctuation and require a left boundary so a short suffix
    # such as "饭？" cannot be mistaken for the full question in a cue.
    key = _canonical_learning_phrase(prompt)
    if not key:
        return False
    for cue in cues:
        text = _canonical_learning_phrase(cue.text)
        start = 0
        while (index := text.find(key, start)) >= 0:
            before = text[index - 1] if index else ""
            after_index = index + len(key)
            after = text[after_index] if after_index < len(text) else ""
            left_boundary = (
                not before
                or not before.isalnum()
                or before == "问"
            )
            ends_with_question_mark = key.endswith(("?", "!", "？", "！"))
            right_boundary = (
                ends_with_question_mark
                or not after
                or not after.isalnum()
            )
            if left_boundary and right_boundary:
                return True
            start = index + 1
    return False


def _canonical_learning_phrase(value: object) -> str:
    """Normalize spacing/Unicode while retaining punctuation as phrase boundaries."""
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _provable_question_source_errors(guide: object, cues: Sequence[Cue]) -> frozenset[int] | None:
    """Indices whose classroom source claim is provably false under the validator's cue matching."""
    if not cues or type(guide) is not dict or type(guide.get("questions")) is not list:
        return None
    allowed: set[int] = set()
    for index, raw in enumerate(guide["questions"]):
        if type(raw) is not dict:
            continue
        prompt = _clean_learning_text(raw.get("prompt"))
        if (
            _clean_learning_text(raw.get("source")) == CLASSROOM_SOURCE
            and prompt
            and not _question_prompt_in_cues(prompt, cues)
        ):
            allowed.add(index)
    return frozenset(allowed)


def _has_unprovable_classroom_source_claim(guide: object, cues: Sequence[Cue] | None) -> bool:
    """Detect classroom-source claims that a repair must not silently rewrite or legitimize."""
    if type(guide) is not dict or type(guide.get("questions")) is not list:
        return False
    for raw in guide["questions"]:
        if type(raw) is not dict:
            continue
        prompt = _clean_learning_text(raw.get("prompt"))
        if _clean_learning_text(raw.get("source")) != CLASSROOM_SOURCE:
            continue
        if not prompt or not cues or not _question_prompt_in_cues(prompt, cues):
            return True
    return False


def _guide_question_baseline_is_safe(guide: object) -> bool:
    """Require enough typed question identity to preserve it across any repair."""
    if type(guide) is not dict or type(guide.get("questions")) is not list:
        return False
    if not 5 <= len(guide["questions"]) <= 8:
        return False
    return all(
        type(question) is dict
        and set(question) == {"type", "prompt", "answer", "source"}
        and type(question.get("type")) is str
        and question["type"] in {"recall", "application"}
        and type(question.get("prompt")) is str
        and bool(_clean_learning_text(question["prompt"]))
        and type(question.get("answer")) is str
        and bool(_clean_learning_text(question["answer"]))
        and type(question.get("source")) is str
        and question["source"] in SOURCE_TYPES
        for question in guide["questions"]
    )


def _json_equal(before: object, after: object) -> bool:
    """Type-strict deep equality for parsed JSON (1, 1.0 and True differ; key sets must match)."""
    if type(before) is not type(after):
        return False
    if type(before) is dict:
        return set(before) == set(after) and all(_json_equal(before[key], after[key]) for key in before)
    if type(before) is list:
        return len(before) == len(after) and all(_json_equal(left, right) for left, right in zip(before, after))
    return before == after


def _check_source_only_guide_repair(
    first_guide: Mapping[str, Any],
    repaired_value: object,
    allowed: frozenset[int],
) -> None:
    """Reject any repair that edits more than questions[i].source for provably wrong classroom claims.

    Failure paths use only schema-known names and indices, never model text or model-chosen keys.
    """
    repaired = _guide_root(repaired_value)
    expected = "source-only repair must preserve every non-source field"
    if type(repaired) is not dict or set(repaired) != set(first_guide):
        _schema_fail("study_guide", expected, repaired)
    for key in sorted(first_guide):
        if key == "questions":
            continue
        before, after = first_guide[key], repaired[key]
        if _json_equal(before, after):
            continue
        if type(before) is list and type(after) is list and len(before) == len(after):
            index = next(i for i, pair in enumerate(zip(before, after)) if not _json_equal(*pair))
            _schema_fail(f"{key}[{index}]", expected, after[index])
        _schema_fail(key, expected, after)
    before_questions = first_guide["questions"]
    after_questions = repaired["questions"]
    if type(after_questions) is not list or len(after_questions) != len(before_questions):
        _schema_fail("questions", "source-only repair must preserve question count and order", after_questions)
    for index, (before, after) in enumerate(zip(before_questions, after_questions)):
        path = f"questions[{index}]"
        if type(before) is not dict or type(after) is not dict or set(before) != set(after):
            _schema_fail(path, expected, after)
        for key in before:
            if key == "source" and index in allowed:
                source = after.get("source")
                if type(source) is not str or source not in SOURCE_TYPES or source == CLASSROOM_SOURCE:
                    _schema_fail(path + ".source", "non-classroom source enum for unsupported classroom claim", source)
                continue
            if not _json_equal(before[key], after[key]):
                known = key in {"type", "prompt", "answer", "source"}
                _schema_fail(f"{path}.{key}" if known else path, expected, after[key])


def _source_only_repair_guard(
    fragment: str,
    safe_mismatch: Mapping[str, Any],
    validator: Callable[[object], dict[str, Any]],
    cues: Sequence[Cue] | None,
) -> Callable[[object], None] | None:
    """Build a preservation check from the exact fragment sent for repair, or None if unprovable."""
    match = _QUESTION_SOURCE_PATH.fullmatch(str(safe_mismatch.get("path", "")))
    if match is None or not cues:
        return None
    try:
        first_value = json.loads(fragment)
    except (json.JSONDecodeError, TypeError):
        return None
    if type(first_value) is not dict:
        return None
    try:
        validator(first_value)
    except SchemaValidationError as exc:
        if exc.detail.get("path") != safe_mismatch.get("path") or exc.detail.get("expected") != CLASSROOM_QUESTION_MISMATCH:
            return None
    except (TypeError, ValueError):
        return None
    else:
        return None
    first_guide = _guide_root(first_value)
    if not _guide_question_baseline_is_safe(first_guide):
        return None
    allowed = _provable_question_source_errors(first_guide, cues)
    if not allowed or int(match.group(1)) not in allowed:
        return None
    source_repaired_value = json.loads(json.dumps(first_value, ensure_ascii=False))
    source_repaired_guide = _guide_root(source_repaired_value)
    for index in allowed:
        source_repaired_guide["questions"][index]["source"] = "根据课堂内容归纳"
    try:
        validator(source_repaired_value)
    except (SchemaValidationError, TypeError, ValueError):
        # The candidate must become fully valid after only the proven source
        # substitutions; otherwise a source repair would be asked to fix more.
        return None
    return lambda value: _check_source_only_guide_repair(first_guide, value, allowed)


def _json_content(
    ai_client: Any,
    prompt: str,
    validator: Callable[[object], dict[str, Any]],
    *,
    schema_hint: str,
    should_cancel: Callable[[], bool] | None = None,
    json_mode: bool = False,
    repair_context: Mapping[str, Any] | None = None,
    guide_source_cues: Sequence[Cue] | None = None,
) -> dict[str, Any]:
    system = "只依据字幕证据处理内容，不补写外部事实。只返回一个严格 JSON 对象。"
    if len(system) + len(prompt) >= MAX_MESSAGE_CHARS:
        raise ValueError("AI 请求超过单次消息安全限制。")
    _check_cancel(should_cancel)
    response = ai_client.chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        max_tokens=4096,
        temperature=0,
        system_prompt=False,
        **_response_format_option(ai_client, json_mode),
    )
    _check_cancel(should_cancel)
    content = response.get("content") if isinstance(response, Mapping) else None
    try:
        return _validated_json(content, validator, response)
    except TranscriptAIFormatError as first_error:
        if getattr(ai_client, "last_call_index", None) is not None:
            first_error.diagnostics["call_index"] = ai_client.last_call_index
        if first_error.category in {"empty", "truncated"}:
            raise
        guide_source_repair = (
            isinstance(repair_context, Mapping)
            and repair_context.get("stage") == "study_guide_generation"
        )
        # A malformed guide has no trustworthy content baseline. A generic
        # schema repair could replace the prompt, answers, themes, or evidence
        # wholesale, so source-only guide workflows fail closed here.
        if guide_source_repair and first_error.category == "format":
            raise
        fragment = _repair_fragment(content)
        if not fragment:
            raise
        mismatch = first_error.diagnostics.get("schema_mismatch")
        safe_mismatch = (
            {key: mismatch[key] for key in ("path", "expected") if type(mismatch.get(key)) is str}
            if isinstance(mismatch, Mapping) else {}
        )
        preservation_guard = _source_only_repair_guard(
            fragment, safe_mismatch, validator, guide_source_cues
        )
        guide_fragment = None
        if guide_source_repair and fragment:
            try:
                guide_fragment = _guide_root(json.loads(fragment))
            except (json.JSONDecodeError, TypeError):
                pass
        if (
            guide_source_repair
            and preservation_guard is None
            and (
                not _guide_question_baseline_is_safe(guide_fragment)
                or _has_unprovable_classroom_source_claim(guide_fragment, guide_source_cues)
            )
        ):
            # An earlier schema error can mask an unsupported classroom-source
            # claim, or the question identity may be too malformed to preserve.
            # Without a source-only baseline guard, generic repair could rewrite
            # the question to make that claim pass or reconstruct it wholesale.
            raise first_error
        if (
            guide_source_repair
            and _QUESTION_SOURCE_PATH.fullmatch(str(safe_mismatch.get("path", "")))
            and preservation_guard is None
        ):
            # A source mismatch can only be repaired when the exact first
            # guide and the cues prove a finite, safe set of source fields.
            raise first_error
        mismatch_details = (
            "\n本次校验失败位置与要求（仅供定位，不含实际模型字段值）："
            + json.dumps(safe_mismatch, ensure_ascii=False, separators=(",", ":"))
        )
        if repair_context is None:
            repair_prompt = (
                "只修复下列 JSON 片段的语法和字段结构，不添加、删除或改写事实。限制："
                + schema_hint
                + mismatch_details
                + "\n片段：\n"
                + fragment
            )
        else:
            repair_details = (
                mismatch_details
                + "\n仅限本次修复使用的受限规则与来源范围："
                + json.dumps(repair_context, ensure_ascii=False, separators=(",", ":"))
            )
            repair_prompt = (
                "只修复下列 JSON 中校验失败的字段，不得删除issue、改变issue的问题含义、扩大/缩小其适用主题或移动到其他cue。"
                "若修复字段是审校evidence，可使用精确 {cue_id,quote_mode:'source_cue'} 协议，让程序绑定该当前cue的完整原文；"
                "若保留legacy {cue_id,quote}，quote仍必须从提供的同一cue原文逐字复制连续片段。不得改写、拼接、跨cue引用或猜测ID。"
                "若来源不支持原issue，保留该失败，不要删改issue来通过校验。不得添加、删除或改写其他事实。限制："
                + schema_hint
                + repair_details
                + "\n片段：\n"
                + fragment
            )
        repair_system = "只修复 JSON，不创造事实。只返回一个严格 JSON 对象。"
        if len(repair_system) + len(repair_prompt) >= MAX_MESSAGE_CHARS:
            raise first_error
        _check_cancel(should_cancel)
        repair = ai_client.chat_completion(
            [
                {
                    "role": "system",
                    "content": repair_system,
                },
                {"role": "user", "content": repair_prompt},
            ],
            max_tokens=4096,
            temperature=0,
            system_prompt=False,
            **_response_format_option(ai_client, json_mode),
        )
        _check_cancel(should_cancel)
        repair_content = repair.get("content") if isinstance(repair, Mapping) else None
        repair_validator = validator
        if preservation_guard is not None:
            def repair_validator(value: object) -> dict[str, Any]:
                preservation_guard(value)
                return validator(value)
        try:
            return _validated_json(repair_content, repair_validator, repair)
        except TranscriptAIFormatError as repair_error:
            if getattr(ai_client, "last_call_index", None) is not None:
                repair_error.diagnostics["call_index"] = ai_client.last_call_index
            raise


def _cue_lookup(cues):
    result = dict()
    for cue in cues or tuple():
        result.update(((cue.cue_id, cue),))
    return result


def _schema_fail(path, expected, actual):
    raise SchemaValidationError(path, expected, actual)


def _required_alias(value, names, path):
    for name in names:
        if name in value:
            result = value.get(name)
            if result is None:
                _schema_fail(path, "required non-null field", result)
            return result
    _schema_fail(path, "required field or known alias", None)


def _coerce_items(value, path, limit):
    if value is None:
        return list()
    if type(value) in (str, dict):
        result = list((value,))
    elif type(value) in (list, tuple):
        result = list(value)
    else:
        _schema_fail(path, "string, object, or array", value)
    if len(result) > limit:
        _schema_fail(path, "bounded array", value)
    return result


def _alias(value, names):
    for name in names:
        if name in value:
            return value.get(name)
    return None


def _coerce_text(value, path, limit, empty=False):
    if type(value) is dict:
        value = _alias(value, ("text", "name", "title", "description", "question", "summary", "content", "value"))
    if type(value) is not str or len(value) > limit or (not empty and not value.strip()):
        _schema_fail(path, "bounded string", value)
    return value.strip()


def _coerce_time(value, path):
    if type(value) is int and value >= 0:
        return value
    if type(value) is float and value >= 0 and value.is_integer():
        return int(value)
    if type(value) is str:
        text = value.strip().lower()
        if text.endswith("ms") and text.removesuffix("ms").strip().isdigit():
            return int(text.removesuffix("ms").strip())
        if text.endswith("s"):
            try:
                seconds = float(text.removesuffix("s").strip())
            except ValueError:
                seconds = -1
            if 0 <= seconds < 1e12:
                return round(seconds.__mul__(1000))
        if text.isdigit():
            return int(text)
        parts = text.replace(",", ".").split(":")
        if len(parts) in (2, 3):
            try:
                seconds = float(parts.pop())
                minutes = int(parts.pop())
                hours = int(parts.pop()) if parts else 0
            except ValueError:
                seconds = -1
                minutes = -1
                hours = -1
            if hours >= 0 and 0 <= minutes < 60 and 0 <= seconds < 60:
                return round((hours.__mul__(3600) + minutes.__mul__(60) + seconds).__mul__(1000))
    _schema_fail(path, "milliseconds or timestamp string", value)


def _unwrap(value, expected):
    if type(value) is not dict:
        _schema_fail("$", "object", value)
    if expected.intersection(value):
        return value
    nested = _alias(value, ("data", "result", "output", "summary"))
    return nested if type(nested) is dict else value


def _text_list(value, path, count, size):
    result = list()
    for index, item in enumerate(_coerce_items(value, path, count)):
        result.append(_coerce_text(item, "%s[%d]" % (path, index), size))
    return result


def _find_evidence_cue(row, cues, quote):
    lookup = _cue_lookup(cues)
    cue_id = _alias(row, ("cue_id", "cueId", "id"))
    candidate = lookup.get(str(cue_id).strip()) if type(cue_id) in (str, int) else None
    if candidate is not None and quote in candidate.text:
        return candidate
    start_value = _alias(row, ("start_ms", "startMs", "start", "start_time", "time", "timestamp"))
    try:
        start = _coerce_time(start_value, "evidence.start_ms") if start_value is not None else None
    except SchemaValidationError:
        start = None
    matches = list()
    for cue in cues:
        if quote in cue.text and (start is None or cue.start_ms <= start <= cue.end_ms):
            matches.append(cue)
    return matches.pop() if len(matches) == 1 else None


def _normalize_evidence(value, cues, path):
    result = list()
    seen = set()
    if cues is None:
        return result
    for raw in _coerce_items(value, path, 12):
        if type(raw) is not dict:
            continue
        quote_value = _alias(raw, ("quote", "text", "description"))
        if type(quote_value) is not str or not quote_value.strip() or len(quote_value) > 240:
            continue
        quote = quote_value.strip()
        cue = _find_evidence_cue(raw, cues, quote)
        key = (cue.cue_id, quote) if cue is not None else None
        if cue is None or key in seen or len(result) >= 6:
            continue
        seen.add(key)
        result.append(dict(cue_id=cue.cue_id, start_ms=cue.start_ms, end_ms=cue.end_ms, quote=quote))
    return sorted(result, key=lambda item: (item.get("start_ms"), item.get("end_ms"), item.get("cue_id"), item.get("quote")))


def _normalize_points(value, path, cues):
    result = list()
    for index, row in enumerate(_coerce_items(value, path, 20)):
        item_path = "%s[%d]" % (path, index)
        text = _coerce_text(row, item_path + ".text", 600)
        evidence = _alias(row, ("evidence", "evidences", "citations", "source")) if type(row) is dict else None
        normalized_evidence = _normalize_evidence(evidence, cues, item_path + ".evidence")
        if cues is not None and not normalized_evidence:
            continue
        result.append(dict(text=text, evidence=normalized_evidence))
    return result


def _combined(value, names):
    result = list()
    for name in names:
        if name in value:
            result.extend(_coerce_items(value.get(name), name, 20))
    return result


def _concept_refs(value, path):
    return _dedupe_strings(
        (_coerce_text(item, path, 200) for item in _coerce_items(value, path, 12)),
        12,
    )


def _normalize_knowledge_points(value, cues):
    result = list()
    allowed = frozenset(("definition", "principle", "method", "fact", "formula", "conclusion"))
    for index, row in enumerate(_coerce_items(value, "knowledge_points", 30)):
        path = "knowledge_points[%d]" % index
        if type(row) is not dict:
            _schema_fail(path, "object", row)
        kind = _coerce_text(_alias(row, ("kind", "type", "category")), path + ".kind", 32).casefold()
        if kind not in allowed:
            _schema_fail(path + ".kind", "definition/principle/method/fact/formula/conclusion", kind)
        concept = _coerce_text(_alias(row, ("concept", "name", "title")), path + ".concept", 200)
        statement = _coerce_text(_alias(row, ("statement", "text", "description")), path + ".statement", 600)
        evidence = _normalize_evidence(_alias(row, ("evidence", "citations", "source")), cues, path + ".evidence")
        if cues is not None and not evidence:
            continue
        result.append(dict(concept=concept, kind=kind, statement=statement, evidence=evidence))
    return result


def _normalize_classroom_examples(value, cues):
    result = list()
    for index, row in enumerate(_coerce_items(value, "classroom_examples", 20)):
        path = "classroom_examples[%d]" % index
        if type(row) is not dict:
            _schema_fail(path, "object", row)
        example = _coerce_text(_alias(row, ("example", "text", "description")), path + ".example", 600)
        related = _concept_refs(_alias(row, ("related_knowledge_points", "related_concepts", "concepts")), path + ".related_knowledge_points")
        evidence = _normalize_evidence(_alias(row, ("evidence", "citations", "source")), cues, path + ".evidence")
        if not related or (cues is not None and not evidence):
            continue
        result.append(dict(example=example, related_knowledge_points=related, evidence=evidence))
    return result


def _normalize_practice_items(value, cues):
    result = list()
    allowed = frozenset(("recall", "application"))
    for index, row in enumerate(_coerce_items(value, "practice_items", 20)):
        path = "practice_items[%d]" % index
        if type(row) is not dict:
            _schema_fail(path, "object", row)
        item_type = _coerce_text(_alias(row, ("type", "kind")), path + ".type", 32).casefold()
        if item_type not in allowed:
            _schema_fail(path + ".type", "recall or application", item_type)
        prompt = _coerce_text(_alias(row, ("prompt", "question", "text")), path + ".prompt", 600)
        related = _concept_refs(_alias(row, ("related_knowledge_points", "related_concepts", "concepts")), path + ".related_knowledge_points")
        answer = _text_list(row.get("answer_key"), path + ".answer_key", 12, 300)
        rubric = _text_list(row.get("rubric"), path + ".rubric", 12, 300)
        evidence = _normalize_evidence(_alias(row, ("evidence", "citations", "source")), cues, path + ".evidence")
        if not related or not (answer or rubric) or (cues is not None and not evidence):
            continue
        result.append(dict(type=item_type, prompt=prompt, related_knowledge_points=related, answer_key=answer, rubric=rubric, evidence=evidence))
    return result


def normalize_map_output(value, cues=None):
    source = _unwrap(value, MAP_KEYS)
    raw_cleaned = _required_alias(source, ("cleaned_transcript", "cleanedTranscript", "cleaned", "transcript", "cues"), "cleaned_transcript")
    rows = _coerce_items(raw_cleaned, "cleaned_transcript", MAX_CHUNK_CUES)
    lookup = _cue_lookup(cues)
    positions = iter(cues or tuple())
    cleaned = list()
    total = 0
    for index, row in enumerate(rows):
        path = "cleaned_transcript[%d]" % index
        text = _coerce_text(row, path + ".text", MAX_CHUNK_CHARS if cues is not None else 600)
        if cues is not None:
            positional = next(positions, None)
            cue_id_value = _alias(row, ("cue_id", "cueId", "id")) if type(row) is dict else None
            if type(cue_id_value) in (str, int):
                cue = lookup.get(str(cue_id_value).strip())
                if cue is None:
                    _schema_fail(path + ".cue_id", "existing cue id", cue_id_value)
            elif type(row) is dict and _alias(row, ("start_ms", "startMs", "start", "start_time")) is not None:
                start_value = _alias(row, ("start_ms", "startMs", "start", "start_time"))
                try:
                    candidate_start = _coerce_time(start_value, path + ".start_ms")
                except SchemaValidationError:
                    candidate_start = None
                matches = list(item for item in cues if candidate_start is not None and item.start_ms <= candidate_start <= item.end_ms)
                cue = matches.pop() if len(matches) == 1 else None
            else:
                cue = positional
            if cue is None:
                continue
            cue_id, start, end = cue.cue_id, cue.start_ms, cue.end_ms
        else:
            if type(row) is not dict:
                _schema_fail(path, "object with cue fields", row)
            cue_id = _coerce_text(_required_alias(row, ("cue_id", "cueId", "id"), path + ".cue_id"), path + ".cue_id", 64)
            start = _coerce_time(_required_alias(row, ("start_ms", "startMs", "start", "start_time"), path + ".start_ms"), path + ".start_ms")
            end = _coerce_time(_required_alias(row, ("end_ms", "endMs", "end", "end_time"), path + ".end_ms"), path + ".end_ms")
            if end <= start:
                _schema_fail(path + ".end_ms", "time after start", end)
        total += len(text)
        if total > 7000:
            _schema_fail("cleaned_transcript", "total text length at most 7000", rows)
        cleaned.append(dict(cue_id=cue_id, start_ms=start, end_ms=end, text=text))
    if not cleaned:
        _schema_fail("cleaned_transcript", "non-empty array", raw_cleaned)
    case_names = ("cases_formulas_conclusions", "casesFormulasConclusions", "cases", "examples", "formulas", "conclusions")
    if not any(name in source for name in case_names):
        _schema_fail("cases_formulas_conclusions", "required field or known alias", None)
    result = dict(
        cleaned_transcript=cleaned,
        topics=_text_list(_required_alias(source, ("topics", "topic", "lesson_topics", "main_topics"), "topics"), "topics", 12, 200),
        emphasized_points=_normalize_points(_required_alias(source, ("emphasized_points", "emphasizedPoints", "key_points", "highlights"), "emphasized_points"), "emphasized_points", cues),
        concepts=_normalize_points(_required_alias(source, ("concepts", "key_concepts", "terms"), "concepts"), "concepts", cues),
        cases_formulas_conclusions=_normalize_points(_combined(source, case_names), "cases_formulas_conclusions", cues),
        review_questions=_text_list(_required_alias(source, ("review_questions", "reviewQuestions", "questions"), "review_questions"), "review_questions", 12, 300),
        knowledge_points=_normalize_knowledge_points(source.get("knowledge_points", list()), cues),
        classroom_examples=_normalize_classroom_examples(source.get("classroom_examples", list()), cues),
        practice_items=_normalize_practice_items(source.get("practice_items", list()), cues),
    )
    warning = source.get("partial_warning")
    if warning is not None:
        warning = _coerce_text(warning, "partial_warning", 120)
        if warning != MAP_FALLBACK_WARNING:
            _schema_fail("partial_warning", "known warning", warning)
        result.update(partial_warning=warning)
    return result


def _normalize_timeline(value, cues):
    result = list()
    lookup = _cue_lookup(cues)
    for index, row in enumerate(_coerce_items(value, "timeline", 80)):
        path = "timeline[%d]" % index
        if type(row) is not dict:
            _schema_fail(path, "object", row)
        title = _coerce_text(_alias(row, ("title", "name", "text")), path + ".title", 200)
        summary = _coerce_text(_alias(row, ("summary", "description", "text")), path + ".summary", 600)
        cue_id_value = _alias(row, ("cue_id", "cueId", "id"))
        cue = lookup.get(str(cue_id_value).strip()) if cues is not None and type(cue_id_value) in (str, int) else None
        if cues is not None and cue is None:
            start_value = _alias(row, ("start_ms", "startMs", "start", "time", "timestamp"))
            try:
                candidate_start = _coerce_time(start_value, path + ".start_ms")
            except SchemaValidationError:
                candidate_start = None
            matches = list(item for item in cues if candidate_start is not None and item.start_ms <= candidate_start <= item.end_ms)
            cue = matches.pop() if len(matches) == 1 else None
        if cues is not None and cue is None:
            continue
        cue_id = cue.cue_id if cue is not None else _coerce_text(cue_id_value, path + ".cue_id", 64)
        start = cue.start_ms if cue is not None else _coerce_time(_alias(row, ("start_ms", "startMs", "start", "time", "timestamp")), path + ".start_ms")
        result.append(dict(cue_id=cue_id, start_ms=start, title=title, summary=summary))
    return sorted(result, key=lambda item: (item.get("start_ms"), item.get("cue_id"), item.get("title")))


def normalize_summary_output(value, cues=None):
    source = _unwrap(value, SUMMARY_KEYS)
    case_names = ("cases_formulas_conclusions", "casesFormulasConclusions", "cases", "examples", "formulas", "conclusions")
    if not any(name in source for name in case_names):
        _schema_fail("cases_formulas_conclusions", "required field or known alias", None)
    return dict(
        lesson_topic=_coerce_text(_required_alias(source, ("lesson_topic", "lessonTopic", "topic", "title", "name"), "lesson_topic"), "lesson_topic", 300),
        learning_objectives=_text_list(_required_alias(source, ("learning_objectives", "learningObjectives", "objectives", "goals"), "learning_objectives"), "learning_objectives", 12, 300),
        emphasized_points=_normalize_points(_required_alias(source, ("emphasized_points", "emphasizedPoints", "key_points", "highlights"), "emphasized_points"), "emphasized_points", cues),
        concepts=_normalize_points(_required_alias(source, ("concepts", "key_concepts", "terms"), "concepts"), "concepts", cues),
        cases_formulas_conclusions=_normalize_points(_combined(source, case_names), "cases_formulas_conclusions", cues),
        review_questions=_text_list(_required_alias(source, ("review_questions", "reviewQuestions", "questions"), "review_questions"), "review_questions", 12, 300),
        timeline=_normalize_timeline(_required_alias(source, ("timeline", "time_line", "chapters", "outline"), "timeline"), cues),
        knowledge_points=_normalize_knowledge_points(source.get("knowledge_points", list()), cues),
        classroom_examples=_normalize_classroom_examples(source.get("classroom_examples", list()), cues),
        practice_items=_normalize_practice_items(source.get("practice_items", list()), cues),
    )


def validate_map(value, cues=None):
    return normalize_map_output(value, cues)


def validate_summary(value, cues=None):
    return normalize_summary_output(value, cues)




PLAIN_MAP_SECTIONS = dict((
    ("TOPICS", "topics"),
    ("EMPHASIZED_POINTS", "emphasized_points"),
    ("CONCEPTS", "concepts"),
    ("CASES_FORMULAS_CONCLUSIONS", "cases_formulas_conclusions"),
    ("REVIEW_QUESTIONS", "review_questions"),
    ("KNOWLEDGE_POINTS", "knowledge_points"),
    ("CLASSROOM_EXAMPLES", "classroom_examples"),
    ("PRACTICE_ITEMS", "practice_items"),
))
LEGACY_PLAIN_MAP_FIELDS = frozenset((
    "topics", "emphasized_points", "concepts",
    "cases_formulas_conclusions", "review_questions",
))
PLAIN_MAP_FORMAT = (
    "不要输出 JSON、代码围栏或 HTML，也不要复述字幕。严格按以下八个标签段输出；每行只能以 - 开头。"
    "知识点只收录字幕明确陈述的定义、原则、方法、事实、公式或结论；教师演示、案例、类比只能放 CLASSROOM_EXAMPLES，"
    "且关联知识点。无法判定时写 NONE。练习是学习者练习，不是 ASR 纠错/训练样本；只允许 recall 或 application，"
    "必须给答案要点或评估标准。所有事实性条目都带同一 cue 的逐字引文。\n"
    "[TOPICS]\n- 主题\n"
    "[EMPHASIZED_POINTS]\n- 要点 || cue-000001 || 逐字短引文\n"
    "[CONCEPTS]\n- 兼容概念说明 || cue-000001 || 逐字短引文\n"
    "[CASES_FORMULAS_CONCLUSIONS]\n- 兼容案例、公式或结论 || cue-000001 || 逐字短引文\n"
    "[REVIEW_QUESTIONS]\n- 兼容复习问题\n"
    "[KNOWLEDGE_POINTS]\n- definition/principle/method/fact/formula/conclusion || 概念 || 可验证陈述 || cue-000001 || 逐字短引文\n"
    "[CLASSROOM_EXAMPLES]\n- 明确标记的课堂例子/演示/类比 || 关联知识点概念 || cue-000001 || 逐字短引文\n"
    "[PRACTICE_ITEMS]\n- recall/application || 题目 || 关联知识点概念 || 答案要点或评估标准 || cue-000001 || 逐字短引文"
)
JSON_MAP_FORMAT = (
    "只返回一个JSON对象，且仅含topics、emphasized_points、concepts、cases_formulas_conclusions、review_questions、"
    "knowledge_points、classroom_examples、practice_items八个字段；它们全部是数组，cleaned_transcript由程序按当前chunk原样补入，不要输出。"
    "topics与review_questions的元素是字符串；emphasized_points、concepts、cases_formulas_conclusions的元素严格为{text,evidence}；"
    "evidence是[{cue_id,quote}]，cue_id只能使用本块给定值，quote必须是该cue中的连续逐字原文，时间由程序计算。"
    "knowledge_points元素严格为{kind,concept,statement,evidence}，kind只能definition/principle/method/fact/formula/conclusion；"
    "classroom_examples元素严格为{example,related_knowledge_points,evidence}；practice_items元素严格为"
    "{type,prompt,related_knowledge_points,answer_key,rubric,evidence}，type只能recall/application，answer_key与rubric是字符串数组。"
    "没有字幕依据的单项分类可留空；不要为了填满数组或响应格式而造主题、引文、课堂例子或练习。若本段确实没有任何可由原文支持的学习内容，"
    "所有学习内容数组留空表示本段无法提取有据内容；这不是已经提取完成的证据，也不能据此推断全片内容不足。"
    "结构示例（只示范字段与类型；evidence使用下方本块中的真实cue与短原文，其他示例文字不可照抄）："
    '{"topics":["从当前原文提炼的主题"],"emphasized_points":[{"text":"从当前原文提炼的要点","evidence":[{"cue_id":"{example_cue_id}","quote":"{example_quote}"}]}],'
    '"concepts":[],"cases_formulas_conclusions":[],"review_questions":[],"knowledge_points":[{"kind":"fact","concept":"从该原文提炼的概念",'
    '"statement":"{example_quote}","evidence":[{"cue_id":"{example_cue_id}","quote":"{example_quote}"}]}],'
    '"classroom_examples":[],"practice_items":[]}。'
)


def _json_map_format(cues: Sequence[Cue]) -> str:
    """Bind the format example to a real cue in this chunk, without inventing evidence."""
    example_cue = next((cue for cue in cues if cue.text.strip()), None)
    if example_cue is None:
        return JSON_MAP_FORMAT.replace('{"cue_id":"{example_cue_id}","quote":"{example_quote}"}', "null")
    quote = example_cue.text.strip()[:120]
    return (
        JSON_MAP_FORMAT
        .replace("{example_cue_id}", json.dumps(example_cue.cue_id, ensure_ascii=False)[1:-1])
        .replace("{example_quote}", json.dumps(quote, ensure_ascii=False)[1:-1])
    )



def _map_with_source_cues(value, cues):
    source = _unwrap(value, MAP_KEYS)
    if type(source) is not dict:
        _schema_fail("$", "object", source)
    payload = dict(source)
    for name in ("cleaned_transcript", "cleanedTranscript", "cleaned", "transcript", "cues"):
        payload.pop(name, None)
    payload.update(cleaned_transcript=list(cue.as_dict() for cue in cues))
    return payload


def has_map_insights(value):
    return bool(
        value.get("topics")
        and any(
            value.get(name)
            for name in (
                "knowledge_points",
                "emphasized_points",
                "concepts",
                "cases_formulas_conclusions",
            )
        )
    )


def _validate_map_insights(value, cues):
    normalized = validate_map(_map_with_source_cues(value, cues), cues)
    if normalized.get("partial_warning"):
        return normalized
    if not normalized.get("topics"):
        _schema_fail("topics", "non-empty array with a source-grounded topic", normalized.get("topics"))
    if not has_map_insights(normalized):
        _schema_fail("$", "at least one source-grounded point, concept, case, conclusion, formula, or question", value)
    return normalized


def parse_plain_map_output(content, cues):
    if type(content) is not str or not content.strip():
        _schema_fail("$", "non-empty plain structured response", content)
    lines = content.replace("\r\n", "\n").replace("\r", "\n").strip().lstrip("\ufeff").split("\n")
    if lines and lines[0].strip().startswith("```"):
        if len(lines) < 3 or lines[-1].strip() != "```":
            _schema_fail("$", "complete optional code fence", content)
        lines = lines[1:-1]
    sections = dict((field, list()) for field in PLAIN_MAP_SECTIONS.values())
    headings = dict(("[%s]" % label, field) for label, field in PLAIN_MAP_SECTIONS.items())
    headings.update(("## %s" % label, field) for label, field in PLAIN_MAP_SECTIONS.items())
    seen = set()
    current = None
    none_sections = set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line in headings:
            current = headings.get(line)
            if current in seen:
                _schema_fail(current, "section appearing once", line)
            seen.add(current)
            continue
        if current is None or not line.startswith("- "):
            _schema_fail("$", "only known headings and dash-prefixed rows", line)
        item = line[2:].strip()
        if item == "NONE":
            if sections.get(current):
                _schema_fail(current, "NONE or rows, not both", item)
            none_sections.add(current)
            continue
        if current in none_sections:
            _schema_fail(current, "NONE or rows, not both", item)
        if current in ("topics", "review_questions"):
            sections.get(current).append(item)
            continue
        parts = tuple(part.strip() for part in item.split("||"))
        if current == "knowledge_points":
            if len(parts) != 5 or not all(parts):
                _schema_fail(current, "kind || concept || statement || cue_id || exact quote", item)
            kind, concept, statement, cue_id, quote = parts
            sections.get(current).append(dict(
                kind=kind, concept=concept, statement=statement,
                evidence=list((dict(cue_id=cue_id, quote=quote),)),
            ))
            continue
        if current == "classroom_examples":
            if len(parts) != 4 or not all(parts):
                _schema_fail(current, "example || related concept || cue_id || exact quote", item)
            example, related, cue_id, quote = parts
            sections.get(current).append(dict(
                example=example, related_knowledge_points=list((related,)),
                evidence=list((dict(cue_id=cue_id, quote=quote),)),
            ))
            continue
        if current == "practice_items":
            if len(parts) != 6 or not all(parts):
                _schema_fail(current, "type || prompt || related concept || answer/rubric || cue_id || exact quote", item)
            item_type, prompt, related, answer, cue_id, quote = parts
            sections.get(current).append(dict(
                type=item_type, prompt=prompt,
                related_knowledge_points=list((related,)), answer_key=list((answer,)), rubric=list(),
                evidence=list((dict(cue_id=cue_id, quote=quote),)),
            ))
            continue
        if len(parts) != 3 or not all(parts):
            _schema_fail(current, "text || cue_id || exact quote", item)
        text, cue_id, quote = parts
        sections.get(current).append(
            dict(text=text, evidence=list((dict(cue_id=cue_id, quote=quote),)))
        )
    if not LEGACY_PLAIN_MAP_FIELDS.issubset(seen):
        _schema_fail("$", "five legacy sections and optional learning-product sections", tuple(sorted(seen)))
    return _validate_map_insights(sections, cues)


def _validated_map_response(content, response, cues):
    try:
        return _validated_json(
            content, lambda value: _validate_map_insights(value, cues), response
        )
    except TranscriptAIFormatError as json_error:
        try:
            return parse_plain_map_output(content, cues)
        except SchemaValidationError as plain_error:
            if json_error.category == "truncated":
                raise json_error
            diagnostics = dict(json_error.diagnostics)
            diagnostics.update(plain_schema_mismatch=dict(plain_error.detail))
            raise TranscriptAIFormatError(
                "AI 返回内容不符合精简 JSON 或固定标签格式，请重试或更换模型。",
                category="schema" if json_error.category == "schema" else "format",
                diagnostics=diagnostics,
            ) from None


def _map_content(ai_client, prompt, cues, should_cancel=None, *, json_mode: bool = False):
    system = (
        "只依据字幕证据提取内容，不补写外部事实。知识点仅限定义、原则、方法、事实、公式和结论；"
        "教师演示、案例和类比必须单列为课堂例子并关联知识点。无法判定时宁缺毋滥并保留逐字证据。"
        "练习面向学生回忆或应用，不得把 ASR 纠错样本伪装成学习训练。"
        + ("严格返回JSON对象，不输出固定标签文本。" if json_mode else "严格使用用户指定的固定标签纯文本格式。")
    )
    if len(system) + len(prompt) >= MAX_MESSAGE_CHARS:
        raise ValueError("AI 请求超过单次消息安全限制。")
    _check_cancel(should_cancel)
    response = ai_client.chat_completion(
        list((dict(role="system", content=system), dict(role="user", content=prompt))),
        max_tokens=3072,
        temperature=0,
        system_prompt=False,
        **_response_format_option(ai_client, json_mode),
    )
    _check_cancel(should_cancel)
    content = response.get("content") if isinstance(response, Mapping) else None
    try:
        return _validated_map_response(content, response, cues)
    except TranscriptAIFormatError as exc:
        if getattr(ai_client, "last_call_index", None) is not None:
            exc.diagnostics["call_index"] = ai_client.last_call_index
        raise


def _fallback_map(chunk: TranscriptChunk) -> dict[str, Any]:
    return {
        "cleaned_transcript": [cue.as_dict() for cue in chunk.cues],
        "topics": [],
        "emphasized_points": [],
        "concepts": [],
        "cases_formulas_conclusions": [],
        "review_questions": [],
        "knowledge_points": [],
        "classroom_examples": [],
        "practice_items": [],
        "partial_warning": MAP_FALLBACK_WARNING,
    }


def _markdown_text(value: object) -> str:
    return html.escape(str(value), quote=False)


def render_cleaned(chunks: Sequence[Mapping[str, Any]]) -> str:
    lines = list(("# 规整字幕", ""))
    by_cue_id: dict[str, Mapping[str, Any]] = dict()
    for chunk in chunks:
        for row in chunk.get("cleaned_transcript", list()):
            by_cue_id.setdefault(str(row.get("cue_id")), row)
    ordered = sorted(
        by_cue_id.values(),
        key=lambda row: (int(row.get("start_ms")), int(row.get("end_ms")), str(row.get("cue_id"))),
    )
    for row in ordered:
        seconds = int(row.get("start_ms")) // 1000
        lines.append(
            f"**{seconds // 60:02d}:{seconds % 60:02d}**  {_markdown_text(row.get('text'))}"
        )
    return "\n\n".join(lines).strip() + "\n"


def _time_label(milliseconds: object) -> str:
    value = int(milliseconds or 0) // 1000
    return f"{value // 60:02d}:{value % 60:02d}"


def _evidence_markdown(rows: Sequence[Mapping[str, Any]]) -> str:
    return "；".join(
        f"{_time_label(row.get('start_ms'))} {_markdown_text(row.get('quote', ''))}"
        for row in rows
    )


def render_summary(summary: Mapping[str, Any]) -> str:
    """Render the learnable handout while retaining legacy summary JSON fields."""
    guide = summary.get("study_guide")
    review = summary.get("review")
    if not isinstance(guide, Mapping):
        title = summary.get("lesson_topic") or "暂未提取到可验证的本节主题"
        return "# " + _markdown_text(title) + "\n"
    lines = ["# " + _markdown_text(summary.get("lesson_topic") or "课程学习讲义"), "", "## 课程地图"]
    lines.extend(f"{index}. {_markdown_text(item)}" for index, item in enumerate(guide.get("course_map", ()), 1))
    lines.extend(("", "## 分主题讲义"))
    for index, theme in enumerate(guide.get("themes", ()), 1):
        lines.extend(("", f"### {index}. {_markdown_text(theme.get('title'))}"))
        lines.append(f"- **一句话结论**：{_markdown_text(theme.get('conclusion'))}")
        lines.append(f"- **原理与适用场景**：{_markdown_text(theme.get('principle_and_context'))}")
        lines.append(f"- **为什么与错误后果**：{_markdown_text(theme.get('why_and_consequences'))}")
        lines.append("- **判断 / 修改步骤**：" + " → ".join(_markdown_text(step) for step in theme.get("steps", ())))
        example = theme.get("classroom_example")
        if isinstance(example, Mapping):
            lines.append(
                "- **课堂案例**：原问法“%s”；问题：%s；改进方向：%s"
                % tuple(_markdown_text(example.get(key)) for key in ("original_question", "problem", "improvement"))
            )
        lines.append(f"- **边界**：{_markdown_text(theme.get('boundaries'))}")
        lines.append(f"- **前后关系**：{_markdown_text(theme.get('connections'))}")
        lines.append(f"- **性质**：{_markdown_text(theme.get('source'))}")
        evidence = _evidence_markdown(theme.get("evidence", ()))
        if evidence:
            lines.append(f"- **字幕证据**：{evidence}")
    lines.extend(("", "## 一页检查清单"))
    lines.extend(f"- [ ] {_markdown_text(item)}" for item in guide.get("checklist", ()))
    lines.extend(("", "## 题目"))
    for index, item in enumerate(guide.get("questions", ()), 1):
        lines.append(f"{index}. 【{_markdown_text(item.get('source'))}】{_markdown_text(item.get('prompt'))}")
    lines.extend(("", "## 延伸问题"))
    lines.extend(f"- {_markdown_text(item)}" for item in guide.get("extensions", ()))
    lines.extend(("", "## 参考答案"))
    for index, item in enumerate(guide.get("questions", ()), 1):
        lines.append(f"{index}. {_markdown_text(item.get('answer'))}")
    lines.extend(("", "## 审校记录"))
    if isinstance(review, Mapping):
        lines.append(f"- 状态：{_markdown_text(review.get('status', '未审校'))}")
        for item in review.get("issues", ()):
            evidence = item.get("evidence", "")
            lines.append(
                "- **%s**：%s；证据：%s；修改：%s"
                % (
                    _markdown_text(item.get("type", "")),
                    _markdown_text(item.get("problem", "")),
                    _evidence_markdown(evidence) if isinstance(evidence, list) else _markdown_text(evidence),
                    _markdown_text(item.get("fix", "")),
                )
            )
        for row in review.get("coverage_ledger", ()):
            lines.append(
                "- 覆盖 %s–%s：%s"
                % (_time_label(row.get("start_ms")), _time_label(row.get("end_ms")), _markdown_text(row.get("status")))
            )
    else:
        lines.append("- 状态：未审校")
    return "\n".join(lines).strip() + "\n"


def _dedupe_strings(values, limit):
    result = list()
    seen = set()
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
        if len(result) >= limit:
            break
    return result


def _merge_points(chunks, field):
    ordered = list()
    by_text = dict()
    for chunk in chunks:
        for point in chunk.get(field, list()):
            text = point.get("text")
            evidence_rows = point.get("evidence", list())
            if type(text) is not str or not text.strip() or not evidence_rows:
                continue
            key = text.strip().casefold()
            if key not in by_text:
                target = dict(text=text.strip(), evidence=list())
                by_text.update(((key, target),))
                ordered.append(target)
            target = by_text.get(key)
            known = set((item.get("cue_id"), item.get("quote")) for item in target.get("evidence", list()))
            for evidence in evidence_rows:
                evidence_key = (evidence.get("cue_id"), evidence.get("quote"))
                if evidence_key not in known and len(target.get("evidence", list())) < 6:
                    target.get("evidence").append(dict(evidence))
                    known.add(evidence_key)
    for point in ordered:
        point.get("evidence").sort(key=lambda item: (item.get("start_ms"), item.get("end_ms"), item.get("cue_id"), item.get("quote")))
    ordered.sort(key=lambda point: (point.get("evidence")[0].get("start_ms"), point.get("text").casefold()))
    return ordered[:20]


def _canonical_learning_key(value):
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _merge_normalized_strings(target, values, limit):
    seen = set(_canonical_learning_key(value) for value in target)
    for value in values:
        key = _canonical_learning_key(value)
        if key and key not in seen and len(target) < limit:
            target.append(value.strip())
            seen.add(key)
    return target


def _merge_evidence(target, rows):
    known = set((item.get("cue_id"), item.get("quote")) for item in target)
    for evidence in rows:
        key = (evidence.get("cue_id"), evidence.get("quote"))
        if key not in known and len(target) < 6:
            target.append(dict(evidence))
            known.add(key)
    target.sort(key=lambda item: (item.get("start_ms"), item.get("end_ms"), item.get("cue_id"), item.get("quote")))


def _first_evidence_start(item):
    first = next(iter(item.get("evidence", list())), dict())
    return first.get("start_ms")


def _merge_learning_products(chunks):
    knowledge = list()
    knowledge_by_key = dict()
    for chunk in chunks:
        for item in chunk.get("knowledge_points", list()):
            key = (item.get("kind"), _canonical_learning_key(item.get("concept")), _canonical_learning_key(item.get("statement")))
            if not all(key) or not item.get("evidence"):
                continue
            target = knowledge_by_key.get(key)
            if target is None:
                target = dict(concept=item.get("concept"), kind=item.get("kind"), statement=item.get("statement"), evidence=list())
                knowledge_by_key[key] = target
                knowledge.append(target)
            _merge_evidence(target.get("evidence"), item.get("evidence", list()))
    knowledge.sort(key=lambda item: (_first_evidence_start(item), _canonical_learning_key(item.get("concept")), item.get("kind")))
    knowledge = knowledge[:30]
    known_concepts = set(_canonical_learning_key(item.get("concept")) for item in knowledge)
    examples = list()
    example_by_key = dict()
    practices = list()
    practice_by_key = dict()
    for chunk in chunks:
        for item in chunk.get("classroom_examples", list()):
            related = _dedupe_strings((ref for ref in item.get("related_knowledge_points", list()) if _canonical_learning_key(ref) in known_concepts), 12)
            key = _canonical_learning_key(item.get("example"))
            if not key or not related or not item.get("evidence"):
                continue
            target = example_by_key.get(key)
            if target is None:
                target = dict(example=item.get("example"), related_knowledge_points=related, evidence=list())
                example_by_key[key] = target
                examples.append(target)
            else:
                target["related_knowledge_points"] = _dedupe_strings(target.get("related_knowledge_points") + related, 12)
            _merge_evidence(target.get("evidence"), item.get("evidence", list()))
        for item in chunk.get("practice_items", list()):
            related = _merge_normalized_strings(list(), (
                ref for ref in item.get("related_knowledge_points", list())
                if _canonical_learning_key(ref) in known_concepts
            ), 12)
            key = (item.get("type"), _canonical_learning_key(item.get("prompt")))
            if not all(key) or not related or not item.get("evidence") or not (item.get("answer_key") or item.get("rubric")):
                continue
            target = practice_by_key.get(key)
            if target is None:
                target = dict(
                    type=item.get("type"), prompt=item.get("prompt"),
                    related_knowledge_points=list(), answer_key=list(), rubric=list(),
                    evidence=list(),
                )
                practice_by_key[key] = target
                practices.append(target)
            _merge_normalized_strings(target.get("related_knowledge_points"), related, 12)
            _merge_normalized_strings(target.get("answer_key"), item.get("answer_key", list()), 12)
            _merge_normalized_strings(target.get("rubric"), item.get("rubric", list()), 12)
            _merge_evidence(target.get("evidence"), item.get("evidence", list()))
    examples.sort(key=lambda item: (_first_evidence_start(item), _canonical_learning_key(item.get("example"))))
    practices.sort(key=lambda item: (_first_evidence_start(item), item.get("type"), _canonical_learning_key(item.get("prompt"))))
    if knowledge and not practices:
        for item in knowledge[:12]:
            practices.append(dict(type="recall", prompt="请说明%s。" % item.get("concept"), related_knowledge_points=list((item.get("concept"),)), answer_key=list((item.get("statement"),)), rubric=list(), evidence=list(dict(row) for row in item.get("evidence", list()))))
    return knowledge, examples[:20], practices[:20]


def _reduce_failure_diagnostics(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, TranscriptAIFormatError):
        diagnostics = dict(exc.diagnostics)
        diagnostics["category"] = exc.category
        return diagnostics
    if isinstance(exc, SchemaValidationError):
        return dict(category="schema", schema_mismatch=dict(exc.detail))
    name = type(exc).__name__
    lowered = name.casefold()
    status_code = getattr(exc, "status_code", None)
    provider_category = getattr(exc, "category", None)
    known_categories = (
        "authentication", "endpoint_or_model", "http_error", "network",
        "rate_limit_or_quota", "request_parameters", "response_format",
        "timeout", "unsupported_tools", "upstream_service",
    )
    if type(provider_category) is str and provider_category in known_categories:
        category = provider_category
    elif isinstance(exc, TimeoutError) or "timeout" in lowered:
        category = "timeout"
    elif isinstance(exc, (ConnectionError, OSError)) or any(token in lowered for token in ("connection", "network", "transport")):
        category = "network"
    elif type(status_code) is int and status_code >= 500:
        category = "upstream_5xx"
    else:
        category = "reduce_error"
    diagnostics: dict[str, Any] = dict(category=category, error_type=name[:120])
    if type(status_code) is int:
        diagnostics["status_code"] = status_code
    return diagnostics


def summary_has_content(summary):
    return bool(
        summary.get("lesson_topic")
        and any(
            summary.get(name)
            for name in (
                "knowledge_points",
                "emphasized_points",
                "concepts",
                "cases_formulas_conclusions",
            )
        )
    )


def deterministic_summary(chunks):
    topics = _dedupe_strings((item for chunk in chunks for item in chunk.get("topics", list())), 12)
    questions = _dedupe_strings((item for chunk in chunks for item in chunk.get("review_questions", list())), 12)
    knowledge, examples, practices = _merge_learning_products(chunks)
    return dict(
        lesson_topic=next(iter(topics), ""),
        learning_objectives=list(),
        emphasized_points=_merge_points(chunks, "emphasized_points"),
        concepts=_merge_points(chunks, "concepts"),
        cases_formulas_conclusions=_merge_points(chunks, "cases_formulas_conclusions"),
        review_questions=questions,
        timeline=list(),
        knowledge_points=knowledge,
        classroom_examples=examples,
        practice_items=practices,
    )


def _clean_learning_text(value: object, fallback: str = "") -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text or text in PLACEHOLDER_TERMS:
        return fallback
    return text[:800]


def _normalize_guide_evidence(value: object, cues: Sequence[Cue], path: str) -> list[dict[str, Any]]:
    return _normalize_evidence(value, cues, path)


def _stable_topic_id(
    explicit: object,
    evidence: Sequence[Mapping[str, Any]],
    conclusion: str,
) -> str:
    if type(explicit) is str and re.fullmatch(r"topic-[a-zA-Z0-9_-]{1,64}", explicit.strip()):
        return explicit.strip()
    basis = "|".join(str(item.get("cue_id") or "") for item in evidence)
    basis += "|" + _canonical_learning_key(conclusion)
    return "topic-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _normalize_study_guide(value: object, cues: Sequence[Cue]) -> dict[str, Any]:
    if type(value) is not dict:
        _schema_fail("study_guide", "object", value)
    root_fields = {"course_map", "themes", "checklist", "questions", "extensions"}
    if set(value) != root_fields:
        _schema_fail("study_guide", "exact course_map/themes/checklist/questions/extensions fields", value)
    for name in root_fields:
        if type(value[name]) is not list:
            _schema_fail(name, "array", value[name])
    course_map = [_clean_learning_text(item) for item in _coerce_items(value.get("course_map"), "course_map", 6)]
    course_map = [item for item in course_map if item]
    if not 4 <= len(course_map) <= 6:
        _schema_fail("course_map", "4-6 substantive rows", course_map)
    themes = []
    seen = set()
    for index, raw in enumerate(_coerce_items(value.get("themes"), "themes", 12)):
        path = f"themes[{index}]"
        if type(raw) is not dict:
            _schema_fail(path, "object", raw)
        theme_fields = {
            "topic_id", "title", "conclusion", "principle_and_context", "why_and_consequences",
            "steps", "classroom_example", "boundaries", "connections", "source", "evidence",
        }
        if set(raw) != theme_fields:
            _schema_fail(path, "exact theme fields including nullable classroom_example", raw)
        title = _clean_learning_text(raw.get("title"))
        conclusion = _clean_learning_text(raw.get("conclusion"))
        if not title or not conclusion:
            _schema_fail(path, "non-empty title and conclusion", raw)
        topic_id = raw.get("topic_id")
        if type(topic_id) is not str or not re.fullmatch(r"topic-[a-zA-Z0-9_-]{1,64}", topic_id.strip()):
            _schema_fail(path + ".topic_id", "stable topic-id string", topic_id)
        source = _clean_learning_text(raw.get("source"))
        if source not in SOURCE_TYPES:
            _schema_fail(path + ".source", "closed source enum", source)
        raw_evidence = raw.get("evidence")
        if type(raw_evidence) is not list or not raw_evidence or any(
            type(row) is not dict or set(row) != {"cue_id", "quote"} for row in raw_evidence
        ):
            _schema_fail(path + ".evidence", "non-empty array of exact {cue_id,quote} objects", raw_evidence)
        evidence = _normalize_guide_evidence(raw_evidence, cues, path + ".evidence")
        if len(evidence) != len(raw_evidence):
            _schema_fail(path + ".evidence", "every quote must match a current cue exactly", raw_evidence)
        if not evidence:
            _schema_fail(path + ".evidence", "at least one exact current-cue citation for every theme", evidence)
        if topic_id in seen:
            continue
        if source == "课堂明确讲述" and not evidence:
            _schema_fail(path + ".evidence", "evidence required for classroom claim", evidence)
        steps = [_clean_learning_text(item) for item in _coerce_items(raw.get("steps"), path + ".steps", 8)]
        steps = [item for item in steps if item]
        if not steps:
            _schema_fail(path + ".steps", "actionable steps", steps)
        principle_and_context = _clean_learning_text(raw.get("principle_and_context"))
        why_and_consequences = _clean_learning_text(raw.get("why_and_consequences"))
        boundaries = _clean_learning_text(raw.get("boundaries"))
        connections = _clean_learning_text(raw.get("connections"))
        if not all((principle_and_context, why_and_consequences, boundaries, connections)):
            _schema_fail(path, "non-empty context, why, boundaries, and connections", raw)
        example = raw.get("classroom_example")
        normalized_example = None
        if example is not None:
            if type(example) is not dict or not evidence or source != "课堂明确讲述":
                _schema_fail(path + ".classroom_example", "evidence-grounded classroom object", example)
            normalized_example = {
                name: _clean_learning_text(example.get(name))
                for name in ("original_question", "problem", "improvement")
            }
            if set(example) != {"original_question", "problem", "improvement"}:
                _schema_fail(path + ".classroom_example", "exact original_question/problem/improvement fields", example)
            if not all(normalized_example.values()) or not any(
                normalized_example["original_question"] in cue.text for cue in cues
            ):
                _schema_fail(path + ".classroom_example", "complete example whose original question occurs in transcript", example)
        theme = {
            "topic_id": topic_id,
            "title": title,
            "conclusion": conclusion,
            "principle_and_context": principle_and_context,
            "why_and_consequences": why_and_consequences,
            "steps": steps,
            "classroom_example": normalized_example,
            "boundaries": boundaries,
            "connections": connections,
            "source": source,
            "evidence": evidence,
        }
        themes.append(theme)
        seen.add(topic_id)
    if not themes:
        _schema_fail("themes", "non-empty substantive themes", themes)
    checklist = [_clean_learning_text(item) for item in _coerce_items(value.get("checklist"), "checklist", 16)]
    checklist = _dedupe_strings((item for item in checklist if item), 16)
    questions = []
    for index, raw in enumerate(_coerce_items(value.get("questions"), "questions", 8)):
        if type(raw) is not dict:
            _schema_fail(f"questions[{index}]", "object", raw)
        if set(raw) != {"type", "prompt", "answer", "source"}:
            _schema_fail(f"questions[{index}]", "exact type/prompt/answer/source fields", raw)
        source = _clean_learning_text(raw.get("source"))
        if source not in SOURCE_TYPES:
            _schema_fail(f"questions[{index}].source", "closed source enum", source)
        item_type = _clean_learning_text(raw.get("type"), "application")
        if item_type not in {"recall", "application"}:
            _schema_fail(f"questions[{index}].type", "recall or application", item_type)
        prompt = _clean_learning_text(raw.get("prompt"))
        answer = _clean_learning_text(raw.get("answer"))
        if source == CLASSROOM_SOURCE and prompt and not _question_prompt_in_cues(prompt, cues):
            _schema_fail(f"questions[{index}].source", CLASSROOM_QUESTION_MISMATCH, source)
        if prompt and answer:
            questions.append(dict(type=item_type, prompt=prompt, answer=answer, source=source))
    if not 5 <= len(questions) <= 8:
        _schema_fail("questions", "5-8 answered recall/application questions", questions)
    extensions = [_clean_learning_text(item) for item in _coerce_items(value.get("extensions"), "extensions", 3)]
    extensions = [item for item in extensions if item]
    if not 2 <= len(extensions) <= 3:
        _schema_fail("extensions", "2-3 questions", extensions)
    return dict(course_map=course_map, themes=themes, checklist=checklist, questions=questions, extensions=extensions)


def _fallback_study_guide(summary: Mapping[str, Any], cues: Sequence[Cue]) -> dict[str, Any]:
    """Build a source-only minimum guide; never inject course-domain knowledge."""
    evidence_items = list(summary.get("knowledge_points", ()))
    if not evidence_items:
        evidence_items = [
            dict(
                concept=f"{_time_label(cue.start_ms)} 字幕片段",
                statement=cue.text[:240],
                evidence=[dict(cue_id=cue.cue_id, start_ms=cue.start_ms, end_ms=cue.end_ms, quote=cue.text[:240])],
                source="字幕存疑",
            )
            for cue in cues[:6]
        ]
    themes: list[dict[str, Any]] = []
    seen_themes: set[str] = set()
    for item in evidence_items:
        statement = _clean_learning_text(item.get("statement"))
        concept = _clean_learning_text(item.get("concept"))
        evidence = list(item.get("evidence", ()))
        key = _canonical_learning_key(concept + statement)
        if not concept or not statement or not evidence or key in seen_themes:
            continue
        first = evidence[0]
        themes.append(dict(
            topic_id=_stable_topic_id(item.get("topic_id"), evidence, statement),
            title=concept,
            conclusion=statement,
            principle_and_context=f"仅用于复习 {_time_label(first.get('start_ms'))} 附近的字幕陈述。",
            why_and_consequences="若离开所列时间戳扩写，可能把未核实内容误当成课堂结论。",
            steps=["打开所列时间戳", "逐字核对引文", "确认结论未超出引文", "存疑时保留原字幕"],
            classroom_example=None,
            boundaries="只覆盖所列字幕引文，不推断未出现的定义、案例或领域规则。",
            connections="与其他主题的关系尚未获得足够字幕证据。",
            source=_clean_learning_text(item.get("source"), "课堂明确讲述"),
            evidence=evidence,
        ))
        seen_themes.add(key)
        if len(themes) >= 6:
            break
    course_map = [
        f"{_time_label(theme['evidence'][0].get('start_ms'))} · {theme['title']}：{theme['conclusion']}"
        for theme in themes
    ]
    questions: list[dict[str, str]] = []
    seen_prompts: set[str] = set()
    seen_bases: set[tuple[str, str]] = set()
    for index, theme in enumerate(themes):
        if index % 2:
            prompt = f"根据 {_time_label(theme['evidence'][0].get('start_ms'))} 的字幕，如何应用“{theme['title']}”？"
            item_type = "application"
        else:
            prompt = f"{_time_label(theme['evidence'][0].get('start_ms'))} 的字幕对“{theme['title']}”作了什么陈述？"
            item_type = "recall"
        key = _canonical_learning_key(prompt)
        basis = (str(theme["evidence"][0].get("cue_id")), item_type)
        if key in seen_prompts or basis in seen_bases:
            continue
        seen_prompts.add(key)
        seen_bases.add(basis)
        questions.append(dict(type=item_type, prompt=prompt, answer=theme["conclusion"], source="根据课堂内容归纳"))
    incomplete_reasons = []
    if len(course_map) < 4:
        incomplete_reasons.append("可验证主题不足4个，课程地图未达到完整讲义规模。")
    if len(questions) < 5:
        incomplete_reasons.append("可生成的实质不同练习不足5题，未使用重复题凑数。")
    extensions = [f"“{theme['title']}”与后续字幕主题之间还有哪些待核实关系？" for theme in themes[:3]]
    return dict(
        course_map=course_map,
        themes=themes,
        checklist=[
            f"复核 {_time_label(theme['evidence'][0].get('start_ms'))} 的引文是否支持“{theme['title']}”。"
            for theme in themes
        ],
        questions=questions,
        extensions=extensions,
        incomplete=bool(incomplete_reasons),
        warnings=incomplete_reasons,
    )


def _guide_prompt(summary: Mapping[str, Any], fallback: Mapping[str, Any]) -> str:
    source = {
        "lesson_topic": summary.get("lesson_topic"),
        "knowledge_points": summary.get("knowledge_points", ()),
        "classroom_examples": summary.get("classroom_examples", ()),
        "emphasized_points": summary.get("emphasized_points", ()),
    }
    fallback_themes = fallback.get("themes", ())
    theme_example = None
    if type(fallback_themes) in (list, tuple) and fallback_themes and type(fallback_themes[0]) is dict:
        source_theme = fallback_themes[0]
        theme_example = {
            name: source_theme.get(name)
            for name in (
                "topic_id", "title", "conclusion", "principle_and_context", "why_and_consequences",
                "steps", "classroom_example", "boundaries", "connections", "source",
            )
        }
        theme_example["evidence"] = [
            {"cue_id": row.get("cue_id"), "quote": row.get("quote")}
            for row in source_theme.get("evidence", ())
            if type(row) is dict and type(row.get("cue_id")) is str and type(row.get("quote")) is str
        ]
    fallback_questions = fallback.get("questions", ())
    question_example = (
        {name: fallback_questions[0].get(name) for name in ("type", "prompt", "answer", "source")}
        if type(fallback_questions) in (list, tuple) and fallback_questions and type(fallback_questions[0]) is dict
        else {"type": "recall", "prompt": "问题（须由当前字幕主题支持）", "answer": "基于字幕的简答", "source": "根据课堂内容归纳"}
    )
    guide_example = {
        "course_map": list(fallback.get("course_map", ()))[:1],
        "themes": [theme_example] if theme_example is not None else [],
        "checklist": list(fallback.get("checklist", ()))[:1],
        "questions": [question_example],
        "extensions": list(fallback.get("extensions", ()))[:1],
    }
    return (
        "阶段1：把分块提取结果重组为可学习讲义，不能逐条拼接。只返回一个 JSON 对象，且仅含 course_map、themes、checklist、questions、extensions。"
        "完整字段契约：course_map 是4-6条非空字符串，表示不同的学习路线要点，不要求每个theme恰好对应一行；"
        "可把有证据支持的定义、方法步骤、适用条件或概念对比组织成不同路线要点，但不得拆成重复改写、增加源外知识或为了达到数量编造。"
        "若源材料确实无法支持4条实质不同的要点，保留证据并报告schema不足，不要凑数。themes 是1-12个对象；checklist 是0-16条字符串；"
        "questions 是5-8个对象；extensions 是2-3条非空字符串。数量不足不能用重复项凑数，保留事实边界。"
        "每个 theme 对象含 topic_id/title/conclusion/principle_and_context/why_and_consequences/steps/classroom_example/"
        "boundaries/connections/source/evidence：topic_id与文本字段为非空字符串；steps为1-8条非空字符串；"
        "source只能是：课堂明确讲述、根据课堂内容归纳、自编练习或例子、字幕存疑；evidence为一个或多个"
        "{cue_id,quote}对象，cue_id须来自当前字幕，quote须为当前cue逐字连续引文（通常20-120字，绝不超过240字），"
        "不要输出时间，程序按cue_id确定时间。每个theme均须有证据，不得把无证据推断包装成教学结论。"
        "classroom_example可为null；没有字幕中明确讲述的课堂例子，或找不到逐字出现的原提问时必须为null。"
        "只有source为课堂明确讲述且evidence有效时才可给对象；对象严格包含非空字符串original_question/problem/improvement，"
        "original_question必须逐字出现在某个当前cue中，problem/improvement不得添加字幕无法支持的课堂事实。"
        "自编题、通用例子或根据课堂归纳的问题不得放入classroom_example。"
        "每个question对象严格含非空字符串type/prompt/answer/source；type只能recall或application；source使用上述枚举。"
        "问题prompt与答案answer的来源分别判断：prompt与answer均须有当前主题依据，但答案依据课堂内容不代表题面曾在课堂提出。"
        "复习题默认标为根据课堂内容归纳；改变场景或问法的题目标为自编练习或例子。仅题面逐字出现在当前cue才可标为课堂明确讲述；"
        "该类别下prompt必须是字幕中逐字出现的提问，不得将答案有依据误当成原题有出处。不得省略答案或换用question/text键。"
        "checklist元素须为可核对的字符串；extensions元素须为待探索问题，不得把缺失事实写成结论。"
        "字段示例（确定性草稿中的真实单主题/单题，只示范结构，不代表完整数量；课堂案例无可靠原句时显示null）："
        + json.dumps(guide_example, ensure_ascii=False, separators=(",", ":"))
        + "。合并同义内容并补足why/how，但证据不足时明确保留待核实。教师建议、案例判断和一般原则要区分。"
        "课程地图、检查清单和题目只能围绕当前字幕证据中的主题，不引入其他课程的知识、案例或固定检查清单。"
        "依据证据说明适用条件、后果与操作步骤；证据不足的 why/how 保留待核实，不补写事实。"
        "课堂例子的原句与提问方式必须忠实保留，改变场景或问法的题目须标明自编练习或例子。"
        "删除空洞占位词。输入提取：" + json.dumps(source, ensure_ascii=False, separators=(",", ":"))
        + "\n确定性草稿：" + json.dumps(fallback, ensure_ascii=False, separators=(",", ":"))
    )


def _evidence_in_chunk(rows: Sequence[Mapping[str, Any]], chunk: TranscriptChunk) -> bool:
    cue_ids = {cue.cue_id for cue in chunk.cues}
    return bool(rows) and all(row.get("cue_id") in cue_ids for row in rows)


def _normalize_audit_evidence(value: object, chunk: TranscriptChunk, path: str) -> list[dict[str, Any]]:
    """Bind audit references to current cues, deriving source-cue quotes only by explicit marker."""
    if type(value) is not list or not value or len(value) > 12:
        _schema_fail(path, "non-empty array of at most 12 {cue_id,quote} objects", value)
    cues_by_id = {cue.cue_id: cue for cue in chunk.cues}
    result: list[dict[str, Any]] = []
    seen_source_cues: set[str] = set()
    seen_legacy_cues: set[str] = set()
    seen_legacy_pairs: set[tuple[str, str]] = set()
    for index, row in enumerate(value):
        row_path = f"{path}[{index}]"
        if type(row) is not dict:
            _schema_fail(row_path, "exact legacy {cue_id,quote} or source {cue_id,quote_mode} object", row)
        fields = set(row)
        if fields == {"cue_id", "quote_mode"}:
            if row.get("quote_mode") != "source_cue":
                _schema_fail(row_path + ".quote_mode", "exact source_cue marker", row.get("quote_mode"))
            cue_id = row.get("cue_id")
            source_cue_ref = True
        elif fields == {"cue_id", "quote"}:
            cue_id, quote = row.get("cue_id"), row.get("quote")
            source_cue_ref = False
        else:
            _schema_fail(row_path, "exact legacy {cue_id,quote} or source {cue_id,quote_mode} object", row)
        if type(cue_id) is not str or cue_id not in cues_by_id:
            _schema_fail(row_path + ".cue_id", "cue ID from the current chunk", cue_id)
        cue = cues_by_id[cue_id]
        if source_cue_ref:
            quote = cue.text
            if not quote or len(quote) > MAX_MESSAGE_CHARS:
                _schema_fail(row_path + ".quote_mode", "non-empty complete source cue within the message safety limit", row.get("quote_mode"))
            if cue_id in seen_source_cues or cue_id in seen_legacy_cues:
                _schema_fail(row_path, "unique current cue reference", row)
            seen_source_cues.add(cue_id)
        else:
            if type(quote) is not str or quote != quote.strip():
                _schema_fail(row_path + ".quote", "trimmed literal quote from the paired cue", quote)
            if not quote or len(quote) > 240 or quote not in cue.text:
                expected = "non-empty trimmed literal substring from the paired current cue, at most 240 characters"
                _schema_fail(row_path + ".quote", expected, quote)
            pair = (cue_id, quote)
            if cue_id in seen_source_cues or pair in seen_legacy_pairs:
                _schema_fail(row_path, "unique legacy cue and quote pair; no duplicate source-cue ref", row)
            seen_legacy_cues.add(cue_id)
            seen_legacy_pairs.add(pair)
        result.append(dict(cue_id=cue_id, start_ms=cue.start_ms, end_ms=cue.end_ms, quote=quote))
    return result


def _topic_merge_id(item: Mapping[str, Any]) -> str:
    return _stable_topic_id(item.get("topic_id"), item.get("evidence", ()), str(item.get("conclusion") or ""))


def _audit_issue_supports(
    identity: str,
    issues: Sequence[Mapping[str, Any]],
    topic_id: str | None = None,
) -> bool:
    key = _canonical_learning_key(identity)
    return any(
        issue.get("evidence")
        and (
            (topic_id is not None and issue.get("target_topic_id") == topic_id)
            or (bool(key) and key in _canonical_learning_key(str(issue.get("problem", "")) + str(issue.get("fix", ""))))
        )
        for issue in issues
    )


def _protected_audit_merge(
    current: Mapping[str, Any],
    revised: Mapping[str, Any],
    issues: Sequence[Mapping[str, Any]],
    chunk: TranscriptChunk,
) -> dict[str, Any]:
    """Merge local audit output without permitting unsupported deletion or rewrite."""
    merged = dict(current)
    current_themes = list(current.get("themes", ()))
    revised_themes = {
        _topic_merge_id(item): item for item in revised.get("themes", ())
    }
    themes = []
    known_theme_keys = set()
    for old in current_themes:
        key = _topic_merge_id(old)
        candidate = revised_themes.get(key)
        if candidate is not None and candidate != old and _audit_issue_supports(
            str(old.get("title")), issues, key
        ):
            themes.append(candidate)
        else:
            themes.append(old)
        known_theme_keys.add(key)
    for candidate in revised.get("themes", ()):
        key = _topic_merge_id(candidate)
        if key not in known_theme_keys and _evidence_in_chunk(candidate.get("evidence", ()), chunk):
            themes.append(candidate)
            known_theme_keys.add(key)
    merged["themes"] = themes

    current_questions = list(current.get("questions", ()))
    revised_questions = {
        _canonical_learning_key(item.get("prompt")): item for item in revised.get("questions", ())
    }
    questions = []
    known_question_keys = set()
    for old in current_questions:
        key = _canonical_learning_key(old.get("prompt"))
        candidate = revised_questions.get(key)
        if candidate is not None and candidate != old and _audit_issue_supports(str(old.get("prompt")), issues):
            questions.append(candidate)
        else:
            questions.append(old)
        known_question_keys.add(key)
    for candidate in revised.get("questions", ()):
        key = _canonical_learning_key(candidate.get("prompt"))
        if key not in known_question_keys and _audit_issue_supports(str(candidate.get("prompt")), issues):
            questions.append(candidate)
            known_question_keys.add(key)
    merged["questions"] = questions[:8]

    for field, limit in (("course_map", 6), ("checklist", 16), ("extensions", 3)):
        values = list(current.get(field, ()))
        if issues:
            _merge_normalized_strings(values, revised.get(field, ()), limit)
        merged[field] = values
    if current.get("incomplete"):
        merged["incomplete"] = True
        merged["warnings"] = list(current.get("warnings", ()))
    return merged


def _apply_topic_patches(
    current: Mapping[str, Any], patches: Sequence[Mapping[str, Any]],
    issues: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply evidence-backed local edits while retaining the complete guide."""
    merged = dict(current)
    themes = [dict(item) for item in current.get("themes", ())]
    by_id = {_topic_merge_id(item): index for index, item in enumerate(themes)}
    for patch in patches:
        topic_id = patch["target_topic_id"]
        index = by_id.get(topic_id)
        if index is None or not _audit_issue_supports(str(themes[index].get("title", "")), issues, topic_id):
            continue
        candidate = dict(themes[index])
        candidate.update(patch["changes"])
        themes[index] = candidate
    merged["themes"] = themes
    return merged


def _audit_guide(
    ai_client: Any, guide: dict[str, Any], chunks: Sequence[TranscriptChunk], cues: Sequence[Cue],
    *, should_cancel: Callable[[], bool] | None = None, json_mode: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    issues: list[dict[str, str]] = []
    ledger: list[dict[str, Any]] = []
    current = guide
    failed = False
    for chunk in chunks:
        _check_cancel(should_cancel)
        started = time.monotonic()
        local_topic_bindings: dict[str, str] = {}
        local_topics = []
        for item in current.get("themes", ()):
            current_cue_ids = {cue.cue_id for cue in chunk.cues}
            local_evidence = [row for row in item.get("evidence", ()) if row.get("cue_id") in current_cue_ids]
            if not local_evidence:
                continue
            alias = f"T{len(local_topics) + 1}"
            canonical_id = _topic_merge_id(item)
            local_topic_bindings[alias] = canonical_id
            local_topics.append(
            {"topic_ref": alias, "title": item.get("title"),
             "conclusion": item.get("conclusion"), "evidence": [row for row in item.get("evidence", ())
                                                                  if row.get("cue_id") in current_cue_ids]}
            )
        available_evidence_cues = [
            {"cue_id": cue.cue_id, "text": cue.text}
            for cue in chunk.cues
        ]
        local_topic_ids = list(local_topic_bindings)
        local_canonical_ids = set(local_topic_bindings.values())
        example_cue = next((cue for cue in chunk.cues if len(cue.text) >= 20), chunk.cues[0])
        evidence_example = {"cue_id": example_cue.cue_id, "quote_mode": "source_cue"}
        prompt = (
            "阶段2：仅审校本连续时间段相关主题，并给出局部修订patch。按遗漏、错配、来源混淆、学习效果、冗余检查。"
            "返回 JSON：reviewed_cue_ids（必须逐项覆盖本段全部 cue）；coverage_confirmation 必须是 {confirmed:true,evidence:[{cue_id,quote_mode:'source_cue'}]}，"
            "evidence 至少一条；每条证据只能包含 cue_id 和 quote_mode 两个字段，quote_mode 必须为 source_cue。"
            "cue_id 只能取下方 available_evidence_cues 中的值。程序将从该cue自动填入完整原文和时间；不要复制、改写或输出quote/start_ms/end_ms。"
            "只能选择实际支持该覆盖判断的cue；程序会引用该cue完整原文，长度受现有消息安全上限约束，不截断或改写。"
            "合法来源引用对象示例（只示范结构，cue_id 必须来自本段）："
            + json.dumps(evidence_example, ensure_ascii=False, separators=(",", ":"))
            + "。issues 必须是数组，每项含非空 type/problem/fix 和非空 evidence 数组；每条 evidence 都必须是"
            "同样的 {cue_id,quote_mode:'source_cue'} 对象，且所选cue的真实原文须直接支持该问题及修订。"
            "有效引用标记不代表问题本身成立；若当前cue原文不能支持问题或修订，不得报告该issue，返回issues=[]。"
            "每个issue必须含target_topic_id：若针对当前主题，只能填写下方本块当前相关主题对应的topic_ref短别名；若仅是通用覆盖观察且不对应现有主题，填null。"
            "涉及删除或改写时必须引用本段 cue。theme_patches 必须是数组，每项包含target_topic_id和changes，"
            "target_topic_id也只能填写同一个topic_ref短别名；每个patch必须与同一target_topic_id且带有当前cue evidence的issue一一对应。"
            "changes只可修改title/conclusion/principle_and_context/why_and_consequences/steps/boundaries/connections；"
            "不得修改或删除evidence、source或其他主题。无修改时 issues 与 theme_patches 均返回空数组，但仍须提供 coverage_confirmation。"
            "本段索引与时间范围由程序根据当前chunk确定；不要输出chunk_index或time_range。"
            "即使无需修改也必须用本段证据确认覆盖。不得把自编练习写成课堂案例。仅列出当前块确有引文的主题；不要推测或重造ID。当前相关主题："
            + json.dumps(local_topics, ensure_ascii=False, separators=(",", ":"))
            + "\n本段相关主题合法topic_ref：" + json.dumps(local_topic_ids, ensure_ascii=False, separators=(",", ":"))
            + "\navailable_evidence_cues（本段唯一可引用的cue及原文）："
            + json.dumps(available_evidence_cues, ensure_ascii=False, separators=(",", ":"))
            + "\n本段字幕：\n" + chunk.as_prompt_text()
        )
        coverage_observation: dict[str, list[str]] = dict(reviewed=[], missing=[])
        def validate(value: object) -> dict[str, Any]:
            if type(value) is not dict:
                _schema_fail("audit", "object", value)
            allowed_fields = {
                "chunk_index", "time_range", "reviewed_cue_ids", "coverage_confirmation",
                "issues", "theme_patches", "revised_study_guide",
            }
            if set(value) - allowed_fields:
                _schema_fail("audit", "known audit fields only", value)
            required_fields = {"reviewed_cue_ids", "coverage_confirmation", "issues"}
            if not required_fields.issubset(value) or not ({"theme_patches", "revised_study_guide"} & set(value)):
                _schema_fail("audit", "coverage, issues, and patch or legacy guide fields", value)
            expected_cue_ids = [cue.cue_id for cue in chunk.cues]
            raw_reviewed = value.get("reviewed_cue_ids")
            reviewed_cue_ids = (
                [item for item in raw_reviewed if type(item) is str]
                if type(raw_reviewed) is list else []
            )
            coverage_observation["reviewed"] = list(dict.fromkeys(reviewed_cue_ids))
            coverage_observation["missing"] = [
                cue_id for cue_id in expected_cue_ids if cue_id not in coverage_observation["reviewed"]
            ]
            if (
                len(reviewed_cue_ids) != len(set(reviewed_cue_ids))
                or set(reviewed_cue_ids) != set(expected_cue_ids)
            ):
                _schema_fail("audit.reviewed_cue_ids", "every current chunk cue exactly once", raw_reviewed)
            confirmation = value.get("coverage_confirmation")
            if type(confirmation) is not dict or confirmation.get("confirmed") is not True:
                _schema_fail("audit.coverage_confirmation", "explicit true confirmation", confirmation)
            confirmation_evidence = _normalize_audit_evidence(
                confirmation.get("evidence"), chunk, "audit.coverage_confirmation.evidence"
            )
            if not _evidence_in_chunk(confirmation_evidence, chunk):
                _schema_fail("audit.coverage_confirmation.evidence", "evidence from current chunk", confirmation_evidence)
            raw_issues = value.get("issues")
            if type(raw_issues) is not list or len(raw_issues) > 20:
                _schema_fail("audit.issues", "array of at most 20 issue objects", raw_issues)
            rows = []
            for issue_index, raw in enumerate(raw_issues):
                issue_path = f"audit.issues[{issue_index}]"
                if type(raw) is not dict or not {"type", "target_topic_id", "problem", "evidence", "fix"}.issubset(raw):
                    _schema_fail(issue_path, "issue object with type/target_topic_id/problem/evidence/fix", raw)
                raw_target = raw.get("target_topic_id")
                if raw_target is not None and type(raw_target) is not str:
                    _schema_fail(issue_path + ".target_topic_id", "current topic_ref alias, exact current local ID, or null", raw_target)
                target_ref = _clean_learning_text(raw_target)
                if target_ref in local_topic_bindings:
                    target_topic_id = local_topic_bindings[target_ref]
                elif target_ref in local_canonical_ids:
                    target_topic_id = target_ref
                elif raw_target is not None:
                    _schema_fail(issue_path + ".target_topic_id", "current related topic_ref or exact current local canonical ID", raw_target)
                else:
                    target_topic_id = ""
                issue_evidence_raw = raw.get("evidence")
                if type(issue_evidence_raw) is not list or not issue_evidence_raw:
                    _schema_fail(issue_path + ".evidence", "non-empty evidence array", issue_evidence_raw)
                issue_evidence = _normalize_audit_evidence(issue_evidence_raw, chunk, issue_path + ".evidence")
                if len(issue_evidence) != len(issue_evidence_raw) or not _evidence_in_chunk(issue_evidence, chunk):
                    _schema_fail(issue_path + ".evidence", "all evidence must uniquely match current cues", issue_evidence_raw)
                rows.append({
                    "type": _coerce_text(raw.get("type"), issue_path + ".type", 128),
                    "target_topic_id": target_topic_id,
                    "problem": _coerce_text(raw.get("problem"), issue_path + ".problem", 600),
                    "evidence": issue_evidence,
                    "fix": _coerce_text(raw.get("fix"), issue_path + ".fix", 600),
                })
            if "theme_patches" in value:
                raw_patches = value.get("theme_patches")
                if type(raw_patches) is not list or len(raw_patches) > 12:
                    _schema_fail("audit.theme_patches", "array of at most 12 local patches", raw_patches)
                known_topics = {_topic_merge_id(item) for item in guide.get("themes", ())}
                allowed_changes = {
                    "title", "conclusion", "principle_and_context", "why_and_consequences",
                    "steps", "boundaries", "connections",
                }
                patches = []
                for patch_index, patch in enumerate(raw_patches):
                    if type(patch) is not dict or set(patch) != {"target_topic_id", "changes"}:
                        _schema_fail(f"audit.theme_patches[{patch_index}]", "target_topic_id and changes", patch)
                    target = _clean_learning_text(patch.get("target_topic_id"))
                    changes = patch.get("changes")
                    if target in local_topic_bindings:
                        target = local_topic_bindings[target]
                    elif target not in local_canonical_ids:
                        _schema_fail(f"audit.theme_patches[{patch_index}].target_topic_id", "current related topic_ref or exact current local canonical ID", patch.get("target_topic_id"))
                    if target not in known_topics or type(changes) is not dict or not changes or set(changes) - allowed_changes:
                        _schema_fail(f"audit.theme_patches[{patch_index}].changes", "non-empty allowed local patch", changes)
                    matching_issue = next((item for item in rows if item.get("target_topic_id") == target), None)
                    if matching_issue is None or not matching_issue.get("evidence"):
                        _schema_fail(f"audit.theme_patches[{patch_index}].target_topic_id", "patch backed by a current-cue issue", target)
                    normalized_changes: dict[str, Any] = {}
                    for field, patch_value in changes.items():
                        if field == "steps":
                            if type(patch_value) is not list or not 3 <= len(patch_value) <= 8:
                                _schema_fail(f"audit.theme_patches[{patch_index}].changes.steps", "array of 3 to 8 steps", patch_value)
                            normalized_steps = [_clean_learning_text(item) for item in patch_value]
                            if any(not item for item in normalized_steps):
                                _schema_fail(f"audit.theme_patches[{patch_index}].changes.steps", "non-empty step text", patch_value)
                            normalized_changes[field] = normalized_steps
                        else:
                            normalized_text = _clean_learning_text(patch_value)
                            if not normalized_text:
                                _schema_fail(f"audit.theme_patches[{patch_index}].changes.{field}", "non-empty text", patch_value)
                            normalized_changes[field] = normalized_text
                    patches.append({"target_topic_id": target, "changes": normalized_changes})
                return dict(
                    issues=rows, theme_patches=patches,
                    coverage_evidence=confirmation_evidence,
                    reviewed_cue_ids=reviewed_cue_ids,
                )
            revised = _normalize_study_guide(value.get("revised_study_guide"), cues)
            return dict(
                issues=rows,
                revised_study_guide=revised,
                coverage_evidence=confirmation_evidence,
                reviewed_cue_ids=reviewed_cue_ids,
            )
        try:
            if len(prompt) >= MAX_MESSAGE_CHARS:
                raise ValueError("讲义审校请求超过单次消息安全限制。")
            result = _json_content(
                ai_client, prompt, validate,
                schema_hint="reviewed_cue_ids、coverage_confirmation、issues(target_topic_id) 与 theme_patches",
                should_cancel=should_cancel, json_mode=json_mode,
                repair_context={
                    "current_topics": [
                        {"topic_ref": alias, "title": next((item.get("title") for item in local_topics if item.get("topic_ref") == alias), "")}
                        for alias in local_topic_ids
                    ],
                    "available_evidence_cues": available_evidence_cues,
                    "audit_evidence_wire": "source_cue refs are exact {cue_id,quote_mode:'source_cue'} objects; program derives quote and times from the current chunk cue; never invent or copy quote text",
                },
            )
            if "theme_patches" in result:
                current = _apply_topic_patches(current, result["theme_patches"], result["issues"])
            else:
                current = _protected_audit_merge(
                    current, result["revised_study_guide"], result["issues"], chunk
                )
            issues.extend(result["issues"])
            status = "audited"
            coverage_evidence = result["coverage_evidence"]
            reviewed_cue_ids = result["reviewed_cue_ids"]
            missing_cue_ids: list[str] = []
            diagnostics = {
                "stage": "study_guide_audit",
                "audit_wire_version": AUDIT_WIRE_VERSION,
                "chunk_index": chunk.index,
                "validator": "study_guide_audit",
                "request_chars": len(prompt),
                "output_chars": len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))),
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
                "category": "ok",
            }
            diagnostics.update(_safe_response_details(ai_client))
        except Exception as exc:
            failed = True
            diagnostics = _reduce_failure_diagnostics(exc)
            mismatch = diagnostics.get("schema_mismatch")
            safe_failure = {
                "stage": "study_guide_audit",
                "audit_wire_version": AUDIT_WIRE_VERSION,
                "chunk_index": chunk.index,
                "validator": "study_guide_audit",
                "validator_path": mismatch.get("path") if isinstance(mismatch, Mapping) else diagnostics.get("schema_path"),
                "violation_code": diagnostics.get("category", "validation"),
                "category": diagnostics.get("category", "error"),
                "request_chars": len(prompt),
                "output_chars": diagnostics.get("content_length"),
                "latency_ms": round((time.monotonic() - started) * 1000, 2),
            }
            safe_failure.update(_schema_diagnostic_fields(exc, ai_client))
            if isinstance(mismatch, Mapping):
                safe_failure["validator_expected"] = mismatch.get("expected")
            diagnostics = safe_failure
            reviewed_cue_ids = coverage_observation["reviewed"]
            missing_cue_ids = (
                coverage_observation["missing"]
                if reviewed_cue_ids else [cue.cue_id for cue in chunk.cues]
            )
            status = "partial" if reviewed_cue_ids and missing_cue_ids else "not_audited"
            coverage_evidence = []
        ledger.append(dict(
            chunk_index=chunk.index,
            start_ms=chunk.cues[0].start_ms,
            end_ms=chunk.cues[-1].end_ms,
            status=status,
            reviewed_cue_ids=reviewed_cue_ids,
            missing_cue_ids=missing_cue_ids,
            coverage_evidence=coverage_evidence,
            diagnostics=diagnostics,
        ))
    status = "completed_with_warnings" if failed else "completed"
    if failed:
        issues.append(dict(type="审校失败", problem="至少一个连续时间段未完成模型审校。", evidence="覆盖账本保留了失败时间段。", fix="保留上一版完整讲义并继续审校后续时间段。"))
    return current, dict(status=status, issues=issues, coverage_ledger=ledger)


def _validated_source_cues(corrected: Sequence[Cue], original: Sequence[Cue]) -> list[Cue]:
    """Accept corrected text only, never a fabricated cue, deleted cue or new timing."""
    expected = {cue.cue_id: cue for cue in original}
    by_id: dict[str, Cue] = {}
    for cue in corrected:
        if not isinstance(cue, Cue) or type(cue.text) is not str or type(cue.cue_id) is not str:
            raise ValueError("校正字幕 cue 结构无效。")
        source = expected.get(cue.cue_id)
        if (
            source is None or cue.cue_id in by_id
            or type(cue.start_ms) is not int or type(cue.end_ms) is not int
            or cue.start_ms != source.start_ms or cue.end_ms != source.end_ms
        ):
            raise ValueError("校正字幕必须保留源 cue 标识与时间范围。")
        text = unicodedata.normalize("NFC", " ".join(cue.text.split()))
        if not text:
            raise ValueError("校正字幕不得清空源 cue。")
        by_id[cue.cue_id] = Cue(cue.start_ms, cue.end_ms, text, cue.cue_id)
    if set(by_id) != set(expected):
        raise ValueError("校正字幕必须完整覆盖源 cue。")
    if sum(len(cue.text.encode("utf-8")) for cue in by_id.values()) > 32 * 1024 * 1024:
        raise ValueError("校正字幕内容超过安全限制。")
    return [by_id[cue.cue_id] for cue in original]


class TranscriptPipeline:
    def run(
        self,
        raw_vtt: str,
        ai_client: Any,
        *,
        cached_chunks: Mapping[int, Mapping[str, Any]] | None = None,
        on_chunk: Callable[[int, dict[str, Any]], None] | None = None,
        on_cleaned: Callable[[str], None] | None = None,
        offline_only: bool = False,
        deterministic_only: bool = False,
        chunk_priority: Callable[[TranscriptChunk], float] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        source_cues: Sequence[Cue] | None = None,
        json_mode: bool = False,
    ) -> PipelineResult:
        """Publish chunks as ready; merge by source order despite changing playback priority.

        A corrected source must preserve every normalized source cue's identity and
        timing. Changed text invalidates supplied map caches before re-extraction.
        """
        if type(json_mode) is not bool:
            raise ValueError("json_mode must be boolean")
        ai_client = _PipelineCallTracker(ai_client, json_mode=json_mode) if ai_client is not None else None
        _check_cancel(should_cancel)
        original_cues = normalize_cues(parse_vtt(raw_vtt))
        cues = original_cues if source_cues is None else _validated_source_cues(source_cues, original_cues)
        chunks = chunk_cues(cues)
        by_index: dict[int, dict[str, Any]] = {}
        warnings: list[str] = list()
        pipeline_diagnostics: list[dict[str, Any]] = []
        chunk_warnings: dict[int, list[str]] = {}
        cached = (cached_chunks or {}) if cues == original_cues else {}
        pending = list(chunks)
        while pending:
            _check_cancel(should_cancel)
            chunk = min(pending, key=lambda item: (chunk_priority(item), item.index)) if chunk_priority else pending[0]
            pending.remove(chunk)
            chunk_warnings[chunk.index] = []
            if chunk.index in cached:
                result = validate_map(cached.get(chunk.index), chunk.cues)
            elif offline_only:
                raise ValueError("离线恢复要求全部字幕分块均已存在且验证通过。")
            elif deterministic_only:
                result = _fallback_map(chunk)
            else:
                prompt = (
                    "从以下带 cue_id 与时间的字幕块提取可学习内容。明确区分真正知识点与教师演示、案例、类比；"
                    "不要把课堂例子提升为原则，也不要把 ASR 纠错样本写成练习。无法判定时留空。"
                    "不得改动术语、数字、公式，不得补写字幕外事实；证据 quote 必须逐字来自所引用 cue。"
                + (_json_map_format(chunk.cues) if json_mode else PLAIN_MAP_FORMAT)
                    + "\n字幕块：\n"
                    + chunk.as_prompt_text()
                )
                if len(prompt) >= MAX_MESSAGE_CHARS:
                    raise ValueError("字幕分块超过 AI 单消息限制。")
                map_started = time.monotonic()
                try:
                    result = _map_content(
                        ai_client, prompt, chunk.cues, should_cancel=should_cancel, json_mode=json_mode
                    )
                except TranscriptAIFormatError as exc:
                    result = _fallback_map(chunk)
                    chunk_warnings[chunk.index].append("第 %d 块：%s" % (chunk.index + 1, exc.category))
                    diagnostic = {
                        "stage": "map", "chunk_index": chunk.index,
                        "validator": "map_response", "request_chars": len(prompt),
                        "latency_ms": round((time.monotonic() - map_started) * 1000, 2),
                    }
                    diagnostic.update(_schema_diagnostic_fields(exc, ai_client))
                    pipeline_diagnostics.append(diagnostic)
            _check_cancel(should_cancel)
            if on_chunk:
                on_chunk(chunk.index, result)
            if result.get("partial_warning"):
                chunk_warnings[chunk.index].append("第 %d 块：已使用回退" % (chunk.index + 1))
            by_index[chunk.index] = result
        _check_cancel(should_cancel)
        mapped = [by_index[chunk.index] for chunk in chunks]
        warnings.extend(warning for chunk in chunks for warning in chunk_warnings[chunk.index])
        cleaned_markdown = render_cleaned(mapped)
        if on_cleaned:
            on_cleaned(cleaned_markdown)
        _check_cancel(should_cancel)
        summary = deterministic_summary(mapped)
        if pipeline_diagnostics:
            summary["pipeline_diagnostics"] = pipeline_diagnostics
        fallback_guide = _fallback_study_guide(summary, cues)
        study_guide = fallback_guide
        review: dict[str, Any] = dict(
            status="not_run_offline" if (offline_only or deterministic_only) else "pending",
            issues=list(),
            coverage_ledger=[
                dict(
                    chunk_index=chunk.index,
                    start_ms=chunk.cues[0].start_ms,
                    end_ms=chunk.cues[-1].end_ms,
                    status="not_audited",
                    reviewed_cue_ids=[],
                    missing_cue_ids=[cue.cue_id for cue in chunk.cues],
                    coverage_evidence=[],
                )
                for chunk in chunks
            ],
        )
        guide_generation_diagnostics: dict[str, Any] | None = None
        if not offline_only and not deterministic_only:
            guide_prompt = _guide_prompt(summary, fallback_guide)
            guide_started = time.monotonic()
            try:
                study_guide = _json_content(
                    ai_client,
                    guide_prompt,
                    lambda value: _normalize_study_guide(_guide_root(value), cues),
                    schema_hint=(
                        "study_guide 课程地图、主题、检查清单、5-8题、延伸问题。question的prompt与answer来源分别判断："
                        "答案有课堂依据不代表prompt曾在课堂提出。复习题默认source为根据课堂内容归纳；改变场景或问法的题目标为自编练习或例子；"
                        "只有prompt逐字出现在当前字幕cue中才可标为课堂明确讲述。若校验指出prompt未在source cue出现，只能修正source元数据为真实类别，"
                        "不得更改、删除prompt或answer事实来通过校验。"
                    ),
                    should_cancel=should_cancel,
                    json_mode=json_mode,
                    repair_context={
                        "stage": "study_guide_generation",
                        "question_source_rule": "judge prompt and answer separately; answer evidence does not prove the prompt was asked in class",
                        "allowed_question_sources": ["课堂明确讲述", "根据课堂内容归纳", "自编练习或例子", "字幕存疑"],
                        "classification": "default review questions to 根据课堂内容归纳; changed scenario or wording to 自编练习或例子; 课堂明确讲述 only when the exact prompt occurs in a current source cue",
                        "repair_limits": [
                            "if validator says the prompt is absent from source cues, never retain 课堂明确讲述",
                            "repair source metadata only; do not edit or remove question prompt or answer facts",
                            "do not alter question wording to force a source classification",
                        ],
                    },
                    guide_source_cues=cues,
                )
            except Exception as exc:
                warnings.append("阶段1讲义生成未通过验证，已使用确定性结构化讲义。")
                reduced = _reduce_failure_diagnostics(exc)
                mismatch = reduced.get("schema_mismatch")
                guide_generation_diagnostics = {
                    "stage": "study_guide_generation",
                    "validator": "study_guide_generation",
                    "chunk_index": None,
                    "validator_path": mismatch.get("path") if isinstance(mismatch, Mapping) else reduced.get("schema_path"),
                    "violation_code": reduced.get("category", "validation"),
                    "category": reduced.get("category", "error"),
                    "request_chars": len(guide_prompt),
                    "output_chars": reduced.get("content_length"),
                    "latency_ms": round((time.monotonic() - guide_started) * 1000, 2),
                }
                guide_generation_diagnostics.update(_schema_diagnostic_fields(exc, ai_client))
                if isinstance(mismatch, Mapping):
                    guide_generation_diagnostics["validator_expected"] = mismatch.get("expected")
            try:
                study_guide, review = _audit_guide(
                    ai_client, study_guide, chunks, cues,
                    should_cancel=should_cancel, json_mode=json_mode,
                )
                if review.get("status") == "completed_with_warnings":
                    warnings.append(AUDIT_FALLBACK_WARNING)
            except Exception as exc:
                warnings.append(AUDIT_FALLBACK_WARNING)
                review = dict(
                    status="completed_with_warnings",
                    issues=[dict(type="审校失败", problem="阶段2未能完成全部对照审校。", evidence="已保留各连续分块的时间范围。", fix="保留阶段1讲义，后续可重试审校。")],
                    coverage_ledger=[
                        dict(
                            chunk_index=chunk.index,
                            start_ms=chunk.cues[0].start_ms,
                            end_ms=chunk.cues[-1].end_ms,
                            status="not_audited",
                            reviewed_cue_ids=[],
                            missing_cue_ids=[cue.cue_id for cue in chunk.cues],
                            coverage_evidence=[],
                        )
                        for chunk in chunks
                    ],
                    diagnostics=_reduce_failure_diagnostics(exc),
                )
            if guide_generation_diagnostics is not None:
                review["guide_generation_diagnostics"] = guide_generation_diagnostics
        _check_cancel(should_cancel)
        summary["study_guide"] = study_guide
        summary["review"] = review
        if study_guide.get("incomplete"):
            warnings.extend(str(item) for item in study_guide.get("warnings", ()))
        summary_empty = not summary_has_content(summary) and not study_guide.get("themes")
        if summary_empty:
            warnings.append(SUMMARY_EMPTY_WARNING)
        elif not summary.get("knowledge_points"):
            warnings.append(LEARNING_PRODUCTS_INCOMPLETE_WARNING)
        return PipelineResult(
            cues=list(cue.as_dict() for cue in cues),
            chunks=mapped,
            cleaned_markdown=cleaned_markdown,
            summary=summary,
            summary_markdown=render_summary(summary),
            partial_warnings=tuple(dict.fromkeys(warnings)),
            reduce_diagnostics=None,
            summary_empty=summary_empty,
        )

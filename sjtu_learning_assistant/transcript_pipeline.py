from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

PROMPT_VERSION = "transcript-v8-full-coverage-topic-id"
PIPELINE_VERSION = "transcript-pipeline-v9"
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
                json.dumps(validated, ensure_ascii=False, separators=(",", ":"))
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


def _json_content(
    ai_client: Any,
    prompt: str,
    validator: Callable[[object], dict[str, Any]],
    *,
    schema_hint: str,
) -> dict[str, Any]:
    system = "只依据字幕证据处理内容，不补写外部事实。只返回一个严格 JSON 对象。"
    if len(system) + len(prompt) >= MAX_MESSAGE_CHARS:
        raise ValueError("AI 请求超过单次消息安全限制。")
    response = ai_client.chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        max_tokens=4096,
        temperature=0,
        system_prompt=False,
    )
    content = response.get("content") if isinstance(response, Mapping) else None
    try:
        return _validated_json(content, validator, response)
    except TranscriptAIFormatError as first_error:
        if first_error.category in {"empty", "truncated"}:
            raise
        fragment = _repair_fragment(content)
        if not fragment:
            raise
        repair_prompt = (
            "只修复下列 JSON 片段的语法和字段结构，不添加、删除或改写事实。限制："
            + schema_hint
            + "\n片段：\n"
            + fragment
        )
        if len(repair_prompt) >= MAX_MESSAGE_CHARS:
            raise first_error
        repair = ai_client.chat_completion(
            [
                {
                    "role": "system",
                    "content": "只修复 JSON，不创造事实。只返回一个严格 JSON 对象。",
                },
                {"role": "user", "content": repair_prompt},
            ],
            max_tokens=4096,
            temperature=0,
            system_prompt=False,
        )
        repair_content = repair.get("content") if isinstance(repair, Mapping) else None
        return _validated_json(repair_content, validator, repair)


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
            if seconds >= 0:
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
        text = _coerce_text(row, path + ".text", 600)
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


def _map_content(ai_client, prompt, cues):
    system = (
        "只依据字幕证据提取内容，不补写外部事实。知识点仅限定义、原则、方法、事实、公式和结论；"
        "教师演示、案例和类比必须单列为课堂例子并关联知识点。无法判定时宁缺毋滥并保留逐字证据。"
        "练习面向学生回忆或应用，不得把 ASR 纠错样本伪装成学习训练。严格使用用户指定的固定标签纯文本格式。"
    )
    if len(system) + len(prompt) >= MAX_MESSAGE_CHARS:
        raise ValueError("AI 请求超过单次消息安全限制。")
    response = ai_client.chat_completion(
        list((dict(role="system", content=system), dict(role="user", content=prompt))),
        max_tokens=3072,
        temperature=0,
        system_prompt=False,
    )
    content = response.get("content") if isinstance(response, Mapping) else None
    return _validated_map_response(content, response, cues)


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
            lines.append(
                "- **%s**：%s；证据：%s；修改：%s"
                % tuple(_markdown_text(item.get(key, "")) for key in ("type", "problem", "evidence", "fix"))
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
        title = _clean_learning_text(raw.get("title"))
        conclusion = _clean_learning_text(raw.get("conclusion"))
        if not title or not conclusion:
            continue
        source = _clean_learning_text(raw.get("source"))
        if source not in SOURCE_TYPES:
            _schema_fail(path + ".source", "closed source enum", source)
        evidence = _normalize_guide_evidence(raw.get("evidence"), cues, path + ".evidence")
        topic_id = _stable_topic_id(raw.get("topic_id"), evidence, conclusion)
        if topic_id in seen:
            continue
        if source == "课堂明确讲述" and not evidence:
            _schema_fail(path + ".evidence", "evidence required for classroom claim", evidence)
        steps = [_clean_learning_text(item) for item in _coerce_items(raw.get("steps"), path + ".steps", 8)]
        steps = [item for item in steps if item]
        if not steps:
            _schema_fail(path + ".steps", "actionable steps", steps)
        example = raw.get("classroom_example")
        normalized_example = None
        if example is not None:
            if type(example) is not dict or not evidence or source != "课堂明确讲述":
                _schema_fail(path + ".classroom_example", "evidence-grounded classroom object", example)
            normalized_example = {
                name: _clean_learning_text(example.get(name))
                for name in ("original_question", "problem", "improvement")
            }
            if not all(normalized_example.values()) or not any(
                normalized_example["original_question"] in cue.text for cue in cues
            ):
                _schema_fail(path + ".classroom_example", "complete example whose original question occurs in transcript", example)
        theme = {
            "topic_id": topic_id,
            "title": title,
            "conclusion": conclusion,
            "principle_and_context": _clean_learning_text(raw.get("principle_and_context"), "结合字幕证据判断适用条件。"),
            "why_and_consequences": _clean_learning_text(raw.get("why_and_consequences"), "忽略该判断会降低回答的可解释性和可用性。"),
            "steps": steps,
            "classroom_example": normalized_example,
            "boundaries": _clean_learning_text(raw.get("boundaries"), "超出字幕证据的内容不作课堂结论。"),
            "connections": _clean_learning_text(raw.get("connections"), "先明确目标，再检查表述并回到研究问题。"),
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
        source = _clean_learning_text(raw.get("source"))
        if source not in SOURCE_TYPES:
            _schema_fail(f"questions[{index}].source", "closed source enum", source)
        prompt = _clean_learning_text(raw.get("prompt"))
        answer = _clean_learning_text(raw.get("answer"))
        if "多久吃一次" in prompt and source != "自编练习或例子":
            _schema_fail(f"questions[{index}].source", "fast-food frequency prompt must be self-authored", source)
        if prompt and answer:
            questions.append(dict(type=_clean_learning_text(raw.get("type"), "application"), prompt=prompt, answer=answer, source=source))
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
    return (
        "阶段1：把分块提取结果重组为可学习讲义，不能逐条拼接。只返回 study_guide JSON 对象。"
        "course_map 4-6行；themes 每项含稳定 topic_id（后续改标题仍保持不变）、title/conclusion/principle_and_context/why_and_consequences/steps/"
        "classroom_example/boundaries/connections/source/evidence；checklist；questions 5-8题且答案内置但展示时后置；extensions 2-3题。"
        "来源只能是：课堂明确讲述、根据课堂内容归纳、自编练习或例子、字幕存疑。课堂案例必须有逐字证据；"
        "自编题不得放入 classroom_example。教师建议、案例判断和一般原则要区分。合并同义内容并补足 why/how。"
        "重点检查问卷长度、现有量表、编码一致、解释目的、通俗语言、漏斗排序、发布前试填；选项重叠必须写边界检查、后果、改写步骤。"
        "若课堂证据含23:34‘为什么现在不吃某快餐’，保留原问法；‘多久吃一次’只能标自编练习，不得冒充课堂案例。"
        "删除空洞占位词。输入提取：" + json.dumps(source, ensure_ascii=False, separators=(",", ":"))
        + "\n确定性草稿：" + json.dumps(fallback, ensure_ascii=False, separators=(",", ":"))
    )


def _evidence_in_chunk(rows: Sequence[Mapping[str, Any]], chunk: TranscriptChunk) -> bool:
    cue_ids = {cue.cue_id for cue in chunk.cues}
    return bool(rows) and all(row.get("cue_id") in cue_ids for row in rows)


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


def _audit_guide(ai_client: Any, guide: dict[str, Any], chunks: Sequence[TranscriptChunk], cues: Sequence[Cue]) -> tuple[dict[str, Any], dict[str, Any]]:
    issues: list[dict[str, str]] = []
    ledger: list[dict[str, Any]] = []
    current = guide
    failed = False
    for chunk in chunks:
        prompt = (
            "阶段2：对照这一连续时间段字幕审校完整讲义。按遗漏、错配、来源混淆、学习效果、冗余检查。"
            "返回 JSON：chunk_index；time_range（start_ms/end_ms，必须等于本段）；reviewed_cue_ids"
            "（必须逐项覆盖本段全部 cue）；coverage_confirmation（confirmed=true，evidence 至少一条且只能引用本段 cue）；"
            "issues（每项 type/problem/evidence/fix，可用 target_topic_id 指向标题修订主题，"
            "涉及删除或改写时 evidence 必须引用本段 cue）；revised_study_guide（修订后的完整讲义）。"
            "即使无需修改也必须用本段证据确认覆盖。不得把自编练习写成课堂案例。当前讲义："
            + json.dumps(current, ensure_ascii=False, separators=(",", ":"))
            + "\n本段字幕：\n" + chunk.as_prompt_text()
        )
        coverage_observation: dict[str, list[str]] = dict(reviewed=[], missing=[])
        def validate(value: object) -> dict[str, Any]:
            if type(value) is not dict:
                _schema_fail("audit", "object", value)
            if value.get("chunk_index") != chunk.index:
                _schema_fail("audit.chunk_index", "current chunk index", value.get("chunk_index"))
            time_range = value.get("time_range")
            expected_range = dict(start_ms=chunk.cues[0].start_ms, end_ms=chunk.cues[-1].end_ms)
            if type(time_range) is not dict or time_range != expected_range:
                _schema_fail("audit.time_range", "exact current chunk range", time_range)
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
            confirmation_evidence = _normalize_evidence(
                confirmation.get("evidence"), chunk.cues, "audit.coverage_confirmation.evidence"
            )
            if not _evidence_in_chunk(confirmation_evidence, chunk):
                _schema_fail("audit.coverage_confirmation.evidence", "evidence from current chunk", confirmation_evidence)
            revised = _normalize_study_guide(value.get("revised_study_guide"), cues)
            rows = []
            for raw in _coerce_items(value.get("issues"), "issues", 20):
                if type(raw) is not dict:
                    continue
                issue_evidence = _normalize_evidence(raw.get("evidence"), chunk.cues, "audit.issues.evidence")
                if not _evidence_in_chunk(issue_evidence, chunk):
                    continue
                rows.append({
                    "type": _clean_learning_text(raw.get("type")),
                    "target_topic_id": _clean_learning_text(raw.get("target_topic_id")),
                    "problem": _clean_learning_text(raw.get("problem")),
                    "evidence": issue_evidence,
                    "fix": _clean_learning_text(raw.get("fix")),
                })
            return dict(
                issues=rows,
                revised_study_guide=revised,
                coverage_evidence=confirmation_evidence,
                reviewed_cue_ids=reviewed_cue_ids,
            )
        try:
            if len(prompt) >= MAX_MESSAGE_CHARS:
                raise ValueError("讲义审校请求超过单次消息安全限制。")
            result = _json_content(ai_client, prompt, validate, schema_hint="chunk_index、time_range、reviewed_cue_ids、coverage_confirmation、issues 与 revised_study_guide")
            current = _protected_audit_merge(
                current, result["revised_study_guide"], result["issues"], chunk
            )
            issues.extend(result["issues"])
            status = "audited"
            coverage_evidence = result["coverage_evidence"]
            reviewed_cue_ids = result["reviewed_cue_ids"]
            missing_cue_ids: list[str] = []
        except Exception:
            failed = True
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
        ))
    status = "completed_with_warnings" if failed else "completed"
    if failed:
        issues.append(dict(type="审校失败", problem="至少一个连续时间段未完成模型审校。", evidence="覆盖账本保留了失败时间段。", fix="保留上一版完整讲义并继续审校后续时间段。"))
    return current, dict(status=status, issues=issues, coverage_ledger=ledger)


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
    ) -> PipelineResult:
        cues = normalize_cues(parse_vtt(raw_vtt))
        chunks = chunk_cues(cues)
        mapped: list[dict[str, Any]] = list()
        warnings: list[str] = list()
        cached = cached_chunks or dict()
        for chunk in chunks:
            if chunk.index in cached:
                result = validate_map(cached.get(chunk.index), chunk.cues)
            elif offline_only:
                raise ValueError("离线恢复要求全部字幕分块均已存在且验证通过。")
            elif deterministic_only:
                result = _fallback_map(chunk)
                if on_chunk:
                    on_chunk(chunk.index, result)
            else:
                prompt = (
                    "从以下带 cue_id 与时间的字幕块提取可学习内容。明确区分真正知识点与教师演示、案例、类比；"
                    "不要把课堂例子提升为原则，也不要把 ASR 纠错样本写成练习。无法判定时留空。"
                    "不得改动术语、数字、公式，不得补写字幕外事实；证据 quote 必须逐字来自所引用 cue。"
                    + PLAIN_MAP_FORMAT
                    + "\n字幕块：\n"
                    + chunk.as_prompt_text()
                )
                if len(prompt) >= MAX_MESSAGE_CHARS:
                    raise ValueError("字幕分块超过 AI 单消息限制。")
                try:
                    result = _map_content(ai_client, prompt, chunk.cues)
                except TranscriptAIFormatError as exc:
                    result = _fallback_map(chunk)
                    warnings.append("第 %d 块：%s" % (chunk.index + 1, exc.category))
                if on_chunk:
                    on_chunk(chunk.index, result)
            if result.get("partial_warning"):
                warnings.append("第 %d 块：已使用回退" % (chunk.index + 1))
            mapped.append(result)
        cleaned_markdown = render_cleaned(mapped)
        if on_cleaned:
            on_cleaned(cleaned_markdown)
        summary = deterministic_summary(mapped)
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
        if not offline_only and not deterministic_only:
            try:
                study_guide = _json_content(
                    ai_client,
                    _guide_prompt(summary, fallback_guide),
                    lambda value: _normalize_study_guide(value.get("study_guide") if type(value) is dict and "study_guide" in value else value, cues),
                    schema_hint="study_guide 课程地图、主题、检查清单、5-8题、延伸问题",
                )
            except Exception:
                warnings.append("阶段1讲义生成未通过验证，已使用确定性结构化讲义。")
            try:
                study_guide, review = _audit_guide(ai_client, study_guide, chunks, cues)
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

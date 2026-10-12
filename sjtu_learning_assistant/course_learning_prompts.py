"""Explicit semantic output contracts; decoding never repairs model content."""

from __future__ import annotations

import json
from collections.abc import Mapping

from .course_learning_schemas import SchemaValidationError


def _object(properties: dict[str, object]) -> dict[str, object]:
    return dict(type="object", properties=properties, required=list(properties), additionalProperties=False)


def _array(items: dict[str, object], *, nonempty: bool = False) -> dict[str, object]:
    return dict(type="array", items=items, minItems=1 if nonempty else 0)


_TEXT = dict(type="string", minLength=1, maxLength=100000)
_SHORT_TEXT = dict(type="string", minLength=1, maxLength=4096)
_LABEL = dict(type="string", minLength=1, maxLength=128)
_SOURCE = dict(type="string", minLength=1, maxLength=256)
_STRINGS = _array(_SHORT_TEXT)
_CUES = _array(_SHORT_TEXT, nonempty=True)
_CONFIDENCE = dict(type="number", minimum=0, maximum=1)
_OFFSET = dict(type="integer", minimum=0)
_TERM = _object(dict(original=_TEXT, canonical=_TEXT, category=_LABEL,
                     confidence=_CONFIDENCE, source=_SOURCE, cue_ids=_CUES, evidence=_STRINGS))
_CHANGE = _object(dict(original=_TEXT, corrected=_TEXT, type=_LABEL,
                       confidence=_CONFIDENCE, reason=_TEXT, start=_OFFSET, end=_OFFSET,
                       cue_ids=_CUES, evidence=_STRINGS))
_UNCERTAIN = _object(dict(text=_TEXT, candidates=_array(_TERM), confidence=_CONFIDENCE,
                         start=_OFFSET, end=_OFFSET, cue_ids=_CUES))
_MEMORY_ROW = _object(dict(
    key=_TEXT, value={"anyOf": [{"type": name} for name in ("string", "object", "array", "number", "boolean")]},
    source=dict(type="string", enum=["single_ai"]), confidence=dict(type="number", minimum=0, maximum=0.64),
    verification=dict(type="string", enum=["UNVERIFIED"]), evidence=_array(_TEXT, nonempty=True),
))
ROLE_SCHEMAS = {
    "Terminology": _object(dict(terms=_array(_TERM))),
    "Correction": _object(dict(chunk_id=_LABEL, corrected_text=_TEXT, changes=_array(_CHANGE),
                               uncertain=_array(_UNCERTAIN), confidence=_CONFIDENCE)),
    "Critic": _object(dict(decision=dict(type="string", enum=["PASS", "REVISE", "UNCERTAIN"]),
                           confidence=_CONFIDENCE, issues=_STRINGS, revision_instructions=_STRINGS)),
    "Memory": _object({name: _array(_MEMORY_ROW) for name in
                       ("add_terms", "update_terms", "new_error_patterns", "new_concepts", "new_relationships")}),
}
EMPTY_TEMPLATES = {
    "Terminology": dict(terms=[]),
    "Correction": dict(chunk_id="<逐字复制输入chunk_id>", corrected_text="<逐字复制输入current_text>",
                        changes=[], uncertain=[], confidence=0.0),
    "Critic": dict(decision="UNCERTAIN", confidence=0.0, issues=[], revision_instructions=[]),
    "Memory": {name: [] for name in ROLE_SCHEMAS["Memory"]["properties"]},
}
ROLE_RULES = {
    "Terminology": "每项必须包含全部7个字段；cue_ids只引用本块输入ID，evidence逐字引用输入。最多10项；无可靠候选返回空terms。",
    "Correction": "无修改时复制chunk_id和current_text，changes/uncertain为空；模板占位符必须换成输入值。"
                  "start/end按Python Unicode字符计数，end不包含且end-start等于original/text长度；"
                  "original必须等于current_text[start:end]。corrected_text必须恰好等于声明changes的结果。"
                  "不能猜偏移或补写事实；保留不确定性。最多10个changes，避免重复整段证据。",
    "Critic": "PASS必须issues=[]且revision_instructions=[]；REVISE必须给非空具体revision_instructions；"
              "无法证实返回UNCERTAIN。decision是枚举字符串，不是单独裸词。",
    "Memory": "所有5个数组都必须出现。行必须包含全部6个字段，source只能single_ai，verification只能UNVERIFIED，"
              "confidence不超过0.64；evidence非空且逐字有据。缺证据返回空数组，不捏造更新。value不可为null。",
}


def role_output_contract(role: str) -> str:
    if role not in ROLE_SCHEMAS:
        raise ValueError("Unknown semantic role")
    return (
        "\n输出契约：只返回一个JSON对象；所有required字段必须出现，additionalProperties=false。"
        "禁止代码围栏、前缀说明、NaN/Infinity、重复键、未知字段；不可补字段或猜值。\nJSON Schema："
        + json.dumps(ROLE_SCHEMAS[role], ensure_ascii=False, separators=(",", ":"))
        + "\n合法保守模板：" + json.dumps(EMPTY_TEMPLATES[role], ensure_ascii=False, separators=(",", ":"))
        + "\n约束：" + ROLE_RULES[role]
    )


def decode_semantic_json(value: object) -> object:
    """Decode exactly one JSON object, reject duplicates/constants, preserve types."""
    if type(value) is not str:
        return value

    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, item in pairs:
            if key in result:
                raise SchemaValidationError("$: duplicate JSON key")
            result[key] = item
        return result

    def reject_constant(value: str) -> None:
        raise SchemaValidationError("$: invalid JSON constant")

    try:
        return json.loads(value, object_pairs_hook=pairs_hook, parse_constant=reject_constant)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise SchemaValidationError("$: invalid JSON") from exc


DIAGNOSTIC_CATEGORIES = frozenset({
    "ok", "schema", "truncated", "timeout", "network", "error", "authentication",
    "endpoint_or_model", "request_parameters", "rate_limit_or_quota", "upstream_service",
    "http_error", "response_format", "client_error", "validation", "unsupported_tools",
})
DIAGNOSTIC_PATHS = frozenset({"$"} | {
    "$." + name for name in (
        "terms", "original", "canonical", "category", "confidence", "source", "cue_ids", "evidence",
        "chunk_id", "corrected_text", "changes", "uncertain", "corrected", "type", "reason",
        "start", "end", "text", "candidates", "decision", "issues", "revision_instructions",
        "add_terms", "update_terms", "new_error_patterns", "new_concepts", "new_relationships",
    )
})


def semantic_diagnostics(response: object, exc: Exception | None = None) -> dict[str, object]:
    """Emit structural metrics only; never echo model text or exception messages."""
    response = response if isinstance(response, Mapping) else {}
    content = response.get("content")
    metadata = response.get("response_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    finish = metadata.get("finish_reason")
    finish = finish if finish in {"stop", "length", "tool_calls", "content_filter"} else None
    category = "ok"
    path = None
    if exc is not None:
        if isinstance(exc, (SchemaValidationError, ValueError, TypeError)):
            category = "truncated" if finish == "length" else "schema"
            prefix = str(exc).split(":", 1)[0]
            path = prefix if prefix in DIAGNOSTIC_PATHS else "$"
        elif isinstance(exc, (TimeoutError,)):
            category = "timeout"
        elif isinstance(exc, (ConnectionError, OSError)):
            category = "network"
        else:
            candidate = getattr(exc, "category", None)
            category = candidate if candidate in DIAGNOSTIC_CATEGORIES else "error"
    code = getattr(exc, "status_code", None) if exc is not None else None
    return dict(category=category, schema_path=path,
                content_length=len(content) if type(content) is str else None,
                finish_reason=finish, status_code=code if type(code) is int and 100 <= code <= 599 else None)

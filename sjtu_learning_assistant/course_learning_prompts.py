"""Explicit semantic output contracts; decoding never repairs model content."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Sequence

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
_TERM = _object(dict(original=_TEXT, canonical=_TEXT, category=_LABEL,
                     confidence=_CONFIDENCE, source=_SOURCE, cue_ids=_CUES, evidence=_STRINGS))
_CORRECTION_PROPOSAL = _object(dict(
    cue_id=_SHORT_TEXT, original=_TEXT, replacement=_TEXT, type=_LABEL,
    confidence=_CONFIDENCE, reason=_TEXT, evidence=_STRINGS,
))
_UNCERTAIN_PROPOSAL = _object(dict(
    cue_id=_SHORT_TEXT, text=_TEXT, candidates=_array(_TERM), confidence=_CONFIDENCE,
))
_CORRECTION_WIRE = _object(dict(
    chunk_id=_LABEL, corrected_text=_TEXT, changes=_array(_CORRECTION_PROPOSAL),
    uncertain=_array(_UNCERTAIN_PROPOSAL), confidence=_CONFIDENCE,
))
_CORRECTION_WIRE["required"] = ["changes", "uncertain", "confidence"]
CRITIC_REVIEW_SCOPE = (
    "审核范围只限本提案相对于提供的current_text、current_cues及课程证据是否有据、必要且最小；"
    "这些字幕文本是本次可用来源，本任务不核对音频，也不判定ASR是否忠实于音频。"
    "无修改且未观察到给定文本中的矛盾或未解决问题时可以PASS；这仅表示文本提案审查通过，不代表音频核验。"
    "提案超出原句锚定、缺少证据或触及数字、否定词、人名、公式保护项而不能证实，应REVISE或UNCERTAIN；"
    "无法从给定文本材料判断提案时用UNCERTAIN。confidence独立反映本次证据，不把0当自动拒绝，也不伪造置信度。"
)
_MEMORY_ROW = _object(dict(
    key=_TEXT, value={"anyOf": [{"type": name} for name in ("string", "object", "array", "number", "boolean")]},
    source=dict(type="string", enum=["single_ai"]), confidence=dict(type="number", minimum=0, maximum=0.64),
    verification=dict(type="string", enum=["UNVERIFIED"]), evidence=_array(_TEXT, nonempty=True),
))
ROLE_SCHEMAS = {
    "Terminology": _object(dict(terms=_array(_TERM))),
    # This is the model-facing wire contract. CorrectionResult remains a
    # separate strict internal/persistence schema with computed offsets.
    "Correction": _CORRECTION_WIRE,
    "Critic": _object(dict(decision=dict(type="string", enum=["PASS", "REVISE", "UNCERTAIN"]),
                           confidence=_CONFIDENCE, issues=_STRINGS, revision_instructions=_STRINGS)),
    "Memory": _object({name: _array(_MEMORY_ROW) for name in
                       ("add_terms", "update_terms", "new_error_patterns", "new_concepts", "new_relationships")}),
}
EMPTY_TEMPLATES = {
    "Terminology": dict(terms=[]),
    "Correction": dict(changes=[], uncertain=[], confidence=0.0),
    "Critic": dict(decision="UNCERTAIN", confidence=0.0, issues=[], revision_instructions=[]),
    "Memory": {name: [] for name in ROLE_SCHEMAS["Memory"]["properties"]},
}
ROLE_RULES = {
    "Terminology": "每项必须包含全部7个字段；cue_ids只引用本块输入ID，evidence逐字引用输入。最多10项；无可靠候选返回空terms。",
    "Correction": "无修改时changes/uncertain为空。每个change输出cue_id、逐字original、replacement、type、confidence、reason、evidence；"
                  "这是模型侧cue锚定提案，不是内部CorrectionResult；不要输出corrected_text或字符offset。"
                  "无需回显chunk_id；如为兼容旧客户端而回显，必须逐字一致。程序会在指定cue中唯一匹配原文、计算Unicode偏移并生成最终corrected_text。"
                  "若额外提供corrected_text，程序会与按锚定修改计算的全文逐字比较，不一致即拒绝。"
                  "uncertain项使用cue_id、逐字text、candidates和confidence，不得输出start/end/cue_ids；"
                  "change只能位于current_cues中对应cue；cue或原文匹配不唯一时返回uncertain，不猜位置。"
                  "无可核验证据或术语候选时，不自行改写或清理口语，默认保留原文；确实无法判断时记录uncertain。"
                  "不得改数字、否定词、人名、公式，除非强证据支持。最多10个changes；保留不确定性。",
    "Critic": "PASS必须issues=[]且revision_instructions=[]；REVISE必须给非空具体revision_instructions；"
              "无法证实返回UNCERTAIN。decision是枚举字符串，不是单独裸词。"
              + CRITIC_REVIEW_SCOPE,
    "Memory": "所有5个数组都必须出现。行必须包含全部6个字段，source只能single_ai，verification只能UNVERIFIED，"
              "confidence不超过0.64；evidence非空且逐字有据。缺证据返回空数组，不捏造更新。value不可为null。",
}


def role_output_contract(
    role: str, *, cue_ids: Sequence[str] | None = None,
    chunk_id: str | None = None, current_text: str | None = None,
) -> str:
    if role not in ROLE_SCHEMAS:
        raise ValueError("Unknown semantic role")
    schema = json.loads(json.dumps(ROLE_SCHEMAS[role]))
    template = dict(EMPTY_TEMPLATES[role])

    def bind_cues(node: object) -> None:
        if type(node) is dict:
            properties = node.get("properties", {})
            if cue_ids is not None and "cue_ids" in properties:
                properties["cue_ids"]["items"]["enum"] = list(cue_ids)
            for child in node.values():
                bind_cues(child)
        elif type(node) is list:
            for child in node:
                bind_cues(child)

    bind_cues(schema)
    if role == "Correction":
        # Chunk identity is established by the program and current cue binding.
        # The model may echo the id for backward compatibility, but need not
        # reproduce a long opaque hash in a no-op response.
        schema["required"] = ["changes", "uncertain", "confidence"]
        if cue_ids is not None:
            schema["properties"]["changes"]["items"]["properties"]["cue_id"]["enum"] = list(cue_ids)
            schema["properties"]["uncertain"]["items"]["properties"]["cue_id"]["enum"] = list(cue_ids)
        if chunk_id is not None:
            schema["properties"]["chunk_id"]["const"] = chunk_id
        else:
            schema["properties"].pop("chunk_id", None)
        # A hybrid model may include corrected_text for compatibility. The
        # program recomputes it from anchored changes and requires equality.
        if chunk_id is None:
            schema["properties"].pop("chunk_id", None)
    result = (
        "\n输出契约：只返回一个JSON对象；所有required字段必须出现，additionalProperties=false。"
        "禁止代码围栏、前缀说明、NaN/Infinity、重复键、未知字段；不可补字段或猜值。\nJSON Schema："
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        + "\n合法保守模板：" + json.dumps(template, ensure_ascii=False, separators=(",", ":"))
        + "\n约束：" + ROLE_RULES[role]
    )
    if cue_ids is not None:
        result += "所有层级的cue_ids只允许本块JSON Schema的enum值，禁止新建、改写或跨块借用ID。"
    if role == "Critic":
        result += ('\n仅在实际审查通过且没有未解决问题时使用PASS完整结构：'
                   '{"decision":"PASS","confidence":0.0,"issues":[],"revision_instructions":[]}。'
                   '模板中的confidence=0.0仅用于展示字段类型，不是推荐值或PASS默认值；必须按实际证据判断。'
                   '若无法从已提供文本材料确认提案的依据、必要性或最小性，则UNCERTAIN；'
                   '不得为通过而伪造置信度或删除问题。')
    return result


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
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
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

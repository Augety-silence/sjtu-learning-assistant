"""Strict, dependency-free schemas for the course learning pipeline.

The objects in this module form persistence and agent boundaries.  They do not
coerce values: accepting ``1`` as ``True`` or a string as a number would make
bad model output look valid.  ``from_dict`` therefore accepts JSON-shaped
objects only and rejects missing as well as additional fields.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, fields
from enum import Enum
from typing import Any, ClassVar, Mapping, TypeVar


class SchemaValidationError(ValueError):
    """Raised when a course-learning payload does not match its schema."""


T = TypeVar("T", bound="StrictSchema")


def _fail(path: str, message: str) -> None:
    raise SchemaValidationError(f"{path}: {message}")


def _mapping(value: object, path: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        _fail(path, "expected object")
    return value


def _strict_keys(value: Mapping[str, Any], expected: set[str], path: str = "$") -> None:
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        _fail(path, "missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        _fail(path, "unknown fields: " + ", ".join(sorted(unknown)))


def _string(value: object, path: str, *, empty: bool = False, limit: int = 100_000) -> str:
    if type(value) is not str:
        _fail(path, "expected string")
    if len(value) > limit:
        _fail(path, f"string exceeds {limit} characters")
    if not empty and not value.strip():
        _fail(path, "must not be empty")
    return value


def _optional_string(value: object, path: str, *, limit: int = 4096) -> str | None:
    if value is None:
        return None
    return _string(value, path, limit=limit)


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if type(value) is not int:
        _fail(path, "expected integer")
    if value < minimum:
        _fail(path, f"must be >= {minimum}")
    return value


def _number(value: object, path: str, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        _fail(path, "expected finite number")
    result = float(value)
    if not minimum <= result <= maximum:
        _fail(path, f"must be in [{minimum}, {maximum}]")
    return result


def _boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        _fail(path, "expected boolean")
    return value


def _string_tuple(
    value: object,
    path: str,
    *,
    allow_empty: bool = True,
    item_limit: int = 4096,
) -> tuple[str, ...]:
    if type(value) not in (list, tuple):
        _fail(path, "expected array")
    result = tuple(_string(item, f"{path}[{index}]", limit=item_limit) for index, item in enumerate(value))
    if not allow_empty and not result:
        _fail(path, "must not be empty")
    return result


def _object_tuple(value: object, path: str) -> tuple[dict[str, Any], ...]:
    if type(value) not in (list, tuple):
        _fail(path, "expected array")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        row = _mapping(item, f"{path}[{index}]")
        _validate_json_value(row, f"{path}[{index}]")
        result.append(dict(row))
    return tuple(result)


def _validate_json_value(value: object, path: str) -> None:
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            _fail(path, "non-finite number is not valid JSON")
        return
    if type(value) is list:
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                _fail(path, "object keys must be strings")
            _validate_json_value(item, f"{path}.{key}")
        return
    _fail(path, "expected JSON-compatible value")


def _to_plain(value: object) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, StrictSchema):
        return {field.name: _to_plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_to_plain(item) for item in value]
    if type(value) is dict:
        return {key: _to_plain(item) for key, item in value.items()}
    return value


def stable_json_dumps(value: object) -> str:
    """Serialize a schema or JSON-shaped object deterministically."""

    plain = _to_plain(value)
    _validate_json_value(plain, "$")
    return json.dumps(plain, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class StrictSchema:
    """Small common API shared by all strict dataclass schemas."""

    _field_names: ClassVar[frozenset[str]]

    def validate(self: T) -> T:
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return _to_plain(self)

    def to_json(self) -> str:
        self.validate()
        return stable_json_dumps(self)

    @classmethod
    def from_json(cls: type[T], value: str) -> T:
        """Parse one strict JSON object, rejecting duplicate keys and NaN."""

        if type(value) is not str:
            _fail("$", "expected JSON string")

        def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    _fail(f"$.{key}", "duplicate field")
                result[key] = item
            return result

        def reject_constant(constant: str) -> None:
            _fail("$", f"invalid JSON number {constant}")

        try:
            parsed = json.loads(
                value,
                object_pairs_hook=pairs_hook,
                parse_constant=reject_constant,
            )
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise SchemaValidationError("$: invalid JSON") from exc
        return cls.from_dict(parsed)  # type: ignore[attr-defined,no-any-return]

    @classmethod
    def _source(cls, value: object) -> Mapping[str, Any]:
        source = _mapping(value, "$")
        _strict_keys(source, {field.name for field in fields(cls)})
        return source


@dataclass(frozen=True)
class SubtitleChunk(StrictSchema):
    chunk_id: str
    course_id: str
    video_id: str
    chapter_id: str | None
    start_index: int
    end_index: int
    previous_context: str
    current_text: str
    next_context: str
    cue_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "SubtitleChunk":
        _string(self.chunk_id, "$.chunk_id", limit=128)
        _string(self.course_id, "$.course_id", limit=4096)
        _string(self.video_id, "$.video_id", limit=4096)
        _optional_string(self.chapter_id, "$.chapter_id", limit=4096)
        start = _integer(self.start_index, "$.start_index")
        end = _integer(self.end_index, "$.end_index")
        if end < start:
            _fail("$.end_index", "must be >= start_index")
        _string(self.previous_context, "$.previous_context", empty=True)
        _string(self.current_text, "$.current_text")
        _string(self.next_context, "$.next_context", empty=True)
        cue_ids = _string_tuple(self.cue_ids, "$.cue_ids", allow_empty=False, item_limit=4096)
        if len(set(cue_ids)) != len(cue_ids):
            _fail("$.cue_ids", "must not contain duplicates")
        return self

    @classmethod
    def from_dict(cls, value: object) -> "SubtitleChunk":
        source = cls._source(value)
        return cls(
            chunk_id=_string(source["chunk_id"], "$.chunk_id", limit=128),
            course_id=_string(source["course_id"], "$.course_id", limit=4096),
            video_id=_string(source["video_id"], "$.video_id", limit=4096),
            chapter_id=_optional_string(source["chapter_id"], "$.chapter_id", limit=4096),
            start_index=_integer(source["start_index"], "$.start_index"),
            end_index=_integer(source["end_index"], "$.end_index"),
            previous_context=_string(source["previous_context"], "$.previous_context", empty=True),
            current_text=_string(source["current_text"], "$.current_text"),
            next_context=_string(source["next_context"], "$.next_context", empty=True),
            cue_ids=_string_tuple(source["cue_ids"], "$.cue_ids", allow_empty=False, item_limit=4096),
        )


@dataclass(frozen=True)
class TermCandidate(StrictSchema):
    original: str
    canonical: str
    category: str
    confidence: float
    source: str
    cue_ids: tuple[str, ...]
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "TermCandidate":
        _string(self.original, "$.original")
        _string(self.canonical, "$.canonical")
        _string(self.category, "$.category", limit=128)
        _number(self.confidence, "$.confidence")
        _string(self.source, "$.source", limit=256)
        _string_tuple(self.cue_ids, "$.cue_ids", allow_empty=False, item_limit=4096)
        _string_tuple(self.evidence, "$.evidence")
        return self

    @classmethod
    def from_dict(cls, value: object) -> "TermCandidate":
        source = cls._source(value)
        return cls(
            original=_string(source["original"], "$.original"),
            canonical=_string(source["canonical"], "$.canonical"),
            category=_string(source["category"], "$.category", limit=128),
            confidence=_number(source["confidence"], "$.confidence"),
            source=_string(source["source"], "$.source", limit=256),
            cue_ids=_string_tuple(source["cue_ids"], "$.cue_ids", allow_empty=False, item_limit=4096),
            evidence=_string_tuple(source["evidence"], "$.evidence"),
        )


@dataclass(frozen=True)
class CorrectionChange(StrictSchema):
    original: str
    corrected: str
    type: str
    confidence: float
    reason: str
    start: int
    end: int
    cue_ids: tuple[str, ...]
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "CorrectionChange":
        original = _string(self.original, "$.original")
        _string(self.corrected, "$.corrected")
        _string(self.type, "$.type", limit=128)
        _number(self.confidence, "$.confidence")
        _string(self.reason, "$.reason")
        start = _integer(self.start, "$.start")
        end = _integer(self.end, "$.end")
        if end <= start or end - start != len(original):
            _fail("$.end", "must delimit original exactly")
        _string_tuple(self.cue_ids, "$.cue_ids", allow_empty=False, item_limit=4096)
        _string_tuple(self.evidence, "$.evidence")
        return self

    @classmethod
    def from_dict(cls, value: object) -> "CorrectionChange":
        source = cls._source(value)
        return cls(
            original=_string(source["original"], "$.original"),
            corrected=_string(source["corrected"], "$.corrected"),
            type=_string(source["type"], "$.type", limit=128),
            confidence=_number(source["confidence"], "$.confidence"),
            reason=_string(source["reason"], "$.reason"),
            start=_integer(source["start"], "$.start"),
            end=_integer(source["end"], "$.end"),
            cue_ids=_string_tuple(source["cue_ids"], "$.cue_ids", allow_empty=False, item_limit=4096),
            evidence=_string_tuple(source["evidence"], "$.evidence"),
        )


@dataclass(frozen=True)
class UncertainSpan(StrictSchema):
    text: str
    candidates: tuple[TermCandidate, ...]
    confidence: float
    start: int
    end: int
    cue_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "UncertainSpan":
        text = _string(self.text, "$.text")
        if type(self.candidates) is not tuple or not all(type(item) is TermCandidate for item in self.candidates):
            _fail("$.candidates", "expected TermCandidate array")
        for item in self.candidates:
            item.validate()
        _number(self.confidence, "$.confidence")
        start = _integer(self.start, "$.start")
        end = _integer(self.end, "$.end")
        if end <= start or end - start != len(text):
            _fail("$.end", "must delimit text exactly")
        _string_tuple(self.cue_ids, "$.cue_ids", allow_empty=False, item_limit=4096)
        return self

    @classmethod
    def from_dict(cls, value: object) -> "UncertainSpan":
        source = cls._source(value)
        candidates = source["candidates"]
        if type(candidates) is not list:
            _fail("$.candidates", "expected array")
        return cls(
            text=_string(source["text"], "$.text"),
            candidates=tuple(TermCandidate.from_dict(item) for item in candidates),
            confidence=_number(source["confidence"], "$.confidence"),
            start=_integer(source["start"], "$.start"),
            end=_integer(source["end"], "$.end"),
            cue_ids=_string_tuple(source["cue_ids"], "$.cue_ids", allow_empty=False, item_limit=4096),
        )


@dataclass(frozen=True)
class CorrectionResult(StrictSchema):
    chunk_id: str
    corrected_text: str
    changes: tuple[CorrectionChange, ...]
    uncertain: tuple[UncertainSpan, ...]
    confidence: float

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "CorrectionResult":
        _string(self.chunk_id, "$.chunk_id", limit=128)
        _string(self.corrected_text, "$.corrected_text")
        if type(self.changes) is not tuple or not all(type(item) is CorrectionChange for item in self.changes):
            _fail("$.changes", "expected CorrectionChange array")
        if type(self.uncertain) is not tuple or not all(type(item) is UncertainSpan for item in self.uncertain):
            _fail("$.uncertain", "expected UncertainSpan array")
        for item in self.changes + self.uncertain:
            item.validate()
        _number(self.confidence, "$.confidence")
        return self

    @classmethod
    def from_dict(cls, value: object) -> "CorrectionResult":
        source = cls._source(value)
        if type(source["changes"]) is not list:
            _fail("$.changes", "expected array")
        if type(source["uncertain"]) is not list:
            _fail("$.uncertain", "expected array")
        return cls(
            chunk_id=_string(source["chunk_id"], "$.chunk_id", limit=128),
            corrected_text=_string(source["corrected_text"], "$.corrected_text"),
            changes=tuple(CorrectionChange.from_dict(item) for item in source["changes"]),
            uncertain=tuple(UncertainSpan.from_dict(item) for item in source["uncertain"]),
            confidence=_number(source["confidence"], "$.confidence"),
        )


class CriticDecision(str, Enum):
    PASS = "PASS"
    REVISE = "REVISE"
    UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class CriticResult(StrictSchema):
    decision: CriticDecision
    confidence: float
    issues: tuple[str, ...]
    revision_instructions: tuple[str, ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "CriticResult":
        if type(self.decision) is not CriticDecision:
            _fail("$.decision", "expected CriticDecision")
        _number(self.confidence, "$.confidence")
        issues = _string_tuple(self.issues, "$.issues")
        instructions = _string_tuple(self.revision_instructions, "$.revision_instructions")
        if self.decision is CriticDecision.PASS and (issues or instructions):
            _fail("$.decision", "PASS cannot contain issues or revision instructions")
        if self.decision is CriticDecision.REVISE and not instructions:
            _fail("$.revision_instructions", "REVISE requires instructions")
        return self

    @classmethod
    def from_dict(cls, value: object) -> "CriticResult":
        source = cls._source(value)
        raw_decision = source["decision"]
        if type(raw_decision) is not str:
            _fail("$.decision", "expected enum string")
        try:
            decision = CriticDecision(raw_decision)
        except ValueError:
            _fail("$.decision", "invalid CriticDecision")
        return cls(
            decision=decision,
            confidence=_number(source["confidence"], "$.confidence"),
            issues=_string_tuple(source["issues"], "$.issues"),
            revision_instructions=_string_tuple(source["revision_instructions"], "$.revision_instructions"),
        )


@dataclass(frozen=True)
class QualityReport(StrictSchema):
    score: float
    passed: bool
    metrics: dict[str, float]
    warnings: tuple[str, ...]
    schema_pass: bool = True
    critic_pass_rate: float = 0.0
    uncertain_rate: float = 0.0
    numeric_change_count: int = 0
    unsupported_change_count: int = 0
    status: str = "completed"

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "QualityReport":
        _number(self.score, "$.score")
        _boolean(self.passed, "$.passed")
        if type(self.metrics) is not dict:
            _fail("$.metrics", "expected object")
        for key, value in self.metrics.items():
            _string(key, "$.metrics key", limit=128)
            _number(value, f"$.metrics.{key}")
        _string_tuple(self.warnings, "$.warnings")
        _boolean(self.schema_pass, "$.schema_pass")
        _number(self.critic_pass_rate, "$.critic_pass_rate")
        _number(self.uncertain_rate, "$.uncertain_rate")
        _integer(self.numeric_change_count, "$.numeric_change_count")
        _integer(self.unsupported_change_count, "$.unsupported_change_count")
        status = _string(self.status, "$.status", limit=64)
        if status not in {"completed", "completed_with_warnings", "partial", "failed"}:
            _fail("$.status", "invalid orchestrator status")
        return self

    @classmethod
    def from_dict(cls, value: object) -> "QualityReport":
        source = cls._source(value)
        metrics = _mapping(source["metrics"], "$.metrics")
        normalized = {
            _string(key, "$.metrics key", limit=128): _number(item, f"$.metrics.{key}")
            for key, item in metrics.items()
        }
        return cls(
            score=_number(source["score"], "$.score"),
            passed=_boolean(source["passed"], "$.passed"),
            metrics=normalized,
            warnings=_string_tuple(source["warnings"], "$.warnings"),
            schema_pass=_boolean(source["schema_pass"], "$.schema_pass"),
            critic_pass_rate=_number(source["critic_pass_rate"], "$.critic_pass_rate"),
            uncertain_rate=_number(source["uncertain_rate"], "$.uncertain_rate"),
            numeric_change_count=_integer(source["numeric_change_count"], "$.numeric_change_count"),
            unsupported_change_count=_integer(
                source["unsupported_change_count"], "$.unsupported_change_count"
            ),
            status=_string(source["status"], "$.status", limit=64),
        )


@dataclass(frozen=True)
class MemoryDelta(StrictSchema):
    add_terms: tuple[dict[str, Any], ...]
    update_terms: tuple[dict[str, Any], ...]
    new_error_patterns: tuple[dict[str, Any], ...]
    new_concepts: tuple[dict[str, Any], ...]
    new_relationships: tuple[dict[str, Any], ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "MemoryDelta":
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not tuple:
                _fail(f"$.{field.name}", "expected object array")
            _object_tuple(value, f"$.{field.name}")
        return self

    @classmethod
    def from_dict(cls, value: object) -> "MemoryDelta":
        source = cls._source(value)
        return cls(**{name: _object_tuple(source[name], f"$.{name}") for name in (
            "add_terms", "update_terms", "new_error_patterns", "new_concepts", "new_relationships"
        )})


@dataclass(frozen=True)
class OrchestratorResult(StrictSchema):
    course_id: str
    video_id: str
    chunks: tuple[SubtitleChunk, ...]
    corrections: tuple[CorrectionResult, ...]
    critics: tuple[CriticResult, ...]
    quality: QualityReport
    memory_delta: MemoryDelta
    warnings: tuple[str, ...]
    corrected_transcript: str = ""
    uncertain: tuple[UncertainSpan, ...] = ()
    memory_version: str | None = None
    events: tuple[dict[str, Any], ...] = ()
    status: str = "completed"
    raw_hash: str = ""
    cache_key: str = ""

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> "OrchestratorResult":
        _string(self.course_id, "$.course_id", limit=4096)
        _string(self.video_id, "$.video_id", limit=4096)
        checks = (
            (self.chunks, SubtitleChunk, "$.chunks"),
            (self.corrections, CorrectionResult, "$.corrections"),
            (self.critics, CriticResult, "$.critics"),
            (self.uncertain, UncertainSpan, "$.uncertain"),
        )
        for values, expected, path in checks:
            if type(values) is not tuple or not all(type(item) is expected for item in values):
                _fail(path, f"expected {expected.__name__} array")
            for item in values:
                item.validate()
        if type(self.quality) is not QualityReport:
            _fail("$.quality", "expected QualityReport")
        if type(self.memory_delta) is not MemoryDelta:
            _fail("$.memory_delta", "expected MemoryDelta")
        self.quality.validate()
        self.memory_delta.validate()
        _string_tuple(self.warnings, "$.warnings")
        _string(self.corrected_transcript, "$.corrected_transcript", empty=True)
        _optional_string(self.memory_version, "$.memory_version", limit=256)
        events = _object_tuple(self.events, "$.events")
        expected_event_fields = {
            "stage", "agent", "model", "prompt_version", "token_usage",
            "latency_ms", "confidence", "status",
        }
        for index, event in enumerate(events):
            _strict_keys(event, expected_event_fields, f"$.events[{index}]")
            _string(event["stage"], f"$.events[{index}].stage", limit=64)
            _string(event["agent"], f"$.events[{index}].agent", limit=64)
            _string(event["model"], f"$.events[{index}].model", limit=256)
            _string(event["prompt_version"], f"$.events[{index}].prompt_version", limit=128)
            usage = _mapping(event["token_usage"], f"$.events[{index}].token_usage")
            for key, item in usage.items():
                _string(key, f"$.events[{index}].token_usage key", limit=64)
                _integer(item, f"$.events[{index}].token_usage.{key}")
            _number(event["latency_ms"], f"$.events[{index}].latency_ms", maximum=86_400_000.0)
            if event["confidence"] is not None:
                _number(event["confidence"], f"$.events[{index}].confidence")
            _string(event["status"], f"$.events[{index}].status", limit=64)
        if self.status not in {"completed", "completed_with_warnings", "partial", "failed"}:
            _fail("$.status", "invalid orchestrator status")
        for name, value in (("raw_hash", self.raw_hash), ("cache_key", self.cache_key)):
            _string(value, f"$.{name}", empty=True, limit=128)
            if value and (len(value) != 64 or any(character not in "0123456789abcdef" for character in value)):
                _fail(f"$.{name}", "expected lowercase SHA-256")
        return self

    @classmethod
    def from_dict(cls, value: object) -> "OrchestratorResult":
        source = cls._source(value)
        for name in ("chunks", "corrections", "critics", "uncertain", "events"):
            if type(source[name]) is not list:
                _fail(f"$.{name}", "expected array")
        return cls(
            course_id=_string(source["course_id"], "$.course_id", limit=4096),
            video_id=_string(source["video_id"], "$.video_id", limit=4096),
            chunks=tuple(SubtitleChunk.from_dict(item) for item in source["chunks"]),
            corrections=tuple(CorrectionResult.from_dict(item) for item in source["corrections"]),
            critics=tuple(CriticResult.from_dict(item) for item in source["critics"]),
            quality=QualityReport.from_dict(source["quality"]),
            memory_delta=MemoryDelta.from_dict(source["memory_delta"]),
            warnings=_string_tuple(source["warnings"], "$.warnings"),
            corrected_transcript=_string(
                source["corrected_transcript"], "$.corrected_transcript", empty=True
            ),
            uncertain=tuple(UncertainSpan.from_dict(item) for item in source["uncertain"]),
            memory_version=_optional_string(source["memory_version"], "$.memory_version", limit=256),
            events=_object_tuple(source["events"], "$.events"),
            status=_string(source["status"], "$.status", limit=64),
            raw_hash=_string(source["raw_hash"], "$.raw_hash", empty=True, limit=128),
            cache_key=_string(source["cache_key"], "$.cache_key", empty=True, limit=128),
        )


__all__ = [
    "CorrectionChange",
    "CorrectionResult",
    "CriticDecision",
    "CriticResult",
    "MemoryDelta",
    "OrchestratorResult",
    "QualityReport",
    "SchemaValidationError",
    "SubtitleChunk",
    "TermCandidate",
    "UncertainSpan",
    "stable_json_dumps",
]

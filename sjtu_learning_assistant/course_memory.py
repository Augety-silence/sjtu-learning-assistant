"""Versioned, course-isolated storage for the course learning pipeline.

The store deliberately uses plain JSON snapshots instead of coupling course memory to
an AI provider or the application database.  Every mutation follows
``propose -> validate -> commit``; commits are serialized across processes and the
small mutable ``HEAD`` file is replaced atomically.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

if os.name == "nt":
    import msvcrt
else:
    import fcntl

SCHEMA_VERSION = 1
MAX_SINGLE_AI_CONFIDENCE = 0.64
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0

PROFILE_FIELDS = frozenset({
    "course_name",
    "course_subject",
    "course_description",
    "teacher",
    "language",
    "domain",
    "known_topics",
    "current_chapter",
    "completed_chapters",
    "provenance",
})
COLLECTION_SECTIONS = frozenset({
    "glossary",
    "concepts",
    "entities",
    "formulas",
    "teacher_language_patterns",
    "frequent_asr_errors",
    "correction_examples",
    "important_points",
    "chapter_summaries",
    "concept_graph",
    "student_feedback",
    "training_examples",
    "model_versions",
})
ALL_SECTIONS = COLLECTION_SECTIONS | {"course_profile"}
VERIFICATION_LEVELS = frozenset({"UNVERIFIED", "LIKELY", "VERIFIED"})
OPERATIONS = frozenset({"upsert", "delete"})

# Larger numbers are more authoritative.  Aliases are accepted because existing
# agents use both the concise and descriptive spellings.
SOURCE_PRIORITIES: dict[str, int] = {
    "single_ai": 100,
    "ai": 100,
    "ai_inference": 100,
    "historical_ai": 200,
    "ai_history": 200,
    "repeated_course_context": 300,
    "multiple_course_context": 300,
    "course_history": 300,
    "course_context": 300,
    "high_confidence_correction": 300,
    "official_course_material": 400,
    "verified_course_material": 400,
    "course_material": 400,
    "official_material": 400,
    "user_correction": 500,
    "user_confirmed": 500,
    "user": 500,
}
SINGLE_AI_SOURCES = frozenset({"single_ai", "ai", "ai_inference"})
TRAINING_SOURCES = frozenset({
    "user_confirmed",
    "user_correction",
    "user",
    "official_course_material",
    "official_material",
    "verified_course_material",
    "course_material",
    "high_confidence_correction",
})
TRAINING_TASK_TYPES = frozenset({
    "subtitle_correction",
    "terminology",
    "summary",
    "keypoint",
    "knowledge_extraction",
    "review_generation",
})

_STATE_FIELDS = frozenset({
    "schema_version",
    "course_id",
    "course_id_hash",
    "course_profile",
    *COLLECTION_SECTIONS,
    "updated_at",
})
_RECORD_FIELDS = frozenset({
    "item_id",
    "value",
    "source",
    "source_priority",
    "confidence",
    "verification",
    "timestamp",
    "evidence",
})
_UPDATE_FIELDS = frozenset({
    "operation",
    "section",
    "key",
    "value",
    "source",
    "confidence",
    "verification",
    "timestamp",
    "evidence",
})
_PROPOSAL_FIELDS = frozenset({
    "proposal_id",
    "course_id",
    "base_version",
    "created_at",
    "updates",
})
_SNAPSHOT_FIELDS = frozenset({
    "schema_version",
    "course_id_hash",
    "version",
    "previous_version",
    "rollback_from",
    "created_at",
    "payload_sha256",
    "payload",
    "snapshot_sha256",
})
_HEAD_FIELDS = frozenset({
    "schema_version",
    "course_id_hash",
    "version",
    "snapshot_sha256",
    "updated_at",
})
_TRAINING_VALUE_FIELDS = frozenset({
    "sample_type",
    "task_type",
    "system",
    "input",
    "output",
    "chosen",
    "rejected",
})


class CourseMemoryError(RuntimeError):
    """Base class for safe, user-visible course-memory failures."""


class MemoryValidationError(CourseMemoryError, ValueError):
    """A proposal or persisted document does not match the strict schema."""


class MemoryConflictError(CourseMemoryError):
    """HEAD changed after a proposal was created."""


class MemoryCorruptionError(CourseMemoryError):
    """No hash-valid snapshot can be recovered."""


class CrossProcessLockError(CourseMemoryError):
    """The per-course lock could not be acquired before its deadline."""


class FineTuningDisabledError(CourseMemoryError):
    """Fine-tuning was requested while the provider is intentionally disabled."""


def _fail(path: str, message: str) -> None:
    raise MemoryValidationError(f"{path}: {message}")


def _strict_object(value: object, fields: frozenset[str], path: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail(path, "expected object")
    result = value
    missing = fields - set(result)
    unknown = set(result) - fields
    if missing:
        _fail(path, "missing fields: " + ", ".join(sorted(missing)))
    if unknown:
        _fail(path, "unknown fields: " + ", ".join(sorted(unknown)))
    return result


def _string(value: object, path: str, *, nullable: bool = False, empty: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str:
        _fail(path, "expected string")
    if len(value) > 100_000:
        _fail(path, "string is too long")
    if not empty and not value.strip():
        _fail(path, "must not be empty")
    return value


def _json_value(value: object, path: str) -> Any:
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _fail(path, "non-finite numbers are not valid JSON")
        return value
    if type(value) is list:
        return [_json_value(item, f"{path}[{index}]") for index, item in enumerate(value)]
    if type(value) is dict:
        result: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str:
                _fail(path, "object keys must be strings")
            result[key] = _json_value(item, f"{path}.{key}")
        return result
    _fail(path, "expected JSON-compatible value")


def _confidence(value: object, path: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        _fail(path, "expected finite number")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        _fail(path, "must be in [0, 1]")
    return result


def _timestamp(value: object, path: str) -> str:
    text = _string(value, path)
    assert isinstance(text, str)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MemoryValidationError(f"{path}: expected ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(path, "timestamp must include a timezone")
    return text


def _string_list(value: object, path: str, *, nonempty: bool = False) -> list[str]:
    if type(value) is not list:
        _fail(path, "expected array")
    result = []
    for index, item in enumerate(value):
        text = _string(item, f"{path}[{index}]")
        assert isinstance(text, str)
        result.append(text)
    if nonempty and not result:
        _fail(path, "must not be empty")
    if len(set(result)) != len(result):
        _fail(path, "must not contain duplicates")
    return result


def _stable_bytes(value: object) -> bytes:
    checked = _json_value(value, "$")
    return json.dumps(
        checked,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_stable_bytes(value)).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def course_storage_key(course_id: str) -> str:
    """Return a fixed-size identity that cannot introduce path components."""

    checked = _string(course_id, "$.course_id")
    assert isinstance(checked, str)
    return hashlib.sha256(checked.encode("utf-8")).hexdigest()


def source_priority(source: str) -> int:
    """Return the explicit trust rank for a supported provenance source."""

    if type(source) is not str or source not in SOURCE_PRIORITIES:
        raise MemoryValidationError("$.source: unsupported source")
    return SOURCE_PRIORITIES[source]


def _empty_state(course_id: str) -> dict[str, Any]:
    identity = course_storage_key(course_id)
    state: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "course_id": course_id,
        "course_id_hash": identity,
        "course_profile": {
            "course_name": None,
            "course_subject": None,
            "course_description": None,
            "teacher": None,
            "language": None,
            "domain": None,
            "known_topics": [],
            "current_chapter": None,
            "completed_chapters": [],
            "provenance": {},
        },
        "updated_at": None,
    }
    for section in COLLECTION_SECTIONS:
        state[section] = {}
    return state


def _validate_record(value: object, path: str, *, expected_key: str | None = None) -> dict[str, Any]:
    record = _strict_object(value, _RECORD_FIELDS, path)
    item_id = _string(record["item_id"], f"{path}.item_id")
    if expected_key is not None and item_id != expected_key:
        _fail(f"{path}.item_id", "must match collection key")
    checked_value = _json_value(record["value"], f"{path}.value")
    source = _string(record["source"], f"{path}.source")
    assert isinstance(source, str)
    priority = source_priority(source)
    if type(record["source_priority"]) is not int or record["source_priority"] != priority:
        _fail(f"{path}.source_priority", "does not match source")
    confidence = _confidence(record["confidence"], f"{path}.confidence")
    verification = _string(record["verification"], f"{path}.verification")
    if verification not in VERIFICATION_LEVELS:
        _fail(f"{path}.verification", "unsupported verification level")
    timestamp = _timestamp(record["timestamp"], f"{path}.timestamp")
    evidence = _string_list(record["evidence"], f"{path}.evidence", nonempty=True)
    if source in SINGLE_AI_SOURCES:
        if confidence > MAX_SINGLE_AI_CONFIDENCE:
            _fail(f"{path}.confidence", "single AI evidence cannot claim high confidence")
        if verification != "UNVERIFIED":
            _fail(f"{path}.verification", "single AI evidence must remain UNVERIFIED")
    return {
        "item_id": item_id,
        "value": checked_value,
        "source": source,
        "source_priority": priority,
        "confidence": confidence,
        "verification": verification,
        "timestamp": timestamp,
        "evidence": evidence,
    }


def validate_memory_state(value: object, *, expected_course_id: str | None = None) -> dict[str, Any]:
    """Validate and copy a complete persisted memory document."""

    state = _strict_object(value, _STATE_FIELDS, "$")
    if type(state["schema_version"]) is not int or state["schema_version"] != SCHEMA_VERSION:
        _fail("$.schema_version", "unsupported schema version")
    course_id = _string(state["course_id"], "$.course_id")
    assert isinstance(course_id, str)
    if expected_course_id is not None and course_id != expected_course_id:
        _fail("$.course_id", "course isolation mismatch")
    identity = course_storage_key(course_id)
    if state["course_id_hash"] != identity:
        _fail("$.course_id_hash", "does not match course_id")
    profile = _strict_object(state["course_profile"], PROFILE_FIELDS, "$.course_profile")
    normalized_profile: dict[str, Any] = {}
    for name in PROFILE_FIELDS - {"known_topics", "completed_chapters", "provenance"}:
        normalized_profile[name] = _string(profile[name], f"$.course_profile.{name}", nullable=True)
    normalized_profile["known_topics"] = _string_list(profile["known_topics"], "$.course_profile.known_topics")
    normalized_profile["completed_chapters"] = _string_list(
        profile["completed_chapters"], "$.course_profile.completed_chapters"
    )
    if type(profile["provenance"]) is not dict:
        _fail("$.course_profile.provenance", "expected object")
    normalized_provenance: dict[str, Any] = {}
    for key, record in profile["provenance"].items():
        if key not in PROFILE_FIELDS - {"provenance"}:
            _fail(f"$.course_profile.provenance.{key}", "unknown profile field")
        normalized_record = _validate_record(
            record, f"$.course_profile.provenance.{key}", expected_key=key
        )
        if normalized_record["value"] != profile[key]:
            _fail(f"$.course_profile.provenance.{key}.value", "must match profile value")
        normalized_provenance[key] = normalized_record
    normalized_profile["provenance"] = normalized_provenance
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "course_id": course_id,
        "course_id_hash": identity,
        "course_profile": normalized_profile,
    }
    for section in COLLECTION_SECTIONS:
        collection = state[section]
        if type(collection) is not dict:
            _fail(f"$.{section}", "expected object")
        result[section] = {
            str(key): _validate_record(record, f"$.{section}.{key}", expected_key=str(key))
            for key, record in collection.items()
            if type(key) is str
        }
        if len(result[section]) != len(collection):
            _fail(f"$.{section}", "keys must be strings")
    updated_at = state["updated_at"]
    result["updated_at"] = None if updated_at is None else _timestamp(updated_at, "$.updated_at")
    return result


@dataclass(frozen=True)
class MemoryUpdate:
    operation: str
    section: str
    key: str
    value: Any
    source: str
    confidence: float
    verification: str
    timestamp: str
    evidence: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: object) -> "MemoryUpdate":
        row = _strict_object(value, _UPDATE_FIELDS, "$.update")
        operation = _string(row["operation"], "$.update.operation")
        section = _string(row["section"], "$.update.section")
        key = _string(row["key"], "$.update.key")
        source = _string(row["source"], "$.update.source")
        verification = _string(row["verification"], "$.update.verification")
        assert all(isinstance(item, str) for item in (operation, section, key, source, verification))
        if operation not in OPERATIONS:
            _fail("$.update.operation", "unsupported operation")
        if section not in ALL_SECTIONS:
            _fail("$.update.section", "unsupported section")
        if section == "course_profile" and key not in PROFILE_FIELDS - {"provenance"}:
            _fail("$.update.key", "unknown course profile field")
        checked_value = _json_value(row["value"], "$.update.value")
        if operation == "delete" and checked_value is not None:
            _fail("$.update.value", "delete requires null")
        priority = source_priority(source)
        del priority
        confidence = _confidence(row["confidence"], "$.update.confidence")
        if verification not in VERIFICATION_LEVELS:
            _fail("$.update.verification", "unsupported verification level")
        timestamp = _timestamp(row["timestamp"], "$.update.timestamp")
        evidence = tuple(_string_list(row["evidence"], "$.update.evidence", nonempty=True))
        if source in SINGLE_AI_SOURCES:
            if confidence > MAX_SINGLE_AI_CONFIDENCE:
                _fail("$.update.confidence", "single AI evidence cannot claim high confidence")
            if verification != "UNVERIFIED":
                _fail("$.update.verification", "single AI evidence must remain UNVERIFIED")
        if operation == "delete" and source not in {"user", "user_confirmed", "user_correction"}:
            _fail("$.update.source", "only a confirmed user source may delete memory")
        return cls(operation, section, key, checked_value, source, confidence, verification, timestamp, evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "section": self.section,
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "confidence": self.confidence,
            "verification": self.verification,
            "timestamp": self.timestamp,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class MemoryProposal:
    proposal_id: str
    course_id: str
    base_version: str | None
    created_at: str
    updates: tuple[MemoryUpdate, ...]

    @classmethod
    def from_dict(cls, value: object) -> "MemoryProposal":
        row = _strict_object(value, _PROPOSAL_FIELDS, "$")
        proposal_id = _string(row["proposal_id"], "$.proposal_id")
        course_id = _string(row["course_id"], "$.course_id")
        base_version = _string(row["base_version"], "$.base_version", nullable=True)
        created_at = _timestamp(row["created_at"], "$.created_at")
        if type(row["updates"]) is not list or not row["updates"]:
            _fail("$.updates", "expected non-empty array")
        assert isinstance(proposal_id, str) and isinstance(course_id, str)
        return cls(
            proposal_id,
            course_id,
            base_version,
            created_at,
            tuple(MemoryUpdate.from_dict(item) for item in row["updates"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "course_id": self.course_id,
            "base_version": self.base_version,
            "created_at": self.created_at,
            "updates": [item.to_dict() for item in self.updates],
        }


@dataclass(frozen=True)
class MemorySnapshot:
    version: str
    previous_version: str | None
    rollback_from: str | None
    created_at: str
    payload_sha256: str
    snapshot_sha256: str
    state: dict[str, Any]


class TrainingSampleQualityGate:
    """Gate model-training data, never learner-facing practice items.

    Subtitle correction pairs remain isolated in ``training_examples`` and must not
    be surfaced as recall/application exercises in course learning products.
    """

    minimum_confidence = 0.90

    def validate(self, value: object, *, source: str, confidence: float,
                 verification: str, evidence: Iterable[str]) -> dict[str, Any]:
        sample = _strict_object(value, _TRAINING_VALUE_FIELDS, "$.training_example.value")
        if source not in TRAINING_SOURCES:
            _fail("$.training_example.source", "source is not eligible for training")
        if confidence < self.minimum_confidence:
            _fail("$.training_example.confidence", "below training quality threshold")
        if verification not in {"LIKELY", "VERIFIED"}:
            _fail("$.training_example.verification", "training sample must be LIKELY or VERIFIED")
        if not tuple(evidence):
            _fail("$.training_example.evidence", "grounding evidence is required")
        sample_type = _string(sample["sample_type"], "$.training_example.value.sample_type")
        task_type = _string(sample["task_type"], "$.training_example.value.task_type")
        if sample_type not in {"sft", "preference"}:
            _fail("$.training_example.value.sample_type", "expected sft or preference")
        if task_type not in TRAINING_TASK_TYPES:
            _fail("$.training_example.value.task_type", "unsupported training task")
        system = _string(sample["system"], "$.training_example.value.system", nullable=True)
        input_text = _string(sample["input"], "$.training_example.value.input")
        output = _string(sample["output"], "$.training_example.value.output", nullable=True)
        chosen = _string(sample["chosen"], "$.training_example.value.chosen", nullable=True)
        rejected = _string(sample["rejected"], "$.training_example.value.rejected", nullable=True)
        if sample_type == "sft":
            if output is None or chosen is not None or rejected is not None:
                _fail("$.training_example.value", "SFT requires output and forbids chosen/rejected")
            if output.strip() == input_text.strip():
                _fail("$.training_example.value.output", "input and output must differ")
        else:
            if output is not None or chosen is None or rejected is None:
                _fail("$.training_example.value", "preference requires chosen/rejected and null output")
            if chosen.strip() == rejected.strip():
                _fail("$.training_example.value.rejected", "chosen and rejected must differ")
        return {
            "sample_type": sample_type,
            "task_type": task_type,
            "system": system,
            "input": input_text,
            "output": output,
            "chosen": chosen,
            "rejected": rejected,
        }


class FineTuneProvider:
    """Disabled-by-default interface; business code cannot start online training."""

    enabled = False

    def submit(self, *, course_id: str, dataset_version: str, config: Mapping[str, Any]) -> str:
        del course_id, dataset_version, config
        raise FineTuningDisabledError("Fine-tuning is disabled until an explicit provider is configured.")

    def rollback(self, model_version: str) -> None:
        del model_version
        raise FineTuningDisabledError("Fine-tuning is disabled until an explicit provider is configured.")


class DisabledFineTuneProvider(FineTuneProvider):
    """Concrete sentinel used by the current local-only implementation."""


@contextmanager
def _exclusive_file_lock(path: Path, timeout: float) -> Iterator[None]:
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    if os.fstat(descriptor).st_size == 0:
                        os.write(descriptor, b"\0")
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise CrossProcessLockError("Timed out waiting for the course-memory lock.")
                time.sleep(0.02)
        yield
    finally:
        try:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise CourseMemoryError("Course-memory path must be a real directory.")
    os.chmod(path, 0o700)


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate field: {key}")
            result[key] = item
        return result

    try:
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            raise MemoryCorruptionError(f"Unsafe memory file: {path.name}")
        raw = path.read_bytes()
        parsed = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise MemoryCorruptionError(f"Invalid memory file: {path.name}") from exc
    if type(parsed) is not dict:
        raise MemoryCorruptionError(f"Invalid memory file: {path.name}")
    return parsed


class CourseMemoryStore:
    """Private, versioned storage for exactly one course."""

    def __init__(self, root: str | Path, course_id: str, *, lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        self.root = Path(root).expanduser()
        self.course_id = _string(course_id, "$.course_id")  # type: ignore[assignment]
        assert isinstance(self.course_id, str)
        self.course_hash = course_storage_key(self.course_id)
        self.course_dir = self.root / "courses" / self.course_hash[:2] / self.course_hash
        self.versions_dir = self.course_dir / "versions"
        self.head_path = self.course_dir / "HEAD"
        self.lock_path = self.course_dir / ".lock"
        self.lock_timeout = float(lock_timeout)
        if self.lock_timeout < 0:
            raise ValueError("lock_timeout must be non-negative")
        for directory in (self.root, self.root / "courses", self.course_dir.parent, self.course_dir, self.versions_dir):
            _ensure_private_directory(directory)
        self.fine_tune_provider: FineTuneProvider = DisabledFineTuneProvider()
        self.training_quality_gate = TrainingSampleQualityGate()

    @contextmanager
    def lock(self) -> Iterator[None]:
        with _exclusive_file_lock(self.lock_path, self.lock_timeout):
            yield

    def _snapshot_path(self, version: str) -> Path:
        if type(version) is not str or not version or any(character not in "0123456789abcdef-" for character in version):
            raise MemoryValidationError("$.version: invalid version identifier")
        return self.versions_dir / f"{version}.json"

    def _decode_snapshot(self, path: Path) -> MemorySnapshot:
        row = _strict_object(_read_json(path), _SNAPSHOT_FIELDS, "$")
        if row["schema_version"] != SCHEMA_VERSION or row["course_id_hash"] != self.course_hash:
            raise MemoryCorruptionError(f"Snapshot isolation/schema mismatch: {path.name}")
        version = _string(row["version"], "$.version")
        previous = _string(row["previous_version"], "$.previous_version", nullable=True)
        rollback_from = _string(row["rollback_from"], "$.rollback_from", nullable=True)
        created_at = _timestamp(row["created_at"], "$.created_at")
        payload_hash = _string(row["payload_sha256"], "$.payload_sha256")
        snapshot_hash = _string(row["snapshot_sha256"], "$.snapshot_sha256")
        assert isinstance(version, str) and isinstance(payload_hash, str) and isinstance(snapshot_hash, str)
        if path != self._snapshot_path(version):
            raise MemoryCorruptionError(f"Snapshot filename mismatch: {path.name}")
        state = validate_memory_state(row["payload"], expected_course_id=self.course_id)
        if _sha256(state) != payload_hash:
            raise MemoryCorruptionError(f"Snapshot payload hash mismatch: {path.name}")
        unsigned = dict(row)
        unsigned.pop("snapshot_sha256")
        if _sha256(unsigned) != snapshot_hash:
            raise MemoryCorruptionError(f"Snapshot hash mismatch: {path.name}")
        return MemorySnapshot(version, previous, rollback_from, created_at, payload_hash, snapshot_hash, state)

    def _valid_head(self) -> tuple[str, str] | None:
        if not self.head_path.exists():
            return None
        try:
            head = _strict_object(_read_json(self.head_path), _HEAD_FIELDS, "$")
            if head["schema_version"] != SCHEMA_VERSION or head["course_id_hash"] != self.course_hash:
                return None
            version = _string(head["version"], "$.version")
            digest = _string(head["snapshot_sha256"], "$.snapshot_sha256")
            _timestamp(head["updated_at"], "$.updated_at")
            assert isinstance(version, str) and isinstance(digest, str)
            return version, digest
        except (MemoryValidationError, MemoryCorruptionError):
            return None

    def _write_head(self, snapshot: MemorySnapshot) -> None:
        head = {
            "schema_version": SCHEMA_VERSION,
            "course_id_hash": self.course_hash,
            "version": snapshot.version,
            "snapshot_sha256": snapshot.snapshot_sha256,
            "updated_at": _utc_now(),
        }
        _atomic_write(self.head_path, _stable_bytes(head))

    def _recover_locked(self) -> MemorySnapshot | None:
        candidates: list[MemorySnapshot] = []
        for path in self.versions_dir.glob("*.json"):
            try:
                candidates.append(self._decode_snapshot(path))
            except (MemoryValidationError, MemoryCorruptionError):
                continue
        if not candidates:
            if any(self.versions_dir.glob("*.json")) or self.head_path.exists():
                raise MemoryCorruptionError("No valid course-memory snapshot is available.")
            return None
        recovered = max(candidates, key=lambda item: (item.created_at, item.version))
        self._write_head(recovered)
        return recovered

    def _load_locked(self, *, recover: bool = True) -> MemorySnapshot | None:
        head = self._valid_head()
        if head is not None:
            try:
                snapshot = self._decode_snapshot(self._snapshot_path(head[0]))
                if snapshot.snapshot_sha256 == head[1]:
                    return snapshot
            except (MemoryValidationError, MemoryCorruptionError, OSError):
                pass
        if recover:
            return self._recover_locked()
        if head is None and not self.head_path.exists():
            return None
        raise MemoryCorruptionError("Course-memory HEAD or current snapshot is invalid.")

    def load_snapshot(self, *, recover: bool = True) -> MemorySnapshot | None:
        with self.lock():
            return self._load_locked(recover=recover)

    def load(self, *, recover: bool = True) -> dict[str, Any]:
        snapshot = self.load_snapshot(recover=recover)
        return validate_memory_state(
            snapshot.state if snapshot is not None else _empty_state(self.course_id),
            expected_course_id=self.course_id,
        )

    @property
    def current_version(self) -> str | None:
        snapshot = self.load_snapshot()
        return None if snapshot is None else snapshot.version

    def propose(self, updates: Iterable[MemoryUpdate | Mapping[str, Any]]) -> MemoryProposal:
        snapshot = self.load_snapshot()
        normalized = tuple(
            item if type(item) is MemoryUpdate else MemoryUpdate.from_dict(dict(item))
            for item in updates
        )
        if not normalized:
            raise MemoryValidationError("$.updates: expected non-empty array")
        proposal = MemoryProposal(
            proposal_id=uuid.uuid4().hex,
            course_id=self.course_id,
            base_version=None if snapshot is None else snapshot.version,
            created_at=_utc_now(),
            updates=normalized,
        )
        self.validate_proposal(proposal, state=_empty_state(self.course_id) if snapshot is None else snapshot.state)
        return proposal

    propose_update = propose

    def _existing_record(self, state: dict[str, Any], update: MemoryUpdate) -> dict[str, Any] | None:
        if update.section == "course_profile":
            return state["course_profile"]["provenance"].get(update.key)
        return state[update.section].get(update.key)

    def validate_proposal(self, proposal: MemoryProposal | Mapping[str, Any], *,
                          state: dict[str, Any] | None = None) -> MemoryProposal:
        checked = (
            MemoryProposal.from_dict(proposal.to_dict())
            if type(proposal) is MemoryProposal
            else MemoryProposal.from_dict(dict(proposal))
        )
        if checked.course_id != self.course_id:
            raise MemoryValidationError("$.course_id: course isolation mismatch")
        base_state = self.load() if state is None else validate_memory_state(state, expected_course_id=self.course_id)
        seen: set[tuple[str, str]] = set()
        for update in checked.updates:
            # Round-trip catches manually constructed dataclass instances too.
            update = MemoryUpdate.from_dict(update.to_dict())
            identity = (update.section, update.key)
            if identity in seen:
                raise MemoryValidationError("$.updates: duplicate target in one proposal")
            seen.add(identity)
            existing = self._existing_record(base_state, update)
            if existing is not None:
                old_priority = int(existing["source_priority"])
                new_priority = source_priority(update.source)
                if new_priority < old_priority:
                    raise MemoryValidationError("$.update.source: lower-priority evidence cannot overwrite memory")
                if new_priority == old_priority and update.confidence < float(existing["confidence"]):
                    raise MemoryValidationError("$.update.confidence: confidence cannot regress at equal priority")
                if update.source in SINGLE_AI_SOURCES:
                    raise MemoryValidationError("$.update.source: single AI evidence cannot overwrite existing memory")
            if update.section == "course_profile" and update.operation == "upsert":
                if update.key in {"known_topics", "completed_chapters"}:
                    _string_list(update.value, "$.update.value")
                else:
                    _string(update.value, "$.update.value")
            if update.section == "training_examples" and update.operation == "upsert":
                self.training_quality_gate.validate(
                    update.value,
                    source=update.source,
                    confidence=update.confidence,
                    verification=update.verification,
                    evidence=update.evidence,
                )
        return checked

    validate = validate_proposal

    def _apply(self, state: dict[str, Any], proposal: MemoryProposal, now: str) -> dict[str, Any]:
        result = json.loads(_stable_bytes(state).decode("utf-8"))
        for update in proposal.updates:
            if update.operation == "delete":
                if update.section == "course_profile":
                    result["course_profile"][update.key] = [] if update.key in {"known_topics", "completed_chapters"} else None
                    result["course_profile"]["provenance"].pop(update.key, None)
                else:
                    result[update.section].pop(update.key, None)
                continue
            record = {
                "item_id": update.key,
                "value": update.value,
                "source": update.source,
                "source_priority": source_priority(update.source),
                "confidence": update.confidence,
                "verification": update.verification,
                "timestamp": update.timestamp,
                "evidence": list(update.evidence),
            }
            if update.section == "course_profile":
                result["course_profile"][update.key] = update.value
                result["course_profile"]["provenance"][update.key] = record
            else:
                result[update.section][update.key] = record
        result["updated_at"] = now
        return validate_memory_state(result, expected_course_id=self.course_id)

    def _commit_state_locked(self, state: dict[str, Any], previous: str | None,
                             *, rollback_from: str | None = None) -> MemorySnapshot:
        now = _utc_now()
        state["updated_at"] = now
        normalized = validate_memory_state(state, expected_course_id=self.course_id)
        payload_hash = _sha256(normalized)
        version = f"{int(time.time_ns()):x}-{uuid.uuid4().hex[:12]}"
        unsigned = {
            "schema_version": SCHEMA_VERSION,
            "course_id_hash": self.course_hash,
            "version": version,
            "previous_version": previous,
            "rollback_from": rollback_from,
            "created_at": now,
            "payload_sha256": payload_hash,
            "payload": normalized,
        }
        digest = _sha256(unsigned)
        document = {**unsigned, "snapshot_sha256": digest}
        target = self._snapshot_path(version)
        if target.exists():
            raise MemoryConflictError("Generated snapshot version already exists.")
        _atomic_write(target, _stable_bytes(document))
        snapshot = self._decode_snapshot(target)
        try:
            self._write_head(snapshot)
        except BaseException:
            # A snapshot is committed only when HEAD publishes it.  Remove an
            # unpublished orphan so later corruption recovery cannot resurrect it.
            head = self._valid_head()
            if head == (snapshot.version, snapshot.snapshot_sha256):
                return snapshot
            target.unlink(missing_ok=True)
            raise
        return snapshot

    def commit(self, proposal: MemoryProposal | Mapping[str, Any]) -> MemorySnapshot:
        checked = (
            MemoryProposal.from_dict(proposal.to_dict())
            if type(proposal) is MemoryProposal
            else MemoryProposal.from_dict(dict(proposal))
        )
        with self.lock():
            current = self._load_locked()
            current_version = None if current is None else current.version
            if checked.base_version != current_version:
                raise MemoryConflictError("Course memory changed after this proposal was created.")
            state = _empty_state(self.course_id) if current is None else current.state
            self.validate_proposal(checked, state=state)
            updated = self._apply(state, checked, _utc_now())
            return self._commit_state_locked(updated, current_version)

    atomic_commit = commit

    def list_versions(self) -> tuple[MemorySnapshot, ...]:
        with self.lock():
            snapshots = []
            for path in self.versions_dir.glob("*.json"):
                try:
                    snapshots.append(self._decode_snapshot(path))
                except (MemoryValidationError, MemoryCorruptionError):
                    continue
            return tuple(sorted(snapshots, key=lambda item: (item.created_at, item.version)))

    def verify_snapshot(self, version: str) -> bool:
        try:
            with self.lock():
                self._decode_snapshot(self._snapshot_path(version))
            return True
        except (OSError, MemoryValidationError, MemoryCorruptionError):
            return False

    def rollback(self, version: str) -> MemorySnapshot:
        """Restore a verified old payload by creating a new auditable snapshot."""

        with self.lock():
            current = self._load_locked()
            if current is None:
                raise MemoryConflictError("Cannot roll back empty course memory.")
            try:
                target = self._decode_snapshot(self._snapshot_path(version))
            except (OSError, MemoryValidationError, MemoryCorruptionError) as exc:
                raise MemoryCorruptionError("Rollback target is missing or failed hash validation.") from exc
            restored = json.loads(_stable_bytes(target.state).decode("utf-8"))
            return self._commit_state_locked(restored, current.version, rollback_from=target.version)

    def recover(self) -> MemorySnapshot | None:
        with self.lock():
            return self._recover_locked()


# A shorter public name for callers that model one store instance as one memory.
CourseMemory = CourseMemoryStore

__all__ = [
    "ALL_SECTIONS",
    "COLLECTION_SECTIONS",
    "CourseMemory",
    "CourseMemoryError",
    "CourseMemoryStore",
    "CrossProcessLockError",
    "DisabledFineTuneProvider",
    "FineTuneProvider",
    "FineTuningDisabledError",
    "MAX_SINGLE_AI_CONFIDENCE",
    "MemoryConflictError",
    "MemoryCorruptionError",
    "MemoryProposal",
    "MemorySnapshot",
    "MemoryUpdate",
    "MemoryValidationError",
    "SCHEMA_VERSION",
    "SOURCE_PRIORITIES",
    "TrainingSampleQualityGate",
    "course_storage_key",
    "source_priority",
    "validate_memory_state",
]

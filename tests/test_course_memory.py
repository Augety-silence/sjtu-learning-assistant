from __future__ import annotations

import json
import multiprocessing
import os
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sjtu_learning_assistant.course_memory import (
    CourseMemoryStore,
    CrossProcessLockError,
    DisabledFineTuneProvider,
    FineTuningDisabledError,
    MemoryConflictError,
    MemoryCorruptionError,
    MemoryProposal,
    MemoryUpdate,
    MemoryValidationError,
    course_storage_key,
    validate_memory_state,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _update(
    *,
    section: str = "glossary",
    key: str = "vif",
    value: object = None,
    source: str = "user_confirmed",
    confidence: float = 0.99,
    verification: str = "VERIFIED",
    operation: str = "upsert",
) -> dict[str, object]:
    if value is None and operation == "upsert":
        value = {"canonical": "VIF", "meaning": "Variance Inflation Factor"}
    return {
        "operation": operation,
        "section": section,
        "key": key,
        "value": value,
        "source": source,
        "confidence": confidence,
        "verification": verification,
        "timestamp": _now(),
        "evidence": ["video-03/chunk-14"],
    }


def _hold_lock(path: str, acquired: multiprocessing.synchronize.Event,
               release: multiprocessing.synchronize.Event) -> None:
    # Import in the child so this works with both fork and spawn start methods.
    from sjtu_learning_assistant.course_memory import _exclusive_file_lock

    with _exclusive_file_lock(Path(path), 2.0):
        acquired.set()
        release.wait(3.0)


class CourseMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "memory"
        self.store = CourseMemoryStore(self.root, "course/../../统计学")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _commit(self, update: dict[str, object] | None = None):
        return self.store.commit(self.store.propose([update or _update()]))

    def test_course_isolation_uses_full_hash_and_never_raw_id(self) -> None:
        other = CourseMemoryStore(self.root, "other/../../统计学")
        self.assertEqual(64, len(self.store.course_hash))
        self.assertEqual(course_storage_key("course/../../统计学"), self.store.course_hash)
        self.assertNotEqual(self.store.course_dir, other.course_dir)
        self.assertEqual(self.root.resolve(), self.store.course_dir.parents[2].resolve())
        self.assertNotIn(self.store.course_id, str(self.store.course_dir.relative_to(self.root)))
        self.assertNotIn("统计学", str(self.store.course_dir))
        self._commit()
        self.assertEqual({}, other.load()["glossary"])

    @unittest.skipIf(os.name == "nt", "Windows does not expose POSIX permission bits")
    def test_directories_and_files_have_private_permissions(self) -> None:
        snapshot = self._commit()
        for directory in (self.root, self.store.course_dir, self.store.versions_dir):
            self.assertEqual(0o700, stat.S_IMODE(directory.stat().st_mode))
        for path in (self.store.head_path, self.store.lock_path,
                     self.store.versions_dir / f"{snapshot.version}.json"):
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    def test_propose_validate_then_atomic_commit(self) -> None:
        proposal = self.store.propose([_update()])
        self.assertIsInstance(proposal, MemoryProposal)
        self.assertIsNone(proposal.base_version)
        self.assertFalse(self.store.head_path.exists())
        self.assertEqual({}, self.store.load()["glossary"])

        validated = self.store.validate_proposal(proposal)
        snapshot = self.store.atomic_commit(validated)
        self.assertEqual(snapshot.version, self.store.current_version)
        self.assertEqual("VIF", self.store.load()["glossary"]["vif"]["value"]["canonical"])

    def test_strict_update_and_proposal_schemas_reject_missing_and_extra_fields(self) -> None:
        missing = _update()
        missing.pop("evidence")
        with self.assertRaisesRegex(MemoryValidationError, "missing fields"):
            MemoryUpdate.from_dict(missing)
        extra = _update()
        extra["run_command"] = "rm -rf"
        with self.assertRaisesRegex(MemoryValidationError, "unknown fields"):
            MemoryUpdate.from_dict(extra)

        proposal = self.store.propose([_update()]).to_dict()
        proposal["unexpected"] = True
        with self.assertRaisesRegex(MemoryValidationError, "unknown fields"):
            MemoryProposal.from_dict(proposal)

    def test_complete_state_schema_rejects_unknown_and_missing_fields(self) -> None:
        state = self.store.load()
        state["unknown"] = []
        with self.assertRaisesRegex(MemoryValidationError, "unknown fields"):
            validate_memory_state(state)
        state = self.store.load()
        del state["course_profile"]["teacher"]
        with self.assertRaisesRegex(MemoryValidationError, "missing fields"):
            validate_memory_state(state)

    def test_source_priority_prevents_lower_trust_overwrite(self) -> None:
        self._commit()
        with self.assertRaisesRegex(MemoryValidationError, "lower-priority"):
            self.store.propose([_update(
                value={"canonical": "wrong"},
                source="historical_ai",
                confidence=0.60,
                verification="UNVERIFIED",
            )])
        replacement = self.store.propose([_update(value={"canonical": "VIF confirmed"})])
        self.store.commit(replacement)
        self.assertEqual("VIF confirmed", self.store.load()["glossary"]["vif"]["value"]["canonical"])

    def test_single_ai_is_confidence_limited_unverified_and_cannot_overwrite(self) -> None:
        with self.assertRaisesRegex(MemoryValidationError, "high confidence"):
            self.store.propose([_update(
                source="single_ai", confidence=0.90, verification="UNVERIFIED"
            )])
        with self.assertRaisesRegex(MemoryValidationError, "UNVERIFIED"):
            self.store.propose([_update(
                source="single_ai", confidence=0.60, verification="LIKELY"
            )])
        self._commit(_update(
            source="single_ai", confidence=0.60, verification="UNVERIFIED"
        ))
        with self.assertRaisesRegex(MemoryValidationError, "cannot overwrite"):
            self.store.propose([_update(
                source="single_ai", confidence=0.61, verification="UNVERIFIED"
            )])

    def test_head_points_to_immutable_version_snapshots(self) -> None:
        first = self._commit()
        first_path = self.store.versions_dir / f"{first.version}.json"
        first_bytes = first_path.read_bytes()
        second = self._commit(_update(key="ols", value={"canonical": "OLS"}))
        head = json.loads(self.store.head_path.read_text("utf-8"))
        self.assertEqual(second.version, head["version"])
        self.assertEqual(second.snapshot_sha256, head["snapshot_sha256"])
        self.assertEqual(first_bytes, first_path.read_bytes())
        self.assertEqual(2, len(self.store.list_versions()))
        self.assertEqual(first.version, second.previous_version)

    def test_stale_proposal_is_rejected(self) -> None:
        stale = self.store.propose([_update(key="stale")])
        self._commit(_update(key="fresh"))
        with self.assertRaises(MemoryConflictError):
            self.store.commit(stale)

    def test_head_replace_failure_does_not_publish_new_version(self) -> None:
        first = self._commit()
        proposal = self.store.propose([_update(key="ols", value={"canonical": "OLS"})])
        real_atomic_write = __import__(
            "sjtu_learning_assistant.course_memory", fromlist=["_atomic_write"]
        )._atomic_write

        def fail_head(path: Path, payload: bytes) -> None:
            if path == self.store.head_path:
                raise OSError("simulated crash before HEAD replacement")
            real_atomic_write(path, payload)

        with patch("sjtu_learning_assistant.course_memory._atomic_write", side_effect=fail_head):
            with self.assertRaises(OSError):
                self.store.commit(proposal)
        self.assertEqual(first.version, self.store.current_version)
        self.assertNotIn("ols", self.store.load()["glossary"])

    def test_rollback_creates_new_snapshot_without_mutating_history(self) -> None:
        first = self._commit()
        second = self._commit(_update(key="ols", value={"canonical": "OLS"}))
        rolled_back = self.store.rollback(first.version)
        self.assertNotEqual(first.version, rolled_back.version)
        self.assertEqual(second.version, rolled_back.previous_version)
        self.assertEqual(first.version, rolled_back.rollback_from)
        self.assertNotIn("ols", self.store.load()["glossary"])
        self.assertTrue(self.store.verify_snapshot(first.version))
        self.assertTrue(self.store.verify_snapshot(second.version))

    def test_corrupt_current_snapshot_recovers_previous_valid_version(self) -> None:
        first = self._commit()
        second = self._commit(_update(key="ols", value={"canonical": "OLS"}))
        second_path = self.store.versions_dir / f"{second.version}.json"
        second_path.write_text("{broken", encoding="utf-8")
        recovered = self.store.load_snapshot()
        self.assertIsNotNone(recovered)
        self.assertEqual(first.version, recovered.version)
        head = json.loads(self.store.head_path.read_text("utf-8"))
        self.assertEqual(first.version, head["version"])

    def test_corrupt_head_is_rebuilt_from_latest_hash_valid_snapshot(self) -> None:
        snapshot = self._commit()
        self.store.head_path.write_text("not json", encoding="utf-8")
        recovered = self.store.recover()
        self.assertIsNotNone(recovered)
        self.assertEqual(snapshot.version, recovered.version)
        self.assertEqual(snapshot.version, self.store.current_version)

    def test_hash_tampering_is_detected_and_all_corruption_fails_closed(self) -> None:
        snapshot = self._commit()
        path = self.store.versions_dir / f"{snapshot.version}.json"
        document = json.loads(path.read_text("utf-8"))
        document["payload"]["glossary"]["vif"]["value"]["canonical"] = "tampered"
        path.write_text(json.dumps(document), encoding="utf-8")
        self.assertFalse(self.store.verify_snapshot(snapshot.version))
        with self.assertRaises(MemoryCorruptionError):
            self.store.load()

    def test_cross_process_lock_has_bounded_wait(self) -> None:
        context = multiprocessing.get_context("spawn" if os.name == "nt" else "fork")
        acquired = context.Event()
        release = context.Event()
        process = context.Process(
            target=_hold_lock,
            args=(str(self.store.lock_path), acquired, release),
        )
        process.start()
        try:
            self.assertTrue(acquired.wait(2.0))
            competing = CourseMemoryStore(self.root, self.store.course_id, lock_timeout=0.05)
            with self.assertRaises(CrossProcessLockError):
                with competing.lock():
                    pass
        finally:
            release.set()
            process.join(3.0)
            if process.is_alive():
                process.terminate()
                process.join()
        self.assertEqual(0, process.exitcode)

    def test_training_quality_gate_accepts_only_grounded_high_quality_samples(self) -> None:
        sample = {
            "sample_type": "sft",
            "task_type": "subtitle_correction",
            "system": "只做最小必要修改",
            "input": "machine leaning",
            "output": "machine learning",
            "chosen": None,
            "rejected": None,
        }
        self._commit(_update(
            section="training_examples", key="sample-1", value=sample,
            source="user_confirmed", confidence=0.99, verification="VERIFIED",
        ))
        self.assertIn("sample-1", self.store.load()["training_examples"])

        for change in ("source", "confidence", "verification", "output"):
            bad = dict(sample)
            source, confidence, verification = "user_confirmed", 0.99, "VERIFIED"
            if change == "source":
                source = "historical_ai"
            elif change == "confidence":
                confidence = 0.80
            elif change == "verification":
                verification = "UNVERIFIED"
            else:
                bad["output"] = bad["input"]
            with self.subTest(change=change), self.assertRaises(MemoryValidationError):
                self.store.propose([_update(
                    section="training_examples", key=f"bad-{change}", value=bad,
                    source=source, confidence=confidence, verification=verification,
                )])

    def test_preference_training_requires_real_distinct_pair(self) -> None:
        sample = {
            "sample_type": "preference",
            "task_type": "terminology",
            "system": None,
            "input": "deep leaning",
            "output": None,
            "chosen": "deep learning",
            "rejected": "deep leaning",
        }
        self._commit(_update(section="training_examples", key="pref-1", value=sample))
        sample["rejected"] = "deep learning"
        with self.assertRaisesRegex(MemoryValidationError, "must differ"):
            self.store.propose([_update(
                section="training_examples", key="pref-2", value=sample
            )])

    def test_fine_tune_provider_is_explicitly_disabled(self) -> None:
        provider = DisabledFineTuneProvider()
        self.assertFalse(provider.enabled)
        with self.assertRaises(FineTuningDisabledError):
            provider.submit(course_id="course", dataset_version="v1", config={})
        with self.assertRaises(FineTuningDisabledError):
            self.store.fine_tune_provider.rollback("model-v1")


if __name__ == "__main__":
    unittest.main()

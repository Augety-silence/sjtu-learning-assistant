from __future__ import annotations
import errno
import os
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sjtu_learning_assistant import recording_audio as audio
from sjtu_learning_assistant import recording_asr_service as service

SOURCE = "sjtu-video:120:340"
RECORD = {"source_id": SOURCE, "course_id": "120", "course_name": "数据库", "title": "Lecture", "term_name": "2026"}
EMPTY = {"status": "empty", "vtt": None, "cue_count": 0}
READY = {"status": "ready", "vtt": "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nExisting source\n", "cue_count": 1}

class FakeRuntime:
    calls = []
    def __init__(self, path, group, **limits):
        self.calls.append((path, group, limits))
    @contextmanager
    def request(self, body, usage, **kwargs):
        yield

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.instances = []
        self.requests = []
        self.responses = [(200, json.dumps({"text": "全文" * 4000}, ensure_ascii=False).encode())]
        self.configuration = SimpleNamespace(asr_model=service.DEFAULT_MODEL, recording_asr_enabled=True, recording_asr_paused=False)
        self.records = [RECORD]
        self.subtitle = lambda source_id: dict(EMPTY)
        self.chunks = 1
        FakeRuntime.calls.clear()
    def tearDown(self):
        for instance in self.instances:
            instance.close()
        self.temp.cleanup()
    def extractor(self, playback, directory):
        source = directory / "source-audio.m4a"
        if not source.exists():
            source.write_bytes(b"fake audio")
        meta = audio.file_metadata(source, 120)
        chunks = []
        for i in range(self.chunks):
            path = directory / f"chunk-{i:03d}.m4a"
            if not path.exists():
                path.write_bytes(b"fake chunk " + str(i).encode())
            chunks.append(audio.file_metadata(path, 120))
        return {"audio": meta, "chunks": chunks}
    def runner(self, **kwargs):
        self.requests.append(kwargs["path"].name)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
    def create(self, **kwargs):
        instance = service.RecordingASRService(settings=lambda: self.configuration,
             discover_sources=lambda: iter(self.records), fetch_subtitles=lambda s: self.subtitle(s),
             fetch_playback=lambda s: {"url": "https://live.sjtu.edu.cn/video.mp4?signed=never_saved"},
             root=self.base / "queue", markdown_root=lambda: self.base / "Knowledge Markdown",
             extractor=self.extractor, request_runner=self.runner, key_provider=lambda: "synthetic-test-key",
             runtime_factory=FakeRuntime, **kwargs)
        self.instances.append(instance)
        return instance
    def wait(self, predicate, timeout=4):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(.01)
        self.fail("worker did not reach expected state")
    def state(self, instance, expected):
        self.wait(lambda: bool(instance.jobs()) and instance.jobs()[0]["state"] == expected)
    def disk(self, instance):
        # Match production readers: keep the observation under the writer's
        # companion lock so Windows cannot open a rename-in-progress target.
        with instance._locked():
            return json.loads((instance._directory(SOURCE) / "job.json").read_text(encoding="utf-8"))
    def put(self, instance, job):
        with instance._locked():
            service._json_write(instance._directory(SOURCE) / "job.json", job)

    def test_full_literal_markdown_and_safe_status(self):
        instance = self.create()
        instance.start(consent=True)
        self.state(instance, "completed")
        job = self.disk(instance)
        text = json.loads((instance._directory(SOURCE) / "asr-result.json").read_text(encoding="utf-8"))["text"]
        markdown = (self.base / "Knowledge Markdown" / job["markdown_relative"]).read_text(encoding="utf-8")
        self.assertIn(text, markdown)
        self.assertEqual(8000, job["text_chars"])
        self.assertIn('timestamps: "none"', markdown)
        self.assertIn('timed: false', markdown)
        self.assertNotIn("00:00", markdown)
        exported = json.dumps(instance.status())
        self.assertNotIn("signed", exported)
        self.assertNotIn("synthetic-test-key", exported)
        self.assertNotIn("signed", (instance._directory(SOURCE) / "job.json").read_text(encoding="utf-8"))
        self.assertEqual(10, FakeRuntime.calls[0][2]["rpm"])
        self.assertEqual(300000, FakeRuntime.calls[0][2]["tpm"])
        self.assertNotIn("synthetic-test-key", FakeRuntime.calls[0][1])

    def test_scan_and_start_are_background_and_consent_gated(self):
        blocked = threading.Event()
        entered = threading.Event()
        instance = self.create()
        def discover():
            entered.set()
            blocked.wait(2)
            return iter(self.records)
        instance.discover_sources = discover
        before = time.monotonic()
        instance.scan()
        self.assertLess(time.monotonic() - before, .5)
        self.assertTrue(entered.wait(1))
        blocked.set()
        self.wait(lambda: bool(instance.jobs()))
        self.assertFalse(self.requests)
        with self.assertRaises(service.RecordingASRError):
            instance.start(consent=False)
        instance.start(consent=True)
        self.state(instance, "completed")

    def test_pause_persists_restart_and_resume(self):
        instance = self.create()
        instance.scan()
        self.wait(lambda: bool(instance.jobs()))
        instance.pause()
        instance.close()
        restarted = self.create()
        self.assertTrue(restarted.status()["paused"])
        self.assertFalse(self.requests)
        restarted.start(consent=True)
        self.state(restarted, "completed")

    def test_pause_drains_current_response_but_stops_next_chunk(self):
        self.chunks = 2
        entered, release = threading.Event(), threading.Event()
        instance = self.create()
        def request(**kwargs):
            self.requests.append(kwargs["path"].name)
            entered.set()
            release.wait(2)
            return 200, b'{"text":"first"}'
        instance.request_runner = request
        instance.start(consent=True)
        self.assertTrue(entered.wait(1))
        instance.pause()
        release.set()
        self.wait(lambda: self.disk(instance)["chunks"][0]["state"] == "completed")
        self.assertEqual(1, len(self.requests))
        instance.close()
        restarted = self.create()
        self.assertTrue(restarted.status()["paused"])
        restarted.resume()
        self.state(restarted, "completed")
        self.assertEqual(2, len(self.requests))

    def test_duplicate_instances_and_process_lock_do_not_duplicate_claim(self):
        self.records = [RECORD, dict(RECORD)]
        entered, release = threading.Event(), threading.Event()
        first = self.create()
        def request(**kwargs):
            self.requests.append(kwargs["path"].name)
            entered.set()
            release.wait(10)
            return 200, b'{"text":"one"}'
        first.request_runner = request
        first.start(consent=True)
        self.assertTrue(entered.wait(1))
        second = self.create()
        second.start(consent=True)
        lock = first.root / "worker.lock"
        code = 'import sys; from sjtu_learning_assistant.recording_asr_service import _lock_fd; f=open(sys.argv[1],"r+b");\ntry: _lock_fd(f.fileno(),blocking=False)\nexcept BlockingIOError: sys.exit(7)\nsys.exit(0)'
        result = subprocess.run([sys.executable, "-c", code, str(lock)], capture_output=True, timeout=10)
        self.assertEqual(7, result.returncode)
        release.set()
        self.state(first, "completed")
        self.assertEqual(1, len(self.requests))
        self.assertEqual(1, first.status()["counts"]["total"])

    def test_raw_response_saved_before_validation(self):
        self.responses = [(200, b"not json paid result")]
        instance = self.create()
        consume = instance._consume_response
        checked = []
        def validate(job, chunk):
            path = instance._directory(SOURCE) / chunk["history"][-1]["response_file"]
            self.assertEqual(b"not json paid result", path.read_bytes())
            checked.append(True)
            consume(job, chunk)
        instance._consume_response = validate
        instance.start(consent=True)
        self.state(instance, "failed")
        self.assertTrue(checked)
        self.assertEqual("invalid_paid_response", instance.jobs()[0]["error"])
        self.assertEqual(1, len(self.requests))

    def test_publication_error_reuses_paid_text_without_key_or_network(self):
        instance = self.create()
        publish = instance._publish
        def unavailable(job):
            raise OSError("disk unavailable")
        instance._publish = unavailable
        instance.start(consent=True)
        self.state(instance, "publication_error")
        instance.key_provider = lambda: self.fail("must not read key for local retry")
        instance._publish = publish
        instance.retry(SOURCE)
        self.state(instance, "completed")
        self.assertEqual(1, len(self.requests))
        self.assertEqual(1, self.disk(instance)["attempts"])

    def test_failed_retries_are_bounded_across_scan_and_restart(self):
        self.responses = [(500, b"failed")] * 3
        instance = self.create()
        instance.start(consent=True)
        self.state(instance, "failed")
        for expected in (2, 3):
            instance.retry(SOURCE)
            self.wait(lambda: instance.jobs()[0]["state"] == "failed" and self.disk(instance)["request_count"] == expected)
        instance.scan()
        instance.close()
        restarted = self.create()
        with self.assertRaisesRegex(service.RecordingASRError, "lifetime_attempt"):
            restarted.retry(SOURCE)
        self.assertEqual(3, self.disk(restarted)["attempts"])
        self.assertEqual(3, self.disk(restarted)["chunks"][0]["attempts"])
        self.assertEqual(3, len(self.requests))

    def test_402_blocks_account_until_explicit_recharge_retry(self):
        self.responses = [(402, b"payment required"), (200, b'{"text":"recharged"}')]
        instance = self.create()
        instance.start(consent=True)
        self.state(instance, "blocked_account")
        instance.resume()
        instance.scan()
        time.sleep(.05)
        self.assertTrue(instance.status()["account_blocked"])
        self.assertEqual(1, len(self.requests))
        instance.retry(SOURCE)
        self.state(instance, "completed")
        self.assertFalse(instance.status()["account_blocked"])
        self.assertEqual(2, self.disk(instance)["attempts"])

    def test_unknown_never_auto_resends_and_requires_confirmation(self):
        self.responses = [httpx.ReadTimeout("private upstream url"), (200, b'{"text":"manual"}')]
        instance = self.create()
        instance.start(consent=True)
        self.state(instance, "outcome_unknown")
        instance.scan()
        instance.close()
        restarted = self.create()
        time.sleep(.05)
        self.assertEqual(1, len(self.requests))
        with self.assertRaisesRegex(service.RecordingASRError, "confirm_unknown"):
            restarted.retry(SOURCE)
        restarted.retry(SOURCE, confirm_unknown=True)
        self.state(restarted, "completed")
        self.assertEqual(2, len(self.requests))
        self.assertNotIn("private", json.dumps(restarted.status()))

    def test_partial_chunks_reuse_completed_chunk(self):
        self.chunks = 2
        self.responses = [(200, b'{"text":"first"}'), (402, b"blocked"), (200, b'{"text":"second"}')]
        instance = self.create()
        instance.start(consent=True)
        self.state(instance, "blocked_account")
        first = self.disk(instance)["chunks"][0]
        instance.retry(SOURCE)
        self.state(instance, "completed")
        self.assertEqual(first["text_sha256"], self.disk(instance)["chunks"][0]["text_sha256"])
        self.assertEqual(["chunk-000.m4a", "chunk-001.m4a", "chunk-001.m4a"], self.requests)
        result = json.loads((instance._directory(SOURCE) / "asr-result.json").read_text(encoding="utf-8"))
        self.assertEqual("first\nsecond", result["text"])

    def test_new_subtitle_before_paid_request_skips(self):
        instance = self.create()
        calls = []
        def subtitles(source_id):
            calls.append(True)
            return EMPTY if len(calls) == 1 else READY
        self.subtitle = subtitles
        instance.start(consent=True)
        self.state(instance, "skipped_source_text")
        self.assertFalse(self.requests)
        self.assertEqual(0, self.disk(instance)["attempts"])

    def test_source_text_not_failed_summary_is_boundary(self):
        self.records = [dict(RECORD, source_text="cached original", summary_status="failed")]
        instance = self.create()
        instance.start(consent=True)
        self.state(instance, "skipped_source_text")
        self.assertFalse(self.requests)

    def test_user_edit_is_protected_on_local_retry(self):
        instance = self.create()
        instance.start(consent=True)
        self.state(instance, "completed")
        job = self.disk(instance)
        path = self.base / "Knowledge Markdown" / job["markdown_relative"]
        path.write_text("USER EDIT keep me", encoding="utf-8")
        job["state"] = "publication_error"
        self.put(instance, job)
        instance.retry(SOURCE)
        self.state(instance, "user_edit_protected")
        self.assertEqual("USER EDIT keep me", path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(self.requests))

    def test_job_errors_are_isolated_and_discovery_failure_visible(self):
        bad = dict(RECORD, source_id="sjtu-video:120:341")
        self.records = [bad, RECORD]
        instance = self.create()
        def subtitles(source):
            if source.endswith("341"):
                raise RuntimeError("private signed URL")
            return EMPTY
        self.subtitle = subtitles
        instance.start(consent=True)
        self.wait(lambda: instance.status()["counts"].get("completed") == 1)
        self.assertEqual(1, instance.status()["counts"].get("failed"))
        def broken():
            raise RuntimeError("private discovery error")
        instance.discover_sources = broken
        instance.scan()
        self.wait(lambda: instance.status()["discovery_error"] == "source_discovery_failed")
        self.assertEqual("error", instance.status()["state"])
        self.assertNotIn("private", json.dumps(instance.status()))

    def test_crash_after_raw_sidecar_reuses_response_no_request(self):
        instance = self.create()
        instance.scan()
        self.wait(lambda: bool(instance.jobs()))
        instance.close()
        job = self.disk(instance)
        directory = instance._directory(SOURCE)
        prepared = self.extractor({}, directory)
        raw = b'{"text":"retained paid result"}'
        attempt = {"attempt": 1, "response_file": "chunk-000-attempt-1.response", "outcome": "sending"}
        service._atomic(directory / attempt["response_file"], raw, immutable=True)
        sidecar = {**attempt, "http_status": 200, "response_sha256": hashlib.sha256(raw).hexdigest(), "outcome": "response_saved"}
        service._json_write(directory / (attempt["response_file"] + ".json"), sidecar)
        job.update(state="transcribing", model=service.DEFAULT_MODEL, attempts=1, request_count=1, audio=prepared["audio"],
                   chunks=[{**prepared["chunks"][0], "index": 0, "attempts": 1, "history": [attempt], "state": "sending"}])
        self.put(instance, job)
        with instance._locked():
            manifest = instance._manifest()
            manifest.update(consent=True, paused=False)
            instance._put_manifest(manifest)
        restarted = self.create()
        self.state(restarted, "completed")
        self.assertFalse(self.requests)
        self.assertEqual(1, self.disk(restarted)["attempts"])

    def test_local_saved_success_retries_even_at_exhausted_budget(self):
        instance = self.create()
        instance.scan()
        self.wait(lambda: bool(instance.jobs()))
        instance.close()
        job = self.disk(instance)
        directory = instance._directory(SOURCE)
        prepared = self.extractor({}, directory)
        raw = b'{"text":"paid success retained"}'
        attempt = {"attempt": 3, "response_file": "chunk-000-attempt-3.response", "outcome": "outcome_unknown"}
        service._atomic(directory / attempt["response_file"], raw)
        sidecar = {**attempt, "http_status": 200, "response_sha256": hashlib.sha256(raw).hexdigest()}
        service._json_write(directory / (attempt["response_file"] + ".json"), sidecar)
        job.update(state="outcome_unknown", model=service.DEFAULT_MODEL, attempts=3, request_count=3, audio=prepared["audio"],
                   chunks=[{**prepared["chunks"][0], "index": 0, "attempts": 3, "history": [attempt], "state": "outcome_unknown"}])
        self.put(instance, job)
        restarted = self.create()
        restarted.start(consent=True)
        time.sleep(.03)
        self.assertFalse(self.requests)
        restarted.retry(SOURCE)
        self.state(restarted, "completed")
        self.assertEqual(3, self.disk(restarted)["attempts"])
        self.assertFalse(self.requests)

    def test_pause_while_waiting_on_rate_quota_keeps_job_queued(self):
        entered = threading.Event()
        class WaitingRuntime(FakeRuntime):
            @contextmanager
            def request(self, body, usage, **kwargs):
                entered.set()
                while not kwargs["cancelled"]():
                    time.sleep(.005)
                raise RuntimeError("queue cancelled")
                yield
        instance = self.create()
        instance.runtime_factory = WaitingRuntime
        instance.start(consent=True)
        self.assertTrue(entered.wait(1))
        instance.pause()
        self.state(instance, "queued")
        self.assertEqual(0, self.disk(instance)["attempts"])
        self.assertFalse(self.requests)
        instance.runtime_factory = FakeRuntime
        instance.resume()
        self.state(instance, "completed")

    def test_import_rejects_unknown_proof_without_credentials(self):
        source = self.base / "bad-import"
        source.mkdir()
        (source / "job.json").write_text('{}', encoding="utf-8")
        instance = self.create()
        with self.assertRaisesRegex(service.RecordingASRError, "recovery_identity"):
            instance.import_completed_checkpoint(source)
        self.assertFalse(self.requests)

    def test_paid_audio_tamper_does_not_send_again(self):
        self.responses = [(500, b"failed")]
        instance = self.create()
        instance.start(consent=True)
        self.state(instance, "failed")
        chunk = self.disk(instance)["chunks"][0]
        (instance._directory(SOURCE) / chunk["file"]).write_bytes(b"user modified audio")
        instance.retry(SOURCE)
        self.state(instance, "failed")
        self.assertEqual(1, len(self.requests))
        self.assertEqual("retained_audio_hash_mismatch", instance.jobs()[0]["error"])

    def test_unavailable_subtitle_check_is_not_evidence_of_absence(self):
        self.subtitle = lambda s: {"status": "unavailable", "vtt": None}
        instance = self.create()
        instance.start(consent=True)
        self.state(instance, "failed")
        self.assertEqual("subtitle_check_inconclusive", instance.jobs()[0]["error"])
        self.assertFalse(self.requests)

    def test_synthetic_paid_recovery_import_is_zero_cost_and_idempotent(self):
        backup = self.generic_import_fixture()
        original_hash = hashlib.sha256((backup / "job.json").read_bytes()).hexdigest()
        instance = self.create()
        instance.key_provider = lambda: self.fail("recovery must never read credentials")
        result = instance.import_completed_checkpoint(backup)
        self.assertEqual("completed", result["state"])
        self.assertEqual(len("Retained paid full lecture"), result["text_chars"])
        self.assertEqual(2, result["attempts"])
        self.assertEqual(2, result["request_count"])
        self.assertEqual(2, instance.import_completed_checkpoint(backup)["attempts"])
        job = self.disk(instance)
        self.assertEqual([402, 200], [a["http_status"] for a in job["chunks"][0]["history"]])
        self.assertEqual(original_hash, hashlib.sha256((backup / "job.json").read_bytes()).hexdigest())
        self.assertFalse(self.requests)

    def test_pause_between_claim_and_source_check_releases_claim(self):
        instance = self.create()
        instance.scan()
        self.wait(lambda: bool(instance.jobs()))
        job = self.disk(instance)
        job["state"] = "checking_source"
        self.put(instance, job)
        instance._process(job)
        self.assertEqual("queued", self.disk(instance)["state"])
        self.assertFalse(self.requests)
        instance.start(consent=True)
        self.state(instance, "completed")

    def test_immutable_publication_preserves_concurrent_editor_file(self):
        path = self.base / "race.md"
        def editor_race(temporary, destination):
            destination.write_bytes(b"USER EDIT raced publication")
            raise FileExistsError()
        with patch.object(service.os, "link", side_effect=editor_race):
            with self.assertRaisesRegex(service.RecordingASRError, "existing_checkpoint_conflict"):
                service._atomic(path, b"generated", immutable=True)
        self.assertEqual(b"USER EDIT raced publication", path.read_bytes())

    def test_constructor_settings_override_old_running_manifest(self):
        instance = self.create()
        instance.scan()
        self.wait(lambda: bool(instance.jobs()))
        instance.close()
        with instance._locked():
            manifest = instance._manifest()
            manifest.update(consent=True, paused=False)
            instance._put_manifest(manifest)
        self.configuration.recording_asr_enabled = False
        restarted = self.create()
        self.assertIsNone(restarted._thread)
        self.assertFalse(restarted.status()["enabled"])
        self.assertTrue(restarted.status()["paused"])
        with self.assertRaisesRegex(service.RecordingASRError, "disabled_in_settings"):
            restarted.resume()
        self.configuration.recording_asr_enabled = True
        self.configuration.recording_asr_paused = True
        again = self.create()
        self.assertIsNone(again._thread)
        self.assertTrue(again.status()["paused"])
        self.assertFalse(self.requests)

    def test_settings_changed_and_notify_never_implicitly_resume(self):
        instance = self.create()
        instance.start(consent=True)
        self.state(instance, "completed")
        count = self.disk(instance)["request_count"]
        self.configuration.asr_endpoint = "https://another.example.test/audio/transcriptions"
        instance.settings_changed()
        instance.notify(settings_changed=True)
        instance.notify()
        self.assertTrue(instance.status()["paused"])
        self.assertEqual(count, self.disk(instance)["request_count"])
        self.assertEqual(1, len(self.requests))

    def test_markdown_root_pinned_before_fetch_and_preserved_on_archive_change(self):
        first = self.base / "original" / "Knowledge Markdown"
        second = self.base / "changed" / "Knowledge Markdown"
        instance = self.create()
        instance.markdown_root = lambda: first
        def subtitles(source):
            self.assertEqual(str(first), self.disk(instance)["markdown_root"])
            instance.markdown_root = lambda: second
            return EMPTY
        self.subtitle = subtitles
        instance.start(consent=True)
        self.state(instance, "completed")
        job = self.disk(instance)
        self.assertTrue((first / job["markdown_relative"]).exists())
        self.assertFalse(second.exists())
        instance.settings_changed()
        self.assertEqual(str(first), self.disk(instance)["markdown_root"])

    def test_configured_endpoint_reaches_injected_request_runner(self):
        endpoint = "https://speech.example.test/v1/audio/transcriptions"
        self.configuration.asr_endpoint = endpoint
        instance = self.create()
        original = instance.request_runner
        endpoints = []
        def request(**kwargs):
            endpoints.append(kwargs["endpoint"])
            return original(**kwargs)
        instance.request_runner = request
        instance.start(consent=True)
        self.state(instance, "completed")
        self.assertEqual([endpoint], endpoints)
        self.assertEqual(endpoint, self.disk(instance)["asr_endpoint"])

    def test_close_requires_paid_response_drain_and_holds_worker_lock(self):
        entered, release = threading.Event(), threading.Event()
        instance = self.create()
        def request(**kwargs):
            self.requests.append(kwargs["path"].name)
            entered.set()
            release.wait(3)
            return 200, b'{"text":"drained success"}'
        instance.request_runner = request
        instance.start(consent=True)
        self.assertTrue(entered.wait(1))
        closed = instance.close(timeout=.01)
        self.assertTrue(closed["draining"])
        self.assertFalse(closed["safe_to_exit"])
        self.assertFalse(instance._thread.daemon)
        self.assertFalse(instance.wait_closed(timeout=.01))
        self.assertEqual("draining", instance.status()["state"])
        with (instance.root / "worker.lock").open("r+b") as lock:
            with self.assertRaises(BlockingIOError):
                service._lock_fd(lock.fileno(), blocking=False)
        release.set()
        self.assertTrue(instance.wait_closed(timeout=None))
        self.assertEqual("completed", self.disk(instance)["state"])
        self.assertTrue((instance._directory(SOURCE) / "asr-result.json").exists())
        self.assertEqual("closed", instance.status()["state"])
        self.assertTrue(instance.status()["safe_to_exit"])

    def test_unsafe_drain_failure_vetoes_process_exit(self):
        instance = self.create()
        instance._drain_error = "checkpoint_save_failed"
        self.assertEqual("drain_error", instance.close()["state"])
        self.assertFalse(instance.wait_closed(timeout=None))
        self.assertFalse(instance.status()["safe_to_exit"])

    def test_generic_import_uses_manifest_identity_and_hashes(self):
        source = self.base / "other-backup"
        source.mkdir()
        identity = "sjtu-video:120:340"
        model = "Custom/Configured-ASR"
        audio_bytes = b"synthetic generic import audio"
        result = {"source_id": identity, "model": model, "text": "Other original lecture全文"}
        paid = {"text": result["text"]}
        result_bytes = json.dumps(result, ensure_ascii=False).encode()
        response_bytes = json.dumps(paid, ensure_ascii=False).encode()
        (source / "lecture.m4a").write_bytes(audio_bytes)
        (source / "success.json").write_bytes(result_bytes)
        (source / "attempt-1.response").write_bytes(response_bytes)
        manifest = {"source_id": identity, "course": "Other course", "recording_id": "340",
                    "asr_attempts": 1, "asr_http_status": 200, "asr_model": model,
                    "audio_file": "lecture.m4a", "audio_bytes": len(audio_bytes), "duration_seconds": 42,
                    "audio_sha256": hashlib.sha256(audio_bytes).hexdigest(),
                    "asr_result_file": "success.json", "asr_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
                    "asr_response_file": "attempt-1.response", "asr_response_sha256": hashlib.sha256(response_bytes).hexdigest(),
                    "text_chars": len(result["text"]), "asr_attempt_history": []}
        (source / "job.json").write_text(json.dumps(manifest), encoding="utf-8")
        instance = self.create()
        instance.key_provider = lambda: self.fail("no credentials during import")
        imported = instance.import_completed_checkpoint(source)
        self.assertEqual(identity, imported["source_id"])
        self.assertEqual("completed", imported["state"])
        self.assertEqual(1, imported["attempts"])
        self.assertEqual(1, instance.import_completed_checkpoint(source)["request_count"])
        self.assertFalse(self.requests)
        self.assertEqual(audio_bytes, (source / "lecture.m4a").read_bytes())
        manifest["asr_result_sha256"] = "0" * 64
        (source / "job.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(service.RecordingASRError, "hash_mismatch"):
            instance.import_completed_checkpoint(source)

    def test_native_http_runner_uses_validated_saved_endpoint_without_redirects(self):
        path = self.base / "fake-native-audio.m4a"
        path.write_bytes(b"audio")
        endpoint = "https://saved.example.test/audio/transcriptions"
        seen, clients = [], []
        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, content=b"raw paid unvalidated body")
        client = httpx.Client
        def injected(**kwargs):
            clients.append(kwargs)
            return client(transport=httpx.MockTransport(handler), **kwargs)
        with patch.object(service.httpx, "Client", side_effect=injected):
            status, raw = service._request_audio(path=path, api_key="synthetic-test-key", endpoint=endpoint, model=service.DEFAULT_MODEL)
        self.assertEqual([endpoint], seen)
        self.assertEqual(200, status)
        self.assertEqual(b"raw paid unvalidated body", raw)
        self.assertFalse(clients[0]["follow_redirects"])
        self.assertGreaterEqual(clients[0]["timeout"].read, 900)
        with self.assertRaises(Exception):
            service._request_audio(path=path, api_key="synthetic-test-key", endpoint="https://saved.example.test/audio?api_key=unsafe", model=service.DEFAULT_MODEL)

    def generic_import_fixture(self):
        source = self.base / "republish-backup"
        source.mkdir(exist_ok=True)
        data = b"synthetic retained source audio"
        result = {"source_id": SOURCE, "model": service.DEFAULT_MODEL, "text": "Retained paid full lecture"}
        result_bytes = json.dumps(result).encode()
        response_bytes = json.dumps({"text": result["text"]}).encode()
        prior = b"HTTP 402 retained response"
        (source / "source-audio.m4a").write_bytes(data)
        (source / "asr-result.json").write_bytes(result_bytes)
        (source / "asr-attempt-1.response").write_bytes(prior)
        (source / "asr-attempt-2.response").write_bytes(response_bytes)
        manifest = {"source_id": SOURCE, "course": "Course", "asr_attempts": 2, "asr_http_status": 200,
                    "asr_model": service.DEFAULT_MODEL, "audio_bytes": len(data), "duration_seconds": 42,
                    "audio_sha256": hashlib.sha256(data).hexdigest(), "text_chars": len(result["text"]),
                    "asr_result_sha256": hashlib.sha256(result_bytes).hexdigest(),
                    "asr_response_sha256": hashlib.sha256(response_bytes).hexdigest(),
                    "asr_attempt_history": [{"attempt": 1, "http_status": 402, "response_file": "asr-attempt-1.response"}]}
        (source / "job.json").write_text(json.dumps(manifest), encoding="utf-8")
        return source

    def test_matching_import_publication_error_republishes_without_network(self):
        source = self.generic_import_fixture()
        instance = self.create()
        publish = instance._publish
        instance._publish = lambda job: (_ for _ in ()).throw(OSError("local disk failure"))
        first = instance.import_completed_checkpoint(source)
        self.assertEqual("publication_error", first["state"])
        before = self.disk(instance)
        instance._publish = publish
        instance.key_provider = lambda: self.fail("local republish must not read credentials")
        instance.request_runner = lambda **kw: self.fail("local republish must not request ASR")
        second = instance.import_completed_checkpoint(source)
        self.assertEqual("completed", second["state"])
        after = self.disk(instance)
        self.assertEqual(2, after["attempts"])
        self.assertEqual(2, after["request_count"])
        self.assertEqual(before["chunks"][0]["history"], after["chunks"][0]["history"])
        self.assertEqual("completed", instance.import_completed_checkpoint(source)["state"])
        self.assertFalse(self.requests)

    def test_matching_import_never_overwrites_user_modified_markdown(self):
        source = self.generic_import_fixture()
        instance = self.create()
        self.assertEqual("completed", instance.import_completed_checkpoint(source)["state"])
        job = self.disk(instance)
        path = Path(job["markdown_root"]) / job["markdown_relative"]
        path.write_text("USER EDIT preserve this note", encoding="utf-8")
        job["state"] = "user_edit_protected"
        self.put(instance, job)
        self.assertEqual("user_edit_protected", instance.import_completed_checkpoint(source)["state"])
        self.assertEqual("USER EDIT preserve this note", path.read_text(encoding="utf-8"))
        self.assertEqual(2, self.disk(instance)["request_count"])
        self.assertFalse(self.requests)

    def test_parallel_import_republish_rejects_active_claim(self):
        source = self.generic_import_fixture()
        instance = self.create()
        entered, release = threading.Event(), threading.Event()
        publish = instance._publish
        def blocked(job):
            entered.set()
            release.wait(2)
            publish(job)
        instance._publish = blocked
        results = []
        thread = threading.Thread(target=lambda: results.append(instance.import_completed_checkpoint(source)))
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(service.RecordingASRError, "job_busy"):
                instance.import_completed_checkpoint(source)
            self.assertEqual(2, self.disk(instance)["request_count"])
        finally:
            release.set()
            thread.join(3)
        self.assertEqual("completed", results[0]["state"])
        self.assertFalse(self.requests)

    def test_publication_root_registry_only_registers_configured_callback(self):
        source = self.generic_import_fixture()
        instance = self.create()
        self.assertEqual("completed", instance.import_completed_checkpoint(source)["state"])
        job = self.disk(instance)
        original = job["markdown_root"]
        with instance._locked():
            roots = instance._manifest()["authorized_markdown_roots"]
        self.assertEqual([original], roots)
        unknown = self.base / "unauthorized" / "Knowledge Markdown"
        job.update(state="publication_error", markdown_root=str(unknown))
        self.put(instance, job)
        with self.assertRaisesRegex(service.RecordingASRError, "unauthorized_markdown_root"):
            instance.import_completed_checkpoint(source)
        with instance._locked():
            self.assertNotIn(str(unknown), instance._manifest()["authorized_markdown_roots"])
        self.assertFalse(unknown.exists())
        job["markdown_root"] = original
        self.put(instance, job)
        changed = self.base / "new-configured" / "Knowledge Markdown"
        instance.markdown_root = lambda: changed
        self.assertEqual("completed", instance.import_completed_checkpoint(source)["state"])
        with instance._locked():
            self.assertEqual([original, str(changed)], instance._manifest()["authorized_markdown_roots"])
        self.assertEqual(original, self.disk(instance)["markdown_root"])
        self.assertFalse(changed.exists())

    def test_disk_observation_waits_for_cross_instance_state_writer(self):
        writer = self.create()
        writer.scan()
        self.wait(lambda: bool(writer.jobs()))
        reader = self.create()
        job = self.disk(writer)
        attempted, opened, done = threading.Event(), threading.Event(), threading.Event()
        results, errors = [], []
        native_lock = service._lock_fd
        native_read = Path.read_text
        def observe():
            try:
                results.append(self.disk(reader))
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()
        thread = threading.Thread(target=observe)
        def trace_lock(fd, *, blocking=True):
            if threading.current_thread() is thread:
                attempted.set()
            return native_lock(fd, blocking=blocking)
        def trace_read(path, *args, **kwargs):
            if path == writer._directory(SOURCE) / "job.json":
                opened.set()
            return native_read(path, *args, **kwargs)
        with patch.object(service, "_lock_fd", side_effect=trace_lock), patch.object(Path, "read_text", new=trace_read):
            with writer._locked():
                thread.start()
                self.assertTrue(attempted.wait(1))
                self.assertFalse(opened.wait(.05), "reader opened job.json while state writer held its lock")
                self.assertFalse(done.is_set())
                job["title"] = "Snapshot written under state lock"
                service._json_write(writer._directory(SOURCE) / "job.json", job)
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertFalse(errors)
        self.assertTrue(opened.is_set())
        self.assertEqual("Snapshot written under state lock", results[0]["title"])
        self.assertFalse(self.requests)

class AudioSafetyTests(unittest.TestCase):
    def test_packaged_ffmpeg_resolution_without_system_binary(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw).resolve() / "packaged-ffmpeg"
            path.write_bytes(b"executable")
            path.chmod(0o700)
            with patch.object(audio.media_features.shutil, "which", return_value=None), patch.dict(sys.modules, {"imageio_ffmpeg": SimpleNamespace(get_ffmpeg_exe=lambda: str(path))}):
                self.assertEqual(str(path), audio.ffmpeg_executable())
    def test_media_urls_are_school_only_direct_mp4(self):
        self.assertEqual("https://live.sjtu.edu.cn/a.mp4?key=x", audio.validate_media_url("https://live.sjtu.edu.cn/a.mp4?key=x"))
        for value in ("https://evil.test/a.mp4", "file:///a.mp4", "https://live.sjtu.edu.cn/a.m3u8", "https://live.sjtu.edu.cn:444/a.mp4", "https://u:p@live.sjtu.edu.cn/a.mp4", "https://live.sjtu.edu.cn/a.mp4#ref"):
            with self.assertRaises(audio.RecordingAudioError):
                audio.validate_media_url(value)
    def test_byte_duration_and_regular_file_caps(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            path = root / "audio.m4a"
            path.write_bytes(b"12345")
            with self.assertRaises(audio.RecordingAudioError):
                audio.file_metadata(path, 1, max_bytes=4)
            with self.assertRaises(audio.RecordingAudioError):
                audio.file_metadata(path, 3601, max_seconds=3600)
    def test_symlink_audio_is_rejected_when_platform_supports_symlinks(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            path = root / "audio.m4a"
            path.write_bytes(b"12345")
            linked = root / "link.m4a"
            try:
                linked.symlink_to(path)
            except (NotImplementedError, OSError) as exc:
                if isinstance(exc, NotImplementedError) or (os.name == "nt" and (
                        getattr(exc, "winerror", None) == 1314 or exc.errno in (errno.EPERM, errno.EACCES))):
                    self.skipTest("platform does not grant symlink creation")
                raise
            with self.assertRaises(audio.RecordingAudioError):
                audio.file_metadata(linked, 1)


    def test_remote_redirect_is_rejected_without_following(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            seen = []
            def redirect(request):
                seen.append(str(request.url))
                return httpx.Response(302, headers={"Location": "https://evil.test/private.mp4"})
            client = httpx.Client
            with patch.object(audio.httpx, "Client", side_effect=lambda **kw: client(transport=httpx.MockTransport(redirect), **kw)):
                with self.assertRaises(audio.RecordingAudioError):
                    audio._download("https://live.sjtu.edu.cn/video.mp4?private-key=x", root / "source.mp4")
            self.assertEqual(1, len(seen))
            self.assertFalse((root / "source.mp4").exists())

    def test_persistent_chunks_cut_on_duration_or_decimal_byte_cap(self):
        self.assertEqual(50_000_000, audio.MAX_CHUNK_BYTES)
        for duration, limit in ((4000, 50_000_000), (100, 4)):
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw).resolve()
                source = root / "source-audio.m4a"
                source.write_bytes(b"source audio")
                def probe(path):
                    return (duration if path == source else 500 if path.name == "chunk-001.m4a" else min(3500, duration)), "aac"
                def convert(source, destination, **kw):
                    destination.write_bytes(b"c")
                    return kw["expected_duration"]
                with patch.object(audio, "MAX_CHUNK_BYTES", limit), patch.object(audio, "probe_audio", side_effect=probe), patch.object(audio, "_convert", side_effect=convert):
                    prepared = audio.prepare_recording_audio({}, root)
                    self.assertEqual(2 if duration > 3600 else 1, len(prepared["chunks"]))
                    self.assertTrue(all((root / c["file"]).is_file() for c in prepared["chunks"]))
                    again = audio.prepare_recording_audio({}, root)
                    self.assertEqual([c["sha256"] for c in prepared["chunks"]], [c["sha256"] for c in again["chunks"]])

    def test_truncated_conversion_never_becomes_source(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            source, destination = root / "source.mp4", root / "audio.m4a"
            source.write_bytes(b"fake mp4")
            def run(arguments):
                Path(arguments[-1]).write_bytes(b"truncated")
                return SimpleNamespace(returncode=0)
            with patch.object(audio, "_run", side_effect=run), patch.object(audio, "probe_audio", return_value=(10, "aac")):
                with self.assertRaisesRegex(audio.RecordingAudioError, "duration_mismatch"):
                    audio._convert(source, destination, copy=True, expected_duration=500)
            self.assertFalse(destination.exists())
            self.assertTrue(list(root.glob("audio.preparing-*.m4a")))

class PortableLockTests(unittest.TestCase):
    def test_windows_byte_range_lock_is_exclusive_and_unlocks_byte_zero(self):
        calls, held = [], [False]
        def locking(fd, mode, length):
            calls.append((mode, length, os.lseek(fd, 0, os.SEEK_CUR)))
            if mode == 2:
                held[0] = False
            elif held[0]:
                raise OSError(errno.EACCES, "already locked")
            else:
                held[0] = True
        native = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw).resolve() / "worker.lock"
            with path.open("w+b") as file, patch.object(service, "_windows_locks", native):
                service._lock_fd(file.fileno(), blocking=False)
                self.assertEqual(1, path.stat().st_size)
                with self.assertRaises(BlockingIOError):
                    service._lock_fd(file.fileno(), blocking=False)
                service._unlock_fd(file.fileno())
                service._lock_fd(file.fileno(), blocking=False)
                service._unlock_fd(file.fileno())
        self.assertTrue(all(length == 1 and offset == 0 for _, length, offset in calls))

    def test_windows_blocking_lock_retries_contention_not_unrelated_errors(self):
        busy, calls = [True], []
        def locking(fd, mode, length):
            calls.append(mode)
            if busy[0]:
                raise OSError(errno.EACCES, "busy")
        native = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw).resolve() / "state.lock"
            with path.open("w+b") as file, patch.object(service, "_windows_locks", native), patch.object(service.time, "sleep", side_effect=lambda _: busy.__setitem__(0, False)) as sleep:
                service._lock_fd(file.fileno())
                self.assertEqual([1, 1], calls)
                sleep.assert_called_once_with(.01)
                native.locking = lambda *args: (_ for _ in ()).throw(OSError(errno.EIO, "device failure"))
                with self.assertRaises(OSError) as failure:
                    service._lock_fd(file.fileno())
                self.assertEqual(errno.EIO, failure.exception.errno)

    def test_windows_atomic_write_keeps_file_fsync_without_directory_open(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw).resolve() / "paid.response"
            body = "完整正文\r\n".encode("utf-8")
            fsync = service.os.fsync
            with patch.object(service.os, "name", "nt"), patch.object(service.os, "open", side_effect=AssertionError("Windows must not open a directory")), patch.object(service.os, "fsync", wraps=fsync) as sync:
                service._atomic(path, body, immutable=True)
                sync.assert_called_once()
            self.assertEqual(body, path.read_bytes())

    def test_windows_local_rename_retries_sharing_violation_only(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw).resolve() / "job.json"
            error = PermissionError(errno.EACCES, "sharing violation")
            error.winerror = 32
            calls = []
            replace = service.os.replace
            def transient(source, destination):
                calls.append(True)
                if len(calls) == 1:
                    raise error
                replace(source, destination)
            with patch.object(service.os, "name", "nt"), patch.object(service.os, "replace", side_effect=transient), patch.object(service.time, "sleep") as sleep:
                service._atomic(path, b"checkpoint")
                sleep.assert_called_once_with(.01)
            self.assertEqual(2, len(calls))
            self.assertEqual(b"checkpoint", path.read_bytes())

if __name__ == "__main__":
    unittest.main()

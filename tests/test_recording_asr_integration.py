"""Offline integration only: no production keys, audio, uploads or DB writes."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from desktop_app import DesktopBridge
from sjtu_learning_assistant.dashboard_service import DashboardService, DashboardError
from sjtu_learning_assistant.local_settings import LocalSettings, SettingsStore, SettingsError, RECORDING_ASR_KEYS


def test_new_defaults_and_previous_model_preserved(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    new = store.load(environ=dict())
    assert new.asr_model == "XingChenAGI/XingChenASR-V3.2-Ultra"
    assert new.asr_endpoint == "https://api.siliconflow.cn/v1/audio/transcriptions"
    assert not new.recording_asr_enabled and new.recording_asr_paused
    payload = new.to_dict()
    payload.update(asr_model="Explicit/LegacyModel", asr_key_saved=True, ai_key_saved=True, ai_enabled=True)
    for key in RECORDING_ASR_KEYS:
        payload.pop(key)
    store.path.write_text(json.dumps(payload))
    migrated = store.load(environ=dict())
    assert migrated.asr_model == "Explicit/LegacyModel" and migrated.asr_key_saved
    assert migrated.ai_key_saved and migrated.ai_enabled
    assert not migrated.recording_asr_enabled and migrated.recording_asr_paused
    store.update(dict(theme_mode="dark"))
    assert store.load(environ=dict()).asr_model == "Explicit/LegacyModel"


def test_recording_preferences_strict_and_corruption_no_consent(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save(LocalSettings(recording_asr_enabled=True, recording_asr_paused=False))
    assert store.load(environ=dict()).recording_asr_enabled
    for payload in (dict(recording_asr_enabled=1),dict(recording_asr_paused="false"),dict(recording_asr_scope="all")):
        with pytest.raises(SettingsError): store.update(payload)
    payload = store.load(environ=dict()).to_dict()
    payload.update(theme_mode="invalid")
    store.path.write_text(json.dumps(payload))
    recovered = store.load(environ=dict())
    assert not recovered.recording_asr_enabled and recovered.recording_asr_paused


def test_bridge_consent_and_payloads_strict():
    fake = Mock()
    fake.recording_asr_status.return_value = dict(enabled=False,paused=True)
    fake.recording_asr_start.return_value = dict(enabled=True,paused=False)
    fake.recording_asr_jobs.return_value = dict(items=list())
    fake.recording_asr_retry.return_value = dict(status="queued")
    bridge = DesktopBridge(fake)
    assert bridge.invoke("recording_asr_status",dict()).get("ok")
    for action in ("recording_asr_status","recording_asr_scan","recording_asr_pause","recording_asr_resume"):
        assert not bridge.invoke(action,dict(endpoint="https://unexpected.invalid")).get("ok")
    for payload in (dict(),dict(consent=False),dict(consent=1),dict(consent="true"),dict(consent=True,scope="all")):
        assert not bridge.invoke("recording_asr_start",payload).get("ok")
    fake.recording_asr_start.assert_not_called()
    assert bridge.invoke("recording_asr_start",dict(consent=True)).get("ok")
    for payload in (dict(limit=True),dict(limit=0),dict(limit=501),dict(limit="100")):
        assert not bridge.invoke("recording_asr_jobs",payload).get("ok")
    for payload in (dict(source_id="../audio"),dict(source_id="sjtu-video:1:2",confirm_unknown=1),dict(source_id="sjtu-video:1:2",path="arbitrary")):
        assert not bridge.invoke("recording_asr_retry",payload).get("ok")
    assert bridge.invoke("recording_asr_retry",dict(source_id="sjtu-video:1:2",confirm_unknown=True)).get("ok")
    fake.recording_asr_retry.assert_called_once_with("sjtu-video:1:2",confirm_unknown=True)
    assert not bridge.invoke("settings_update",dict(recording_asr_enabled=True)).get("ok")


def bare_dashboard(tmp_path):
    service = object.__new__(DashboardService)
    service.settings_store = SettingsStore(tmp_path / "settings.json")
    service.settings_store.save(LocalSettings())
    service._archive_root_override = None
    service._effective_settings = service.settings_store.load
    service._recording_asr_instance = Mock()
    service._recording_asr_call = lambda method, *args, **kwargs: getattr(service._recording_asr_instance, method)(*args,**kwargs)
    return service


def test_start_resume_and_pause_preserve_independent_ai_and_budgets(tmp_path):
    service = bare_dashboard(tmp_path)
    with pytest.raises(DashboardError): service.recording_asr_resume()
    with pytest.raises(DashboardError): service.recording_asr_start(consent=False)
    service.recording_asr_start(consent=True)
    service._recording_asr_instance.start.assert_called_once_with(consent=True)
    settings = service.settings_store.load(environ=dict())
    assert settings.recording_asr_enabled and not settings.recording_asr_paused
    assert not settings.ai_enabled and not settings.ai_key_saved
    service.recording_asr_pause()
    assert service.settings_store.load(environ=dict()).recording_asr_paused
    service.recording_asr_resume()
    service._recording_asr_instance.resume.assert_called_once()
    service._recording_asr_instance.reset.assert_not_called()


def test_start_failure_keeps_consent_but_pauses(tmp_path):
    service = bare_dashboard(tmp_path)
    service._recording_asr_instance.start.side_effect = DashboardError("safe failure")
    with pytest.raises(DashboardError): service.recording_asr_start(consent=True)
    settings = service.settings_store.load(environ=dict())
    assert settings.recording_asr_enabled and settings.recording_asr_paused


def test_settings_change_pauses_without_reset(tmp_path):
    service = bare_dashboard(tmp_path)
    service.settings_store.update(dict(recording_asr_enabled=True,recording_asr_paused=False))
    service._notify_recording_asr_settings()
    assert service.settings_store.load(environ=dict()).recording_asr_paused
    service._recording_asr_instance.pause.assert_called_once()
    service._recording_asr_instance.reset.assert_not_called()


def test_discovery_all_courses_and_partial_coverage(tmp_path):
    service = bare_dashboard(tmp_path)
    service.engine = object()
    service._subtitle_courses = lambda: (("1","Current"),("2","Past"),("3","Inaccessible"))
    videos = Mock()
    videos.list_course_videos.side_effect = (
        (dict(source_id="sjtu-video:1:10",title="Current lecture"),),
        (dict(source_id="sjtu-video:2:20",title="Past lecture"),dict(source_id="sjtu-video:1:30",title="Wrong course")),
        RuntimeError("must not expose upstream URL or secret"))
    service._video_service = lambda: videos
    with patch("sjtu_learning_assistant.dashboard_service.transcript_course_metadata",return_value=dict()):
        sources = tuple(service._recording_asr_sources())
    assert tuple(item.get("source_id") for item in sources) == ("sjtu-video:1:10","sjtu-video:2:20")
    assert all(call.kwargs.get("limit") == 10000 for call in videos.list_course_videos.call_args_list)
    coverage = service._recording_asr_coverage
    assert coverage.get("total_courses") == 3 and coverage.get("scanned_courses") == 2
    assert coverage.get("missing_sources") == 1 and coverage.get("status") == "partial"
    assert len(coverage.get("inaccessible_courses")) == 1
    assert "must not expose" not in str(coverage)


def write_catalog_fixture(tmp_path, job_changes=None):
    import hashlib
    from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge
    archive = tmp_path / "archive"
    root = tmp_path / "recording-asr" / "v1"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text(json.dumps(dict(version=1,consent=True,paused=True)))
    identity = "sjtu-video:1:2"
    key = hashlib.sha256(identity.encode()).hexdigest().__getitem__(slice(None,16))
    job_root = root / "jobs" / key
    job_root.mkdir(parents=True)
    relative = "Recordings ASR/Term/Course/Lecture.md"
    output = archive / "Knowledge Markdown" / relative
    output.parent.mkdir(parents=True)
    raw = "---\ntimed: false\ntimestamps: none\n---\n# 原始机器转写\n\n" + "".join("原始全文并未校对。" for _ in range(1200)) + "原始全文结尾"
    output.write_text(raw,encoding="utf-8")
    job = dict(source_id=identity,course_id="1",course_name="Course",term_name="Term",title="Lecture",
        state="completed",model="XingChenAGI/XingChenASR-V3.2-Ultra",markdown_relative=relative,
        markdown_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),text_chars=len(raw),text_sha256=hashlib.sha256(raw.encode()).hexdigest())
    job.update(job_changes or dict())
    (job_root / "job.json").write_text(json.dumps(job))
    catalog = CompiledKnowledge(lambda:SimpleNamespace(archive_root=str(archive)),tmp_path / "local-projects.json",
        recording_asr_root=root,transcript_metadata=lambda:dict())
    return catalog, output, job_root, raw


def test_catalog_raw_fulltext_provenance_no_vtt_or_false_approved(tmp_path):
    catalog, output, job_root, raw = write_catalog_fixture(tmp_path)
    listing = catalog.search(source_id="automatic-recording-asr")
    assert listing.get("count") == 1
    document = listing.get("items").__getitem__(0)
    assert document.get("contract") == "recording-asr-raw-v1"
    assert document.get("review_status") == "unreviewed"
    assert document.get("timed") is False and document.get("timestamps") == "none"
    assert document.get("status_label") == "MD完成/人工未校对"
    assert document.get("provenance").get("reviewed") is False
    page = catalog.read(document.get("id"),max_chars=16000)
    assert page.get("text") == raw and page.get("total_chars") > 5000
    assert not (job_root / "raw.vtt").exists()
    assert catalog.compilation_status().get("classroom_documents") == 0
    assert catalog.search("原始全文结尾").get("count") == 1


def test_catalog_preserves_edits_and_rejects_cross_root_and_identity(tmp_path):
    catalog, output, job_root, raw = write_catalog_fixture(tmp_path)
    output.write_text(raw + "\n人工补充",encoding="utf-8")
    entry = catalog.search().get("items").__getitem__(0)
    assert entry.get("user_modified") is True and entry.get("review_status") == "unreviewed"
    assert output.read_text(encoding="utf-8").endswith("人工补充")
    job_path = job_root / "job.json"
    job = json.loads(job_path.read_text(encoding="utf-8"))
    for changes in (dict(course_id="wrong"),dict(markdown_relative="../outside.md"),dict(markdown_relative="raw.vtt")):
        invalid = dict(job); invalid.update(changes); job_path.write_text(json.dumps(invalid))
        result = catalog.search()
        assert result.get("count") == 0 and result.get("issues")
    assert output.read_text(encoding="utf-8").endswith("人工补充")


def test_recording_source_navigation_merges_only_same_term_identity():
    from sjtu_learning_assistant.knowledge_navigation import navigation_scopes
    docs = (dict(id="slides",source_id="automatic-courses",folder="Term/Course/Slides",term_id="term:1",course_id="1"),
            dict(id="raw",source_id="automatic-recording-asr",folder="Term/Course/录播原始转写",term_id="term:1",course_id="1"))
    scopes = navigation_scopes(docs)
    assert scopes.get("slides") == scopes.get("raw") and scopes.get("raw")
    docs.__getitem__(1).update(course_id="2")
    assert navigation_scopes(docs) == dict()


def test_actual_backend_contract_normalized_without_uploads(tmp_path):
    import threading
    service = bare_dashboard(tmp_path)
    service.local_projects_path = tmp_path / "local-projects.json"
    service._recording_asr_instance = None
    service._client_lock = threading.RLock()
    service._closed = False
    service._recording_asr_coverage = dict(status="not_scanned",total_courses=None,scanned_courses=0,inaccessible_courses=tuple(),missing_sources=0)
    service._recording_asr_sources = lambda: (dict(source_id="sjtu-video:1:2",course_id="1",title="Lecture"),)
    service.video_subtitles = Mock(return_value=dict(status="empty",vtt="",cue_count=0))
    service.video = Mock()
    # Restore the production dispatcher, rather than the bare fake helper.
    service._recording_asr_call = DashboardService._recording_asr_call.__get__(service)
    worker = service._recording_asr_service()
    try:
        status = service.recording_asr_status()
        assert not status.get("enabled") and status.get("paused") and status.get("in_flight") == 0
        assert service.recording_asr_jobs().get("items") == list()
        worker.scan()
        import time
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not worker.jobs(): time.sleep(0.01)
        jobs = service.recording_asr_jobs()
        assert jobs.get("total") == 1 and jobs.get("items").__getitem__(0).get("status") == "queued"
        service.video.assert_not_called()
        service.video_subtitles.assert_not_called()
        assert service.recording_asr_status().get("counts").get("requests") == 0
    finally:
        worker.pause(); worker.close()


def test_error_codes_redact_untrusted_message():
    assert "HTTP 402" in DashboardService._recording_asr_error("asr_http_402")
    assert "重复扣费" in DashboardService._recording_asr_error("confirm_unknown_may_repeat_charge_required")
    assert "secret" not in DashboardService._recording_asr_error("https://unexpected.invalid/path?key=secret")


def test_dashboard_quit_drains_before_clients_or_engine_cleanup(tmp_path):
    import threading
    service = bare_dashboard(tmp_path)
    events = list()
    worker = Mock()
    worker.pause.side_effect = lambda: events.append("pause")
    worker.close.side_effect = lambda: events.append("close_requested")
    worker.wait_closed.side_effect = lambda timeout=None: events.append("paid_response_drained") or True
    service._recording_asr_instance = worker
    service._client_lock = threading.RLock()
    service._closed = False
    service._canvas_client = service._ai_client = service._transcript_service_instance = None
    service._ai_client_config = None
    service._ai_task_lock = threading.Lock()
    service._ai_task_threads = dict()
    service._material_temp = Mock()
    service._academic_temp = Mock()
    service._material_temp.cleanup.side_effect = lambda: events.append("temporary_cleanup")
    service.settings_store.update = Mock(side_effect=OSError("settings disk unavailable"))
    service.close()
    worker.wait_closed.assert_called_once_with(timeout=None)
    assert events == (list(("pause","close_requested","paid_response_drained","temporary_cleanup")))
    service.close()
    worker.close.assert_called_once()


def test_native_quit_guard_drain_and_safe_veto():
    from desktop_app import _install_recording_asr_quit_guard
    class ClosingEvent:
        def __init__(self): self.handlers = list()
        def __iadd__(self, handler): self.handlers.append(handler); return self
    event = ClosingEvent()
    window = SimpleNamespace(events=SimpleNamespace(closing=event))
    service = Mock()
    _install_recording_asr_quit_guard(window, service)
    handler = event.handlers.__getitem__(0)
    service.prepare_recording_asr_quit.return_value = True
    assert handler() is None
    service.prepare_recording_asr_quit.return_value = False
    assert handler() is False
    service.prepare_recording_asr_quit.side_effect = RuntimeError("must not expose details")
    assert handler() is False


def test_explicit_actions_recreate_only_safely_closed_worker(tmp_path):
    import threading
    service = bare_dashboard(tmp_path)
    service._closed = False
    service._client_lock = threading.RLock()
    service.local_projects_path = tmp_path / "local-projects.json"
    service._recording_asr_quit_drained = True
    service._recording_asr_sources = Mock()
    service.video_subtitles = Mock()
    service.video = Mock()
    original = service._recording_asr_instance
    original.status.return_value = dict(state="closed",safe_to_exit=True,drain_error=None)
    replacement = Mock()
    with patch("sjtu_learning_assistant.recording_asr_service.RecordingASRService",return_value=replacement) as factory:
        assert service._recording_asr_service() is original
        factory.assert_not_called()
        assert service._recording_asr_service(restart_closed=True) is replacement
        factory.assert_called_once()
        assert not service._recording_asr_quit_drained
    for state in (dict(state="draining",safe_to_exit=False),dict(state="closed",safe_to_exit=False,drain_error="worker_storage_error")):
        original.status.return_value = state
        service._recording_asr_instance = original
        with patch("sjtu_learning_assistant.recording_asr_service.RecordingASRService") as factory:
            with pytest.raises(DashboardError): service._recording_asr_service(restart_closed=True)
            factory.assert_not_called()
            assert service._recording_asr_instance is original

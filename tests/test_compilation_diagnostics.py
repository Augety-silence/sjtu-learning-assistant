"""Read-only offline snapshots and automatic source-policy integration."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from sjtu_learning_assistant.compilation_diagnostics import diagnose_source, catalog_snapshot, DiagnosticSnapshotCache
from sjtu_learning_assistant.course_markdown_sync import CourseMarkdownSync
from sjtu_learning_assistant.knowledge_documents import digest
from sjtu_learning_assistant.local_settings import LocalSettings
import test_pptx_static as static_fixtures

class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'archive'
        self.source.mkdir()
        self.path = self.source / '课件.pdf'
        self.path.write_bytes(b'local source only')
        self.target = self.source / 'Knowledge Markdown'
    def test_controlled_codes_no_compiler_or_network_and_no_writes(self):
        before = self.path.read_bytes()
        cases = ((dict(attempts=3), 'requires_authorized_segmented_repair'),
                 (dict(error='HTTP 402 private provider URL'), 'account_payment_required'),
                 (dict(error='timeout with private key'), 'request_outcome_unknown'),
                 (dict(error='PdfiumError'), 'renderer_status_only'),
                 (dict(error='another lock'), 'lock_status_only'),
                 (dict(source_hash='different'), 'source_changed'))
        with patch('sjtu_learning_assistant.knowledge_documents.DocumentCompiler', side_effect=AssertionError('no compiler')), patch('httpx.Client', side_effect=AssertionError('no HTTP')):
            for item, code in cases:
                result = diagnose_source(self.source, self.path, self.target, item)
                self.assertEqual(code, result.get('code'))
                self.assertNotIn('private', json.dumps(result))
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(self.target.exists())
    def test_manifest_corruption_and_user_edit_blocked(self):
        mirror = self.target / 'Courses' / '课件.pdf.md'
        mirror.parent.mkdir(parents=True)
        mirror.write_text('人工修改', encoding='utf-8')
        self.assertEqual('manual_edit', diagnose_source(self.source, self.path, self.target, dict(mirror_hash='bad')).get('code'))
        sid = digest('课件.pdf'.encode()).__getitem__(slice(16))
        manifest = self.target / '90_Inbox' / 'Documents' / sid / 'revision' / 'manifest.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_text('bad-json', encoding='utf-8')
        self.assertEqual('manifest_corrupt', diagnose_source(self.source, self.path, self.target, dict()).get('code'))
    def test_async_seven_second_cache_single_scan_no_ui_wait(self):
        clock, gate, called = list((0,)), threading.Event(), list()
        def loader():
            called.append(True)
            gate.wait(2)
            return dict(readonly=True, courses=dict(total=9))
        cache = DiagnosticSnapshotCache(clock=lambda: clock.__getitem__(0))
        started = time.monotonic()
        self.assertEqual('scanning', cache.snapshot(loader).get('status'))
        for _ in range(10):
            cache.snapshot(loader)
        self.assertLess(time.monotonic() - started, 0.5)
        gate.set()
        for _ in range(100):
            if cache.value is not None:
                break
            time.sleep(0.01)
        cache.snapshot(loader)
        self.assertEqual(1, len(called))
        clock.__setitem__(0, 8)
        cache.snapshot(loader)
        for _ in range(100):
            if len(called) == 2:
                break
            time.sleep(0.01)
        self.assertEqual(2, len(called))
    def test_catalog_separate_course_asr_counters_and_no_urls(self):
        marker = self.target / '99_System' / 'Course-Sync.json'
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps(dict(version=1, source_root=str(self.source), quota=dict(day='2026-11-25', used=50),
            items=dict.fromkeys(('课件.pdf',), dict(status='failed', attempts=3, error='secret url https://private.invalid')))), encoding='utf-8')
        snapshot = catalog_snapshot(self.source, list((self.path,)), self.root / 'transcripts', asr=dict(total=4, completed=2, endpoint='https://private.invalid'))
        self.assertEqual(1, snapshot.get('courses').get('total'))
        self.assertEqual(4, snapshot.get('asr_counts').get('total'))
        self.assertNotIn('private.invalid', json.dumps(snapshot))
        self.assertEqual(50, json.loads(marker.read_text(encoding='utf-8')).get('quota').get('used'))

class AutomaticStaticTests(static_fixtures.StaticTests):
    def settings(self):
        return LocalSettings(archive_root=str(self.source), ai_enabled=True, ai_key_saved=True, course_markdown_enabled=True)
    def worker(self, enabled):
        return CourseMarkdownSync(self.settings, lambda: list((self.path,)), self.runner,
            static_pptx_policy=enabled, deterministic_diagnostics=True)
    def test_retained_upgrade_selective_and_completed_file_zero_calls(self):
        self.worker(False).run_once()
        marker = self.source / 'Knowledge Markdown' / '99_System' / 'Course-Sync.json'
        mirror = self.source / 'Knowledge Markdown' / 'Courses' / '静态.pptx.md'
        basis = mirror.read_bytes()
        self.assertEqual('retained', json.loads(marker.read_text(encoding='utf-8')).get('items').get('静态.pptx').get('status'))
        self.worker(True).run_once()
        body = mirror.read_text(encoding='utf-8')
        self.assertIn('quality_status: static-source-reviewed', body)
        count = len(self.calls)
        self.worker(True).run_once()
        self.assertEqual(count, len(self.calls))
        previous = self.source / 'Knowledge Markdown' / '90_Inbox' / 'Previous'
        self.assertTrue(any(p.read_bytes() == basis for p in previous.rglob(digest(basis) + '.md')))
    def test_exhausted_source_no_network_no_budget_reset_under_poll(self):
        self.worker(False).run_once()
        marker = self.source / 'Knowledge Markdown' / '99_System' / 'Course-Sync.json'
        state = json.loads(marker.read_text(encoding='utf-8'))
        item = state.get('items').get('静态.pptx')
        item.update(attempts=3, auto_paused=True)
        state.get('quota').update(used=40)
        marker.write_text(json.dumps(state), encoding='utf-8')
        worker = self.worker(True)
        for _ in range(5):
            worker.run_once()
        state = json.loads(marker.read_text(encoding='utf-8'))
        item = state.get('items').get('静态.pptx')
        self.assertEqual('requires_authorized_segmented_repair', item.get('diagnosis').get('code'))
        self.assertEqual(3, item.get('attempts'))
        self.assertEqual(1, item.get('diagnostic_checks'))
        self.assertEqual(40, state.get('quota').get('used'))
        self.assertEqual(list(), self.calls)

    def test_completed_static_source_not_reparsed_and_user_mirror_protected(self):
        self.worker(False).run_once()
        self.worker(True).run_once()
        with patch('sjtu_learning_assistant.pptx_static.extract_static_pptx', side_effect=AssertionError('completed source reparsed')):
            self.worker(True).run_once()
        mirror = self.source / 'Knowledge Markdown' / 'Courses' / '静态.pptx.md'
        mirror.write_text('人工编辑保留', encoding='utf-8')
        count = len(self.calls)
        self.worker(True).run_once()
        self.assertEqual(count, len(self.calls))
        self.assertEqual('人工编辑保留', mirror.read_text(encoding='utf-8'))

class DiagnosticBridgeTests(unittest.TestCase):
    def test_readonly_bridge_rejects_payload_and_never_creates_services(self):
        from desktop_app import DesktopBridge
        from sjtu_learning_assistant.dashboard_service import DashboardService
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            service = DashboardService.__new__(DashboardService)
            service._effective_settings = lambda: LocalSettings(archive_root=str(root))
            service._course_markdown_paths = lambda: list()
            service.local_projects_path = root / 'projects.json'
            service.start_course_markdown_sync = lambda: self.fail('worker started')
            service.start_transcript_markdown_sync = lambda: self.fail('worker started')
            service._recording_asr_service = lambda: self.fail('ASR service constructed')
            bridge = DesktopBridge(service)
            with patch('httpx.Client', side_effect=AssertionError('no HTTP')), patch('subprocess.run', side_effect=AssertionError('no subprocess')):
                self.assertFalse(bridge.invoke('compilation_diagnostics', dict(repair=True)).get('ok'))
                self.assertTrue(bridge.invoke('compilation_diagnostics', dict()).get('ok'))
                for _ in range(100):
                    if service._compilation_diagnostic_cache.value is not None:
                        break
                    time.sleep(0.01)
                result = service.compilation_diagnostics()
            self.assertTrue(result.get('readonly'))
            self.assertNotIn(str(root), json.dumps(result))
            self.assertEqual(list(), list(root.iterdir()))

class DraftCompatibilityTests(DiagnosticsTests):
    def test_segment_subset_and_retained_resume_draft_local_validation_no_fake_approval(self):
        from office_fixtures import knowledge_response
        from sjtu_learning_assistant.knowledge_summary import SUMMARY_PROMPT
        sid = digest('课件.pdf'.encode()).__getitem__(slice(16))
        directory = self.target / '90_Inbox' / 'Documents' / sid / 'revision'
        name = 'summary/part-0-test-resume.json'
        output = directory / name
        output.parent.mkdir(parents=True)
        draft = knowledge_response('deepseek-chat', SUMMARY_PROMPT, json.dumps(dict(pages=list((dict(page=1),)), topic_ids=list())))
        payload = json.dumps(dict(draft=draft), ensure_ascii=False)
        output.write_text(payload, encoding='utf-8')
        manifest = directory / 'manifest.json'
        manifest.write_text(json.dumps(dict(version=1, source='课件.pdf', source_hash=digest(self.path.read_bytes()),
            pages=dict.fromkeys(('1', '2'), 'preserved'), outputs=dict.fromkeys((name,), digest(payload.encode())))), encoding='utf-8')
        before = manifest.read_bytes(), output.read_bytes()
        with patch('httpx.Client', side_effect=AssertionError('no HTTP')):
            result = diagnose_source(self.source, self.path, self.target, dict(status='failed', attempts=1, error='精炼结构校验'))
        self.assertEqual('checkpoint_compatible', result.get('code'))
        self.assertFalse(result.get('automatic_allowed'))
        self.assertNotIn('approved', result)
        self.assertEqual(before, (manifest.read_bytes(), output.read_bytes()))

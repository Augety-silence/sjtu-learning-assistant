"""5000 字、危险内容只保留、真正并行和取消限速的回归；不使用真实 API。"""
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from office_fixtures import build_pptx, build_pdf, knowledge_response
from sjtu_learning_assistant.ai_runtime import SharedAIRuntime, cancellation_scope
from sjtu_learning_assistant.course_markdown_sync import CourseMarkdownSync
from sjtu_learning_assistant.knowledge_documents import DocumentCompiler, digest, compilation_fingerprint
from sjtu_learning_assistant.knowledge_summary import SummaryCompiler, SUMMARY_PROMPT, validate_summary
from sjtu_learning_assistant.local_settings import LocalSettings
from sjtu_learning_assistant.summary_policy import summary_budget
from sjtu_learning_assistant.transcript_service import TranscriptService


class ParallelSummaryPolicyTests(unittest.TestCase):
    def test_old_three_thousand_rejected_draft_now_valid_without_new_generation(self):
        data = {'pages': [{'page': 1}], 'topic_ids': []}
        value = json.loads(knowledge_response('deepseek-chat', SUMMARY_PROMPT, json.dumps(data)))
        value['markdown'] += '\n' + '课程概念。' * 600
        self.assertGreater(len(value['markdown']), 3000)
        validate_summary(value, [1])
        request = Mock(side_effect=AssertionError('旧完整稿不能重新计费'))
        cache = {}
        compiler = SummaryCompiler(request, cache.get, cache.update)
        cache[compiler.prefix + 'old.json-retry.json'] = json.dumps(value)
        self.assertEqual(value, compiler._save_summary('old.json', SUMMARY_PROMPT, data, [1], []))
        request.assert_not_called()

    def test_actual_request_uses_new_budget_but_old_cache_identity_is_retained(self):
        cache, seen = {}, []
        def request(model, prompt, payload):
            seen.append((prompt, json.loads(payload)))
            return knowledge_response(model, prompt, payload)
        compiler = SummaryCompiler(request, cache.get, cache.update)
        compiler.compile([{'page': 1, 'source_text': '知识' * 25000, 'text': '来源'}])
        self.assertIn('最多5000字符', seen[0][0])
        self.assertEqual(4300, seen[0][1]['source_budget']['target_chars'])
        count = len(seen)
        compiler.compile([{'page': 1, 'source_text': '知识' * 25000, 'text': '来源'}])
        self.assertEqual(count, len(seen))
        self.assertEqual(2600, summary_budget(50000, legacy=True)['target_chars'])

    def test_external_macro_and_embedded_objects_never_reach_office_or_api(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source, target = root / 'archive', root / 'output'
            source.mkdir()
            external = build_pptx(source / 'external.pptx', extra={
                'ppt/slides/_rels/slide2.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rExt" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="https://example.invalid/a" TargetMode="External"/></Relationships>'})
            embedded = build_pptx(source / 'embedded.pptx', extra={'ppt/embeddings/object.bin': b'never execute'})
            macro = build_pptx(source / 'macro.pptm', extra={'ppt/vbaProject.bin': b'never execute'})
            runner = Mock(side_effect=AssertionError('不应调用模型'))
            with patch('sjtu_learning_assistant.knowledge_documents.subprocess.Popen') as office:
                for path in (external, embedded, macro):
                    original = path.read_bytes()
                    note = DocumentCompiler(runner, threading.Event()).compile(source, path, target)
                    self.assertIn('quality_status: source-only', note.read_text())
                    self.assertIn('skipped_active_content', note.read_text())
                    self.assertEqual('completed', json.loads((note.parent / 'manifest.json').read_text())['status'])
                    self.assertEqual(original, path.read_bytes())
                office.assert_not_called()
            runner.assert_not_called()

    def test_completed_same_source_is_not_downgraded_by_new_skip_rule(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp).resolve()
            path = build_pptx(source / 'completed.pptx', extra={'ppt/embeddings/object.bin': b'never execute'})
            target = source / 'Knowledge Markdown'
            marker = target / '99_System/Course-Sync.json'
            marker.parent.mkdir(parents=True)
            mirror = target / 'Courses/completed.pptx.md'
            mirror.parent.mkdir()
            body = '# 已验收总结\n正文保留。'
            mirror.write_text(body, encoding='utf-8')
            marker.write_text(json.dumps({'version': 1, 'source_root': str(source), 'quota': {'day': '2026-11-22', 'used': 0},
                'items': {'completed.pptx': {'fingerprint': compilation_fingerprint(digest(path.read_bytes())),
                          'status': 'completed', 'text_ready': True, 'mirror_hash': digest(body.encode())}}}), encoding='utf-8')
            settings = LocalSettings(archive_root=str(source), ai_enabled=True, ai_key_saved=True, course_markdown_enabled=True)
            runner = Mock(side_effect=AssertionError('已完成不再计费'))
            sync = CourseMarkdownSync(lambda: settings, lambda: [path], runner)
            sync.run_once()
            self.assertEqual(body, mirror.read_text())
            self.assertEqual(1, sync.status()['completed'])
            self.assertEqual(0, sync.status()['retained'])
            runner.assert_not_called()
            sync.close()

    def test_course_compiles_distinct_files_concurrently_without_lost_counts(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp).resolve()
            paths = [build_pdf(source / f'{index}.pdf') for index in range(3)]
            gate = threading.Barrier(3)
            calls, lock = [], threading.Lock()
            class Compiler:
                def __init__(self, call, cancel, **kwargs):
                    self.call, self.cancel = call, cancel
                def compile(self, root, path, target):
                    gate.wait(timeout=5)
                    self.call('deepseek-chat', '测试', '合成输入')
                    with lock:
                        calls.append(path.name)
                    note = target / '90_Inbox' / path.name / 'document.md'
                    note.parent.mkdir(parents=True)
                    note.write_text('# 完成', encoding='utf-8')
                    return note
            settings = LocalSettings(archive_root=str(source), ai_enabled=True, ai_key_saved=True, course_markdown_enabled=True)
            sync = CourseMarkdownSync(lambda: settings, lambda: paths + paths, lambda *args: 'ok',
                                      workers=3, compiler_factory=Compiler)
            sync.run_once()
            self.assertEqual(3, sync.status()['completed'])
            state = json.loads((source / 'Knowledge Markdown/99_System/Course-Sync.json').read_text())
            self.assertEqual(3, state['quota']['used'])
            self.assertEqual(3, len(set(calls)))
            sync.close()

    def test_transcript_pool_dispatches_different_lessons(self):
        with tempfile.TemporaryDirectory() as temp:
            service = TranscriptService(Path(temp) / 'transcripts/v1', autostart_worker=False, notes_workers=4)
            gate = threading.Barrier(4)
            names = []
            for index in range(4):
                (service.root / 'videos' / str(index)).mkdir()
            def process(directory):
                gate.wait(timeout=5)
                names.append(directory.name)
            with patch.object(service, '_backfill_lesson', side_effect=process):
                service._backfill_next_lesson()
            self.assertEqual(4, len(set(names)))
            service.close()

    def test_ten_inflight_share_rpm_and_queued_cancel_sends_nothing(self):
        with tempfile.TemporaryDirectory() as temp:
            now = [1000.0]
            runtime = SharedAIRuntime(Path(temp) / 'runtime.sqlite', 'scope', clock=lambda: now[0],
                                     sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds))
            body = {'max_tokens': 1, 'messages': []}
            ids = [runtime.reserve(body) for _ in range(10)]
            self.assertEqual(1000, now[0])
            cancelled = [False]
            def sleep(seconds):
                cancelled[0] = True
            runtime.sleeper = sleep
            with cancellation_scope(lambda: cancelled[0]):
                with self.assertRaisesRegex(RuntimeError, '停止'):
                    runtime.reserve(body)
            with sqlite3.connect(runtime.path) as db:
                self.assertEqual(10, db.execute('SELECT count(*) FROM requests WHERE started IS NOT NULL').fetchone()[0])
            for identity in ids:
                runtime.finish(identity, {}, 'done', 1000)
            runtime.sleeper = lambda seconds: now.__setitem__(0, now[0] + seconds)
            runtime.reserve(body)
            self.assertGreaterEqual(now[0], 1060)

    def test_large_waiting_request_does_not_block_small_request_that_fits(self):
        with tempfile.TemporaryDirectory() as temp:
            now = [1000.0]
            runtime = SharedAIRuntime(Path(temp) / 'runtime.sqlite', 'scope', tpm=100,
                                     clock=lambda: now[0], sleeper=lambda seconds: now.__setitem__(0, now[0] + seconds))
            runtime.reserve({'max_tokens': 60, 'messages': []})
            with runtime._db() as db:
                db.execute('INSERT INTO requests(id,scope,queued,reserved,lease) VALUES(?,?,?,?,?)',
                           ('waiting-large', 'scope', 999, 80, 2000))
            runtime.reserve({'max_tokens': 10, 'messages': []})
            self.assertEqual(1000, now[0], '容量足够的小请求不得被队首大输入阻塞')

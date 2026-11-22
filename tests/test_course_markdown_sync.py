from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import unquote

from office_fixtures import build_pdf, knowledge_response
from sjtu_learning_assistant.course_markdown_sync import CourseMarkdownSync
from sjtu_learning_assistant.dashboard_service import DashboardService
from sjtu_learning_assistant.ai_classifier import AIClassificationError
from sjtu_learning_assistant.knowledge_compiler import KnowledgeCompilerError
from sjtu_learning_assistant.local_settings import LocalSettings, SettingsError, SettingsStore
from desktop_app import DesktopBridge


class CourseMarkdownSyncTests(unittest.TestCase):
    def test_automatic_slices_rotate_and_do_not_increment_failure_attempts(self):
        self.paths.append(build_pdf(self.path.parent / "next.pdf", ("Next",)))
        worker = CourseMarkdownSync(lambda: self.settings, lambda: self.paths, self.runner,
            max_documents_per_cycle=1, slice_calls=1, now_provider=lambda: self.now)
        self.addCleanup(worker.close)
        wiki = Mock(return_value={"built": 0, "cached": 0, "failed": 0, "issues": []})
        worker.wiki_rebuilder = wiki
        worker.run_once()
        state = json.loads((self.target / "99_System/Course-Sync.json").read_text(encoding="utf-8"))
        self.assertEqual(0, worker.status()["failed"])
        self.assertTrue(worker.status()["has_runnable"])
        self.assertEqual(0, next(i for i in state["items"].values() if i.get("last_dispatched_at"))["attempts"])
        wiki.assert_not_called()
        self.now += timedelta(seconds=1)
        worker.run_once()
        state = json.loads((self.target / "99_System/Course-Sync.json").read_text(encoding="utf-8"))
        self.assertEqual(2, sum(bool(i.get("last_dispatched_at")) for i in state["items"].values()))
        for _ in range(12):
            self.now += timedelta(seconds=1)
            worker.run_once()
        self.assertEqual(2, worker.status()["completed"])
        self.assertEqual(0, worker.status()["failed"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "archive"
        self.path = self.source / "2026" / "课程 #1" / "课件(1).pdf"
        self.path.parent.mkdir(parents=True)
        build_pdf(self.path)
        self.settings = LocalSettings(archive_root=str(self.source), ai_enabled=True, ai_key_saved=True,
                                      course_markdown_enabled=True, course_markdown_daily_calls=100)
        self.paths = [self.path]
        self.runner = Mock(side_effect=knowledge_response)
        self.day = "2026-11-14"
        self.now = datetime(2026, 11, 14, tzinfo=timezone.utc)
        self.worker = self.new_worker()
        self.target = self.source / "Knowledge Markdown"
        self.mirror = self.target / "Courses" / self.path.relative_to(self.source).with_name(self.path.name + ".md")

    def new_worker(self):
        return CourseMarkdownSync(lambda: self.settings, lambda: self.paths, self.runner, today=lambda: self.day, now_provider=lambda: self.now)

    def tearDown(self):
        self.worker.close()
        self.temp.cleanup()

    def test_existing_download_to_corresponding_md_and_new_download_incremental(self):
        original = self.path.read_bytes()
        self.worker.run_once()
        self.assertTrue(self.mirror.is_file())
        self.assertEqual("completed", self.worker.status()["status"])
        self.assertEqual(original, self.path.read_bytes())
        self.worker.run_once()
        self.assertEqual(4, self.runner.call_count)
        new = build_pdf(self.path.parent / "new.pdf", ("New slide",))
        self.paths.append(new)
        self.worker.run_once()
        self.assertEqual(7, self.runner.call_count)
        self.assertEqual(2, self.worker.status()["completed"])

    def test_wiki_rebuild_runs_before_and_after_conversion_and_failure_keeps_md(self):
        seen = []
        def rebuild(runner, cancelled):
            self.assertFalse(cancelled())
            seen.append(self.mirror.read_text(encoding="utf-8"))
            if len(seen) == 1:
                return {"built": 0, "cached": 0, "failed": 0, "issues": []}
            raise RuntimeError("independent wiki failure")
        self.worker.wiki_rebuilder = rebuild
        original = self.path.read_bytes()
        self.worker.run_once()
        self.assertEqual(2, len(seen))
        self.assertIn("multimodal_status: pending", seen[0])
        self.assertIn("contract: course-summary-v1", seen[1])
        self.assertEqual("completed_with_warnings", self.worker.status()["status"])
        self.assertEqual(1, self.worker.status()["completed"])
        self.assertEqual(original, self.path.read_bytes())
        self.assertIn("原 MD 保留", " ".join(self.worker.status()["issues"]))

    def test_mirror_images_are_relative_and_url_encoded(self):
        self.worker.run_once()
        import re
        self.assertIn('[课件 p.1]', self.mirror.read_text(encoding='utf-8'))
        evidence = list((self.target / '90_Inbox').rglob('pages/page-*.md'))
        self.assertEqual(2, len(evidence))
        self.assertTrue(all(p.is_file() for p in evidence))

    def test_reverified_visual_evidence_links_resolve_in_course_mirror(self):
        def runner(model, system, content):
            if '这是来源矛盾的原图复核' in system:
                return knowledge_response(model, system, content) + '\n原图复核完成'
            if '来源审校员' in system:
                payload = json.loads(content)
                if payload.get('pages') and '原图复核完成' not in payload['pages'][0]['text']:
                    return '{"approved":false,"issues":["第1页：转写矛盾"]}'
            return knowledge_response(model, system, content)
        self.runner.side_effect = runner
        self.worker.run_once()
        import re
        evidence = list((self.target / '90_Inbox').rglob('*-visual-*.md'))
        self.assertEqual(1, len(evidence))
        self.assertIn('原图复核完成', evidence[0].read_text(encoding='utf-8'))

    def test_source_revision_updates_generated_mirror_keeps_previous(self):
        self.worker.run_once()
        old = self.mirror.read_text(encoding="utf-8")
        build_pdf(self.path, ("Changed slide",))
        self.worker.run_once()
        self.assertNotEqual(old, self.mirror.read_text(encoding="utf-8"))
        self.assertEqual(2, len(list(self.target.rglob("document.md"))))

    def test_manual_edits_protected_even_if_source_changes(self):
        self.worker.run_once()
        self.mirror.write_text("我的笔记", encoding="utf-8")
        build_pdf(self.path, ("Changed slide",))
        self.worker.run_once()
        self.assertEqual("我的笔记", self.mirror.read_text(encoding="utf-8"))
        self.assertEqual(4, self.runner.call_count)
        self.assertEqual(1, self.worker.status()["failed"])

    def test_legacy_daily_budget_does_not_stop_compilation_and_restarts_reuse_all(self):
        self.settings = replace(self.settings, course_markdown_daily_calls=1)
        self.worker.run_once()
        self.assertTrue(self.mirror.exists())
        self.assertIn("contract: course-summary-v1", self.mirror.read_text(encoding="utf-8"))
        self.assertEqual("completed", self.worker.status()["status"])
        restart = self.new_worker()
        restart.run_once()
        self.assertEqual(4, self.runner.call_count)
        self.day = "2026-11-15"
        restart.run_once()
        self.assertTrue(self.mirror.exists())
        self.assertEqual(4, self.runner.call_count)

    def test_all_text_markdown_ready_even_when_visual_service_fails(self):
        self.settings = replace(self.settings, course_markdown_daily_calls=1)
        self.paths.append(build_pdf(self.path.parent / "second.pdf", ("Second",)))
        self.runner.side_effect = RuntimeError("unavailable")
        self.worker.run_once()
        self.assertEqual(2, self.worker.status()["text_ready"])
        self.assertEqual(2, len(list((self.target / "Courses").rglob("*.md"))))
        self.assertEqual(2, self.runner.call_count)

    def test_missing_ai_does_not_block_local_basic_markdown(self):
        self.settings = replace(self.settings, ai_enabled=False, ai_key_saved=False)
        self.worker.run_once()
        self.assertTrue(self.mirror.is_file())
        self.assertEqual(1, self.worker.status()["text_ready"])
        self.assertEqual("needs_ai", self.worker.status()["status"])
        self.runner.assert_not_called()

    def test_text_compilation_does_not_use_chat_preview_400k_limit(self):
        self.settings = replace(self.settings, course_markdown_daily_calls=1)
        build_pdf(self.path, tuple("Long text " * 350 for _ in range(130)))
        self.runner.side_effect = RuntimeError("unavailable")
        self.worker.run_once()
        self.assertTrue(self.mirror.is_file())
        self.assertGreater(len(self.mirror.read_text(encoding="utf-8")), 400_000)
        self.assertIn("## 第 130 页", self.mirror.read_text(encoding="utf-8"))
        self.assertEqual(1, self.worker.status()["text_ready"])

    def test_mid_cycle_midnight_resets_quota_before_next_call(self):
        self.settings = replace(self.settings, course_markdown_daily_calls=2)
        def runner(*args):
            if self.runner.call_count == 1:
                self.day = "2026-11-15"
            return knowledge_response(*args)
        self.runner.side_effect = runner
        self.worker.run_once()
        marker = json.loads((self.target / "99_System/Course-Sync.json").read_text(encoding="utf-8"))
        self.assertEqual({"day": "2026-11-15", "used": 3}, marker["quota"])
        self.worker.run_once()
        self.assertEqual(3, self.worker.status()["calls_today"])

    def test_first_database_failure_can_recover_and_lock_scaffold_is_safe(self):
        broken = CourseMarkdownSync(lambda: self.settings, Mock(side_effect=RuntimeError()), self.runner)
        broken.run_once()
        self.assertEqual("blocked", broken.status()["status"])
        self.worker.run_once()
        self.assertTrue(self.mirror.is_file())
        other = self.source / "other-root"
        other.mkdir()
        self.settings = replace(self.settings, archive_root=str(other))
        skeleton = other / "Knowledge Markdown/99_System"
        skeleton.mkdir(parents=True)
        (skeleton / "Document-Compiler.lock").write_bytes(b"0")
        self.paths = [build_pdf(other / "new.pdf", ("Recovered",))]
        self.worker.run_once()
        self.assertEqual("completed", self.worker.status()["status"])

    def test_failed_calls_count_and_bad_file_is_isolated(self):
        self.runner.side_effect = RuntimeError("secret request")
        for _ in range(4):
            self.worker.run_once()
        self.assertEqual(1, self.runner.call_count)
        self.assertNotIn("secret request", str(self.worker.status()))
        self.assertEqual(1, self.worker.status()["calls_today"])
        self.runner.side_effect = knowledge_response
        good = build_pdf(self.path.parent / "good.pdf", ("Good",))
        self.paths.append(good)
        self.worker.run_once()
        self.assertEqual(1, self.worker.status()["completed"])

    def test_failed_course_retry_survives_restart_without_three_attempt_lock(self):
        self.runner.side_effect = RuntimeError('unavailable')
        for delay in (0, 301, 901, 2701):
            self.now += timedelta(seconds=delay)
            self.worker.run_once()
        self.assertEqual(4, self.runner.call_count)
        restarted = self.new_worker()
        self.addCleanup(restarted.close)
        restarted.run_once()
        self.assertEqual(4, self.runner.call_count)
        self.now += timedelta(hours=2, seconds=1)
        self.runner.side_effect = knowledge_response
        restarted.run_once()
        self.assertEqual(1, restarted.status()['completed'])

    def test_disabled_is_readonly_and_does_not_load_ai(self):
        self.settings = replace(self.settings, course_markdown_enabled=False)
        self.worker.run_once()
        self.assertFalse(self.target.exists())
        self.runner.assert_not_called()

    def test_cancellation_from_settings_does_not_commit_response(self):
        def runner(*_):
            self.settings = replace(self.settings, course_markdown_enabled=False)
            self.worker.notify(settings_changed=True)
            return "result"
        self.runner.side_effect = runner
        self.worker.run_once()
        self.assertTrue(self.mirror.exists())
        self.assertNotIn("## AI 解读", self.mirror.read_text(encoding="utf-8"))
        self.assertEqual(1, self.runner.call_count)

    def test_unmanaged_output_and_symlinks_are_rejected(self):
        self.target.mkdir()
        (self.target / "user.md").write_text("笔记", encoding="utf-8")
        self.worker.run_once()
        self.assertEqual("blocked", self.worker.status()["status"])
        self.assertEqual(["user.md"], [p.name for p in self.target.iterdir()])
        self.runner.assert_not_called()

    def test_path_outside_archive_never_sent(self):
        self.paths = [build_pdf(self.root / "outside.pdf")]
        self.worker.run_once()
        self.runner.assert_not_called()
        self.assertEqual(1, self.worker.status()["failed"])

    def test_mirror_replace_crash_recovers_without_repeated_api(self):
        real_write = __import__("sjtu_learning_assistant.course_markdown_sync", fromlist=["_atomic_write"])._atomic_write
        def fail(path, body):
            if path == self.mirror and "converted_by: libreoffice" in body:
                raise OSError("disk failure")
            return real_write(path, body)
        with patch("sjtu_learning_assistant.course_markdown_sync._atomic_write", side_effect=fail):
            self.worker.run_once()
        self.worker.run_once()
        self.assertTrue(self.mirror.is_file())
        self.assertEqual(4, self.runner.call_count)

    def test_contract_upgrade_keeps_old_mirror_until_lecture_is_complete(self):
        self.worker.run_once()
        previous = self.mirror.read_bytes()
        marker = self.target / '99_System/Course-Sync.json'
        state = json.loads(marker.read_text(encoding='utf-8'))
        state['items'][self.path.relative_to(self.source).as_posix()]['fingerprint'] = 'old-contract'
        marker.write_text(json.dumps(state), encoding='utf-8')
        # 不依赖模型成功也不能回退覆盖成原文稿。
        self.settings = replace(self.settings, course_markdown_daily_calls=1)
        self.runner.side_effect = RuntimeError("unavailable")
        with patch('sjtu_learning_assistant.course_markdown_sync.compilation_fingerprint', return_value='new-prompt'), patch('sjtu_learning_assistant.knowledge_summary.SUMMARY_VERSION', 'new-prompt'):
            self.worker.run_once()
        self.assertEqual(previous, self.mirror.read_bytes())
        self.assertEqual('completed_with_warnings', self.worker.status()['status'])

    def test_settings_migration_retains_values_does_not_enable_silently(self):
        store = SettingsStore(self.root / "settings.json")
        payload = self.settings.to_dict()
        del payload["course_markdown_enabled"]
        del payload["course_markdown_daily_calls"]
        store.path.write_text(json.dumps(payload), encoding="utf-8")
        loaded = store.load(environ={})
        self.assertFalse(loaded.course_markdown_enabled)
        self.assertTrue(loaded.auto_download_current_term)
        self.assertEqual(str(self.source), loaded.archive_root)
        self.assertEqual(0, store.update({"course_markdown_daily_calls": 0}).course_markdown_daily_calls)
        for value in (True, -1, 1001, 1.5):
            with self.assertRaises(SettingsError):
                store.update({"course_markdown_daily_calls": value})

    def test_download_and_sync_completion_notify_and_bridge_accepts_documents(self):
        archive = Mock()
        archive.download_file_by_source_id.return_value = SimpleNamespace(status="downloaded", source_id="1", size=2)
        dashboard = DashboardService(SimpleNamespace(), archive_root=self.source, archive_service_factory=lambda: archive)
        automatic = Mock()
        automatic.status.return_value = {"status": "completed", "issues": []}
        dashboard._course_markdown_instance = automatic
        dashboard.settings_store = SettingsStore(self.root / "dashboard-settings.json")
        dashboard.update_settings({"theme_mode": "dark"})
        automatic.notify.assert_not_called()
        dashboard.update_settings({"course_markdown_enabled": True})
        automatic.notify.assert_called_once_with(settings_changed=True)
        automatic.notify.reset_mock()
        dashboard.download_material("1")
        automatic.notify.assert_called_once_with(settings_changed=False)
        dashboard._sync_lock.acquire()
        dashboard._release_sync_when_done(SimpleNamespace(wait=lambda: 0))
        self.assertEqual(2, automatic.notify.call_count)
        dashboard.knowledge_compiler_start = Mock(return_value={"accepted": True})
        bridge = DesktopBridge(dashboard)
        result = bridge.invoke("knowledge_compiler_start", {"mode": "documents", "source_root": str(self.source), "target_root": str(self.target)})
        self.assertTrue(result["ok"])
        self.assertTrue(bridge.invoke("course_markdown_status")["ok"])
        self.assertFalse(bridge.invoke("course_markdown_status", {"bad": True})["ok"])
        dashboard.close()
        automatic.close.assert_called_once()

    def test_knowledge_ai_retains_safe_api_error_and_uses_complete_output_budget(self):
        dashboard = DashboardService(SimpleNamespace())
        dashboard._effective_settings = lambda: self.settings
        dashboard.ai_key_loader = lambda: 'synthetic-test-key'
        client = Mock()
        dashboard.ai_client_factory = Mock(return_value=client)
        client.chat_completion.side_effect = AIClassificationError('AI 请求超时，请检查网络或稍后重试。', category='timeout')
        with self.assertRaisesRegex(KnowledgeCompilerError, '请求超时'):
            dashboard._run_knowledge_compiler_ai('qwen', '合成编译任务', '合成课件')
        self.assertEqual(8192, client.chat_completion.call_args.kwargs['max_tokens'])
        self.assertEqual(240, dashboard.ai_client_factory.call_args.kwargs['timeout'].read)
        client.close.assert_called_once()
        dashboard.close()


if __name__ == "__main__":
    unittest.main()

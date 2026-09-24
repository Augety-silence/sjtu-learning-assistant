from __future__ import annotations

import errno
import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from sjtu_learning_assistant.archive_service import ArchiveError, ArchiveFileContext, ArchiveService
from sjtu_learning_assistant.local_settings import LocalSettings, SettingsError, SettingsStore, validate_archive_root
from sjtu_learning_assistant.models import Base, Course, SyncState

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


class LocalSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = SettingsStore(self.root / "support" / "settings.json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_defaults_atomic_round_trip_and_no_temporary_file(self) -> None:
        defaults = self.store.load(environ={})
        self.assertEqual("", defaults.mail_account)
        self.assertTrue(defaults.auto_download_current_term)
        self.assertTrue(defaults.organize_by_category)
        self.assertEqual("system", defaults.theme_mode)
        archive = self.root / "archive"
        saved = self.store.update({"archive_root": str(archive)})
        self.assertEqual(str(archive), saved.archive_root)
        self.assertEqual(saved, self.store.load())
        self.assertEqual(0o600, self.store.path.stat().st_mode & 0o777)
        self.assertEqual([], list(self.store.path.parent.glob(".settings-*")))
        payload = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                "archive_root",
                "auto_download_current_term",
                "organize_by_category",
                "ai_enabled",
                "ai_base_url",
                "ai_model",
                "ai_key_saved",
                "ai_chat_send_shortcut",
                "ai_reply_language",
                "ai_attachment_context_budget",
                "ai_auto_open_activity",
                "ai_code_line_numbers",
                "theme_mode",
                "mail_account",
            },
            set(payload),
        )

    def test_payload_type_unknown_and_sensitive_keys_are_rejected(self) -> None:
        for payload in (
            [],
            {},
            {"auto_download_current_term": 1},
            {"unknown": True},
            {"email": "student@example.edu"},
            {"canvas_token": "not-a-real-token"},
        ):
            with self.subTest(payload=payload), self.assertRaises(SettingsError):
                self.store.update(payload)

    def test_mail_account_uses_environment_only_as_initial_default(self) -> None:
        self.assertEqual(
            "student-id",
            self.store.load(environ={"SJTU_EMAIL": "student-id"}).mail_account,
        )
        self.store.save(LocalSettings(mail_account="saved-id"))
        self.assertEqual(
            "saved-id",
            self.store.load(environ={"SJTU_EMAIL": "other-id"}).mail_account,
        )

    def test_legacy_settings_gain_mail_account_without_storing_credentials(self) -> None:
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text(
            json.dumps(
                {
                    "archive_root": str(self.root / "archive"),
                    "auto_download_current_term": True,
                    "organize_by_category": True,
                }
            ),
            encoding="utf-8",
        )
        loaded = self.store.load(environ={"SJTU_EMAIL": "student-id"})
        self.assertEqual("student-id", loaded.mail_account)
        saved = self.store.update({"mail_account": "saved-id"})
        self.assertEqual("saved-id", saved.mail_account)
        payload = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertNotIn("password", payload)
        self.assertNotIn("token", payload)

    def test_environment_then_explicit_override_precedence(self) -> None:
        archive = self.root / "archive"
        cli_archive = self.root / "cli-archive"
        self.store.save(LocalSettings(archive_root=str(archive)))
        resolved = self.store.resolve(
            archive_root=cli_archive,
            organize_by_category=True,
            environ={
                "SJTU_ARCHIVE_ROOT": str(self.root / "environment"),
                "SJTU_AUTO_DOWNLOAD_CURRENT_TERM": "false",
                "SJTU_ORGANIZE_BY_CATEGORY": "false",
            },
        )
        self.assertEqual(str(cli_archive), resolved.archive_root)
        self.assertFalse(resolved.auto_download_current_term)
        self.assertTrue(resolved.organize_by_category)

    def test_ai_chat_preferences_validate_and_persist(self) -> None:
        saved = self.store.update(
            {
                "ai_chat_send_shortcut": "cmd_enter",
                "ai_reply_language": "en",
                "ai_attachment_context_budget": "deep",
                "ai_auto_open_activity": False,
                "ai_code_line_numbers": True,
                "theme_mode": "dark",
            }
        )
        self.assertEqual("cmd_enter", saved.ai_chat_send_shortcut)
        self.assertEqual("en", saved.ai_reply_language)
        self.assertEqual("deep", saved.ai_attachment_context_budget)
        self.assertFalse(saved.ai_auto_open_activity)
        self.assertTrue(saved.ai_code_line_numbers)
        self.assertEqual("dark", saved.theme_mode)
        self.assertEqual(saved, self.store.load())

        invalid_changes = (
            {"ai_chat_send_shortcut": "ctrl_enter"},
            {"ai_reply_language": "fr"},
            {"ai_attachment_context_budget": "unlimited"},
            {"ai_auto_open_activity": 1},
            {"ai_code_line_numbers": "yes"},
            {"theme_mode": "sepia"},
        )
        for change in invalid_changes:
            with self.subTest(change=change), self.assertRaises(SettingsError):
                self.store.update(change)

    def test_pre_personalization_settings_are_migrated_with_safe_defaults(self) -> None:
        legacy = LocalSettings().to_dict()
        for key in (
            "ai_chat_send_shortcut",
            "ai_reply_language",
            "ai_attachment_context_budget",
            "ai_auto_open_activity",
            "ai_code_line_numbers",
            "theme_mode",
        ):
            legacy.pop(key)
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text(json.dumps(legacy), encoding="utf-8")

        loaded = self.store.load(environ={})

        self.assertEqual("enter", loaded.ai_chat_send_shortcut)
        self.assertEqual("auto", loaded.ai_reply_language)
        self.assertEqual("balanced", loaded.ai_attachment_context_budget)
        self.assertTrue(loaded.ai_auto_open_activity)
        self.assertFalse(loaded.ai_code_line_numbers)
        self.assertEqual("system", loaded.theme_mode)

    def test_pre_theme_settings_are_migrated_to_follow_system(self) -> None:
        previous = LocalSettings().to_dict()
        previous.pop("theme_mode")
        self.store.path.parent.mkdir(parents=True)
        self.store.path.write_text(json.dumps(previous), encoding="utf-8")

        loaded = self.store.load(environ={})

        self.assertEqual("system", loaded.theme_mode)

    def test_archive_root_rejects_relative_root_and_symlink(self) -> None:
        with self.assertRaises(SettingsError):
            validate_archive_root("relative/path")
        with self.assertRaises(SettingsError):
            validate_archive_root("/")
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(SettingsError, "符号链接"):
            validate_archive_root(str(link / "child"))


class ArchiveOrganizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "archive"
        self.root.mkdir()
        self.client = httpx.Client(base_url="https://oc.sjtu.edu.cn")
        self.service = ArchiveService(
            SimpleNamespace(),
            self.client,
            archive_root=self.root,
            organize_by_category=True,
        )
        self.updates: list[tuple[str, Path]] = []
        self.service._update_local_path = lambda source_id, target: self.updates.append(
            (source_id, target)
        )

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def context(self, source: Path, *, source_id: str = "42", content: bytes = b"same") -> ArchiveFileContext:
        return ArchiveFileContext(
            source_id=source_id,
            course_name="NLP",
            term_name="2026-2027 Fall",
            display_name="lecture.pdf",
            expected_size=len(content),
            source_updated_at=NOW,
            local_path=str(source),
            download_status="downloaded",
            download_attempts=1,
            downloaded_size=len(content),
            downloaded_sha256=hashlib.sha256(content).hexdigest(),
            downloaded_source_updated_at=NOW,
            folder_names=("Week 1",),
            file_id=1,
            course_id=1,
            category="courseware",
        )

    def target(self, context: ArchiveFileContext) -> Path:
        return self.service._planned_paths([context])[context.source_id]

    def test_category_path_and_disabled_legacy_path(self) -> None:
        source = self.root / "legacy.pdf"
        context = self.context(source)
        self.assertEqual(
            ("2026-2027 Fall", "NLP", "课件", "lecture.pdf"),
            self.target(context).relative_to(self.root).parts,
        )
        legacy = ArchiveService(
            SimpleNamespace(), self.client, archive_root=self.root, organize_by_category=False
        )
        self.assertEqual(
            ("2026-2027 Fall", "NLP", "week 1", "lecture.pdf"),
            legacy._planned_paths([context])[context.source_id].relative_to(self.root).parts,
        )

    def test_organize_moves_old_repeated_directory_layout_to_canonical_path(self) -> None:
        source = (
            self.root
            / "2026-2027 Fall"
            / "NLP"
            / "NLP"
            / "课件"
            / "课 件"
            / "Week 1"
            / "week-1"
            / "lecture.pdf"
        )
        source.parent.mkdir(parents=True)
        source.write_bytes(b"same")
        context = self.context(source)
        target = self.target(context)

        self.service._active_course_ids = lambda: set((context.course_id,))
        self.service._load_contexts = lambda course_ids=None: (context,)
        summary = self.service.organize_current_term()

        self.assertEqual(1, summary.moved)
        self.assertFalse(source.exists())
        self.assertEqual(
            ("2026-2027 Fall", "NLP", "课件", "lecture.pdf"),
            target.relative_to(self.root).parts,
        )
        self.assertFalse((self.root / "2026-2027 Fall" / "NLP" / "NLP").exists())
        self.assertEqual(b"same", target.read_bytes())

    def test_organize_migrates_legacy_supplementary_tree_to_flat_other(self) -> None:
        legacy_category = self.root / "2026-2027 Fall" / "NLP" / "补充资料"
        source = legacy_category / "References" / "Deep" / "lecture.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"same")
        context = replace(
            self.context(source),
            category="supplementary",
            folder_names=("References", "Deep"),
            ai_category="supplementary",
        )
        target = self.target(context)

        result = self.service._organize_one(context, target)

        self.assertEqual("moved", result.status)
        self.assertEqual(
            ("2026-2027 Fall", "NLP", "其他", "lecture.pdf"),
            target.relative_to(self.root).parts,
        )
        self.assertEqual(b"same", target.read_bytes())
        self.assertFalse(legacy_category.exists())

    def test_move_is_idempotent_and_recovers_interrupted_db_update(self) -> None:
        source = self.root / "2026-2027 Fall" / "NLP" / "Week 1" / "lecture.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"same")
        context = self.context(source)
        target = self.target(context)
        first = self.service._organize_one(context, target)
        self.assertEqual("moved", first.status)
        self.assertFalse(source.exists())
        self.assertEqual(b"same", target.read_bytes())

        stale = self.context(source)
        second = self.service._organize_one(stale, target)
        self.assertEqual("unchanged", second.status)
        self.assertEqual(target, self.updates[-1][1])

    def test_duplicate_and_conflict_are_safe(self) -> None:
        source = self.root / "2026-2027 Fall" / "NLP" / "Week 1" / "lecture.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"same")
        context = self.context(source)
        target = self.target(context)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"same")
        result = self.service._organize_one(context, target)
        self.assertEqual("moved", result.status)
        self.assertFalse(source.exists())

        source.parent.mkdir(parents=True)
        source.write_bytes(b"same")
        target.write_bytes(b"different")
        result = self.service._organize_one(context, target)
        conflict = Path(result.local_path or "")
        self.assertEqual("lecture [42].pdf", conflict.name)
        self.assertEqual(b"same", conflict.read_bytes())
        self.assertEqual(b"different", target.read_bytes())

    def test_migrates_from_a_previous_archive_root(self) -> None:
        old_root = Path(self.temp.name) / "old-archive"
        source = (
            old_root
            / "2026-2027 Fall"
            / "NLP"
            / "Week 1"
            / "lecture.pdf"
        )
        source.parent.mkdir(parents=True)
        source.write_bytes(b"same")
        context = self.context(source)
        target = self.target(context)

        result = self.service._organize_one(context, target)

        self.assertEqual("moved", result.status)
        self.assertFalse(source.exists())
        self.assertTrue(old_root.is_dir())
        self.assertEqual(b"same", target.read_bytes())
        self.assertEqual(target, self.updates[-1][1])

    def test_parent_symlink_is_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(outside, target_is_directory=True)
        source = linked / "lecture.pdf"
        source.write_bytes(b"same")
        context = self.context(source)
        with self.assertRaisesRegex(ArchiveError, "symlink"):
            self.service._organize_one(context, self.target(context))
        self.assertTrue(source.exists())

    def test_cross_volume_copy_fsync_replace_then_delete(self) -> None:
        source = self.root / "2026-2027 Fall" / "NLP" / "Week 1" / "lecture.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"same")
        context = self.context(source)
        target = self.target(context)
        real_replace = os.replace

        def replace_with_cross_volume(first, second):
            if Path(first) == source:
                raise OSError(errno.EXDEV, "cross-device")
            return real_replace(first, second)

        with patch("sjtu_learning_assistant.archive_service.os.replace", side_effect=replace_with_cross_volume):
            result = self.service._organize_one(context, target)
        self.assertEqual("moved", result.status)
        self.assertFalse(source.exists())
        self.assertEqual(b"same", target.read_bytes())
        self.assertEqual([], list(target.parent.glob(".sjtu-move-*")))

    def test_digest_mismatch_preserves_source_and_database_path(self) -> None:
        source = (
            self.root
            / "2026-2027 Fall"
            / "NLP"
            / "Week 1"
            / "lecture.pdf"
        )
        source.parent.mkdir(parents=True)
        source.write_bytes(b"tampered")
        context = self.context(source)
        target = self.target(context)

        with self.assertRaisesRegex(ArchiveError, "数据库记录不一致"):
            self.service._organize_one(context, target)

        self.assertEqual(b"tampered", source.read_bytes())
        self.assertFalse(target.exists())
        self.assertEqual(0, len(self.updates))

    def test_database_failure_restores_same_volume_source(self) -> None:
        source = self.root / "2026-2027 Fall" / "NLP" / "Week 1" / "lecture.pdf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"same")
        context = self.context(source)
        target = self.target(context)
        self.service._update_local_path = lambda *_args: (_ for _ in ()).throw(
            RuntimeError("database unavailable")
        )
        with self.assertRaises(RuntimeError):
            self.service._organize_one(context, target)
        self.assertEqual(b"same", source.read_bytes())
        self.assertFalse(target.exists())


class ActiveCourseScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine(
            f"sqlite+pysqlite:///{Path(self.temp.name) / 'scope.db'}"
        )
        Base.metadata.create_all(self.engine)
        self.client = httpx.Client(base_url="https://oc.sjtu.edu.cn")

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_recent_canvas_sync_scope_excludes_historical_courses(self) -> None:
        old_time = datetime(2025, 9, 1, tzinfo=timezone.utc)
        with Session(self.engine) as session, session.begin():
            current = Course(
                source_id="current", name="Current", term_name="localized term", raw_data={}
            )
            old = Course(source_id="old", name="Old", term_name="2024 Fall", raw_data={})
            current.updated_at = NOW
            old.updated_at = old_time
            session.add_all([current, old])
            session.add(
                SyncState(
                    source="canvas",
                    resource="courses",
                    status="success",
                    last_sync_at=NOW,
                    last_success_at=NOW,
                )
            )
        service = ArchiveService(
            self.engine,
            self.client,
            archive_root=Path(self.temp.name) / "archive",
            use_recent_active_courses=True,
        )
        with Session(self.engine) as session:
            expected = session.query(Course.id).filter_by(source_id="current").scalar()
        self.assertEqual({expected}, service._active_course_ids())


class BridgeSettingsActionTests(unittest.TestCase):
    class Service:
        def settings_status(self):
            return {"archive_root": "/tmp/archive"}

        def update_settings(self, payload):
            return payload

        def pick_archive_root(self):
            return {"cancelled": True}

        def organize_archive(self):
            return {"moved": 1}

        def download_current_term(self):
            return {"downloaded": 1}

    def test_settings_and_archive_actions_are_allowlisted_and_validated(self) -> None:
        from desktop_app import DesktopBridge

        bridge = DesktopBridge(self.Service())
        self.assertTrue(bridge.invoke("settings_status")["ok"])
        self.assertEqual(
            {"auto_download_current_term": False},
            bridge.invoke(
                "settings_update", {"auto_download_current_term": False}
            )["data"],
        )
        self.assertEqual(
            {
                "ai_chat_send_shortcut": "cmd_enter",
                "ai_reply_language": "zh",
                "ai_attachment_context_budget": "economy",
                "ai_auto_open_activity": False,
                "ai_code_line_numbers": True,
                "theme_mode": "dark",
            },
            bridge.invoke(
                "settings_update",
                {
                    "ai_chat_send_shortcut": "cmd_enter",
                    "ai_reply_language": "zh",
                    "ai_attachment_context_budget": "economy",
                    "ai_auto_open_activity": False,
                    "ai_code_line_numbers": True,
                    "theme_mode": "dark",
                },
            )["data"],
        )
        self.assertFalse(
            bridge.invoke("settings_update", {"ai_unknown_preference": True})["ok"]
        )
        self.assertTrue(bridge.invoke("settings_pick_archive_root")["data"]["cancelled"])
        self.assertEqual(1, bridge.invoke("archive_organize")["data"]["moved"])
        self.assertEqual(
            1, bridge.invoke("archive_download_current_term")["data"]["downloaded"]
        )
        self.assertFalse(bridge.invoke("archive_organize", {"unexpected": True})["ok"])
        self.assertFalse(bridge.invoke("health", {"unexpected": True})["ok"])


class DashboardSyncSettingsTests(unittest.TestCase):
    def test_sync_command_uses_mail_and_archive_settings(self) -> None:
        from sjtu_learning_assistant.dashboard_service import DashboardService

        class Store:
            def __init__(self, auto_download: bool, mail_account: str = "") -> None:
                self.auto_download = auto_download
                self.mail_account = mail_account

            def resolve(self, **_overrides):
                return LocalSettings(
                    archive_root="/tmp/archive",
                    auto_download_current_term=self.auto_download,
                    organize_by_category=True,
                    mail_account=self.mail_account,
                )

        enabled = DashboardService(SimpleNamespace(), settings_store=Store(True))
        disabled = DashboardService(SimpleNamespace(), settings_store=Store(False))
        with_mail = DashboardService(
            SimpleNamespace(), settings_store=Store(True, "student-id")
        )
        self.assertNotIn("--no-download", enabled._fallback_sync_command())
        self.assertIn("--no-download", disabled._fallback_sync_command())
        self.assertIn("--canvas-only", enabled._fallback_sync_command())
        with_mail_command = with_mail._fallback_sync_command()
        self.assertNotIn("--canvas-only", with_mail_command)
        self.assertEqual(
            "student-id", with_mail_command[with_mail_command.index("--email") + 1]
        )
        self.assertEqual(
            ["--archive-root", "/tmp/archive"],
            enabled._fallback_sync_command()[-2:],
        )


if __name__ == "__main__":
    unittest.main()

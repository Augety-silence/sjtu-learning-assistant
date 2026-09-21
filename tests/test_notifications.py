from __future__ import annotations

import importlib
import subprocess
import unittest
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import launchd_control
import sync_data_to_db
from sjtu_learning_assistant.models import NotificationEvent
from sjtu_learning_assistant.notifications import (
    MAX_NOTIFICATIONS_PER_CATEGORY,
    MacOSNotificationSender,
    NotificationCandidate,
    NotificationError,
    NotificationService,
    sanitize_notification_text,
)
from sjtu_learning_assistant.repository import (
    CanvasPersistResult,
    UpsertResult,
    _result,
)


class FakeEventStore:
    def __init__(self, *, cursor: int | None = None) -> None:
        self.recorded: set[str] = set()
        self.suppressed: set[str] = set()
        self.sent: set[str] = set()
        self.cursor = cursor
        self.advanced: list[int] = []
        self.fail_reserve = False
        self.fail_record_sent = False

    def reserve(self, candidates, *, suppressed: bool):
        if self.fail_reserve:
            raise RuntimeError("模拟通知数据库失败")
        reserved = []
        for candidate in candidates:
            if candidate.event_key in self.recorded:
                continue
            reserved.append(candidate)
            if suppressed:
                self.recorded.add(candidate.event_key)
                self.suppressed.add(candidate.event_key)
        return reserved

    def record_sent(self, candidates):
        if self.fail_record_sent:
            raise RuntimeError("模拟通知账本更新失败")
        keys = {item.event_key for item in candidates}
        self.recorded.update(keys)
        self.sent.update(keys)

    def load_canvas_cursor(self):
        return self.cursor

    def advance_canvas_cursor(self, cursor: int):
        self.cursor = cursor
        self.advanced.append(cursor)


class RecordingSender:
    def __init__(self, *, fail_titles: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.fail_titles = fail_titles or set()

    def send(self, title: str, subtitle: str, body: str) -> None:
        self.calls.append((title, subtitle, body))
        if title in self.fail_titles:
            raise NotificationError("模拟通知失败")


def candidate(index: int, event_type: str = "new_file") -> NotificationCandidate:
    return NotificationCandidate(
        event_key=f"{event_type}:{index}",
        event_type=event_type,
        item_id=index,
        title=f"事项 {index}",
        subtitle="测试课程",
        body="查看详情",
    )


class NotificationSchemaTests(unittest.TestCase):
    def test_orm_table_and_0005_migration_are_linked(self) -> None:
        table = NotificationEvent.__table__
        self.assertEqual("notification_events", table.name)
        self.assertIn(
            "uq_notification_events_event_key",
            {constraint.name for constraint in table.constraints},
        )
        self.assertEqual(
            "items.id",
            str(next(iter(table.c.item_id.foreign_keys)).target_fullname),
        )
        migration = importlib.import_module(
            "migrations.versions.0005_add_notification_events"
        )
        self.assertEqual("0005", migration.revision)
        self.assertEqual("0004", migration.down_revision)


class MacOSNotificationSenderTests(unittest.TestCase):
    def test_uses_absolute_osascript_without_shell_and_passes_text_as_argv(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        hostile = '" & do shell script "touch /tmp/unsafe" & "'

        def runner(arguments, **kwargs):
            calls.append((arguments, kwargs))
            return subprocess.CompletedProcess(arguments, 0, "", "")

        MacOSNotificationSender(runner=runner, platform="darwin").send(
            hostile, "副标题", "正文\x00内容"
        )

        arguments, kwargs = calls[0]
        self.assertEqual("/usr/bin/osascript", arguments[0])
        self.assertEqual("-e", arguments[1])
        self.assertEqual("--", arguments[3])
        self.assertEqual(hostile, arguments[4])
        self.assertNotIn(hostile, arguments[2])
        self.assertNotIn("shell", kwargs)
        self.assertTrue(kwargs["capture_output"])
        self.assertNotIn("\x00", arguments[6])

    def test_rejects_non_macos_and_reports_osascript_failure(self) -> None:
        with self.assertRaisesRegex(NotificationError, "macOS"):
            MacOSNotificationSender(platform="linux").send("a", "b", "c")

        def runner(arguments, **kwargs):
            return subprocess.CompletedProcess(arguments, 1, "", "permission denied")

        with self.assertRaisesRegex(NotificationError, "permission denied"):
            MacOSNotificationSender(runner=runner, platform="darwin").send(
                "a", "b", "c"
            )

    def test_sanitizes_control_characters_and_limits_length(self) -> None:
        cleaned = sanitize_notification_text(" a\x00b\n" + "c" * 600)
        self.assertNotIn("\x00", cleaned)
        self.assertLessEqual(len(cleaned), 512)
        self.assertTrue(cleaned.endswith("…"))


class NotificationServiceTests(unittest.TestCase):
    def make_service(self, sender=None, store=None) -> NotificationService:
        return NotificationService(
            object(),
            sender=sender or RecordingSender(),
            store=store or FakeEventStore(),
            now=datetime(2026, 9, 21, tzinfo=timezone.utc),
        )

    def test_is_idempotent_across_repeated_processing(self) -> None:
        sender = RecordingSender()
        store = FakeEventStore()
        service = self.make_service(sender, store)
        events = [candidate(1), candidate(2)]

        first = service.process_candidates(events)
        second = service.process_candidates(events)

        self.assertEqual(2, first.sent_batches)
        self.assertEqual(0, second.sent_batches)
        self.assertEqual(2, second.duplicate_events)
        self.assertEqual(2, len(sender.calls))
        self.assertEqual({"new_file:1", "new_file:2"}, store.sent)

    def test_first_baseline_is_recorded_without_sending(self) -> None:
        sender = RecordingSender()
        store = FakeEventStore()
        result = self.make_service(sender, store).process_candidates(
            [candidate(1), candidate(2)], baseline=True
        )

        self.assertEqual(2, result.suppressed_events)
        self.assertEqual([], sender.calls)
        self.assertEqual({"new_file:1", "new_file:2"}, store.suppressed)

    def test_caps_each_category_at_three_and_merges_overflow(self) -> None:
        sender = RecordingSender()
        service = self.make_service(sender, FakeEventStore())
        result = service.process_candidates([candidate(index) for index in range(7)])

        self.assertEqual(MAX_NOTIFICATIONS_PER_CATEGORY, result.sent_batches)
        self.assertEqual(3, len(sender.calls))
        self.assertEqual("事项 0", sender.calls[0][0])
        self.assertEqual("事项 1", sender.calls[1][0])
        self.assertEqual("另有 5 条新文件", sender.calls[2][0])
        self.assertIn("事项 6", sender.calls[2][2])

    def test_sender_failure_is_retryable_on_next_round(self) -> None:
        sender = RecordingSender(fail_titles={"事项 1"})
        store = FakeEventStore()
        service = self.make_service(sender, store)

        first = service.process_candidates([candidate(1), candidate(2)])
        self.assertEqual(1, first.failed_batches)
        self.assertFalse(first.completed)
        self.assertEqual(1, first.sent_batches)
        self.assertNotIn("new_file:1", store.recorded)
        self.assertIn("new_file:2", store.sent)

        sender.fail_titles.clear()
        second = service.process_candidates([candidate(1), candidate(2)])

        self.assertEqual(1, second.sent_batches)
        self.assertEqual(1, second.duplicate_events)
        self.assertIn("new_file:1", store.sent)
        self.assertEqual(3, len(sender.calls))

    def test_sent_ledger_failure_keeps_event_retryable(self) -> None:
        sender = RecordingSender()
        store = FakeEventStore()
        store.fail_record_sent = True
        service = self.make_service(sender, store)

        first = service.process_candidates([candidate(1)])

        self.assertEqual(1, first.failed_batches)
        self.assertFalse(first.completed)
        self.assertNotIn("new_file:1", store.recorded)
        store.fail_record_sent = False
        second = service.process_candidates([candidate(1)])
        self.assertEqual(1, second.sent_batches)
        self.assertIn("new_file:1", store.sent)

    def test_existing_canvas_state_without_notification_marker_is_baseline(self) -> None:
        sender = RecordingSender()
        store = FakeEventStore(cursor=None)
        service = self.make_service(sender, store)
        generated = {
            "announcement": [candidate(1, "new_announcement")],
            "assignment": [candidate(2, "new_assignment")],
            "file": [candidate(3, "new_file")],
        }
        service._canvas_item_high_watermark = lambda: 30
        service._new_item_candidates = (
            lambda item_type, *, after_id: generated[item_type]
        )
        service._due_assignment_candidates = lambda: [
            candidate(4, "assignment_due_24h")
        ]

        summary = service.process_canvas_sync()

        self.assertEqual(4, summary.suppressed_events)
        self.assertEqual(0, summary.sent_batches)
        self.assertEqual([], sender.calls)
        self.assertEqual([30], store.advanced)
        self.assertEqual(
            {
                "new_announcement:1",
                "new_assignment:2",
                "new_file:3",
                "assignment_due_24h:4",
            },
            store.suppressed,
        )

    def test_cursor_is_not_advanced_until_failed_event_is_retried(self) -> None:
        sender = RecordingSender(fail_titles={"事项 1"})
        store = FakeEventStore(cursor=10)
        service = self.make_service(sender, store)
        service._canvas_item_high_watermark = lambda: 20
        service._new_item_candidates = lambda item_type, *, after_id: (
            [candidate(1)] if item_type == "file" else []
        )
        service._due_assignment_candidates = lambda: []

        first = service.process_canvas_sync()
        self.assertFalse(first.completed)
        self.assertEqual([], store.advanced)
        self.assertEqual(10, store.cursor)

        sender.fail_titles.clear()
        second = service.process_canvas_sync()
        self.assertTrue(second.completed)
        self.assertEqual([20], store.advanced)
        self.assertIn("new_file:1", store.sent)

    def test_notification_database_failure_is_isolated_and_cursor_stays_put(self) -> None:
        sender = RecordingSender()
        store = FakeEventStore(cursor=10)
        store.fail_reserve = True
        service = self.make_service(sender, store)
        service._canvas_item_high_watermark = lambda: 20
        service._new_item_candidates = lambda item_type, *, after_id: (
            [candidate(1)] if item_type == "file" else []
        )
        service._due_assignment_candidates = lambda: []

        summary = service.process_canvas_sync()

        self.assertFalse(summary.completed)
        self.assertGreaterEqual(summary.failed_batches, 1)
        self.assertEqual([], sender.calls)
        self.assertEqual([], store.advanced)

    def test_due_candidate_uses_assignment_id_and_due_time_for_idempotency(self) -> None:
        due_at = datetime(2026, 9, 21, 12, 30, tzinfo=timezone.utc)
        assignment = SimpleNamespace(
            id=20,
            source_id="assignment-20",
            name="期末报告",
            due_at=due_at,
        )

        class FakeQueryResult:
            def all(self):
                return [(assignment, "文本分析", 200)]

        class FakeSession:
            def __init__(self, _engine):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, _statement):
                return FakeQueryResult()

        with patch(
            "sjtu_learning_assistant.notifications.Session", FakeSession
        ):
            events = self.make_service()._due_assignment_candidates()

        self.assertEqual(1, len(events))
        self.assertEqual(200, events[0].item_id)
        self.assertIn("assignment-20", events[0].event_key)
        self.assertIn(due_at.isoformat(), events[0].event_key)
        self.assertIn("09 月 21 日 20:30", events[0].body)

    def test_upsert_result_exposes_only_new_source_ids_in_stable_order(self) -> None:
        result = _result(["new-2", "old", "new-1", "new-2"], {"old"})
        self.assertEqual(2, result.inserted)
        self.assertEqual(("new-2", "new-1"), result.inserted_source_ids)


class NotificationCliTests(unittest.TestCase):
    def test_sync_parser_supports_archive_options(self) -> None:
        args = sync_data_to_db.parse_args(
            [
                "--canvas-only",
                "--no-notify",
                "--no-download",
                "--archive-root",
                "/tmp/SJTU Archive",
                "--current-term",
                "2026-2027 Fall",
            ]
        )
        self.assertTrue(args.no_notify)
        self.assertTrue(args.no_download)
        self.assertEqual(Path("/tmp/SJTU Archive"), args.archive_root)
        self.assertEqual("2026-2027 Fall", args.current_term)

    def test_sync_main_disables_notification_processing(self) -> None:
        engine = SimpleNamespace(dispose=lambda: None)
        health = SimpleNamespace(database="test", server_version="16")
        with (
            patch.object(sync_data_to_db, "create_database_engine", return_value=engine),
            patch.object(sync_data_to_db, "check_database", return_value=health),
            patch.object(sync_data_to_db, "sync_canvas") as sync_canvas,
        ):
            self.assertEqual(
                0,
                sync_data_to_db.main(["--canvas-only", "--no-notify"]),
            )
        sync_canvas.assert_called_once_with(
            engine,
            notify=False,
            download=True,
            archive_root=Path.home() / "Documents" / "SJTU Study",
            current_term=None,
        )

    def test_sync_main_propagates_no_download(self) -> None:
        engine = SimpleNamespace(dispose=lambda: None)
        health = SimpleNamespace(database="test", server_version="16")
        with (
            patch.object(sync_data_to_db, "create_database_engine", return_value=engine),
            patch.object(sync_data_to_db, "check_database", return_value=health),
            patch.object(sync_data_to_db, "sync_canvas") as sync_canvas,
        ):
            self.assertEqual(
                0,
                sync_data_to_db.main(
                    [
                        "--canvas-only",
                        "--no-download",
                        "--archive-root",
                        "/tmp/SJTU Archive",
                        "--current-term",
                        "2026-2027 Fall",
                    ]
                ),
            )
        sync_canvas.assert_called_once_with(
            engine,
            notify=True,
            download=False,
            archive_root=Path("/tmp/SJTU Archive"),
            current_term="2026-2027 Fall",
        )

    def test_notification_failure_does_not_fail_completed_canvas_sync(self) -> None:
        empty = UpsertResult(0, 0, 0)
        persisted = CanvasPersistResult(
            empty, empty, empty, empty, empty, empty, empty
        )
        incremental = SimpleNamespace(etag=None, not_modified=False, records=[])
        client_context = MagicMock()
        client_context.__enter__.return_value = object()
        notification_service = MagicMock()
        notification_service.process_canvas_sync.side_effect = RuntimeError(
            "通知账本暂时不可用"
        )
        with (
            patch.object(sync_data_to_db, "get_sync_state", return_value=None),
            patch.object(sync_data_to_db, "get_token", return_value=("token", True)),
            patch.object(
                sync_data_to_db, "build_http_client", return_value=client_context
            ),
            patch.object(
                sync_data_to_db,
                "fetch_active_courses",
                return_value=[{"id": 1, "name": "课程"}],
            ),
            patch.object(
                sync_data_to_db,
                "fetch_course_announcements_incremental",
                return_value=incremental,
            ),
            patch.object(
                sync_data_to_db,
                "fetch_course_assignments_incremental",
                return_value=incremental,
            ),
            patch.object(
                sync_data_to_db,
                "fetch_course_files_incremental",
                return_value=incremental,
            ),
            patch.object(sync_data_to_db, "fetch_course_folders", return_value=[]),
            patch.object(sync_data_to_db, "fetch_course_modules", return_value=[]),
            patch.object(sync_data_to_db, "persist_canvas_data", return_value=persisted),
            patch.object(
                sync_data_to_db,
                "NotificationService",
                return_value=notification_service,
            ),
        ):
            sync_data_to_db.sync_canvas(object(), notify=True, download=False)
        notification_service.process_canvas_sync.assert_called_once()

    @staticmethod
    def launchd_options(**overrides: object) -> Namespace:
        values: dict[str, object] = {
            "canvas_only": True,
            "mail_only": False,
            "email": None,
            "no_notify": True,
            "no_download": False,
            "archive_root": str(Path.home() / "Documents" / "SJTU Study"),
            "current_term": None,
            "initial_mail_limit": 100,
        }
        values.update(overrides)
        return Namespace(**values)

    def test_launchd_propagates_archive_options(self) -> None:
        options = self.launchd_options(
            no_download=True,
            archive_root="/tmp/SJTU Archive",
            current_term="2026-2027 Fall",
        )
        arguments = launchd_control.sync_arguments(options)
        self.assertIn("--no-notify", arguments)
        self.assertIn("--no-download", arguments)
        self.assertIn("/tmp/SJTU Archive", arguments)
        self.assertIn("2026-2027 Fall", arguments)
        plist = launchd_control.build_plist(options)
        self.assertIn("--no-download", plist["ProgramArguments"])

    def test_launchd_rejects_blank_email(self) -> None:
        parser = launchd_control.build_parser()
        args = parser.parse_args(["install", "--email", "   "])
        with self.assertRaises(SystemExit):
            launchd_control.validate_sync_options(parser, args, non_interactive=True)

    def test_notify_test_command_reports_success_and_failure(self) -> None:
        with patch.object(launchd_control, "send_test_notification") as send:
            self.assertEqual(0, launchd_control.main(["notify-test"]))
            send.assert_called_once_with()
        with patch.object(
            launchd_control,
            "send_test_notification",
            side_effect=NotificationError("失败"),
        ):
            self.assertEqual(1, launchd_control.main(["notify-test"]))


if __name__ == "__main__":
    unittest.main()

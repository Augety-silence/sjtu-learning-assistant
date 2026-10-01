from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from desktop_app import DesktopBridge
from sjtu_learning_assistant.academic_features import AcademicFeatureService
from sjtu_learning_assistant.canvas_client import CanvasNetworkError
from sjtu_learning_assistant.desktop_database import SCHEMA_VERSION, bootstrap_sqlite
from sjtu_learning_assistant.models import Base, TimetableCourse, TimetableSession
from sjtu_learning_assistant.timetable import (
    SJTUEducationAPIProvider,
    TimetableError,
    TimetableService,
    parse_ics,
    parse_sjtu_lessons,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sjtu_lessons_anonymous.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


class TimetableTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.service = TimetableService(self.engine, FIXTURE)

    def tearDown(self):
        self.engine.dispose()

    def test_official_parser_preserves_explicit_week_day_period_and_gaps(self):
        courses, sessions, warnings = parse_sjtu_lessons(
            json.loads(FIXTURE.read_text())
        )
        self.assertEqual(1, len(courses))
        self.assertEqual([1, 3, 4], [item.week for item in sessions])
        self.assertNotIn(2, [item.week for item in sessions])
        self.assertEqual([1, 1, 1], [item.day for item in sessions])
        self.assertEqual([1, 1, 1], [item.period for item in sessions])
        self.assertEqual([2, 2, 2], [item.duration for item in sessions])
        self.assertEqual(2, len({item.classroom for item in sessions}))
        self.assertEqual([], warnings)

    def test_provider_is_explicitly_disabled_without_network_or_keyring(self):
        with (
            patch("socket.create_connection") as network,
            patch("keyring.get_password") as keyring,
        ):
            status = SJTUEducationAPIProvider().status()
        network.assert_not_called()
        keyring.assert_not_called()
        self.assertEqual("awaiting_configuration", status["state"])
        self.assertEqual("等待开放平台配置", status["message"])
        self.assertFalse(status["supportsOAuth"])
        self.assertNotIn("client_secret", str(status).lower())

    def test_preview_then_commit_is_idempotent_and_does_not_retain_path(self):
        first = self.service.preview_local_file(str(FIXTURE.resolve()))
        self.assertEqual(
            {"courses": 1, "sessions": 3},
            {key: first[key] for key in ("courses", "sessions")},
        )
        self.assertNotIn(str(FIXTURE), str(first))
        committed = self.service.commit_preview(first["previewId"])
        self.assertEqual(
            (1, 3, 0),
            (
                committed["importedCourses"],
                committed["importedSessions"],
                committed["updatedSessions"],
            ),
        )
        second = self.service.load_bundled_sample()
        committed_again = self.service.commit_preview(second["previewId"])
        self.assertEqual(
            (0, 0, 0),
            (
                committed_again["importedCourses"],
                committed_again["importedSessions"],
                committed_again["updatedSessions"],
            ),
        )
        with Session(self.engine) as db:
            self.assertEqual(1, db.scalar(select(func.count(TimetableCourse.id))))
            self.assertEqual(3, db.scalar(select(func.count(TimetableSession.id))))
        status = self.service.status()
        self.assertTrue(status["hasLocalData"])
        self.assertEqual("awaiting_configuration", status["state"])

    def test_ics_minimum_fields_timezone_and_uid_upsert(self):
        ics = """BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:course-event-1\r\nSUMMARY:自然语言处理\r\nDTSTART;TZID=Asia/Shanghai:20261012T080000\r\nDTEND;TZID=Asia/Shanghai:20261012T094000\r\nLOCATION:示例楼 301\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"""
        _courses, sessions, warnings = parse_ics(ics)
        self.assertEqual("course-event-1", sessions[0].source_id)
        self.assertEqual("Asia/Shanghai", str(sessions[0].start_at.tzinfo))
        self.assertEqual("示例楼 301", sessions[0].classroom)
        self.assertEqual([], warnings)
        with tempfile.NamedTemporaryFile(
            suffix=".ics", mode="w", encoding="utf-8"
        ) as file:
            file.write(ics)
            file.flush()
            result = self.service.commit_preview(
                self.service.preview_local_file(file.name)["previewId"]
            )
        self.assertEqual(1, result["importedSessions"])

    def test_schedule_contract_and_range(self):
        self.service.commit_preview(self.service.load_bundled_sample()["previewId"])
        result = self.service.schedule(
            datetime(2026, 9, 1, tzinfo=SHANGHAI),
            datetime(2026, 10, 1, tzinfo=SHANGHAI),
        )
        self.assertEqual(3, len(result["events"]))
        self.assertEqual(
            {
                "id",
                "title",
                "courseName",
                "startAt",
                "endAt",
                "location",
                "periodLabel",
                "eventType",
                "source",
                "canonicalCourseId",
            },
            set(result["events"][0]),
        )
        self.assertEqual("course", result["events"][0]["eventType"])

    def test_rejects_relative_unsupported_and_oversized_files(self):
        with self.assertRaises(TimetableError):
            self.service.preview_local_file("relative.json")
        with (
            tempfile.NamedTemporaryFile(suffix=".txt") as file,
            self.assertRaises(TimetableError),
        ):
            file.write(b"x")
            file.flush()
            self.service.preview_local_file(file.name)

    def test_migration_head_and_all_timetable_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = create_engine(
                "sqlite+pysqlite:///" + str(Path(directory) / "migration.db")
            )
            self.assertEqual("0019", SCHEMA_VERSION)
            self.assertEqual("0019", bootstrap_sqlite(engine))
            tables = set(inspect(engine).get_table_names())
            self.assertTrue(
                {
                    "canonical_courses",
                    "timetable_provider_connections",
                    "timetable_courses",
                    "timetable_sessions",
                    "timetable_import_runs",
                    "timetable_import_audit",
                }.issubset(tables)
            )
            engine.dispose()

    def test_calendar_keeps_local_cache_when_canvas_network_fails(self):
        self.service.commit_preview(self.service.load_bundled_sample()["previewId"])

        class OfflineCanvas:
            def calendar_events(self, context_codes, start_date, end_date):
                raise CanvasNetworkError("offline")

        academic = AcademicFeatureService(OfflineCanvas(), engine=self.engine)
        events = academic.events_between(
            datetime(2026, 9, 1, tzinfo=SHANGHAI),
            datetime(2026, 10, 1, tzinfo=SHANGHAI),
            course_ids=[1],
        )
        self.assertEqual(3, len(events))
        self.assertTrue(all(item["eventType"] == "course" for item in events))

    def test_desktop_bridge_contracts(self):
        class Dashboard:
            pass

        bridge = DesktopBridge(
            Dashboard(),
            file_picker=lambda: str(FIXTURE.resolve()),
            timetable_service=self.service,
        )
        status = bridge.invoke("timetable_status", {})
        self.assertEqual("awaiting_configuration", status["data"]["state"])
        preview = bridge.invoke("timetable_preview_local_file", {})["data"]
        self.assertEqual(
            {"previewId", "format", "courses", "sessions", "warnings"}, set(preview)
        )
        commit = bridge.invoke(
            "timetable_commit_preview", {"previewId": preview["previewId"]}
        )["data"]
        self.assertEqual(
            {"status", "importedCourses", "importedSessions", "updatedSessions"},
            set(commit),
        )
        schedule = bridge.invoke(
            "timetable_schedule",
            {
                "startAt": "2026-09-01T00:00:00+08:00",
                "endAt": "2026-10-01T00:00:00+08:00",
            },
        )["data"]
        self.assertEqual(3, len(schedule["events"]))


if __name__ == "__main__":
    unittest.main()

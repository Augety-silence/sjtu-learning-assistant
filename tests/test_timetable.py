from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from desktop_app import DesktopBridge
from sjtu_learning_assistant.academic_features import AcademicFeatureService
from sjtu_learning_assistant.desktop_database import SCHEMA_VERSION, bootstrap_sqlite
from sjtu_learning_assistant.models import (
    Base,
    TimetableCourse,
    TimetableImportAudit,
    TimetableImportRun,
    TimetableSession,
)
from sjtu_learning_assistant.timetable import (
    SJTUEducationAPIProvider,
    TimetableError,
    TimetableService,
    parse_ics,
    parse_sjtu_lessons,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sjtu_lessons_anonymous.json"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def lesson(source_id: str, name: str, sessions: list[dict]) -> dict:
    return {
        "id": source_id,
        "course": {"code": source_id.upper(), "name": name},
        "schedules": sessions,
    }


def occurrence(source_id: str, day: int) -> dict:
    return {
        "id": source_id,
        "startAt": f"2026-10-{day:02d}T08:00:00+08:00",
        "finishAt": f"2026-10-{day:02d}T09:40:00+08:00",
    }


class TimetableTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.service = TimetableService(self.engine, FIXTURE)

    def tearDown(self):
        self.engine.dispose()

    def commit_payload(self, lessons: list[dict]) -> dict:
        with tempfile.NamedTemporaryFile(
            suffix=".json", mode="w", encoding="utf-8"
        ) as file:
            json.dump({"data": {"lessons": lessons}}, file)
            file.flush()
            preview = self.service.preview_local_file(file.name)
        return self.service.commit_preview(preview["previewId"])

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
            (1, 3, 0, 0, 0),
            tuple(
                committed[key]
                for key in (
                    "importedCourses",
                    "importedSessions",
                    "updatedSessions",
                    "deletedCourses",
                    "deletedSessions",
                )
            ),
        )
        second = self.service.load_bundled_sample()
        committed_again = self.service.commit_preview(second["previewId"])
        self.assertEqual(
            (0, 0, 0, 0, 0),
            tuple(
                committed_again[key]
                for key in (
                    "importedCourses",
                    "importedSessions",
                    "updatedSessions",
                    "deletedCourses",
                    "deletedSessions",
                )
            ),
        )
        with Session(self.engine) as db:
            self.assertEqual(1, db.scalar(select(func.count(TimetableCourse.id))))
            self.assertEqual(3, db.scalar(select(func.count(TimetableSession.id))))
        self.assertTrue(self.service.status()["hasLocalData"])

    def test_failed_commit_keeps_preview_token_for_retry(self):
        preview = self.service.load_bundled_sample()
        original = self.service._commit_snapshot
        with patch.object(
            self.service, "_commit_snapshot", side_effect=RuntimeError("db failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "db failed"):
                self.service.commit_preview(preview["previewId"])
        with patch.object(self.service, "_commit_snapshot", wraps=original) as commit:
            result = self.service.commit_preview(preview["previewId"])
        self.assertEqual("committed", result["status"])
        commit.assert_called_once()
        with self.assertRaises(TimetableError):
            self.service.commit_preview(preview["previewId"])

    def test_incomplete_reimport_fails_before_snapshot_cleanup(self):
        self.commit_payload([lesson("a", "课程 A", [occurrence("a-1", 5)])])
        invalid_payloads = [
            [lesson("a", "课程 A", "not-a-list")],
            [lesson("a", "课程 A", ["not-an-object"])],
            [lesson("a", "课程 A", [{"id": "a-1", "startAt": "bad"}])],
        ]
        for lessons in invalid_payloads:
            with self.subTest(lessons=lessons):
                with tempfile.NamedTemporaryFile(
                    suffix=".json", mode="w", encoding="utf-8"
                ) as file:
                    json.dump({"data": {"lessons": lessons}}, file)
                    file.flush()
                    with self.assertRaisesRegex(TimetableError, "快照不完整|无效"):
                        self.service.preview_local_file(file.name)
                with Session(self.engine) as db:
                    self.assertEqual(
                        ["a-1"],
                        list(db.scalars(select(TimetableSession.source_id))),
                    )

    def test_duplicate_identical_source_ids_are_merged(self):
        repeated = occurrence("a-1", 5)
        courses, sessions, warnings = parse_sjtu_lessons(
            {
                "data": {
                    "lessons": [
                        lesson("a", "课程 A", [repeated, repeated]),
                        lesson("a", "课程 A", [repeated, repeated]),
                    ]
                }
            }
        )
        self.assertEqual((1, 1, []), (len(courses), len(sessions), warnings))
        result = self.commit_payload(
            [
                lesson("a", "课程 A", [repeated, repeated]),
                lesson("a", "课程 A", [repeated, repeated]),
            ]
        )
        self.assertEqual((1, 1), (result["importedCourses"], result["importedSessions"]))

    def test_duplicate_conflicting_source_ids_are_rejected(self):
        with self.assertRaisesRegex(TimetableError, "课程 source_id=a.*内容冲突"):
            parse_sjtu_lessons(
                {
                    "data": {
                        "lessons": [
                            lesson("a", "课程 A", [occurrence("a-1", 5)]),
                            lesson("a", "课程 A（冲突）", [occurrence("a-1", 5)]),
                        ]
                    }
                }
            )
        with self.assertRaisesRegex(TimetableError, "session source_id=a-1.*内容冲突"):
            parse_sjtu_lessons(
                {
                    "data": {
                        "lessons": [
                            lesson(
                                "a",
                                "课程 A",
                                [occurrence("a-1", 5), occurrence("a-1", 6)],
                            )
                        ]
                    }
                }
            )

    def test_reimport_replaces_source_snapshot_and_audits_deletions(self):
        first = self.commit_payload(
            [
                lesson("a", "课程 A", [occurrence("a-1", 5), occurrence("a-2", 12)]),
                lesson("b", "课程 B", [occurrence("b-1", 6)]),
            ]
        )
        self.assertEqual((2, 3), (first["importedCourses"], first["importedSessions"]))
        second = self.commit_payload([lesson("a", "课程 A", [occurrence("a-1", 5)])])
        self.assertEqual((1, 2), (second["deletedCourses"], second["deletedSessions"]))
        with Session(self.engine) as db:
            self.assertEqual(["a"], list(db.scalars(select(TimetableCourse.source_id))))
            self.assertEqual(["a-1"], list(db.scalars(select(TimetableSession.source_id))))
            latest_run = db.scalar(
                select(TimetableImportRun).order_by(TimetableImportRun.id.desc())
            )
            self.assertEqual((1, 2), (latest_run.deleted_courses, latest_run.deleted_sessions))
            deleted = list(
                db.scalars(
                    select(TimetableImportAudit).where(
                        TimetableImportAudit.import_run_id == latest_run.id,
                        TimetableImportAudit.action == "deleted",
                    )
                )
            )
            self.assertEqual(3, len(deleted))
        schedule = self.service.schedule(
            datetime(2026, 10, 1, tzinfo=SHANGHAI),
            datetime(2026, 11, 1, tzinfo=SHANGHAI),
        )
        self.assertEqual(["课程 A"], [event["title"] for event in schedule["events"]])

    def test_ics_preserves_tzid_utc_and_cross_day_times(self):
        ics = """BEGIN:VCALENDAR\r
BEGIN:VEVENT\r
UID:shanghai\r
SUMMARY:上海课程\r
DTSTART;TZID=Asia/Shanghai:20261012T080000\r
DTEND;TZID=Asia/Shanghai:20261012T094000\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:new-york\r
SUMMARY:纽约课程\r
DTSTART;TZID=America/New_York:20261012T200000\r
DTEND;TZID=America/New_York:20261012T220000\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:utc-cross-day\r
SUMMARY:UTC 课程\r
DTSTART:20261012T160000Z\r
DTEND:20261013T020000Z\r
END:VEVENT\r
END:VCALENDAR\r
"""
        _courses, sessions, warnings = parse_ics(ics)
        by_id = {item.source_id: item for item in sessions}
        self.assertEqual("Asia/Shanghai", str(by_id["shanghai"].start_at.tzinfo))
        self.assertEqual(datetime(2026, 10, 13, 8, tzinfo=SHANGHAI), by_id["new-york"].start_at)
        self.assertEqual(datetime(2026, 10, 13, 0, tzinfo=SHANGHAI), by_id["utc-cross-day"].start_at)
        self.assertEqual(datetime(2026, 10, 13, 10, tzinfo=SHANGHAI), by_id["utc-cross-day"].finish_at)
        self.assertEqual([], warnings)

    def test_ics_unknown_tzid_is_explicit_error(self):
        ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:bad-zone
SUMMARY:未知时区
DTSTART;TZID=Mars/Olympus:20261012T080000
DTEND;TZID=Mars/Olympus:20261012T090000
END:VEVENT
END:VCALENDAR
"""
        with self.assertRaisesRegex(TimetableError, "未知时区.*Mars/Olympus"):
            parse_ics(ics)

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
            self.assertEqual("0020", SCHEMA_VERSION)
            self.assertEqual("0020", bootstrap_sqlite(engine))
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
            run_columns = {
                column["name"]: column
                for column in inspect(engine).get_columns("timetable_import_runs")
            }
            self.assertTrue(
                {"deleted_courses", "deleted_sessions"}.issubset(run_columns)
            )
            for name in ("deleted_courses", "deleted_sessions"):
                self.assertFalse(run_columns[name]["nullable"])
                self.assertIn("0", str(run_columns[name]["default"]))
            engine.dispose()

    def test_real_0019_database_upgrades_to_0020_without_data_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "released-0019.db"
            engine = create_engine("sqlite+pysqlite:///" + str(path))
            config = Config()
            config.set_main_option(
                "script_location", str(Path(__file__).parent.parent / "migrations")
            )
            config.set_main_option("sqlalchemy.url", str(engine.url))
            self.assertEqual("0020", bootstrap_sqlite(engine))
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, "0019")
            columns_0019 = {
                column["name"]
                for column in inspect(engine).get_columns("timetable_import_runs")
            }
            self.assertNotIn("deleted_courses", columns_0019)
            self.assertNotIn("deleted_sessions", columns_0019)
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO timetable_import_runs "
                    "(provider, source_format, source_digest, status, "
                    "imported_courses, imported_sessions, updated_sessions, warnings) "
                    "VALUES ('local', 'ics', 'digest', 'committed', 1, 2, 3, '[]')"
                )
                run_id = connection.exec_driver_sql(
                    "SELECT id FROM timetable_import_runs"
                ).scalar_one()
                connection.exec_driver_sql(
                    "INSERT INTO timetable_import_audit "
                    "(import_run_id, entity_type, source_id, action, details) "
                    "VALUES (?, 'session', 'old-session', 'unchanged', '{}')",
                    (run_id,),
                )
            self.assertEqual("0020", bootstrap_sqlite(engine))
            with engine.begin() as connection:
                row = connection.exec_driver_sql(
                    "SELECT imported_courses, imported_sessions, updated_sessions, "
                    "deleted_courses, deleted_sessions FROM timetable_import_runs"
                ).one()
                self.assertEqual((1, 2, 3, 0, 0), tuple(row))
                connection.exec_driver_sql(
                    "INSERT INTO timetable_import_audit "
                    "(import_run_id, entity_type, source_id, action, details) "
                    "VALUES (?, 'session', 'removed-session', 'deleted', '{}')",
                    (run_id,),
                )
            engine.dispose()

    def test_canvas_calendar_and_local_schedule_are_separate(self):
        self.service.commit_preview(self.service.load_bundled_sample()["previewId"])

        class Canvas:
            def calendar_events(self, context_codes, start_date, end_date):
                return [
                    {
                        "id": "canvas-1",
                        "title": "Canvas 作业",
                        "context_code": "course_1",
                        "start_at": "2026-09-21T08:00:00+08:00",
                    }
                ]

        academic = AcademicFeatureService(Canvas(), engine=self.engine)
        canvas_events = academic.events_between(
            datetime(2026, 9, 1, tzinfo=SHANGHAI),
            datetime(2026, 10, 1, tzinfo=SHANGHAI),
            course_ids=[1],
        )
        local_events = self.service.schedule(
            datetime(2026, 8, 31, tzinfo=SHANGHAI),
            datetime(2026, 10, 12, tzinfo=SHANGHAI),
        )["events"]
        self.assertEqual(["canvas-1"], [event["id"] for event in canvas_events])
        self.assertEqual(3, len(local_events))
        self.assertTrue(set(event["id"] for event in canvas_events).isdisjoint(event["id"] for event in local_events))

    def test_production_desktop_bridge_contract_table_end_to_end(self):
        class Dashboard:
            pass

        bridge = DesktopBridge(
            Dashboard(),
            file_picker=lambda: str(FIXTURE.resolve()),
            timetable_service=self.service,
        )
        contract = [
            ("timetable_status", {}, {"state", "hasLocalData"}),
            (
                "timetable_preview_local_file",
                {},
                {"previewId", "format", "courses", "sessions", "warnings"},
            ),
            (
                "timetable_load_bundled_sample",
                {},
                {"previewId", "format", "courses", "sessions", "warnings"},
            ),
        ]
        results = {}
        for action, payload, required_keys in contract:
            response = bridge.invoke(action, payload)
            self.assertTrue(response["ok"], (action, response))
            self.assertTrue(required_keys.issubset(response["data"]), action)
            results[action] = response["data"]
        commit = bridge.invoke(
            "timetable_commit_preview",
            {"previewId": results["timetable_preview_local_file"]["previewId"]},
        )
        self.assertTrue(commit["ok"])
        self.assertTrue(
            {
                "status",
                "importedCourses",
                "importedSessions",
                "updatedSessions",
                "deletedCourses",
                "deletedSessions",
            }.issubset(commit["data"])
        )
        schedule = bridge.invoke(
            "timetable_schedule",
            {
                "startAt": "2026-08-31T00:00:00+08:00",
                "endAt": "2026-10-12T00:00:00+08:00",
            },
        )
        self.assertTrue(schedule["ok"])
        self.assertEqual(3, len(schedule["data"]["events"]))
        self.assertEqual(
            {
                "timetable_status",
                "timetable_schedule",
                "timetable_preview_local_file",
                "timetable_commit_preview",
                "timetable_load_bundled_sample",
            },
            set(bridge._handlers).intersection(
                {
                    "timetable_status",
                    "timetable_schedule",
                    "timetable_preview_local_file",
                    "timetable_commit_preview",
                    "timetable_load_bundled_sample",
                }
            ),
        )


if __name__ == "__main__":
    unittest.main()

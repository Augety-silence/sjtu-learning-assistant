from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from sjtu_learning_assistant.database import create_database_engine, default_sqlite_url
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.mail_client import (
    EmailBodyUpdate,
    EmailRecord,
    MailFetchResult,
    fetch_email_body_backfill,
)
from sjtu_learning_assistant.models import Email, UnifiedItem
from sjtu_learning_assistant.repository import (
    EmailBodyBackfillTarget,
    get_email_body_backfill_targets,
    persist_email_body_backfill,
    persist_emails,
)
from sync_data_to_db import sync_mail
from sjtu_learning_assistant.mail_sync import MailCheckError


@dataclass(frozen=True)
class Target:
    source_id: str
    uid: int
    uid_validity: str


class MailBodyBackfillFetchTests(unittest.TestCase):
    def test_uidvalidity_peek_and_single_message_failure_isolation(self) -> None:
        raw = b"Subject: Old\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nold body"

        class FakeIMAP:
            fetches: list[tuple[str, str]] = []
            readonly = False

            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def login(self, *_args):
                return "OK", []

            def select(self, _folder, *, readonly=False):
                type(self).readonly = readonly
                return "OK", []

            def response(self, name):
                return name, [b"99"]

            def uid(self, command, uid, query):
                self.fetches.append((uid, query))
                if uid == "2":
                    return "NO", []
                return "OK", [(b"1 (BODY[] {80}", raw), b")"]

        targets = (
            Target("student@example.com:INBOX:99:1", 1, "99"),
            Target("student@example.com:INBOX:99:2", 2, "99"),
            Target("student@example.com:INBOX:100:3", 3, "100"),
        )
        with patch("sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL", FakeIMAP):
            result = fetch_email_body_backfill(
                "student@example.com", "not-a-real-secret", targets
            )

        self.assertTrue(FakeIMAP.readonly)
        self.assertEqual(
            [("1", "(BODY.PEEK[])"), ("2", "(BODY.PEEK[])")],
            FakeIMAP.fetches,
        )
        self.assertEqual(3, result.selected)
        self.assertEqual(2, result.attempted)
        self.assertEqual(1, result.failed)
        self.assertEqual(1, result.uid_validity_mismatched)
        self.assertEqual("old body", result.updates[0].body_text)
        self.assertNotIn("3", [uid for uid, _query in FakeIMAP.fetches])


class MailBodyBackfillPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            default_sqlite_url(Path(self.temp.name) / "app.db")
        )
        bootstrap_sqlite(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp.cleanup()

    @staticmethod
    def record(uid: int, *, body_text: str | None = None) -> EmailRecord:
        return EmailRecord(
            source_id=f"student@example.com:INBOX:99:{uid}",
            subject=f"mail-{uid}",
            sender_name=None,
            sender_address="sender@example.com",
            sent_at=None,
            received_at=None,
            body_preview=body_text,
            is_unread=True,
            raw_data={"folder": "INBOX", "uid": uid, "uid_validity": "99"},
            body_text=body_text,
        )

    def test_selects_bounded_blank_batch_and_transaction_only_updates_bodies(self) -> None:
        persist_emails(
            self.engine,
            [self.record(1), self.record(2), self.record(3), self.record(4, body_text="kept")],
            cursor="cursor",
        )
        with Session(self.engine) as session, session.begin():
            session.execute(
                update(Email)
                .where(Email.source_id == "student@example.com:INBOX:99:1")
                .values(is_unread=False)
            )
            session.execute(
                update(UnifiedItem)
                .where(UnifiedItem.source_id == "student@example.com:INBOX:99:1")
                .values(is_read=True)
            )

        targets = get_email_body_backfill_targets(
            self.engine, "STUDENT@example.com", limit=2
        )
        self.assertEqual((1, 2), tuple(target.uid for target in targets))
        self.assertTrue(all(isinstance(target, EmailBodyBackfillTarget) for target in targets))

        updated = persist_email_body_backfill(
            self.engine,
            [EmailBodyUpdate(targets[0].source_id, "filled body", "filled preview")],
        )
        self.assertEqual(1, updated)
        with Session(self.engine) as session:
            email = session.scalar(
                select(Email).where(Email.source_id == targets[0].source_id)
            )
            item = session.scalar(
                select(UnifiedItem).where(UnifiedItem.source_id == targets[0].source_id)
            )
            self.assertEqual("filled body", email.body_text)
            self.assertEqual("filled preview", email.body_preview)
            self.assertFalse(email.is_unread)
            self.assertTrue(item.is_read)
            self.assertEqual(
                "kept",
                session.scalar(
                    select(Email.body_text).where(
                        Email.source_id == "student@example.com:INBOX:99:4"
                    )
                ),
            )


class SyncMailBackfillIsolationTests(unittest.TestCase):
    def test_backfill_failure_does_not_undo_incremental_sync(self) -> None:
        incremental = MailFetchResult(
            messages=[],
            cursor="next-cursor",
            uid_validity="99",
            highest_uid=10,
            bootstrap_truncated=False,
        )
        persisted = Mock(fetched=0, inserted=0, updated=0)
        engine = object()
        with (
            patch("sync_data_to_db.get_password", return_value=("secret", "test")),
            patch("sync_data_to_db.get_sync_state", return_value=None),
            patch("sync_data_to_db.fetch_incremental_mail", return_value=incremental),
            patch("sync_data_to_db.persist_emails", return_value=persisted) as persist,
            patch(
                "sync_data_to_db.get_email_body_backfill_targets",
                return_value=(Target("student@example.com:INBOX:99:1", 1, "99"),),
            ),
            patch(
                "sync_data_to_db.fetch_email_body_backfill",
                side_effect=MailCheckError("sensitive server detail"),
            ),
        ):
            sync_mail(engine, "student@example.com", 100)

        persist.assert_called_once_with(engine, incremental.messages, cursor="next-cursor")


if __name__ == "__main__":
    unittest.main()

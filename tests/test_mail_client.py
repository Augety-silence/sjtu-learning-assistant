from __future__ import annotations

import imaplib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sjtu_learning_assistant.mail_client import (
    fetch_email_body_backfill,
    fetch_incremental_mail,
    make_cursor,
    parse_cursor,
)
from sjtu_learning_assistant.mail_sync import MailCheckError


class MailCursorTests(unittest.TestCase):
    def test_cursor_round_trip(self) -> None:
        cursor = make_cursor("12345", 678)
        self.assertEqual(("12345", 678), parse_cursor(cursor))

    def test_invalid_cursor_forces_safe_bootstrap(self) -> None:
        self.assertEqual((None, 0), parse_cursor("not-json"))
        self.assertEqual((None, 0), parse_cursor(None))

    def test_negative_uid_is_clamped(self) -> None:
        self.assertEqual(("12345", 0), parse_cursor('{"uid_validity":"12345","last_uid":-1}'))

    def test_imap_abort_is_reported_as_connection_interruption(self) -> None:
        with patch(
            "sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL",
            side_effect=imaplib.IMAP4.abort("server disconnected"),
        ):
            with self.assertRaisesRegex(MailCheckError, "IMAP 连接被中断"):
                fetch_incremental_mail("student@example.com", "password", None)

    def test_backfill_fetch_abort_is_not_counted_as_one_bad_message(self) -> None:
        client = MagicMock()
        client.__enter__.return_value = client
        client.select.return_value = ("OK", [])
        client.response.return_value = ("UIDVALIDITY", [b"12345"])
        client.uid.side_effect = imaplib.IMAP4.abort("server disconnected")
        target = SimpleNamespace(source_id="mail-1", uid=7, uid_validity="12345")

        with patch(
            "sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL",
            return_value=client,
        ):
            with self.assertRaisesRegex(MailCheckError, "IMAP 连接被中断"):
                fetch_email_body_backfill(
                    "student@example.com", "password", [target]
                )


if __name__ == "__main__":
    unittest.main()

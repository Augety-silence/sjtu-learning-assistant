from __future__ import annotations

import imaplib
import unittest
from unittest.mock import patch

from sjtu_learning_assistant.mail_client import fetch_incremental_mail, make_cursor, parse_cursor
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


if __name__ == "__main__":
    unittest.main()

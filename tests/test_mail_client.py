from __future__ import annotations

import unittest

from sjtu_learning_assistant.mail_client import make_cursor, parse_cursor


class MailCursorTests(unittest.TestCase):
    def test_cursor_round_trip(self) -> None:
        cursor = make_cursor("12345", 678)
        self.assertEqual(("12345", 678), parse_cursor(cursor))

    def test_invalid_cursor_forces_safe_bootstrap(self) -> None:
        self.assertEqual((None, 0), parse_cursor("not-json"))
        self.assertEqual((None, 0), parse_cursor(None))

    def test_negative_uid_is_clamped(self) -> None:
        self.assertEqual(("12345", 0), parse_cursor('{"uid_validity":"12345","last_uid":-1}'))


if __name__ == "__main__":
    unittest.main()

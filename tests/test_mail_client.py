from __future__ import annotations
import json
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
    def test_expunge_is_skipped_but_no_bad_never_advance_cursor(self) -> None:
        for fetch_status, fetch_data in (("OK", [None]), ("NO", []), ("BAD", [])):
            with self.subTest(status=fetch_status):
                client = MagicMock()
                client.__enter__.return_value = client
                client.select.return_value = ("OK", [])
                client.response.return_value = ("UIDVALIDITY", [b"12345"])
                client.uid.side_effect = [("OK", [b"7"]), (fetch_status, fetch_data)]
                with patch("sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL", return_value=client):
                    if fetch_status == "OK":
                        result = fetch_incremental_mail("student@example.com", "fake", None)
                        self.assertEqual([], result.messages)
                        self.assertEqual(7, result.highest_uid)
                    else:
                        with self.assertRaises(MailCheckError):
                            fetch_incremental_mail("student@example.com", "fake", None)

    def test_initial_sync_keeps_checkpoint_for_older_uids(self) -> None:
        client = MagicMock()
        client.__enter__.return_value = client
        client.select.return_value = ("OK", [])
        client.response.return_value = ("UIDVALIDITY", [b"12345"])

        fetched_uids = []
        all_uids = b" ".join(str(uid).encode() for uid in range(1, 251))

        def fake_uid(command, *args):
            if command == "search":
                return "OK", [all_uids]
            if command == "fetch":
                fetched_uids.append(int(args[0]))
                return "OK", [(b"fake metadata", b"fake message")]
            raise AssertionError(f"Unexpected IMAP command: {command}")

        client.uid.side_effect = fake_uid

        with (
            patch(
                "sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL",
                return_value=client,
            ),
            patch(
                "sjtu_learning_assistant.mail_client._extract_fetch_parts",
                return_value=(b"FLAGS ()", b"fake message"),
            ),
            patch(
                "sjtu_learning_assistant.mail_client._parse_message",
                side_effect=lambda **kwargs: kwargs["uid"],
            ),
        ):
            result = fetch_incremental_mail(
                "student@example.com",
                "fake",
                None,
                initial_limit=100,
            )

        self.assertEqual(list(range(151, 251)), fetched_uids)
        cursor = json.loads(result.cursor)
        self.assertEqual(250, cursor["last_uid"])
        self.assertEqual(151, cursor["history_before"])
        self.assertFalse(cursor["history_complete"])

    def test_history_backfill_resumes_and_keeps_new_mail_incremental(self) -> None:
        cursor = None
        fetched_overall: list[int] = []
        batches: list[list[int]] = []

        # A new message arrives after the first sync while older history is pending.
        mailbox_versions = [range(1, 251), range(1, 252), range(1, 252)]
        for mailbox_uids in mailbox_versions:
            client = MagicMock()
            client.__enter__.return_value = client
            client.select.return_value = ("OK", [])
            client.response.return_value = ("UIDVALIDITY", [b"12345"])
            fetched_this_batch: list[int] = []
            uid_list = b" ".join(str(uid).encode() for uid in mailbox_uids)

            def fake_uid(command, *args):
                if command == "search":
                    return "OK", [uid_list]
                if command == "fetch":
                    uid = int(args[0])
                    fetched_this_batch.append(uid)
                    return "OK", [(b"fake metadata", b"fake message")]
                raise AssertionError(f"Unexpected IMAP command: {command}")

            client.uid.side_effect = fake_uid
            with (
                patch(
                    "sjtu_learning_assistant.mail_client.imaplib.IMAP4_SSL",
                    return_value=client,
                ),
                patch(
                    "sjtu_learning_assistant.mail_client._extract_fetch_parts",
                    return_value=(b"FLAGS ()", b"fake message"),
                ),
                patch(
                    "sjtu_learning_assistant.mail_client._parse_message",
                    side_effect=lambda **kwargs: kwargs["uid"],
                ),
            ):
                result = fetch_incremental_mail(
                    "student@example.com", "fake", cursor, initial_limit=100
                )

            cursor = result.cursor
            batches.append(fetched_this_batch)
            fetched_overall.extend(fetched_this_batch)

        self.assertEqual(list(range(151, 251)), batches[0])
        self.assertEqual([*range(51, 151), 251], batches[1])
        self.assertEqual(list(range(1, 51)), batches[2])
        self.assertEqual(list(range(1, 252)), sorted(fetched_overall))
        self.assertEqual(len(fetched_overall), len(set(fetched_overall)))
        final_cursor = json.loads(cursor)
        self.assertTrue(final_cursor["history_complete"])
        self.assertIsNone(final_cursor["history_before"])


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

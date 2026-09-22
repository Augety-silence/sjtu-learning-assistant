from __future__ import annotations

import tempfile
import unittest
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from sjtu_learning_assistant.database import create_database_engine, default_sqlite_url
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.mail_client import (
    EmailAttachment as AttachmentDTO,
    EmailBodyUpdate,
    EmailRecord,
    _message_content,
    _parse_message,
)
from sjtu_learning_assistant.models import Email, EmailAttachment, UnifiedItem
from sjtu_learning_assistant.repository import (
    persist_email_body_backfill,
    persist_emails,
)
from sjtu_learning_assistant.text_content import sanitize_html


class HTMLSanitizerTests(unittest.TestCase):
    def test_strict_whitelist_urls_and_suppressed_content(self) -> None:
        dirty = """
        <div style="color:red" onclick="bad()"><custom>kept</custom>
        <script>alert(1)</script><style>.x{}</style><iframe>hidden</iframe>
        <a href="http://bad" onmouseover="x">bad</a>
        <a href="https://safe.example/a?q=1">safe</a>
        <img src="cid:<logo@example>" srcset="https://bad 2x" style="x">
        <img src="https://remote.example/tracker.png">
        <table><tbody><tr><th colspan="2">H</th><td>V</td></tr></tbody></table>
        </div>
        """
        cleaned = sanitize_html(dirty)
        self.assertIn("<div>", cleaned or "")
        self.assertIn("<a>bad</a>", cleaned or "")
        self.assertIn(
            '<a href="https://safe.example/a?q=1">safe</a>', cleaned or ""
        )
        self.assertIn('<img data-resource-id="logo@example">', cleaned or "")
        self.assertIn(
            "<table><tbody><tr><th>H</th><td>V</td></tr></tbody></table>",
            cleaned or "",
        )
        self.assertNotIn("alert", cleaned or "")
        self.assertNotIn("style", cleaned or "")
        self.assertNotIn("src=", cleaned or "")

    def test_malformed_html_is_closed_and_escaped(self) -> None:
        self.assertEqual("<p>&lt;x&gt;<b>bold</b></p>", sanitize_html("<p>&lt;x&gt;<b>bold"))


class MIMEParsingTests(unittest.TestCase):
    def test_alternative_related_and_mixed_parts(self) -> None:
        message = EmailMessage()
        message["Subject"] = "rich"
        message.set_content("café body", charset="iso-8859-1")
        message.add_alternative(
            '<p onclick="x">rich <strong>body</strong><img src="cid:logo"></p>',
            subtype="html",
            charset="utf-8",
        )
        html_part = message.get_payload()[1]
        html_part.make_related()
        html_part.add_related(
            b"image-data",
            maintype="image",
            subtype="png",
            cid="<logo>",
            filename="../logo.png",
            disposition="inline",
        )
        message.add_attachment(
            "附件内容".encode("gb18030"),
            maintype="application",
            subtype="octet-stream",
            filename="报告.bin",
        )

        parsed = _parse_message(
            email_address="student@example.com",
            uid_validity="99",
            uid=7,
            metadata=b'7 (FLAGS () INTERNALDATE "22-Sep-2026 12:00:00 +0000")',
            headers=message.as_bytes(policy=policy.default),
        )

        self.assertEqual("café body", parsed.body_text)
        self.assertEqual(
            '<p>rich <strong>body</strong><img data-resource-id="logo"></p>',
            parsed.body_html,
        )
        self.assertEqual(2, len(parsed.attachments))
        inline = parsed.attachments[0]
        self.assertEqual("logo", inline.resource_id)
        self.assertEqual(b"image-data", inline.payload)
        self.assertTrue(inline.is_inline)
        self.assertEqual("报告.bin", parsed.attachments[1].filename)

    def test_attachment_limits_keep_metadata_but_drop_payloads(self) -> None:
        message = EmailMessage()
        message.set_content("body")
        message.add_attachment(b"abc", maintype="application", subtype="octet-stream", filename="a.bin")
        message.add_attachment(b"def", maintype="application", subtype="octet-stream", filename="b.bin")
        parsed = BytesParser(policy=policy.default).parsebytes(message.as_bytes())

        with patch("sjtu_learning_assistant.mail_client.MAX_ATTACHMENT_BYTES", 2):
            _text, _html, attachments = _message_content(parsed)
        self.assertEqual([3, 3], [item.size for item in attachments])
        self.assertEqual([None, None], [item.payload for item in attachments])

        with (
            patch("sjtu_learning_assistant.mail_client.MAX_ATTACHMENT_BYTES", 10),
            patch("sjtu_learning_assistant.mail_client.MAX_MESSAGE_ATTACHMENT_BYTES", 5),
        ):
            _text, _html, attachments = _message_content(parsed)
        self.assertEqual([None, None], [item.payload for item in attachments])


class RichMailPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.attachment_root = root / "mail-attachments"
        self.engine = create_database_engine(default_sqlite_url(root / "app.db"))
        bootstrap_sqlite(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    @staticmethod
    def record(payload: bytes | None = b"payload") -> EmailRecord:
        return EmailRecord(
            source_id="student@example.com:INBOX:99:7",
            subject="rich",
            sender_name=None,
            sender_address="sender@example.com",
            sent_at=None,
            received_at=None,
            body_preview="body",
            is_unread=True,
            raw_data={"folder": "INBOX", "uid": 7, "uid_validity": "99"},
            body_text="body",
            body_html="<p>body</p>",
            attachments=(
                AttachmentDTO(
                    resource_id="logo",
                    filename="../../logo.png",
                    content_type="image/png",
                    content_id="logo",
                    disposition="inline",
                    size=7,
                    payload=payload,
                    is_inline=True,
                ),
            ),
        )

    def test_persists_metadata_to_db_and_payload_to_controlled_path_idempotently(self) -> None:
        record = self.record()
        persist_emails(
            self.engine,
            [record],
            cursor="cursor-1",
            attachment_root=self.attachment_root,
        )
        with Session(self.engine) as session, session.begin():
            session.query(Email).filter_by(source_id=record.source_id).update({"is_unread": False})
            session.query(UnifiedItem).filter_by(source_id=record.source_id).update({"is_read": True})
        persist_emails(
            self.engine,
            [record],
            cursor="cursor-2",
            attachment_root=self.attachment_root,
        )

        with Session(self.engine) as session:
            email = session.scalar(select(Email))
            attachment = session.scalar(select(EmailAttachment))
            self.assertEqual("<p>body</p>", email.body_html)
            self.assertFalse(email.is_unread)
            self.assertTrue(session.scalar(select(UnifiedItem)).is_read)
            self.assertEqual(7, attachment.size)
            self.assertIsNotNone(attachment.sha256)
            path = Path(attachment.local_path or "")
            self.assertEqual(b"payload", path.read_bytes())
            self.assertEqual(self.attachment_root, path.parents[1])
            self.assertNotIn("..", path.name)
            self.assertEqual(1, len(list(self.attachment_root.rglob("*-logo.png"))))

    def test_metadata_only_attachment_is_not_written(self) -> None:
        persist_emails(
            self.engine,
            [self.record(None)],
            cursor="cursor",
            attachment_root=self.attachment_root,
        )
        with Session(self.engine) as session:
            attachment = session.scalar(select(EmailAttachment))
            self.assertIsNone(attachment.local_path)
            self.assertIsNone(attachment.sha256)

    def test_backfill_persists_rich_body_and_attachment_without_read_state_changes(self) -> None:
        base = self.record(None)
        base = EmailRecord(
            **{
                **base.__dict__,
                "body_text": None,
                "body_html": None,
                "body_preview": None,
                "attachments": (),
            }
        )
        persist_emails(
            self.engine,
            [base],
            cursor="cursor",
            attachment_root=self.attachment_root,
        )
        with Session(self.engine) as session, session.begin():
            session.query(Email).update({"is_unread": False})
            session.query(UnifiedItem).update({"is_read": True})

        update = EmailBodyUpdate(
            source_id=base.source_id,
            body_text="filled",
            body_preview="filled",
            body_html="<p>filled</p>",
            attachments=self.record().attachments,
        )
        self.assertEqual(
            1,
            persist_email_body_backfill(
                self.engine,
                [update],
                attachment_root=self.attachment_root,
            ),
        )
        with Session(self.engine) as session:
            email = session.scalar(select(Email))
            self.assertEqual("<p>filled</p>", email.body_html)
            self.assertFalse(email.is_unread)
            self.assertTrue(session.scalar(select(UnifiedItem)).is_read)
            self.assertEqual(1, len(list(session.scalars(select(EmailAttachment)))))

    def test_symlink_target_is_rejected_without_overwriting_external_file(self) -> None:
        record = self.record()
        persist_emails(
            self.engine,
            [record],
            cursor="cursor",
            attachment_root=self.attachment_root,
        )
        with Session(self.engine) as session:
            path = Path(session.scalar(select(EmailAttachment)).local_path or "")
        path.unlink()
        external = Path(self.temporary.name) / "external.bin"
        external.write_bytes(b"outside")
        path.symlink_to(external)

        with self.assertRaisesRegex(RuntimeError, "符号链接"):
            persist_emails(
                self.engine,
                [record],
                cursor="cursor-2",
                attachment_root=self.attachment_root,
            )
        self.assertEqual(b"outside", external.read_bytes())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import base64
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from desktop_app import DesktopBridge
from sjtu_learning_assistant.dashboard_service import (
    DashboardError,
    DashboardService,
    NotFoundError,
)
from sjtu_learning_assistant.database import create_database_engine, default_sqlite_url
from sjtu_learning_assistant.desktop_database import bootstrap_sqlite
from sjtu_learning_assistant.mail_client import EmailAttachment as AttachmentDTO
from sjtu_learning_assistant.mail_client import EmailRecord
from sjtu_learning_assistant.models import Email, EmailAttachment
from sjtu_learning_assistant.repository import persist_canvas_data, persist_emails


class FakeCanvasClient:
    def __init__(self, responses: list[tuple[int, dict[str, str], bytes]]) -> None:
        self.responses = list(responses)
        self.requests: list[str] = []
        self.closed = False

    def build_request(self, method: str, url: str, **_kwargs: object) -> httpx.Request:
        return httpx.Request(method, url)

    def send(self, request: httpx.Request, **_kwargs: object) -> httpx.Response:
        self.requests.append(str(request.url))
        status, headers, content = self.responses.pop(0)
        return httpx.Response(
            status, headers=headers, content=content, request=request
        )

    def close(self) -> None:
        self.closed = True


class DashboardRichResourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.attachment_root = self.root / "mail-attachments"
        self.engine = create_database_engine(default_sqlite_url(self.root / "app.db"))
        bootstrap_sqlite(self.engine)
        self.commands: list[list[str]] = []

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary.cleanup()

    def command_runner(self, command: list[str], **_kwargs: object) -> SimpleNamespace:
        self.commands.append(list(command))
        return SimpleNamespace(returncode=0)

    @staticmethod
    def email_record(
        source_id: str,
        attachment_id: str,
        *,
        payload: bytes = b"png-data",
        filename: str = "../logo.png",
    ) -> EmailRecord:
        return EmailRecord(
            source_id=source_id,
            subject="富邮件",
            sender_name="教师",
            sender_address="teacher@example.com",
            sent_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
            received_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
            body_preview="正文",
            is_unread=True,
            raw_data={"uid": 1, "rich_content_fetched": True},
            body_text="正文",
            body_html=f'<p>正文<img data-resource-id="{attachment_id}"></p>',
            attachments=(
                AttachmentDTO(
                    resource_id=attachment_id,
                    filename=filename,
                    content_type="image/png",
                    content_id=attachment_id,
                    disposition="inline",
                    size=len(payload),
                    payload=payload,
                    is_inline=True,
                ),
            ),
        )

    def persist_mail(self) -> None:
        persist_emails(
            self.engine,
            [
                self.email_record("mail:one", "cid-one"),
                self.email_record("mail:two", "cid-two"),
            ],
            cursor="cursor",
            attachment_root=self.attachment_root,
        )

    def service(self, **kwargs: object) -> DashboardService:
        return DashboardService(
            self.engine,
            mail_attachments_root=self.attachment_root,
            command_runner=self.command_runner,
            **kwargs,
        )

    def test_email_detail_has_safe_attachment_dto_and_inline_data(self) -> None:
        self.persist_mail()
        detail = self.service().message_detail("email", "mail:one")
        self.assertEqual("html", detail["format"])
        self.assertFalse(detail["pending_body_sync"])
        self.assertIn('data-resource-id="cid-one"', detail["body_html"])
        self.assertEqual("正文", detail["body"])
        attachment = detail["attachments"][0]
        self.assertEqual(
            {"id", "name", "type", "size", "is_inline", "available", "inline_data_url"},
            set(attachment),
        )
        self.assertEqual("cid-one", attachment["id"])
        self.assertEqual("logo.png", attachment["name"])
        self.assertTrue(attachment["available"])
        self.assertEqual(
            "data:image/png;base64," + base64.b64encode(b"png-data").decode("ascii"),
            attachment["inline_data_url"],
        )
        self.assertNotIn("local_path", attachment)

    def test_mail_attachment_checks_ownership_and_uses_fixed_open_arguments(self) -> None:
        self.persist_mail()
        service = self.service()
        opened = service.open_mail_attachment("mail:one", "cid-one")
        revealed = service.reveal_mail_attachment("mail:one", "cid-one")
        self.assertEqual("opened", opened["status"])
        self.assertEqual("revealed", revealed["status"])
        self.assertEqual("/usr/bin/open", self.commands[0][0])
        self.assertEqual(["/usr/bin/open", "-R"], self.commands[1][:2])
        self.assertTrue(Path(self.commands[0][-1]).is_relative_to(self.attachment_root.resolve()))
        with self.assertRaises(NotFoundError):
            service.open_mail_attachment("mail:two", "cid-one")

    def test_mail_attachment_rejects_outside_directory_and_symlink_component(self) -> None:
        self.persist_mail()
        service = self.service()
        outside = self.root / "outside.png"
        outside.write_bytes(b"outside")
        with Session(self.engine) as session, session.begin():
            attachment = session.scalar(
                select(EmailAttachment)
                .join(Email)
                .where(Email.source_id == "mail:one")
            )
            attachment.local_path = str(outside)
        with self.assertRaises(NotFoundError):
            service.open_mail_attachment("mail:one", "cid-one")

        real_directory = self.attachment_root / "real"
        real_directory.mkdir()
        target = real_directory / "image.png"
        target.write_bytes(b"image")
        linked_directory = self.attachment_root / "linked"
        linked_directory.symlink_to(real_directory, target_is_directory=True)
        with Session(self.engine) as session, session.begin():
            attachment = session.scalar(
                select(EmailAttachment)
                .join(Email)
                .where(Email.source_id == "mail:one")
            )
            attachment.local_path = str(linked_directory / "image.png")
        with self.assertRaisesRegex(DashboardError, "不安全"):
            service.reveal_mail_attachment("mail:one", "cid-one")
        self.assertEqual([], self.commands)

    def test_email_resource_requires_html_whitelist_and_inline_image(self) -> None:
        self.persist_mail()
        service = self.service()
        resource = service.message_resource("email", "mail:one", "cid-one")
        self.assertTrue(resource["data_url"].startswith("data:image/png;base64,"))
        with self.assertRaises(NotFoundError):
            service.message_resource("email", "mail:one", "cid-two")
        with self.assertRaises(NotFoundError):
            service.message_resource("email", "mail:one", "invented")

    def persist_canvas(self) -> None:
        persist_canvas_data(
            self.engine,
            raw_courses=[{"id": 1, "name": "课程"}],
            announcements_by_course={
                "1": [
                    {
                        "id": 11,
                        "title": "公告",
                        "message": (
                            '<p onclick="bad()">公告<img src="https://oc.sjtu.edu.cn/files/1/preview">'
                            '<a href="https://example.edu/info">链接</a></p><script>bad()</script>'
                        ),
                        "posted_at": "2026-09-22T01:00:00Z",
                        "html_url": "https://oc.sjtu.edu.cn/a",
                        "attachments": [
                            {
                                "filename": "../../资料.pdf",
                                "size": 42,
                                "url": "https://oc.sjtu.edu.cn/files/42/download",
                            },
                            {"filename": "bad", "url": "http://oc.sjtu.edu.cn/bad"},
                        ],
                    }
                ]
            },
            assignments_by_course={
                "1": [
                    {
                        "id": 12,
                        "name": "作业",
                        "description": '<div>说明<img src="https://oc.sjtu.edu.cn/images/a.webp"></div>',
                        "updated_at": "2026-09-22T02:00:00Z",
                        "html_url": "https://oc.sjtu.edu.cn/b",
                        "files": [
                            {
                                "display_name": "folder\\答案.txt",
                                "size": 8,
                                "download_url": "https://files.example.edu/a",
                            }
                        ],
                    }
                ]
            },
        )

    def test_canvas_detail_sanitizes_html_extracts_resources_and_safe_files(self) -> None:
        self.persist_canvas()
        service = self.service()
        announcement = service.message_detail("announcement", "11")
        assignment = service.message_detail("assignment", "12")
        self.assertIn("data-resource-id=", announcement["body_html"])
        self.assertIn('<a href="https://example.edu/info">链接</a>', announcement["body_html"])
        self.assertNotIn("onclick", announcement["body_html"])
        self.assertNotIn("script", announcement["body_html"])
        self.assertEqual("公告链接", announcement["body"])
        self.assertEqual("资料.pdf", announcement["attachments"][0]["name"])
        self.assertEqual(42, announcement["attachments"][0]["size"])
        self.assertEqual(1, len(announcement["attachments"]))
        self.assertEqual("答案.txt", assignment["attachments"][0]["name"])
        self.assertEqual("说明", assignment["body"])
        self.assertEqual(announcement["resources"], service.message_detail("announcement", "11")["resources"])

    def test_canvas_resource_allows_same_origin_redirect_and_returns_only_data_url(self) -> None:
        self.persist_canvas()
        client = FakeCanvasClient(
            [
                (302, {"location": "/files/1/content"}, b""),
                (200, {"content-type": "image/png"}, b"image"),
            ]
        )
        service = self.service(canvas_client_factory=lambda: client)
        resource_id = service.message_detail("announcement", "11")["resources"][0]["id"]
        result = service.message_resource("announcement", "11", resource_id)
        self.assertEqual(
            "data:image/png;base64," + base64.b64encode(b"image").decode("ascii"),
            result["data_url"],
        )
        self.assertEqual(2, len(client.requests))
        self.assertTrue(all(url.startswith("https://oc.sjtu.edu.cn/") for url in client.requests))
        self.assertEqual({"data_url"}, set(result))
        self.assertTrue(client.closed)

    def test_canvas_resource_rejects_illegal_id_redirect_type_and_size(self) -> None:
        self.persist_canvas()
        factory_calls = 0

        def unused_factory() -> FakeCanvasClient:
            nonlocal factory_calls
            factory_calls += 1
            return FakeCanvasClient([])

        service = self.service(canvas_client_factory=unused_factory)
        with self.assertRaises(NotFoundError):
            service.message_resource("announcement", "11", "invented")
        self.assertEqual(0, factory_calls)

        resource_id = service.message_detail("announcement", "11")["resources"][0]["id"]
        for response, error in (
            ((302, {"location": "https://evil.example/image"}, b""), "不安全"),
            ((200, {"content-type": "text/html"}, b"not-image"), "图片"),
        ):
            client = FakeCanvasClient([response])
            with self.subTest(error=error), self.assertRaisesRegex(DashboardError, error):
                self.service(canvas_client_factory=lambda client=client: client).message_resource(
                    "announcement", "11", resource_id
                )
            self.assertTrue(client.closed)

        client = FakeCanvasClient([(200, {"content-type": "image/png"}, b"1234")])
        with patch("sjtu_learning_assistant.dashboard_service.INLINE_IMAGE_LIMIT", 3):
            with self.assertRaisesRegex(DashboardError, "大小"):
                self.service(canvas_client_factory=lambda: client).message_resource(
                    "announcement", "11", resource_id
                )


class BridgeRichResourceValidationTests(unittest.TestCase):
    class Service:
        def message_resource(self, kind: str, source_id: str, resource_id: str) -> dict[str, str]:
            return {"kind": kind, "source_id": source_id, "resource_id": resource_id}

        def open_mail_attachment(self, source_id: str, attachment_id: str) -> dict[str, str]:
            return {"source_id": source_id, "attachment_id": attachment_id, "status": "opened"}

        def reveal_mail_attachment(self, source_id: str, attachment_id: str) -> dict[str, str]:
            return {"source_id": source_id, "attachment_id": attachment_id, "status": "revealed"}

    def setUp(self) -> None:
        self.bridge = DesktopBridge(self.Service())

    def test_actions_are_allowlisted_and_accept_only_strict_payloads(self) -> None:
        resource = self.bridge.invoke(
            "message_resource",
            {"kind": "email", "source_id": "mail:1", "resource_id": "cid:1"},
        )
        self.assertTrue(resource["ok"])
        self.assertTrue(
            self.bridge.invoke(
                "mail_attachment_open",
                {"kind": "email", "source_id": "mail:1", "attachment_id": "a"},
            )["ok"]
        )
        self.assertTrue(
            self.bridge.invoke(
                "mail_attachment_reveal",
                {"source_id": "mail:1", "attachment_id": "a"},
            )["ok"]
        )

        invalid = (
            ("message_resource", {"kind": "all", "source_id": "a", "resource_id": "b"}),
            ("message_resource", {"kind": "email", "source_id": "a", "resource_id": ""}),
            ("message_resource", {"kind": "email", "source_id": "a", "resource_id": "x" * 513}),
            ("mail_attachment_open", {"kind": "assignment", "source_id": "a", "attachment_id": "b"}),
            ("mail_attachment_open", {"source_id": "a", "attachment_id": "b", "extra": True}),
            ("mail_attachment_reveal", {"source_id": "a\n", "attachment_id": "b"}),
            ("mail_attachment_reveal", {"source_id": "a"}),
        )
        for action, payload in invalid:
            with self.subTest(action=action, payload=payload):
                response = self.bridge.invoke(action, payload)
                self.assertFalse(response["ok"])
                self.assertEqual("operation_failed", response["error"]["code"])
                self.assertNotIn("/Users/", str(response))


if __name__ == "__main__":
    unittest.main()

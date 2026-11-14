import unittest
import httpx
from unittest.mock import patch

from sjtu_learning_assistant.mail_images import fetch_mail_image, trusted_mail_image_url


class MailImageTests(unittest.TestCase):
    def test_streaming_limit_and_redirect_budget_are_enforced(self):
        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"123"
                yield b"456"
        with patch("sjtu_learning_assistant.mail_images.MAIL_IMAGE_LIMIT", 5):
            with self.assertRaisesRegex(ValueError, "大小"):
                fetch_mail_image("https://oc.sjtu.edu.cn/logo.png", transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"Content-Type":"image/png"}, stream=Stream())))
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location":"/loop"})
        with self.assertRaisesRegex(ValueError, "跳转次数"):
            fetch_mail_image("https://oc.sjtu.edu.cn/logo.png", transport=httpx.MockTransport(handler))
        self.assertEqual(4, len(requests))

    def test_tls_error_is_not_retried_without_verification(self):
        requests = []
        def handler(request):
            requests.append(request)
            raise httpx.ConnectError("synthetic TLS failure")
        with self.assertRaises(httpx.ConnectError):
            fetch_mail_image("https://oc.sjtu.edu.cn/logo.png", transport=httpx.MockTransport(handler))
        self.assertEqual(1, len(requests))
    def test_allowlist_rejects_local_arbitrary_and_credential_urls(self):
        for url in ("http://oc.sjtu.edu.cn/a", "https://127.0.0.1/a", "https://evil.test/a", "https://oc.sjtu.edu.cn.evil.test/a", "https://user:pass@oc.sjtu.edu.cn/a", "https://oc.sjtu.edu.cn:444/a", "https://oc.sjtu.edu.cn/api/v1/users/self", "https://oc.sjtu.edu.cn/%2e%2e/a", "https://oc.sjtu.edu.cn/a\n"):
            with self.subTest(url=url):
                self.assertIsNone(trusted_mail_image_url(url))

    def test_public_image_fetch_does_not_send_credentials_or_redirect_cookies(self):
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(302, headers={"Location": "https://s3.jcloud.sjtu.edu.cn/logo.png", "Set-Cookie": "tracking=synthetic"})
            return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"png-data")
        data = fetch_mail_image("https://oc.sjtu.edu.cn/logo.png", transport=httpx.MockTransport(handler))
        self.assertTrue(data.startswith("data:image/png;base64,"))
        for request in requests:
            for header in ("Cookie", "Authorization", "Referer"):
                self.assertNotIn(header, request.headers)

    def test_unsafe_redirect_wrong_type_size_and_http_failure_are_rejected(self):
        cases = (
            (302, {"Location": "https://127.0.0.1/a"}, b""),
            (200, {"Content-Type": "image/svg+xml"}, b"<svg/>"),
            (200, {"Content-Type": "image/png", "Content-Length": "999999999"}, b""),
            (200, {"Content-Type": "image/png", "Content-Length": "invalid"}, b""),
            (403, {}, b"private response"),
        )
        for status, headers, content in cases:
            with self.subTest(status=status, headers=headers), self.assertRaises((ValueError, httpx.HTTPError)):
                fetch_mail_image("https://oc.sjtu.edu.cn/logo.png", transport=httpx.MockTransport(lambda request: httpx.Response(status, headers=headers, content=content)))

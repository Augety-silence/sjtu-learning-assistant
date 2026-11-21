import unittest
import httpx
from unittest.mock import patch

from sjtu_learning_assistant.mail_images import fetch_mail_image, raster_image_type, trusted_mail_image_url, _resolve_public_host


class MailImageTests(unittest.TestCase):
    def setUp(self):
        resolver = patch("sjtu_learning_assistant.mail_images._resolve_public_host", return_value="8.8.8.8")
        resolver.start()
        self.addCleanup(resolver.stop)
    def test_raster_headers_do_not_guess_from_filename_or_active_content(self):
        for payload, expected in (
            (b"\x89PNG\r\n\x1a\n", "image/png"),
            (b"\xff\xd8\xff\xe0", "image/jpeg"),
            (b"GIF87a", "image/gif"),
            (b"GIF89a", "image/gif"),
            (b"RIFF\x20\x00\x00\x00WEBPVP8 ", "image/webp"),
            (b"<svg xmlns='http://www.w3.org/2000/svg'/>", None),
            (b"<html><script>bad()</script></html>", None),
            (b"not-an-image.png", None),
            (b"RIFF\x00\x00\x00\x00WAVEfmt ", None),
            (b"RIFF\x00\x00\x00\x00WEBPjunk", None),
            (b"", None),
        ):
            with self.subTest(payload=payload):
                self.assertEqual(expected, raster_image_type(payload))

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
    def test_rejects_local_reserved_and_credential_urls(self):
        for url in ("http://oc.sjtu.edu.cn/a", "https://127.0.0.1/a", "https://evil.test/a", "https://oc.sjtu.edu.cn.evil.test/a", "https://user:pass@oc.sjtu.edu.cn/a", "https://oc.sjtu.edu.cn:444/a", "https://oc.sjtu.edu.cn/api/v1/users/self", "https://oc.sjtu.edu.cn/%2e%2e/a", "https://oc.sjtu.edu.cn/a\n"):
            with self.subTest(url=url):
                self.assertIsNone(trusted_mail_image_url(url))

    def test_public_image_fetch_does_not_send_credentials_or_redirect_cookies(self):
        requests = []
        def handler(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(302, headers={"Location": "https://s3.jcloud.sjtu.edu.cn/logo.png", "Set-Cookie": "tracking=synthetic"})
            return httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"\x89PNG\r\n\x1a\n")
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

    def test_public_https_sources_and_protocol_relative_urls_are_supported(self):
        for host in ("img01.51jobcdn.com", "fecdn.51jobcdn.com", "img07.51jobcdn.com", "receiver.51job.com", "images.other-provider.com"):
            with self.subTest(host=host):
                self.assertEqual(f"https://{host}/a.png?q=1", trusted_mail_image_url(f"//{host}/a.png?q=1"))

    def test_dns_private_mixed_mapped_and_metadata_answers_fail_before_connect(self):
        for address in ("127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "::1", "fe80::1", "::ffff:127.0.0.1", "224.0.0.1"):
            answers = [(2, 1, 6, "", ("8.8.8.8", 443)), (2, 1, 6, "", (address, 443))]
            with self.subTest(address=address), patch("sjtu_learning_assistant.mail_images.socket.getaddrinfo", return_value=answers), self.assertRaisesRegex(ValueError, "公网"):
                _resolve_public_host("images.example.com")

    def test_dns_result_is_pinned_but_original_host_and_tls_sni_are_preserved(self):
        requests = []
        with patch("sjtu_learning_assistant.mail_images._resolve_public_host", return_value="8.8.4.4") as resolve:
            fetch_mail_image("https://img01.51jobcdn.com/a.png", transport=httpx.MockTransport(lambda request: (requests.append(request) or httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"\x89PNG\r\n\x1a\n"))))
        resolve.assert_called_once_with("img01.51jobcdn.com")
        self.assertEqual("8.8.4.4", requests[0].url.host)
        self.assertEqual("img01.51jobcdn.com", requests[0].headers["host"])
        self.assertEqual("img01.51jobcdn.com", requests[0].extensions["sni_hostname"])

    def test_redirect_revalidates_dns_and_rejects_rebinding_before_second_request(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(302, headers={"Location": "/next.png", "Set-Cookie": "tracking=synthetic"})
        with patch("sjtu_learning_assistant.mail_images._resolve_public_host", side_effect=("8.8.8.8", ValueError("非公网"))), self.assertRaisesRegex(ValueError, "公网"):
            fetch_mail_image("https://images.example.com/a.png", transport=httpx.MockTransport(handler))
        self.assertEqual(1, len(requests))

    def test_generic_mime_raster_is_recovered_but_fake_or_mismatched_content_is_rejected(self):
        data = fetch_mail_image("https://images.example.com/a", transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"Content-Type": "application/octet-stream"}, content=b"GIF89a")))
        self.assertTrue(data.startswith("data:image/gif;base64,"))
        for payload in (b"<html><script>bad()</script></html>", b"<svg/>", b"GIF89a", b""):
            with self.subTest(payload=payload), self.assertRaisesRegex(ValueError, "类型"):
                fetch_mail_image("https://images.example.com/a", transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=payload)))

    def test_trickling_stream_has_total_time_budget(self):
        with patch("sjtu_learning_assistant.mail_images.time.monotonic", side_effect=(0, 31)), self.assertRaisesRegex(ValueError, "超时"):
            fetch_mail_image("https://images.example.com/a", transport=httpx.MockTransport(lambda request: httpx.Response(200, headers={"Content-Type": "image/png"}, content=b"\x89PNG\r\n\x1a\n")))

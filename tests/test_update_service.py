from __future__ import annotations

import hashlib
import unittest

import httpx

from sjtu_learning_assistant.update_service import (
    ChecksumMismatchError,
    InvalidVersionError,
    UpdateService,
    UnsafeUpdateURLError,
    compare_versions,
    parse_release_metadata,
    parse_sha256_metadata,
    require_sha256,
    validate_update_url,
    verify_sha256,
)


ASSET_NAME = "SJTU-Learning-Assistant-1.3.0-Windows-x64-Setup.exe"
ASSET_URL = (
    "https://github.com/Augety-silence/sjtu-learning-assistant/"
    f"releases/download/v1.3.0/{ASSET_NAME}"
)
CHECKSUM_URL = ASSET_URL + ".sha256"


def release_payload(*, version="v1.3.0", digest=None):
    asset = {
        "name": ASSET_NAME,
        "browser_download_url": ASSET_URL,
        "size": 1234,
        "content_type": "application/octet-stream",
    }
    if digest is not None:
        asset["digest"] = "sha256:" + digest
    return {
        "tag_name": version,
        "html_url": (
            "https://github.com/Augety-silence/sjtu-learning-assistant/"
            f"releases/tag/{version}"
        ),
        "published_at": "2026-09-29T00:00:00Z",
        "body": "安全更新",
        "draft": False,
        "prerelease": "-" in version,
        "assets": [
            asset,
            {
                "name": ASSET_NAME + ".sha256",
                "browser_download_url": CHECKSUM_URL,
                "size": 100,
                "content_type": "text/plain",
            },
        ],
    }


class UpdateServiceTests(unittest.TestCase):
    def test_strict_semver_comparison(self) -> None:
        self.assertEqual(1, compare_versions("v1.10.0", "1.9.9"))
        self.assertEqual(-1, compare_versions("1.0.0-alpha.2", "1.0.0-alpha.10"))
        self.assertEqual(0, compare_versions("1.0.0+build.1", "1.0.0+build.2"))
        with self.assertRaises(InvalidVersionError):
            compare_versions("1.0", "1.0.0")
        with self.assertRaises(InvalidVersionError):
            compare_versions("1.0.0-01", "1.0.0")

    def test_https_allowlist_rejects_credentials_ports_and_other_repositories(self) -> None:
        self.assertEqual(ASSET_URL, validate_update_url(ASSET_URL))
        for unsafe in (
            "http://github.com/Augety-silence/sjtu-learning-assistant/releases/download/v1/x",
            "https://evil.example/update.exe",
            "https://user:password@github.com/Augety-silence/sjtu-learning-assistant/releases/download/v1/x",
            "https://github.com:444/Augety-silence/sjtu-learning-assistant/releases/download/v1/x",
            "https://github.com/other/repo/releases/download/v1/x",
        ):
            with self.subTest(url=unsafe), self.assertRaises(UnsafeUpdateURLError):
                validate_update_url(unsafe)

    def test_parse_release_assets_and_sha256_metadata(self) -> None:
        digest = hashlib.sha256(b"payload").hexdigest()
        release = parse_release_metadata(release_payload(digest=digest))
        self.assertEqual("1.3.0", release.version)
        self.assertEqual(digest, release.assets[0].sha256)
        self.assertEqual(
            digest,
            parse_sha256_metadata(f"{digest}  *{ASSET_NAME}\n", ASSET_NAME),
        )

    def test_check_uses_fake_transport_strict_timeout_and_returns_plan_only(self) -> None:
        payload = b"installer bytes"
        digest = hashlib.sha256(payload).hexdigest()
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            self.assertTrue(all(value <= 5.0 for value in request.extensions["timeout"].values()))
            if request.url.host == "api.github.com":
                return httpx.Response(200, json=release_payload())
            if str(request.url) == CHECKSUM_URL:
                return httpx.Response(200, text=f"{digest}  {ASSET_NAME}\n")
            raise AssertionError(f"发生了未计划的网络请求：{request.url}")

        service = UpdateService(
            "1.2.1",
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(5.0),
        )
        result = service.check(system="Windows", machine="x86_64")
        self.assertTrue(result.update_available)
        self.assertEqual(2, len(seen))
        self.assertEqual(ASSET_URL, result.download_plan.download_url)
        self.assertEqual(digest, result.download_plan.sha256)
        self.assertTrue(result.download_plan.requires_sha256_verification)
        # 服务只生成计划：不会请求二进制资产，也不会覆盖正在运行的应用。
        self.assertNotIn(ASSET_URL, [str(request.url) for request in seen])

    def test_no_update_does_not_resolve_or_download_asset(self) -> None:
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json=release_payload(version="v1.2.1"))

        result = UpdateService(
            "1.2.1", transport=httpx.MockTransport(handler)
        ).check(system="Windows", machine="x64")
        self.assertFalse(result.update_available)
        self.assertIsNone(result.download_plan)
        self.assertEqual(1, len(calls))

    def test_explicit_sha256_verification(self) -> None:
        content = b"verified package"
        digest = hashlib.sha256(content).hexdigest()
        self.assertTrue(verify_sha256(content, digest.upper()))
        self.assertEqual(digest, require_sha256(content, digest))
        with self.assertRaises(ChecksumMismatchError):
            require_sha256(content + b"tampered", digest)


if __name__ == "__main__":
    unittest.main()

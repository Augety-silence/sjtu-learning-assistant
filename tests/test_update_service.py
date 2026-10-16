from __future__ import annotations

import hashlib
import unittest

import httpx

from sjtu_learning_assistant.update_service import (
    ChecksumMismatchError,
    InvalidUpdateMetadataError,
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


def multi_platform_release_payload(artifact_names):
    assets = []
    checksum_by_name = {}
    for name in artifact_names:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
        checksum_by_name[name] = digest
        url = (
            "https://github.com/Augety-silence/sjtu-learning-assistant/releases/download/"
            f"v1.7.3/{name}"
        )
        assets.extend(
            (
                {
                    "name": name,
                    "browser_download_url": url,
                    "size": 1234,
                    "content_type": "application/octet-stream",
                },
                {
                    "name": name + ".sha256",
                    "browser_download_url": url + ".sha256",
                    "size": 100,
                    "content_type": "text/plain",
                },
            )
        )
    return {
        "tag_name": "v1.7.3",
        "html_url": (
            "https://github.com/Augety-silence/sjtu-learning-assistant/"
            "releases/tag/v1.7.3"
        ),
        "published_at": "2026-10-16T00:00:00Z",
        "body": "synthetic release fixture",
        "draft": False,
        "prerelease": False,
        "assets": assets,
    }, checksum_by_name


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

    def test_windows_release_prefers_unique_setup_and_resolves_its_sidecar(self):
        names = [
            "SJTU-Learning-Assistant-1.7.3-macOS-arm64.dmg",
            "SJTU-Learning-Assistant-1.7.3-Windows-x64-portable.zip",
            "SJTU-Learning-Assistant-1.7.3-Windows-x64-Setup.exe",
        ]
        payload, checksums = multi_platform_release_payload(names)
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.host == "api.github.com":
                return httpx.Response(200, json=payload)
            for name, digest in checksums.items():
                if str(request.url).endswith("/" + name + ".sha256"):
                    return httpx.Response(200, text=f"{digest}  {name}\n")
            raise AssertionError(f"unexpected synthetic request: {request.url}")

        result = UpdateService("1.7.2", transport=httpx.MockTransport(handler)).check(
            system="Windows", machine="x64"
        )

        self.assertTrue(result.update_available)
        self.assertEqual(names[2], result.download_plan.asset_name)
        self.assertEqual(checksums[names[2]], result.download_plan.sha256)
        self.assertTrue(result.download_plan.requires_sha256_verification)
        self.assertEqual(2, len(seen))
        explicitly_portable = UpdateService(
            "1.7.2", transport=httpx.MockTransport(handler)
        ).check(system="Windows", machine="x64", asset_name=names[1])
        self.assertEqual(names[1], explicitly_portable.download_plan.asset_name)
        self.assertEqual(checksums[names[1]], explicitly_portable.download_plan.sha256)
        mac_release = UpdateService("1.7.2", transport=httpx.MockTransport(handler)).check(
            system="Darwin", machine="arm64", fetch_checksum=False
        )
        self.assertEqual(names[0], mac_release.download_plan.asset_name)

    def test_windows_release_selects_portable_when_it_is_the_only_windows_asset(self):
        portable = "SJTU-Learning-Assistant-1.7.3-Windows-x64-portable.zip"
        payload, checksums = multi_platform_release_payload([portable])

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.github.com":
                return httpx.Response(200, json=payload)
            return httpx.Response(200, text=f"{checksums[portable]}  {portable}\n")

        result = UpdateService("1.7.2", transport=httpx.MockTransport(handler)).check(
            system="Windows", machine="x64"
        )
        self.assertEqual(portable, result.download_plan.asset_name)
        explicit = UpdateService("1.7.2", transport=httpx.MockTransport(handler)).check(
            system="Windows", machine="x64", asset_name=portable
        )
        self.assertEqual(portable, explicit.download_plan.asset_name)
        self.assertEqual(checksums[portable], explicit.download_plan.sha256)

    def test_windows_release_still_rejects_duplicate_setup_or_portable_assets(self):
        cases = {
            "duplicate setup": [
                "SJTU-Learning-Assistant-1.7.3-Windows-x64-Setup.exe",
                "SJTU-Learning-Assistant-1.7.3-Windows-x64-Full-Setup.exe",
            ],
            "duplicate portable": [
                "SJTU-Learning-Assistant-1.7.3-Windows-x64-portable.zip",
                "SJTU-Learning-Assistant-1.7.3-Windows-x64-Backup-portable.zip",
            ],
            "portable plus unknown windows asset": [
                "SJTU-Learning-Assistant-1.7.3-Windows-x64-portable.zip",
                "SJTU-Learning-Assistant-1.7.3-Windows-x64-debug.tar",
            ],
            "unique setup plus duplicate portable": [
                "SJTU-Learning-Assistant-1.7.3-Windows-x64-Setup.exe",
                "SJTU-Learning-Assistant-1.7.3-Windows-x64-portable.zip",
                "SJTU-Learning-Assistant-1.7.3-Windows-x64-Backup-portable.zip",
            ],
        }
        for label, names in cases.items():
            with self.subTest(label=label):
                payload, _checksums = multi_platform_release_payload(names)
                transport = httpx.MockTransport(
                    lambda request, payload=payload: httpx.Response(200, json=payload)
                )
                with self.assertRaisesRegex(
                    InvalidUpdateMetadataError, "无法唯一确定"
                ):
                    UpdateService("1.7.2", transport=transport).check(
                        system="Windows", machine="x64", fetch_checksum=False
                    )

    def test_newer_release_without_matching_platform_asset_uses_release_page(self):
        windows_asset = "SJTU-Learning-Assistant-1.7.3-Windows-x64-Setup.exe"
        payload, _checksums = multi_platform_release_payload([windows_asset])
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)
        )

        result = UpdateService("1.7.2", transport=transport).check(
            system="Darwin", machine="arm64", fetch_checksum=False
        )

        self.assertTrue(result.update_available)
        self.assertIsNone(result.download_plan)
        self.assertEqual(payload["html_url"], result.release.page_url)

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

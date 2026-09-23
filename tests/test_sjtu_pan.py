from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from sjtu_learning_assistant.cloud_storage import (
    CloudDirectoryPage,
    CloudRemoteApiError,
    CloudTimeoutError,
    SJTUCloudPanProvider,
    UploadSession,
)
from sjtu_learning_assistant.cloud_storage.sjtu_pan import (
    KEYCHAIN_ACCOUNT,
    KEYCHAIN_SERVICE,
    delete_user_token,
    load_user_token,
    save_user_token,
)


TOKEN = "user-token-for-tests"


def credential(access_token: str = "access-one") -> dict[str, object]:
    return {
        "status": 0,
        "accessToken": access_token,
        "libraryId": "library",
        "spaceId": "space",
        "expiresIn": 3600,
    }


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


class MissingPasswordError(Exception):
    pass


class MissingPasswordKeyring:
    class errors:
        PasswordDeleteError = MissingPasswordError

    @staticmethod
    def delete_password(_service: str, _account: str) -> None:
        raise MissingPasswordError


class SJTUCloudPanTests(unittest.TestCase):
    def provider(self, handler) -> tuple[SJTUCloudPanProvider, httpx.Client]:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return SJTUCloudPanProvider(
            TOKEN,
            client=client,
            trusted_object_hosts=("cos.example.test",),
        ), client

    def test_keychain_and_validate_token(self) -> None:
        keyring = FakeKeyring()
        save_user_token("  token-value  ", keyring_module=keyring)
        self.assertEqual("token-value", load_user_token(keyring_module=keyring))
        self.assertNotIn(TOKEN, repr(SJTUCloudPanProvider(TOKEN, client=httpx.Client())))
        self.assertEqual("token-value", keyring.values[(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)])

        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json=credential(), request=request)

        provider, client = self.provider(handler)
        try:
            self.assertTrue(provider.validate_token())
            self.assertEqual("library", provider.get_space_credentials().library_id)
        finally:
            client.close()
        self.assertEqual("POST", requests[0].method)
        self.assertEqual(TOKEN, requests[0].url.params["user_token"])

    def test_delete_user_token_is_idempotent_when_keyring_entry_is_missing(self) -> None:
        delete_user_token(keyring_module=MissingPasswordKeyring())

    def test_401_refreshes_once(self) -> None:
        calls = {"credentials": 0, "directory": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/user/v1/space/1/personal":
                calls["credentials"] += 1
                return httpx.Response(200, json=credential(f"access-{calls['credentials']}"), request=request)
            calls["directory"] += 1
            if calls["directory"] == 1:
                return httpx.Response(401, json={"message": TOKEN}, request=request)
            return httpx.Response(200, json={"contents": [], "totalNum": 0, "path": []}, request=request)

        provider, client = self.provider(handler)
        try:
            page = provider.list_directory()
        finally:
            client.close()
        self.assertEqual(0, page.total)
        self.assertEqual({"credentials": 2, "directory": 2}, calls)

    def test_timeout_and_error_response_are_sanitized(self) -> None:
        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("contains-secret", request=request)

        provider, client = self.provider(timeout_handler)
        try:
            with self.assertRaises(CloudTimeoutError) as caught:
                provider.list_directory()
        finally:
            client.close()
        self.assertNotIn("contains-secret", str(caught.exception))

        def error_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            return httpx.Response(503, text=f"upstream leaked {TOKEN}", request=request)

        provider, client = self.provider(error_handler)
        try:
            with self.assertRaises(CloudRemoteApiError) as caught:
                provider.list_directory()
        finally:
            client.close()
        self.assertEqual(503, caught.exception.status_code)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_directory_pagination_and_segment_encoding(self) -> None:
        requested_pages: list[str] = []
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            requested_pages.append(request.url.params["page"])
            paths.append(request.url.raw_path.decode().split("?", 1)[0])
            page = int(request.url.params["page"])
            contents = ([{"name": "A", "path": ["课程 1", "A"], "type": "file", "size": "3"}] if page == 1 else [{"name": "B", "path": ["课程 1", "B"], "type": "directory"}])
            return httpx.Response(200, json={"contents": contents, "totalNum": 2, "path": ["课程 1"]}, request=request)

        provider, client = self.provider(handler)
        try:
            items = list(provider.iter_directory(("课程 1",), page_size=1))
        finally:
            client.close()
        self.assertEqual(["A", "B"], [item.name for item in items])
        self.assertEqual(["1", "2"], requested_pages)
        self.assertTrue(all("%E8%AF%BE%E7%A8%8B%201" in path for path in paths))

    def test_create_ensure_exists_move_copy_delete(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            if request.method == "PUT" and "/directory/" in request.url.path:
                if request.url.path.endswith("/a"):
                    return httpx.Response(409, json={"message": "SameNameDirectoryOrFileExists"}, request=request)
                return httpx.Response(200, json={"status": 0}, request=request)
            if request.method == "GET":
                if request.url.path.endswith("/missing"):
                    return httpx.Response(404, request=request)
                return httpx.Response(200, json={"name": request.url.path.rsplit("/", 1)[-1], "type": "file"}, request=request)
            if request.method == "DELETE":
                return httpx.Response(204, request=request)
            body = json.loads(request.content)
            return httpx.Response(200, json={"name": request.url.path.rsplit("/", 1)[-1], "path": ["dest"], "type": "file", "body": body}, request=request)

        provider, client = self.provider(handler)
        try:
            provider.ensure_directory(("a", "b"))
            self.assertTrue(provider.exists("found"))
            self.assertFalse(provider.exists("missing"))
            provider.move("old", "new")
            provider.copy("old", "copy")
            provider.delete("gone")
        finally:
            client.close()
        move_request = next(req for req in requests if req.url.path.endswith("/new"))
        copy_request = next(req for req in requests if req.url.path.endswith("/copy"))
        self.assertEqual({"from": "old"}, json.loads(move_request.content))
        self.assertEqual({"copyFrom": "old"}, json.loads(copy_request.content))

    def test_download_temp_is_atomic_and_cleans_up(self) -> None:
        fail_download = False

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            if fail_download:
                return httpx.Response(500, request=request)
            return httpx.Response(200, content=b"downloaded", request=request)

        provider, client = self.provider(handler)
        try:
            with tempfile.TemporaryDirectory() as directory:
                with provider.download_temp("file.bin", directory=Path(directory)) as path:
                    self.assertEqual(b"downloaded", path.read_bytes())
                    self.assertFalse(path.name.endswith(".part"))
                self.assertFalse(path.exists())
                fail_download = True
                with self.assertRaises(CloudRemoteApiError):
                    provider.download_temp("bad.bin", directory=Path(directory))
                self.assertEqual([], list(Path(directory).iterdir()))
        finally:
            client.close()

    def test_simple_upload_uses_dynamic_url_and_headers(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            if request.url.host == "cos.example.test":
                self.assertEqual("dynamic-signature", request.headers["authorization"])
                self.assertEqual(b"payload", request.content)
                return httpx.Response(200, request=request)
            if request.method == "PUT":
                return httpx.Response(200, json={"confirmKey": "simple-confirm", "domain": "cos.example.test", "path": "/object", "headers": {"authorization": "dynamic-signature"}}, request=request)
            return httpx.Response(200, json={"name": "file.txt", "path": ["dir", "file.txt"], "type": "file", "size": "7"}, request=request)

        provider, client = self.provider(handler)
        try:
            result = provider.simple_upload(("dir", "file.txt"), b"payload")
        finally:
            client.close()
        self.assertEqual("file.txt", result.name)
        self.assertEqual("cos.example.test", requests[2].url.host)
        confirms = [request for request in requests if "confirm" in request.url.params]
        self.assertEqual(1, len(confirms))
        self.assertEqual("ask", confirms[0].url.params["conflict_resolution_strategy"])

    def test_multipart_renew_resume_confirm_and_safe_serialization(self) -> None:
        requests: list[httpx.Request] = []

        def upload_payload(numbers: list[int]) -> dict[str, object]:
            return {
                "status": 0,
                "confirmKey": "confirm-key",
                "domain": "cos.example.test",
                "path": "/multipart-object",
                "uploadId": "upload-id",
                "expiration": "2099-01-01T00:00:00Z",
                "parts": {str(number): {"headers": {"authorization": f"signature-{number}"}} for number in numbers},
            }

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            if request.url.host == "cos.example.test":
                return httpx.Response(200, request=request)
            if request.method == "POST" and "confirm" in request.url.params:
                return httpx.Response(200, json={"name": "large.bin", "path": ["large.bin"], "type": "file", "size": "6"}, request=request)
            body = json.loads(request.content)
            return httpx.Response(200, json=upload_payload(body["partNumberRange"]), request=request)

        provider, client = self.provider(handler)
        try:
            session = provider.init_multipart("large.bin", part_numbers=[1])
            provider.upload_part(session, 1, b"abc")
            # Part 2 was not in init and therefore forces renew.
            result = provider.resume_upload(session, io.BytesIO(b"abcdef"), chunk_size=3)
        finally:
            client.close()

        snapshot = session.to_dict()
        self.assertNotIn("accessToken", snapshot)
        self.assertNotIn("headers", snapshot)
        self.assertEqual([1, 2], session.uploaded_parts)
        self.assertEqual(6, session.bytes_uploaded)
        self.assertEqual("large.bin", result.name)
        renewed = [request for request in requests if "renew" in request.url.params]
        confirmed = [request for request in requests if "confirm" in request.url.params]
        self.assertEqual(1, len(renewed))
        self.assertEqual(1, len(confirmed))
        self.assertEqual(snapshot, UploadSession.from_dict(snapshot).to_dict())

    def test_sjtu_jcloud_object_host_is_trusted(self) -> None:
        provider, client = self.provider(lambda request: httpx.Response(500, request=request))
        try:
            self.assertEqual(
                "https://s3pan3.jcloud.sjtu.edu.cn/object",
                provider._validate_object_url("https://s3pan3.jcloud.sjtu.edu.cn/object"),
            )
            with self.assertRaisesRegex(CloudRemoteApiError, "不可信"):
                provider._validate_object_url("https://jcloud.sjtu.edu.cn.evil.test/object")
        finally:
            client.close()

    def test_download_redirect_range_and_target_validation(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            if request.url.host == "pan.sjtu.edu.cn":
                return httpx.Response(
                    302,
                    headers={"Location": "https://cos.example.test/object?signature=dynamic"},
                    request=request,
                )
            self.assertEqual("bytes=2-4", request.headers["Range"])
            self.assertNotIn("access_token", request.url.params)
            return httpx.Response(
                206,
                headers={"Content-Range": "bytes 2-4/10"},
                content=b"cde",
                request=request,
            )

        provider, client = self.provider(handler)
        try:
            self.assertEqual(b"cde", b"".join(provider.download_stream("file", start=2, end=4)))
        finally:
            client.close()
        self.assertEqual("bytes=2-4", requests[-1].headers["Range"])

        def evil_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            return httpx.Response(301, headers={"Location": "http://127.0.0.1/secret"}, request=request)

        provider, client = self.provider(evil_handler)
        try:
            with self.assertRaisesRegex(CloudRemoteApiError, "不可信"):
                b"".join(provider.download_stream("file"))
        finally:
            client.close()

    def test_range_download_requires_matching_206_content_range(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            return httpx.Response(200, content=b"whole file", request=request)

        provider, client = self.provider(handler)
        try:
            with self.assertRaisesRegex(CloudRemoteApiError, "206"):
                b"".join(provider.download_stream("file", start=3, end=5))
        finally:
            client.close()

    def test_pagination_uses_accumulated_items_when_server_caps_page_size(self) -> None:
        pages = {1: ["a", "b"], 2: ["c", "d"], 3: ["e"]}
        requested: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            page = int(request.url.params["page"])
            requested.append(page)
            contents = [{"name": name, "path": [name], "type": "file"} for name in pages[page]]
            return httpx.Response(200, json={"contents": contents, "totalNum": 5}, request=request)

        provider, client = self.provider(handler)
        try:
            names = [item.name for item in provider.iter_directory(page_size=100)]
        finally:
            client.close()
        self.assertEqual(["a", "b", "c", "d", "e"], names)
        self.assertEqual([1, 2, 3], requested)

    def test_simple_upload_requires_confirm_key(self) -> None:
        cos_calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal cos_calls
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            if request.url.host == "cos.example.test":
                cos_calls += 1
                return httpx.Response(200, request=request)
            return httpx.Response(200, json={"domain": "cos.example.test", "path": "/object", "headers": {"authorization": "signed"}}, request=request)

        provider, client = self.provider(handler)
        try:
            with self.assertRaises(CloudRemoteApiError):
                provider.simple_upload("file", b"payload")
        finally:
            client.close()
        self.assertEqual(0, cos_calls)

    def test_restored_session_renews_authority_and_inherits_overwrite(self) -> None:
        requests: list[httpx.Request] = []
        snapshot = {
            "remote_path": ["large.bin"],
            "confirm_key": "confirm-key",
            "overwrite": True,
            "next_part": 2,
            "uploaded_parts": [1],
            "part_sizes": {"1": 3},
            "bytes_uploaded": 3,
            "domain": "evil.invalid",
            "object_path": "//evil/path",
            "upload_id": "attacker-upload",
        }
        session = UploadSession.from_dict(snapshot)
        self.assertEqual("", session.domain)
        self.assertEqual("", session.upload_id)

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            if request.url.host == "cos.example.test":
                self.assertEqual("server-upload", request.url.params["uploadId"])
                return httpx.Response(200, request=request)
            if "confirm" in request.url.params:
                return httpx.Response(200, json={"name": "large.bin", "path": ["large.bin"], "type": "file"}, request=request)
            return httpx.Response(200, json={
                "confirmKey": "confirm-key",
                "domain": "cos.example.test",
                "path": "/server-object",
                "uploadId": "server-upload",
                "expiration": "2099-01-01T00:00:00Z",
                "parts": {"2": {"headers": {"authorization": "server-signature"}}},
            }, request=request)

        provider, client = self.provider(handler)
        try:
            result = provider.resume_upload(session, io.BytesIO(b"abcdef"), chunk_size=3)
        finally:
            client.close()
        self.assertEqual("large.bin", result.name)
        self.assertFalse(any(request.url.host == "evil.invalid" for request in requests))
        confirm = next(request for request in requests if "confirm" in request.url.params)
        self.assertEqual("overwrite", confirm.url.params["conflict_resolution_strategy"])
        persisted = session.to_dict()
        self.assertNotIn("domain", persisted)
        self.assertNotIn("object_path", persisted)
        self.assertNotIn("upload_id", persisted)

    def test_out_of_order_parts_and_reflected_error_code_are_rejected(self) -> None:
        def upload_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            return httpx.Response(200, json={
                "confirmKey": "confirm-key", "domain": "cos.example.test",
                "path": "/object", "uploadId": "upload-id",
                "parts": {"1": {"headers": {"authorization": "signed"}}},
            }, request=request)

        provider, client = self.provider(upload_handler)
        try:
            session = provider.init_multipart("file", part_numbers=[1])
            with self.assertRaisesRegex(ValueError, "按顺序"):
                provider.upload_part(session, 2, b"wrong")
        finally:
            client.close()

        def error_handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("personal"):
                return httpx.Response(200, json=credential(), request=request)
            return httpx.Response(500, json={"code": f"bad/{TOKEN}"}, request=request)

        provider, client = self.provider(error_handler)
        try:
            with self.assertRaises(CloudRemoteApiError) as caught:
                provider.list_directory()
        finally:
            client.close()
        self.assertIsNone(caught.exception.code)
        self.assertNotIn(TOKEN, str(caught.exception))


if __name__ == "__main__":
    unittest.main()

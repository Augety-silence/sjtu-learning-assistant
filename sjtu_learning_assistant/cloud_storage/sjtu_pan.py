"""Shanghai Jiao Tong University Pan cloud storage provider.

Only server-returned credentials and signed upload headers are used. Secrets are never
included in exception messages or persisted multipart session snapshots.
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import threading
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpx

from .base import CloudStorageProvider, RemotePath, TemporaryDownload
from .errors import (
    CloudAuthError,
    CloudConflictError,
    CloudCredentialExpiredError,
    CloudNetworkError,
    CloudNotFoundError,
    CloudRemoteApiError,
    CloudTimeoutError,
)
from .models import CloudDirectoryPage, CloudItem, SpaceCredential, SpaceInfo, UploadSession

PAN_BASE_URL = "https://pan.sjtu.edu.cn"
KEYCHAIN_SERVICE = "SJTU Learning Assistant - SJTU Pan"
KEYCHAIN_ACCOUNT = "user-token"
MAX_TOKEN_LENGTH = 4096
MAX_PARTS_PER_RENEWAL = 50
TRUSTED_OBJECT_HOST_SUFFIXES = (
    ".myqcloud.com",
    ".tencentcos.cn",
    ".jcloud.sjtu.edu.cn",
)
SAFE_ERROR_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(?:\d+|\*)$")


def _load_keyring() -> Any:
    try:
        import keyring  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CloudAuthError("缺少 keyring 依赖，无法读取交大云盘凭据。") from exc
    return keyring


def validate_user_token_value(value: object) -> str:
    if type(value) is not str:
        raise CloudAuthError("交大云盘 UserToken 格式不正确。")
    token = value.strip()
    if not token or len(token) > MAX_TOKEN_LENGTH or any(ord(c) < 33 or ord(c) == 127 for c in token):
        raise CloudAuthError("交大云盘 UserToken 格式不正确。")
    return token


def load_user_token(*, keyring_module: Any | None = None) -> str | None:
    backend = keyring_module or _load_keyring()
    try:
        value = backend.get_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except Exception:
        raise CloudAuthError("无法从系统 Keychain 读取交大云盘 UserToken。") from None
    return None if value is None else validate_user_token_value(value)


def save_user_token(value: object, *, keyring_module: Any | None = None) -> None:
    token = validate_user_token_value(value)
    try:
        (keyring_module or _load_keyring()).set_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT, token)
    except Exception:
        raise CloudAuthError("无法将交大云盘 UserToken 保存到系统 Keychain。") from None


def delete_user_token(*, keyring_module: Any | None = None) -> None:
    backend = keyring_module or _load_keyring()
    try:
        backend.delete_password(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)
    except Exception as exc:
        missing_error = getattr(
            getattr(backend, "errors", None), "PasswordDeleteError", None
        )
        if isinstance(missing_error, type) and isinstance(exc, missing_error):
            return
        raise CloudAuthError("无法从系统 Keychain 删除交大云盘 UserToken。") from None


def _segments(path: RemotePath) -> tuple[str, ...]:
    raw = path.split("/") if isinstance(path, str) else list(path)
    result = tuple(part for part in (str(item).strip() for item in raw) if part)
    if any(part in {".", ".."} or "/" in part or "\\" in part or "\x00" in part for part in result):
        raise ValueError("云盘路径包含无效分段。")
    return result


def _encoded_path(path: RemotePath) -> str:
    return "/".join(quote(part, safe="") for part in _segments(path))


def _integer(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_message(payload: Any, fallback: str) -> tuple[str, str | None]:
    if not isinstance(payload, Mapping):
        return fallback, None
    code = payload.get("code")
    message = payload.get("message") or payload.get("msg")
    # Server text can reflect request/query values. Keep only a conservative status label.
    raw_code = str(code) if code is not None else ""
    safe_code = raw_code if SAFE_ERROR_CODE.fullmatch(raw_code) else None
    if isinstance(message, str) and message in {
        "SameNameDirectoryOrFileExists", "MultipartUploadIncomplete", "NotFound",
        "Unauthorized", "Forbidden",
    }:
        return message, safe_code
    return fallback, safe_code


def _item(payload: Mapping[str, Any], fallback_path: tuple[str, ...] = ()) -> CloudItem:
    raw_path = payload.get("path")
    path = tuple(str(value) for value in raw_path) if isinstance(raw_path, list) else fallback_path
    name = str(payload.get("name") or (path[-1] if path else ""))
    item_type = str(payload.get("type") or payload.get("fileType") or "").lower()
    is_directory = item_type in {"directory", "folder", "dir"}
    size = _integer(payload.get("size"))
    known = {"name", "path", "type", "fileType", "size", "contentType", "eTag", "creationTime", "modificationTime"}
    return CloudItem(
        name=name,
        path=path or fallback_path,
        is_directory=is_directory,
        size=size,
        content_type=str(payload["contentType"]) if payload.get("contentType") else None,
        etag=str(payload["eTag"]) if payload.get("eTag") else None,
        created_at=str(payload["creationTime"]) if payload.get("creationTime") else None,
        modified_at=str(payload["modificationTime"]) if payload.get("modificationTime") else None,
        metadata={key: value for key, value in payload.items() if key not in known},
    )


class SJTUCloudPanProvider(CloudStorageProvider):
    def __init__(
        self,
        user_token: str | None = None,
        *,
        client: httpx.Client | None = None,
        keyring_module: Any | None = None,
        base_url: str = PAN_BASE_URL,
        timeout: float = 30.0,
        trusted_object_hosts: Sequence[str] = (),
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("交大云盘地址必须是 HTTPS。")
        self._base_url = base_url.rstrip("/")
        self._pan_host = parsed.hostname.lower() if parsed.hostname else ""
        self._trusted_object_hosts = frozenset(str(host).lower().rstrip(".") for host in trusted_object_hosts)
        if any(not host or ":" in host or "/" in host for host in self._trusted_object_hosts):
            raise ValueError("可信对象存储域名格式无效。")
        self._keyring = keyring_module
        clean_credential = validate_user_token_value
        self._user_token = clean_credential(user_token) if user_token is not None else None
        self._credential: SpaceCredential | None = None
        self._credential_lock = threading.RLock()
        self._part_credentials: dict[str, dict[int, Mapping[str, str]]] = {}
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "SJTUCloudPanProvider":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def set_user_token(self, value: object, *, persist: bool = True) -> None:
        token = validate_user_token_value(value)
        if persist:
            save_user_token(token, keyring_module=self._keyring)
        with self._credential_lock:
            self._user_token = token
            self._credential = None
            self._part_credentials.clear()

    def clear_user_token(self, *, delete_from_keychain: bool = True) -> None:
        if delete_from_keychain:
            delete_user_token(keyring_module=self._keyring)
        with self._credential_lock:
            self._user_token = None
            self._credential = None
            self._part_credentials.clear()

    def _token(self) -> str:
        if self._user_token is None:
            self._user_token = load_user_token(keyring_module=self._keyring)
        if self._user_token is None:
            raise CloudAuthError("尚未设置交大云盘 UserToken。")
        return self._user_token

    def _send_raw(self, method: str, url: str | httpx.URL, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, url, **kwargs)
        except httpx.TimeoutException:
            raise CloudTimeoutError("交大云盘请求超时。") from None
        except httpx.RequestError:
            raise CloudNetworkError("无法连接交大云盘服务。") from None

    def _decode(self, response: httpx.Response, operation: str) -> Any:
        if response.status_code in (401, 403):
            raise CloudAuthError(f"交大云盘认证失败（HTTP {response.status_code}）。")
        if response.status_code == 404:
            raise CloudNotFoundError(f"{operation}失败：项目不存在。", status_code=404)
        payload: Any = None
        if response.content:
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError):
                payload = None
        safe_message, safe_code = _safe_message(payload, f"{operation}失败。")
        known_secrets = tuple(
            secret for secret in (
                self._user_token,
                self._credential.access_token if self._credential else None,
            ) if secret
        )
        if safe_code and any(secret in safe_code or safe_code in secret for secret in known_secrets):
            safe_code = None
        if response.status_code in (400, 409) and (
            safe_message == "SameNameDirectoryOrFileExists"
            or safe_code == "SameNameDirectoryOrFileExists"
        ):
            raise CloudConflictError(safe_message, status_code=response.status_code, code=safe_code)
        if response.status_code == 409:
            raise CloudConflictError(f"{operation}失败：目标已存在。", status_code=409, code=safe_code)
        if response.status_code >= 400:
            message = (
                safe_message
                if safe_message != f"{operation}失败。"
                else f"{operation}失败（HTTP {response.status_code}）。"
            )
            raise CloudRemoteApiError(message, status_code=response.status_code, code=safe_code)
        if isinstance(payload, Mapping) and _integer(payload.get("status"), 0) != 0:
            message = (
                safe_message
                if safe_message != f"{operation}失败。"
                else f"{operation}被远端拒绝。"
            )
            raise CloudRemoteApiError(message, status_code=response.status_code, code=safe_code)
        return payload

    def validate_token(self, user_token: str | None = None) -> bool:
        if user_token is not None:
            token = validate_user_token_value(user_token)
        else:
            token = self._token()
        credential = self._fetch_credentials(token)
        if user_token is None or token == self._user_token:
            with self._credential_lock:
                self._credential = credential
        return True

    def _fetch_credentials(self, token: str) -> SpaceCredential:
        response = self._send_raw("POST", f"{self._base_url}/user/v1/space/1/personal", params={"user_token": token})
        payload = self._decode(response, "获取空间凭据")
        if not isinstance(payload, Mapping):
            raise CloudRemoteApiError("交大云盘空间凭据响应格式错误。")
        try:
            access_token = str(payload["accessToken"])
            library_id = str(payload["libraryId"])
            space_id = str(payload["spaceId"])
            expires_in = int(payload["expiresIn"])
        except (KeyError, TypeError, ValueError):
            raise CloudRemoteApiError("交大云盘空间凭据缺少必需字段。") from None
        if not access_token or not library_id or not space_id or expires_in <= 0:
            raise CloudRemoteApiError("交大云盘空间凭据格式错误。")
        return SpaceCredential(
            access_token=access_token,
            library_id=library_id,
            space_id=space_id,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
        )

    def get_space_credentials(self, *, force_refresh: bool = False) -> SpaceCredential:
        with self._credential_lock:
            if not force_refresh and self._credential and not self._credential.is_expired():
                return self._credential
            self._credential = self._fetch_credentials(self._token())
            return self._credential

    def refresh_credentials(self) -> SpaceCredential:
        return self.get_space_credentials(force_refresh=True)

    def _pan_api_url(
        self, kind: str, credential: SpaceCredential, path: RemotePath
    ) -> httpx.URL:
        encoded = _encoded_path(path)
        suffix = f"/{encoded}" if encoded else "/"
        base = httpx.URL(self._base_url)
        prefix = base.raw_path.rstrip(b"/")
        raw_path = prefix + (
            f"/api/v1/{kind}/{quote(credential.library_id, safe='')}/"
            f"{quote(credential.space_id, safe='')}{suffix}"
        ).encode("ascii")
        # Supplying raw_path is intentional: httpx must not quote the already encoded
        # UTF-8 path a second time (notably %, spaces, # and ? in file names).
        return base.copy_with(raw_path=raw_path, query=None)

    def _api_request(
        self,
        method: str,
        kind: str,
        path: RemotePath = (),
        *,
        operation: str,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        allow_not_found: bool = False,
    ) -> Any:
        for attempt in range(2):
            credential = self.get_space_credentials(force_refresh=attempt == 1)
            url = self._pan_api_url(kind, credential, path)
            query = dict(params or {})
            query["access_token"] = credential.access_token
            response = self._send_raw(method, url, params=query, json=json_body)
            if response.status_code in (401, 403) and attempt == 0:
                continue
            try:
                return self._decode(response, operation)
            except CloudNotFoundError:
                if allow_not_found:
                    return None
                raise
            except CloudAuthError:
                raise CloudCredentialExpiredError("刷新空间凭据后认证仍失败。") from None
        raise CloudCredentialExpiredError("刷新空间凭据后认证仍失败。")

    def get_space_info(self) -> SpaceInfo:
        response = self._send_raw("GET", f"{self._base_url}/user/v1/space/1", params={"user_token": self._token()})
        payload = self._decode(response, "读取空间信息")
        if not isinstance(payload, Mapping):
            raise CloudRemoteApiError("交大云盘空间信息响应格式错误。")
        return SpaceInfo(
            capacity=_integer(payload.get("capacity"), 0) or 0,
            used=_integer(payload.get("size"), 0) or 0,
            available=_integer(payload.get("availableSpace"), 0) or 0,
            has_personal_space=bool(payload.get("hasPersonalSpace", True)),
        )

    def list_directory(self, path: RemotePath = (), *, page: int = 1, page_size: int = 100) -> CloudDirectoryPage:
        if page < 1 or not 1 <= page_size <= 1000:
            raise ValueError("分页参数无效。")
        normalized = _segments(path)
        payload = self._api_request(
            "GET", "directory", normalized, operation="列举目录",
            params={"page": page, "page_size": page_size, "order_by": "name", "order_by_type": "asc"},
        )
        if not isinstance(payload, Mapping) or not isinstance(payload.get("contents", []), list):
            raise CloudRemoteApiError("交大云盘目录响应格式错误。")
        items = tuple(_item(value, normalized + (str(value.get("name", "")),)) for value in payload.get("contents", []) if isinstance(value, Mapping))
        return CloudDirectoryPage(
            items=items,
            page=page,
            page_size=page_size,
            total=_integer(payload.get("totalNum"), len(items)) or 0,
            path=tuple(str(value) for value in payload.get("path", normalized)),
        )

    def iter_directory(self, path: RemotePath = (), *, page_size: int = 100) -> Iterator[CloudItem]:
        page_number = 1
        accumulated = 0
        previous_fingerprint: tuple[tuple[str, ...], ...] | None = None
        while True:
            result = self.list_directory(path, page=page_number, page_size=page_size)
            fingerprint = tuple(item.path for item in result.items)
            if not result.items or fingerprint == previous_fingerprint:
                return
            yield from result.items
            accumulated += len(result.items)
            if result.total > 0 and accumulated >= result.total:
                return
            previous_fingerprint = fingerprint
            page_number += 1

    def create_directory(self, path: RemotePath) -> None:
        normalized = _segments(path)
        if not normalized:
            return
        self._api_request(
            "PUT", "directory", normalized, operation="创建目录",
            params={"conflict_resolution_strategy": "ask"},
        )

    def ensure_directory(self, path: RemotePath) -> None:
        current: list[str] = []
        for part in _segments(path):
            current.append(part)
            try:
                self.create_directory(tuple(current))
            except CloudConflictError:
                continue

    def get_info(self, path: RemotePath) -> CloudItem:
        normalized = _segments(path)
        payload = self._api_request("GET", "directory", normalized, operation="读取项目信息", params={"info": None})
        if not isinstance(payload, Mapping):
            raise CloudRemoteApiError("交大云盘项目信息响应格式错误。")
        return _item(payload, normalized)

    def exists(self, path: RemotePath) -> bool:
        try:
            self.get_info(path)
        except CloudNotFoundError:
            return False
        return True

    def _validate_object_url(self, url: str) -> str:
        parsed = urlsplit(url)
        host = parsed.hostname.lower().rstrip(".") if parsed.hostname else ""
        trusted = host in self._trusted_object_hosts or any(
            host.endswith(suffix) and host != suffix[1:]
            for suffix in TRUSTED_OBJECT_HOST_SUFFIXES
        )
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if (
            parsed.scheme != "https"
            or not trusted
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or parsed.fragment
        ):
            raise CloudRemoteApiError("服务端返回了不可信的对象存储地址。")
        return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))

    @staticmethod
    def _validate_download_range(response: httpx.Response, start: int | None, end: int | None) -> None:
        if start is None:
            if response.status_code != 200:
                raise CloudRemoteApiError(
                    f"下载响应状态无效（HTTP {response.status_code}）。",
                    status_code=response.status_code,
                )
            return
        if response.status_code != 206:
            raise CloudRemoteApiError(
                f"范围下载未返回 206（HTTP {response.status_code}）。",
                status_code=response.status_code,
            )
        match = CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
        if not match:
            raise CloudRemoteApiError("范围下载缺少有效 Content-Range。")
        actual_start, actual_end = (int(value) for value in match.groups())
        if actual_start != start or actual_end < actual_start or (end is not None and actual_end > end):
            raise CloudRemoteApiError("范围下载的 Content-Range 与请求不一致。")

    def download_stream(self, path: RemotePath, *, chunk_size: int = 64 * 1024, start: int | None = None, end: int | None = None) -> Iterator[bytes]:
        if chunk_size <= 0 or (start is not None and start < 0) or (end is not None and (start is None or end < start)):
            raise ValueError("下载范围或分块大小无效。")
        normalized = _segments(path)
        range_headers = {"Range": f"bytes={start}-{'' if end is None else end}"} if start is not None else {}

        def send_stream(
            url: str | httpx.URL, *, params: Mapping[str, Any] | None = None
        ) -> httpx.Response:
            try:
                request = self._client.build_request("GET", url, params=params, headers=range_headers)
                return self._client.send(request, stream=True)
            except httpx.TimeoutException:
                raise CloudTimeoutError("交大云盘下载超时。") from None
            except httpx.RequestError:
                raise CloudNetworkError("无法连接交大云盘服务。") from None

        def generate() -> Iterator[bytes]:
            response: httpx.Response | None = None
            for attempt in range(2):
                cred = self.get_space_credentials(force_refresh=attempt == 1)
                pan_url = self._pan_api_url("file", cred, normalized)
                response = send_stream(pan_url, params={"access_token": cred.access_token})
                if response.status_code in (401, 403) and attempt == 0:
                    response.close()
                    continue
                break
            if response is None:
                raise CloudNetworkError("无法连接交大云盘服务。")
            try:
                if response.status_code in (301, 302):
                    location = response.headers.get("Location")
                    response.close()
                    if not location:
                        raise CloudRemoteApiError("下载重定向缺少 Location。")
                    target = self._validate_object_url(urljoin(str(pan_url), location))
                    # Never forward Pan query credentials; only Range is copied.
                    response = send_stream(target)
                if response.status_code >= 400 or response.status_code in (301, 302):
                    response.read()
                    self._decode(response, "下载文件")
                self._validate_download_range(response, start, end)
                yield from response.iter_bytes(chunk_size)
            except httpx.TimeoutException:
                raise CloudTimeoutError("交大云盘下载超时。") from None
            except httpx.RequestError:
                raise CloudNetworkError("交大云盘下载中断。") from None
            finally:
                response.close()

        return generate()

    def download_temp(self, path: RemotePath, *, directory: Path | None = None) -> TemporaryDownload:
        target_dir = str(directory) if directory else None
        descriptor, temporary_name = tempfile.mkstemp(prefix="sjtu-pan-", suffix=".part", dir=target_dir)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                for chunk in self.download_stream(path):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            final_path = temporary_path.with_suffix("")
            os.replace(temporary_path, final_path)
            return TemporaryDownload(final_path)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary_path.unlink(missing_ok=True)
            temporary_path.with_suffix("").unlink(missing_ok=True)
            raise

    def delete(self, path: RemotePath, *, permanent: bool = False, missing_ok: bool = True) -> None:
        try:
            self._api_request("DELETE", "file", path, operation="删除项目", params={"permanent": 1 if permanent else 0})
        except CloudNotFoundError:
            if not missing_ok:
                raise

    def _copy_or_move(self, source: RemotePath, destination: RemotePath, *, move: bool, overwrite: bool) -> CloudItem:
        key = "from" if move else "copyFrom"
        payload = self._api_request(
            "PUT", "file", destination, operation="移动项目" if move else "复制项目",
            params={"conflict_resolution_strategy": "overwrite" if overwrite else "ask"},
            json_body={key: "/".join(_segments(source))},
        )
        return _item(payload, _segments(destination)) if isinstance(payload, Mapping) else self.get_info(destination)

    def move(self, source: RemotePath, destination: RemotePath, *, overwrite: bool = False) -> CloudItem:
        return self._copy_or_move(source, destination, move=True, overwrite=overwrite)

    def copy(self, source: RemotePath, destination: RemotePath, *, overwrite: bool = False) -> CloudItem:
        return self._copy_or_move(source, destination, move=False, overwrite=overwrite)

    @staticmethod
    def _read_source(source: bytes | BinaryIO) -> bytes:
        data = source if isinstance(source, bytes) else source.read()
        if not isinstance(data, bytes):
            raise TypeError("上传源必须产生 bytes。")
        return data

    def _signed_url(self, domain: str, object_path: str, params: Mapping[str, Any] | None = None) -> str:
        if not object_path.startswith("/") or object_path.startswith("//") or "\\" in object_path:
            raise CloudRemoteApiError("服务端返回了无效的对象存储路径。")
        candidate = domain if "://" in domain else f"https://{domain}"
        parsed = urlsplit(candidate)
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise CloudRemoteApiError("服务端返回了无效的对象存储域名。")
        url = urlunsplit(("https", parsed.netloc, object_path, "", ""))
        trusted_url = self._validate_object_url(url)
        return str(httpx.URL(trusted_url, params=params)) if params else trusted_url

    def _signed_put(self, domain: str, object_path: str, headers: Mapping[str, str], data: bytes, *, params: Mapping[str, Any] | None = None) -> None:
        url = self._signed_url(domain, object_path, params)
        safe_headers = {str(key): str(value) for key, value in headers.items()}
        response = self._send_raw("PUT", url, headers=safe_headers, content=data)
        if response.status_code >= 400:
            raise CloudRemoteApiError(f"对象存储上传失败（HTTP {response.status_code}）。", status_code=response.status_code)

    def simple_upload(self, path: RemotePath, source: bytes | BinaryIO, *, overwrite: bool = False) -> CloudItem:
        normalized = _segments(path)
        payload = self._api_request(
            "PUT", "file", normalized, operation="初始化简单上传",
            params={"conflict_resolution_strategy": "overwrite" if overwrite else "ask"}, json_body={},
        )
        if not isinstance(payload, Mapping) or not all(
            payload.get(key) for key in ("confirmKey", "domain", "path", "headers")
        ):
            raise CloudRemoteApiError("简单上传凭据响应格式错误。")
        headers = payload["headers"]
        if not isinstance(headers, Mapping):
            raise CloudRemoteApiError("简单上传凭据响应格式错误。")
        self._signed_put(str(payload["domain"]), str(payload["path"]), headers, self._read_source(source))
        confirmed = self._api_request(
            "POST", "file", (str(payload["confirmKey"]),), operation="确认简单上传",
            params={"confirm": None, "conflict_resolution_strategy": "overwrite" if overwrite else "ask"},
        )
        return _item(confirmed, normalized) if isinstance(confirmed, Mapping) else self.get_info(normalized)

    def init_multipart(self, path: RemotePath, *, part_numbers: Sequence[int] | None = None, overwrite: bool = False) -> UploadSession:
        normalized = _segments(path)
        numbers = list(part_numbers or range(1, MAX_PARTS_PER_RENEWAL + 1))
        if not numbers or len(numbers) > MAX_PARTS_PER_RENEWAL or any(number < 1 for number in numbers):
            raise ValueError("分片编号范围无效。")
        payload = self._api_request(
            "POST", "file", normalized, operation="初始化分片上传",
            params={"multipart": None, "conflict_resolution_strategy": "overwrite" if overwrite else "ask"},
            json_body={"partNumberRange": numbers},
        )
        session = self._session_from_payload(normalized, payload, overwrite=overwrite)
        self._cache_parts(session, payload)
        return session

    def _session_from_payload(self, path: tuple[str, ...], payload: Any, *, overwrite: bool) -> UploadSession:
        if not isinstance(payload, Mapping):
            raise CloudRemoteApiError("分片上传凭据响应格式错误。")
        confirm_key = str(payload.get("confirmKey", ""))
        if not confirm_key:
            raise CloudRemoteApiError("分片上传凭据缺少 confirmKey。")
        session = UploadSession(remote_path=path, confirm_key=confirm_key, overwrite=overwrite)
        self._apply_upload_authority(session, payload)
        return session

    def _apply_upload_authority(self, session: UploadSession, payload: Any) -> None:
        if not isinstance(payload, Mapping):
            raise CloudRemoteApiError("分片上传凭据响应格式错误。")
        try:
            domain = str(payload["domain"])
            object_path = str(payload["path"])
            upload_id = str(payload["uploadId"])
        except KeyError:
            raise CloudRemoteApiError("分片上传凭据缺少服务端上传目标。") from None
        if not domain or not object_path or not upload_id:
            raise CloudRemoteApiError("分片上传凭据格式错误。")
        # Validate before replacing runtime authority; persisted values are never consulted.
        self._signed_url(domain, object_path)
        old_upload_id = session.upload_id
        session.domain = domain
        session.object_path = object_path
        session.upload_id = upload_id
        session.expiration = str(payload.get("expiration")) if payload.get("expiration") else None
        session.authority_verified = True
        if old_upload_id and old_upload_id != upload_id:
            self._part_credentials.pop(old_upload_id, None)

    def _cache_parts(self, session: UploadSession, payload: Any) -> None:
        parts = payload.get("parts") if isinstance(payload, Mapping) else None
        if not isinstance(parts, Mapping):
            raise CloudRemoteApiError("分片上传响应缺少动态签名。")
        cache: dict[int, Mapping[str, str]] = self._part_credentials.setdefault(session.upload_id, {})
        try:
            for number, info in parts.items():
                headers = info.get("headers") if isinstance(info, Mapping) else None
                if isinstance(headers, Mapping):
                    cache[int(number)] = {str(key): str(value) for key, value in headers.items()}
        except (TypeError, ValueError):
            raise CloudRemoteApiError("分片上传响应包含无效分片编号。") from None

    def renew_upload(self, session: UploadSession, part_numbers: Sequence[int] | None = None) -> UploadSession:
        session.validate_contiguous()
        numbers = list(part_numbers or range(session.next_part, session.next_part + MAX_PARTS_PER_RENEWAL))
        if not numbers or len(numbers) > MAX_PARTS_PER_RENEWAL or any(number < session.next_part for number in numbers):
            raise ValueError("分片编号范围无效。")
        payload = self._api_request(
            "POST", "file", (session.confirm_key,), operation="续期分片上传",
            params={"renew": None}, json_body={"partNumberRange": numbers},
        )
        self._apply_upload_authority(session, payload)
        self._cache_parts(session, payload)
        return session

    def upload_part(self, session: UploadSession, part_number: int, data: bytes) -> None:
        session.validate_contiguous()
        if part_number != session.next_part or not isinstance(data, bytes) or not data:
            raise ValueError("分片必须按顺序上传且内容不能为空。")
        if not session.authority_verified:
            self.renew_upload(session, [part_number])
        headers = self._part_credentials.get(session.upload_id, {}).get(part_number)
        if headers is None:
            self.renew_upload(session, [part_number])
            headers = self._part_credentials.get(session.upload_id, {}).get(part_number)
        if headers is None:
            raise CloudRemoteApiError("服务端未返回该分片的动态签名。")
        try:
            self._signed_put(
                session.domain, session.object_path, headers, data,
                params={"uploadId": session.upload_id, "partNumber": part_number},
            )
        except CloudRemoteApiError:
            # A signed part request is idempotent. Renew its short-lived signature once.
            self.renew_upload(session, [part_number])
            renewed_headers = self._part_credentials.get(session.upload_id, {}).get(part_number)
            if renewed_headers is None:
                raise CloudRemoteApiError("续期后仍未获得该分片的动态签名。") from None
            self._signed_put(
                session.domain, session.object_path, renewed_headers, data,
                params={"uploadId": session.upload_id, "partNumber": part_number},
            )
        session.uploaded_parts.append(part_number)
        session.part_sizes[part_number] = len(data)
        session.next_part += 1

    def confirm_upload(self, session: UploadSession, *, overwrite: bool | None = None, crc64: str | None = None) -> CloudItem:
        session.validate_contiguous()
        effective_overwrite = session.overwrite if overwrite is None else overwrite
        payload = self._api_request(
            "POST", "file", (session.confirm_key,), operation="确认分片上传",
            params={"confirm": None, "conflict_resolution_strategy": "overwrite" if effective_overwrite else "ask"},
            json_body={"crc64": crc64} if crc64 is not None else None,
        )
        self._part_credentials.pop(session.upload_id, None)
        return _item(payload, session.remote_path) if isinstance(payload, Mapping) else self.get_info(session.remote_path)

    def resume_upload(self, session: UploadSession, source: bytes | BinaryIO, *, chunk_size: int = 4 * 1024 * 1024, overwrite: bool | None = None) -> CloudItem:
        if chunk_size <= 0:
            raise ValueError("分片大小无效。")
        session.validate_contiguous()
        if not session.authority_verified:
            self.renew_upload(session)
        stream = io.BytesIO(source) if isinstance(source, bytes) else source
        continuous_bytes = session.bytes_uploaded
        if continuous_bytes:
            if not hasattr(stream, "seek"):
                raise ValueError("恢复上传需要可定位的数据源。")
            stream.seek(continuous_bytes)
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            if not isinstance(chunk, bytes):
                raise TypeError("上传源必须产生 bytes。")
            self.upload_part(session, session.next_part, chunk)
        return self.confirm_upload(session, overwrite=overwrite)

    def multipart_upload(self, path: RemotePath, source: bytes | BinaryIO, *, overwrite: bool = False, chunk_size: int = 4 * 1024 * 1024) -> CloudItem:
        session = self.init_multipart(path, overwrite=overwrite)
        return self.resume_upload(session, source, chunk_size=chunk_size, overwrite=overwrite)

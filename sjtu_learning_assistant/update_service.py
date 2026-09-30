"""面向桌面应用的只检查、不自更新的安全更新服务。

服务只访问固定 HTTPS 主机，使用有界超时读取 GitHub Release 元数据。它返回下载
计划而不会下载、执行或覆盖当前应用；调用方在自行下载后必须显式执行 SHA-256 校验。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import platform
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Mapping, Sequence
from urllib.parse import unquote, urljoin, urlsplit

import httpx

DEFAULT_RELEASE_API_URL = (
    "https://api.github.com/repos/Augety-silence/"
    "sjtu-learning-assistant/releases/latest"
)
ALLOWED_UPDATE_HOSTS = frozenset(
    {
        "api.github.com",
        "github.com",
        "objects.githubusercontent.com",
        "release-assets.githubusercontent.com",
        "github-releases.githubusercontent.com",
    }
)
DEFAULT_TIMEOUT = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_CHECKSUM_BYTES = 64 * 1024
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SEMVER_RE = re.compile(
    r"^(?:v)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


class UpdateServiceError(RuntimeError):
    """可展示且不包含远端响应正文的更新检查错误。"""


class InvalidVersionError(UpdateServiceError, ValueError):
    pass


class InvalidUpdateMetadataError(UpdateServiceError, ValueError):
    pass


class UnsafeUpdateURLError(UpdateServiceError, ValueError):
    pass


class ChecksumMismatchError(UpdateServiceError):
    pass


@dataclass(frozen=True)
class SemanticVersion:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: object) -> "SemanticVersion":
        if type(value) is not str:
            raise InvalidVersionError("版本号必须是字符串。")
        match = _SEMVER_RE.fullmatch(value.strip())
        if match is None:
            raise InvalidVersionError("版本号不是严格的语义版本。")
        prerelease = tuple((match.group(4) or "").split(".")) if match.group(4) else ()
        build = tuple((match.group(5) or "").split(".")) if match.group(5) else ()
        if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in prerelease):
            raise InvalidVersionError("语义版本的预发布数字标识不能有前导零。")
        return cls(int(match.group(1)), int(match.group(2)), int(match.group(3)), prerelease, build)

    def _compare_prerelease(self, other: "SemanticVersion") -> int:
        if not self.prerelease and not other.prerelease:
            return 0
        if not self.prerelease:
            return 1
        if not other.prerelease:
            return -1
        for left, right in zip(self.prerelease, other.prerelease):
            if left == right:
                continue
            left_numeric, right_numeric = left.isdigit(), right.isdigit()
            if left_numeric and right_numeric:
                return -1 if int(left) < int(right) else 1
            if left_numeric != right_numeric:
                return -1 if left_numeric else 1
            return -1 if left < right else 1
        if len(self.prerelease) == len(other.prerelease):
            return 0
        return -1 if len(self.prerelease) < len(other.prerelease) else 1

    def compare(self, other: "SemanticVersion") -> int:
        left = (self.major, self.minor, self.patch)
        right = (other.major, other.minor, other.patch)
        if left != right:
            return -1 if left < right else 1
        return self._compare_prerelease(other)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        return self.compare(other) < 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemanticVersion):
            return NotImplemented
        # SemVer 的 build metadata 不参与版本优先级。
        return self.compare(other) == 0

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def __str__(self) -> str:
        value = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            value += "-" + ".".join(self.prerelease)
        if self.build:
            value += "+" + ".".join(self.build)
        return value


def compare_versions(left: str, right: str) -> int:
    """按 SemVer 优先级比较版本，返回 -1、0 或 1。"""

    return SemanticVersion.parse(left).compare(SemanticVersion.parse(right))


def is_newer_version(candidate: str, current: str) -> bool:
    return compare_versions(candidate, current) > 0


def validate_update_url(url: object, *, purpose: str = "asset") -> str:
    """验证 URL 的 scheme、认证信息、端口、主机及固定仓库路径。"""

    if type(url) is not str or not url or len(url) > 4096 or "\x00" in url:
        raise UnsafeUpdateURLError("更新地址格式无效。")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise UnsafeUpdateURLError("更新地址格式无效。") from exc
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme.lower() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or host not in ALLOWED_UPDATE_HOSTS
        or parsed.fragment
    ):
        raise UnsafeUpdateURLError("更新地址不在固定 HTTPS allowlist 中。")
    decoded_path = unquote(parsed.path)
    if "\x00" in decoded_path or any(part == ".." for part in decoded_path.split("/")):
        raise UnsafeUpdateURLError("更新地址路径不安全。")
    if purpose == "metadata":
        if host != "api.github.com" or decoded_path.rstrip("/") != urlsplit(
            DEFAULT_RELEASE_API_URL
        ).path:
            raise UnsafeUpdateURLError("Release 元数据地址不受信任。")
    elif purpose == "release-page":
        if host != "github.com" or not decoded_path.startswith(
            "/Augety-silence/sjtu-learning-assistant/releases/"
        ):
            raise UnsafeUpdateURLError("Release 页面不属于固定仓库。")
    elif host == "github.com" and not decoded_path.startswith(
        "/Augety-silence/sjtu-learning-assistant/releases/download/"
    ):
        raise UnsafeUpdateURLError("Release 下载地址不属于固定仓库。")
    return url


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int
    content_type: str | None = None
    sha256: str | None = None
    checksum_url: str | None = None

    @property
    def browser_download_url(self) -> str:
        return self.download_url


@dataclass(frozen=True)
class ReleaseMetadata:
    version: str
    tag_name: str
    page_url: str
    published_at: str | None
    notes: str
    prerelease: bool
    assets: tuple[ReleaseAsset, ...]


@dataclass(frozen=True)
class DownloadPlan:
    """供 UI 展示或外部下载器使用的信息；创建计划不会产生文件写入。"""

    version: str
    asset_name: str
    download_url: str
    size: int
    sha256: str | None
    checksum_url: str | None
    release_page_url: str
    requires_sha256_verification: bool = True


@dataclass(frozen=True)
class UpdateCheck:
    current_version: str
    latest_version: str
    update_available: bool
    release: ReleaseMetadata
    download_plan: DownloadPlan | None

    @property
    def plan(self) -> DownloadPlan | None:
        return self.download_plan


def _bounded_string(value: object, field: str, *, limit: int, required: bool = True) -> str:
    if type(value) is not str or "\x00" in value or len(value) > limit:
        raise InvalidUpdateMetadataError(f"Release {field} 格式无效。")
    clean = value.strip()
    if required and not clean:
        raise InvalidUpdateMetadataError(f"Release {field} 不能为空。")
    return clean


def normalize_sha256(value: object) -> str:
    if type(value) is not str:
        raise InvalidUpdateMetadataError("SHA-256 格式无效。")
    clean = value.strip()
    if clean.lower().startswith("sha256:"):
        clean = clean[7:].strip()
    if not _SHA256_RE.fullmatch(clean):
        raise InvalidUpdateMetadataError("SHA-256 格式无效。")
    return clean.lower()


def parse_sha256_metadata(content: str | bytes, asset_name: str | None = None) -> str:
    """解析常见 sha256sum sidecar；指定文件名时拒绝其他条目的摘要。"""

    if isinstance(content, bytes):
        if len(content) > MAX_CHECKSUM_BYTES:
            raise InvalidUpdateMetadataError("SHA-256 元数据过大。")
        try:
            content = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidUpdateMetadataError("SHA-256 元数据编码无效。") from exc
    if type(content) is not str or len(content.encode("utf-8")) > MAX_CHECKSUM_BYTES:
        raise InvalidUpdateMetadataError("SHA-256 元数据格式无效。")
    wanted = asset_name.replace("\\", "/").rsplit("/", 1)[-1] if asset_name else None
    unnamed: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        direct = re.fullmatch(r"([0-9a-fA-F]{64})", line)
        if direct:
            unnamed.append(direct.group(1).lower())
            continue
        standard = re.fullmatch(r"([0-9a-fA-F]{64})\s+[*]?(.+?)", line)
        openssl = re.fullmatch(r"SHA256\s*\((.+?)\)\s*=\s*([0-9a-fA-F]{64})", line, re.I)
        if standard:
            digest, filename = standard.group(1).lower(), standard.group(2).strip()
        elif openssl:
            filename, digest = openssl.group(1).strip(), openssl.group(2).lower()
        else:
            continue
        filename_basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if wanted is None or filename_basename == wanted:
            return digest
    if wanted and len(unnamed) == 1:
        return unnamed[0]
    if wanted is None and len(unnamed) == 1:
        return unnamed[0]
    raise InvalidUpdateMetadataError("未找到目标资产的 SHA-256。")


# 简短别名便于调用方使用。
parse_sha256 = parse_sha256_metadata


def _sidecar_for(asset_name: str, assets: Sequence[Mapping[str, Any]]) -> str | None:
    candidates = {
        asset_name + ".sha256",
        asset_name + ".sha256.txt",
        "SHA256SUMS",
        "sha256sums.txt",
        "checksums.txt",
    }
    for item in assets:
        if item.get("name") in candidates:
            url = item.get("browser_download_url")
            return validate_update_url(url) if isinstance(url, str) else None
    return None


def parse_release_metadata(payload: object) -> ReleaseMetadata:
    """将 GitHub Release JSON 解析为尺寸受限、URL 已校验的不可变元数据。"""

    if type(payload) is not dict:
        raise InvalidUpdateMetadataError("Release 元数据必须是对象。")
    if payload.get("draft") is True:
        raise InvalidUpdateMetadataError("不能使用草稿 Release。")
    tag = _bounded_string(payload.get("tag_name"), "tag_name", limit=100)
    version = str(SemanticVersion.parse(tag))
    page_url = validate_update_url(
        _bounded_string(payload.get("html_url"), "html_url", limit=4096),
        purpose="release-page",
    )
    raw_assets = payload.get("assets")
    if type(raw_assets) is not list or len(raw_assets) > 200:
        raise InvalidUpdateMetadataError("Release assets 格式无效。")
    parsed_assets: list[ReleaseAsset] = []
    for raw in raw_assets:
        if type(raw) is not dict:
            raise InvalidUpdateMetadataError("Release asset 格式无效。")
        name = _bounded_string(raw.get("name"), "asset name", limit=255)
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise InvalidUpdateMetadataError("Release asset 名称不安全。")
        url = validate_update_url(
            _bounded_string(raw.get("browser_download_url"), "asset url", limit=4096)
        )
        size = raw.get("size", 0)
        if type(size) is not int or not 0 <= size <= 20 * 1024 * 1024 * 1024:
            raise InvalidUpdateMetadataError("Release asset size 格式无效。")
        digest_value = raw.get("digest") or raw.get("sha256")
        digest = normalize_sha256(digest_value) if digest_value is not None else None
        content_type = raw.get("content_type")
        if content_type is not None:
            content_type = _bounded_string(content_type, "content_type", limit=200, required=False)
        parsed_assets.append(
            ReleaseAsset(
                name=name,
                download_url=url,
                size=size,
                content_type=content_type or None,
                sha256=digest,
                checksum_url=None if digest else _sidecar_for(name, raw_assets),
            )
        )
    published = payload.get("published_at")
    if published is not None:
        published = _bounded_string(published, "published_at", limit=64, required=False) or None
    notes = _bounded_string(payload.get("body", ""), "body", limit=50_000, required=False)
    return ReleaseMetadata(
        version=version,
        tag_name=tag,
        page_url=page_url,
        published_at=published,
        notes=notes,
        prerelease=payload.get("prerelease") is True,
        assets=tuple(parsed_assets),
    )


def _platform_tokens(system: str, machine: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    normalized_system = system.casefold()
    normalized_machine = machine.casefold()
    if normalized_system in {"darwin", "macos", "mac"}:
        os_tokens = ("macos", "darwin", ".dmg")
    elif normalized_system in {"windows", "win32", "win"}:
        os_tokens = ("windows", "win64", "setup.exe", "portable.zip")
    elif normalized_system == "linux":
        os_tokens = ("linux", "appimage")
    else:
        os_tokens = (normalized_system,)
    if normalized_machine in {"arm64", "aarch64"}:
        arch_tokens = ("arm64", "aarch64")
    elif normalized_machine in {"amd64", "x86_64", "x64"}:
        arch_tokens = ("x64", "amd64", "x86_64")
    else:
        arch_tokens = (normalized_machine,)
    return os_tokens, arch_tokens


def select_release_asset(
    release: ReleaseMetadata,
    *,
    asset_name: str | None = None,
    system: str | None = None,
    machine: str | None = None,
) -> ReleaseAsset:
    installable = [
        asset
        for asset in release.assets
        if not asset.name.casefold().endswith((".sha256", ".sha256.txt"))
        and asset.name.casefold() not in {"sha256sums", "sha256sums.txt", "checksums.txt"}
    ]
    if asset_name is not None:
        matches = [asset for asset in installable if asset.name == asset_name]
    else:
        os_tokens, arch_tokens = _platform_tokens(system or sys.platform, machine or platform.machine())
        matches = [
            asset
            for asset in installable
            if any(token in asset.name.casefold() for token in os_tokens)
            and any(token in asset.name.casefold() for token in arch_tokens)
        ]
        # 某些 DMG/Setup 命名已蕴含固定架构；没有架构 token 时允许唯一 OS 资产。
        if not matches:
            os_matches = [
                asset for asset in installable if any(token in asset.name.casefold() for token in os_tokens)
            ]
            matches = os_matches if len(os_matches) == 1 else []
    if len(matches) != 1:
        raise InvalidUpdateMetadataError("无法唯一确定当前平台的 Release asset。")
    return matches[0]


def build_download_plan(release: ReleaseMetadata, asset: ReleaseAsset) -> DownloadPlan:
    return DownloadPlan(
        version=release.version,
        asset_name=asset.name,
        download_url=asset.download_url,
        size=asset.size,
        sha256=asset.sha256,
        checksum_url=asset.checksum_url,
        release_page_url=release.page_url,
    )


def _iter_bytes(source: bytes | bytearray | memoryview | str | os.PathLike[str] | BinaryIO, chunk_size: int) -> Iterable[bytes]:
    if isinstance(source, (bytes, bytearray, memoryview)):
        yield bytes(source)
        return
    if isinstance(source, (str, os.PathLike)):
        with Path(source).open("rb") as handle:
            while chunk := handle.read(chunk_size):
                yield chunk
        return
    while chunk := source.read(chunk_size):
        if not isinstance(chunk, bytes):
            raise TypeError("校验流必须以二进制模式打开。")
        yield chunk


def sha256_digest(source: bytes | bytearray | memoryview | str | os.PathLike[str] | BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in _iter_bytes(source, 1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(
    source: bytes | bytearray | memoryview | str | os.PathLike[str] | BinaryIO,
    expected_sha256: str,
) -> bool:
    expected = normalize_sha256(expected_sha256)
    return hmac.compare_digest(sha256_digest(source), expected)


def require_sha256(
    source: bytes | bytearray | memoryview | str | os.PathLike[str] | BinaryIO,
    expected_sha256: str,
) -> str:
    expected = normalize_sha256(expected_sha256)
    actual = sha256_digest(source)
    if not hmac.compare_digest(actual, expected):
        raise ChecksumMismatchError("下载文件的 SHA-256 校验失败。")
    return actual


class UpdateService:
    """通过可注入 transport 的 httpx 客户端执行安全更新检查。"""

    def __init__(
        self,
        current_version: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: httpx.Timeout | float = DEFAULT_TIMEOUT,
    ) -> None:
        self.current_version = str(SemanticVersion.parse(current_version)) if current_version else None
        if isinstance(timeout, (int, float)):
            if not 0 < float(timeout) <= 30:
                raise ValueError("timeout 必须大于 0 且不超过 30 秒。")
            timeout = httpx.Timeout(float(timeout))
        if not isinstance(timeout, httpx.Timeout):
            raise TypeError("timeout 必须是秒数或 httpx.Timeout。")
        timeout_values = (timeout.connect, timeout.read, timeout.write, timeout.pool)
        if any(value is None or not 0 < value <= 30 for value in timeout_values):
            raise ValueError("所有网络 timeout 必须大于 0 且不超过 30 秒。")
        self.transport = transport
        self.timeout = timeout

    def _get(self, url: str, *, purpose: str, maximum: int) -> httpx.Response:
        validate_update_url(url, purpose=purpose)
        current_url = url
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "SJTU-Learning-Assistant-Update-Checker",
                },
            ) as client:
                for _ in range(4):
                    with client.stream("GET", current_url) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise UpdateServiceError("更新服务返回了无效重定向。")
                            current_url = validate_update_url(
                                urljoin(current_url, location), purpose=purpose
                            )
                            continue
                        response.raise_for_status()
                        declared_size = response.headers.get("content-length")
                        if declared_size is not None:
                            try:
                                parsed_size = int(declared_size)
                                if parsed_size < 0 or parsed_size > maximum:
                                    raise InvalidUpdateMetadataError(
                                        "远端更新元数据超过大小限制。"
                                    )
                            except ValueError as exc:
                                raise InvalidUpdateMetadataError(
                                    "远端更新元数据长度无效。"
                                ) from exc
                        chunks: list[bytes] = []
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > maximum:
                                raise InvalidUpdateMetadataError(
                                    "远端更新元数据超过大小限制。"
                                )
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        return httpx.Response(
                            status_code=response.status_code,
                            headers=response.headers,
                            content=content,
                            request=response.request,
                        )
                raise UpdateServiceError("更新服务重定向次数过多。")
        except UpdateServiceError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise UpdateServiceError("无法安全获取更新信息。") from exc

    def fetch_release(self) -> ReleaseMetadata:
        response = self._get(
            DEFAULT_RELEASE_API_URL,
            purpose="metadata",
            maximum=MAX_METADATA_BYTES,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise InvalidUpdateMetadataError("Release JSON 格式无效。") from exc
        return parse_release_metadata(payload)

    def resolve_asset_checksum(self, asset: ReleaseAsset) -> ReleaseAsset:
        if asset.sha256 is not None or asset.checksum_url is None:
            return asset
        response = self._get(
            asset.checksum_url,
            purpose="asset",
            maximum=MAX_CHECKSUM_BYTES,
        )
        digest = parse_sha256_metadata(response.content, asset.name)
        return ReleaseAsset(
            name=asset.name,
            download_url=asset.download_url,
            size=asset.size,
            content_type=asset.content_type,
            sha256=digest,
            checksum_url=asset.checksum_url,
        )

    def check(
        self,
        current_version: str | None = None,
        *,
        asset_name: str | None = None,
        system: str | None = None,
        machine: str | None = None,
        fetch_checksum: bool = True,
    ) -> UpdateCheck:
        current = str(SemanticVersion.parse(current_version or self.current_version))
        release = self.fetch_release()
        available = is_newer_version(release.version, current)
        plan = None
        if available:
            asset = select_release_asset(
                release,
                asset_name=asset_name,
                system=system,
                machine=machine,
            )
            if fetch_checksum:
                asset = self.resolve_asset_checksum(asset)
            plan = build_download_plan(release, asset)
        return UpdateCheck(current, release.version, available, release, plan)

    def check_for_update(self, *args: Any, **kwargs: Any) -> UpdateCheck:
        return self.check(*args, **kwargs)

    def check_for_updates(self, *args: Any, **kwargs: Any) -> UpdateCheck:
        return self.check(*args, **kwargs)

    # 显式别名强调这里只拉取元数据，不下载应用二进制。
    fetch_latest_release = fetch_release

    @staticmethod
    def verify_download(
        source: bytes | bytearray | memoryview | str | os.PathLike[str] | BinaryIO,
        expected_sha256: str,
    ) -> bool:
        return verify_sha256(source, expected_sha256)


# 便于桌面层采用领域命名，同时保留上面的精确类型名。
ReleaseInfo = ReleaseMetadata
UpdatePlan = DownloadPlan
parse_semver = SemanticVersion.parse
verify_file_sha256 = verify_sha256

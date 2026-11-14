"""公网邮件图片的匿名读取；不使用 Canvas、邮箱或浏览器凭据。"""

from __future__ import annotations

import base64
import ipaddress
import re
import socket
import ssl
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import certifi
import httpx

MAIL_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
MAIL_IMAGE_LIMIT = 5 * 1024 * 1024
RICH_MAIL_CONTENT_VERSION = 3


def raster_image_type(payload: bytes) -> str | None:
    """识别有限栅格图片文件头；不依赖扩展名，也不将 HTML/SVG 作为图片。"""
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if (len(payload) >= 16 and payload[:4] == b"RIFF"
            and payload[8:12] == b"WEBP" and payload[12:16] in {b"VP8 ", b"VP8L", b"VP8X"}):
        return "image/webp"
    return None


def trusted_mail_image_url(value: str) -> str | None:
    if not value or len(value) > 4096 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return None
    try:
        if value.startswith("//"):
            value = "https:" + value
        parsed = urlsplit(value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower()
        if (
            parsed.scheme != "https" or not host
            or host.endswith((".localhost", ".local", ".internal", ".test", ".example", ".invalid"))
            or host in {"localhost", "local", "internal", "test", "example", "invalid"}
            or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment
            or "\\" in value or "%" in host
            or parsed.path.startswith("/api/")
            or any(p in {".", ".."} for p in unquote(parsed.path).replace("\\", "/").split("/"))
        ):
            return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.split(".")):
                return None
        else:
            if not _public_address(address):
                return None
    except (ValueError, UnicodeError):
        return None
    netloc = f"[{host}]" if ":" in host else host
    return urlunsplit(("https", netloc, parsed.path, parsed.query, ""))


def _public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return address.is_global and not address.is_multicast and not (
        isinstance(address, ipaddress.IPv6Address) and
        (address.ipv4_mapped is not None or address.sixtofour is not None or address.teredo is not None)
    )


def _resolve_public_host(host: str) -> str:
    """全部 DNS 答案必须是公网地址；连接锁定本次验证 IP，防止再次解析重绑定。"""
    answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = tuple(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses or any(not _public_address(ipaddress.ip_address(address)) for address in addresses):
        raise ValueError("邮件图片地址不是安全的公网地址。")
    return addresses[0]


class _PublicImageTransport(httpx.BaseTransport):
    def __init__(self, transport: httpx.BaseTransport):
        self.transport = transport

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if trusted_mail_image_url(str(request.url)) is None:
            raise ValueError("邮件图片地址不安全。")
        host = request.url.host
        address = _resolve_public_host(host)
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        pinned = httpx.Request(
            request.method, request.url.copy_with(host=address), headers=headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        return self.transport.handle_request(pinned)

    def close(self) -> None:
        self.transport.close()


def fetch_mail_image(url: str, *, transport: httpx.BaseTransport | None = None) -> str:
    """逐跳验证并锁定公网 IP；TLS 校验原域名，拒绝 HTML/SVG 和超限响应。"""
    normalized = trusted_mail_image_url(url)
    if normalized is None:
        raise ValueError("邮件图片来源不受支持。")
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    with httpx.Client(
        # 禁用复用：IP 相同但域名不同的 CDN 不得共享已按另一 SNI 验证的连接。
        transport=_PublicImageTransport(transport or httpx.HTTPTransport(
            verify=context, trust_env=False, retries=0,
            limits=httpx.Limits(max_keepalive_connections=0),
        )),
        verify=context, trust_env=False, follow_redirects=False,
        timeout=httpx.Timeout(10, read=15),
        headers={"Accept": ", ".join(sorted(MAIL_IMAGE_TYPES)), "Accept-Encoding": "identity"},
    ) as client:
        current = normalized
        started = time.monotonic()
        for _ in range(4):
            # 不保留跨请求 cookie，且不附带 Authorization/Referer。
            client.cookies.clear()
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    next_url = urljoin(current, response.headers.get("location", ""))
                    normalized_next = trusted_mail_image_url(next_url)
                    if not response.headers.get("location") or normalized_next is None:
                        raise ValueError("邮件图片跳转地址不安全。")
                    current = normalized_next
                    continue
                response.raise_for_status()
                kind = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if kind not in MAIL_IMAGE_TYPES and kind not in {"application/octet-stream", "binary/octet-stream"}:
                    raise ValueError("邮件资源不是受支持的图片。")
                length = response.headers.get("content-length")
                if length is not None and not 0 <= int(length) <= MAIL_IMAGE_LIMIT:
                    raise ValueError("邮件图片超过大小限制。")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > 30:
                        raise ValueError("邮件图片读取超时。")
                    size += len(chunk)
                    if size > MAIL_IMAGE_LIMIT:
                        raise ValueError("邮件图片超过大小限制。")
                    chunks.append(chunk)
                payload = b"".join(chunks)
                detected = raster_image_type(payload)
                if detected is None or (kind in MAIL_IMAGE_TYPES and kind != detected):
                    raise ValueError("邮件资源的图片类型不匹配。")
                return f"data:{detected};base64," + base64.b64encode(payload).decode("ascii")
        raise ValueError("邮件图片跳转次数过多。")

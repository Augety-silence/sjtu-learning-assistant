"""邮件图片的有限来源和匿名读取；不使用 Canvas、邮箱或浏览器凭据。"""

from __future__ import annotations

import base64
import ssl
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import certifi
import httpx

MAIL_IMAGE_HOSTS = frozenset({"oc.sjtu.edu.cn", "s3.jcloud.sjtu.edu.cn"})
MAIL_IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
MAIL_IMAGE_LIMIT = 5 * 1024 * 1024
RICH_MAIL_CONTENT_VERSION = 2


def trusted_mail_image_url(value: str) -> str | None:
    if not value or len(value) > 4096 or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or parsed.hostname not in MAIL_IMAGE_HOSTS
            or parsed.port not in (None, 443)
            or parsed.username is not None or parsed.password is not None
            or parsed.fragment
            or parsed.path.startswith("/api/")
            or any(p in {".", ".."} for p in unquote(parsed.path).replace("\\", "/").split("/"))
        ):
            return None
    except ValueError:
        return None
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))


def fetch_mail_image(url: str, *, transport: httpx.BaseTransport | None = None) -> str:
    """用户明确加载后读取，逐跳验证两固定主机，禁止 HTML/SVG 和无限响应。"""
    if trusted_mail_image_url(url) is None:
        raise ValueError("邮件图片来源不受支持。")
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    with httpx.Client(
        transport=transport, verify=context, trust_env=False, follow_redirects=False,
        timeout=httpx.Timeout(10, read=15),
        headers={"Accept": ", ".join(sorted(MAIL_IMAGE_TYPES)), "Accept-Encoding": "identity"},
    ) as client:
        current = url
        for _ in range(4):
            # 不保留跨请求 cookie，且不附带 Authorization/Referer。
            client.cookies.clear()
            with client.stream("GET", current) as response:
                if response.is_redirect:
                    next_url = urljoin(current, response.headers.get("location", ""))
                    if not response.headers.get("location") or trusted_mail_image_url(next_url) is None:
                        raise ValueError("邮件图片跳转地址不安全。")
                    current = next_url
                    continue
                response.raise_for_status()
                kind = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if kind not in MAIL_IMAGE_TYPES:
                    raise ValueError("邮件资源不是受支持的图片。")
                length = response.headers.get("content-length")
                if length is not None and not 0 <= int(length) <= MAIL_IMAGE_LIMIT:
                    raise ValueError("邮件图片超过大小限制。")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAIL_IMAGE_LIMIT:
                        raise ValueError("邮件图片超过大小限制。")
                    chunks.append(chunk)
                return f"data:{kind};base64," + base64.b64encode(b"".join(chunks)).decode("ascii")
        raise ValueError("邮件图片跳转次数过多。")

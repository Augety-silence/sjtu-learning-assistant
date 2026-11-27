"""Validate bundled UI and avoid Windows file:// navigation ambiguities.

WebView2 virtual host mapping serves *only* the trusted frontend directory.
No HTTP listener, account settings or data-directory fallback is introduced.
"""
from __future__ import annotations

import hashlib
import html
from html.parser import HTMLParser
from pathlib import Path
import threading
from urllib.parse import unquote, urlsplit

UI_HOST = "sjtu-desktop-ui.invalid"
BOOTSTRAP_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<style>body{font:16px system-ui;background:#f5f5f5;color:#222;margin:48px;line-height:1.8}</style>
<h1>SJTU 学习助手</h1><p>正在检查本地界面资源并初始化 WebView2…</p></html>"""


class FrontendResourceError(RuntimeError):
    """A safe relative-path message that can be shown before opening the UI."""


class _References(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag, attrs) -> None:
        values = dict(attrs)
        if tag in {"script", "img"} and values.get("src"):
            self.references.append(values["src"])
        if tag == "link" and values.get("href"):
            self.references.append(values["href"])


def validate_frontend(index: Path) -> str:
    """Check HTML and its direct local script/style/image references before navigation."""
    index = Path(index)
    if not index.is_file():
        raise FrontendResourceError("安装包缺少界面入口 index.html，请完整解压或重新安装。")
    try:
        payload = index.read_bytes()
        if not payload or len(payload) > 2 * 1024 * 1024:
            raise FrontendResourceError("界面入口文件为空或超出读取范围。")
        parser = _References()
        parser.feed(payload.decode("utf-8"))
    except (OSError, UnicodeError) as exc:
        raise FrontendResourceError("界面入口无法读取，请检查文件权限或重新安装。") from exc
    root = index.parent.resolve()
    if len(parser.references) > 256:
        raise FrontendResourceError("界面资源引用数量异常。")
    for reference in parser.references:
        parts = urlsplit(reference)
        if parts.scheme or parts.netloc or not parts.path:
            # Inline data and explicit remote links are not filesystem paths.
            continue
        relative = unquote(parts.path).lstrip("/")
        if "\\" in relative or "\x00" in relative:
            raise FrontendResourceError("界面包含无效的本地资源引用。")
        resource = (root / relative).resolve()
        if not resource.is_relative_to(root):
            raise FrontendResourceError("界面资源引用超出安装包目录。")
        if not resource.is_file():
            safe_name = Path(relative).name[:80]
            raise FrontendResourceError(f"安装包缺少界面资源：{safe_name}。请完整解压或重新安装。")
    return hashlib.sha256(payload).hexdigest()[:16]


def frontend_url(fingerprint: str) -> str:
    if len(fingerprint) != 16 or any(c not in "0123456789abcdef" for c in fingerprint):
        raise ValueError("Invalid frontend fingerprint")
    return f"https://{UI_HOST}/index.html?build={fingerprint}"


class WindowsFrontendLoader:
    """Initialize CoreWebView2 on the UI thread, map local content, then navigate.

    The initial navigation uses a small in-memory page, not an on-disk file URL.
    No repeated/fallback account requests or global web-security flags are used.
    """

    def __init__(self, index: Path, *, report=None, log=None) -> None:
        self.index = Path(index)
        self.url = frontend_url(validate_frontend(self.index))
        self.report = report or (lambda text: None)
        self.log = log or (lambda text: None)
        self._state = "bootstrap"
        self._lock = threading.Lock()
        self._window = None
        self._navigation_handler = None

    def attach(self, window) -> None:
        self._window = window
        window.events.loaded += self._loaded

    def _failed(self, reason: str) -> None:
        with self._lock:
            if self._state == "failed":
                return
            self._state = "failed"
        self.log(reason)
        message = (
            "界面启动失败：" + reason + "。请完整解压测试包，并安装 Microsoft Edge WebView2 Runtime。"
        )
        try:
            if self._window is not None:
                self._window.load_html(
                    '<!doctype html><meta charset="utf-8"><h1>界面启动失败</h1><p>'
                    + html.escape(message) + '</p>'
                )
        except Exception:
            pass
        self.report(message)

    def _loaded(self, *_args) -> None:
        with self._lock:
            if self._state != "bootstrap":
                return
            self._state = "mapping"
        try:
            native = self._window.native
            # pywebview 5.4 exposes the actual WinForms WebView2 control here.
            control = getattr(native, "webview", None)
            if control is None:
                raise RuntimeError("未找到 WebView2 控件")
            from System import Action
            from Microsoft.Web.WebView2.Core import CoreWebView2HostResourceAccessKind

            def install_mapping() -> None:
                try:
                    core = control.CoreWebView2
                    if core is None:
                        raise RuntimeError("WebView2 尚未初始化")
                    core.SetVirtualHostNameToFolderMapping(
                        UI_HOST,
                        str(self.index.parent.resolve()),
                        CoreWebView2HostResourceAccessKind.DenyCors,
                    )
                    self._navigation_handler = self._navigation_completed
                    core.NavigationCompleted += self._navigation_handler
                    with self._lock:
                        self._state = "navigating"
                    core.Navigate(self.url)
                except Exception as exc:
                    # No raw paths, account data or exception messages in logs.
                    self._failed("WebView2 本地映射失败（" + type(exc).__name__ + "）")

            native.Invoke(Action(install_mapping))
        except Exception as exc:
            self._failed("WebView2 初始化失败（" + type(exc).__name__ + "）")

    def _navigation_completed(self, _sender, args) -> None:
        with self._lock:
            if self._state != "navigating":
                return
            self._state = "loaded" if args.IsSuccess else "navigation-failed"
        if args.IsSuccess:
            self.log("frontend-navigation-ok")
        else:
            self._failed("WebView2 导航失败（" + str(args.WebErrorStatus)[:80] + "）")

"""Offline native startup gate for the actual packaged React frontend.

Mocked unit tests are not native proof. Release proof requires executing the
packaged app on its native OS. This mode imports no application services.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import time
import webbrowser

from .frontend_startup import BOOTSTRAP_HTML, WindowsFrontendLoader, validate_frontend

TIMEOUT_SECONDS = 85
HARD_TIMEOUT_SECONDS = 89
FRONTEND_PATH = "dashboard-web/dist/index.html"
RENDERERS = dict(darwin="wkwebview", win32="edgechromium")

# A rendered shell/navigation/header are required, not merely #root or an error
# boundary. Rejected page-level data requests are expected in this offline mode.
PROBE = r"""(() => {
  const shell = document.querySelector('#root .v2-shell');
  const box = shell ? shell.getBoundingClientRect() : null;
  const ready = !!(window.pywebview && window.pywebview.api &&
    typeof window.pywebview.api.invoke === 'function');
  const appShell = !!(shell && box.width > 0 && box.height > 0 &&
    document.documentElement.dataset.uiRelease === '2' &&
    shell.querySelector('.v2-sidebar nav[aria-label="主导航"] button') &&
    shell.querySelector('.v2-main .v2-app-header') &&
    shell.querySelector('.v2-content .v2-page-heading h1'));
  const rootError = !!document.querySelector('#root > main > .state-error[role="alert"]');
  const moduleScript = !!document.querySelector('script[type="module"][src]');
  if (ready && !window.__sjtuStartupProbeSent) {
    window.__sjtuStartupProbeSent = true;
    window.pywebview.api.invoke('startup_probe', {nonce: __NONCE__}).then(r => {
      window.__sjtuStartupProbeReady = !!(r && r.ok && r.data && r.data.nonce === __NONCE__);
    }).catch(() => { window.__sjtuStartupProbeReady = false; });
  }
  return {app_shell: appShell && moduleScript && !rootError,
    bridge_ready: ready && window.__sjtuStartupProbeReady === true};
})()"""


class RejectBridge:
    """Controlled boot DTOs and a nonce handshake; never dispatch real APIs."""

    def __init__(self, nonce):
        self._nonce = nonce
        self._probe_seen = False

    def invoke(self, action, payload=None):
        if action == "startup_probe" and isinstance(payload, dict) and payload.get("nonce") == self._nonce:
            self._probe_seen = True
            return dict(ok=True, data=dict(nonce=self._nonce))
        if action == "settings_status":
            return dict(ok=True, data=dict(theme_mode="system", ai_enabled=False,
                current_term_md_enabled=False, course_markdown_enabled=False,
                recording_asr_enabled=False, recording_asr_paused=True))
        if action == "capabilities":
            return dict(ok=True, data=dict(courses=dict(items=list()), media=dict(),
                update=dict(check_only=True, automatic_install=False),
                mcp=dict(transport="disabled", read_only=True)))
        if action == "sync_status":
            return dict(ok=True, data=dict(status="idle", last_success_at=None,
                last_canvas_success_at=None, last_run_status=None, last_run_at=None))
        return dict(ok=False, error=dict(code="not_allowed", message="Unavailable in offline startup self-test."))


def _deny_network(*args, **kwargs):
    raise OSError("Network disabled in startup self-test")


def _isolate_environment(directory):
    directory = Path(directory)
    roots = dict(HOME=directory / "home", USERPROFILE=directory / "home",
        APPDATA=directory / "roaming", LOCALAPPDATA=directory / "local",
        XDG_CONFIG_HOME=directory / "config", XDG_CACHE_HOME=directory / "cache",
        XDG_DATA_HOME=directory / "data", SJTU_APP_SUPPORT_DIR=directory / "support",
        TMPDIR=directory / "temp", TEMP=directory / "temp", TMP=directory / "temp")
    for key in tuple(os.environ):
        if key.startswith(("SJTU_", "OPENAI_", "ANTHROPIC_", "AZURE_")) or key.lower() in (
            "http_proxy", "https_proxy", "all_proxy", "no_proxy"):
            os.environ.pop(key, None)
    for key, value in roots.items():
        value.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.environ.update(((key, str(value)),))
    os.environ.update(PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring", PYTHONUTF8="1")
    socket.socket.connect = _deny_network
    socket.socket.connect_ex = _deny_network
    socket.create_connection = _deny_network
    socket.getaddrinfo = _deny_network
    webbrowser.open = lambda *args, **kwargs: False
    webbrowser.open_new = webbrowser.open
    webbrowser.open_new_tab = webbrowser.open
    return directory / "profile"


def _secure_macos(profile):
    """Ephemeral WK store and native remote-content blocking BEFORE page load."""
    import Foundation
    import objc
    import webview.platforms.cocoa as cocoa

    kit = cocoa.WebKit
    isolated_store = kit.WKWebsiteDataStore.nonPersistentDataStore()
    rules_dir = Path(profile) / "rules"
    rules_dir.mkdir(parents=True, mode=0o700)
    store = kit.WKContentRuleListStore.storeWithURL_(Foundation.NSURL.fileURLWithPath_(str(rules_dir)))
    compiled = dict(done=False, value=None)

    def completion(value, error):
        compiled.update(done=True, value=value if error is None else None)

    rules = json.dumps(({"trigger": {"url-filter": "^https?://"}, "action": {"type": "block"}}, {"trigger": {"url-filter": "^wss?://"}, "action": {"type": "block"}}, {"trigger": {"url-filter": "^ftp://"}, "action": {"type": "block"}},))
    store.compileContentRuleListForIdentifier_encodedContentRuleList_completionHandler_(
        "sjtu-offline-startup", rules, completion)
    deadline = time.monotonic() + 10
    while not compiled.get("done") and time.monotonic() < deadline:
        Foundation.NSRunLoop.currentRunLoop().runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.05))
    if compiled.get("value") is None:
        raise RuntimeError("Native content blocking unavailable")
    rule = compiled.get("value")
    base = cocoa.BrowserView.WebKitHost

    def initWithFrame_(self, frame):
        config = kit.WKWebViewConfiguration.new()
        config.setWebsiteDataStore_(isolated_store)
        config.userContentController().addContentRuleList_(rule)
        return objc.super(base, self).initWithFrame_configuration_(frame, config)

    # Keep BrowserView.WebKitHost itself: pywebview's inherited event handlers
    # use super(BrowserView.WebKitHost, self), which must not point at a subclass.
    objc.classAddMethods(base, (objc.selector(initWithFrame_, signature=base.initWithFrame_.signature),))

    class PrivateStores:
        def defaultDataStore(self):
            return isolated_store

        def __getattr__(self, name):
            return getattr(kit.WKWebsiteDataStore, name)

    class PrivateKit:
        WKWebsiteDataStore = PrivateStores()

        def __getattr__(self, name):
            return getattr(kit, name)

    cocoa.WebKit = PrivateKit()


class _OfflineWindowsLoader(WindowsFrontendLoader):
    def __init__(self, index, session):
        super().__init__(index, report=lambda text: session.fail("navigation_failed"))
        self._session = session
        self._filter_handler = None

    def _loaded(self, *args):
        if self._state != "bootstrap":
            return
        try:
            from System import Action
            from Microsoft.Web.WebView2.Core import CoreWebView2WebResourceContext
            native = self._window.native

            def install_filter():
                try:
                    core = native.webview.CoreWebView2
                    if core is None:
                        raise RuntimeError("WebView2 unavailable")

                    def filter_request(sender, args):
                        uri = str(args.Request.Uri)
                        if not uri.startswith("https://sjtu-desktop-ui.invalid/"):
                            args.Response = core.Environment.CreateWebResourceResponse(
                                None, 403, "Blocked", "Content-Type: text/plain")

                    self._filter_handler = filter_request
                    core.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All)
                    core.WebResourceRequested += filter_request
                    self._session.network_blocked = True
                except Exception:
                    self._session.fail("network_guard_failed")

            native.Invoke(Action(install_filter))
            if self._session.network_blocked:
                super()._loaded()
        except Exception:
            self._session.fail("network_guard_failed")


class _Session:
    def __init__(self, bridge, frontend_hash, report):
        self.bridge = bridge
        self.frontend_hash = frontend_hash
        self.report = report
        self.window = None
        self.webview = None
        self.network_blocked = False
        self.result = None
        self.reason = "not_ready"
        self.closed = threading.Event()
        self.lock = threading.Lock()

    def finish(self, result, reason):
        with self.lock:
            if self.result is not None:
                return
            if result == "passed" and getattr(self.webview, "renderer", None) != RENDERERS.get(sys.platform):
                result, reason = "failed", "renderer_unavailable"
            self.result, self.reason = result, reason
        if self.window is not None:
            threading.Thread(target=self._destroy, daemon=True).start()

    def _destroy(self):
        try:
            self.window.destroy()
        except Exception:
            pass

    def fail(self, reason):
        self.finish("failed", reason)

    def poll(self):
        script = PROBE.replace("__NONCE__", json.dumps(self.bridge._nonce))
        while not self.closed.is_set() and self.result is None:
            try:
                value = self.window.evaluate_js(script)
                if (isinstance(value, dict) and value.get("app_shell") is True
                        and value.get("bridge_ready") is True and self.bridge._probe_seen
                        and self.network_blocked):
                    self.finish("passed", "ok")
                    return
            except Exception:
                pass  # Bootstrap and native navigation may not yet be ready.
            self.closed.wait(0.1)

    def payload(self):
        renderer = getattr(self.webview, "renderer", None)
        valid_renderer = renderer == RENDERERS.get(sys.platform)
        passed = self.result == "passed" and valid_renderer and self.network_blocked and self.bridge._probe_seen
        return dict(schema_version=1, result="passed" if passed else "failed",
            reason="ok" if passed else self.reason if valid_renderer else "renderer_unavailable",
            renderer=renderer if valid_renderer else "unavailable",
            frontend=dict(path=FRONTEND_PATH, sha256=self.frontend_hash, fingerprint=self.frontend_hash[:16]),
            app_shell=passed, bridge_ready=passed, data_isolated=True,
            network_blocked=self.network_blocked, backend="reject-only")

    def watchdog(self):
        if not self.closed.wait(TIMEOUT_SECONDS):
            self.fail("timeout")
            _write_report(self.report, self.payload())
            if not self.closed.wait(HARD_TIMEOUT_SECONDS - TIMEOUT_SECONDS):
                os._exit(2)  # Also bounds a wedged native GUI call.


def _write_report(path, value):
    payload = (json.dumps(value, sort_keys=True) + "\n").encode("utf-8")
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)
    return True


def run(report):
    """Only a frozen native app may issue proof; no normal startup is invoked."""
    report = Path(report)
    if (report.suffix.lower() != ".json" or not report.is_absolute() or report.exists() or any(p.is_symlink() for p in (report, *report.parents))
            or not report.parent.is_dir() or not getattr(sys, "frozen", False)
            or sys.platform not in RENDERERS or not hasattr(sys, "_MEIPASS")):
        return 2
    root = Path(sys._MEIPASS).resolve()
    if report.is_relative_to(root):
        return 2
    index = root / FRONTEND_PATH
    try:
        fingerprint = validate_frontend(index)
        frontend_hash = hashlib.sha256(index.read_bytes()).hexdigest()
        if frontend_hash[:16] != fingerprint:
            return 2
    except Exception:
        _write_report(report, dict(schema_version=1, result="failed", reason="frontend_resources_invalid"))
        return 2
    bridge = RejectBridge(secrets.token_hex(16))
    session = _Session(bridge, frontend_hash, report)
    threading.Thread(target=session.watchdog, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="sjtu-startup-private-", dir=report.parent) as directory:
            profile = _isolate_environment(Path(directory))
            profile.mkdir(mode=0o700)
            import webview
            session.webview = webview
            if sys.platform == "darwin":
                _secure_macos(profile)
                session.network_blocked = True
            loader = _OfflineWindowsLoader(index, session) if sys.platform == "win32" else None
            options = dict(html=BOOTSTRAP_HTML) if loader is not None else dict()
            url = None if loader is not None else index.as_uri() + "?build=" + fingerprint
            window = webview.create_window("SJTU packaged startup self-test", url,
                js_api=bridge, width=1280, height=820, min_size=(960, 640), **options)
            session.window = window
            if loader is not None:
                loader.attach(window)
            window.events.closed += lambda: session.closed.set()
            webview.start(session.poll, debug=False, private_mode=True, storage_path=str(profile),
                http_server=False, gui="edgechromium" if sys.platform == "win32" else None)
            session.closed.set()
    except Exception:
        session.fail("native_startup_failed")
        session.closed.set()
    if session.result is None:
        session.fail("closed_before_ready")
    try:
        if hashlib.sha256(index.read_bytes()).hexdigest() != frontend_hash:
            session.result, session.reason = "failed", "frontend_changed"
    except OSError:
        session.result, session.reason = "failed", "frontend_changed"
    result = session.payload()
    written = _write_report(report, result)
    return 0 if written and result.get("result") == "passed" else 2


def main(argv=None):
    values = sys.argv[1:] if argv is None else argv
    if len(values) != 1:
        return 2
    return run(Path(values[0]))

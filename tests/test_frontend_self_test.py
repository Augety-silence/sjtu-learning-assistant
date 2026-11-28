"""Unit fakes verify gate safety; never claim native packaged startup passed."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import os
import socket
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from sjtu_learning_assistant import frontend_self_test as gate


class Event:
    def __init__(self):
        self.items = list()
    def __iadd__(self, item):
        self.items.append(item)
        return self
    def set(self):
        for item in self.items:
            item()


class FrontendSelfTestTests(unittest.TestCase):
    def test_bridge_rejects_actions_and_requires_real_nonce_round_trip(self):
        bridge = gate.RejectBridge("nonce")
        for action in ("trigger_sync", "ai_chat_send", "save_settings", "recording_asr_start", "download_material"):
            self.assertFalse(bridge.invoke(action).get("ok"))
        self.assertFalse(bridge.invoke("startup_probe", dict(nonce="wrong")).get("ok"))
        self.assertFalse(bridge._probe_seen)
        self.assertTrue(bridge.invoke("startup_probe", dict(nonce="nonce")).get("ok"))
        self.assertTrue(bridge._probe_seen)
        self.assertFalse(bridge.invoke("settings_status").get("data").get("ai_enabled"))
        self.assertEqual(list(), bridge.invoke("capabilities").get("data").get("courses").get("items"))

    def test_invalid_mode_and_existing_report_fail_without_native_start(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory).resolve() / "report.json"
            with patch.object(sys, "frozen", False, create=True), patch.object(gate, "_isolate_environment") as isolate:
                self.assertEqual(2, gate.run(report))
                self.assertEqual(2, gate.run(Path("relative.json")))
                self.assertEqual(2, gate.main(tuple()))
            isolate.assert_not_called()
            self.assertFalse(report.exists())
            self.assertTrue(gate._write_report(report, dict(result="failed")))
            before = report.read_bytes()
            self.assertFalse(gate._write_report(report, dict(result="passed")))
            self.assertEqual(before, report.read_bytes())

    def test_isolation_clears_provider_environment_and_blocks_python_network(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, dict(SJTU_DATABASE_URL="fake", OPENAI_API_KEY="fake", HTTPS_PROXY="fake")):
            with patch.object(socket.socket, "connect"), patch.object(socket.socket, "connect_ex"), patch.object(socket, "create_connection"), patch.object(socket, "getaddrinfo"), patch.object(gate.webbrowser, "open"), patch.object(gate.webbrowser, "open_new"), patch.object(gate.webbrowser, "open_new_tab"):
                gate._isolate_environment(Path(directory))
                for key in ("SJTU_DATABASE_URL", "OPENAI_API_KEY", "HTTPS_PROXY"):
                    self.assertNotIn(key, os.environ)
                for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SJTU_APP_SUPPORT_DIR"):
                    self.assertTrue(Path(os.environ.get(key)).is_relative_to(directory))
                with self.assertRaises(OSError):
                    socket.create_connection(("example.invalid", 443))
                self.assertFalse(gate.webbrowser.open("https://example.invalid"))

    def test_probe_requires_shell_navigation_header_and_bridge_handshake(self):
        for selector in (".v2-shell", "dataset.uiRelease === '2'", ".v2-sidebar nav", ".v2-main .v2-app-header", ".v2-content .v2-page-heading h1", "rootError", "moduleScript", "startup_probe", "__sjtuStartupProbeReady"):
            self.assertIn(selector, gate.PROBE)
        self.assertNotIn(".app-shell", gate.PROBE)
        session = gate._Session(gate.RejectBridge("nonce"), "a".ljust(64, "a"), Path("unused.json"))
        session.webview = SimpleNamespace(renderer="qt")
        session.network_blocked = True
        session.result = "passed"
        with patch.object(sys, "platform", "darwin"):
            self.assertEqual("failed", session.payload().get("result"))
            session.webview.renderer = "wkwebview"
            self.assertEqual("failed", session.payload().get("result"))
            session.bridge.invoke("startup_probe", dict(nonce="nonce"))
            self.assertEqual("passed", session.payload().get("result"))

    def _fake_gui(self, platform_name, shell=True, ready=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = root / "package"
            index = package / gate.FRONTEND_PATH
            index.parent.mkdir(parents=True)
            source = b'<html><script type="module" src="entry.js"></script><div id="root"></div></html>'
            index.write_bytes(source)
            index.with_name("entry.js").write_bytes(b"synthetic unit fixture, not native proof")
            report = root / "report.json"
            calls = dict()
            window = SimpleNamespace(events=SimpleNamespace(closed=Event()))
            def create_window(*args, **kwargs):
                calls.update(url=args[1], options=kwargs)
                def probe(script):
                    bridge = kwargs.get("js_api")
                    if ready:
                        bridge.invoke("startup_probe", dict(nonce=bridge._nonce))
                    if not shell or not ready:
                        window.events.closed.set()
                    return dict(app_shell=shell, bridge_ready=ready)
                window.evaluate_js = probe
                return window
            window.destroy = window.events.closed.set
            webview = SimpleNamespace(renderer=gate.RENDERERS.get(platform_name), create_window=create_window)
            def start(func, **kwargs):
                calls.update(start_options=kwargs)
                func()
            webview.start = start
            loader = Mock()
            def loader_factory(index, session):
                session.network_blocked = True
                calls.update(loader_index=index)
                return loader
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(package), create=True), patch.object(sys, "platform", platform_name), patch.dict(sys.modules, dict(webview=webview)), patch.object(gate, "_isolate_environment", side_effect=lambda path: Path(path) / "profile"), patch.object(gate, "_secure_macos"), patch.object(gate, "_OfflineWindowsLoader", side_effect=loader_factory), patch.object(gate._Session, "watchdog"):
                code = gate.run(report)
            value = json.loads(report.read_text())
            self.assertEqual(hashlib.sha256(source).hexdigest(), value.get("frontend").get("sha256"))
            self.assertNotIn(str(root), report.read_text())
            self.assertEqual(source, index.read_bytes())
            return code, value, calls, loader

    def test_fake_gui_loads_packaged_index_and_never_synthetic_html_as_final_ui(self):
        for platform_name in ("darwin", "win32"):
            with self.subTest(platform_name=platform_name):
                code, value, calls, loader = self._fake_gui(platform_name)
                self.assertEqual(0, code)  # Fake only, not native release proof.
                self.assertEqual("reject-only", value.get("backend"))
                self.assertTrue(calls.get("start_options").get("private_mode"))
                self.assertFalse(calls.get("start_options").get("http_server"))
                if platform_name == "darwin":
                    self.assertTrue(calls.get("url").startswith("file://"))
                    self.assertIn("/dashboard-web/dist/index.html" + chr(63) + "build=", calls.get("url"))
                    self.assertNotIn("html", calls.get("options"))
                    loader.attach.assert_not_called()
                else:
                    self.assertIsNone(calls.get("url"))
                    self.assertEqual(gate.BOOTSTRAP_HTML, calls.get("options").get("html"))
                    self.assertEqual("index.html", calls.get("loader_index").name)
                    loader.attach.assert_called_once()

    def test_fake_placeholder_or_unready_bridge_never_passes(self):
        for shell, ready in ((False, True), (True, False)):
            with self.subTest(shell=shell, ready=ready):
                code, value, _, _ = self._fake_gui("darwin", shell=shell, ready=ready)
                self.assertEqual(2, code)
                self.assertEqual("failed", value.get("result"))

    def test_dispatch_bypasses_normal_services(self):
        import desktop_app
        with tempfile.TemporaryDirectory() as directory:
            report = str(Path(directory).resolve() / "report.json")
            with patch.object(sys, "argv", ("app", "--startup-self-test", report)), patch.object(gate, "main", return_value=2) as run, patch.object(desktop_app, "run_desktop_app", side_effect=AssertionError("normal startup forbidden")), patch.object(desktop_app, "create_database_engine", side_effect=AssertionError("DB forbidden")):
                self.assertEqual(2, desktop_app.main())
            self.assertEqual((report,), tuple(run.call_args.args[0]))

    def test_mac_guard_keeps_original_host_class_and_never_opens_default_store(self):
        def original_init(self, frame):
            return self
        original_init.signature = b"fake-signature"
        host = type("FakeHost", (object,), dict(initWithFrame_=original_init))
        isolated, rule = object(), object()
        native_store = Mock()
        native_store.compileContentRuleListForIdentifier_encodedContentRuleList_completionHandler_.side_effect = lambda name, rules, done: done(rule, None)
        kit = SimpleNamespace(
            WKWebsiteDataStore=SimpleNamespace(nonPersistentDataStore=lambda: isolated),
            WKContentRuleListStore=SimpleNamespace(storeWithURL_=lambda url: native_store),
        )
        cocoa = SimpleNamespace(WebKit=kit, BrowserView=SimpleNamespace(WebKitHost=host))
        foundation = SimpleNamespace(NSURL=SimpleNamespace(fileURLWithPath_=lambda path: path))
        objc = SimpleNamespace(selector=lambda function, signature: function, classAddMethods=Mock())
        import types
        parent = types.ModuleType("webview")
        platforms = types.ModuleType("webview.platforms")
        parent.platforms = platforms
        platforms.cocoa = cocoa
        modules = dict(Foundation=foundation, objc=objc)
        modules.update((("webview", parent), ("webview.platforms", platforms), ("webview.platforms.cocoa", cocoa)))
        with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, modules):
            gate._secure_macos(Path(directory))
        self.assertIs(host, cocoa.BrowserView.WebKitHost)
        self.assertIs(isolated, cocoa.WebKit.WKWebsiteDataStore.defaultDataStore())
        objc.classAddMethods.assert_called_once()
        self.assertIs(host, objc.classAddMethods.call_args.args[0])
        encoded = native_store.compileContentRuleListForIdentifier_encodedContentRuleList_completionHandler_.call_args.args[1]
        self.assertTrue(all(item.get("action").get("type") == "block" for item in json.loads(encoded)))

    def test_windows_native_filter_installs_before_mapping_and_navigation(self):
        from sjtu_learning_assistant.frontend_startup import UI_HOST
        order = list()
        core = SimpleNamespace(
            NavigationCompleted=Event(), WebResourceRequested=Event(),
            Environment=SimpleNamespace(CreateWebResourceResponse=Mock(return_value="blocked")),
            SetVirtualHostNameToFolderMapping=lambda *args: order.append("mapping"),
            Navigate=lambda url: order.append("navigate"),
            AddWebResourceRequestedFilter=lambda *args: order.append("filter"),
        )
        session = SimpleNamespace(network_blocked=False, fail=Mock())
        native = SimpleNamespace(webview=SimpleNamespace(CoreWebView2=core), Invoke=lambda action: action())
        modules = dict(System=SimpleNamespace(Action=lambda action: action))
        modules.update((("Microsoft.Web.WebView2.Core", SimpleNamespace(
            CoreWebView2WebResourceContext=SimpleNamespace(All="all"),
            CoreWebView2HostResourceAccessKind=SimpleNamespace(DenyCors="deny"))),))
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.html"
            index.write_text("<html></html>")
            loader = gate._OfflineWindowsLoader(index, session)
            window = SimpleNamespace(native=native, events=SimpleNamespace(loaded=Event()))
            loader.attach(window)
            with patch.dict(sys.modules, modules):
                loader._loaded()
        self.assertEqual(("filter", "mapping", "navigate"), tuple(order))
        session.fail.assert_not_called()
        self.assertTrue(session.network_blocked)
        handler = next(iter(core.WebResourceRequested.items))
        remote = SimpleNamespace(Request=SimpleNamespace(Uri="https://example.invalid/provider"), Response=None)
        handler(core, remote)
        self.assertEqual("blocked", remote.Response)
        local = SimpleNamespace(Request=SimpleNamespace(Uri="https://" + UI_HOST + "/index.html"), Response=None)
        handler(core, local)
        self.assertIsNone(local.Response)

    def test_watchdog_bounds_native_stall_and_writes_failed_status(self):
        session = gate._Session(gate.RejectBridge("nonce"), "a".ljust(64, "a"), Path("unused.json"))
        session.webview = SimpleNamespace(renderer="wkwebview")
        session.closed = Mock()
        session.closed.wait.return_value = False
        with patch.object(sys, "platform", "darwin"), patch.object(gate, "_write_report") as write, patch.object(os, "_exit") as exit:
            session.watchdog()
        self.assertEqual("failed", session.result)
        self.assertEqual("timeout", session.reason)
        write.assert_called_once()
        exit.assert_called_once_with(2)
        self.assertLessEqual(gate.HARD_TIMEOUT_SECONDS, 90)

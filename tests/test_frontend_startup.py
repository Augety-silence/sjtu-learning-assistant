from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from sjtu_learning_assistant.frontend_startup import (
    FrontendResourceError, UI_HOST, WindowsFrontendLoader, frontend_url, validate_frontend,
)


class Event:
    def __init__(self): self.handlers = []
    def __iadd__(self, handler): self.handlers.append(handler); return self
    def fire(self, *args):
        for handler in self.handlers: handler(*args)


class FrontendStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def make_index(self, directory=None):
        folder = directory or self.root
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'assets').mkdir(exist_ok=True)
        (folder / 'assets' / 'entry.js').write_text('console.log("test")')
        index = folder / 'index.html'
        index.write_text('<html><script src="./assets/entry.js?build=1"></script></html>')
        return index

    def test_supported_special_paths_do_not_appear_in_url(self):
        for name in ('normal', 'SJTU Learning Assistant', '中文 资料', 'percent%20', 'hash#目录'):
            with self.subTest(name=name):
                index = self.make_index(self.root / name)
                loader = WindowsFrontendLoader(index)
                self.assertTrue(loader.url.startswith(f'https://{UI_HOST}/index.html?build='))
                self.assertNotIn('file:', loader.url)
                self.assertNotIn(name, loader.url)

    def test_missing_entry_is_clear_error(self):
        with self.assertRaisesRegex(FrontendResourceError, '缺少界面入口'):
            validate_frontend(self.root / 'index.html')

    def test_missing_direct_asset_is_clear_error(self):
        index = self.make_index()
        (self.root / 'assets' / 'entry.js').unlink()
        with self.assertRaisesRegex(FrontendResourceError, 'entry.js'):
            validate_frontend(index)

    def test_percent_encoded_asset_decodes_once(self):
        index = self.make_index()
        (self.root / 'literal%20.js').write_text('test')
        index.write_text('<script src="literal%2520.js"></script>')
        self.assertEqual(16, len(validate_frontend(index)))

    def test_root_relative_assets_and_hash_are_separate(self):
        index = self.make_index()
        index.write_text('<script src="/assets/entry.js#fragment"></script>')
        self.assertEqual(16, len(validate_frontend(index)))

    def test_outside_reference_is_rejected(self):
        index = self.make_index()
        index.write_text('<script src="../outside.js"></script>')
        with self.assertRaisesRegex(FrontendResourceError, '超出'):
            validate_frontend(index)

    def test_encoded_outside_reference_is_rejected(self):
        index = self.make_index()
        index.write_text('<script src="%2e%2e/outside.js"></script>')
        with self.assertRaisesRegex(FrontendResourceError, '超出'):
            validate_frontend(index)

    def test_invalid_fingerprint_rejected(self):
        with self.assertRaises(ValueError): frontend_url('bad#path')

    def fake_window(self, core):
        window = SimpleNamespace(events=SimpleNamespace(loaded=Event()), html=[], native=SimpleNamespace())
        window.load_html = window.html.append
        window.native.webview = SimpleNamespace(CoreWebView2=core)
        window.native.Invoke = lambda action: action()
        return window

    def test_mapping_precedes_navigation_and_only_runs_once(self):
        index = self.make_index()
        calls = []
        core = SimpleNamespace(NavigationCompleted=Event())
        core.SetVirtualHostNameToFolderMapping = lambda *args: calls.append(('map', args))
        core.Navigate = lambda url: calls.append(('navigate', url))
        window = self.fake_window(core)
        loader = WindowsFrontendLoader(index)
        loader.attach(window)
        modules = {
            'System': SimpleNamespace(Action=lambda fn: fn),
            'Microsoft.Web.WebView2.Core': SimpleNamespace(
                CoreWebView2HostResourceAccessKind=SimpleNamespace(DenyCors='deny-cors'),
            ),
        }
        with patch.dict('sys.modules', modules):
            window.events.loaded.fire()
            window.events.loaded.fire()
        self.assertEqual(['map', 'navigate'], [call[0] for call in calls])
        self.assertEqual((UI_HOST, str(index.parent.resolve()), 'deny-cors'), calls[0][1])
        self.assertEqual(loader.url, calls[1][1])
        core.NavigationCompleted.fire(core, SimpleNamespace(IsSuccess=True))
        self.assertEqual('loaded', loader._state)

    def test_navigation_failure_surfaces_specific_status(self):
        index = self.make_index()
        reports = []
        loader = WindowsFrontendLoader(index, report=reports.append)
        loader._window = self.fake_window(SimpleNamespace())
        loader._state = 'navigating'
        loader._navigation_completed(None, SimpleNamespace(IsSuccess=False, WebErrorStatus='FileNotFound'))
        self.assertEqual('failed', loader._state)
        self.assertIn('FileNotFound', reports[0])
        self.assertNotIn(str(self.root), reports[0])

    def test_mapping_failure_never_navigates_or_leaks_exception(self):
        index = self.make_index()
        reports = []
        calls = []
        core = SimpleNamespace(NavigationCompleted=Event())
        def mapping(*_args): raise RuntimeError('secret/user/path')
        core.SetVirtualHostNameToFolderMapping = mapping
        core.Navigate = calls.append
        window = self.fake_window(core)
        loader = WindowsFrontendLoader(index, report=reports.append)
        loader.attach(window)
        with patch.dict('sys.modules', {
            'System': SimpleNamespace(Action=lambda fn: fn),
            'Microsoft.Web.WebView2.Core': SimpleNamespace(CoreWebView2HostResourceAccessKind=SimpleNamespace(DenyCors='deny')),
        }):
            window.events.loaded.fire()
        self.assertEqual([], calls)
        self.assertNotIn('secret/user/path', reports[0])


if __name__ == '__main__': unittest.main()

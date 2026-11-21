import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import desktop_app


class BackgroundLifecycleTests(unittest.TestCase):
    def test_close_hides_reopen_returns_and_app_quit_is_inherited(self):
        class WindowDelegate:
            pass
        class AppDelegate:
            def applicationShouldTerminate_(self, app):
                return True
        instance = SimpleNamespace(webview=Mock(), show=Mock())
        browser = SimpleNamespace(WindowDelegate=WindowDelegate, AppDelegate=AppDelegate,
            get_instance=Mock(return_value=instance), instances={"master": instance})
        fake = SimpleNamespace(BrowserView=browser)
        with patch.object(sys,"platform","darwin"), patch.object(desktop_app,"_MACOS_BACKGROUND_INSTALLED",False), patch.dict(sys.modules,{"webview.platforms.cocoa":fake}):
            self.assertTrue(desktop_app._install_macos_background_lifecycle())
            native = Mock()
            self.assertFalse(browser.WindowDelegate().windowShouldClose_(native))
            native.orderOut_.assert_called_once_with(None)
            app = browser.AppDelegate()
            self.assertTrue(app.applicationShouldTerminate_(None))
            self.assertTrue(app.applicationShouldHandleReopen_hasVisibleWindows_(None,False))
            instance.show.assert_called_once()
            delegate = browser.WindowDelegate
            self.assertTrue(desktop_app._install_macos_background_lifecycle())
            self.assertIs(delegate,browser.WindowDelegate)

    def test_nonmac_does_not_patch_window_or_block_quit(self):
        with patch.object(sys,"platform","win32"):
            self.assertFalse(desktop_app._install_macos_background_lifecycle())

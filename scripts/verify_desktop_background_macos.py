#!/usr/bin/env python3
"""原生Mac关窗、后台调度、Dock重开、明确退出隔离验收；不读学习数据。"""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import webview
from PyObjCTools import AppHelper
from webview.platforms.cocoa import BrowserView
from desktop_app import DesktopScheduler, _install_macos_background_lifecycle


def main():
    assert _install_macos_background_lifecycle()
    count = []
    scheduler = DesktopScheduler(lambda: count.append(1), interval_seconds=0.1)
    window = webview.create_window("SJTU后台隔离验收", html="<p>合成页面</p>")
    def native_call(function):
        event = threading.Event()
        result = []
        def run():
            try:
                result.append(function())
            finally:
                event.set()
        AppHelper.callAfter(run)
        assert event.wait(5), "native call timeout"
        return result[0] if result else None
    def check():
        try:
            assert window.events.loaded.wait(10)
            scheduler.start()
            instance = BrowserView.instances[window.uid]
            native_call(lambda: instance.window.performClose_(None))
            assert not native_call(lambda: instance.window.isVisible())
            before = len(count)
            threading.Event().wait(0.5)
            assert len(count) > before, "隐藏后计划任务停止"
            native_call(lambda: BrowserView.app.delegate().applicationShouldHandleReopen_hasVisibleWindows_(BrowserView.app, False))
            threading.Event().wait(0.3)
            assert native_call(lambda: instance.window.isVisible())
            assert native_call(lambda: BrowserView.app.delegate().applicationShouldTerminate_(BrowserView.app))
            print(json.dumps(dict(ok=True, native="Cocoa", hidden_on_close=True,
                scheduler_continues=True, reopen=True, explicit_quit_allowed=True)), flush=True)
            scheduler.stop()
            AppHelper.callAfter(BrowserView.app.terminate_, None)
        except Exception as exc:
            print(json.dumps(dict(ok=False,error=str(exc))), flush=True)
            scheduler.stop()
            AppHelper.callAfter(BrowserView.app.stop_, None)
    webview.start(check, gui="cocoa")
    return 1  # 成功的明确退出由NSApplication返回进程状态0。


if __name__ == "__main__":
    raise SystemExit(main())

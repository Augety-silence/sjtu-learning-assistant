#!/usr/bin/env python3
"""原生 WKWebView 合成进度验收；不访问学习库、凭据或学校 API。"""
from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path

import webview


class SyntheticBridge:
    def __init__(self):
        self.calls = 0

    def invoke(self, action, payload=None):
        if action == "ai_chat_sessions":
            return dict(ok=True, data=dict(items=[]))
        if action == "markdown_sync_progress":
            self.calls += 1
            def group(total, completed, ai):
                return dict(total=total, completed=completed, pending=total-completed, failed=0,
                    ai_completed=ai, ai_failed=2, issues=[dict(name="第一堂课的合成课程文件.pdf", code="timeout")], truncated=False)
            return dict(ok=True, data=dict(courses=group(93,93,10), transcripts=group(18,18,0),
                automatic=True, ai_ready=True, ai_model="deepseek-reasoner", updated_at="2026-11-22T00:00:00Z",
                current=dict(status="running", phase="ai_summary", current_file="第一堂课的合成课程文件.pdf")))
        return dict(ok=False, error=dict(message="隔离验收不执行其他操作"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    bridge = SyntheticBridge()
    window = webview.create_window("同步进度合成验收", args.html.resolve().as_uri(), js_api=bridge,
        width=1440, height=820, text_select=True)
    outcome = dict(ok=False)

    def wait(expression):
        deadline = time.monotonic() + 15
        while not window.evaluate_js(expression):
            assert time.monotonic() < deadline, "合成页面加载超时"
            threading.Event().wait(0.1)

    def capture(name):
        if args.screenshots is None:
            return
        from AppKit import NSBitmapImageRep, NSJPEGFileType, NSImageCompressionFactor
        from PyObjCTools import AppHelper
        from webview.platforms.cocoa import BrowserView
        event = threading.Event()
        result = []
        def snapshot():
            instance = BrowserView.instances[window.uid]
            def done(image, error):
                try:
                    if error is not None or image is None:
                        raise RuntimeError("native snapshot failed")
                    bitmap = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                    data = bitmap.representationUsingType_properties_(NSJPEGFileType, {NSImageCompressionFactor: 0.65})
                    path = args.screenshots / (name + ".jpg")
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    assert data.writeToFile_atomically_(str(path), True)
                    result.append(True)
                finally:
                    event.set()
            instance.webview.takeSnapshotWithConfiguration_completionHandler_(None, done)
        AppHelper.callAfter(snapshot)
        assert event.wait(10) and result, "snapshot timeout"

    def check():
        try:
            wait("Array.from(document.querySelectorAll('button')).some(b=>b.innerText==='同步进度')")
            assert bridge.calls == 0
            layouts = []
            for width in (1440, 760, 390):
                window.resize(width, 820)
                threading.Event().wait(0.3)
                window.evaluate_js("Array.from(document.querySelectorAll('button')).find(b=>b.innerText==='同步进度').click()")
                wait("document.querySelector('.markdown-progress-window')?.innerText.includes('93 / 93')")
                wait("document.querySelector('.markdown-progress-current')?.innerText.startsWith('正在整理')")
                layout = window.evaluate_js("""(() => {
                  const panel=document.querySelector('.markdown-progress-window');
                  const r=panel.getBoundingClientRect();
                  const heading=document.querySelector('.page-heading h1');
                  const hr=heading.getBoundingClientRect();
                  return {width:innerWidth, left:r.left, right:r.right, bottom:r.bottom, height:innerHeight,
                    titleHeight:hr.height, titleLineHeight:parseFloat(getComputedStyle(heading).lineHeight),
                    complete:panel.innerText.includes('93 / 93')&&panel.innerText.includes('18 / 18'),
                    separate:panel.innerText.includes('10 / 93')&&panel.innerText.includes('0 / 18'),
                    selectable:getComputedStyle(panel).userSelect!=='none',
                    scrollable:panel.scrollWidth<=panel.clientWidth+1};
                })()""")
                assert layout["complete"] and layout["separate"] and layout["scrollable"]
                assert layout["titleHeight"] <= layout["titleLineHeight"] + 1, layout
                assert layout["left"] >= 10 and layout["right"] <= layout["width"]-10 and layout["bottom"] <= layout["height"]-8, layout
                capture("desktop" if width==1440 else "compact" if width==760 else "mobile")
                window.evaluate_js("document.querySelector('[aria-label=关闭同步进度]').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
                wait("!document.querySelector('.markdown-progress-window')")
                assert window.evaluate_js("document.activeElement.innerText==='同步进度'")
                layouts.append(layout)
            before = bridge.calls
            threading.Event().wait(5.2)
            assert before == bridge.calls
            outcome.update(ok=True, native="WKWebView", layouts=layouts, on_demand=True,
                escape_restores_focus=True, closed_stops_polling=True, inference_calls=0)
        except Exception as exc:
            outcome.update(error_type=type(exc).__name__, error=str(exc))
        finally:
            window.destroy()
    webview.start(check, gui="cocoa")
    print(json.dumps(outcome, ensure_ascii=False))
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

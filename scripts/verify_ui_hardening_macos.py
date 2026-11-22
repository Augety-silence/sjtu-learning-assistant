"""原生 WKWebView：提示、短长邮件、模糊和保留播放器的合成验收。

不访问学习数据库、API 密钥或学校服务。
"""
import argparse
import json
import threading
import time
from pathlib import Path

import webview


class Bridge:
    def __init__(self, media):
        self.media_path = media

    def media(self):
        return self.media_path.as_uri()

    def invoke(self, action, payload=None):
        if action != "message_detail":
            return dict(ok=False, error=dict(message="隔离验收不执行其他操作"))
        long = (payload or {}).get("source_id") == "long"
        return dict(ok=True, data=dict(title="长邮件" if long else "短邮件", source_label="合成消息",
            occurred_at=None, body=("当堂课程通知。\n" * 500 if long else "明天下午课程照常进行。"),
            body_html=None, format="text", pending_body_sync=False, attachments=[], resources=[],
            url=None, is_unread=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    parser.add_argument("media", type=Path)
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    window = webview.create_window("SJTU界面隔离验收", args.html.resolve().as_uri(),
        js_api=Bridge(args.media.resolve()), width=1280, height=820, text_select=True)
    outcome = dict(ok=False)

    def wait(expression):
        deadline = time.monotonic() + 15
        while not window.evaluate_js(expression):
            assert time.monotonic() < deadline, "页面等待超时: " + expression
            threading.Event().wait(.1)

    def click(label):
        window.evaluate_js("Array.from(document.querySelectorAll('button')).find(b=>b.textContent===" + json.dumps(label) + ").click()")

    def capture(name):
        if not args.screenshots:
            return
        from AppKit import NSBitmapImageRep, NSJPEGFileType, NSImageCompressionFactor
        from PyObjCTools import AppHelper
        from webview.platforms.cocoa import BrowserView
        event = threading.Event()
        def snapshot():
            def done(image, error):
                if image is not None and error is None:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    bitmap = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                    data = bitmap.representationUsingType_properties_(NSJPEGFileType, {NSImageCompressionFactor: .7})
                    data.writeToFile_atomically_(str(args.screenshots / (name + ".jpg")), True)
                event.set()
            BrowserView.instances[window.uid].webview.takeSnapshotWithConfiguration_completionHandler_(None, done)
        AppHelper.callAfter(snapshot)
        assert event.wait(10), "截图超时"

    def check():
        try:
            wait("Boolean(document.querySelector('[aria-label=边界说明]'))")
            layouts = []
            for width, height, theme in ((1280, 820, "light"), (760, 620, "dark"), (390, 600, "light")):
                window.resize(width, height)
                window.evaluate_js("document.documentElement.dataset.theme=" + json.dumps(theme))
                threading.Event().wait(.3)
                window.evaluate_js("document.querySelector('[aria-label=边界说明]').focus()")
                wait("Boolean(document.querySelector('.info-tooltip-bubble:not([hidden])'))")
                tip = window.evaluate_js("""(() => {const t=document.querySelector('.info-tooltip-bubble');const r=t.getBoundingClientRect();const s=getComputedStyle(t);return {inside:r.left>=11&&r.right<=innerWidth-11&&r.top>=11&&r.bottom<=innerHeight-11, global:t.parentElement===document.body, white:s.color==='rgb(255, 255, 255)', blur:s.backdropFilter||s.webkitBackdropFilter};})()""")
                assert tip["inside"] and tip["global"] and tip["white"] and "blur" in tip["blur"], tip
                capture(f"tooltip-{width}-{theme}")
                window.evaluate_js("document.querySelector('[aria-label=边界说明]').blur()")
                for label in ("短邮件", "长邮件"):
                    click(label)
                    wait("Boolean(document.querySelector('.message-detail-body'))")
                    threading.Event().wait(.4)
                    layout = window.evaluate_js("""(() => {const d=document.querySelector('.message-dialog');const r=d.getBoundingClientRect();const stack=d.querySelector('.message-content-stack');return {inside:r.top>=11&&r.bottom<=innerHeight-11&&r.left>=0&&r.right<=innerWidth, centered:Math.abs((r.top+r.bottom)/2-innerHeight/2)<2,height:r.height,scroll:stack.scrollHeight>stack.clientHeight+10, width:innerWidth};})()""")
                    assert layout["inside"] and layout["centered"], layout
                    if label == "长邮件":
                        assert layout["scroll"], layout
                    else:
                        assert not layout["scroll"] and layout["height"] < height * .8, layout
                    capture(f"mail-{'short' if label=='短邮件' else 'long'}-{width}-{theme}")
                    layouts.append(layout)
                    window.evaluate_js("document.querySelector('[aria-label=关闭消息详情]').click()")
                    wait("!document.querySelector('.message-dialog')")
            window.resize(1280, 820)
            click("个性化设置")
            wait("Boolean(document.querySelector('.ai-personalization-panel'))")
            blur = window.evaluate_js("getComputedStyle(document.querySelector('.ai-personalization-panel')).webkitBackdropFilter || getComputedStyle(document.querySelector('.ai-personalization-panel')).backdropFilter")
            assert "blur" in blur, blur
            capture("personalization")
            window.evaluate_js("document.querySelector('[aria-label=关闭个性化设置]').click()")
            window.evaluate_js("document.querySelector('[aria-label=\"播放 缓存验收\"]').click()")
            wait("Boolean(document.querySelector('video')?.readyState >= 1)")
            window.evaluate_js("window.__cachedVideo=document.querySelector('video'); window.__cachedVideo.pause(); window.__cachedVideo.currentTime=2")
            wait("document.querySelector('video').currentTime>=1.9")
            click("切换页面")
            wait("!document.querySelector('video')")
            assert window.evaluate_js("window.__cachedVideo.paused")
            click("切换页面")
            wait("Boolean(document.querySelector('video'))")
            assert window.evaluate_js("document.querySelector('video')===window.__cachedVideo && window.__cachedVideo.currentTime>=1.9 && document.documentElement.dataset.playRequests==='1'")
            capture("retained-video")
            outcome.update(ok=True, native="WKWebView", tooltip_clear=True, mail_layouts=layouts,
                personalization_blur=blur, player_same_dom=True, playback_requests=1, paused_on_leave=True, inference_calls=0)
        except Exception as exc:
            outcome.update(error=str(exc), error_type=type(exc).__name__)
        finally:
            window.destroy()
    webview.start(check, gui="cocoa")
    print(json.dumps(outcome, ensure_ascii=False))
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

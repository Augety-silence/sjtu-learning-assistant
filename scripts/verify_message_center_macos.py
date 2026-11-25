"""使用真实 WKWebView 验证消息阅读、搜索及 AI 本地引用；仅合成夹具。"""
import argparse
import json
import time
from pathlib import Path
import webview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    args = parser.parse_args()
    window = webview.create_window("消息中心隔离验收", args.html.resolve().as_uri() + "#/messages", width=1280, height=820, text_select=True)
    result = {"ok": False, "cases": []}
    def wait(expression):
        deadline = time.monotonic() + 20
        while not window.evaluate_js(expression):
            if time.monotonic() > deadline:
                raise AssertionError("原生功能等待超时")
            time.sleep(.1)
    def check():
        try:
            wait("document.querySelectorAll('.message-open').length === 3 && !!document.querySelector('.message-reader [role=document]')")
            result["cases"].append("初始消息与完整正文")
            window.evaluate_js("document.querySelectorAll('.message-open')[1].click()")
            wait("document.querySelector('.message-reader h3').textContent === '论文阅读清单已更新' && !!document.querySelector('.message-reader [role=document]')")
            result["cases"].append("点击正确消息详情")
            window.evaluate_js("document.querySelector('.message-reader-back').click()")
            wait("!document.querySelector('.message-workspace-reading')")
            result["cases"].append("返回消息列表")
            window.evaluate_js("window.dispatchEvent(new CustomEvent('sjtu-open-message', {detail: '#/messages?kind=email&ref=email:101'}))")
            wait("document.querySelector('.message-reader h3').textContent === '历史消息引用定位'")
            result["cases"].append("AI引用定位分页外消息")
            result["ok"] = True
        except Exception as exc:
            result["error"] = type(exc).__name__ + ': ' + str(exc)
        finally:
            print(json.dumps(result, ensure_ascii=False), flush=True)
            window.destroy()
    webview.start(check, gui="cocoa")
    if not result["ok"]:
        raise SystemExit(1)

if __name__ == '__main__':
    main()

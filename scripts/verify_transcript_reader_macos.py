#!/usr/bin/env python3
"""在原生 macOS WKWebView 中检查合成字幕页签与文本层叠，无真实数据。"""
from __future__ import annotations

import argparse
import json
import threading
import time
import traceback
from pathlib import Path

import webview


class SyntheticBridge:
    def invoke(self, action, payload=None):
        payload = payload or {}
        if action == "provider_usage_summary":
            metrics = {key: dict(tokens=tokens, reported_calls=1, missing_calls=0) for key,tokens in (("input",1000),("output",20),("cache",800),("total",1020))}
            return dict(ok=True, data=dict(periods={key:dict(calls=1,metrics=metrics) for key in ("today","week","all")}, scope="synthetic", timezone="CST", first_recorded_at=None))
        if action == "transcript_artifacts":
            items = [dict(id="a" * 32 + ":" + kind, kind=kind, label=kind,
                content_type="text/markdown") for kind in ("lesson_summary", "original")]
            return dict(ok=True, data=dict(items=items))
        if action == "transcript_artifact_read":
            artifact_id = payload.get("artifact_id", "")
            content = "# 原始字幕\n\n## 00:00:05\n\n    老师先讲机会成本，再讲决策条件。" if artifact_id.endswith(":original") else "# 当堂老师讲了什么\n\n老师先介绍机会成本，再说明相关成本的适用条件。"
            return dict(ok=True, data=dict(id=artifact_id, content=content))
        return dict(ok=False, error=dict(message="隔离验收不执行其他操作"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    args = parser.parse_args()
    path = args.html.resolve(strict=True)
    window = webview.create_window("字幕界面隔离验收", path.as_uri(),
        js_api=SyntheticBridge(), width=1050, height=760, text_select=True)
    outcome = dict(ok=False)

    def check():
        try:
            deadline = time.monotonic() + 15
            while not window.evaluate_js("Boolean(document.querySelector('main > button'))"):
                if time.monotonic() > deadline:
                    raise RuntimeError("合成页面加载超时")
                threading.Event().wait(0.1)
            def check_video_layout():
                return window.evaluate_js("""(() => {
                  const panel=document.querySelector('.video-list-panel');
                  const header=panel.querySelector('.video-list-header').getBoundingClientRect();
                  const list=panel.querySelector('.video-recording-list');
                  const first=list.querySelector('li strong').getBoundingClientRect();
                  const tabs=document.querySelector('.video-learning-tabs');
                  const toolbar=document.querySelector('.video-course-toolbar');
                  const heading=toolbar.querySelector('.video-course-heading').getBoundingClientRect();
                  const source=toolbar.querySelector('[role=tablist]').getBoundingClientRect();
                  return {rows:list.children.length, clear:first.top>=header.bottom,
                    toolbarInline:Math.abs(heading.top-source.top)<10 && heading.right<=source.left+1,
                    sourceNoCover:!toolbar.querySelector('[data-selection-indicator]'),
                    toolbarWithin:source.right<=toolbar.getBoundingClientRect().right+1,
                    noNoise:!document.querySelector('.video-status-filters,.video-jobs,.video-batch-bar'),
                    tabs:tabs.querySelectorAll('[role=tab]').length,
                    noCover:!tabs.querySelector('[data-selection-indicator]'),
                    retry:document.querySelector('.video-material-toolbar').innerText.includes('重试同步'),
                    within:Array.from(tabs.querySelectorAll('[role=tab]')).every(t=>t.getBoundingClientRect().right<=tabs.getBoundingClientRect().right+1)};
                })()""")
            for width in (1050, 760):
                window.resize(width, 760)
                threading.Event().wait(0.3)
                layout=check_video_layout()
                assert layout["rows"]==18 and layout["clear"] and layout["noNoise"] and layout["tabs"]==3 and layout["noCover"] and layout["retry"] and layout["within"] and layout["toolbarInline"] and layout["sourceNoCover"] and layout["toolbarWithin"], layout
                meter=window.evaluate_js("document.querySelector('.token-usage-panel').innerText")
                assert "1,020" in meter and "800" in meter, meter
            window.resize(1050,760)
            window.evaluate_js("document.querySelector('main > button').click()")
            while not window.evaluate_js("Boolean(document.querySelector('.transcript-markdown'))"):
                if time.monotonic() > deadline:
                    raise RuntimeError("字幕正文加载超时")
                threading.Event().wait(0.1)
            data = window.evaluate_js("""(() => {
              const dialog = document.querySelector('[role=dialog]');
              return {text: dialog.innerText, tabs: dialog.querySelectorAll('[role=tab]').length,
                layers: Array.from(document.querySelectorAll('.tabs-sliding-group')).every(g =>
                  getComputedStyle(g.querySelector('[data-selection-indicator]')).zIndex === '0' &&
                  Array.from(g.querySelectorAll('[role=tab]')).every(t => getComputedStyle(t).zIndex === '1'))};
            })()""")
            assert data["tabs"] == 2 and data["layers"]
            assert "机会成本" in data["text"] and "校对与历史材料" not in data["text"]
            window.evaluate_js("document.querySelector('[role=dialog] [role=tab]:last-child').dispatchEvent(new MouseEvent('mousedown',{bubbles:true,button:0}))")
            threading.Event().wait(0.5)
            original = window.evaluate_js("document.querySelector('[role=dialog]').innerText")
            assert "00:00:05" in original and "老师先讲机会成本" in original
            geometry = window.evaluate_js("""(() => {
              const g = document.querySelector('[role=dialog] .tabs-sliding-group');
              const tab = g.querySelector('[data-state=active]').getBoundingClientRect();
              const bg = g.querySelector('[data-selection-indicator]').getBoundingClientRect();
              return Math.abs(tab.x-bg.x)<2 && Math.abs(tab.y-bg.y)<2 && Math.abs(tab.width-bg.width)<2;
            })()""")
            assert geometry
            outcome.update(ok=True, native="WKWebView", tabs=2, layers=True, original_readable=True, geometry=True, video_rows=18, video_tabs=3, video_layout_sizes=[1050,760], no_video_cover=True, toolbar_inline=True, usage_dashboard=True)
        except Exception as exc:
            outcome.update(error_type=type(exc).__name__, error=str(exc),
                traceback=traceback.format_exc(),
                synthetic_page=window.evaluate_js("document.body.innerText.slice(0,500)"))
        finally:
            window.destroy()

    webview.start(check, gui="cocoa")
    print(json.dumps(outcome, ensure_ascii=False))
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

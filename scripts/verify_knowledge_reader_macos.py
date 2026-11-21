#!/usr/bin/env python3
"""在原生WKWebView中核验合成知识目录／阅读工具栏，不接触真实学习数据。"""
from __future__ import annotations

import argparse
import json
import threading
import time
import traceback
from pathlib import Path

import webview


class SyntheticBridge:
    """WKWebView注入Bridge时仍返回同一隔离数据，绝不连生产服务。"""
    def invoke(self, action, payload=None):
        payload = payload or {}
        docs = [dict(id=str(i + 1) * 32, source_id="automatic-courses" if i < 3 else "automatic-transcripts",
            source_name="合成资料", relative_path=f"秋/数据库/第{i+1}讲.md", title=f"第{i+1}讲：数据库资料.md",
            folder="秋/数据库", status="completed", contract="course-summary-v1") for i in range(6)]
        if action == "settings_status":
            data = dict(ai_enabled=True, ai_key_saved=True)
        elif action == "ai_chat_sessions":
            data = dict(items=[])
        elif action == "knowledge_compiler_status":
            data = dict(status="idle", task_id=None, mode=None, phase_index=0, total_phases=0,
                current_phase=None, counts=dict(markdown_files=0, processed_files=0, generated_notes=0, warnings=0), events=[], warnings=[], error=None)
        elif action == "course_markdown_status":
            data = dict(enabled=True, status="completed", output_root="/synthetic", total=6, completed=6, pending=0, failed=0, issues=[], calls_today=0)
        elif action == "compiled_knowledge_sources":
            data = dict(items=[dict(id=s, name=s, kind="automatic", root="/synthetic", count=3)
                for s in ("automatic-courses", "automatic-transcripts")], count=6, issues=[], truncated=False)
        elif action == "compiled_knowledge_search":
            data = dict(items=docs, count=6, next_offset=None, issues=[], truncated=False, folders=[
                dict(source_id="automatic-learning:fixture", path=p, name=p.split("/")[-1], count=6) for p in ("秋", "秋/数据库")])
        elif action == "compiled_knowledge_read":
            doc = next(d for d in docs if d["id"] == payload["document_id"])
            number = docs.index(doc) + 1
            text = "# " + doc["title"] + "\n\n" + "\n".join(f"## 知识点{i+1}\n\n第{number}份正文：关系代数、查询条件及老师当堂补充。\n" for i in range(60))
            data = dict(doc, text=text, total_chars=len(text), offset=0, next_offset=None, section="文档开头", citation=doc["relative_path"], content_sha256=doc["id"], truncated=False)
        elif action == "compiled_knowledge_open":
            self.revealed = payload["source_id"]
            data = dict(status="opened")
        else:
            return dict(ok=False, error=dict(code="unknown_action", message="隔离验收不执行其他操作"))
        return dict(ok=True, data=data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    args = parser.parse_args()
    path = args.html.resolve(strict=True)
    bridge = SyntheticBridge()
    window = webview.create_window("知识目录隔离验收", path.as_uri(), js_api=bridge, width=1440, height=900, text_select=True)
    outcome = {"ok": False}

    def wait(expression):
        deadline = time.monotonic() + 15
        while not window.evaluate_js(expression):
            if time.monotonic() > deadline:
                raise RuntimeError("合成知识界面加载超时")
            threading.Event().wait(0.1)

    def check():
        try:
            wait("Boolean(document.querySelector('.knowledge-folder-tree button'))")
            root = window.evaluate_js("""(() => {
              const l=document.querySelector('.compiled-knowledge-layout');
              const n=document.querySelector('.compiled-knowledge-navigation');
              return {noReader:!document.querySelector('.compiled-knowledge-reading-pane'),
                width:Math.abs(l.getBoundingClientRect().width-n.getBoundingClientRect().width)<2,
                noPlaceholder:!document.querySelector('.compiled-knowledge-welcome')};
            })()""")
            assert all(root.values()), root
            nav = window.evaluate_js("Array.from(document.querySelectorAll('.sidebar nav button')).map(b=>b.getAttribute('aria-label')).slice(-4)")
            assert nav == ["交大云盘", "课程知识库", "设置", "资料项目"], nav
            assert window.evaluate_js("!document.querySelector('.page-heading p,.sidebar-footer')")
            window.evaluate_js("document.querySelector('.knowledge-folder-tree button').click()")
            wait("Boolean(document.querySelector('.rich-preview-markdown p'))")
            readings = []
            for width in (1440, 1050, 760, 375):
                window.resize(width, 760)
                threading.Event().wait(0.3)
                data = window.evaluate_js("""(() => {
                  const t=document.querySelector('.compiled-knowledge-reader-actions');
                  const r=document.querySelector('.compiled-knowledge-reader');
                  const icon=t.querySelector('.compiled-knowledge-navigation-toggle svg');
                  const tip=t.querySelector('[role=tooltip]');
                  return {width:r.clientWidth, files:document.querySelectorAll('.compiled-knowledge-item').length,
                    noOverflow:t.scrollWidth<=t.clientWidth+1,
                    noInlineCitation:!r.innerText.includes('来源：'),
                    hidden:getComputedStyle(tip).display==='none',
                    small:icon.getBoundingClientRect().width===16 && getComputedStyle(icon).strokeWidth==='1.5px'};
                })()""")
                assert data["files"] == 6 and data["noOverflow"] and data["noInlineCitation"] and data["hidden"] and data["small"], data
                assert data["width"] > (500 if width == 1440 else 300 if width >= 760 else 200), data
                window.evaluate_js("document.querySelector('.page-heading .info-tooltip button').focus()")
                assert window.evaluate_js("(() => { const r=document.querySelector('.page-heading [role=tooltip]').getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth+1; })()")
                window.evaluate_js("document.querySelector('.page-heading .info-tooltip button').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})); document.activeElement.blur()")
                readings.append(data)
            window.resize(1440, 900)
            for i in range(6):
                window.evaluate_js(f"document.querySelectorAll('.compiled-knowledge-item')[{i}].click()")
                wait(f"document.querySelector('.rich-preview-markdown').innerText.includes('第{i+1}份正文')")
            window.evaluate_js("document.querySelector('.compiled-knowledge-document-info button').focus()")
            assert window.evaluate_js("getComputedStyle(document.querySelector('.compiled-knowledge-document-info [role=tooltip]')).display==='block'")
            window.evaluate_js("document.querySelector('.compiled-knowledge-document-info button').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
            assert window.evaluate_js("getComputedStyle(document.querySelector('.compiled-knowledge-document-info [role=tooltip]')).display==='none'")
            window.evaluate_js("Array.from(document.querySelectorAll('[role=toolbar] button')).find(b=>b.innerText==='在本地显示').click()")
            deadline = time.monotonic() + 5
            while getattr(bridge, "revealed", "") != "automatic-transcripts" and not window.evaluate_js("document.documentElement.dataset.revealed==='automatic-transcripts'"):
                assert time.monotonic() < deadline, "合成定位来源不正确"
                threading.Event().wait(0.1)
            window.evaluate_js("document.querySelector('.compiled-knowledge-navigation-toggle').click()")
            expanded = window.evaluate_js("document.querySelector('.compiled-knowledge-reader').clientWidth")
            assert expanded > 900, expanded
            window.evaluate_js("document.querySelector('.compiled-knowledge-navigation-toggle').click(); Array.from(document.querySelectorAll('button')).find(b=>b.innerText==='全部课程').click()")
            wait("!document.querySelector('.compiled-knowledge-reading-pane')")
            window.evaluate_js("document.querySelector('.knowledge-tools summary').click()")
            wait("document.querySelector('.knowledge-tools').open")
            assert window.evaluate_js("!document.querySelector('.knowledge-phase-strip')")
            assert window.evaluate_js("Array.from(document.querySelectorAll('.knowledge-mode-grid input')).some(i=>i.checked&&i.value==='documents')")
            assert window.evaluate_js("Array.from(document.querySelectorAll('.compiled-knowledge p:not(.sr-only)')).every(p=>!p.innerText.includes('找到')&&!p.innerText.includes('已加载')&&!p.innerText.includes('已在系统文件管理器'))")
            outcome.update(ok=True, root=root, readings=readings, navigation=nav, quiet_copy=True, all_six_read=True,
                tooltip_focus_and_escape=True, original_source_reveal=True, expanded_width=expanded)
        except Exception:
            outcome["error"] = traceback.format_exc()
            outcome["fixture"] = window.evaluate_js("({text:document.getElementById('root')?.innerText,api:Boolean(window.pywebview?.api?.invoke)})")
        finally:
            print(json.dumps(outcome, ensure_ascii=False), flush=True)
            window.destroy()

    webview.start(check, gui="cocoa")
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

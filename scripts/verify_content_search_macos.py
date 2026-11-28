"""隔离 WKWebView 搜索验收，不访问生产数据、MCP、日历或模型。"""
import argparse
import json
import threading
import time
import traceback
from pathlib import Path
import webview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    window = webview.create_window("文档与字幕搜索隔离验收", args.html.resolve().as_uri(), width=1100, height=820)
    report = dict(ok=False, native="WKWebView", checks=[], production_data_accesses=0)

    def wait(expression):
        deadline = time.monotonic() + 12
        while not window.evaluate_js(expression):
            if time.monotonic() > deadline:
                raise RuntimeError("等待搜索界面超时")
            threading.Event().wait(.1)

    def type_query(selector, text):
        window.evaluate_js(f"""(() => {{ const field=document.querySelector({json.dumps(selector)});
          Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(field,{json.dumps(text)});
          field.dispatchEvent(new Event('input',{{bubbles:true}})); }})()""")

    def check():
        try:
            wait("Boolean(document.querySelector('#open-document'))")
            type_query('.video-transcript-search input', '对偶')
            wait("document.querySelectorAll('.video-transcript-search mark').length===2")
            assert window.evaluate_js("document.querySelectorAll('.video-transcript-segment').length===1")
            window.evaluate_js("document.querySelector('.video-transcript-segment').click()")
            wait("document.body.dataset.seekSeconds==='4'")
            window.evaluate_js("document.querySelector('.video-transcript-search [aria-label=清除搜索]').click()")
            wait("document.querySelectorAll('.video-transcript-segment').length===3")
            report['checks'].append('字幕过滤、高亮、点击定位、清除恢复')
            window.evaluate_js("document.querySelector('#open-document').click()")
            wait("Boolean(document.querySelector('.v2-file-preview-dialog input'))")
            type_query('.v2-file-preview-dialog input', '建模与优化')
            wait("document.querySelector('.v2-file-preview-dialog .content-search-count').textContent==='1 / 36'")
            assert window.evaluate_js("Boolean(document.querySelector('.v2-file-preview-dialog strong mark'))")
            window.evaluate_js("document.querySelector('.v2-file-preview-dialog [aria-label=上一个匹配]').click()")
            wait("document.querySelector('.v2-file-preview-dialog .content-search-count').textContent==='36 / 36'")
            assert window.evaluate_js("document.querySelector('.v2-file-preview-content').scrollTop>0 && document.scrollingElement.scrollTop===0")
            report['checks'].append('跨 Markdown 格式匹配、计数、局部滚动')
            colors = []
            for theme in ('light', 'dark'):
                window.evaluate_js(f"document.documentElement.dataset.theme='{theme}'")
                colors.append(window.evaluate_js("""(() => { const e=document.querySelector('mark[data-search-current]');
                  const s=getComputedStyle(e); return {text:s.color,background:s.backgroundColor}; })()"""))
            assert colors[0]['background'] != colors[1]['background']
            for width in (1100, 760):
                window.resize(width, 820)
                threading.Event().wait(.2)
                assert window.evaluate_js("""(() => { const d=document.querySelector('.v2-file-preview-dialog').getBoundingClientRect(),
                  b=document.querySelector('.v2-file-preview-dialog .content-search-bar').getBoundingClientRect();
                  return b.left>=d.left && b.right<=d.right && document.documentElement.scrollWidth<=innerWidth+1; })()""")
            report['checks'].append(dict(themes=colors, responsive=True))
            window.evaluate_js("document.querySelector('.v2-file-preview-close').click()")
            wait("!document.querySelector('.v2-file-preview-dialog')")
            type_query('.pdf-search-preview input', '对偶')
            wait("document.querySelectorAll('.pdf-search-results mark').length===1")
            window.evaluate_js("document.querySelector('.pdf-search-page').click()")
            wait("document.querySelector('.pdf-search-layout iframe').getAttribute('src')==='about:blank#page=2'")
            report['checks'].append('PDF 可读文本高亮与页码定位')
            assert window.evaluate_js("!document.body.dataset.fixtureError")
            report['ok'] = True
        except Exception as exc:
            report.update(error=str(exc), traceback=traceback.format_exc())
        finally:
            window.destroy()

    webview.start(check, gui="cocoa", private_mode=True)
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

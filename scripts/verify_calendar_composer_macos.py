"""本轮 WKWebView 对齐与浮层验收；只打开内存夹具，不访问生产数据。"""
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
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    url = args.html.resolve().as_uri() + "?followup=1#/calendar"
    window = webview.create_window("SJTU 日历与对话布局隔离验收", url, width=args.width, height=820, text_select=True)
    result = dict(ok=False, native="WKWebView", width=args.width, production_database_accesses=0, checks=[])

    def wait(expression):
        deadline = time.monotonic() + 15
        while not window.evaluate_js(expression):
            if time.monotonic() >= deadline:
                raise RuntimeError("等待超时: " + expression)
            threading.Event().wait(.1)

    def check():
        try:
            wait("document.querySelectorAll('.v2-all-day-cell .v2-calendar-event').length >= 6")
            layout = window.evaluate_js("""(() => {
              const rect=e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,width:r.width}};
              const h=[...document.querySelectorAll('.v2-day-heading')].map(rect),
                    a=[...document.querySelectorAll('.v2-all-day-cell')].map(rect),
                    t=[...document.querySelectorAll('.v2-time-day')].map(rect),
                    today=document.querySelector('.v2-day-heading .is-today');
              return {h,a,t,today:today?rect(today):null,todayHeading:today?rect(today.parentElement):null};
            })()""")
            for heading, all_day, timed in zip(layout["h"], layout["a"], layout["t"]):
                for key in ("left", "right"):
                    assert abs(heading[key] - timed[key]) <= 1, layout
                    assert abs(all_day[key] - timed[key]) <= 1, layout
            if layout["today"]:
                a, b = layout["today"], layout["todayHeading"]
                assert abs((a["left"] + a["right"] - b["left"] - b["right"]) / 2) <= 1, layout
            result["checks"].append(dict(calendar_alignment=layout))
            window.evaluate_js("document.querySelector('.v2-native-status').click()")
            wait("Boolean(document.querySelector('[aria-label=\"Mac 日历配置\"]'))")
            assert window.evaluate_js("!document.querySelector('.v2-native-calendar select') && Boolean(document.querySelector('[data-ai-floating-menu] select'))")
            window.evaluate_js("document.querySelector('.v2-native-status').click(); Array.from(document.querySelectorAll('button')).find(e=>e.textContent.includes('添加日程')).click()")
            wait("Boolean(document.querySelector('.v2-event-dialog'))")
            assert window.evaluate_js("document.querySelector('.v2-event-dialog').getBoundingClientRect().width <= 460 && !document.querySelector('.v2-event-dialog input[type=date]')")
            window.evaluate_js("document.querySelector('[aria-label=选择日期]').click()")
            wait("Boolean(document.querySelector('.v2-date-popover'))")
            window.evaluate_js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
            wait("!document.querySelector('.v2-date-popover') && Boolean(document.querySelector('.v2-event-dialog'))")
            window.evaluate_js("document.querySelector('[aria-label=关闭]').click(); location.hash='/ai-chat'")
            wait("Boolean(document.querySelector('.ai-chat-messages .ai-message-user'))")
            wait("Boolean(document.querySelector('.ai-conversation').style.getPropertyValue('--ai-reading-width'))")
            chat = window.evaluate_js("""(() => {
              const r=s=>{const e=document.querySelector(s).getBoundingClientRect();return {left:e.left,right:e.right,width:e.width}};
              return {composer:r('.ai-composer-box'),messages:r('.ai-chat-messages'),user:r('.ai-message-user .ai-markdown')};
            })()""")
            assert abs(chat["composer"]["left"] - chat["messages"]["left"]) <= 1, chat
            assert abs(chat["composer"]["right"] - chat["messages"]["right"]) <= 1, chat
            assert abs(chat["user"]["right"] - chat["messages"]["right"]) <= 1, chat
            window.evaluate_js("document.querySelector('.ai-composer-controls button').click()")
            wait("Boolean(document.querySelector('[aria-label=当前对话配置]'))")
            placement = window.evaluate_js("""(() => {
              const rect=e=>{const r=e.getBoundingClientRect();return {top:r.top,bottom:r.bottom,height:r.height}};
              const panel=document.querySelector('[aria-label=当前对话配置]');
              const menu=panel.closest('[data-ai-floating-menu]');
              return {panel:rect(panel),menu:rect(menu),overflow:getComputedStyle(menu).overflowY,anchor:rect(document.querySelector('.ai-composer-controls button'))};
            })()""")
            result['checks'].append(dict(workspace_placement=placement))
            # 长配置在浮层内部滚动，定位边界应测浮层外框而非其未裁剪的内容。
            assert placement['menu']['bottom'] < placement['anchor']['top'], placement
            assert placement['overflow'] in ('auto', 'scroll'), placement
            window.evaluate_js("document.querySelectorAll('.ai-composer-controls>button')[1].click()")
            wait("Boolean(document.querySelector('[aria-label=文件操作权限]'))")
            assert window.evaluate_js("document.querySelectorAll('.ai-composer-actions>button').length===1 && !document.querySelector('.ui2-config-page')")
            assert window.evaluate_js("document.documentElement.scrollWidth<=innerWidth+1 && window.__v2Errors.length===0")
            result["checks"].append(dict(chat_alignment=chat, floating_configuration=True, compact_editor=True, custom_date_picker=True))
            result["ok"] = True
        except Exception as error:
            result["error"] = str(error) or type(error).__name__
            result["traceback"] = traceback.format_exc()
        finally:
            window.destroy()

    webview.start(check, gui="cocoa", private_mode=True)
    args.result.parent.mkdir(parents=True, exist_ok=True)
    args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

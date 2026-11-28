"""2.0.1 设置控件原生几何与深浅色验收，完全隔离生产数据。"""
import argparse
import json
import threading
import time
from pathlib import Path
import webview


def main():
    p=argparse.ArgumentParser();p.add_argument('html',type=Path);p.add_argument('--result',type=Path,required=True);args=p.parse_args()
    window=webview.create_window('2.0.1 控件隔离验收',args.html.resolve().as_uri(),width=1000,height=820)
    result=dict(ok=False, checks=[])
    def run():
        try:
            deadline=time.monotonic()+15
            while not window.evaluate_js("Boolean(document.querySelector('.ai-preference-options'))"):
                if time.monotonic()>deadline: raise RuntimeError('fixture timeout')
                threading.Event().wait(.1)
            # 后台 WKWebView 会暂停 CSS 动画；测量最终配色时禁用控件过渡。
            window.evaluate_js("document.querySelector('[role=switch]').style.transition='none'")
            for width in (1000,760):
                window.resize(width,820)
                for theme in ('light','dark'):
                    window.evaluate_js("document.documentElement.dataset.theme="+json.dumps(theme))
                    threading.Event().wait(.3)
                    state=window.evaluate_js("""(() => {
                      const toggle=document.querySelector('[role=switch]'),ts=getComputedStyle(toggle),thumb=getComputedStyle(toggle,'::after');
                      const heading=document.querySelector('.heading-with-info'),h=heading.children[0].getBoundingClientRect(),i=heading.children[1].getBoundingClientRect();
                      const buttons=[...document.querySelectorAll('.ai-preference-options button')].map(b=>{
                        const r=b.getBoundingClientRect(), label=b.querySelector('span:not(.selection-indicator)').getBoundingClientRect(), icon=b.querySelector('svg')?.getBoundingClientRect();
                        return {height:r.height, font:getComputedStyle(b).fontSize, labelCenter:label.y+label.height/2, iconCenter:icon?icon.y+icon.height/2:null, background:getComputedStyle(b).backgroundColor};
                      });return {switchWidth:ts.width,appearance:ts.appearance,thumb:thumb.backgroundColor,bg:ts.backgroundColor,aligned:Math.abs(h.y+h.height/2-i.y-i.height/2)<2,buttons,overflow:document.documentElement.scrollWidth>innerWidth};
                    })()""")
                    assert state['switchWidth']=='36px' and state['appearance']=='none',state
                    assert state['thumb']!=state['bg'],state
                    if theme == 'dark': assert state['bg']=='rgb(233, 233, 233)',state
                    assert state['aligned'] and not state['overflow'],state
                    for b in state['buttons']:
                        assert b['height']>=32 and b['font']=='12px',b
                        assert b['iconCenter'] is None or abs(b['labelCenter']-b['iconCenter'])<2,b
                    result['checks'].append(dict(width=width,theme=theme,geometry=state))
            result['ok']=True
        except Exception as exc:result['error']=str(exc)
        finally:window.destroy()
    webview.start(run,gui='cocoa',private_mode=True)
    args.result.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 1

if __name__=='__main__':raise SystemExit(main())

"""2.0 视频、扩展和按钮的原生 WKWebView 验收；使用独立内存夹具。"""
import argparse
import json
import threading
import time
import traceback
from pathlib import Path
import webview


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html',type=Path)
    parser.add_argument('--width',type=int,required=True)
    parser.add_argument('--result',type=Path,required=True)
    parser.add_argument('--screenshots',type=Path,required=True)
    args=parser.parse_args()
    window=webview.create_window('2.0 视频与扩展隔离验收',args.html.resolve().as_uri()+'?video_ai=1#/videos',width=args.width,height=820,text_select=True)
    result=dict(ok=False,width=args.width,native='WKWebView',production_database_accesses=0,checks=[])
    def evaluate(code):return window.evaluate_js(code)
    def wait(code):
        deadline=time.monotonic()+20
        while not evaluate(code):
            if time.monotonic()>deadline:raise RuntimeError('等待超时：'+code)
            threading.Event().wait(.1)
    def capture(name):
        threading.Event().wait(.5)
        from AppKit import NSBitmapImageRep, NSJPEGFileType, NSImageCompressionFactor
        from PyObjCTools import AppHelper
        from webview.platforms.cocoa import BrowserView
        event=threading.Event();args.screenshots.mkdir(parents=True,exist_ok=True)
        target=args.screenshots/(str(args.width)+'-'+name+'.jpg')
        def snap():
            def done(image,error):
                if image is not None and error is None:
                    bitmap=NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                    bitmap.representationUsingType_properties_(NSJPEGFileType,{NSImageCompressionFactor:.8}).writeToFile_atomically_(str(target),True)
                event.set()
            BrowserView.instances[window.uid].webview.takeSnapshotWithConfiguration_completionHandler_(None,done)
        AppHelper.callAfter(snap)
        if not event.wait(10) or not target.is_file():raise RuntimeError('截图失败')
    def colors():
        return evaluate("""(() => {const visible=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0};return [...document.querySelectorAll('button')].filter(visible).map(e=>{const c=getComputedStyle(e);return {text:(e.textContent||e.getAttribute('aria-label')).slice(0,45),color:c.color,background:c.backgroundColor,border:c.borderColor,disabled:e.disabled}})})()""")
    def assert_monochrome():
        for row in colors():
            for key in ('color','background'):
                import re
                numbers=re.findall(r'[0-9.]+',row[key])
                if len(numbers)>=3:
                    channels=list(map(float,numbers[:3]))
                    assert max(channels)-min(channels)<=2,row
    def check():
        try:
            wait("Boolean(document.querySelector('.video-recording-play-target'))")
            evaluate("""(() => {const e=document.querySelector('.video-recording-title-row strong'),r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);if(!hit.closest('.video-recording-play-target'))throw new Error('可见标题没有命中播放按钮');hit.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));hit.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));hit.click()})()""")
            wait("Boolean(document.querySelector('.v2-playback-controls'))")
            state=evaluate("""(() => {const p=document.querySelector('.video-player-shell').getBoundingClientRect(),l=document.querySelector('.video-list-panel').getBoundingClientRect(),v=document.querySelector('video'),c=document.querySelector('.v2-playback-controls').getBoundingClientRect();return {width:p.width,height:p.height,listLeft:l.left,playerLeft:p.left,nativeControls:v.controls,controlsInside:c.left>=p.left&&c.right<=p.right&&c.bottom<=p.bottom,errors:window.__v2Errors,overflow:document.documentElement.scrollWidth>innerWidth+1}})()""")
            assert abs(state['width']/state['height']-16/9)<.02,state
            assert state['controlsInside'] and not state['nativeControls'] and not state['overflow'] and state['errors']==[],state
            if args.width>=1000:assert state['listLeft']<state['playerLeft'],state
            evaluate("(() => {const v=document.querySelector('video');v.currentTime=2;v.dispatchEvent(new Event('timeupdate'))})()")
            wait("Boolean(document.querySelector('.video-caption-overlay'))")
            assert evaluate("(() => {const c=document.querySelector('.video-caption-overlay'),r=c.getBoundingClientRect(),p=document.querySelector('.video-player-shell').getBoundingClientRect();return r.height<=55 && r.bottom<p.bottom && r.left>=p.left})()")
            evaluate("[...document.querySelectorAll('.video-learning-tabs button')].find(e=>e.textContent==='字幕').dispatchEvent(new MouseEvent('mousedown',{button:0,ctrlKey:false,bubbles:true}))")
            evaluate("[...document.querySelectorAll('.video-learning-tabs button')].find(e=>e.textContent==='字幕').click()")
            wait("document.querySelector('.video-learning-tabs [data-state=active]')?.textContent==='字幕'")
            wait("document.querySelectorAll('.video-transcript-time').length>0")
            state['subtitleTags']=evaluate("document.querySelectorAll('.video-transcript-time').length")
            evaluate("document.querySelectorAll('.video-transcript-segment')[1].click()")
            wait("document.querySelector('.video-transcript-segment[aria-current=true]')?.textContent.includes('第二段')")
            result['checks'].append(dict(visibleTitlePlays=True,automaticCaption=True,subtitleClickSeeks=True))
            result['checks'].append(dict(video=state));capture('video')
            ppt=evaluate("Boolean(document.querySelector('[aria-label=\"打开 PPT 分屏\"]'))")
            if ppt:
                evaluate("document.querySelector('[aria-label=\"打开 PPT 分屏\"]').click()")
                wait("Boolean(document.querySelector('.v2-slide-overlay'))")
                evaluate("document.querySelector('.v2-slide-overlay').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))")
                assert evaluate("Boolean(document.querySelector('.v2-slides-main'))")
                evaluate("document.querySelector('.v2-slide-overlay').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))")
                assert evaluate("!document.querySelector('.v2-slides-main')")
                result['checks'].append(dict(pptDoubleClickSwap=True))
            evaluate("location.hash='/ai-chat'");wait("Boolean(document.querySelector('.ai-composer-input'))")
            evaluate("[...document.querySelectorAll('button')].find(e=>e.textContent==='技能配置').click()")
            wait("document.querySelectorAll('.extension-switch').length>0")
            assert evaluate("![...document.querySelectorAll('button')].some(e=>e.textContent.includes('导入 ZIP'))")
            evaluate("[...document.querySelectorAll('[role=tab]')].find(e=>e.textContent==='外部技能').click()")
            assert evaluate("[...document.querySelectorAll('button')].some(e=>e.textContent.includes('导入 ZIP'))")
            evaluate("document.querySelector('.extension-row-info').click()")
            wait("Boolean(document.querySelector('.extension-file-layout'))")
            result['checks'].append(dict(skillImportExternalOnly=True,originalFiles=True,buttons=colors()));capture('skills')
            evaluate("[...document.querySelectorAll('button')].find(e=>e.textContent==='MCP 服务').click()")
            wait("Boolean(document.querySelector('.extension-row-info'))")
            evaluate("document.querySelector('.extension-row-info').click()")
            wait("Boolean(document.querySelector('.extension-tools'))")
            result['checks'].append(dict(mcpConfiguration=True,buttons=colors()));capture('mcp')
            evaluate("location.hash='/assignments'");wait("Boolean(document.querySelector('.v2-assignment-row'))")
            evaluate("document.querySelector('.v2-assignment-row').click()")
            wait("Boolean(document.querySelector('.submission-forms'))")
            result['checks'].append(dict(assignmentButtons=colors()));capture('assignment')
            assert evaluate("document.documentElement.scrollWidth<=innerWidth+1 && window.__v2Errors.length===0")
            assert_monochrome()
            result['ok']=True
        except Exception as error:
            result.update(error=str(error),traceback=traceback.format_exc(),diagnostic=evaluate("({text:document.body.textContent.slice(-5500),errors:window.__v2Errors})"));capture('failure')
        finally:window.destroy()
    webview.start(check,gui='cocoa',private_mode=True)
    args.result.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 1

if __name__=='__main__':raise SystemExit(main())

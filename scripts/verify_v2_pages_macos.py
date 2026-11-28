"""2.0 全页面 WKWebView 隔离验收。桥接由前端内存夹具提供，不读生产数据。"""
import argparse
import json
import threading
import time
from pathlib import Path
import webview

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html', type=Path)
    parser.add_argument('--width', type=int, default=1280)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--screenshots', type=Path, required=True)
    args = parser.parse_args()
    window = webview.create_window('SJTU 2.0 全页面隔离验收', args.html.resolve().as_uri() + '#/overview', width=args.width, height=820, text_select=True)
    result = dict(ok=False, native='WKWebView', width=args.width, production_database_accesses=0, pages=[])
    def evaluate(expression):
        return window.evaluate_js(expression)
    def wait(expression):
        deadline = time.monotonic() + 15
        while not evaluate(expression):
            if time.monotonic() > deadline:
                raise RuntimeError('页面等待超时: ' + expression)
            threading.Event().wait(.1)
    def capture(name):
        from AppKit import NSBitmapImageRep, NSJPEGFileType, NSImageCompressionFactor
        from PyObjCTools import AppHelper
        from webview.platforms.cocoa import BrowserView
        event = threading.Event()
        target = args.screenshots / (name + '.jpg')
        def snapshot():
            def done(image, error):
                if image is not None and error is None:
                    args.screenshots.mkdir(parents=True, exist_ok=True)
                    bitmap = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                    bitmap.representationUsingType_properties_(NSJPEGFileType, {NSImageCompressionFactor:.8}).writeToFile_atomically_(str(target), True)
                event.set()
            BrowserView.instances[window.uid].webview.takeSnapshotWithConfiguration_completionHandler_(None, done)
        AppHelper.callAfter(snapshot)
        if not event.wait(10) or not target.is_file():
            raise RuntimeError('原生截图失败')
    def check():
        try:
            wait("Boolean(document.querySelector('.v2-sidebar'))")
            for page in ('overview','calendar','messages','assignments','materials','videos','backup','knowledge','settings','ai-chat'):
                evaluate('location.hash=' + json.dumps('/'+page))
                threading.Event().wait(.7)
                if page == 'assignments':
                    evaluate("document.querySelector('.v2-assignment-row')?.click()")
                if page == 'materials':
                    for label in ('2026','文本分析','课件'):
                        evaluate("Array.from(document.querySelectorAll('.v2-material-folder-row')).find(e=>e.textContent.includes("+json.dumps(label)+"))?.click()")
                        threading.Event().wait(.2)
                    evaluate("document.querySelector('.v2-material-row>button')?.click()")
                if page == 'knowledge':
                    evaluate("Array.from(document.querySelectorAll('.compiled-knowledge-folders button')).find(e=>e.textContent.includes('机器学习'))?.click()")
                    threading.Event().wait(.2)
                    evaluate("document.querySelector('.compiled-knowledge-item')?.click()")
                if page == 'videos':
                    evaluate("document.querySelector('.video-recording-play-target')?.click()")
                threading.Event().wait(.4)
                state = evaluate("(() => {const b=document.querySelector('.v2-sidebar .v2-brand img,.ai-sidebar .v2-brand img'),r=b?.getBoundingClientRect(),p=document.querySelector('.video-player-shell');return {view:location.hash,viewport:innerWidth,bodyWidth:document.documentElement.scrollWidth,errors:window.__v2Errors,brand:r?{x:r.x,y:r.y,width:r.width,height:r.height}:null,player:p?{width:p.offsetWidth,height:p.offsetHeight}:null,text:document.body.textContent.length}})()")
                assert state['errors'] == [], state
                assert state['bodyWidth'] <= state['viewport'] + 1, state
                assert state['text'] > 150, state
                if state['player']:
                    p = state['player']
                    assert abs(p['width']/p['height']-16/9) < .015, p
                capture(page)
                result['pages'].append(state)
            assert result['pages'][0]['brand'] == result['pages'][-1]['brand'], result['pages']
            result['ok'] = True
        except Exception as error:
            result['error'] = str(error)
        finally:
            window.destroy()
    webview.start(check, gui='cocoa', private_mode=True)
    args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1
if __name__ == '__main__':
    raise SystemExit(main())

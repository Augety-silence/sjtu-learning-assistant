"""原生窗口核对字幕列表位于视频下方，并保留时间定位与独立滚动。"""
import argparse,json,threading,time
from pathlib import Path
import webview


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('html',type=Path)
    parser.add_argument('--width',type=int,required=True)
    parser.add_argument('--result',type=Path,required=True)
    parser.add_argument('--screenshot',type=Path,required=True)
    args=parser.parse_args()
    # 数据与真实接口隔离：8秒合成录像，40段合成字幕，其中一段模拟295字。
    vtt='WEBVTT\n\n'
    for i in range(40):
        start=i*200;end=start+200
        stamp=lambda ms:'00:00:'+str(ms//1000).zfill(2)+'.'+str(ms%1000).zfill(3)
        text=('较长字幕测试，正文完整保留并在列表阅读。'*20)[:295] if i==1 else f'第{i+1}段合成字幕，用于验证时间标签和列表内部滚动。'
        vtt+=stamp(start)+' --> '+stamp(end)+'\n'+text+'\n\n'
    fixture=args.html.with_name('subtitles-below-'+str(args.width)+'.html')
    fixture.write_text(args.html.read_text().replace('</head>','<script>window.__V2_LIVE_VIDEO__='+json.dumps({'subtitles':{'status':'ready','vtt':vtt,'cue_count':40}},ensure_ascii=False)+';</script></head>'))
    window=webview.create_window('字幕下方布局验收',fixture.as_uri()+'?video_ai=1#/videos',width=args.width,height=820,text_select=True)
    result={'ok':False,'width':args.width,'native':'WKWebView','production_data_accesses':0}
    def evaluate(code):return window.evaluate_js(code)
    def wait(code):
        end=time.monotonic()+20
        while not evaluate(code):
            if time.monotonic()>end:raise RuntimeError('等待页面状态超时')
            threading.Event().wait(.1)
    def capture():
        from AppKit import NSBitmapImageRep,NSJPEGFileType,NSImageCompressionFactor
        from PyObjCTools import AppHelper
        from webview.platforms.cocoa import BrowserView
        event=threading.Event();args.screenshot.parent.mkdir(parents=True,exist_ok=True)
        def snap():
            def done(image,error):
                if image is not None and error is None:
                    bitmap=NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
                    bitmap.representationUsingType_properties_(NSJPEGFileType,{NSImageCompressionFactor:.8}).writeToFile_atomically_(str(args.screenshot),True)
                event.set()
            BrowserView.instances[window.uid].webview.takeSnapshotWithConfiguration_completionHandler_(None,done)
        AppHelper.callAfter(snap)
        if not event.wait(10):raise RuntimeError('截图超时')
    def check():
        try:
            wait("Boolean(document.querySelector('.video-recording-title-row strong'))")
            evaluate("(() => {const e=document.querySelector('.video-recording-title-row strong'),r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);if(!hit.closest('.video-recording-play-target'))throw new Error('标题点击未命中播放');hit.click()})()")
            wait("document.querySelectorAll('.v2-video-learning .video-transcript-segment').length===40")
            state=evaluate("(() => {const p=document.querySelector('.video-player-shell'),r=p.getBoundingClientRect(),l=document.querySelector('.v2-video-learning'),s=l.getBoundingClientRect(),panel=l.querySelector('.video-tool-panel'),b=panel.getBoundingClientRect(),t=l.querySelector('.video-transcript-segment'),c=getComputedStyle(t);return {ratio:r.width/r.height,panelTop:s.top,playerBottom:r.bottom,aligned:Math.abs(r.left-s.left)<1&&Math.abs(r.right-s.right)<1,insidePlayer:Boolean(p.querySelector('.video-tool-panel')),defaultExpanded:l.classList.contains('expanded'),bodyHeight:b.height,scrollHeight:panel.scrollHeight,color:c.color,background:getComputedStyle(l).backgroundColor,longChars:l.querySelectorAll('.video-transcript-segment span')[1].textContent.length,overflow:document.documentElement.scrollWidth>innerWidth+1}})()")
            assert abs(state['ratio']-16/9)<.02 and state['panelTop']>=state['playerBottom']+10 and state['aligned'] and not state['insidePlayer'] and state['defaultExpanded'],state
            assert state['scrollHeight']>state['bodyHeight'] and state['longChars']==295 and not state['overflow'],state
            result['layout']=state
            evaluate("document.querySelector('video').pause();document.querySelector('.v2-video-learning').scrollIntoView({block:'end'})")
            threading.Event().wait(.2)
            evaluate("document.querySelector('.video-tool-panel').scrollTop=0")
            capture()
            before=evaluate("(() => {let e=document.querySelector('.v2-video-learning');const positions=[];while(e){positions.push(e.scrollTop);e=e.parentElement}return positions})()")
            evaluate("(() => {const v=document.querySelector('video');v.currentTime=7.8;v.dispatchEvent(new Event('timeupdate'))})()")
            wait("document.querySelector('.video-transcript-segment[aria-current=true]')?.textContent.includes('第40段')")
            assert evaluate("document.querySelector('.video-tool-panel').scrollTop>0")
            assert evaluate("(() => {let e=document.querySelector('.v2-video-learning');const positions=[];while(e){positions.push(e.scrollTop);e=e.parentElement}return positions})()") == before
            evaluate("document.querySelector('.video-transcript-segment[aria-current=true]').click()")
            assert abs(evaluate("document.querySelector('video').currentTime")-7.8)<.01
            evaluate("document.querySelector('[aria-label=\"收起视频学习面板\"]').click()")
            assert evaluate("getComputedStyle(document.querySelector('.video-tool-panel')).display==='none'")
            evaluate("document.querySelector('[aria-label=\"展开视频学习面板\"]').click()")
            assert evaluate("getComputedStyle(document.querySelector('.video-tool-panel')).display!=='none'")
            result.update(ok=True,localScrollOnly=True,clickSeeks=True,collapseWorks=True,errors=evaluate('window.__v2Errors'))
            assert not result['errors']
        except Exception as error:result['error']=str(error)
        finally:window.destroy()
    webview.start(check,gui='cocoa',private_mode=True)
    args.result.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 1

if __name__=='__main__':raise SystemExit(main())

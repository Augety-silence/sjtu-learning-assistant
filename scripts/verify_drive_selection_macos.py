"""扩展配置和固定传输入口的原生验收；桥接数据全部为合成数据。"""
import argparse,json,threading,time,traceback
from pathlib import Path
import webview

def main():
 parser=argparse.ArgumentParser();parser.add_argument('html',type=Path);parser.add_argument('--width',type=int,required=True);parser.add_argument('--result',type=Path,required=True);parser.add_argument('--screenshots',type=Path,required=True);args=parser.parse_args()
 window=webview.create_window('2.0 云盘勾选布局验收',args.html.resolve().as_uri()+'#/ai-chat',width=args.width,height=820,text_select=True)
 result=dict(ok=False,width=args.width,native='WKWebView',production_database_accesses=0,checks=[])
 def evaluate(js):return window.evaluate_js(js)
 def wait(js):
  deadline=time.monotonic()+20
  while not evaluate(js):
   if time.monotonic()>deadline:raise RuntimeError('等待超时：'+js)
   threading.Event().wait(.1)
 def click(label):
  evaluate("[...document.querySelectorAll('button')].find(e=>e.textContent.trim()==="+json.dumps(label)+" || e.getAttribute('aria-label')==="+json.dumps(label)+").click()")
 def capture(name):
  threading.Event().wait(.3)
  from AppKit import NSBitmapImageRep,NSJPEGFileType,NSImageCompressionFactor
  from PyObjCTools import AppHelper
  from webview.platforms.cocoa import BrowserView
  event=threading.Event();args.screenshots.mkdir(parents=True,exist_ok=True);target=args.screenshots/(str(args.width)+'-'+name+'.jpg')
  def snap():
   def done(image,error):
    if image is not None and error is None:
     bitmap=NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation());bitmap.representationUsingType_properties_(NSJPEGFileType,{NSImageCompressionFactor:.8}).writeToFile_atomically_(str(target),True)
    event.set()
   BrowserView.instances[window.uid].webview.takeSnapshotWithConfiguration_completionHandler_(None,done)
  AppHelper.callAfter(snap)
  if not event.wait(10) or not target.is_file():raise RuntimeError('截图失败')
 def close():
  evaluate("document.querySelector('.extension-modal-close').click()")
  wait("!document.querySelector('.extension-modal')")
 def geometry():
  return evaluate("""(() => {const m=document.querySelector('.extension-modal'),r=m.getBoundingClientRect(),b=document.querySelector('.extension-modal-body'),list=document.querySelector('.extension-rows');return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height,bodyScroll:b.scrollHeight> b.clientHeight+1,listHeight:list.getBoundingClientRect().height,overflow:document.documentElement.scrollWidth>innerWidth+1,focusInside:m.contains(document.activeElement),backgroundInert:document.getElementById('root').hasAttribute('inert'),errors:window.__v2Errors}})()""")
 def measure():
  return evaluate("""(() => {const r=e=>{const b=e.getBoundingClientRect();return {top:b.top,left:b.left,width:b.width,height:b.height}};return {table:r(document.querySelector('.drive-workspace')),header:r(document.querySelector('.drive-table thead')),toolbar:r(document.querySelector('.drive-toolbar')),search:r(document.querySelector('.drive-search')),scroll:document.querySelector('.drive-table-scroll').scrollTop,overflow:document.documentElement.scrollWidth>innerWidth+1,extraBar:!!document.querySelector('.drive-selection-bar'),errors:window.__v2Errors}})()""")
 def stable(label,baseline):
  state=measure()
  for part in ('table','header','toolbar','search'):
   for key in ('top','left','width'):
    assert abs(state[part][key]-baseline[part][key])<1,(label,part,key,baseline,state)
  assert abs(state['toolbar']['height']-baseline['toolbar']['height'])<1,(label,state)
  assert abs(state['scroll']-baseline['scroll'])<1,(label,state)
  assert not state['overflow'] and not state['extraBar'] and not state['errors'],(label,state)
  result['checks'].append(dict(state=label,geometry=state))
 def check():
  try:
   wait("Boolean(document.querySelector('.ai-composer-input'))")
   evaluate("""(() => {const original=window.pywebview.api.invoke;window.__downloadCalls=[];window.pywebview.api.invoke=async(a,p={})=>{if(a==='drive_list'){const d=await original(a,p),sample=d.data.items;d.data.items=Array.from({length:19},(_,i)=>{const f=sample[i%sample.length];return {...f,name:i+' '+f.name,path:[i+' '+f.name]}});d.data.total=19;d.data.pages=1;return d};if(a==='drive_prepare_download')return {ok:true,data:{transfer_id:p.transfer_id}};if(a==='drive_download'){window.__downloadCalls.push(p);return new Promise(resolve=>window.__pendingDownload=resolve)};return original(a,p)}})()""")
   evaluate("location.hash='/backup'");wait("document.querySelectorAll('.drive-row').length===19")
   evaluate("document.querySelector('.drive-table-scroll').scrollTop=160")
   baseline=measure();result['baseline']=baseline;capture('unselected')
   evaluate("document.querySelectorAll('.drive-row input[type=checkbox]')[5].click()")
   wait("Boolean(document.querySelector('.drive-selection-tools'))")
   assert evaluate("document.querySelector('.drive-toolbar').contains(document.querySelector('.drive-selection-tools'))")
   stable('folder-selected',baseline)
   click('取消选择');wait("!document.querySelector('.drive-selection-tools')");stable('folder-cleared',baseline)
   evaluate("document.querySelectorAll('.drive-row input[type=checkbox]')[6].click()")
   wait("Boolean(document.querySelector('.drive-selection-tools'))");stable('file-selected',baseline);capture('selected')
   evaluate("document.querySelectorAll('.drive-row input[type=checkbox]')[7].click()")
   wait("document.querySelector('.drive-selection-tools')?.textContent.includes('已选择 2 项')")
   stable('multi-selected',baseline);capture('multi-selected')
   assert evaluate("document.querySelector('.drive-selection-tools [aria-label=\"重命名\"]').disabled")
   click('取消选择');wait("!document.querySelector('.drive-selection-tools')");stable('selection-cleared',baseline)
   evaluate("document.querySelectorAll('.drive-row input[type=checkbox]')[6].click()")
   wait("Boolean(document.querySelector('.drive-selection-tools'))")
   evaluate("document.querySelector('.drive-selection-tools [aria-label=\"下载\"]').click()")
   wait("window.__downloadCalls.length===1");stable('downloading',baseline)
   assert evaluate("!document.querySelector('.drive-transfer-popover') && window.__downloadCalls[0].save_mode==='downloads'")
   evaluate("window.__pendingDownload({ok:true,data:{status:'downloaded',transfer_id:window.__downloadCalls[0].transfer_id,name:'合成文件.md',size:100}})")
   threading.Event().wait(.2);stable('download-complete',baseline)
   evaluate("document.querySelector('.drive-location [aria-label=\"查看传输列表\"]').click()")
   wait("Boolean(document.querySelector('.drive-transfer-row[data-status=done]'))")
   stable('transfer-opened',baseline);capture('transfer-complete')
   evaluate("document.querySelector('.drive-location [aria-label=\"查看传输列表\"]').click()")
   click('取消选择');wait("!document.querySelector('.drive-selection-tools')");stable('final-clear',baseline)
   result['ok']=True
  except Exception as e:
   result.update(error=str(e),traceback=traceback.format_exc(),diagnostic=evaluate("({text:document.body.textContent.slice(-5000),errors:window.__v2Errors})"));capture('failure')
  finally:window.destroy()

 webview.start(check,gui='cocoa',private_mode=True)
 args.result.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 1
if __name__=='__main__':raise SystemExit(main())

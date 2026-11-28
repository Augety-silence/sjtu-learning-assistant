"""扩展配置和固定传输入口的原生验收；桥接数据全部为合成数据。"""
import argparse,json,threading,time,traceback
from pathlib import Path
import webview

def main():
 parser=argparse.ArgumentParser();parser.add_argument('html',type=Path);parser.add_argument('--width',type=int,required=True);parser.add_argument('--result',type=Path,required=True);parser.add_argument('--screenshots',type=Path,required=True);args=parser.parse_args()
 window=webview.create_window('2.0 扩展与传输验收',args.html.resolve().as_uri()+'#/ai-chat',width=args.width,height=820,text_select=True)
 result=dict(ok=False,width=args.width,native='WKWebView',production_database_accesses=0,checks=[])
 def evaluate(js):return window.evaluate_js(js)
 def wait(js):
  deadline=time.monotonic()+20
  while not evaluate(js):
   if time.monotonic()>deadline:raise RuntimeError('等待超时：'+js)
   threading.Event().wait(.1)
 def click(label):
  evaluate("[...document.querySelectorAll('button')].find(e=>e.textContent.trim()==="+json.dumps(label)+").click()")
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
 def check():
  try:
   wait("Boolean(document.querySelector('.ai-composer-input'))")
   evaluate("""(() => {const original=window.pywebview.api.invoke;window.__downloadCalls=[];window.__pendingDownload=null;window.pywebview.api.invoke=async (a,p={})=>{if(a==='ai_mcp_list'){const d=await original(a,p);const sample=d.data.items[0];d.data.items=Array.from({length:12},(_,i)=>({...sample,id:'service-'+i,name:i?'合成服务 '+i:'DuckDuckGo',tools:Array.from({length:18},(_,j)=>({...sample.tools[0],name:'search_'+j,description:'较长的工具说明。'.repeat(35)}))}));return d;}if(a==='drive_prepare_download')return {ok:true,data:{transfer_id:p.transfer_id}};if(a==='drive_download'){window.__downloadCalls.push(p);return new Promise(resolve=>{window.__pendingDownload=resolve})};if(a==='drive_cancel'){window.__pendingDownload?.({ok:false,error:{code:'cancelled',message:'已取消'}});window.__pendingDownload=null;return {ok:true,data:{cancelled:true,transfer_id:p.transfer_id}}};return original(a,p)}})()""")
   click('技能配置');wait("document.querySelectorAll('.extension-switch').length>0")
   click('外部技能');wait("document.querySelector('.extension-row-info')?.textContent.includes('论文阅读')");evaluate("window.__listHeight=document.querySelector('.extension-rows').getBoundingClientRect().height;document.querySelector('.extension-row-info').focus();document.querySelector('.extension-row-info').click()")
   wait("Boolean(document.querySelector('.extension-modal'))")
   state=geometry();assert state['focusInside'] and state['backgroundInert'] and not state['overflow'],state
   assert state['left']>=10 and state['right']<=args.width and state['top']>=10 and state['bottom']<=820,state
   assert abs(evaluate('window.__listHeight')-state['listHeight'])<1,state
   assert evaluate("document.querySelector('.extension-file-layout').textContent.includes('scripts/read.py')")
   capture('skill-config');close()
   assert evaluate("document.activeElement.classList.contains('extension-row-info') && !document.getElementById('root').hasAttribute('inert')")
   result['checks'].append(dict(skillPortal=True,originalPaths=True,focusRestore=True,geometry=state))
   click('MCP 服务');wait("document.querySelectorAll('.extension-row-info').length===12")
   evaluate("window.__listHeight=document.querySelector('.extension-rows').getBoundingClientRect().height;document.querySelector('.extension-row-info').focus();document.querySelector('.extension-row-info').click()")
   wait("Boolean(document.querySelector('.extension-tools'))")
   state=geometry();assert state['bodyScroll'] and state['focusInside'] and not state['overflow'],state
   assert abs(evaluate('window.__listHeight')-state['listHeight'])<1,state
   evaluate("document.querySelector('.extension-modal-body').scrollTop=10000")
   assert evaluate("document.querySelector('.extension-modal-body').scrollTop>0")
   evaluate("document.querySelector('.extension-modal-body').scrollTop=0");capture('mcp-config');close()
   click('添加 / 导入');wait("Boolean(document.querySelector('.extension-import'))")
   assert evaluate("Boolean(document.querySelector('.extension-modal textarea'))");close()
   result['checks'].append(dict(mcpPortal=True,manyServices=12,longToolDescriptions=18,independentScroll=True,importPortal=True,geometry=state))
   evaluate("location.hash='/backup'");wait("Boolean(document.querySelector('.drive-location'))")
   assert evaluate("Boolean(document.querySelector('.drive-location [aria-label=\"查看传输列表\"]'))")
   evaluate("document.querySelector('.drive-location [aria-label=\"查看传输列表\"]').click()")
   wait("Boolean(document.querySelector('.drive-transfer-popover'))")
   assert evaluate("document.querySelector('.drive-transfer-popover').textContent.includes('暂无传输任务')")
   evaluate("document.querySelector('.drive-location [aria-label=\"查看传输列表\"]').click()")
   evaluate("document.querySelector('.drive-table tbody tr[data-kind=file]').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))")
   wait("window.__downloadCalls.length===1")
   assert evaluate("window.__downloadCalls[0].save_mode==='downloads' && !document.querySelector('.drive-transfer-popover')")
   evaluate("document.querySelector('.drive-location [aria-label=\"查看传输列表\"]').click()")
   wait("Boolean(document.querySelector('.drive-transfer-row'))")
   state=evaluate("""(() => {const r=document.querySelector('.drive-transfer-popover').getBoundingClientRect(),a=document.querySelector('.drive-location').getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,anchorTop:a.top,titleDisplay:getComputedStyle(document.querySelector('.drive-transfer-title')).display,trayMargin:getComputedStyle(document.querySelector('.drive-transfer-tray')).marginTop,overflow:document.documentElement.scrollWidth>innerWidth+1}})()""")
   assert state['left']>=10 and state['right']<=args.width and state['bottom']<=820 and state['top']>state['anchorTop'] and state['titleDisplay']=='flex' and state['trayMargin']=='0px',state
   capture('drive-transfer')
   evaluate("document.querySelector('.drive-transfer-actions button').click()")
   wait("document.querySelector('.drive-transfer-row')?.dataset.status==='cancelled'")
   evaluate("document.querySelector('.drive-location [aria-label=\"查看传输列表\"]').click()")
   evaluate("document.querySelector('.drive-table tbody tr[data-kind=file]').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))")
   wait("window.__downloadCalls.length===2")
   evaluate("window.__pendingDownload({ok:true,data:{status:'downloaded',transfer_id:window.__downloadCalls[1].transfer_id,name:'合成资料.pdf',size:200}})")
   threading.Event().wait(.2)
   assert evaluate("!document.querySelector('.drive-transfer-popover')")
   evaluate("document.querySelector('.drive-location [aria-label=\"查看传输列表\"]').click()")
   wait("Boolean(document.querySelector('.drive-transfer-row[data-status=done]'))")
   capture('drive-complete')
   result['checks'].append(dict(fixedHeader=True,noAutomaticPopup=True,downloadsMode=True,cancel=True,completed=True,geometry=state))
   assert evaluate("window.__v2Errors.length===0")
   result['ok']=True
  except Exception as e:
   result.update(error=str(e),traceback=traceback.format_exc(),diagnostic=evaluate("({text:document.body.textContent.slice(-6000),errors:window.__v2Errors})"));capture('failure')
  finally:window.destroy()
 webview.start(check,gui='cocoa',private_mode=True)
 args.result.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 1
if __name__=='__main__':raise SystemExit(main())

"""文件浏览与悬浮预览的原生验收；桥接数据全部为合成数据。"""
import argparse,json,threading,time,traceback
from pathlib import Path
import webview

def main():
 parser=argparse.ArgumentParser();parser.add_argument('html',type=Path);parser.add_argument('--width',type=int,required=True);parser.add_argument('--result',type=Path,required=True);parser.add_argument('--screenshots',type=Path,required=True);args=parser.parse_args()
 window=webview.create_window('2.0 文件浏览与悬浮预览验收',args.html.resolve().as_uri()+'#/ai-chat',width=args.width,height=820,text_select=True)
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
 def preview_geometry():
  return evaluate("""(() => {const m=document.querySelector('.v2-file-preview-dialog'),r=m.getBoundingClientRect(),body=document.querySelector('.v2-file-preview-body');return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height,bodyOverflow:body.scrollWidth>body.clientWidth+1,overflow:document.documentElement.scrollWidth>innerWidth+1,focusInside:m.contains(document.activeElement),backgroundInert:document.getElementById('root').hasAttribute('inert'),errors:window.__v2Errors}})()""")
 def check_preview(name):
  wait("Boolean(document.querySelector('.v2-file-preview-dialog'))")
  wait("document.querySelector('.v2-file-preview-dialog').contains(document.activeElement)")
  value=preview_geometry()
  assert value['left']>=0 and value['right']<=args.width+1 and value['top']>=0 and value['bottom']<=820 and value['height']>400 and value['width']>650,value
  assert value['focusInside'] and value['backgroundInert'] and not value['overflow'] and not value['bodyOverflow'] and not value['errors'],value
  result['checks'].append(dict(state=name,geometry=value));capture(name)
 def close_preview():
  evaluate("document.querySelector('.v2-file-preview-close').click()")
  wait("!document.querySelector('.v2-file-preview-dialog')")
  assert evaluate("!document.getElementById('root').hasAttribute('inert')")
 def material_state():
  return evaluate("""(() => {const r=document.querySelector('.v2-material-files').getBoundingClientRect();return {top:r.top,width:r.width,scroll:document.querySelector('.v2-material-file-scroll').scrollTop,focus:document.activeElement?.textContent,inlinePreview:!!document.querySelector('.v2-material-preview'),overflow:document.documentElement.scrollWidth>innerWidth+1}})()""")
 def check():
  try:
   wait("Boolean(document.querySelector('.ai-composer-input'))")
   evaluate("""(() => {
     const original=window.pywebview.api.invoke;window.__previewCalls=[];window.__fileActions=[];
     window.pywebview.api.invoke=async(a,p={})=>{
       if(a==='material_tree'){
         const r=await original(a,p),category=r.data.root.children[0].children[0].children[0];
         category.children=[{id:'test-folder',kind:'folder',name:'下一层文件夹',children:[]},...Array.from({length:26},(_,i)=>({id:'file-'+i,kind:'file',name:i+' 课程阅读笔记.md',source_id:'md-'+i,course_id:'1001',size:4200,can_preview:true,can_open:true,download_status:'downloaded',local_path:'/synthetic/notes.md'})),...['pptx','docx','pdf'].map(ext=>({id:'office-'+ext,kind:'file',name:'Office 课件.'+ext,source_id:'office-'+ext,course_id:'1001',size:12800,can_preview:true,can_open:true,download_status:'downloaded',local_path:'/synthetic/document.'+ext}))];
         return r;
       }
       if(a==='material_preview'){
         window.__previewCalls.push(p.source_id);
         if(p.source_id==='office-pdf')return {ok:true,data:{kind:'pdf',name:'Office 课件.pdf',data_url:window.__fixturePDF}};
         return {ok:true,data:{kind:'text',name:'课程阅读笔记.md',text:'# 课程阅读笔记\\n\\n'+('这是一段用于验证悬浮阅读与文字排版的示例内容。\\n\\n'.repeat(80))}};
       }
       if(a==='media_preview'){
         window.__previewCalls.push(p.source_id);
         return {ok:true,data:p.source_id==='office-pptx'?{kind:'pptx',name:'Office 课件.pptx',slides:[{number:1,text:'第一页：线性模型与监督学习'},{number:2,text:'第二页：损失函数与训练样本'}]}:{kind:'docx',name:'Office 课件.docx',text:'Word 课程文档正文'}};
       }
       if(['material_open','material_reveal','material_download'].includes(a)){window.__fileActions.push({a,p});return {ok:true,data:{status:'opened'}};}
       if(a==='compiled_knowledge_read'){const r=await original(a,p);r.data.text+='\\n\\n'+('详细阅读内容与公式推导。\\n\\n'.repeat(100));r.data.total_chars=r.data.text.length;return r;}
       return original(a,p);
     };
   })()""")
   # 合成 PDF 内嵌到验收桥接，无真实课程或网络请求。
   import base64
   parts=[b'%PDF-1.4\n'];offsets=[0]
   objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
   stream=b'BT /F1 20 Tf 50 780 Td (Synthetic PDF preview) Tj ET'
   objects.append(b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream')
   for index,obj in enumerate(objects,1):offsets.append(sum(map(len,parts)));parts.append(str(index).encode()+b' 0 obj\n'+obj+b'\nendobj\n')
   xref=sum(map(len,parts));parts.append(b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{offset:010d} 00000 n \n'.encode() for offset in offsets[1:])+b'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n'+str(xref).encode()+b'\n%%EOF')
   evaluate('window.__fixturePDF='+json.dumps('data:application/pdf;base64,'+base64.b64encode(b''.join(parts)).decode()))
   evaluate("location.hash='/materials'")
   for level in range(3):
    wait("Boolean(document.querySelector('.v2-material-files .v2-material-folder-row'))")
    evaluate("document.querySelector('.v2-material-files .v2-material-folder-row').click()")
   wait("document.querySelectorAll('.v2-material-row').length===29")
   assert evaluate("!document.querySelector('.v2-material-tree').textContent.includes('课程阅读笔记.md') && !!document.querySelector('.v2-material-files .v2-material-folder-row')")
   evaluate("document.querySelector('.v2-material-file-scroll').scrollTop=160")
   initial=material_state();assert not initial['inlinePreview'] and not initial['overflow'],initial;capture('materials-files')
   evaluate("document.querySelectorAll('.v2-material-row>button')[4].focus();document.querySelectorAll('.v2-material-row>button')[4].click()")
   wait("Boolean(document.querySelector('.v2-file-preview-body h1'))");check_preview('materials-markdown')
   close_preview();after=material_state();assert after['top']==initial['top'] and after['width']==initial['width'] and after['scroll']==initial['scroll'],(initial,after)
   assert evaluate("document.activeElement===document.querySelectorAll('.v2-material-row>button')[4]")
   evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true,cancelable:true}))")
   wait("window.__previewCalls.length===2");close_preview();result['checks'].append(dict(state='space-preview-and-return',stable=True))
   for extension in ('pptx','docx','pdf'):
     evaluate("[...document.querySelectorAll('.v2-material-row>button')].find(e=>e.textContent.includes("+json.dumps('Office 课件.'+extension)+")).focus();[...document.querySelectorAll('.v2-material-row>button')].find(e=>e.textContent.includes("+json.dumps('Office 课件.'+extension)+")).click()")
     wait("!!document.querySelector('.v2-file-preview-content')")
     if extension=='pdf':
       wait("document.querySelector('.v2-file-preview-dialog iframe')?.getAttribute('src')?.startsWith('blob:')")
       threading.Event().wait(.7)
       assert evaluate("document.querySelector('.v2-file-preview-dialog iframe').getBoundingClientRect().height>450")
     else:
       wait("document.querySelector('.v2-file-preview-content').textContent.includes("+json.dumps('第一页' if extension=='pptx' else 'Word 课程文档正文')+")")
     check_preview('materials-'+extension);close_preview()
   evaluate("location.hash='/knowledge'")
   wait("Boolean(document.querySelector('.v2-knowledge-file-layout'))")
   assert evaluate("!document.querySelector('.compiled-knowledge-reading-pane') && !document.querySelector('.v2-file-preview-dialog')")
   evaluate("document.querySelector('.v2-knowledge-folder-row').click()")
   wait("document.querySelectorAll('.v2-knowledge-file-row').length===20")
   assert evaluate("document.querySelector('.v2-knowledge-folder-row').textContent.includes('练习') && !document.querySelector('.v2-knowledge-directory').textContent.includes('.pptx.md')")
   capture('knowledge-files')
   evaluate("document.querySelectorAll('.v2-knowledge-file-row')[3].focus();document.querySelectorAll('.v2-knowledge-file-row')[3].click()")
   wait("!!document.querySelector('.compiled-knowledge-reader h1')");check_preview('knowledge-markdown')
   evaluate("document.querySelector('.compiled-knowledge-reader').scrollTop=244;document.querySelector('.compiled-knowledge-reader').dispatchEvent(new Event('scroll',{bubbles:true}))")
   close_preview();assert evaluate("document.activeElement===document.querySelectorAll('.v2-knowledge-file-row')[3]")
   evaluate("document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true,cancelable:true}))")
   wait("document.querySelector('.compiled-knowledge-reader')?.scrollTop===244")
   result['checks'].append(dict(state='knowledge-reading-position-restored',scroll=244));close_preview()
   click('下一组结果');wait("document.querySelectorAll('.v2-knowledge-file-row').length===4")
   click('返回首组结果');wait("document.querySelectorAll('.v2-knowledge-file-row').length===20")
   evaluate("document.querySelector('.v2-knowledge-folder-row').click()")
   wait("document.querySelectorAll('.v2-knowledge-file-row').length===4")
   click('返回上级知识目录');wait("document.querySelectorAll('.v2-knowledge-file-row').length===20")
   result['checks'].append(dict(state='directory-and-pagination',ok=True))
   result['ok']=True
  except Exception as e:
   result.update(error=str(e),traceback=traceback.format_exc(),diagnostic=evaluate("({text:document.body.textContent.slice(-5000),errors:window.__v2Errors})"));capture('failure')
  finally:window.destroy()

 webview.start(check,gui='cocoa',private_mode=True)
 args.result.write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 1
if __name__=='__main__':raise SystemExit(main())

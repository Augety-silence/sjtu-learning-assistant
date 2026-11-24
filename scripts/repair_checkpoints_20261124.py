"""仅恢复本轮已核查失败与大PDF，原件/旧稿不清理，付费总账本最多64次。"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.local_settings import SettingsStore
from sjtu_learning_assistant.knowledge_compiler import _atomic_write, KnowledgeResponseError
from sjtu_learning_assistant.knowledge_documents import DocumentCompiler, CompilationDeferred, CONTRACT, VISION_MODEL, digest, target_lock, safe_destination
from sjtu_learning_assistant.course_markdown_sync import CourseMarkdownSync
from sjtu_learning_assistant.knowledge_summary import CONCISE_REVIEW_PROMPT, SUMMARY_PROMPT, CONCISE_COMPRESSION_PROMPT
from sjtu_learning_assistant.transcript_notes import compile_summary, lesson_markdown, PROMPT_VERSION, SummaryDeferred

BACKUPS=APP_SUPPORT_DIR / 'recovery-backups/checkpoint-repair-20261124'
LEDGER=BACKUPS / 'requests.json'
MAX_REQUESTS=64
LOCK=threading.RLock()

def backup(path: Path, name: str):
    destination=BACKUPS/name
    if not destination.exists():
        if path.is_dir():
            shutil.copytree(path,destination,symlinks=True)
        else:
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,destination)
    return destination

def pending_note_directories(root: Path, only: str | None = None):
    directories=[]
    for directory in root.iterdir():
        if only and directory.name != only:
            continue
        manifest_path=directory/'manifest.json'
        # 下载暂存目录可能尚未有manifest，不应阻断已登记的5份失败稿。
        if directory.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        manifest=json.loads(manifest_path.read_text())
        notes=manifest.get('lesson_notes',{})
        if notes.get('status')=='pending' and notes.get('auto_paused'):
            directories.append(directory)
    return directories

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=('courses','transcripts','large'))
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--slice-calls',type=int,default=8)
    parser.add_argument('--only',help='仅恢复指定文件名或字幕目录ID，不新增或重置额度')
    args=parser.parse_args()
    settings=SettingsStore().load()
    if settings.ai_enabled:
        raise RuntimeError('先暂停后台新请求，避免与定向恢复同时改写')
    source=Path(settings.archive_root).resolve()
    target=source/'Knowledge Markdown'
    with sqlite3.connect('file:'+str(APP_SUPPORT_DIR/'data/app.db')+'?mode=ro',uri=True) as db:
        active=set(row[0] for row in db.execute("SELECT local_path FROM course_files WHERE is_active=1 AND download_status='downloaded' AND local_path IS NOT NULL"))
    BACKUPS.mkdir(parents=True,exist_ok=True)
    if not LEDGER.exists():
        with LEDGER.open('x') as out:
            json.dump(dict(requests=0,limit=MAX_REQUESTS,results=[]),out)
    history=json.loads(LEDGER.read_text())
    if history.get('limit') != MAX_REQUESTS:
        raise RuntimeError('恢复账本不一致，不重置额度')
    clients={}
    def emit(value):
        print(json.dumps(value,ensure_ascii=False),flush=True)
    def save_history():
        _atomic_write(LEDGER,json.dumps(history,ensure_ascii=False,indent=2))
    def client(model):
        with LOCK:
            if model not in clients:
                from sjtu_learning_assistant.ai_classifier import OpenAIClassificationClient
                from sjtu_learning_assistant.ai_keychain import get_ai_api_key
                import httpx
                clients[model]=OpenAIClassificationClient(api_key=get_ai_api_key(),base_url=settings.ai_base_url,model=model,
                    timeout=httpx.Timeout(connect=15,read=240,write=60,pool=15))
            return clients[model]
    def runner(model,system,content,*,token_limit=None):
        if args.offline:
            raise CompilationDeferred('离线复验需要新模型请求；检查点保留。')
        max_tokens=1024 if system.startswith(CONCISE_REVIEW_PROMPT) else 6500 if system.startswith(CONCISE_COMPRESSION_PROMPT) else 7500 if system.startswith(SUMMARY_PROMPT) else 8192
        if token_limit is not None:
            max_tokens=min(max_tokens,token_limit)
        def complete():
            with LOCK:
                if history['requests']>=MAX_REQUESTS:
                    raise CompilationDeferred('本轮64次预约额度已用尽，未重置。')
                history['requests']+=1
                number=history['requests']
                save_history()
            emit(dict(event='request',number=number,model=model))
            result=client(model).chat_completion([{'role':'system','content':system},{'role':'user','content':content}],
                max_tokens=max_tokens,temperature=0.1,system_prompt=False,knowledge_compilation=True,
                response_format={'type':'json_object'} if 'json' in system.casefold() else None)
            content_result=result.get('content')
            # 全部付费响应另存追加文件，任何下游校验/取消不会丢失。
            _atomic_write(BACKUPS/f'paid-response-{number:03d}.json',json.dumps(result,ensure_ascii=False))
            if result.get('finish_reason')=='length' or (result.get('response_metadata') or {}).get('finish_reason')=='length':
                raise KnowledgeResponseError('知识库响应被截断；未发布。',content=content_result or '')
            if type(content_result) is not str or not content_result.strip():
                raise ValueError('模型响应为空')
            return content_result.strip()
        if 'json' in system.casefold():
            from sjtu_learning_assistant.compilation_cache import CompilationResponseCache
            from sjtu_learning_assistant.ai_keychain import get_ai_api_key
            identity=(model,settings.ai_base_url,hashlib.sha256(get_ai_api_key().encode()).hexdigest())
            cache=CompilationResponseCache(APP_SUPPORT_DIR/'compilation-responses.sqlite3')
            return cache.get_or_compute(cache.identity(identity,system,content,max_tokens),complete)
        return complete()
    def record(value):
        with LOCK:
            history['results'].append(value)
            save_history()
        emit(value)
    try:
        if args.kind in ('courses','large'):
            with target_lock(target):
                marker=target/'99_System/Course-Sync.json'
                backup(marker,'Course-Sync.json')
                state=json.loads(marker.read_text())
                def persist():
                    _atomic_write(marker,json.dumps(state,ensure_ascii=False,indent=2)+'\n')
                for relative,item in state['items'].items():
                    path=source/relative
                    if args.only and path.name != args.only:
                        continue
                    is_large=path.name in ('玉仁 and 云君 - 大模型基础（浙江大学）.pdf','鑫 et al. - 大语言模型（人民大学）.pdf')
                    if (str(path) not in active or not path.is_file() or item.get('status') not in ('pending','failed')
                            or (args.kind=='large')!=is_large):
                        continue
                    if args.kind=='courses' and not (item.get('auto_recovery_version') or path.name=='Dantzig-Wolfe Decomposition [14294986].pdf'):
                        continue
                    source_hash=digest(path.read_bytes())
                    source_id=digest(relative.encode())[:16]
                    revision=digest((source_hash+CONTRACT+VISION_MODEL).encode())[:16]
                    directory=target/'90_Inbox/Documents'/source_id/revision
                    if directory.is_dir():
                        backup(directory,'documents/'+source_id+'/'+revision)
                    mirror=safe_destination(target,target/'Courses'/Path(relative+'.md'))
                    if mirror.is_file():
                        backup(mirror,'mirrors/'+relative+'.md')
                        if digest(mirror.read_bytes()) not in {item.get('mirror_hash'),item.get('pending_hash')}:
                            record(dict(file=path.name,status='protected',error='人工修改或未登记镜像，未覆盖'))
                            continue
                    # 本轮调度与旧重试额度分开追加记录，不清attempts、不清auto_paused。
                    item['checkpoint_repair_20261124']=dict(dispatched_at=datetime.now(timezone.utc).isoformat(),offline=args.offline)
                    persist()
                    try:
                        note=DocumentCompiler(runner,threading.Event(),slice_calls=args.slice_calls,text_first=True,
                            review_percent=20,parallel_batches=3,auto_recovery=True,content_recovery=args.kind=='courses').compile(source,path,target)
                        assert source_hash==digest(path.read_bytes())
                        def publish(row,destination,body):
                            if destination.is_file() and digest(destination.read_bytes()) not in {row.get('mirror_hash'),row.get('pending_hash')}:
                                raise ValueError('镜像变化，未覆盖')
                            _atomic_write(destination,body)
                            row['mirror_hash']=digest(body.encode())
                        CourseMarkdownSync._publish_compiled_note(note,mirror,target,item,publish)
                        for key in ('auto_paused','error','retry_after'):
                            item.pop(key,None)
                        persist()
                        record(dict(file=path.name,status='completed'))
                    except Exception as exc:
                        record(dict(file=path.name,status='deferred' if isinstance(exc,CompilationDeferred) else 'failed',error=str(exc),category=getattr(exc,'category','')))
        else:
            directories=pending_note_directories(APP_SUPPORT_DIR/'transcripts/v1/videos',args.only)
            def recover_notes(directory):
                path=directory/'manifest.json'
                manifest=json.loads(path.read_text())
                backup(directory,'transcripts/'+directory.name)
                raw=directory/'raw.vtt'
                if raw.is_symlink() or (directory/'lesson-checkpoints').is_symlink():
                    raise ValueError('字幕或检查点为符号链接，未改写')
                raw_hash=digest(raw.read_bytes())
                if raw_hash!=manifest.get('raw_sha256'):
                    raise ValueError('字幕源哈希不一致')
                folders=list((directory/'lesson-checkpoints').iterdir())
                if len(folders)!=1:
                    raise ValueError('字幕检查点服务身份不唯一')
                cache_dir=folders[0]
                if cache_dir.is_symlink():
                    raise ValueError('字幕检查点路径不安全')
                def cached(key):
                    p=cache_dir/(key+'.json')
                    if p.is_symlink():
                        raise ValueError('字幕缓存为符号链接，未读取')
                    return json.loads(p.read_text()) if p.is_file() else None
                def commit(key,value):
                    p=cache_dir/(key+'.json')
                    if not p.exists():
                        _atomic_write(p,json.dumps(value,ensure_ascii=False))
                class NotesClient:
                    def chat_completion(self,messages,**kwargs):
                        return {'content':runner('deepseek-chat',messages[0]['content'],messages[1]['content'],token_limit=kwargs.get('max_tokens')),'finish_reason':'stop'}
                body,report=compile_summary(raw.read_text(),NotesClient(),cached=cached,commit=commit,max_calls=args.slice_calls,
                    review_percent=20,auto_recovery=True,content_recovery=True,recovery_epoch=int(manifest['lesson_notes'].get('recovery_epoch') or 0))
                if raw_hash!=digest(raw.read_bytes()):
                    raise ValueError('字幕源变化，未发布')
                manifest=json.loads(path.read_text())
                records=manifest['artifacts']
                for kind,name in (('original','original.md'),('lesson','lesson.md'),('lesson_summary','lecture-summary.md')):
                    p=directory/name
                    if p.is_symlink() or p.exists() and digest(p.read_bytes())!=records.get(kind,{}).get('sha256'):
                        raise ValueError('字幕稿人工修改，未覆盖')
                for kind,name,text in (('lesson_summary','lecture-summary.md',body),('lesson','lesson.md',lesson_markdown(manifest,(directory/'original.md').read_text(),body))):
                    _atomic_write(directory/name,text)
                    records[kind]=dict(path=name,sha256=digest(text.encode()))
                manifest['lesson_notes'].update(status='completed',prompt_version=PROMPT_VERSION,raw_sha256=raw_hash,report=report)
                for key in ('auto_paused','error_code','retry_after'):
                    manifest['lesson_notes'].pop(key,None)
                _atomic_write(path,json.dumps(manifest,ensure_ascii=False,indent=2))
                return dict(transcript=directory.name,status='completed',chars=len(body))
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures={pool.submit(recover_notes,d):d for d in directories}
                for future in as_completed(futures):
                    try:
                        record(future.result())
                    except Exception as exc:
                        deferred=isinstance(exc,(SummaryDeferred,CompilationDeferred))
                        record(dict(transcript=futures[future].name,status='deferred' if deferred else 'failed',
                            error=str(exc) or ('已保存检查点，本次调用切片结束。' if deferred else type(exc).__name__),
                            error_type=type(exc).__name__))
    finally:
        for value in clients.values():
            value.close()
    emit(dict(event='done',requests_total=history['requests'],limit=MAX_REQUESTS))

if __name__=='__main__':
    main()

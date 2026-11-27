"""临时转 MD 的范围、保护、中断恢复与检索；不连接真实账号或模型。"""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from unittest.mock import Mock
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sjtu_learning_assistant.current_term_markdown import CurrentTermMarkdown, ready_records, SOURCE, file_fingerprint
from sjtu_learning_assistant.current_term_md_conversion import convert_local
from sjtu_learning_assistant.compiled_knowledge import CompiledKnowledge
from sjtu_learning_assistant.local_settings import LocalSettings, SettingsStore
from sjtu_learning_assistant.models import Base, Course, CourseFile, SyncState

@pytest.fixture
def setup(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'test.db')); Base.metadata.create_all(engine)
    root=tmp_path/'archive'; root.mkdir()
    settings=LocalSettings(archive_root=str(root),current_term_md_enabled=True)
    stamp=datetime(2026,11,25,tzinfo=timezone.utc); payload='本学期检索关键词：电磁感应\n'.encode()
    with Session(engine) as s,s.begin():
        for i,label in enumerate(['2025-2026 Fall','2026-2027 Fall',None],1):
            s.add(Course(id=i,source_id=str(i),name='课程'+str(i),term_name=label,raw_data={})); s.flush()
            s.add(CourseFile(id=i,source_id=str(i),course_id=i,display_name='lecture.txt',size=len(payload),source_updated_at=stamp,last_seen_at=stamp,is_active=True))
    def download(source_id,directory,size,cancelled):
        path=directory/'source.bin'; path.write_bytes(payload); digest=hashlib.sha256(payload).hexdigest()
        (directory/'download.json').write_text(json.dumps(dict(source_id=source_id,size=len(payload),sha256=digest)), encoding='utf-8')
        return path,len(payload),digest
    downloader=Mock(side_effect=download)
    def make(**kwargs): return CurrentTermMarkdown(engine,lambda:settings,downloader,**kwargs)
    yield engine,root,settings,downloader,make
    engine.dispose()

def job(engine):
    with Session(engine) as s:
        return json.loads(s.scalar(select(SyncState.cursor).where(SyncState.source==SOURCE,SyncState.resource!='scan')))

def test_latest_scope_cleanup_and_search_without_original(setup):
    engine,root,settings,downloader,make=setup
    result=make().run_once(); assert result['total']==result['completed']==1
    assert downloader.call_args.args[0]=='2'
    item=job(engine); assert item['cleaned'] and not (root/item['temporary_path']).exists()
    with Session(engine) as s:
        assert all(f.local_path is None and f.download_status=='pending' for f in s.scalars(select(CourseFile)))
    ready=ready_records(engine,root); assert len(ready)==1 and ready[0]['course_id']=='2'
    knowledge=CompiledKnowledge(lambda:settings,root.parent/'projects.json',engine=engine)
    found=knowledge.search(query='电磁感应',term_id=ready[0]['term_id'],course_id='2')
    assert found['count']==1 and found['items'][0]['status']=='compiled'
    assert '电磁感应' in knowledge.read(found['items'][0]['id'])['text']
    assert make().run_once()['completed']==1; downloader.assert_called_once()

def test_existing_original_never_downloaded_or_deleted(setup):
    engine,root,_,downloader,make=setup; original=root/'mine.txt'; original.write_bytes(b'preserved'); before=file_fingerprint(original)
    with Session(engine) as s,s.begin():
        f=s.get(CourseFile,2); f.local_path=str(original); f.download_status='downloaded'
    assert make().run_once()['existing']==1; downloader.assert_not_called(); assert file_fingerprint(original)==before

def test_unknown_term_has_no_download_fallback(setup):
    engine,_,_,downloader,make=setup
    with Session(engine) as s,s.begin():
        for c in s.scalars(select(Course)): c.term_name=None; c.raw_data={}
    assert make().run_once()['total']==0; downloader.assert_not_called()

def test_failed_conversion_keeps_source_and_bounds_retry(setup):
    engine,root,_,downloader,make=setup; broken=Mock(side_effect=ValueError('invalid'))
    assert make(converter=broken).run_once()['failed']==1
    item=job(engine); assert (root/item['temporary_path']).is_file() and not ready_records(engine,root)
    make(converter=broken).run_once(); broken.assert_called_once(); downloader.assert_called_once()
    for _ in range(2):
        with Session(engine) as s,s.begin():
            state=s.scalar(select(SyncState).where(SyncState.resource==item['key'])); j=json.loads(state.cursor); j['retry_after']=0; state.cursor=json.dumps(j)
        make(converter=broken).run_once()
    assert job(engine)['attempts']==3; make(converter=broken).run_once(); assert broken.call_count==3 and downloader.call_count==1

def test_restart_after_md_publication_only_cleans(setup):
    engine,root,_,downloader,make=setup; first=make(); first._cleanup=Mock(side_effect=InterruptedError)
    assert first.run_once()['pending']==1; item=job(engine); assert item['state']=='md_ready' and (root/item['temporary_path']).exists()
    converter=Mock(side_effect=AssertionError('reuse ready MD')); assert make(converter=converter).run_once()['completed']==1
    assert not (root/item['temporary_path']).exists(); converter.assert_not_called(); downloader.assert_called_once()

def test_restart_after_download_receipt_no_second_download(setup):
    engine,root,_,downloader,make=setup; actual=downloader.side_effect
    def killed(*args): actual(*args); raise KeyboardInterrupt()
    downloader.side_effect=killed
    with pytest.raises(KeyboardInterrupt): make().run_once()
    assert job(engine)['state']=='downloading'; downloader.side_effect=AssertionError('recover download')
    assert make().run_once()['completed']==1; downloader.assert_called_once()

def test_manual_download_race_preserves_both_copies(setup):
    engine,root,_,_,make=setup
    def convert(*args):
        result=convert_local(*args); original=root/'manual.txt'; original.write_text('用户主动下载原件', encoding='utf-8')
        with Session(engine) as s,s.begin():
            f=s.get(CourseFile,2); f.local_path=str(original); f.download_status='downloaded'
        return result
    assert make(converter=convert).run_once()['completed']==1; item=job(engine)
    assert item['state']=='retained' and (root/item['temporary_path']).exists(); assert (root/'manual.txt').read_text(encoding='utf-8')=='用户主动下载原件'

@pytest.mark.parametrize('change',['md','source','symlink','version','asset'])
def test_cleanup_rejects_changed_files_and_versions(setup,change):
    engine,root,_,_,make=setup; first=make(); first._cleanup=Mock(side_effect=InterruptedError); first.run_once(); item=job(engine)
    source=root/item['temporary_path']; md=root/item['md_path']
    if change=='md': md.write_text('用户修改的 MD', encoding='utf-8')
    elif change=='source': source.write_text('用户修改的原件', encoding='utf-8')
    elif change=='symlink':
        other=root/'protected.txt'; other.write_text('保留', encoding='utf-8'); source.unlink(); source.symlink_to(other)
    elif change=='version':
        with Session(engine) as s,s.begin(): s.get(CourseFile,2).source_updated_at+=timedelta(seconds=1)
        assert not ready_records(engine,root) and source.exists(); return
    else:
        asset=md.parent/'assets'/'image.png'; asset.parent.mkdir(); asset.write_bytes(b'original asset')
        with Session(engine) as s,s.begin():
            state=s.scalar(select(SyncState).where(SyncState.resource==item['key'])); j=json.loads(state.cursor); j['assets']={asset.relative_to(root).as_posix():file_fingerprint(asset)}; state.cursor=json.dumps(j)
        asset.write_bytes(b'changed asset!')
    assert make().run_once()['failed']==1; assert source.exists() or source.is_symlink()
    if change=='symlink': assert (root/'protected.txt').read_text(encoding='utf-8')=='保留'

def test_pdf_page_images_survive_cleanup(setup):
    engine,root,_,downloader,make=setup
    from pypdf import PdfWriter
    original=root/'fixture.pdf'; writer=PdfWriter(); writer.add_blank_page(width=300,height=300)
    with original.open('wb') as output: writer.write(output)
    payload=original.read_bytes(); original.unlink()
    with Session(engine) as s,s.begin(): f=s.get(CourseFile,2); f.display_name='pages.pdf'; f.size=len(payload)
    def download(source_id,directory,size,cancelled):
        source=directory/'source.bin'; source.write_bytes(payload); return source,len(payload),hashlib.sha256(payload).hexdigest()
    downloader.side_effect=download
    assert make().run_once()['completed']==1; item=job(engine); assert item['units']==1 and item['assets']
    assert all((root/p).is_file() for p in item['assets']) and not (root/item['temporary_path']).exists()

def test_material_view_and_original_download_option_after_cleanup(setup):
    engine,root,settings,_,make=setup
    from sjtu_learning_assistant.dashboard_service import DashboardService
    make().run_once(); store=SettingsStore(root.parent/'settings.json'); store.save(settings); runner=Mock(return_value=Mock(returncode=0))
    service=DashboardService(engine,settings_store=store,command_runner=runner,local_projects_path=root.parent/'projects.json')
    assert 'markdown_ready' in str(service.material_tree()); assert '电磁感应' in service.material_preview('2')['text']
    assert service.open_material('2')['status']=='opened' and runner.call_args.args[0][-1].endswith('document.md')
    assert '--no-download' in service._fallback_sync_command()
    progress=service.markdown_sync_progress()
    assert progress['courses']['inventory']['markdown_only']==1
    assert progress['courses']['inventory']['not_downloaded']==2
    service.close()

def test_legacy_settings_upgrade_keeps_ai_and_asr_pause(tmp_path):
    payload=LocalSettings(ai_enabled=True,course_markdown_enabled=False,recording_asr_paused=True).to_dict(); payload.pop('current_term_md_enabled')
    path=tmp_path/'settings.json'; path.write_text(json.dumps(payload), encoding='utf-8'); loaded=SettingsStore(path).load()
    assert not loaded.current_term_md_enabled and not loaded.course_markdown_enabled and loaded.ai_enabled and loaded.recording_asr_paused

@pytest.mark.parametrize('suffix',['.pptx','.docx','.xlsx'])
def test_ooxml_text_and_all_assets_saved_before_cleanup(setup,suffix):
    engine,root,_,downloader,make=setup
    from office_fixtures import build_pptx,build_docx,build_xlsx
    builder={'.pptx':build_pptx,'.docx':build_docx,'.xlsx':build_xlsx}[suffix]
    original=builder(root/('fixture'+suffix)); payload=original.read_bytes(); original.unlink()
    with Session(engine) as s,s.begin(): f=s.get(CourseFile,2); f.display_name='fixture'+suffix; f.size=len(payload)
    def download(source_id,directory,size,cancelled):
        path=directory/'source.bin'; path.write_bytes(payload); return path,len(payload),hashlib.sha256(payload).hexdigest()
    downloader.side_effect=download
    assert make().run_once()['completed']==1
    item=job(engine); text=(root/item['md_path']).read_text(encoding='utf-8')
    assert item['units']>0 and '未进行 AI' in text and not (root/item['temporary_path']).exists()
    assert all((root/relative).is_file() for relative in item['assets'])
    if suffix=='.pptx': assert '讲解重点' in text and item['assets']

def test_incomplete_extraction_keeps_original(setup,monkeypatch):
    engine,root,_,downloader,make=setup
    from sjtu_learning_assistant.document_text import DocumentText
    monkeypatch.setattr('sjtu_learning_assistant.current_term_md_conversion.extract_document',lambda *a,**k:DocumentText('pdf','pypdf','页',('部分',),2,True))
    with Session(engine) as s,s.begin(): s.get(CourseFile,2).display_name='incomplete.pdf'
    assert make().run_once()['failed']==1
    assert (root/job(engine)['temporary_path']).exists() and not ready_records(engine,root)

def test_batch_skips_ready_md_and_explicit_download_remains_independent(setup):
    engine,root,_,_,make=setup; make().run_once()
    from sjtu_learning_assistant.archive_service import ArchiveService
    service=ArchiveService(engine,archive_root=root)
    context=service._load_contexts(source_id='2')[0]
    service._download_one=Mock(side_effect=AssertionError('no batch redownload'))
    assert service._download_contexts([context])[0].status=='unchanged'; service._download_one.assert_not_called()

def test_temporary_downloader_validates_receipt_and_never_updates_original_records(setup):
    engine,root,_,_,_=setup
    import httpx
    from sjtu_learning_assistant.archive_service import ArchiveService
    payload=b'content'; calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(200,json={'url':'https://objects.example.edu/file/2'}) if req.url.host=='oc.sjtu.edu.cn' else httpx.Response(200,content=payload)
    canvas=httpx.Client(base_url='https://oc.sjtu.edu.cn',transport=httpx.MockTransport(handler))
    service=ArchiveService(engine,canvas,archive_root=root,download_client_factory=lambda:httpx.Client(transport=httpx.MockTransport(handler)))
    directory=root/'isolated'; path,size,digest=service.download_temporary('2',directory,len(payload))
    assert path.read_bytes()==payload and size==len(payload) and digest==hashlib.sha256(payload).hexdigest()
    assert json.loads((directory/'download.json').read_text(encoding='utf-8'))['sha256']==digest
    with Session(engine) as s: assert s.get(CourseFile,2).local_path is None and s.get(CourseFile,2).download_attempts==0
    canvas.close()

def test_running_queue_discovers_new_current_term_file_and_stops_cleanly(setup):
    engine,root,_,downloader,make=setup
    import time
    from threading import Event
    worker=make(interval=.03)
    def wait_for(predicate):
        deadline=time.monotonic()+4
        while not predicate():
            assert time.monotonic()<deadline,'后台新增文件发现超时'
            Event().wait(.01)
    worker.start()
    try:
        wait_for(lambda:worker.status()['completed']==1)
        with Session(engine) as s,s.begin():
            original=s.get(CourseFile,2)
            s.add(CourseFile(source_id='new-file',course_id=2,display_name='新增.txt',size=original.size,
                source_updated_at=original.source_updated_at,last_seen_at=datetime.now(timezone.utc),is_active=True))
        worker.notify()
        wait_for(lambda:worker.status()['total']==2 and worker.status()['completed']==2)
        assert downloader.call_count==2 and len(ready_records(engine,root))==2
    finally: worker.close()
    assert not worker._thread.is_alive()

"""自动安装前暂停新请求并等待在途响应安全落盘。"""
import sqlite3
import time
from urllib.parse import quote
from sjtu_learning_assistant.database import APP_SUPPORT_DIR
from sjtu_learning_assistant.update_service import UpdateServiceError


def prepare_update_shutdown(service, scheduler, *, timeout=180):
    saved = service.settings_store.load()
    service._updating = True
    service.settings_store.update(dict(ai_enabled=False))
    scheduler.stop()
    def restore():
        service.settings_store.update(dict(ai_enabled=saved.ai_enabled, recording_asr_paused=saved.recording_asr_paused))
    recording = getattr(service, '_recording_asr_instance', None)
    try:
        # 先通知现有自动编译取消排队；失败时可以继续 notify 恢复，避免关闭后失去重试。
        for name in ('_course_markdown_instance', '_current_term_md_instance', '_subtitle_sync_instance'):
            worker = getattr(service, name, None)
            if worker:
                worker.notify(settings_changed=True) if name == '_course_markdown_instance' else worker.notify()
        compiler = getattr(service, '_knowledge_compiler_instance', None)
        if compiler:
            compiler.cancel()
        recording = getattr(service, '_recording_asr_instance', None)
        if recording:
            recording.pause()
        deadline = time.monotonic() + timeout
        runtime = APP_SUPPORT_DIR / 'ai-runtime.sqlite3'
        while True:
            active = 0
            if runtime.is_file():
                with sqlite3.connect('file:' + quote(str(runtime)) + '?mode=ro', uri=True) as db:
                    active = db.execute('SELECT count(*) FROM requests WHERE ended IS NULL AND lease>?', (time.time(),)).fetchone()[0]
            with service._ai_task_lock:
                chatting = any(thread.is_alive() for thread in service._ai_task_threads.values())
            if not active and not chatting and not service._sync_lock.locked():
                if service.prepare_recording_asr_quit() is False:
                    raise UpdateServiceError('录播转写仍在保存，请稍后重试。')
                return restore
            if time.monotonic() > deadline:
                raise UpdateServiceError('当前任务仍在执行，更新已下载；请在任务结束后重新检查更新。')
            time.sleep(.25)
    except Exception:
        restore()
        service._updating = False
        scheduler.start()
        for name in ('_course_markdown_instance', '_current_term_md_instance', '_subtitle_sync_instance'):
            worker = getattr(service, name, None)
            if worker:
                worker.notify(settings_changed=True) if name == '_course_markdown_instance' else worker.notify()
        if recording and not saved.recording_asr_paused:
            recording.resume()
        raise

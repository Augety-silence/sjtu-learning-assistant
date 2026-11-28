"""有意义的升级边界测试：字节校验、取消、平台选择与原子回退。"""
import hashlib
import io
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
import httpx
from sjtu_learning_assistant.automatic_update import download_package, AutomaticUpdater
from sjtu_learning_assistant.update_service import DownloadPlan, UpdateService, UpdateServiceError, ChecksumMismatchError, UnsafeUpdateURLError
from sjtu_learning_assistant.update_installer import atomic_install, extract_windows_package

URL = 'https://github.com/Augety-silence/sjtu-learning-assistant/releases/download/v2.0.1/app.dmg'

class AutomaticUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.payload = b'official-package-bytes'
        self.plan = DownloadPlan('2.0.1', 'app.dmg', URL, len(self.payload), hashlib.sha256(self.payload).hexdigest(), None, URL)
    def tearDown(self):
        self.temp.cleanup()
    def service(self, handler):
        return UpdateService('2.0.0', transport=httpx.MockTransport(handler))
    def test_exact_bytes_are_verified_and_atomically_completed(self):
        progress=[]
        result=download_package(self.service(lambda _: httpx.Response(200, content=self.payload)), self.plan, self.root/'package', progress.append, threading.Event())
        self.assertEqual(result.read_bytes(), self.payload)
        self.assertEqual(progress[-1], len(self.payload))
        self.assertFalse((self.root/'package.part').exists())
    def test_wrong_hash_never_produces_installable_file(self):
        with self.assertRaises(ChecksumMismatchError):
            download_package(self.service(lambda _: httpx.Response(200, content=b'X'*len(self.payload))), self.plan, self.root/'package', lambda _: None, threading.Event())
        self.assertFalse((self.root/'package').exists())
        self.assertFalse((self.root/'package.part').exists())
    def test_untrusted_redirect_is_rejected(self):
        with self.assertRaises(UnsafeUpdateURLError):
            download_package(self.service(lambda _: httpx.Response(302, headers={'Location':'https://evil.example/package'})), self.plan, self.root/'package', lambda _: None, threading.Event())
    def test_truncated_package_is_rejected(self):
        with self.assertRaises(UpdateServiceError):
            download_package(self.service(lambda _: httpx.Response(200, content=self.payload[:-1])), self.plan, self.root/'package', lambda _: None, threading.Event())
    def test_cancel_removes_partial_download(self):
        cancel=threading.Event(); cancel.set()
        with self.assertRaisesRegex(UpdateServiceError, '取消'):
            download_package(self.service(lambda _: httpx.Response(200, content=self.payload)), self.plan, self.root/'package', lambda _: None, cancel)
        self.assertFalse((self.root/'package.part').exists())
    def test_duplicate_start_keeps_single_worker(self):
        entered, release = threading.Event(), threading.Event()
        updater = AutomaticUpdater(self.service(lambda _: httpx.Response(200, content=self.payload)), self.root, self.root/'app', lambda _: None)
        def run(_): entered.set(); release.wait(5)
        with patch.object(updater, '_run', run):
            updater.start(self.plan); entered.wait(3)
            first=updater.thread
            updater.start(self.plan)
            self.assertIs(updater.thread,first)
            release.set(); first.join(3)
    def test_failed_relaunch_restores_original_directory(self):
        target,stage=self.root/'app',self.root/'stage'
        target.mkdir();stage.mkdir()
        (target/'version').write_text('old');(stage/'version').write_text('new')
        calls=[]
        def launch(app):
            calls.append((app/'version').read_text())
            if calls[-1]=='new': raise OSError('launch rejected')
        with self.assertRaises(OSError): atomic_install(stage,target,launch)
        self.assertEqual((target/'version').read_text(),'old')
        self.assertEqual(calls,['new','old'])
    def test_successful_install_preserves_old_package(self):
        target,stage=self.root/'app',self.root/'stage';target.mkdir();stage.mkdir()
        (target/'old').write_text('preserved');(stage/'new').write_text('ready')
        backup=atomic_install(stage,target,lambda _:None)
        self.assertTrue((backup/'old').is_file());self.assertTrue((target/'new').is_file())
    def test_actual_portable_zip_root_is_supported(self):
        archive=self.root/'package.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('SJTU Learning Assistant.exe',b'PE fixture')
            z.writestr('_internal/support.txt',b'support')
        result=extract_windows_package(archive,self.root/'out')
        self.assertEqual(result,self.root/'out')

    def test_shutdown_timeout_restores_preferences_and_scheduler(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from sjtu_learning_assistant.update_shutdown import prepare_update_shutdown
        settings=SimpleNamespace(ai_enabled=True,recording_asr_paused=False)
        store=Mock();store.load.return_value=settings
        sync=threading.Lock();sync.acquire()
        worker=Mock()
        service=SimpleNamespace(settings_store=store,_ai_task_lock=threading.Lock(),_ai_task_threads={},_sync_lock=sync,_course_markdown_instance=worker,prepare_recording_asr_quit=Mock(return_value=True))
        scheduler=Mock()
        with patch('sjtu_learning_assistant.update_shutdown.APP_SUPPORT_DIR',self.root), self.assertRaises(UpdateServiceError):
            prepare_update_shutdown(service,scheduler,timeout=0)
        self.assertFalse(service._updating)
        store.update.assert_any_call(dict(ai_enabled=True,recording_asr_paused=False))
        scheduler.start.assert_called_once()
        worker.close.assert_not_called()

    def test_shutdown_waits_for_tasks_before_helper_launch(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from sjtu_learning_assistant.update_shutdown import prepare_update_shutdown
        store=Mock();store.load.return_value=SimpleNamespace(ai_enabled=True,recording_asr_paused=False)
        service=SimpleNamespace(settings_store=store,_ai_task_lock=threading.Lock(),_ai_task_threads={},_sync_lock=threading.Lock(),prepare_recording_asr_quit=Mock(return_value=True))
        scheduler=Mock()
        with patch('sjtu_learning_assistant.update_shutdown.APP_SUPPORT_DIR',self.root):
            restore=prepare_update_shutdown(service,scheduler)
        self.assertTrue(service._updating)
        service.prepare_recording_asr_quit.assert_called_once()
        restore()
        store.update.assert_any_call(dict(ai_enabled=True,recording_asr_paused=False))

    def test_portable_paths_cannot_escape_output(self):
        archive=self.root/'package.zip'
        for name in ('../evil.exe','C:/evil.exe','a\\evil.exe','App/../evil.exe'):
            with self.subTest(name=name):
                with zipfile.ZipFile(archive,'w') as z:z.writestr(name,b'x')
                if '\\' in name:
                    archive.write_bytes(archive.read_bytes().replace(name.replace('\\','/').encode(),name.encode()))
                with self.assertRaises(UpdateServiceError):extract_windows_package(archive,self.root/'out')
                self.assertFalse((self.root/'evil.exe').exists())

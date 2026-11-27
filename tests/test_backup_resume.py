from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import threading
from unittest import TestCase
from unittest.mock import patch
from sqlalchemy import select, update
from sqlalchemy.orm import Session
import test_backup_service as fixture_module
FakeProvider = fixture_module.FakeProvider
from sjtu_learning_assistant.backup_service import BackupCandidate, BackupManager, BACKUP_ROOT
from sjtu_learning_assistant.backup_checkpoint import BackupCheckpoint, SOURCE
from sjtu_learning_assistant.models import SyncState


class BackupResumeTests(TestCase):
    setUp = fixture_module.BackupServiceTests.setUp
    tearDown = fixture_module.BackupServiceTests.tearDown
    _service = fixture_module.BackupServiceTests._service
    _add_records = fixture_module.BackupServiceTests._add_records

    def candidates(self, count):
        result = []
        for index in range(count):
            path = self.archive / (str(index)+'.pdf'); path.write_bytes(b'abcd')
            result.append(BackupCandidate(str(index), 'canvas', (BACKUP_ROOT,'Canvas',str(index)+'.pdf'),str(path),self.archive,True))
        return tuple(result)

    def provider(self):
        provider = FakeProvider(); provider.identity = 'account-one/space-one'
        return provider

    def test_2000_files_resume_after_1200_and_repeated_run_zero_remote_checks(self):
        candidates = self.candidates(2000); provider = self.provider(); stop = threading.Event()
        def progress(value):
            if value['done'] >= 1200: stop.set()
        result = self._service(provider).backup(candidates,cancel_event=stop,progress_callback=progress)
        self.assertEqual(1200,result['uploaded'])
        provider.info_requests.clear(); provider.exists_requests.clear(); provider.simple.clear()
        events = []
        result = self._service(provider).backup(candidates,progress_callback=events.append)
        self.assertEqual((800,1200),(result['uploaded'],result['skipped_existing']))
        self.assertEqual(1200,events[0]['files']['completed'])
        self.assertEqual(2000,events[-1]['files']['completed'])
        self.assertEqual(800,len(provider.exists_requests))
        provider.info_requests.clear(); provider.exists_requests.clear(); provider.simple.clear(); provider.directories.clear()
        result = self._service(provider).backup(candidates)
        self.assertEqual(2000,result['skipped_existing'])
        self.assertEqual(([],[],[],[]),(provider.exists_requests,provider.info_requests,provider.simple,provider.directories))

    def test_same_size_edit_account_isolation_and_periodic_remote_missing(self):
        candidates = self.candidates(1); provider = self.provider(); service = self._service(provider)
        self.assertEqual(1,service.backup(candidates)['uploaded'])
        path = self.archive / '0.pdf'; info=path.stat(); path.write_bytes(b'wxyz')
        import os
        os.utime(path,ns=(info.st_atime_ns,info.st_mtime_ns))
        self.assertEqual(1,service.backup(candidates)['uploaded'])
        self.assertEqual(b'wxyz',provider.payloads[candidates[0].remote_path])
        other = self.provider(); other.identity = 'account-two'
        self.assertEqual(1,self._service(other).backup(candidates)['uploaded'])
        provider.remote.clear()
        with Session(self.engine) as session, session.begin():
            session.execute(update(SyncState).where(SyncState.source==SOURCE).values(last_success_at=datetime.now(timezone.utc)-timedelta(days=8)))
        self.assertEqual(1,service.backup(candidates)['uploaded'])

    def test_uncertain_success_recovers_without_duplicate_upload_and_failed_file_only_retries(self):
        candidates = self.candidates(3); provider = self.provider(); provider.fail_paths.add(candidates[1].remote_path)
        result = self._service(provider).backup(candidates)
        self.assertEqual((2,1),(result['uploaded'],result['failed']))
        provider.fail_paths.clear(); provider.simple.clear()
        self.assertEqual(1,self._service(provider).backup(candidates)['uploaded'])
        self.assertEqual([candidates[1].remote_path],[call[0] for call in provider.simple])
        with Session(self.engine) as session, session.begin():
            row=session.scalar(select(SyncState).where(SyncState.source==SOURCE))
            value=json.loads(row.cursor); value['pid']=999999999; row.cursor=json.dumps(value); row.status='uploading'
        provider.simple.clear()
        self.assertEqual(0,self._service(provider).backup(candidates)['uploaded'])
        self.assertEqual([],provider.simple)

    def test_cloud_success_before_checkpoint_save_and_local_deletion_revalidates(self):
        candidates=self.candidates(1); provider=self.provider()
        original=BackupCheckpoint.finish
        def interrupt(checkpoint,candidate,status,**kwargs):
            if status=='completed': raise KeyboardInterrupt()
            return original(checkpoint,candidate,status,**kwargs)
        with patch.object(BackupCheckpoint,'finish',interrupt),self.assertRaises(KeyboardInterrupt):
            self._service(provider).backup(candidates)
        provider.simple.clear()
        self.assertEqual(0,self._service(provider).backup(candidates)['uploaded'])
        self.assertEqual([],provider.simple)
        provider.download_requests.clear()
        # 无数据库原件只保留在本地；完整校验仍然核对远端字节。
        self._service(provider).backup(candidates,remove_local=True)
        self.assertTrue(provider.download_requests)

    def test_concurrent_file_records_and_claims_do_not_overwrite(self):
        candidates=self.candidates(2)
        one=BackupCheckpoint(self.engine,'space'); two=BackupCheckpoint(self.engine,'space')
        self.addCleanup(one.close);self.addCleanup(two.close)
        fingerprint=dict(size=4,sha256='a'*64)
        self.assertTrue(one.claim(candidates[0],fingerprint))
        self.assertFalse(two.claim(candidates[0],fingerprint))
        self.assertTrue(two.claim(candidates[1],fingerprint))
        two.finish(candidates[1],'completed');one.finish(candidates[0],'failed',error='network')
        with Session(self.engine) as session:
            self.assertEqual(['completed','failed'],sorted(session.scalars(select(SyncState.status).where(SyncState.source==SOURCE))))

    def test_manager_restores_display_history_from_database(self):
        provider=self.provider()
        manager=BackupManager(self.engine,archive_root=self.archive,mail_attachments_root=self.mail,provider_factory=lambda:provider,token_loader=lambda:True)
        manager._update_progress(dict(done=5,total=9,current_name='lecture.pdf',files=dict(completed=5,pending=3,failed=1)))
        second=BackupManager(self.engine,archive_root=self.archive,mail_attachments_root=self.mail,token_loader=lambda:True)
        self.assertEqual(5,second.status()['progress']['files']['completed'])

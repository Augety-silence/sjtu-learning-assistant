"""部署工具不能为退出一个不存在的主界面而重新启动 App。"""
from contextlib import redirect_stdout
import io
from pathlib import Path
import plistlib
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import deploy_ui_update_local as deploy


class LocalDeployGuardTests(unittest.TestCase):
    def test_quit_event_is_only_sent_to_running_main_process(self):
        for main_running in (False, True):
            with self.subTest(main_running=main_running), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle = root / 'new.app'
                (bundle / 'Contents').mkdir(parents=True)
                with (bundle / 'Contents/Info.plist').open('wb') as stream:
                    plistlib.dump(dict(CFBundleShortVersionString='1.8.18'), stream)
                app = root / 'installed.app'
                app.mkdir()
                state = root / 'state.json'
                state.write_text('{}', encoding='utf-8')
                calls, polls = [], []
                def run(command, **kwargs):
                    calls.append(command)
                    if command[0] == 'pgrep':
                        polls.append(command)
                        return SimpleNamespace(returncode=0 if len(polls) == 1 and main_running else 1)
                    if command[0] == '/usr/bin/ditto':
                        shutil.copytree(command[1], command[2])
                    return SimpleNamespace(returncode=0)
                store = Mock()
                store.load.return_value = SimpleNamespace(ai_enabled=False)
                with patch.object(deploy, 'STATE', state), patch.object(deploy, 'APP', app), \
                     patch.object(deploy, 'BACKUP', root / 'backup.app'), \
                     patch.object(deploy, 'STAGED', root / 'staged.app'), \
                     patch.object(deploy, 'SettingsStore', return_value=store), \
                     patch.object(deploy, 'requests', return_value=dict(inflight=0, queued=0)), \
                     patch.object(deploy.subprocess, 'run', side_effect=run), \
                     patch.object(deploy.sys, 'argv', ['deploy', 'install', '--bundle', str(bundle)]), \
                     redirect_stdout(io.StringIO()):
                    deploy.main()
                self.assertEqual(int(main_running), sum(cmd[0] == 'osascript' for cmd in calls))
                self.assertTrue(app.is_dir())
                self.assertTrue((root / 'backup.app').is_dir())
                store.save.assert_not_called()

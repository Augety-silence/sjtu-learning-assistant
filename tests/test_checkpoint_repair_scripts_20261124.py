import json
from pathlib import Path
import tempfile
import unittest
from scripts.repair_checkpoints_20261124 import pending_note_directories

class TargetSelectionTests(unittest.TestCase):
    def test_incomplete_download_does_not_block_paused_notes_or_select_completed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            (root/'download-in-progress').mkdir()
            for name,status,paused in (('failed-a','pending',True),('failed-b','pending',True),
                                       ('old-completed','completed',False),('ordinary-pending','pending',False)):
                directory=root/name
                directory.mkdir()
                (directory/'manifest.json').write_text(json.dumps({'lesson_notes':{'status':status,'auto_paused':paused}}))
            self.assertEqual({'failed-a','failed-b'},{p.name for p in pending_note_directories(root)})
            self.assertEqual(['failed-b'],[p.name for p in pending_note_directories(root,'failed-b')])
            self.assertEqual([],pending_note_directories(root,'download-in-progress'))

    def test_linked_manifest_is_preserved_and_never_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            outside=root/'outside.json'
            raw=json.dumps({'lesson_notes':{'status':'pending','auto_paused':True}})
            outside.write_text(raw)
            directory=root/'linked'
            directory.mkdir()
            (directory/'manifest.json').symlink_to(outside)
            self.assertEqual([],pending_note_directories(root))
            self.assertEqual(raw,outside.read_text())

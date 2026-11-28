from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from sjtu_learning_assistant.calendar_export import CalendarExportError, save_calendar_ics

CONTENT = 'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:test@local\r\nSUMMARY:中文日程\r\nDTSTART:20261128T020000Z\r\nDTEND:20261128T023000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'


class CalendarExportTests(unittest.TestCase):
    def test_utf8_crlf_unique_file_no_overwrite_and_no_path_disclosure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / '中文 folder'
            first = save_calendar_ics(CONTENT, directory=root)
            second = save_calendar_ics(CONTENT, directory=root)
            self.assertNotEqual(first['filename'], second['filename'])
            self.assertEqual(CONTENT.encode('utf-8'), (root / first['filename']).read_bytes())
            self.assertNotIn(folder, str(first))
    def test_unsupported_attachment_and_bad_content_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'unused'
            for content in (None, 'invalid', CONTENT.replace('SUMMARY:', 'ATTACH:'), CONTENT.replace('SUMMARY:', 'URL:'), CONTENT.replace('中文', '\0')):
                with self.subTest(content=content), self.assertRaises(CalendarExportError):
                    save_calendar_ics(content, directory=root)
            self.assertFalse(root.exists())
    def test_bridge_rejects_destination_injection_and_surfaces_export_error(self):
        from desktop_app import DesktopBridge
        from tests.test_desktop_app import FakeService
        bridge = DesktopBridge(FakeService())
        with patch('desktop_app.save_calendar_ics', return_value={'filename':'sample.ics'}) as save:
            self.assertTrue(bridge.invoke('personal_calendar_export_ics', {'content':CONTENT})['ok'])
            self.assertFalse(bridge.invoke('personal_calendar_export_ics', {'content':CONTENT, 'path':'outside.ics'})['ok'])
            save.assert_called_once_with(CONTENT)
        with patch('desktop_app.save_calendar_ics', side_effect=CalendarExportError('导出失败')):
            self.assertEqual('operation_failed', bridge.invoke('personal_calendar_export_ics', {'content':CONTENT})['error']['code'])

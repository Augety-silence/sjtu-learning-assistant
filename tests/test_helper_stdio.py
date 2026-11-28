import io
import sys
import unittest
from unittest.mock import patch
from sjtu_learning_assistant import helper_stdio


class HelperStdioTests(unittest.TestCase):
    def test_frozen_windows_restores_only_inherited_streams(self):
        streams = [io.StringIO() for _ in range(3)]
        with patch.object(sys, 'platform', 'win32'), patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'stdin', None), patch.object(sys, 'stdout', None), patch.object(sys, 'stderr', None), patch.object(helper_stdio, '_windows_stream', side_effect=streams) as restore:
            self.assertTrue(helper_stdio.restore_helper_stdio())
            self.assertEqual([(-10, 'r'), (-11, 'w'), (-12, 'w')], [call.args for call in restore.call_args_list])
            self.assertIs(streams[0], sys.stdin)
            self.assertIs(streams[1], sys.stdout)
    def test_missing_pipe_fails_without_console_and_closes_partial_stream(self):
        created = io.StringIO()
        with patch.object(sys, 'platform', 'win32'), patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'stdin', None), patch.object(sys, 'stdout', None), patch.object(helper_stdio, '_windows_stream', side_effect=[created, OSError('No pipe')]):
            self.assertFalse(helper_stdio.restore_helper_stdio())
            self.assertIsNone(sys.stdin)
            self.assertIsNone(sys.stdout)
        self.assertTrue(created.closed)
    def test_unfrozen_process_does_not_restore_windows_handles(self):
        with patch.object(sys, 'platform', 'win32'), patch.object(sys, 'frozen', False, create=True), patch.object(helper_stdio, '_windows_stream') as restore:
            self.assertTrue(helper_stdio.restore_helper_stdio())
            restore.assert_not_called()
    def test_cli_without_pipes_never_starts_desktop_or_search_service(self):
        import desktop_app
        with patch.object(sys, 'argv', ['app', '--web-search-mcp']), patch.object(helper_stdio, 'restore_helper_stdio', return_value=False), patch.object(desktop_app, 'run_desktop_app') as desktop, patch('sjtu_learning_assistant.web_search_mcp.main') as server:
            self.assertEqual(2, desktop_app.main())
            desktop.assert_not_called()
            server.assert_not_called()

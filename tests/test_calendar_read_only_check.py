from datetime import datetime, timezone
from unittest.mock import Mock
import unittest
from sjtu_learning_assistant.calendar_self_test import read_only_check


class CalendarReadOnlyCheckTests(unittest.TestCase):
    def test_reads_evening_without_connecting_or_mutating(self):
        service = Mock()
        service.call.side_effect = [
            dict(supported=True, enabled=True, authorization='full_access'),
            dict(items=[
                dict(title='private title', all_day=False, start_at='2026-11-28T18:00:00+00:00', end_at='2026-11-28T18:30:00+00:00'),
                dict(title='private all day', all_day=True, start_at='2026-11-28T00:00:00+00:00', end_at='2026-11-29T00:00:00+00:00'),
            ], truncated=False),
        ]
        result = read_only_check(service, datetime(2026, 11, 28, 17, tzinfo=timezone.utc))
        self.assertTrue(result['ok'])
        self.assertEqual(2, result['today_events'])
        self.assertEqual([['18:00', '18:30']], result['evening_time_windows'])
        self.assertNotIn('private', str(result))
        self.assertEqual(['status', 'events'], [call.args[0] for call in service.call.call_args_list])
        self.assertEqual(0, result['calendar_writes'])

    def test_missing_permission_does_not_request_access(self):
        service = Mock()
        service.call.return_value = dict(supported=True, enabled=True, authorization='not_determined')
        result = read_only_check(service, datetime(2026, 11, 28, tzinfo=timezone.utc))
        self.assertFalse(result['ok'])
        service.call.assert_called_once_with('status')
        self.assertEqual(0, result['permission_requests'])

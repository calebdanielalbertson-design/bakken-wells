"""Tests for scripts/wells/hours.py: DMR downloads wait until outside Central business hours."""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wells import hours  # noqa: E402


def utc(*parts):
    return dt.datetime(*parts, tzinfo=dt.timezone.utc)


class CentralTimeTests(unittest.TestCase):
    def test_standard_and_daylight_time(self):
        self.assertEqual(hours.central(utc(2026, 11, 9, 3, 52)), dt.datetime(2026, 11, 8, 21, 52))   # CST
        self.assertEqual(hours.central(utc(2026, 7, 1, 17, 0)), dt.datetime(2026, 7, 1, 12, 0))      # CDT

    def test_changeovers(self):
        self.assertEqual(hours.central(utc(2026, 3, 8, 7, 59)), dt.datetime(2026, 3, 8, 1, 59))      # last minute of CST
        self.assertEqual(hours.central(utc(2026, 3, 8, 8, 0)), dt.datetime(2026, 3, 8, 3, 0))        # CDT begins
        self.assertEqual(hours.central(utc(2026, 11, 1, 6, 30)), dt.datetime(2026, 11, 1, 1, 30))    # CDT
        self.assertEqual(hours.central(utc(2026, 11, 1, 7, 30)), dt.datetime(2026, 11, 1, 1, 30))    # CST again


class DecideTests(unittest.TestCase):
    def test_scheduled_morning_run_on_time(self):
        self.assertFalse(hours.decide(utc(2026, 11, 10, 10, 27))[0])   # Tuesday 04:27 CST

    def test_late_start_on_a_weekday_is_deferred(self):
        defer, message = hours.decide(utc(2026, 11, 10, 16, 35))       # Tuesday 10:35 CST
        self.assertTrue(defer)
        self.assertIn('evening', message)

    def test_weekends_are_never_business_hours(self):
        self.assertFalse(hours.decide(utc(2026, 11, 8, 16, 44))[0])    # Sunday 10:44 CST (the run of 2026-11-08)
        self.assertFalse(hours.decide(utc(2026, 11, 7, 16, 33))[0])    # Saturday

    def test_edges(self):
        self.assertTrue(hours.decide(utc(2026, 11, 9, 13, 0))[0])      # Monday 07:00 CST
        self.assertFalse(hours.decide(utc(2026, 11, 9, 12, 59))[0])    # Monday 06:59 CST
        self.assertFalse(hours.decide(utc(2026, 11, 10, 0, 0))[0])     # Monday 18:00 CST
        self.assertFalse(hours.decide(utc(2026, 11, 10, 0, 47))[0])    # the evening schedule, 18:47 CST
        self.assertFalse(hours.decide(utc(2026, 7, 14, 0, 47))[0])     # the evening schedule in summer, 19:47 CDT

    def test_manual_override(self):
        self.assertFalse(hours.decide(utc(2026, 11, 10, 16, 35), override=True)[0])

    def test_github_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            hours.main(['--now', '2026-11-10T16:35:00Z', '--github-output', str(out)])
            hours.main(['--now', '2026-11-10T16:35:00Z', '--override', 'true', '--github-output', str(out)])
            self.assertEqual(out.read_text(encoding='utf-8').split(), ['defer=true', 'defer=false'])


if __name__ == '__main__':
    unittest.main()

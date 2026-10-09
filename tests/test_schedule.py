import unittest

from usage_monitor.models import UsageSnapshot, WindowUsage
from usage_monitor.schedule import ClaudeSchedule


def snap(percent):
    return UsageSnapshot((WindowUsage("5 horas", percent, None),), 0, "test")


class ScheduleTests(unittest.TestCase):
    def test_settles_from_30s_to_5min_to_30min(self):
        schedule = ClaudeSchedule(0)
        schedule.observe(snap(10), 0)
        for now in (30, 60, 90):
            schedule.observe(snap(10), now)
            self.assertEqual(schedule.delay, 30)
        schedule.observe(snap(10), 120)
        self.assertEqual(schedule.delay, 300)
        schedule.observe(snap(10), 420)
        self.assertEqual(schedule.delay, 1800)
        schedule.observe(snap(10), 2220)
        self.assertEqual(schedule.delay, 1800)

    def test_change_or_manual_reset_restarts_fast_polling(self):
        schedule = ClaudeSchedule(0)
        schedule.observe(snap(10), 0)
        schedule.observe(snap(10), 120)
        schedule.observe(snap(10), 420)
        self.assertTrue(schedule.observe(snap(11), 2220))
        self.assertEqual(schedule.delay, 30)
        schedule.observe(snap(11), 2340)
        schedule.reset(2400)
        self.assertEqual(schedule.delay, 30)
        schedule.observe(snap(11), 2430)
        self.assertEqual(schedule.delay, 30)

    def test_unscheduled_readings_never_slow_down(self):
        schedule = ClaudeSchedule(0)
        schedule.observe(snap(10), 0)
        schedule.observe(snap(10), 120)
        schedule.observe(snap(10), 130, scheduled=False)
        self.assertEqual(schedule.delay, 300)


if __name__ == "__main__":
    unittest.main()

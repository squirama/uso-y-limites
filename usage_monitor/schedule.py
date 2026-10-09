"""Adaptive Claude polling: fast while the counter moves, slower once it settles."""

INTERVALS = (30, 300, 1800)
SETTLE_SECONDS = 120


def values(snapshot):
    return tuple((w.label, w.used_percent) for w in snapshot.windows)


class ClaudeSchedule:
    def __init__(self, now):
        self.level = 0
        self.last_change = now
        self.last_values = None

    @property
    def delay(self):
        return INTERVALS[self.level]

    def reset(self, now):
        self.level = 0
        self.last_change = now

    def observe(self, snapshot, now, scheduled=True):
        """Return True when the level changed, so the caller can reschedule."""
        current = values(snapshot)
        previous, self.last_values = self.last_values, current
        before = self.level
        if previous is not None and current != previous:
            self.reset(now)
        elif previous is None:
            self.last_change = now
        elif scheduled and self.level == 0 and now - self.last_change >= SETTLE_SECONDS:
            self.level = 1
        elif scheduled and self.level == 1:
            self.level = 2
        return self.level != before

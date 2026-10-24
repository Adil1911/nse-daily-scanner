"""NSE session calendar and India time."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
# Today's daily bar is treated as final only after this time.
EOD_FINAL = time(16, 0)


def now_ist(override: str | None = None) -> datetime:
    if override:
        dt = datetime.fromisoformat(override)
        return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)
    return datetime.now(IST)


class Calendar:
    def __init__(self, holidays=()):
        self.holidays = {date.fromisoformat(h) if isinstance(h, str) else h for h in holidays}

    def is_session(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def next_session(self, d: date) -> date:
        nxt = d + timedelta(days=1)
        while not self.is_session(nxt):
            nxt += timedelta(days=1)
        return nxt

    def sessions_between(self, start: date, end: date) -> int:
        """Sessions s with start < s <= end (0 when end <= start)."""
        if end <= start:
            return 0
        count = 0
        d = start + timedelta(days=1)
        while d <= end:
            if self.is_session(d):
                count += 1
            d += timedelta(days=1)
        return count

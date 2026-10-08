"""Time helpers. Storage is always UTC; Europe/Berlin is used only to cut days and display."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

UTC = ZoneInfo("UTC")
BERLIN = ZoneInfo("Europe/Berlin")


def to_berlin(ts: pd.Timestamp | pd.DatetimeIndex) -> pd.Timestamp | pd.DatetimeIndex:
    return ts.tz_convert(BERLIN)


def day_bounds_utc(day: date, tz: ZoneInfo = BERLIN) -> tuple[pd.Timestamp, pd.Timestamp]:
    """UTC start and end of a local delivery day. The span is 23, 24 or 25 hours."""
    start = pd.Timestamp(datetime(day.year, day.month, day.day), tz=tz)
    nxt = day + timedelta(days=1)
    end = pd.Timestamp(datetime(nxt.year, nxt.month, nxt.day), tz=tz)
    return start.tz_convert(UTC), end.tz_convert(UTC)


def local_days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def month_starts(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """UTC month starts of every calendar month overlapping [start, end)."""
    cur = pd.Timestamp(year=start.year, month=start.month, day=1, tz=UTC)
    out = []
    while cur < end:
        out.append(cur)
        cur = cur + pd.offsets.MonthBegin(1)
    return out

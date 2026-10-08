"""Gap and duplicate detection for price series (DR-4). Nothing here repairs data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from bdo.timeutil import day_bounds_utc


@dataclass
class QualityReport:
    rows: int
    duplicates: list[pd.Timestamp] = field(default_factory=list)
    gaps: list[tuple[pd.Timestamp, pd.Timestamp]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.duplicates and not self.gaps

    def to_dict(self) -> dict[str, object]:
        return {
            "rows": self.rows,
            "ok": self.ok,
            "duplicates": [t.isoformat() for t in self.duplicates],
            "gaps": [{"start": a.isoformat(), "end": b.isoformat()} for a, b in self.gaps],
        }


def check_prices(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> QualityReport:
    """Report duplicate timestamps and every uncovered span inside [start, end)."""
    report = QualityReport(rows=len(df))
    dup_mask = df["ts_utc"].duplicated(keep="first")
    report.duplicates = list(df.loc[dup_mask, "ts_utc"].unique())
    uniq = df.loc[~dup_mask].sort_values("ts_utc")
    cursor = start
    for ts, res in zip(uniq["ts_utc"], uniq["resolution_min"], strict=True):
        if ts >= end:
            break
        if ts > cursor:
            report.gaps.append((cursor, ts))
        cursor = max(cursor, ts + pd.Timedelta(minutes=int(res)))
    if cursor < end:
        report.gaps.append((cursor, end))
    return report


def day_prices(df: pd.DataFrame, day: date) -> pd.DataFrame | None:
    """Prices of one Berlin delivery day, or None when the day has a gap or duplicate."""
    start, end = day_bounds_utc(day)
    part = df[(df["ts_utc"] >= start) & (df["ts_utc"] < end)]
    if part.empty or not check_prices(part, start, end).ok:
        return None
    return part.reset_index(drop=True)

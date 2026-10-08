"""Hourly TSO day-ahead forecasts of load, wind and solar from SMARD, cached per year."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import structlog

from bdo.config import data_dir
from bdo.data import smard
from bdo.timeutil import UTC

log = structlog.get_logger()

SERIES = {"load_fc": 411, "wind_on_fc": 123, "wind_off_fc": 3791, "pv_fc": 125}  # MWh per hour


def ensure_fundamentals(
    first_year: int, last_year: int, zone: str = "DE-LU", root: Path | None = None,
    now: pd.Timestamp | None = None, res: str = "hour",
) -> pd.DataFrame:
    """Frame indexed by UTC interval start with one column per series. Missing stays NaN.

    `res` is "hour" or "quarterhour"; quarter-hour values are MWh per quarter-hour.
    """
    root = (root or data_dir()) / "fundamentals" / zone
    root.mkdir(parents=True, exist_ok=True)
    now = now or pd.Timestamp.now(tz=UTC)
    columns = {}
    for name, flt in SERIES.items():
        parts = []
        for year in range(first_year, last_year + 1):
            suffix = "" if res == "hour" else "_qh"
            path = root / f"{name}{suffix}_{year}.parquet"
            # A finished year never changes; the running year is refreshed on each call.
            if not path.exists() or year >= now.year:
                start = pd.Timestamp(year=year, month=1, day=1, tz=UTC)
                end = pd.Timestamp(year=year + 1, month=1, day=1, tz=UTC)
                log.info("fundamentals.fetch", series=name, year=year)
                smard.fetch_hourly(flt, start, end, zone, res=res).to_frame(name).to_parquet(
                    path)
            parts.append(pd.read_parquet(path)[name])
        columns[name] = pd.concat(parts)
    return pd.DataFrame(columns).sort_index()

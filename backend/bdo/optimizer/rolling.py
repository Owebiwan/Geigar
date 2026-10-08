"""Rolling-horizon planning (OP-6): plan D+1 once prices publish, re-plan on SoC drift."""

from __future__ import annotations

from datetime import date

import pandas as pd

from bdo.config import Config
from bdo.data import load_profile, weather
from bdo.data.quality import day_prices
from bdo.optimizer.model import solve_day
from bdo.optimizer.schedule import DayInputs, Schedule


def build_inputs(
    cfg: Config, prices: pd.DataFrame, day: date, ghi: pd.Series | None = None
) -> DayInputs | None:
    """Inputs for one delivery day, or None when the day's prices have gaps."""
    part = day_prices(prices, day)
    if part is None:
        return None
    index = pd.DatetimeIndex(part["ts_utc"])
    load = load_profile.load_kw(index, cfg.site.load_annual_kwh) if cfg.site.has_load else None
    solar = None
    if cfg.site.has_solar and ghi is not None:
        solar = weather.solar_kw(ghi, cfg.site.solar_kwp, index)
    return DayInputs.from_prices(part, load, solar)


def plan_day(cfg: Config, inputs: DayInputs, soc0: float, mtd_peak_kw: float = 0.0) -> Schedule:
    return solve_day(inputs, cfg.battery, cfg.site, soc0, mtd_peak_kw)


def interval_at(schedule: pd.DataFrame, now: pd.Timestamp) -> int | None:
    """Index of the interval containing `now`, or None outside the schedule."""
    starts = schedule["ts_utc"]
    pos = int((starts <= now).sum()) - 1
    if pos < 0:
        return None
    end = starts.iloc[pos] + pd.Timedelta(hours=float(schedule["dt_h"].iloc[pos]))
    return pos if now < end else None


def needs_reoptimisation(
    schedule: pd.DataFrame, now: pd.Timestamp, measured_soc: float, tolerance: float
) -> bool:
    """True when the measured SoC has drifted from the plan by more than the tolerance."""
    pos = interval_at(schedule, now)
    if pos is None:
        return False
    row = schedule.iloc[pos]
    frac = (now - row["ts_utc"]) / pd.Timedelta(hours=float(row["dt_h"]))
    planned = row["soc_start"] + (row["soc"] - row["soc_start"]) * frac
    return bool(abs(measured_soc - planned) > tolerance)


def reoptimise(
    cfg: Config, schedule: Schedule, inputs: DayInputs, now: pd.Timestamp, measured_soc: float,
    mtd_peak_kw: float = 0.0,
) -> Schedule:
    """Re-plan from the next interval boundary to the end of the day.

    The current interval keeps its setpoint, so the battery will keep moving until the
    boundary. The new plan therefore starts from the planned end-of-interval SoC
    shifted by the drift measured now, not from the raw measurement.
    """
    pos = interval_at(schedule.frame, now)
    start = 0 if pos is None else pos + 1
    if start >= len(inputs.index):
        return schedule
    soc0 = measured_soc
    if pos is not None:
        row = schedule.frame.iloc[pos]
        frac = (now - row["ts_utc"]) / pd.Timedelta(hours=float(row["dt_h"]))
        planned_now = row["soc_start"] + (row["soc"] - row["soc_start"]) * frac
        soc0 = float(row["soc"] + (measured_soc - planned_now))
    tail = solve_day(inputs.tail(start), cfg.battery, cfg.site, soc0, mtd_peak_kw)
    frame = pd.concat([schedule.frame.iloc[:start], tail.frame], ignore_index=True)
    out = Schedule(frame, "optimizer", tail.solve_seconds, tail.status, dict(tail.meta))
    out.meta["reoptimised_at"] = now.isoformat()
    return out

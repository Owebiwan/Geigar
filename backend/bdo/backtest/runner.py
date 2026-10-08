"""Day-by-day replay of a full year for the optimizer and its baselines (BT-1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from bdo.config import Config
from bdo.optimizer import baselines
from bdo.optimizer.model import solve_day
from bdo.optimizer.rolling import build_inputs
from bdo.optimizer.schedule import DayInputs, Schedule, check_constraints
from bdo.timeutil import local_days

STRATEGIES = ("optimizer", "naive", "persistence", "idle")


@dataclass
class BacktestResult:
    year: int
    config_hash: str
    daily: pd.DataFrame  # one row per day and strategy
    intervals: pd.DataFrame  # optimizer schedule for every interval of the year
    skipped_days: list[date] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    solve_seconds: list[float] = field(default_factory=list)


def _day_row(day: date, sched: Schedule, idle: Schedule, cfg: Config) -> dict[str, object]:
    f = sched.frame
    dt = f["dt_h"]
    charge_kwh = float((f["charge_kw"] * dt).sum())
    discharge_kwh = float((f["discharge_kw"] * dt).sum())
    buy = float((f["price_eur_mwh"] * f["charge_kw"] * dt).sum() / charge_kwh) if charge_kwh else 0
    sell = (
        float((f["price_eur_mwh"] * f["discharge_kw"] * dt).sum() / discharge_kwh)
        if discharge_kwh else 0
    )
    return {
        "date": day.isoformat(),
        "strategy": sched.strategy,
        "intervals": len(f),
        # Battery value: what the site earns with the battery minus what it earns without.
        "revenue_eur": sched.cash_flow_eur - idle.cash_flow_eur,
        "cash_flow_eur": sched.cash_flow_eur,
        "degradation_eur": float(f["degradation_eur"].sum()),
        "charge_kwh": charge_kwh,
        "discharge_kwh": discharge_kwh,
        "cycles": discharge_kwh / cfg.battery.capacity_kwh,
        "spread_captured_eur_mwh": sell - buy if charge_kwh and discharge_kwh else 0.0,
        "peak_import_kw": sched.peak_import_kw,
        "soc_end": sched.soc_end,
        "price_min": float(f["price_eur_mwh"].min()),
        "price_max": float(f["price_eur_mwh"].max()),
        "price_mean": float(f["price_eur_mwh"].mean()),
    }


def run_backtest(
    cfg: Config, year: int, prices: pd.DataFrame, ghi: pd.Series | None = None,
    forecasts: dict[str, pd.Series] | None = None, last_day: date | None = None,
) -> BacktestResult:
    """Replay a year. `forecasts` maps a strategy name to hourly price forecasts (UTC);
    each one is planned on its forecast and settled at the real prices."""
    from bdo.forecast.apply import day_guess

    b, s = cfg.battery, cfg.site
    forecasts = forecasts or {}
    names = (*STRATEGIES, *forecasts)
    soc = {name: b.soc_start for name in names}
    mtd_peak = {name: 0.0 for name in names}
    rows: list[dict[str, object]] = []
    frames: list[pd.DataFrame] = []
    result = BacktestResult(year, cfg.hash(), pd.DataFrame(), pd.DataFrame())
    previous: DayInputs | None = build_inputs(cfg, prices, date(year - 1, 12, 31), ghi)
    month = 0

    for day in local_days(date(year, 1, 1), last_day or date(year, 12, 31)):
        if day.month != month:
            month = day.month
            mtd_peak = {name: 0.0 for name in names}
        inputs = build_inputs(cfg, prices, day, ghi)
        if inputs is None:
            # Missing or duplicated prices: the day is reported and skipped, SoC carries over.
            result.skipped_days.append(day)
            previous = None
            continue

        idle = baselines.idle(inputs, b, s, soc["idle"])
        opt = solve_day(inputs, b, s, soc["optimizer"], mtd_peak["optimizer"])
        day_runs = {
            "optimizer": opt,
            "naive": baselines.naive(inputs, b, s, soc["naive"]),
            "persistence": baselines.persistence(
                inputs, previous, b, s, soc["persistence"], mtd_peak["persistence"]
            ),
            "idle": idle,
        }
        for name, hourly in forecasts.items():
            guess = day_guess(inputs, hourly, previous)
            day_runs[name] = baselines.plan_on_guess(
                inputs, guess, b, s, soc[name], mtd_peak[name], name)
        result.solve_seconds.append(opt.solve_seconds)
        for name, sched in day_runs.items():
            for problem in check_constraints(sched.frame, b):
                result.violations.append(f"{day} {name}: {problem}")
            rows.append(_day_row(day, sched, idle, cfg))
            soc[name] = sched.soc_end
            mtd_peak[name] = max(mtd_peak[name], sched.peak_import_kw)
        frames.append(opt.frame.assign(date=day.isoformat()))
        previous = inputs

    result.daily = pd.DataFrame(rows)
    result.intervals = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return result

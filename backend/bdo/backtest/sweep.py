"""Scenario sweep over battery duration and round-trip efficiency (BT-3)."""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from bdo.backtest.results import FLOAT_FORMAT, summarise, year_irradiance, year_prices
from bdo.backtest.runner import run_backtest
from bdo.config import Config, data_dir
from bdo.data.prices import resolve_source


def scenario(cfg: Config, duration_h: float, round_trip: float) -> Config:
    """Same inverter power, different energy capacity and efficiency."""
    out = cfg.model_copy(deep=True)
    out.battery.capacity_kwh = cfg.battery.max_discharge_kw * duration_h
    out.battery.eta_charge = out.battery.eta_discharge = math.sqrt(round_trip)
    return out


def run_sweep(
    cfg: Config, year: int, durations: list[float], efficiencies: list[float],
    root: Path | None = None,
) -> pd.DataFrame:
    prices, ghi = year_prices(cfg, year), year_irradiance(cfg, year)
    rows = []
    for duration in durations:
        for rte in efficiencies:
            sc = scenario(cfg, duration, rte)
            summary, _, _ = summarise(sc, run_backtest(sc, year, prices, ghi), resolve_source(cfg))
            rows.append(
                {
                    "duration_h": duration,
                    "round_trip_efficiency": rte,
                    "capacity_kwh": sc.battery.capacity_kwh,
                    "config_hash": sc.hash(),
                    "violations": len(summary["violations"]),
                    **summary["headline"],
                }
            )
    table = pd.DataFrame(rows)
    out = (root or data_dir()) / "runs"
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / f"sweep-{year}-{cfg.hash()}.csv", index=False, float_format=FLOAT_FORMAT)
    return table

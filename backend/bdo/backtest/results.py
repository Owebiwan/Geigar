"""Run a backtest end to end and persist its outputs (BT-5, BT-6)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from bdo.backtest import metrics, report
from bdo.backtest.runner import BacktestResult, run_backtest
from bdo.config import Config, data_dir
from bdo.data import weather
from bdo.data.prices import ensure_prices, resolve_source
from bdo.data.reserves import ensure_imbalance_prices, ensure_reserve_prices
from bdo.timeutil import day_bounds_utc

FLOAT_FORMAT = "%.6f"


def year_prices(cfg: Config, year: int) -> pd.DataFrame:
    start, _ = day_bounds_utc(date(year - 1, 12, 31))
    _, end = day_bounds_utc(date(year, 12, 31))
    return ensure_prices(cfg, start, end)


def year_irradiance(cfg: Config, year: int) -> pd.Series | None:
    if not cfg.site.has_solar:
        return None
    return weather.irradiance_cached(cfg.site, date(year - 1, 12, 30), date(year, 12, 31))


def year_reserves(cfg: Config, year: int) -> pd.DataFrame | None:
    """Reserve capacity prices for the year, or None when reserves are switched off."""
    if not cfg.reserves.enabled:
        return None
    start, _ = day_bounds_utc(date(year, 1, 1))
    _, end = day_bounds_utc(date(year, 12, 31))
    return ensure_reserve_prices(start, end)


def imbalance_stats(year: int, prices: pd.DataFrame) -> dict[str, float] | None:
    """How far the imbalance price sits from the day-ahead price: the cost of missing a
    schedule by 1 MWh. None when imbalance prices cannot be fetched."""
    start, _ = day_bounds_utc(date(year, 1, 1))
    _, end = day_bounds_utc(date(year, 12, 31))
    try:
        imbalance = ensure_imbalance_prices(start, end)
    except RuntimeError:
        return None
    if imbalance.empty:
        return None
    day_ahead = prices.drop_duplicates("ts_utc").set_index("ts_utc")["price_eur_mwh"]
    # Hourly day-ahead prices hold for the four settlement periods of their hour.
    aligned = day_ahead.reindex(imbalance.index.union(day_ahead.index)).ffill(limit=3)
    gap = (imbalance - aligned.reindex(imbalance.index)).dropna()
    return {
        "periods": float(len(gap)),
        "mean_abs_gap_eur_mwh": float(gap.abs().mean()),
        "p95_abs_gap_eur_mwh": float(gap.abs().quantile(0.95)),
        "mean_gap_eur_mwh": float(gap.mean()),
    }


def run_dir(cfg: Config, year: int, root: Path | None = None) -> Path:
    path = (root or data_dir()) / "runs" / f"{year}-{cfg.hash()}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def summarise(
    cfg: Config, result: BacktestResult, source: str
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    monthly = metrics.monthly(result.daily, cfg)
    yearly = metrics.yearly(monthly, cfg)
    summary = {
        "year": result.year,
        "config_hash": result.config_hash,
        "source": source,
        "days": int(result.daily["date"].nunique()),
        "headline": metrics.headline(yearly),
        "special_days": metrics.special_days(result.daily),
        "markets": metrics.markets(yearly),
        "skipped_days": [d.isoformat() for d in result.skipped_days],
        "violations": result.violations,
        "config": cfg.model_dump(include={"battery", "site", "market", "reserves"}),
    }
    return summary, monthly, yearly


def run_and_save(cfg: Config, year: int, root: Path | None = None) -> tuple[dict, Path]:
    source = resolve_source(cfg)
    prices = year_prices(cfg, year)
    result = run_backtest(cfg, year, prices, year_irradiance(cfg, year),
                          reserve_prices=year_reserves(cfg, year))
    summary, monthly, yearly = summarise(cfg, result, source)
    if summary["markets"] is not None:
        mw = cfg.battery.max_discharge_kw / 1000.0
        summary["markets"]["eur_per_mw_year"] = summary["markets"]["total_eur"] / mw
        summary["markets"]["imbalance"] = imbalance_stats(year, prices)
        summary["markets"]["assumptions"] = cfg.reserves.model_dump()
    out = run_dir(cfg, year, root)
    result.daily.to_csv(out / "daily.csv", index=False, float_format=FLOAT_FORMAT)
    monthly.to_csv(out / "monthly.csv", index=False, float_format=FLOAT_FORMAT)
    yearly.to_csv(out / "yearly.csv", index=False, float_format=FLOAT_FORMAT)
    result.intervals.to_parquet(out / "intervals.parquet", index=False)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    (out / "report.html").write_text(report.render(summary, result.daily, monthly, yearly))
    h = summary["headline"]
    title = (f"{year} · {cfg.battery.capacity_kwh:,.0f} kWh / {cfg.battery.max_discharge_kw:,.0f} "
             f"kW · optimizer EUR {h['optimizer_eur']:,.0f} · "
             f"{report.uplift_text(h['uplift_vs_naive_pct'])} vs naive rule")
    (out / "monthly.svg").write_text(report.standalone_monthly_svg(monthly, title))
    # Timing is machine dependent, so it stays out of the deterministic outputs.
    timing = {"max_solve_seconds": max(result.solve_seconds, default=0.0)}
    (out / "timing.json").write_text(json.dumps(timing))
    return summary, out


def load_run(cfg: Config, year: int, root: Path | None = None) -> dict | None:
    out = (root or data_dir()) / "runs" / f"{year}-{cfg.hash()}"
    if not (out / "summary.json").exists():
        return None
    summary = json.loads((out / "summary.json").read_text())
    summary["monthly"] = pd.read_csv(out / "monthly.csv").to_dict("records")
    summary["yearly"] = pd.read_csv(out / "yearly.csv").to_dict("records")
    return summary

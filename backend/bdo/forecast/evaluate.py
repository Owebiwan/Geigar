"""Train the forecaster, replay the year on its forecasts and compare in EUR."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from bdo.backtest import metrics
from bdo.backtest.results import FLOAT_FORMAT, year_irradiance
from bdo.backtest.runner import run_backtest
from bdo.config import Config, data_dir
from bdo.data.fundamentals import ensure_fundamentals
from bdo.data.prices import ensure_prices
from bdo.forecast.features import build_features, hourly_prices
from bdo.forecast.intrahour import quarter_hour_forecast, quarter_table, walk_forward_deviation
from bdo.forecast.model import VARIANTS, ModelParams, error_metrics, walk_forward
from bdo.timeutil import BERLIN, UTC, day_bounds_utc

HISTORY_YEARS = 5  # training window plus a margin for the lag features
FIRST_QUARTER_HOUR_YEAR = 2025
MAIN = "forecast_fundamentals"


def _window(year: int, now: pd.Timestamp | None) -> tuple[pd.Timestamp, pd.Timestamp, date]:
    """Evaluation span: the whole year, or up to yesterday for the running year."""
    now = now or pd.Timestamp.now(tz=UTC)
    last = min(date(year, 12, 31), now.tz_convert(BERLIN).date() - timedelta(days=1))
    return day_bounds_utc(date(year, 1, 1))[0], day_bounds_utc(last)[1], last


def predictions(
    cfg: Config, year: int, root: Path | None = None, params: ModelParams | None = None,
    now: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.Series]]:
    """Prices and walk-forward forecasts for `year`.

    Forecasts do not depend on the battery, so they are cached per year and reused
    across battery scenarios. The running year is always recomputed.
    """
    first, end, last = _window(year, now)
    start = pd.Timestamp(year=year - HISTORY_YEARS, month=1, day=1, tz=UTC)
    # The fundamentals come from SMARD, so the prices do too: one consistent source.
    prices = ensure_prices(cfg, start, end, source="smard", now=now)
    cache = (root or data_dir()) / "forecast" / f"predictions-{year}.parquet"
    if cache.exists() and last == date(year, 12, 31):
        frame = pd.read_parquet(cache)
        return prices, {name: frame[name].dropna() for name in VARIANTS}

    table = build_features(prices, ensure_fundamentals(year - HISTORY_YEARS, year, now=now))
    out = {name: walk_forward(table, feats, year, params) for name, feats in VARIANTS.items()}
    if year >= FIRST_QUARTER_HOUR_YEAR:
        qh = ensure_fundamentals(max(FIRST_QUARTER_HOUR_YEAR, year - 1), year, now=now,
                                 res="quarterhour")
        deviation = walk_forward_deviation(quarter_table(prices, qh, out[MAIN]), first)
        out[MAIN] = quarter_hour_forecast(out[MAIN], deviation)
    cache.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(out).to_parquet(cache)
    return prices, out


def evaluate(
    cfg: Config, year: int, root: Path | None = None, params: ModelParams | None = None,
    now: pd.Timestamp | None = None,
) -> tuple[dict, dict[str, pd.DataFrame]]:
    first, end, last = _window(year, now)
    prices, forecasts = predictions(cfg, year, root, params, now)
    result = run_backtest(cfg, year, prices, year_irradiance(cfg, year), forecasts, last)
    monthly = metrics.monthly(result.daily, cfg)
    yearly = metrics.yearly(monthly, cfg).set_index("strategy")
    total = {str(k): float(v) for k, v in yearly["total_eur"].items()}
    cycles = {str(k): float(v) for k, v in yearly["cycles"].items()}

    # Price error is scored on hourly means so every row is comparable.
    in_year = prices[(prices["ts_utc"] >= first) & (prices["ts_utc"] < end)]
    actual = hourly_prices(in_year)
    errors = {}
    for name, fc in forecasts.items():
        errors[name] = error_metrics(actual, fc.groupby(pd.DatetimeIndex(fc.index).floor("h"))
                                     .mean())
    errors["persistence"] = error_metrics(actual, hourly_prices(prices).shift(24, freq="h"))
    best = total["optimizer"]
    rows = [
        {
            "strategy": name,
            "total_eur": total[name],
            "share_of_optimizer_pct": total[name] / best * 100.0,
            "cycles": cycles[name],
            "mae": errors.get(name, {}).get("mae"),
            "rmse": errors.get(name, {}).get("rmse"),
            "shape_corr": errors.get(name, {}).get("shape_corr"),
        }
        for name in ["optimizer", *forecasts, "persistence", "naive"]
    ]
    quarterly = result.daily.assign(quarter=pd.PeriodIndex(result.daily["date"], freq="Q")
                                    .astype(str))
    by_quarter = quarterly.pivot_table(index="quarter", columns="strategy", values="revenue_eur",
                                       aggfunc="sum")
    summary = {
        "year": year,
        "last_day": last.isoformat(),
        "days": int(result.daily["date"].nunique()),
        "config_hash": cfg.hash(),
        "source": "smard",
        "strategies": rows,
        "quarterly_share_pct": {
            str(q): {str(k): float(v / r["optimizer"] * 100.0) for k, v in r.items()
                     if k in (*forecasts, "persistence")}
            for q, r in by_quarter.iterrows()
        },
        "violations": result.violations,
        "skipped_days": [d.isoformat() for d in result.skipped_days],
    }
    return summary, {"daily": result.daily, "monthly": monthly}


def run_and_save(
    cfg: Config, year: int, root: Path | None = None, now: pd.Timestamp | None = None
) -> tuple[dict, Path]:
    summary, frames = evaluate(cfg, year, root, now=now)
    out = (root or data_dir()) / "forecast" / f"{year}-{cfg.hash()}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))
    frames["daily"].to_csv(out / "daily.csv", index=False, float_format=FLOAT_FORMAT)
    frames["monthly"].to_csv(out / "monthly.csv", index=False, float_format=FLOAT_FORMAT)
    return summary, out


def load_run(cfg: Config, year: int, root: Path | None = None) -> dict | None:
    out = (root or data_dir()) / "forecast" / f"{year}-{cfg.hash()}"
    if not (out / "summary.json").exists():
        return None
    summary = json.loads((out / "summary.json").read_text())
    monthly = pd.read_csv(out / "monthly.csv")
    summary["monthly"] = monthly[monthly["strategy"].isin(list(VARIANTS))].to_dict("records")
    return summary

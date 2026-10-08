"""Forecast one delivery day whose auction has not cleared yet.

Used for the provisional plan on the SCHEDULE screen. The controller never
dispatches on a forecast: it only follows published prices.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import structlog

from bdo.config import Config, data_dir
from bdo.data.fundamentals import ensure_fundamentals
from bdo.data.prices import ensure_prices
from bdo.data.quality import day_prices
from bdo.forecast.features import build_features
from bdo.forecast.intrahour import quarter_hour_forecast, quarter_table, walk_forward_deviation
from bdo.forecast.model import VARIANTS, ModelParams, fit
from bdo.timeutil import UTC, day_bounds_utc

log = structlog.get_logger()
MAIN, PRICES_ONLY = "forecast_fundamentals", "forecast_prices"
_FUNDAMENTAL_INPUTS = ["load_fc", "wind_fc", "pv_fc"]
RECHECK_SECONDS = 1800


@dataclass
class LiveForecast:
    day: date
    prices: pd.DataFrame  # ts_utc, price_eur_mwh (forecast), resolution_min, source
    variant: str  # which model produced it
    trained_until: pd.Timestamp


def forecast_day(
    cfg: Config, day: date, now: pd.Timestamp | None = None, root: Path | None = None,
    params: ModelParams | None = None,
) -> LiveForecast | None:
    """Forecast prices for `day`, or None when the day before has no complete prices.

    The model needs yesterday's prices as inputs, so only the first uncleared day can
    be forecast. If the TSO forecasts for the day are not out yet, the prices-only
    model is used.
    """
    now = now or pd.Timestamp.now(tz=UTC)
    p = params or ModelParams()
    start, end = day_bounds_utc(day)
    if start > now + pd.Timedelta(days=2):
        return None  # the day before cannot have prices yet; do not go looking for them
    history_start = start - pd.Timedelta(days=365.25 * p.train_years + 30)
    prices = ensure_prices(cfg, history_start, start, source="smard", now=now)
    previous = day_prices(prices, day - timedelta(days=1))
    if previous is None:
        return None

    stamp = f"{day}-{prices['ts_utc'].max():%Y%m%dT%H%M}"
    cache_dir = (root or data_dir()) / "forecast" / "live"
    cached = sorted(cache_dir.glob(f"{stamp}-*.parquet"))
    if cached and MAIN in cached[-1].name:
        return LiveForecast(day, pd.read_parquet(cached[-1]), MAIN, start)
    if cached and now.timestamp() - cached[-1].stat().st_mtime < RECHECK_SECONDS:
        # A prices-only forecast is upgraded once the TSO forecasts are out. Looking for
        # them means a slow download, so do not look again on every request.
        return LiveForecast(day, pd.read_parquet(cached[-1]), PRICES_ONLY, start)

    fund = ensure_fundamentals(history_start.year, end.year, now=now)
    table = build_features(prices, fund, until=end - pd.Timedelta(hours=1))
    target = table[(table.index >= start) & (table.index < end)]
    has_fundamentals = bool(target[_FUNDAMENTAL_INPUTS].notna().all().all())
    variant = MAIN if has_fundamentals else PRICES_ONLY
    if cached and variant in cached[-1].name:
        cached[-1].touch()  # still no TSO forecasts: check again after the next interval
        return LiveForecast(day, pd.read_parquet(cached[-1]), variant, start)

    train = table[table.index < start].dropna(subset=["price", "lag24"])
    features = VARIANTS[variant]
    predicted = fit(train, features, p).predict(target[features])
    if p.difference_target:
        predicted = predicted + target["lag24"].to_numpy()
    hourly = pd.Series(predicted, index=target.index, name=variant)
    resolution = int(previous["resolution_min"].iloc[0])

    if resolution == 15:
        index = pd.date_range(start, end, freq="15min", inclusive="left")
        base = pd.Series(hourly.reindex(index.floor("h")).to_numpy(), index=index)
        if has_fundamentals:
            qh = ensure_fundamentals(start.year - 1, end.year, now=now, res="quarterhour")
            blank = pd.DataFrame({"ts_utc": index, "price_eur_mwh": float("nan"),
                                  "resolution_min": 15, "source": "smard"})
            extended = pd.concat([prices, blank], ignore_index=True)
            deviation = walk_forward_deviation(quarter_table(extended, qh, hourly), start)
            series = quarter_hour_forecast(hourly, deviation).reindex(index).fillna(base)
        else:
            # Without 15-minute fundamentals, repeat yesterday's pattern inside the hour.
            prev = previous.set_index("ts_utc")["price_eur_mwh"]
            pattern = prev - prev.groupby(pd.DatetimeIndex(prev.index).floor("h")).transform("mean")
            pattern.index = pattern.index + pd.Timedelta(days=1)
            series = base + pattern.reindex(index).fillna(0.0)
    else:
        series = hourly

    frame = pd.DataFrame({"ts_utc": series.index, "price_eur_mwh": series.to_numpy(),
                          "resolution_min": resolution, "source": variant})
    cache_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(cache_dir / f"{stamp}-{variant}.parquet", index=False)
    log.info("forecast.live", day=str(day), variant=variant, rows=len(frame))
    return LiveForecast(day, frame, variant, start)

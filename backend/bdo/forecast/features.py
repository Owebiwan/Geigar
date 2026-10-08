"""Feature table for day-ahead price forecasting, one row per delivery hour.

Every feature for delivery day D is known before the auction closes at noon on D-1:
prices up to and including D-1 (cleared on D-2), the calendar, and, in the
"fundamentals" variant, the TSOs' day-ahead forecasts of load, wind and solar.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from bdo.timeutil import BERLIN

PRICE_FEATURES = [
    "lag24", "lag48", "lag168", "d1_mean", "d1_min", "d1_max", "d1_std", "d2_mean", "d7_mean",
    "lag24_vs_d1", "hour", "dow", "holiday", "doy_sin", "doy_cos",
]
FUNDAMENTAL_FEATURES = [
    "load_fc", "wind_fc", "pv_fc", "residual_fc", "residual_lag24", "residual_delta24",
    "residual_delta168", "residual_day_mean", "residual_vs_day", "residual_d1_mean",
    "renewable_share",
]


def _easter(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    j, k = c // 4, c % 4
    m = (a + 11 * h) // 319
    r = (2 * e + 2 * j - k - h + m + 32) % 7
    month = (h - m + r + 90) // 25
    return date(year, month, (h - m + r + month + 19) % 32)


def german_holidays(year: int) -> set[date]:
    """Public holidays observed in every German state."""
    easter = _easter(year)
    moving = [easter + timedelta(days=d) for d in (-2, 1, 39, 50)]
    fixed = [date(year, m, d) for m, d in ((1, 1), (5, 1), (10, 3), (12, 25), (12, 26))]
    return {*moving, *fixed}


def hourly_prices(prices: pd.DataFrame) -> pd.Series:
    """Hourly mean price. An hour with missing quarter-hours stays missing."""
    s = prices.drop_duplicates("ts_utc").set_index("ts_utc")
    hours = pd.DatetimeIndex(s.index).floor("h")
    expected = (60 // s["resolution_min"]).groupby(hours).first()
    grouped = s["price_eur_mwh"].groupby(hours)
    return grouped.mean().where(grouped.count() == expected).rename("price")


def build_features(
    prices: pd.DataFrame, fundamentals: pd.DataFrame | None = None,
    until: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Feature table. `until` extends it past the last known price, for days to forecast."""
    price = hourly_prices(prices)
    last = price.index.max() if until is None else max(price.index.max(), until)
    index = pd.date_range(price.index.min(), last, freq="h")
    df = pd.DataFrame({"price": price.reindex(index)})
    local = index.tz_convert(BERLIN)
    day = pd.Series(local.date, index=df.index)

    for lag in (24, 48, 168):
        df[f"lag{lag}"] = df["price"].shift(lag)
    daily = df["price"].groupby(day).agg(["mean", "min", "max", "std"])
    for back, cols in ((1, ["mean", "min", "max", "std"]), (2, ["mean"]), (7, ["mean"])):
        # Look up by calendar day, not by row offset, so a missing day cannot misalign it.
        lookup = day.map(lambda d, b=back: d - timedelta(days=b))
        for col in cols:
            df[f"d{back}_{col}"] = lookup.map(daily[col]).to_numpy()
    df["lag24_vs_d1"] = df["lag24"] - df["d1_mean"]

    df["hour"] = local.hour
    df["dow"] = local.dayofweek
    holidays = {h for y in range(local.year.min(), local.year.max() + 1)
                for h in german_holidays(int(y))}
    df["holiday"] = day.isin(holidays).astype(int).to_numpy()
    angle = 2 * np.pi * local.dayofyear / 365.25
    df["doy_sin"], df["doy_cos"] = np.sin(angle), np.cos(angle)

    if fundamentals is not None:
        f = fundamentals.reindex(df.index)
        df["load_fc"] = f["load_fc"]
        df["wind_fc"] = f["wind_on_fc"] + f["wind_off_fc"]
        df["pv_fc"] = f["pv_fc"]
        df["residual_fc"] = df["load_fc"] - df["wind_fc"] - df["pv_fc"]
        df["residual_lag24"] = df["residual_fc"].shift(24)
        df["residual_delta24"] = df["residual_fc"] - df["residual_lag24"]
        df["residual_delta168"] = df["residual_fc"] - df["residual_fc"].shift(168)
        day_mean = df["residual_fc"].groupby(day).mean()
        df["residual_day_mean"] = day.map(day_mean).to_numpy()
        df["residual_vs_day"] = df["residual_fc"] - df["residual_day_mean"]
        df["residual_d1_mean"] = day.map(lambda d: d - timedelta(days=1)).map(day_mean).to_numpy()
        df["renewable_share"] = (df["wind_fc"] + df["pv_fc"]) / df["load_fc"]
    return df

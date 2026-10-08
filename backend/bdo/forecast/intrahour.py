"""Price pattern inside the hour for 15-minute products.

The hourly model forecasts each hour's mean price. In the 15-minute market about a
quarter of the arbitrage value sits in how the four quarter-hours deviate from that
mean: prices saw-tooth as load and solar ramp across an hourly-scheduled fleet. This
model forecasts that deviation from the TSOs' 15-minute forecasts of load, wind and
solar, and from the pattern of the previous days.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from bdo.timeutil import BERLIN

FEATURES = ["quarter", "hour", "residual_dev", "pv_dev", "load_dev", "wind_dev",
            "residual_slope", "residual_hour", "dev_d1", "dev_week", "level"]
MIN_TRAIN_DAYS = 7
RETRAIN_DAYS = 7


def quarter_table(
    prices: pd.DataFrame, fundamentals_qh: pd.DataFrame, hourly_forecast: pd.Series
) -> pd.DataFrame:
    """One row per 15-minute interval of the 15-minute era, with target and features."""
    quarters = prices[prices["resolution_min"] == 15].drop_duplicates("ts_utc")
    df = pd.DataFrame({"price": quarters.set_index("ts_utc")["price_eur_mwh"]})
    if df.empty:
        return df
    index = pd.DatetimeIndex(df.index)
    hours = index.floor("h")
    hour_mean = df["price"].groupby(hours).transform("mean")
    df["dev"] = df["price"] - hour_mean

    f = fundamentals_qh.reindex(index) * 4.0  # MWh per quarter-hour -> MW
    series = {
        "residual": f["load_fc"] - f["wind_on_fc"] - f["wind_off_fc"] - f["pv_fc"],
        "pv": f["pv_fc"], "load": f["load_fc"], "wind": f["wind_on_fc"] + f["wind_off_fc"],
    }
    for name, values in series.items():
        df[f"{name}_dev"] = values - values.groupby(hours).transform("mean")
    by_hour = series["residual"].groupby(hours).mean()
    df["residual_slope"] = (by_hour.shift(-1) - by_hour.shift(1)).reindex(hours).to_numpy()
    df["residual_hour"] = by_hour.reindex(hours).to_numpy()

    local = index.tz_convert(BERLIN)
    df["quarter"] = local.minute // 15
    df["hour"] = local.hour
    # Lag on a gap-free 15-minute grid so a missing day cannot shift the lookup.
    grid = df["dev"].reindex(pd.date_range(index.min(), index.max(), freq="15min"))
    df["dev_d1"] = grid.shift(96).reindex(index).to_numpy()
    week = pd.concat([grid.shift(96 * k) for k in range(1, 8)], axis=1).mean(axis=1)
    df["dev_week"] = week.reindex(index).to_numpy()
    # Deviations scale with the price level. Use the forecast level where there is one,
    # the realised hourly mean for older training rows.
    df["level"] = hourly_forecast.reindex(hours).to_numpy()
    df["level"] = df["level"].fillna(hour_mean)
    df["local_date"] = local.date
    return df


def walk_forward_deviation(table: pd.DataFrame, first: pd.Timestamp) -> pd.Series:
    """Forecast the deviation for every quarter-hour from `first` on.

    Retrains weekly on all earlier 15-minute days. Until a week of history exists it
    falls back to the average pattern of the previous days, then to zero.
    """
    if table.empty:
        return pd.Series(dtype="float64")
    fallback = table["dev_week"].fillna(table["dev_d1"]).fillna(0.0)
    pred = pd.Series(np.nan, index=table.index)
    days = sorted(set(table["local_date"]))
    targets = [d for d in days if pd.Timestamp(d, tz=BERLIN) >= first.tz_convert(BERLIN).floor("D")]
    for i in range(0, len(targets), RETRAIN_DAYS):
        block = targets[i:i + RETRAIN_DAYS]
        train = table[table["local_date"] < block[0]].dropna(subset=["dev"])
        if train["local_date"].nunique() < MIN_TRAIN_DAYS:
            continue
        model = HistGradientBoostingRegressor(
            max_iter=200, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=30,
            l2_regularization=1.0, random_state=0,
        ).fit(train[FEATURES], train["dev"])
        mask = table["local_date"].isin(block)
        pred[mask] = model.predict(table.loc[mask, FEATURES])
    out = pred.fillna(fallback)
    return out[out.index >= first].rename("deviation")


def quarter_hour_forecast(hourly_forecast: pd.Series, deviation: pd.Series) -> pd.Series:
    """Hourly forecast with the 15-minute era replaced by quarter-hour values."""
    if deviation.empty:
        return hourly_forecast
    hours = pd.DatetimeIndex(deviation.index).floor("h")
    quarters = pd.Series(hourly_forecast.reindex(hours).to_numpy() + deviation.to_numpy(),
                         index=deviation.index).dropna()
    covered = set(pd.DatetimeIndex(quarters.index).floor("h"))
    keep = hourly_forecast[[h not in covered for h in hourly_forecast.index]]
    return pd.concat([keep, quarters]).sort_index().rename(hourly_forecast.name)

"""Gradient-boosted day-ahead price forecaster with walk-forward retraining."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from bdo.forecast.features import FUNDAMENTAL_FEATURES, PRICE_FEATURES
from bdo.timeutil import UTC

VARIANTS = {
    "forecast_prices": PRICE_FEATURES,
    "forecast_fundamentals": [*PRICE_FEATURES, *FUNDAMENTAL_FEATURES],
}


@dataclass(frozen=True)
class ModelParams:
    max_iter: int = 400
    learning_rate: float = 0.05
    max_leaf_nodes: int = 31
    min_samples_leaf: int = 40
    l2_regularization: float = 1.0
    # True predicts the change against the same hour yesterday instead of the price.
    # On the 2024 validation year the plain price target gave a slightly worse MAE but
    # a better daily shape (0.853 vs 0.830 correlation), and shape is what dispatch needs.
    difference_target: bool = False
    train_years: float = 4.0


def fit(train: pd.DataFrame, features: list[str], p: ModelParams) -> HistGradientBoostingRegressor:
    target = train["price"] - train["lag24"] if p.difference_target else train["price"]
    model = HistGradientBoostingRegressor(
        loss="absolute_error", max_iter=p.max_iter, learning_rate=p.learning_rate,
        max_leaf_nodes=p.max_leaf_nodes, min_samples_leaf=p.min_samples_leaf,
        l2_regularization=p.l2_regularization, categorical_features=None, random_state=0,
    )
    return model.fit(train[features], target)


def walk_forward(
    table: pd.DataFrame, features: list[str], year: int, params: ModelParams | None = None
) -> pd.Series:
    """Forecast every hour of `year`, retraining at the start of each month.

    The model for a month is trained only on hours before that month began, so no
    forecast ever sees its own or a later price.
    """
    p = params or ModelParams()
    out = []
    usable = table.dropna(subset=["price", "lag24"])
    for month in range(1, 13):
        start = pd.Timestamp(year=year, month=month, day=1, tz=UTC)
        end = start + pd.offsets.MonthBegin(1)
        first = start - pd.Timedelta(days=365.25 * p.train_years)
        train = usable[(usable.index >= first) & (usable.index < start)]
        test = table[(table.index >= start) & (table.index < end)]
        test = test.dropna(subset=["lag24"])
        if train.empty or test.empty:
            continue
        pred = fit(train, features, p).predict(test[features])
        if p.difference_target:
            pred = pred + test["lag24"].to_numpy()
        out.append(pd.Series(pred, index=test.index))
    return pd.concat(out).rename("forecast") if out else pd.Series(dtype="float64")


def error_metrics(actual: pd.Series, forecast: pd.Series) -> dict[str, float]:
    both = pd.concat([actual.rename("a"), forecast.rename("f")], axis=1, sort=True).dropna()
    err = both["f"] - both["a"]
    # Arbitrage depends on the daily shape, so score that too: the correlation of
    # forecast and actual after removing each UTC day's mean.
    days = pd.DatetimeIndex(both.index).floor("D")
    shape = both - both.groupby(days).transform("mean")
    return {
        "hours": float(len(both)),
        "mae": float(err.abs().mean()),
        "rmse": float(np.sqrt((err**2).mean())),
        "bias": float(err.mean()),
        "shape_corr": float(shape["a"].corr(shape["f"])),
    }

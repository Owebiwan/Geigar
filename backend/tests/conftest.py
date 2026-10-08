from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bdo.config import Config, load_config
from bdo.optimizer.schedule import DayInputs

ROOT = Path(__file__).resolve().parents[2]
# The API module loads its config at import time, wherever pytest is started from.
os.environ.setdefault("BDO_CONFIG", str(ROOT / "config" / "battery.yaml"))


@pytest.fixture
def cfg() -> Config:
    return load_config(ROOT / "config" / "battery.yaml")


@pytest.fixture
def site_cfg() -> Config:
    return load_config(ROOT / "config" / "site.yaml")


def make_prices(start: str, periods: int, res: int = 60, seed: int = 0,
                negative: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=periods, freq=f"{res}min", tz="UTC")
    hours = np.arange(periods) * res / 60.0
    price = 80 + 45 * np.sin(hours / 24 * 4 * np.pi) + rng.normal(0, 12, periods)
    if negative:
        price[periods // 2 - periods // 8: periods // 2 + periods // 8] -= 160
    return pd.DataFrame({"ts_utc": idx, "price_eur_mwh": price, "resolution_min": res,
                         "source": "test"})


def make_inputs(periods: int = 96, res: int = 15, seed: int = 0, negative: bool = False,
                load: float = 0.0, solar: float = 0.0) -> DayInputs:
    prices = make_prices("2025-10-19T22:00:00Z", periods, res, seed, negative)
    idx = pd.DatetimeIndex(prices["ts_utc"])
    hours = np.arange(periods) * res / 60.0
    load_s = pd.Series(load * (0.6 + 0.4 * np.sin(hours / 24 * 2 * np.pi) ** 2), index=idx)
    solar_s = pd.Series(solar * np.clip(np.sin((hours - 6) / 12 * np.pi), 0, None), index=idx)
    return DayInputs.from_prices(prices, load_s, solar_s)

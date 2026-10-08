"""Synthetic commercial load profile shaped like the BDEW G0 standard profile.

G0 ("Gewerbe allgemein") has a weekday daytime plateau, a lower Saturday, a flat
Sunday and a mild winter uplift. The shape below reproduces those features and is
scaled so a full year sums to the configured annual consumption. It is a synthetic
stand-in, not the official BDEW table.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bdo.timeutil import BERLIN, UTC


def _shape(local: pd.DatetimeIndex) -> np.ndarray:
    hour = np.asarray(local.hour + local.minute / 60.0)
    dow = np.asarray(local.dayofweek)
    doy = np.asarray(local.dayofyear)
    base = 0.35
    morning = 1 / (1 + np.exp(-(hour - 7.0) * 1.6))
    evening = 1 / (1 + np.exp((hour - 18.0) * 1.3))
    lunch_dip = 1 - 0.12 * np.exp(-((hour - 12.75) ** 2) / 0.6)
    weekday = base + 0.65 * morning * evening * lunch_dip
    saturday = base + 0.30 * morning * (1 / (1 + np.exp((hour - 14.0) * 1.3)))
    sunday = np.full_like(hour, base)
    shape = np.where(dow < 5, weekday, np.where(dow == 5, saturday, sunday))
    season = 1 + 0.10 * np.cos(2 * np.pi * (doy - 15) / 365.25)
    return np.asarray(shape * season, dtype=float)


def load_kw(index: pd.DatetimeIndex, annual_kwh: float) -> pd.Series:
    """Average site load in kW for each interval start in `index` (UTC)."""
    if annual_kwh <= 0:
        return pd.Series(0.0, index=index)
    year = index[0].tz_convert(BERLIN).year
    ref = pd.date_range(f"{year}-01-01", f"{year + 1}-01-01", freq="15min", tz=BERLIN,
                        inclusive="left")
    scale = annual_kwh / (_shape(ref).sum() * 0.25)
    return pd.Series(_shape(index.tz_convert(BERLIN)) * scale, index=index.tz_convert(UTC))

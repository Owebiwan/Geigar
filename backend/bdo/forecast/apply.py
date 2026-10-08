"""Turn an hourly price forecast into the guess the optimizer plans on."""

from __future__ import annotations

import numpy as np
import pandas as pd

from bdo.optimizer.schedule import DayInputs
from bdo.timeutil import BERLIN


def day_guess(
    inputs: DayInputs, hourly: pd.Series, previous: DayInputs | None
) -> np.ndarray | None:
    """Forecast prices on the day's own intervals, or None if any hour has no forecast.

    A forecast that already carries quarter-hour values is used as it is. An hourly
    forecast on a 15-minute day gets yesterday's deviation of each quarter from its
    hourly mean added, which carries the ramp-driven saw-tooth inside the hour.
    """
    direct = hourly.reindex(inputs.index).to_numpy(dtype=float)
    if not np.isnan(direct).any():
        return direct  # the forecast already has this day's resolution
    hours = inputs.index.floor("h")
    base = hourly.reindex(hours).to_numpy(dtype=float)
    if np.isnan(base).any():
        return None
    if inputs.dt_h >= 1.0 or previous is None or previous.dt_h >= 1.0:
        return base
    prev = pd.Series(previous.price, index=previous.index)
    deviation = prev - prev.groupby(previous.index.floor("h")).transform("mean")
    deviation.index = previous.index.tz_convert(BERLIN).strftime("%H:%M")
    deviation = deviation[~deviation.index.duplicated()]
    shift = deviation.reindex(inputs.index.tz_convert(BERLIN).strftime("%H:%M")).to_numpy()
    return base + np.nan_to_num(shift, nan=0.0)

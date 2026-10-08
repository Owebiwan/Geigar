"""Baselines the optimizer has to beat (BL-1) plus a no-foresight reference."""

from __future__ import annotations

import numpy as np
import pandas as pd

from bdo.config import BatteryConfig, SiteConfig
from bdo.optimizer.model import InfeasibleError, solve_day
from bdo.optimizer.schedule import DayInputs, Schedule, settle
from bdo.timeutil import BERLIN


def naive(
    inputs: DayInputs, battery: BatteryConfig, site: SiteConfig, soc0: float, hours: int = 4
) -> Schedule:
    """Charge at full power in the cheapest hours, discharge in the most expensive ones."""
    # Floor in UTC: Berlin offsets are whole hours, and local flooring is ambiguous
    # in the repeated hour of the autumn DST change.
    local_hour = inputs.index.floor("h")
    hourly = pd.Series(inputs.price, index=local_hour).groupby(level=0).mean()
    cheap = set(hourly.nsmallest(hours).index)
    dear = set(hourly.drop(index=list(cheap)).nlargest(hours).index)
    power = np.zeros(len(inputs.index))
    power[local_hour.isin(cheap)] = battery.max_charge_kw
    power[local_hour.isin(dear)] = -battery.max_discharge_kw
    return settle(power, inputs, battery, site, soc0, "naive")


def idle(inputs: DayInputs, battery: BatteryConfig, site: SiteConfig, soc0: float) -> Schedule:
    """The site without a battery. Its cash flow is the reference for battery value."""
    return settle(np.zeros(len(inputs.index)), inputs, battery, site, soc0, "idle")


def _prices_by_clock(previous: DayInputs, today: DayInputs) -> np.ndarray:
    """Yesterday's price at the same local clock time for each of today's intervals."""
    prev = pd.Series(previous.price, index=previous.index.tz_convert(BERLIN).strftime("%H:%M"))
    prev = prev[~prev.index.duplicated()]
    floor = "h" if previous.dt_h >= 1.0 else "15min"
    keys = today.index.floor(floor).tz_convert(BERLIN).strftime("%H:%M")
    out = prev.reindex(keys).to_numpy(dtype=float)
    # A clock time that did not exist yesterday (DST change) takes the day's mean.
    return np.where(np.isnan(out), np.nanmean(out), out)


def persistence(
    inputs: DayInputs,
    previous: DayInputs | None,
    battery: BatteryConfig,
    site: SiteConfig,
    soc0: float,
    mtd_peak_kw: float = 0.0,
) -> Schedule:
    """Plan against yesterday's prices, then get paid at today's real prices.

    This is what the optimizer earns with the simplest possible forecast and no
    knowledge of the cleared auction, so it brackets the perfect-foresight result.
    """
    guess = None if previous is None else _prices_by_clock(previous, inputs)
    return plan_on_guess(inputs, guess, battery, site, soc0, mtd_peak_kw, "persistence")


def plan_on_guess(
    inputs: DayInputs,
    guess: np.ndarray | None,
    battery: BatteryConfig,
    site: SiteConfig,
    soc0: float,
    mtd_peak_kw: float,
    strategy: str,
) -> Schedule:
    """Optimise against guessed prices, then settle the plan at the real ones.

    With no guess the battery stays idle for the day.
    """
    idle_plan = np.zeros(len(inputs.index))
    if guess is None:
        return settle(idle_plan, inputs, battery, site, soc0, strategy)
    believed = DayInputs(inputs.index, inputs.dt_h, guess, inputs.load_kw, inputs.solar_kw)
    try:
        plan = solve_day(believed, battery, site, soc0, mtd_peak_kw)
    except InfeasibleError:
        return settle(idle_plan, inputs, battery, site, soc0, strategy)
    # Only the battery plan is carried over; curtailment reacts to the real price.
    return settle(plan.frame["power_kw"].to_numpy(), inputs, battery, site, soc0, strategy)

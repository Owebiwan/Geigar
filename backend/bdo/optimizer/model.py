"""Day-ahead dispatch optimisation as a mixed-integer linear program (OP-1..OP-5).

Decision variables per interval: charge and discharge power, grid import and export,
solar curtailment and one binary that selects charge or discharge mode. Stored energy
has one value per interval boundary.

Why a binary and not a cost penalty (OP-3): with negative prices the plain LP is paid
to consume, so it charges and discharges at once to burn energy in the conversion
losses. A penalty would have to exceed the most negative price ever seen to stop
that. One binary per interval removes the behaviour outright and HiGHS still solves
a 15-minute day in a fraction of a second.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache

import cvxpy as cp
import numpy as np

from bdo.config import BatteryConfig, SiteConfig
from bdo.optimizer.schedule import DayInputs, Schedule, settle


class InfeasibleError(RuntimeError):
    pass


@dataclass
class _Problem:
    problem: cp.Problem
    price: cp.Parameter
    load: cp.Parameter
    solar: cp.Parameter
    e0: cp.Parameter
    e_end: cp.Parameter
    mtd_peak: cp.Parameter
    charge: cp.Variable
    discharge: cp.Variable
    curtail: cp.Variable


@lru_cache(maxsize=64)
def _build(n: int, dt: float, battery_json: str, site_json: str) -> _Problem:
    b = BatteryConfig.model_validate_json(battery_json)
    s = SiteConfig.model_validate_json(site_json)

    price = cp.Parameter(n)
    load = cp.Parameter(n, nonneg=True)
    solar = cp.Parameter(n, nonneg=True)
    e0 = cp.Parameter(nonneg=True)
    e_end = cp.Parameter(nonneg=True)
    mtd_peak = cp.Parameter(nonneg=True)

    c = cp.Variable(n, nonneg=True)
    d = cp.Variable(n, nonneg=True)
    imp = cp.Variable(n, nonneg=True)
    exp = cp.Variable(n, nonneg=True)
    curtail = cp.Variable(n, nonneg=True)
    charging = cp.Variable(n, boolean=True)
    e = cp.Variable(n + 1)
    peak = cp.Variable(nonneg=True)

    cons = [
        e[0] == e0,
        e[1:] == e[:-1] + (b.eta_charge * c - d / b.eta_discharge) * dt,
        e >= b.soc_min * b.capacity_kwh,
        e <= b.soc_max * b.capacity_kwh,
        e[n] == e_end,
        c <= b.max_charge_kw * charging,
        d <= b.max_discharge_kw * (1 - charging),
        # Site power balance: solar serves load and battery first, the rest is exported.
        imp - exp == load + c - d - (solar - curtail),
        curtail <= solar,
        imp <= b.grid_import_kw,
        exp <= b.grid_export_kw,
        peak >= imp,
        peak >= mtd_peak,
    ]
    if b.max_cycles_per_day is not None:
        cons.append(cp.sum(d) * dt <= b.max_cycles_per_day * b.capacity_kwh * (n * dt / 24.0))

    energy = cp.sum(cp.multiply(price, exp - imp)) * dt / 1000.0
    degradation = b.degradation_eur_per_kwh * cp.sum(c + d) * dt
    grid_fee = s.grid_fee_eur_per_mwh / 1000.0 * cp.sum(imp) * dt
    # Only the increase over the month-to-date peak costs money; the rest is sunk.
    demand = s.demand_charge_eur_per_kw_year / 12.0 * (peak - mtd_peak)
    problem = cp.Problem(cp.Maximize(energy - degradation - grid_fee - demand), cons)
    return _Problem(problem, price, load, solar, e0, e_end, mtd_peak, c, d, curtail)


def solve_day(
    inputs: DayInputs,
    battery: BatteryConfig,
    site: SiteConfig,
    soc0: float,
    mtd_peak_kw: float = 0.0,
    soc_end: float | None = None,
    strategy: str = "optimizer",
) -> Schedule:
    """Optimise one horizon and return the schedule settled through the battery physics."""
    n = len(inputs.index)
    p = _build(n, inputs.dt_h, battery.model_dump_json(), site.model_dump_json())
    soc0 = float(np.clip(soc0, battery.soc_min, battery.soc_max))
    p.price.value = inputs.price
    p.load.value = inputs.load_kw
    p.solar.value = inputs.solar_kw
    p.e0.value = soc0 * battery.capacity_kwh
    p.e_end.value = (battery.soc_end if soc_end is None else soc_end) * battery.capacity_kwh
    p.mtd_peak.value = max(0.0, mtd_peak_kw)

    started = time.perf_counter()
    p.problem.solve(solver=cp.HIGHS, highs_options={"mip_rel_gap": 1e-7, "threads": 1})
    elapsed = time.perf_counter() - started
    if p.problem.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise InfeasibleError(f"solver status {p.problem.status}")

    # Round away solver noise so a 1e-9 kW "charge" cannot flip an idle interval.
    power = np.round(np.asarray(p.charge.value) - np.asarray(p.discharge.value), 6)
    curtail = np.round(np.asarray(p.curtail.value), 6)
    schedule = settle(power, inputs, battery, site, soc0, strategy, curtail)
    schedule.solve_seconds = elapsed
    schedule.status = str(p.problem.status)
    schedule.meta["objective_eur"] = float(p.problem.value)
    return schedule

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

from bdo.config import BatteryConfig, ReservesConfig, SiteConfig
from bdo.optimizer.schedule import RESERVE_PRODUCTS, DayInputs, Schedule, settle


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
    reserve: dict[str, cp.Variable]
    reserve_price: dict[str, cp.Parameter]
    reserve_cap: dict[str, cp.Parameter]


@lru_cache(maxsize=64)
def _build(
    n: int, dt: float, battery_json: str, site_json: str,
    reserves_json: str | None = None, blocks: tuple[int, ...] = (),
) -> _Problem:
    b = BatteryConfig.model_validate_json(battery_json)
    s = SiteConfig.model_validate_json(site_json)
    rc = None if reserves_json is None else ReservesConfig.model_validate_json(reserves_json)

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

    reserve: dict[str, cp.Variable] = {}
    reserve_price: dict[str, cp.Parameter] = {}
    reserve_cap: dict[str, cp.Parameter] = {}
    reserve_income: cp.Expression | float = 0.0
    if rc is not None:
        for name in RESERVE_PRODUCTS:
            reserve[name] = cp.Variable(n, nonneg=True)
            reserve_price[name] = cp.Parameter(n)  # EUR per kW for the interval
            reserve_cap[name] = cp.Parameter(n, nonneg=True)  # 0 where the product is off
            cons.append(reserve[name] <= reserve_cap[name])
            # A product is sold as one constant band per 4-hour block.
            same = [t for t in range(n - 1) if blocks[t] == blocks[t + 1]]
            if same:
                cons.append(reserve[name][same] == reserve[name][[t + 1 for t in same]])
        fcr, up, down = (reserve[k] for k in RESERVE_PRODUCTS)
        # Power: the day-ahead position plus everything sold as reserve must fit the inverter
        # and the grid connection. FCR is symmetric and counts in both directions.
        cons += [
            (d - c) + fcr + up <= b.max_discharge_kw,
            (c - d) + fcr + down <= b.max_charge_kw,
            exp + fcr + up <= b.grid_export_kw,
            imp + fcr + down <= b.grid_import_kw,
        ]
        # Energy: a full activation for the required duration must stay inside the SoC window.
        need_up = (fcr * rc.fcr_energy_hours + up * rc.afrr_energy_hours) / b.eta_discharge
        need_down = (fcr * rc.fcr_energy_hours + down * rc.afrr_energy_hours) * b.eta_charge
        for stored in (e[:-1], e[1:]):
            cons += [stored - need_up >= b.soc_min * b.capacity_kwh,
                     stored + need_down <= b.soc_max * b.capacity_kwh]
        wear = b.degradation_eur_per_kwh * rc.fcr_throughput_kwh_per_kw_h * cp.sum(fcr) * dt
        reserve_income = sum(reserve_price[k] @ reserve[k] for k in RESERVE_PRODUCTS) - wear

    energy = cp.sum(cp.multiply(price, exp - imp)) * dt / 1000.0
    degradation = b.degradation_eur_per_kwh * cp.sum(c + d) * dt
    grid_fee = s.grid_fee_eur_per_mwh / 1000.0 * cp.sum(imp) * dt
    # Only the increase over the month-to-date peak costs money; the rest is sunk.
    demand = s.demand_charge_eur_per_kw_year / 12.0 * (peak - mtd_peak)
    objective = energy - degradation - grid_fee - demand + reserve_income
    problem = cp.Problem(cp.Maximize(objective), cons)
    return _Problem(problem, price, load, solar, e0, e_end, mtd_peak, c, d, curtail,
                    reserve, reserve_price, reserve_cap)


def solve_day(
    inputs: DayInputs,
    battery: BatteryConfig,
    site: SiteConfig,
    soc0: float,
    mtd_peak_kw: float = 0.0,
    soc_end: float | None = None,
    strategy: str = "optimizer",
    reserves: ReservesConfig | None = None,
) -> Schedule:
    """Optimise one horizon and return the schedule settled through the battery physics.

    With `reserves` and reserve prices on the inputs, FCR and aFRR capacity are sold
    together with the day-ahead position.
    """
    n = len(inputs.index)
    multi = reserves is not None and reserves.enabled and inputs.reserve_prices is not None
    if multi:
        assert reserves is not None
        p = _build(n, inputs.dt_h, battery.model_dump_json(), site.model_dump_json(),
                   reserves.model_dump_json(), inputs.reserve_blocks())
    else:
        p = _build(n, inputs.dt_h, battery.model_dump_json(), site.model_dump_json())
    soc0 = float(np.clip(soc0, battery.soc_min, battery.soc_max))
    p.price.value = inputs.price
    p.load.value = inputs.load_kw
    p.solar.value = inputs.solar_kw
    p.e0.value = soc0 * battery.capacity_kwh
    p.e_end.value = (battery.soc_end if soc_end is None else soc_end) * battery.capacity_kwh
    p.mtd_peak.value = max(0.0, mtd_peak_kw)
    if multi:
        assert reserves is not None and inputs.reserve_prices is not None
        on = {"fcr": reserves.fcr, "afrr_up": reserves.afrr, "afrr_dn": reserves.afrr}
        limit = max(battery.max_charge_kw, battery.max_discharge_kw)
        for name in RESERVE_PRODUCTS:
            raw = inputs.reserve_prices[name]
            offered = on[name] & ~np.isnan(raw)
            # EUR per MW per 15 minutes -> EUR per kW for one interval.
            p.reserve_price[name].value = np.nan_to_num(raw, nan=0.0) / 1000.0 * (
                inputs.dt_h / 0.25)
            p.reserve_cap[name].value = np.where(offered, limit, 0.0)

    started = time.perf_counter()
    p.problem.solve(solver=cp.HIGHS, highs_options={"mip_rel_gap": 1e-7, "threads": 1})
    elapsed = time.perf_counter() - started
    if p.problem.status not in (cp.OPTIMAL, cp.OPTIMAL_INACCURATE):
        raise InfeasibleError(f"solver status {p.problem.status}")

    # Round away solver noise so a 1e-9 kW "charge" cannot flip an idle interval.
    power = np.round(np.asarray(p.charge.value) - np.asarray(p.discharge.value), 6)
    curtail = np.round(np.asarray(p.curtail.value), 6)
    sold = None
    if multi:
        sold = {k: np.round(np.clip(np.asarray(p.reserve[k].value), 0.0, None), 6)
                for k in RESERVE_PRODUCTS}
    schedule = settle(power, inputs, battery, site, soc0, strategy, curtail, sold, reserves)
    schedule.solve_seconds = elapsed
    schedule.status = str(p.problem.status)
    schedule.meta["objective_eur"] = float(p.problem.value)
    return schedule

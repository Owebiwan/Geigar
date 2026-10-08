"""Schedule container, physics-based settlement and constraint checks.

Sign convention everywhere: `power_kw` > 0 charges the battery, < 0 discharges it.
`grid_kw` > 0 is import from the grid, < 0 is export.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from bdo.config import BatteryConfig, SiteConfig

TOL = 1e-4


@dataclass
class DayInputs:
    """Everything the optimizer needs for one horizon. All arrays share `index`."""

    index: pd.DatetimeIndex  # UTC interval starts
    dt_h: float
    price: np.ndarray  # EUR/MWh
    load_kw: np.ndarray
    solar_kw: np.ndarray

    @classmethod
    def from_prices(
        cls,
        prices: pd.DataFrame,
        load_kw: pd.Series | None = None,
        solar_kw: pd.Series | None = None,
    ) -> DayInputs:
        index = pd.DatetimeIndex(prices["ts_utc"])
        res = prices["resolution_min"].unique()
        if len(res) != 1:
            raise ValueError("a horizon must have one price resolution")
        zeros = np.zeros(len(index))
        return cls(
            index=index,
            dt_h=float(res[0]) / 60.0,
            price=prices["price_eur_mwh"].to_numpy(dtype=float),
            load_kw=zeros if load_kw is None else load_kw.to_numpy(dtype=float),
            solar_kw=zeros if solar_kw is None else solar_kw.to_numpy(dtype=float),
        )

    def tail(self, start: int) -> DayInputs:
        return DayInputs(
            self.index[start:], self.dt_h, self.price[start:],
            self.load_kw[start:], self.solar_kw[start:],
        )


@dataclass
class Schedule:
    frame: pd.DataFrame
    strategy: str
    solve_seconds: float = 0.0
    status: str = "settled"
    meta: dict[str, object] = field(default_factory=dict)

    @property
    def soc_end(self) -> float:
        return float(self.frame["soc"].iloc[-1])

    @property
    def cash_flow_eur(self) -> float:
        return float(self.frame["cash_flow_eur"].sum())

    @property
    def peak_import_kw(self) -> float:
        return float(self.frame["grid_kw"].clip(lower=0).max())


def settle(
    power_kw: np.ndarray,
    inputs: DayInputs,
    battery: BatteryConfig,
    site: SiteConfig,
    soc0: float,
    strategy: str,
    curtail_kw: np.ndarray | None = None,
) -> Schedule:
    """Run a requested power plan through the battery physics and price the result.

    Requests are clamped to power, SoC and grid limits, so no strategy can produce an
    infeasible schedule. Every strategy is priced by this one function, which keeps
    the comparison between optimizer and baselines fair.

    Solar curtailment: the optimizer passes its own decision in `curtail_kw`. Without
    one, surplus solar is curtailed instead of exported whenever the price is negative,
    which is what any PV inverter with a price signal does.
    """
    n = len(inputs.index)
    dt = inputs.dt_h
    e_min, e_max = battery.soc_min * battery.capacity_kwh, battery.soc_max * battery.capacity_kwh
    e = float(np.clip(soc0 * battery.capacity_kwh, e_min, e_max))
    cols: dict[str, np.ndarray] = {
        k: np.zeros(n)
        for k in ("charge_kw", "discharge_kw", "soc_start", "soc", "grid_kw", "curtail_kw")
    }
    for t in range(n):
        solar = inputs.solar_kw[t]
        planned_curtail = 0.0
        if curtail_kw is not None:
            planned_curtail = float(np.clip(curtail_kw[t], 0.0, solar))
        site_net = inputs.load_kw[t] - (solar - planned_curtail)
        req = float(power_kw[t])
        c = d = 0.0
        if req > 0:
            c = min(req, battery.max_charge_kw, (e_max - e) / (battery.eta_charge * dt))
            c = max(0.0, min(c, battery.grid_import_kw - site_net))
        elif req < 0:
            d = min(-req, battery.max_discharge_kw, (e - e_min) * battery.eta_discharge / dt)
            d = max(0.0, min(d, battery.grid_export_kw + site_net))
        cols["soc_start"][t] = e / battery.capacity_kwh
        e += (battery.eta_charge * c - d / battery.eta_discharge) * dt
        net = site_net + c - d
        surplus = max(0.0, -net)
        if curtail_kw is None and inputs.price[t] < 0:
            curtail = min(surplus, solar)
        else:
            curtail = min(max(0.0, surplus - battery.grid_export_kw), solar - planned_curtail)
        cols["charge_kw"][t], cols["discharge_kw"][t] = c, d
        cols["soc"][t] = e / battery.capacity_kwh
        cols["grid_kw"][t] = net + curtail
        cols["curtail_kw"][t] = curtail + planned_curtail
    frame = pd.DataFrame(
        {
            "ts_utc": inputs.index,
            "dt_h": dt,
            "price_eur_mwh": inputs.price,
            "power_kw": cols["charge_kw"] - cols["discharge_kw"],
            **cols,
            "load_kw": inputs.load_kw,
            "solar_kw": inputs.solar_kw,
        }
    )
    imp = frame["grid_kw"].clip(lower=0)
    throughput = (frame["charge_kw"] + frame["discharge_kw"]) * dt
    frame["energy_eur"] = -frame["price_eur_mwh"] / 1000.0 * frame["grid_kw"] * dt
    frame["grid_fee_eur"] = site.grid_fee_eur_per_mwh / 1000.0 * imp * dt
    frame["degradation_eur"] = battery.degradation_eur_per_kwh * throughput
    frame["cash_flow_eur"] = frame["energy_eur"] - frame["grid_fee_eur"] - frame["degradation_eur"]
    return Schedule(frame=frame, strategy=strategy)


def check_constraints(frame: pd.DataFrame, battery: BatteryConfig, tol: float = TOL) -> list[str]:
    """Return a description of every physical limit a schedule breaks (NF-2)."""
    out: list[str] = []
    if (frame["charge_kw"] > battery.max_charge_kw + tol).any():
        out.append("charge power above limit")
    if (frame["discharge_kw"] > battery.max_discharge_kw + tol).any():
        out.append("discharge power above limit")
    if ((frame["charge_kw"] < -tol) | (frame["discharge_kw"] < -tol)).any():
        out.append("negative charge or discharge power")
    if ((frame["charge_kw"] > tol) & (frame["discharge_kw"] > tol)).any():
        out.append("simultaneous charge and discharge")
    soc_tol = tol / battery.capacity_kwh * 10
    if (frame["soc"] < battery.soc_min - soc_tol).any():
        out.append("SoC below window")
    if (frame["soc"] > battery.soc_max + soc_tol).any():
        out.append("SoC above window")
    if (frame["grid_kw"] > battery.grid_import_kw + tol).any():
        out.append("grid import above connection limit")
    if (frame["grid_kw"] < -battery.grid_export_kw - tol).any():
        out.append("grid export above connection limit")
    return out

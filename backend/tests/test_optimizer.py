from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bdo.optimizer import baselines
from bdo.optimizer.model import InfeasibleError, solve_day
from bdo.optimizer.rolling import interval_at, needs_reoptimisation, reoptimise
from bdo.optimizer.schedule import DayInputs, check_constraints, settle

from .conftest import make_inputs


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("periods,res", [(96, 15), (24, 60), (92, 15), (100, 15), (25, 60)])
def test_optimizer_never_violates_limits(cfg, seed, periods, res):
    inputs = make_inputs(periods, res, seed, negative=seed % 2 == 0)
    plan = solve_day(inputs, cfg.battery, cfg.site, 0.5)
    assert check_constraints(plan.frame, cfg.battery) == []
    assert plan.soc_end == pytest.approx(cfg.battery.soc_end, abs=1e-6)
    # Settling the solver's plan through the physics must not change what it earns.
    assert plan.cash_flow_eur == pytest.approx(plan.meta["objective_eur"], abs=1e-3)


@pytest.mark.parametrize("seed", range(4))
def test_site_schedule_respects_grid_limit_and_peak(site_cfg, seed):
    inputs = make_inputs(96, 15, seed, load=600, solar=400)
    plan = solve_day(inputs, site_cfg.battery, site_cfg.site, 0.5)
    assert check_constraints(plan.frame, site_cfg.battery) == []
    idle = baselines.idle(inputs, site_cfg.battery, site_cfg.site, 0.5)
    assert plan.peak_import_kw < idle.peak_import_kw  # demand charge makes it shave the peak
    demand = site_cfg.site.demand_charge_eur_per_kw_year / 12 * plan.peak_import_kw
    assert plan.cash_flow_eur - demand == pytest.approx(plan.meta["objective_eur"], abs=1e-3)


def test_no_simultaneous_charge_and_discharge_on_negative_prices(cfg):
    inputs = make_inputs(96, 15, 3, negative=True)
    assert inputs.price.min() < 0
    frame = solve_day(inputs, cfg.battery, cfg.site, 0.5).frame
    assert not ((frame["charge_kw"] > 1e-6) & (frame["discharge_kw"] > 1e-6)).any()


def test_charges_when_cheap_and_discharges_when_dear(cfg):
    price = np.array([10.0] * 4 + [200.0] * 4)
    idx = pd.date_range("2025-06-01", periods=8, freq="h", tz="UTC")
    plan = solve_day(DayInputs(idx, 1.0, price, np.zeros(8), np.zeros(8)), cfg.battery, cfg.site,
                     0.5)
    f = plan.frame
    assert f["power_kw"].iloc[:4].sum() > 0 and f["power_kw"].iloc[4:].sum() < 0
    assert plan.cash_flow_eur > 0


def test_flat_prices_mean_no_dispatch(cfg):
    idx = pd.date_range("2025-06-01", periods=24, freq="h", tz="UTC")
    inputs = DayInputs(idx, 1.0, np.full(24, 50.0), np.zeros(24), np.zeros(24))
    plan = solve_day(inputs, cfg.battery, cfg.site, 0.5)
    assert plan.frame["power_kw"].abs().max() < 1e-6


def test_cycle_limit_is_respected(cfg):
    cfg.battery.max_cycles_per_day = 0.5
    plan = solve_day(make_inputs(96, 15, 1), cfg.battery, cfg.site, 0.5)
    discharged = (plan.frame["discharge_kw"] * plan.frame["dt_h"]).sum()
    assert discharged <= 0.5 * cfg.battery.capacity_kwh + 1e-3


def test_one_day_solves_in_under_two_seconds(cfg):
    plan = solve_day(make_inputs(96, 15, 5, negative=True), cfg.battery, cfg.site, 0.5)
    assert plan.solve_seconds < 2.0


def test_solar_is_stored_before_export(site_cfg):
    site_cfg.site.demand_charge_eur_per_kw_year = 0
    idx = pd.date_range("2025-06-01T08:00Z", periods=8, freq="h")
    price = np.array([-20.0] * 4 + [150.0] * 4)
    solar = np.array([300.0] * 4 + [0.0] * 4)
    inputs = DayInputs(idx, 1.0, price, np.zeros(8), solar)
    f = solve_day(inputs, site_cfg.battery, site_cfg.site, 0.2, soc_end=0.2).frame
    assert f["charge_kw"].iloc[:4].sum() > 0  # solar goes into the battery at negative prices
    # Nothing is exported at a negative price: the surplus is stored, the rest curtailed.
    assert f["grid_kw"].iloc[:4].min() >= -1e-6
    assert f["curtail_kw"].iloc[:4].sum() > 0


def test_infeasible_end_soc_raises(cfg):
    idx = pd.date_range("2025-06-01", periods=1, freq="15min", tz="UTC")
    inputs = DayInputs(idx, 0.25, np.array([50.0]), np.zeros(1), np.zeros(1))
    with pytest.raises(InfeasibleError):
        solve_day(inputs, cfg.battery, cfg.site, 0.1, soc_end=0.9)


def test_settle_clamps_impossible_requests(cfg):
    inputs = make_inputs(96, 15)
    wild = np.where(np.arange(96) % 2 == 0, 5000.0, -5000.0)
    for power in (wild, np.full(96, 5000.0), np.full(96, -5000.0)):
        frame = settle(power, inputs, cfg.battery, cfg.site, 0.5, "wild").frame
        assert check_constraints(frame, cfg.battery) == []


def test_check_constraints_reports_each_violation(cfg):
    frame = settle(np.zeros(4), make_inputs(4, 15), cfg.battery, cfg.site, 0.5, "x").frame
    frame.loc[0, "charge_kw"] = 9999
    frame.loc[0, "discharge_kw"] = 9999
    frame.loc[1, "soc"] = 0.99
    frame.loc[2, "soc"] = 0.01
    frame.loc[3, "grid_kw"] = 9999
    frame.loc[2, "grid_kw"] = -9999
    problems = check_constraints(frame, cfg.battery)
    assert len(problems) == 7


@pytest.mark.parametrize("seed", range(4))
def test_optimizer_beats_or_matches_baselines(cfg, seed):
    inputs = make_inputs(96, 15, seed, negative=seed == 0)
    opt = solve_day(inputs, cfg.battery, cfg.site, 0.5)
    naive = baselines.naive(inputs, cfg.battery, cfg.site, 0.5)
    assert check_constraints(naive.frame, cfg.battery) == []
    # Compare on equal terms: value the energy the naive rule leaves in the battery at zero.
    assert opt.cash_flow_eur >= naive.cash_flow_eur - 1e-6 or naive.soc_end < 0.5


def test_persistence_plans_on_yesterday_and_settles_on_today(cfg):
    today, yesterday = make_inputs(96, 15, 1), make_inputs(24, 60, 2)
    pers = baselines.persistence(today, yesterday, cfg.battery, cfg.site, 0.5)
    assert check_constraints(pers.frame, cfg.battery) == []
    assert np.allclose(pers.frame["price_eur_mwh"], today.price)
    assert pers.cash_flow_eur <= solve_day(today, cfg.battery, cfg.site, 0.5).cash_flow_eur + 1e-6
    first = baselines.persistence(today, None, cfg.battery, cfg.site, 0.5)
    assert first.frame["power_kw"].abs().sum() == 0


def test_interval_lookup_and_drift_detection(cfg):
    inputs = make_inputs(96, 15, 2)
    plan = solve_day(inputs, cfg.battery, cfg.site, 0.5)
    t0 = inputs.index[0]
    assert interval_at(plan.frame, t0) == 0
    assert interval_at(plan.frame, t0 + pd.Timedelta(minutes=29)) == 1
    assert interval_at(plan.frame, t0 - pd.Timedelta(seconds=1)) is None
    assert interval_at(plan.frame, t0 + pd.Timedelta(hours=24)) is None
    now = t0 + pd.Timedelta(hours=6)
    planned = float(plan.frame["soc_start"].iloc[24])
    assert not needs_reoptimisation(plan.frame, now, planned + 0.01, 0.05)
    assert needs_reoptimisation(plan.frame, now, planned + 0.2, 0.05)
    assert not needs_reoptimisation(plan.frame, t0 + pd.Timedelta(days=2), 0.5, 0.05)


def test_reoptimise_replans_the_rest_of_the_day_from_measured_soc(cfg):
    inputs = make_inputs(96, 15, 2)
    plan = solve_day(inputs, cfg.battery, cfg.site, 0.5)
    now = inputs.index[40] + pd.Timedelta(minutes=5)
    row = plan.frame.iloc[40]
    planned_now = row["soc_start"] + (row["soc"] - row["soc_start"]) / 3
    new = reoptimise(cfg, plan, inputs, now, measured_soc=planned_now - 0.1)
    assert len(new.frame) == 96
    assert new.frame.iloc[:41].equals(plan.frame.iloc[:41])
    # The rest of interval 40 still runs as planned, so the new plan starts 0.1 below its end.
    expected = max(cfg.battery.soc_min, row["soc"] - 0.1)
    assert new.frame["soc_start"].iloc[41] == pytest.approx(expected)
    assert new.soc_end == pytest.approx(cfg.battery.soc_end, abs=1e-6)
    assert check_constraints(new.frame.iloc[41:], cfg.battery) == []
    late = reoptimise(cfg, plan, inputs, inputs.index[95], 0.5)
    assert late is plan

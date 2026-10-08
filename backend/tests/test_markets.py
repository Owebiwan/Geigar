from __future__ import annotations

from datetime import date

import httpx
import numpy as np
import pandas as pd
import pytest

from bdo.backtest import metrics, results
from bdo.backtest.runner import run_backtest
from bdo.backtest.sweep import with_markets
from bdo.data import entsoe, reserves
from bdo.device.controller import Controller
from bdo.optimizer.model import solve_day
from bdo.optimizer.schedule import check_constraints
from bdo.timeutil import day_bounds_utc

from .conftest import make_inputs, make_prices
from .test_device import FakeDevice

CAPACITY_XML = """<Balancing_MarketDocument xmlns="urn:x">
<TimeSeries><flowDirection.direction>{direction}</flowDirection.direction>
<Period><timeInterval><start>2025-10-12T22:00Z</start><end>2025-10-13T22:00Z</end></timeInterval>
<resolution>PT15M</resolution>
<Point><position>1</position><quantity>213</quantity>
<procurement_Price.amount>1.11</procurement_Price.amount></Point>
<Point><position>17</position><quantity>132</quantity>
<procurement_Price.amount>3.24</procurement_Price.amount></Point>
</Period></TimeSeries></Balancing_MarketDocument>"""

IMBALANCE_XML = """<Balancing_MarketDocument xmlns="urn:x"><TimeSeries>
<Period><timeInterval><start>2025-10-13T00:00Z</start><end>2025-10-13T01:00Z</end></timeInterval>
<resolution>PT15M</resolution>
<Point><position>1</position><imbalance_Price.amount>156.31</imbalance_Price.amount></Point>
<Point><position>2</position><imbalance_Price.amount>-20</imbalance_Price.amount></Point>
<Point><position>4</position><imbalance_Price.amount>90</imbalance_Price.amount></Point>
</Period></TimeSeries></Balancing_MarketDocument>"""


def _reserve_frame(index: pd.DatetimeIndex, fcr=4.0, up=3.0, dn=2.0) -> pd.DataFrame:
    grid = pd.date_range(index[0].floor("D") - pd.Timedelta(days=1), periods=96 * 4, freq="15min")
    return pd.DataFrame({"fcr": fcr, "afrr_up": up, "afrr_dn": dn}, index=grid)


def test_capacity_prices_hold_for_their_whole_block():
    parsed = reserves.parse_capacity(CAPACITY_XML.format(direction="A03"))["A03"]
    assert len(parsed) == 96
    assert (parsed.iloc[:16] == 1.11).all() and (parsed.iloc[16:] == 3.24).all()


def test_imbalance_prices_parse_with_omitted_points():
    parsed = reserves.parse_imbalance(IMBALANCE_XML)
    assert list(parsed) == [156.31, -20.0, -20.0, 90.0]
    assert reserves.parse_imbalance("<a xmlns='urn:x'/>").empty


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_reserve_fetch_combines_products_and_splits_oversized_windows():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        seen.append((q["processType"], q["periodStart"], q["periodEnd"]))
        whole = (q["periodStart"], q["periodEnd"]) == ("202510120000", "202510140000")
        if q["processType"] == reserves.AFRR and whole:
            return httpx.Response(400, text="<text>The number of instances (128) exceeds the "
                                            "allowed maximum (100)</text>")
        if q["processType"] == reserves.FCR:
            return httpx.Response(200, text=CAPACITY_XML.format(direction="A03"))
        body = CAPACITY_XML.format(direction="A01") if q["periodStart"] == "202510120000" else (
            "<text>No matching data found</text>")
        return httpx.Response(200 if "A01" in body else 400, text=body)

    start, end = pd.Timestamp("2025-10-12T00:00Z"), pd.Timestamp("2025-10-14T00:00Z")
    frame = reserves.fetch_reserve_prices(start, end, "secret", _client(handler))
    assert (reserves.AFRR, "202510120000", "202510130000") in seen  # the retried first half
    assert list(frame.columns) == ["fcr", "afrr_up", "afrr_dn"]
    assert frame["fcr"].notna().sum() == 96 and frame["afrr_dn"].isna().all()
    assert frame.index.min() == pd.Timestamp("2025-10-12T22:00Z")


def test_entsoe_errors_never_carry_the_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="<text>Unauthorized for token top-secret</text>")

    start, end = pd.Timestamp("2025-10-12T00:00Z"), pd.Timestamp("2025-10-14T00:00Z")
    for call in (
        lambda: reserves.fetch_imbalance_prices(start, end, "top-secret", _client(handler)),
        lambda: entsoe.fetch_prices(start, end, "top-secret", client=_client(handler)),
    ):
        with pytest.raises(RuntimeError) as err:
            call()
        assert "top-secret" not in str(err.value) and "401" in str(err.value)


def test_balancing_data_is_cached_per_month_and_needs_the_token(tmp_path, monkeypatch):
    calls = []

    def fake(start, end, token, client=None):
        calls.append(start)
        return pd.Series(50.0, index=pd.date_range(start, end, freq="15min", inclusive="left"),
                         name="imbalance_eur_mwh")

    monkeypatch.setattr(reserves, "fetch_imbalance_prices", fake)
    monkeypatch.setenv("ENTSOE_TOKEN", "t")
    now = pd.Timestamp("2026-01-01T00:00Z")
    start, end = pd.Timestamp("2025-03-10T00:00Z"), pd.Timestamp("2025-04-02T00:00Z")
    first = reserves.ensure_imbalance_prices(start, end, tmp_path, now)
    reserves.ensure_imbalance_prices(start, end, tmp_path, now)
    assert len(calls) == 2 and first.index.min() == start and first.index.max() < end
    monkeypatch.delenv("ENTSOE_TOKEN")
    assert len(reserves.ensure_imbalance_prices(start, end, tmp_path, now)) == len(first)
    with pytest.raises(RuntimeError, match="ENTSOE_TOKEN"):
        reserves.ensure_reserve_prices(start, end, tmp_path, now)


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("periods,res", [(96, 15), (24, 60), (92, 15), (100, 15)])
def test_multi_market_schedule_can_always_deliver_what_it_sold(cfg, seed, periods, res):
    cfg = with_markets(cfg)
    cfg.reserves.afrr_energy_hours = [0.5, 1.0, 4.0][seed % 3]
    inputs = make_inputs(periods, res, seed, negative=seed % 2 == 0)
    rng = np.random.default_rng(seed)
    prices = _reserve_frame(inputs.index) * rng.uniform(0.2, 3.0, (96 * 4, 3))
    blocks = pd.DatetimeIndex(prices.index).tz_convert("Europe/Berlin")
    prices = prices.groupby([blocks.date, blocks.hour // 4]).transform("first")
    inputs = inputs.with_reserves(prices)
    plan = solve_day(inputs, cfg.battery, cfg.site, 0.5, reserves=cfg.reserves)
    assert check_constraints(plan.frame, cfg.battery, reserves=cfg.reserves) == []
    assert plan.cash_flow_eur == pytest.approx(plan.meta["objective_eur"], abs=1e-3)
    assert plan.frame[["fcr_kw", "afrr_up_kw", "afrr_dn_kw"]].to_numpy().sum() > 0
    base = solve_day(inputs, cfg.battery, cfg.site, 0.5)
    assert plan.cash_flow_eur >= base.cash_flow_eur - 1e-6
    assert base.frame["fcr_kw"].sum() == 0 and base.frame["fcr_eur"].sum() == 0


def test_reserve_products_can_be_switched_off_and_missing_prices_are_not_sold(cfg):
    cfg = with_markets(cfg)
    inputs = make_inputs(96, 15, 1)
    prices = _reserve_frame(inputs.index)
    prices.loc[prices.index >= inputs.index[48], "fcr"] = np.nan  # not published
    cfg.reserves.afrr = False
    frame = solve_day(inputs.with_reserves(prices), cfg.battery, cfg.site, 0.5,
                      reserves=cfg.reserves).frame
    assert frame["afrr_up_kw"].sum() == 0 and frame["afrr_dn_kw"].sum() == 0
    assert frame["fcr_kw"].iloc[:48].sum() > 0 and frame["fcr_kw"].iloc[48:].sum() == 0
    assert solve_day(inputs.with_reserves(None), cfg.battery, cfg.site, 0.5,
                     reserves=cfg.reserves).frame["fcr_kw"].sum() == 0


def test_reserve_checks_report_undeliverable_plans(cfg):
    cfg = with_markets(cfg)
    inputs = make_inputs(96, 15, 2).with_reserves(_reserve_frame(make_inputs(96, 15, 2).index))
    frame = solve_day(inputs, cfg.battery, cfg.site, 0.5, reserves=cfg.reserves).frame.copy()
    frame.loc[0, "fcr_kw"] = 5000.0
    frame["afrr_up_kw"] = 400.0
    frame["afrr_dn_kw"] = 400.0
    frame.loc[3, "afrr_dn_kw"] = -5.0
    problems = check_constraints(frame, cfg.battery, reserves=cfg.reserves)
    for text in ("negative reserve", "upward reserve exceeds", "downward reserve exceeds",
                 "not enough stored energy", "not enough headroom", "changes inside a 4-hour"):
        assert any(text in p for p in problems), text


def test_hash_only_changes_when_reserves_are_enabled(cfg):
    before = cfg.hash()
    cfg.reserves.afrr_energy_hours = 2.0
    assert cfg.hash() == before
    assert with_markets(cfg).hash() != before and cfg.reserves.enabled is False


def test_backtest_reports_markets_breakdown(cfg, tmp_path, monkeypatch):
    start, _ = day_bounds_utc(date(2022, 12, 31))
    _, end = day_bounds_utc(date(2023, 1, 12))
    hours = int((end - start) / pd.Timedelta(hours=1))
    prices = make_prices(start.isoformat(), hours, seed=6)
    grid = pd.date_range(start, end, freq="15min", inclusive="left")
    reserve = pd.DataFrame({"fcr": 4.0, "afrr_up": 3.0, "afrr_dn": 2.0}, index=grid)
    multi_cfg = with_markets(cfg)
    result = run_backtest(multi_cfg, 2023, prices, last_day=date(2023, 1, 12),
                          reserve_prices=reserve)
    assert result.violations == [] and "multi_market" in set(result.daily["strategy"])
    yearly = metrics.yearly(metrics.monthly(result.daily, multi_cfg), multi_cfg)
    m = metrics.markets(yearly)
    assert m is not None and m["total_eur"] > m["day_ahead_only_eur"] > 0
    parts = (m["energy_eur"] + m["fcr_eur"] + m["afrr_up_eur"] + m["afrr_dn_eur"]
             - m["degradation_eur"] - m["reserve_wear_eur"])
    assert parts == pytest.approx(m["total_eur"], rel=1e-9)
    assert metrics.markets(yearly[yearly["strategy"] != "multi_market"]) is None
    # Without reserve prices the same config simply runs day-ahead only.
    plain = run_backtest(multi_cfg, 2023, prices, last_day=date(2023, 1, 3))
    assert "multi_market" not in set(plain.daily["strategy"])

    monkeypatch.setattr(results, "year_prices", lambda c, y: prices)
    monkeypatch.setattr(results, "ensure_reserve_prices", lambda a, b: reserve)
    monkeypatch.setattr(results, "ensure_imbalance_prices",
                        lambda a, b: pd.Series(60.0, index=grid, name="imbalance_eur_mwh"))
    monkeypatch.setattr(results, "run_backtest",
                        lambda c, y, p, g, reserve_prices=None: run_backtest(
                            c, y, p, g, last_day=date(2023, 1, 12),
                            reserve_prices=reserve_prices))
    summary, out = results.run_and_save(multi_cfg, 2023, tmp_path)
    assert summary["markets"]["eur_per_mw_year"] == pytest.approx(
        summary["markets"]["total_eur"] / 0.5)
    assert summary["markets"]["imbalance"]["mean_abs_gap_eur_mwh"] > 0
    assert summary["config"]["reserves"]["enabled"] is True
    assert results.year_reserves(cfg, 2023) is None
    monkeypatch.setattr(results, "ensure_imbalance_prices",
                        lambda a, b: (_ for _ in ()).throw(RuntimeError("no token")))
    assert results.imbalance_stats(2023, prices) is None


async def test_controller_prices_schedule_deviations_at_the_imbalance_price(cfg):
    inputs = make_inputs(96, 15, 2)
    plan = solve_day(inputs, cfg.battery, cfg.site, 0.5)
    i = int(plan.frame[plan.frame["power_kw"] > 1].index[0])
    row = plan.frame.iloc[i]
    device = FakeDevice()
    controller = Controller(cfg, device)
    controller.load_schedule(plan.frame, inputs.index[0])
    t = row["ts_utc"]
    await controller.step(t, wall=0.0)
    device.power = row["power_kw"] + 100.0  # the battery draws 100 kW more than planned
    await controller.step(t + pd.Timedelta(seconds=1), wall=1.0)
    await controller.step(t + pd.Timedelta(minutes=12), wall=2.0)
    extra_kwh = 100.0 * (12 * 60 - 1) / 3600
    assert controller.imbalance_kwh == pytest.approx(extra_kwh, rel=0.02)
    assert controller.imbalance_eur is None and controller.state(t)["imbalance_eur"] is None

    controller.reset_day()
    controller.imbalance_prices = pd.Series({t: row["price_eur_mwh"] + 80.0})
    await controller.step(t, wall=3.0)
    await controller.step(t + pd.Timedelta(minutes=12), wall=4.0)
    # 20 kWh bought at 80 EUR/MWh above the day-ahead price costs 1.60 EUR.
    assert controller.imbalance_eur == pytest.approx(-100.0 * 0.2 / 1000 * 80.0, rel=0.02)
    assert controller.state(t)["imbalance_kwh"] > 0

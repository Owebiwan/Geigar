from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from bdo.backtest.runner import run_backtest
from bdo.data import fundamentals as fundamentals_mod
from bdo.forecast import evaluate
from bdo.forecast.apply import day_guess
from bdo.forecast.features import (
    FUNDAMENTAL_FEATURES,
    PRICE_FEATURES,
    build_features,
    german_holidays,
    hourly_prices,
)
from bdo.forecast.intrahour import quarter_hour_forecast, quarter_table, walk_forward_deviation
from bdo.forecast.model import VARIANTS, ModelParams, error_metrics, walk_forward
from bdo.optimizer.schedule import DayInputs
from bdo.timeutil import day_bounds_utc

from .conftest import make_prices

FAST = ModelParams(max_iter=30, train_years=1.0)


def _world(start: str = "2022-12-01T00:00Z", days: int = 420, seed: int = 5):
    """Synthetic market where the price is driven by residual load plus noise."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=days * 24, freq="h")
    hour = idx.hour.to_numpy()
    load = 55000 + 9000 * np.sin((hour - 7) / 24 * 2 * np.pi) + rng.normal(0, 800, len(idx))
    wind = np.clip(12000 + 9000 * np.sin(np.arange(len(idx)) / 90) + rng.normal(0, 1500, len(idx)),
                   0, None)
    pv = 20000 * np.clip(np.sin((hour - 6) / 12 * np.pi), 0, None)
    price = 20 + (load - wind - pv) / 500 + rng.normal(0, 4, len(idx))
    prices = pd.DataFrame({"ts_utc": idx, "price_eur_mwh": price, "resolution_min": 60,
                           "source": "smard"})
    fund = pd.DataFrame({"load_fc": load, "wind_on_fc": wind * 0.8, "wind_off_fc": wind * 0.2,
                         "pv_fc": pv}, index=idx)
    return prices, fund


def test_holidays_include_fixed_and_easter_dates():
    h = german_holidays(2025)
    assert {date(2025, 1, 1), date(2025, 4, 18), date(2025, 4, 21), date(2025, 5, 29),
            date(2025, 6, 9), date(2025, 10, 3), date(2025, 12, 26)} <= h
    assert date(2025, 4, 20) not in h  # Easter Sunday itself is not a listed holiday


def test_hourly_prices_average_quarters_and_keep_incomplete_hours_missing():
    q = make_prices("2025-10-20T00:00Z", 8, res=15)
    hourly = hourly_prices(q)
    assert hourly.iloc[0] == pytest.approx(q["price_eur_mwh"].iloc[:4].mean())
    assert np.isnan(hourly_prices(q.drop(index=[5])).iloc[1])


def test_features_never_use_the_delivery_day_or_later_prices():
    prices, fund = _world(days=40)
    base = build_features(prices, fund)
    changed = prices.copy()
    cut = pd.Timestamp("2022-12-30T23:00Z")  # Berlin midnight: start of delivery day 31 Dec
    changed.loc[changed["ts_utc"] >= cut, "price_eur_mwh"] += 500.0
    after = build_features(changed, fund)
    day = (base.index >= cut) & (base.index < cut + pd.Timedelta(hours=24))
    cols = [*PRICE_FEATURES, *FUNDAMENTAL_FEATURES]
    pd.testing.assert_frame_equal(base.loc[day, cols], after.loc[day, cols])
    assert not base.loc[day, "price"].equals(after.loc[day, "price"])


def test_walk_forward_only_trains_on_the_past_and_beats_persistence():
    prices, fund = _world()
    table = build_features(prices, fund)
    actual = table.loc["2023", "price"]
    with_fund = walk_forward(table, VARIANTS["forecast_fundamentals"], 2023, FAST)
    assert with_fund.index.min() >= pd.Timestamp("2023-01-01T00:00Z")
    assert len(with_fund) == len(actual)
    model, naive = error_metrics(actual, with_fund), error_metrics(actual, table.loc["2023",
                                                                                      "lag24"])
    assert model["mae"] < naive["mae"] and model["shape_corr"] > naive["shape_corr"]
    # Scrambling every price from June on must not change forecasts made before June.
    scrambled = prices.copy()
    late = scrambled["ts_utc"] >= pd.Timestamp("2023-06-01T00:00Z")
    scrambled.loc[late, "price_eur_mwh"] = 999.0
    again = walk_forward(build_features(scrambled, fund), VARIANTS["forecast_fundamentals"], 2023,
                         FAST)
    early = with_fund.index < pd.Timestamp("2023-05-31T00:00Z")
    np.testing.assert_allclose(with_fund[early], again[early])


def test_difference_target_and_empty_history():
    prices, fund = _world(days=120, start="2022-12-01T00:00Z")
    table = build_features(prices, fund)
    diff = walk_forward(table, VARIANTS["forecast_prices"], 2023,
                        ModelParams(max_iter=20, train_years=1.0, difference_target=True))
    assert len(diff) > 0 and diff.notna().all()
    assert walk_forward(table, VARIANTS["forecast_prices"], 2030, FAST).empty


def test_day_guess_adds_yesterdays_quarter_hour_pattern():
    idx = pd.date_range("2025-10-20T22:00Z", periods=8, freq="15min")
    prev_idx = idx - pd.Timedelta(days=1)
    saw = np.array([10.0, 0.0, 0.0, -10.0] * 2)
    previous = DayInputs(prev_idx, 0.25, 50 + saw, np.zeros(8), np.zeros(8))
    today = DayInputs(idx, 0.25, np.zeros(8), np.zeros(8), np.zeros(8))
    hourly = pd.Series([100.0, 200.0], index=idx.floor("h").unique())
    np.testing.assert_allclose(day_guess(today, hourly, previous),
                               np.repeat([100.0, 200.0], 4) + saw)
    np.testing.assert_allclose(day_guess(today, hourly, None), np.repeat([100.0, 200.0], 4))
    assert day_guess(today, hourly.iloc[:1], previous) is None  # an hour without a forecast


def test_perfect_forecast_earns_the_optimizer_revenue_and_missing_forecast_idles(cfg):
    start, _ = day_bounds_utc(date(2022, 12, 31))
    _, end = day_bounds_utc(date(2023, 1, 10))
    prices = make_prices(start.isoformat(), int((end - start) / pd.Timedelta(hours=1)), seed=9)
    perfect = prices.set_index("ts_utc")["price_eur_mwh"]
    result = run_backtest(cfg, 2023, prices, forecasts={"perfect": perfect, "blind": perfect[:0]})
    assert result.violations == []
    total = result.daily.groupby("strategy")["revenue_eur"].sum()
    assert total["perfect"] == pytest.approx(total["optimizer"], rel=1e-6)
    assert total["blind"] == 0.0


def _patch_sources(monkeypatch, prices, fund, calls=None):
    monkeypatch.setattr(evaluate, "ensure_prices",
                        lambda c, a, b, source=None, now=None: prices)
    monkeypatch.setattr(evaluate, "ensure_fundamentals",
                        lambda a, b, now=None, res="hour": fund)

    def fast(table, feats, year, params=None):
        if calls is not None:
            calls.append(year)
        return walk_forward(table, feats, year, ModelParams(max_iter=20, train_years=1.0))

    monkeypatch.setattr(evaluate, "walk_forward", fast)


def test_evaluate_scores_models_in_eur_and_caches_predictions(cfg, tmp_path, monkeypatch):
    prices, fund = _world(start="2021-12-01T00:00Z", days=62)
    calls: list[int] = []
    _patch_sources(monkeypatch, prices, fund, calls)
    summary, out = evaluate.run_and_save(cfg, 2022, tmp_path)
    rows = {r["strategy"]: r for r in summary["strategies"]}
    assert set(rows) == {"optimizer", "forecast_prices", "forecast_fundamentals", "persistence",
                         "naive"}
    assert rows["optimizer"]["share_of_optimizer_pct"] == 100.0
    assert rows["forecast_fundamentals"]["mae"] < rows["persistence"]["mae"]
    assert rows["forecast_fundamentals"]["total_eur"] <= rows["optimizer"]["total_eur"] + 1e-6
    assert summary["violations"] == [] and "2022Q1" in summary["quarterly_share_pct"]
    assert (out / "monthly.csv").exists() and (out / "summary.json").exists()
    loaded = evaluate.load_run(cfg, 2022, tmp_path)
    assert loaded is not None
    assert {m["strategy"] for m in loaded["monthly"]} == set(VARIANTS)
    assert evaluate.load_run(cfg, 1999, tmp_path) is None

    # Forecasts do not depend on the battery: a second scenario reuses the cached ones.
    from bdo.backtest.sweep import scenario
    assert len(calls) == 2
    other, _ = evaluate.run_and_save(scenario(cfg, 4, 0.9), 2022, tmp_path)
    assert len(calls) == 2 and other["config_hash"] != summary["config_hash"]


def test_running_year_is_evaluated_up_to_yesterday(cfg, tmp_path, monkeypatch):
    prices, fund = _world(start="2021-12-01T00:00Z", days=62)
    _patch_sources(monkeypatch, prices, fund)
    now = pd.Timestamp("2022-01-20T09:00Z")
    summary, _ = evaluate.run_and_save(cfg, 2022, tmp_path, now=now)
    assert summary["last_day"] == "2022-01-19" and summary["days"] == 19


def _quarter_world(days: int = 40, seed: int = 3):
    """15-minute market whose intra-hour pattern follows the residual-load ramp."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-10-01T22:00Z", periods=days * 96, freq="15min")
    t = np.arange(len(idx))
    load = 14000 + 2500 * np.sin(t / 96 * 2 * np.pi) + rng.normal(0, 60, len(idx))
    pv = 5000 * np.clip(np.sin((t % 96 - 24) / 48 * np.pi), 0, None)
    wind = 3000 + 1500 * np.sin(t / 700)
    fund = pd.DataFrame({"load_fc": load, "wind_on_fc": wind * 0.8, "wind_off_fc": wind * 0.2,
                         "pv_fc": pv}, index=idx)
    residual = (load - wind - pv) * 4
    hours = idx.floor("h")
    hour_mean = pd.Series(residual, index=idx).groupby(hours).transform("mean").to_numpy()
    hourly_price = 80 + (hour_mean - hour_mean.mean()) / 400
    price = hourly_price + (residual - hour_mean) / 40 + rng.normal(0, 1.0, len(idx))
    prices = pd.DataFrame({"ts_utc": idx, "price_eur_mwh": price, "resolution_min": 15,
                           "source": "smard"})
    hourly_fc = pd.Series(hourly_price, index=idx).groupby(hours).mean()
    return prices, fund, hourly_fc


def test_intrahour_model_beats_the_previous_days_pattern_without_leakage():
    prices, fund, hourly_fc = _quarter_world()
    table = quarter_table(prices, fund, hourly_fc)
    first = prices["ts_utc"].iloc[0]
    pred = walk_forward_deviation(table, first)
    assert len(pred) == len(table) and pred.notna().all()
    later = table.index >= first + pd.Timedelta(days=14)
    model_err = (pred[later] - table.loc[later, "dev"]).abs().mean()
    naive_err = (table.loc[later, "dev_week"] - table.loc[later, "dev"]).abs().mean()
    assert model_err < naive_err
    # The first week has no trained model and uses the fallback pattern.
    week1 = table.index < first + pd.Timedelta(days=6)
    fallback = table["dev_week"].fillna(table["dev_d1"]).fillna(0.0)
    np.testing.assert_allclose(pred[week1], fallback[week1])
    # Changing prices from day 30 on must not change forecasts for earlier days.
    changed = prices.copy()
    cut = first + pd.Timedelta(days=30)
    changed.loc[changed["ts_utc"] >= cut, "price_eur_mwh"] *= 3
    again = walk_forward_deviation(quarter_table(changed, fund, hourly_fc), first)
    early = pred.index < cut - pd.Timedelta(days=8)
    np.testing.assert_allclose(pred[early], again[early])


def test_quarter_hour_forecast_replaces_hours_of_the_15_minute_era():
    hours = pd.date_range("2025-09-30T20:00Z", periods=4, freq="h")
    hourly = pd.Series([10.0, 20.0, 30.0, 40.0], index=hours)
    quarters = pd.date_range(hours[2], periods=8, freq="15min")
    deviation = pd.Series([1.0, 0.0, 0.0, -1.0] * 2, index=quarters)
    out = quarter_hour_forecast(hourly, deviation)
    assert list(out.index[:2]) == list(hours[:2]) and len(out) == 10
    assert list(out.iloc[2:6]) == [31.0, 30.0, 30.0, 29.0]
    assert quarter_hour_forecast(hourly, deviation[:0]).equals(hourly)
    assert quarter_table(make_prices("2025-01-01T00:00Z", 24), pd.DataFrame(), hourly).empty
    assert walk_forward_deviation(pd.DataFrame(), hours[0]).empty
    # A forecast with the day's own resolution is used directly by the backtest.
    today = DayInputs(quarters, 0.25, np.zeros(8), np.zeros(8), np.zeros(8))
    np.testing.assert_allclose(day_guess(today, out, None), out.iloc[2:].to_numpy())


def test_live_forecast_for_an_uncleared_day(cfg, tmp_path, monkeypatch):
    from bdo.forecast import live

    prices, fund = _world(start="2022-12-01T00:00Z", days=63)
    day = date(2023, 1, 30)  # prices are only "published" up to the day before
    start, end = day_bounds_utc(day)
    known = prices[prices["ts_utc"] < start]
    monkeypatch.setattr(live, "ensure_prices", lambda c, a, b, source=None, now=None: known)
    calls = []

    def fundamentals(a, b, now=None, res="hour"):
        calls.append(res)
        return fund

    monkeypatch.setattr(live, "ensure_fundamentals", fundamentals)
    fast = ModelParams(max_iter=20, train_years=1.0)
    now = pd.Timestamp("2023-01-29T08:00Z")
    out = live.forecast_day(cfg, day, now=now, root=tmp_path, params=fast)
    assert out is not None and out.variant == "forecast_fundamentals"
    assert len(out.prices) == 24 and out.prices["ts_utc"].iloc[0] == start
    actual = prices[(prices["ts_utc"] >= start) & (prices["ts_utc"] < end)]["price_eur_mwh"]
    assert (out.prices["price_eur_mwh"].to_numpy() - actual.to_numpy()).__abs__().mean() < 15
    # A second request is served from the cache without touching the fundamentals again.
    n = len(calls)
    again = live.forecast_day(cfg, day, now=now, root=tmp_path, params=fast)
    assert len(calls) == n and again.prices.equals(out.prices)
    # Without TSO forecasts for the day the prices-only model answers.
    monkeypatch.setattr(live, "ensure_fundamentals",
                        lambda a, b, now=None, res="hour": fund[fund.index < start])
    blind = live.forecast_day(cfg, day, now=now, root=tmp_path / "c", params=fast)
    assert blind is not None and blind.variant == "forecast_prices"
    # While that prices-only forecast is fresh, the slow look for TSO forecasts is skipped.
    monkeypatch.setattr(live, "ensure_fundamentals", lambda *a, **k: 1 / 0)
    fresh = pd.Timestamp.now(tz="UTC")
    quick = live.forecast_day(cfg, day, now=fresh, root=tmp_path / "c", params=fast)
    assert quick is not None and quick.variant == "forecast_prices"
    # Two days ahead cannot be forecast: its previous day has no prices yet.
    assert live.forecast_day(cfg, date(2023, 1, 31), now=now, root=tmp_path, params=fast) is None
    # A far-future day is refused before any data is requested.
    monkeypatch.setattr(live, "ensure_prices", lambda *a, **k: 1 / 0)
    assert live.forecast_day(cfg, date(2031, 1, 5), now=now, root=tmp_path) is None


def test_fundamentals_are_cached_per_finished_year(tmp_path, monkeypatch):
    calls = []

    def fake(flt, start, end, zone="DE-LU", client=None, workers=6, res="hour"):
        calls.append((flt, start.year))
        return pd.Series(1.0, index=pd.date_range(start, periods=48, freq="h"))

    monkeypatch.setattr(fundamentals_mod.smard, "fetch_hourly", fake)
    now = pd.Timestamp("2025-06-01T00:00Z")
    first = fundamentals_mod.ensure_fundamentals(2023, 2024, root=tmp_path, now=now)
    fundamentals_mod.ensure_fundamentals(2023, 2024, root=tmp_path, now=now)
    assert len(calls) == 8 and list(first.columns) == list(fundamentals_mod.SERIES)
    fundamentals_mod.ensure_fundamentals(2025, 2025, root=tmp_path, now=now)
    fundamentals_mod.ensure_fundamentals(2025, 2025, root=tmp_path, now=now)
    assert len(calls) == 16  # the running year is refreshed every time

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from bdo.backtest import metrics, report, results, sweep
from bdo.backtest.runner import run_backtest
from bdo.timeutil import day_bounds_utc

from .conftest import make_prices


@pytest.fixture(scope="module")
def year_of_prices() -> pd.DataFrame:
    start, _ = day_bounds_utc(date(2022, 12, 31))
    _, end = day_bounds_utc(date(2023, 12, 31))
    hours = int((end - start) / pd.Timedelta(hours=1))
    prices = make_prices(start.isoformat(), hours, seed=11, negative=True)
    gap_start, _ = day_bounds_utc(date(2023, 7, 4))
    return prices[prices["ts_utc"] != gap_start + pd.Timedelta(hours=5)].reset_index(drop=True)


@pytest.fixture(scope="module")
def run(year_of_prices):
    from bdo.config import load_config

    from .conftest import ROOT
    cfg = load_config(ROOT / "config" / "battery.yaml")
    return cfg, run_backtest(cfg, 2023, year_of_prices)


def test_backtest_covers_the_year_with_zero_violations(run):
    cfg, result = run
    assert result.violations == []
    assert result.skipped_days == [date(2023, 7, 4)]  # the gap day is reported and skipped
    assert result.daily["date"].nunique() == 364
    assert set(result.daily["strategy"]) == {"optimizer", "naive", "persistence", "idle"}
    assert result.config_hash == cfg.hash()
    assert max(result.solve_seconds) < 2.0


def test_soc_carries_across_midnight(run):
    _, result = run
    naive = result.daily[result.daily["strategy"] == "naive"].reset_index(drop=True)
    assert naive["soc_end"].nunique() > 1  # the naive rule does not return to a fixed SoC
    first = result.intervals.groupby("date")["soc_start"].first()
    last = result.intervals.groupby("date")["soc"].last()
    assert (first.iloc[1:].to_numpy() == pytest.approx(last.iloc[:-1].to_numpy(), abs=1e-6))


def test_metrics_headline_and_special_days(run):
    cfg, result = run
    monthly = metrics.monthly(result.daily, cfg)
    yearly = metrics.yearly(monthly, cfg)
    head = metrics.headline(yearly)
    assert len(monthly) == 12 * 4
    assert head["optimizer_eur"] > head["persistence_eur"] > 0
    assert head["optimizer_eur"] > head["naive_eur"]
    assert head["uplift_vs_naive_pct"] > 0
    assert head["eur_per_mwh_capacity"] == pytest.approx(
        head["optimizer_eur"] / cfg.battery.capacity_kwh * 1000)
    assert head["peak_savings_eur"] == 0  # no site load in the arbitrage config
    days = metrics.special_days(result.daily)
    assert [d["label"] for d in days][:3] == ["MOST NEGATIVE PRICE", "HIGHEST SPREAD",
                                             "WORST DAY FOR NAIVE RULE"]
    assert days[0]["value"] == result.daily["price_min"].min()


def test_backtest_is_deterministic(run, year_of_prices):
    cfg, result = run
    again = run_backtest(cfg, 2023, year_of_prices)
    pd.testing.assert_frame_equal(result.daily, again.daily)


def test_outputs_are_written_with_the_config_hash(run, year_of_prices, tmp_path, monkeypatch):
    cfg, _ = run
    monkeypatch.setattr(results, "year_prices", lambda c, y: year_of_prices)
    summary, out = results.run_and_save(cfg, 2023, tmp_path)
    assert out.name == f"2023-{cfg.hash()}"
    for name in ("daily.csv", "monthly.csv", "yearly.csv", "summary.json", "report.html",
                 "intervals.parquet"):
        assert (out / name).exists()
    first = (out / "daily.csv").read_bytes()
    results.run_and_save(cfg, 2023, tmp_path)
    assert (out / "daily.csv").read_bytes() == first
    saved = json.loads((out / "summary.json").read_text())
    assert saved["config_hash"] == cfg.hash() and saved["violations"] == []
    html = (out / "report.html").read_text()
    assert "<svg" in html and "Upper bound" in html and "2023-07-04" in html
    loaded = results.load_run(cfg, 2023, tmp_path)
    assert loaded is not None and len(loaded["monthly"]) == 48
    assert results.load_run(cfg, 1999, tmp_path) is None


def test_report_charts_render(run):
    cfg, result = run
    monthly = metrics.monthly(result.daily, cfg)
    assert report.monthly_bars(monthly).count("<rect") == 36
    assert report.cumulative_lines(result.daily).count("<polyline") == 3


def test_peak_shaving_metrics_for_a_site(year_of_prices):
    from bdo.config import load_config

    from .conftest import ROOT
    cfg = load_config(ROOT / "config" / "site.yaml")
    cfg.site.solar_kwp = 0
    start, _ = day_bounds_utc(date(2022, 12, 31))
    _, end = day_bounds_utc(date(2023, 1, 31))
    part = year_of_prices[(year_of_prices["ts_utc"] >= start) & (year_of_prices["ts_utc"] < end)]
    result = run_backtest(cfg, 2023, part)
    assert result.violations == []
    monthly = metrics.monthly(result.daily, cfg)
    jan = monthly[(monthly["month"] == "2023-01") & (monthly["strategy"] == "optimizer")].iloc[0]
    assert jan["peak_reduction_kw"] > 0 and jan["peak_savings_eur"] > 0
    assert jan["total_eur"] == pytest.approx(jan["revenue_eur"] + jan["peak_savings_eur"])


def test_sweep_scenarios(run, year_of_prices, tmp_path, monkeypatch):
    cfg, _ = run
    sc = sweep.scenario(cfg, 4, 0.81)
    assert sc.battery.capacity_kwh == 2000 and sc.battery.eta_charge == pytest.approx(0.9)
    assert sc.hash() != cfg.hash() and cfg.battery.capacity_kwh == 1000
    start, _ = day_bounds_utc(date(2022, 12, 31))
    _, end = day_bounds_utc(date(2023, 1, 14))
    part = year_of_prices[(year_of_prices["ts_utc"] >= start) & (year_of_prices["ts_utc"] < end)]
    monkeypatch.setattr(sweep, "year_prices", lambda c, y: part)
    table = sweep.run_sweep(cfg, 2023, [1, 4], [0.9], tmp_path)
    assert list(table["duration_h"]) == [1, 4] and (table["violations"] == 0).all()
    assert table["optimizer_eur"].iloc[1] > table["optimizer_eur"].iloc[0]
    assert (tmp_path / "runs" / f"sweep-2023-{cfg.hash()}.csv").exists()

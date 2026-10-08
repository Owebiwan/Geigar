from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from bdo.api import main
from bdo.backtest import results
from bdo.device.service import Runner, create_app
from bdo.timeutil import day_bounds_utc

from .conftest import ROOT, make_prices


@pytest.fixture
def client(monkeypatch, tmp_path):
    from bdo.config import load_config

    monkeypatch.setenv("BDO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ENTSOE_TOKEN", raising=False)
    monkeypatch.setattr(main, "cfg", load_config(ROOT / "config" / "battery.yaml"))
    monkeypatch.setattr(main, "CONTROLLER_URL", "http://127.0.0.1:1")

    def fake_prices(cfg, start, end, source=None, store=None, now=None):
        if start >= pd.Timestamp("2030-01-01T00:00Z"):
            return make_prices(start.isoformat(), 0)
        n = int((end - start) / pd.Timedelta(minutes=15))
        return make_prices(start.isoformat(), n, res=15, seed=4)

    monkeypatch.setattr(main, "ensure_prices", fake_prices)
    return TestClient(main.app)


def test_health_and_config(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    body = client.get("/api/config").json()
    assert body["battery"]["id"] == "BAT-01" and body["source"] == "smard"
    assert len(body["config_hash"]) == 12


def test_schedule_for_a_day(client):
    body = client.get("/api/schedule", params={"day": "2025-10-26"}).json()
    assert body["available"] and body["intervals"] == 100 and body["resolution_min"] == 15
    row = body["rows"][0]
    assert {"ts_utc", "local", "price_eur_mwh", "power_kw", "soc", "cash_flow_eur",
            "naive_power_kw"} <= set(row)
    assert row["local"] == "00:00"
    assert body["summary"]["revenue_eur"] >= body["summary"]["naive_eur"] - 1e-6
    assert body["summary"]["solve_ms"] < 2000


def test_schedule_reports_missing_prices_instead_of_guessing(client):
    body = client.get("/api/schedule", params={"day": "2030-01-05"}).json()
    assert body["available"] is False and body["quality"]["gaps"]
    assert client.get("/api/schedule", params={"day": "2025-10-26"}).json()["provisional"] is False


def test_schedule_offers_a_provisional_plan_on_forecast_prices(client, monkeypatch):
    from bdo.forecast.live import LiveForecast

    start, _ = day_bounds_utc(date(2030, 1, 5))
    guess = make_prices(start.isoformat(), 96, res=15, seed=8)
    monkeypatch.setattr(main.forecast_live, "forecast_day",
                        lambda cfg, day: LiveForecast(day, guess, "forecast_prices", start))
    body = client.get("/api/schedule", params={"day": "2030-01-05"}).json()
    assert body["available"] and body["provisional"] is True
    assert body["forecast_model"] == "forecast_prices" and body["intervals"] == 96
    assert body["rows"][0]["price_eur_mwh"] == pytest.approx(guess["price_eur_mwh"].iloc[0])

    def broken(cfg, day):
        raise RuntimeError("no history")

    monkeypatch.setattr(main.forecast_live, "forecast_day", broken)
    assert client.get("/api/schedule", params={"day": "2030-01-05"}).json()["available"] is False
    assert client.get("/api/schedule").status_code == 200  # default day: tomorrow


def test_backtest_endpoint(client, monkeypatch):
    assert client.get("/api/backtest", params={"year": 2023}).json()["available"] is False
    start, _ = day_bounds_utc(date(2022, 12, 31))
    _, end = day_bounds_utc(date(2023, 1, 20))
    hours = int((end - start) / pd.Timedelta(hours=1))
    monkeypatch.setattr(results, "year_prices",
                        lambda c, y: make_prices(start.isoformat(), hours, seed=3))
    body = client.post("/api/backtest/run", params={"year": 2023, "duration": 1}).json()
    assert body["available"] and body["config"]["battery"]["capacity_kwh"] == 500
    assert body["headline"]["optimizer_eur"] > 0 and body["monthly"]
    cached = client.get("/api/backtest", params={"year": 2023, "duration": 1}).json()
    assert cached["available"] and cached["forecast"] is None

    def fake_forecast(cfg, year, root=None, now=None):
        out = main.data_dir() / "forecast" / f"{year}-{cfg.hash()}"
        out.mkdir(parents=True)
        (out / "summary.json").write_text('{"strategies": [], "days": 20}')
        pd.DataFrame({"strategy": ["forecast_fundamentals", "naive"], "month": ["2023-01"] * 2,
                      "total_eur": [5.0, 1.0]}).to_csv(out / "monthly.csv", index=False)
        return {}, out

    monkeypatch.setattr(main.forecast_eval, "run_and_save", fake_forecast)
    done = client.post("/api/forecast/run", params={"year": 2023, "duration": 1}).json()
    assert done["forecast"]["days"] == 20 and len(done["forecast"]["monthly"]) == 1

    assert done["markets"] is None and done["markets_possible"] is False
    grid = pd.date_range(start, end, freq="15min", inclusive="left")
    reserve = pd.DataFrame({"fcr": 4.0, "afrr_up": 3.0, "afrr_dn": 2.0}, index=grid)
    monkeypatch.setattr(results, "ensure_reserve_prices", lambda a, b: reserve)
    monkeypatch.setattr(results, "ensure_imbalance_prices",
                        lambda a, b: pd.Series(60.0, index=grid, name="imbalance_eur_mwh"))
    multi = client.post("/api/markets/run", params={"year": 2023, "duration": 1}).json()
    assert multi["markets"]["total_eur"] > multi["markets"]["day_ahead_only_eur"]
    assert multi["markets"]["violations"] == 0 and multi["markets"]["monthly"]
    assert multi["headline"]["optimizer_eur"] == body["headline"]["optimizer_eur"]
    assert client.get("/api/backtest", params={"year": 2023}).json()["available"] is False


def test_system_reports_controller_down_and_feed_status(client):
    body = client.get("/api/system").json()
    assert body["controller"]["controller_ok"] is False
    assert body["feed"]["source"] == "smard" and body["feed"]["entsoe_token"] is False
    assert any(r["name"] == "WSET" for r in body["registers"])
    assert client.post("/api/mode", json={"mode": "STOP"}).status_code == 503
    assert client.post("/api/manual", json={"power_kw": 10}).status_code == 503
    assert client.post("/api/sim/fault", json={"active": True}).status_code == 503


def test_daily_fetch_job_records_its_outcome(client):
    status = client.post("/api/fetch").json()
    assert status["ok"] is True and "complete" in status["detail"]


def test_telemetry_socket_reports_controller_down(client):
    with client.websocket_connect("/api/ws/telemetry") as ws:
        assert ws.receive_json() == {"controller_ok": False, "link_ok": False}


def test_controller_service_api(cfg, monkeypatch):
    cfg.controller.modbus_port = 1  # no device: the service must still answer
    runner = Runner(cfg, speed=720.0, replay_day=date(2025, 5, 11))
    assert runner.poll_wall == 0.25

    async def idle_loop() -> None:
        return None

    monkeypatch.setattr(runner, "loop", idle_loop)
    with TestClient(create_app(runner)) as client:
        state = client.get("/state").json()
        assert state["mode"] == "AUTO" and state["replay_day"] == "2025-05-11"
        assert state["soc"] is None and state["link_ok"] is False
        assert client.post("/mode", json={"mode": "MANUAL"}).json()["mode"] == "MANUAL"
        assert client.post("/mode", json={"mode": "WARP"}).status_code == 422
        assert client.post("/manual", json={"power_kw": 9999}).json()["manual_kw"] == 9999
        assert client.get("/schedule").json() == [] and client.get("/events").json() == []
        assert client.post("/sim/fault", json={"active": True}).status_code == 503
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json()["battery_id"] == "BAT-01"


async def test_runner_plans_the_day_and_reoptimises_on_drift(cfg, monkeypatch):
    from bdo.device import service
    from bdo.device.controller import Telemetry

    start, end = day_bounds_utc(date(2025, 10, 20))
    monkeypatch.setattr(service, "ensure_prices",
                        lambda c, a, b: make_prices(start.isoformat(), 96, res=15, seed=2))
    runner = Runner(cfg, speed=720.0, replay_day=date(2025, 10, 20))
    runner._plan(date(2025, 10, 20), start)
    assert runner.plan is not None and len(runner.schedule_rows()) == 96
    assert "reoptimised_at" not in runner.plan.meta

    # A controller started at 14:05 plans only the rest of the day, from the current SoC.
    late = Runner(cfg, speed=1.0, replay_day=None)
    late.controller.telemetry = Telemetry(0.8, 0.0, 750.0, 25.0, 0, 0, 1)
    late._plan(date(2025, 10, 20), start + pd.Timedelta(hours=14, minutes=5))
    frame = late.plan.frame
    assert len(frame) == 96
    assert frame["power_kw"].iloc[:57].abs().sum() == 0  # past and running intervals idle
    assert frame["soc"].iloc[:57].eq(0.8).all()
    assert frame["soc_start"].iloc[57] == pytest.approx(0.8)
    assert frame["power_kw"].iloc[57:].abs().sum() > 0
    assert late.plan.soc_end == pytest.approx(cfg.battery.soc_end, abs=1e-6)
    after_close = Runner(cfg, speed=1.0, replay_day=None)
    after_close._plan(date(2025, 10, 20), start + pd.Timedelta(hours=23, minutes=59))
    assert after_close.plan.frame["power_kw"].abs().sum() == 0
    c = runner.controller
    c.link_ok, c.decision = True, service.Controller(cfg, runner.device).decision.__class__(
        0.0, "schedule", False)
    now = start + pd.Timedelta(hours=5)
    planned = float(runner.plan.frame["soc_start"].iloc[20])
    c.telemetry = Telemetry(planned, 0.0, 750.0, 25.0, 0, 0, 1)
    runner._check_drift(now)
    assert runner.reoptimisations == 0
    c.telemetry = Telemetry(min(0.9, planned + 0.2), 0.0, 750.0, 25.0, 0, 0, 1)
    runner._check_drift(now)
    assert runner.reoptimisations == 1 and "reoptimised_at" in runner.plan.meta
    runner._check_drift(now + pd.Timedelta(minutes=1))  # same interval: no second re-plan
    assert runner.reoptimisations == 1
    assert runner.state()["reoptimisations"] == 1

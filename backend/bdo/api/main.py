"""Public API for the dashboard: schedules, backtest results, system health, live relay."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta
from typing import Any

import httpx
import pandas as pd
import structlog
import websockets
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from bdo.backtest import results
from bdo.backtest.sweep import scenario, with_markets
from bdo.config import Config, data_dir, load_config
from bdo.data.prices import cross_check, ensure_prices, resolve_source
from bdo.data.quality import check_prices
from bdo.data.store import PriceStore
from bdo.device import registers as reg
from bdo.forecast import evaluate as forecast_eval
from bdo.forecast import live as forecast_live
from bdo.optimizer import baselines
from bdo.optimizer.rolling import build_inputs, plan_day
from bdo.timeutil import BERLIN, UTC, day_bounds_utc

log = structlog.get_logger()
CONTROLLER_URL = os.environ.get("BDO_CONTROLLER_URL", "http://127.0.0.1:8100")
cfg: Config = load_config()
fetch_status: dict[str, Any] = {"last_run": None, "ok": None, "detail": "not run yet"}


def fetch_next_day() -> None:
    """Daily job (DR-5): pull tomorrow's prices once the auction results are out."""
    tomorrow = (datetime.now(BERLIN) + timedelta(days=1)).date()
    start, end = day_bounds_utc(tomorrow)
    try:
        report = check_prices(ensure_prices(cfg, start, end), start, end)
        detail = "complete" if report.ok else f"{len(report.gaps)} gap(s): not published yet?"
        fetch_status.update(ok=report.ok, detail=f"{tomorrow} {detail}")
    except Exception as exc:  # noqa: BLE001 - reported on the SYSTEM screen
        fetch_status.update(ok=False, detail=f"{tomorrow} fetch failed: {exc}")
    fetch_status["last_run"] = pd.Timestamp.now(tz=UTC).isoformat()
    log.info("prices.daily_job", **fetch_status)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    scheduler = AsyncIOScheduler(timezone=BERLIN)
    # Auction results publish around 12:45-13:00 CET; retry hourly in case of delay.
    scheduler.add_job(fetch_next_day, "cron", hour="13-18", minute=15)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Battery Dispatch Optimizer", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ModeRequest(BaseModel):
    mode: str


class ManualRequest(BaseModel):
    power_kw: float


class FaultRequest(BaseModel):
    active: bool


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict[str, Any]:
    out = cfg.model_dump()
    out["config_hash"] = cfg.hash()
    out["source"] = resolve_source(cfg)
    return out


@app.get("/api/schedule")
def schedule(day: str | None = None) -> dict[str, Any]:
    """Optimised plan for one delivery day (default: tomorrow), with the naive rule alongside."""
    target = date.fromisoformat(day) if day else (datetime.now(BERLIN) + timedelta(days=1)).date()
    start, end = day_bounds_utc(target)
    try:
        prices = ensure_prices(cfg, start, end)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"price fetch failed: {exc}") from exc
    report = check_prices(prices, start, end)
    inputs = build_inputs(cfg, prices, target)
    provisional = None
    if inputs is None and not report.rows:
        # Nothing published yet: offer a plan on forecast prices, clearly marked as such.
        try:
            provisional = forecast_live.forecast_day(cfg, target)
        except Exception as exc:  # noqa: BLE001 - a failed forecast is not an API error
            log.warning("forecast.live_failed", day=str(target), error=str(exc))
        if provisional is not None:
            inputs = build_inputs(cfg, provisional.prices, target)
    if inputs is None:
        return {"date": target.isoformat(), "available": False, "quality": report.to_dict(),
                "reason": "No complete day-ahead prices for this day."}
    plan = plan_day(cfg, inputs, cfg.battery.soc_start)
    naive = baselines.naive(inputs, cfg.battery, cfg.site, cfg.battery.soc_start)
    idle = baselines.idle(inputs, cfg.battery, cfg.site, cfg.battery.soc_start)
    cols = ["ts_utc", "price_eur_mwh", "power_kw", "soc_start", "soc", "grid_kw", "load_kw",
            "solar_kw", "cash_flow_eur"]
    frame = plan.frame[cols].copy()
    frame["naive_power_kw"] = naive.frame["power_kw"]
    frame["local"] = frame["ts_utc"].dt.tz_convert(BERLIN).dt.strftime("%H:%M")
    frame["ts_utc"] = frame["ts_utc"].map(lambda t: t.isoformat())
    discharge = float((plan.frame["discharge_kw"] * plan.frame["dt_h"]).sum())
    return {
        "date": target.isoformat(),
        "available": True,
        "provisional": provisional is not None,
        "forecast_model": None if provisional is None else provisional.variant,
        "resolution_min": int(inputs.dt_h * 60),
        "intervals": len(frame),
        "rows": json.loads(frame.to_json(orient="records")),
        "summary": {
            "revenue_eur": plan.cash_flow_eur - idle.cash_flow_eur,
            "naive_eur": naive.cash_flow_eur - idle.cash_flow_eur,
            "cycles": discharge / cfg.battery.capacity_kwh,
            "price_min": float(inputs.price.min()),
            "price_max": float(inputs.price.max()),
            "solve_ms": plan.solve_seconds * 1000.0,
        },
        "quality": report.to_dict(),
    }


def _scenario(duration: float | None) -> Config:
    if duration is None:
        return cfg
    return scenario(cfg, duration, cfg.battery.round_trip_efficiency)


@app.get("/api/backtest")
def backtest(year: int = 2025, duration: float | None = None) -> dict[str, Any]:
    sc = _scenario(duration)
    run = results.load_run(sc, year)
    if run is None:
        return {"available": False, "year": year, "config_hash": sc.hash()}
    run["available"] = True
    run["forecast"] = forecast_eval.load_run(sc, year)
    multi = results.load_run(with_markets(sc), year)
    run["markets"] = None if multi is None else {
        **multi["markets"],
        "config_hash": multi["config_hash"],
        "violations": len(multi["violations"]),
        "monthly": [m for m in multi["monthly"] if m["strategy"] == "multi_market"],
    }
    run["markets_possible"] = bool(os.environ.get("ENTSOE_TOKEN"))
    return run


@app.post("/api/backtest/run")
async def backtest_run(year: int = 2025, duration: float | None = None) -> dict[str, Any]:
    sc = _scenario(duration)
    try:
        await asyncio.to_thread(results.run_and_save, sc, year)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"backtest failed: {exc}") from exc
    return backtest(year, duration)


@app.post("/api/markets/run")
async def markets_run(year: int = 2025, duration: float | None = None) -> dict[str, Any]:
    """Backtest the scenario with FCR and aFRR capacity sold alongside day-ahead trading."""
    try:
        await asyncio.to_thread(results.run_and_save, with_markets(_scenario(duration)), year)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"multi-market run failed: {exc}") from exc
    return backtest(year, duration)


@app.post("/api/forecast/run")
async def forecast_run(year: int = 2025, duration: float | None = None) -> dict[str, Any]:
    """Train the price forecaster and score it on this scenario. Slow the first time."""
    sc = _scenario(duration)
    try:
        await asyncio.to_thread(forecast_eval.run_and_save, sc, year)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"forecast run failed: {exc}") from exc
    return backtest(year, duration)


async def _controller(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.request(method, f"{CONTROLLER_URL}{path}", json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f"controller unreachable: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(resp.status_code, resp.text)
    return resp.json()


@app.get("/api/system")
async def system() -> dict[str, Any]:
    source = resolve_source(cfg)
    now = pd.Timestamp.now(tz=UTC)
    today = now.tz_convert(BERLIN).date()
    start, _ = day_bounds_utc(today - timedelta(days=6))
    _, end = day_bounds_utc(today)
    store = PriceStore(data_dir())
    feed_error = None
    try:
        # Today's prices were published yesterday, so the last week should be complete.
        cached = await asyncio.to_thread(ensure_prices, cfg, start, end)
    except Exception as exc:  # noqa: BLE001 - shown on the SYSTEM screen
        feed_error = str(exc)
        cached = store.load(source, start, end)
    try:
        controller: Any = await _controller("GET", "/state")
        events: Any = await _controller("GET", "/events")
        live_schedule: Any = await _controller("GET", "/schedule")
    except HTTPException as exc:
        controller, events, live_schedule = {"controller_ok": False, "error": exc.detail}, [], []
    return {
        "controller": controller,
        "events": events,
        "live_schedule": live_schedule,
        "feed": {
            "source": source,
            "entsoe_token": bool(os.environ.get("ENTSOE_TOKEN")),
            "last_fetch": store.last_fetch(source),
            "daily_job": fetch_status,
            "window_days": 7,
            "error": feed_error,
            "quality": check_prices(cached, start, end).to_dict(),
        },
        "registers": [
            {"address": r.address, "name": r.name, "kind": r.kind, "scale": r.scale,
             "unit": r.unit, "description": r.description}
            for r in [*reg.INPUT_REGISTERS, *reg.HOLDING_REGISTERS]
        ],
    }


@app.get("/api/system/crosscheck")
def crosscheck(days: int = 7) -> dict[str, Any]:
    today = datetime.now(BERLIN).date()
    start, _ = day_bounds_utc(today - timedelta(days=days))
    _, end = day_bounds_utc(today - timedelta(days=1))
    return cross_check(cfg, start, end)


@app.post("/api/mode")
async def mode(req: ModeRequest) -> Any:
    return await _controller("POST", "/mode", req.model_dump())


@app.post("/api/manual")
async def manual(req: ManualRequest) -> Any:
    return await _controller("POST", "/manual", req.model_dump())


@app.post("/api/sim/fault")
async def sim_fault(req: FaultRequest) -> Any:
    return await _controller("POST", "/sim/fault", req.model_dump())


@app.post("/api/fetch")
async def fetch_now() -> dict[str, Any]:
    await asyncio.to_thread(fetch_next_day)
    return fetch_status


@app.websocket("/api/ws/telemetry")
async def telemetry(socket: WebSocket) -> None:
    """Relay the controller's telemetry stream to the browser (SC-5)."""
    await socket.accept()
    url = CONTROLLER_URL.replace("http", "ws", 1) + "/ws"
    try:
        while True:
            try:
                async with websockets.connect(url, open_timeout=3) as upstream:
                    async for message in upstream:
                        await socket.send_text(str(message))
            except (OSError, websockets.WebSocketException, TimeoutError):
                await socket.send_json({"controller_ok": False, "link_ok": False})
                await asyncio.sleep(2)
    except (WebSocketDisconnect, RuntimeError):
        pass

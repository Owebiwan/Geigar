"""Controller process: control loop plus a small internal HTTP/WebSocket API.

The public API talks to this service and never to the device, so the safety layer
in the controller stays the single gate in front of the hardware.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pandas as pd
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from bdo.config import Config
from bdo.data.prices import ensure_prices
from bdo.data.reserves import ensure_imbalance_prices
from bdo.device import registers as reg
from bdo.device.clock import Clock
from bdo.device.controller import Controller, ModbusDevice
from bdo.optimizer.rolling import (
    build_inputs,
    interval_at,
    needs_reoptimisation,
    plan_rest_of_day,
    reoptimise,
)
from bdo.optimizer.schedule import DayInputs, Schedule
from bdo.timeutil import BERLIN, day_bounds_utc

log = structlog.get_logger()


class ModeRequest(BaseModel):
    mode: str


class ManualRequest(BaseModel):
    power_kw: float


class FaultRequest(BaseModel):
    active: bool


class Runner:
    def __init__(self, cfg: Config, speed: float, replay_day: date | None) -> None:
        self.cfg = cfg
        self.speed = speed
        self.replay_day = replay_day
        self.device = ModbusDevice(cfg.controller.modbus_host, cfg.controller.modbus_port,
                                   cfg.controller.modbus_unit)
        self.controller = Controller(cfg, self.device)
        self.clock = Clock(speed, day_bounds_utc(replay_day)[0] if replay_day else None)
        self.day: date | None = None
        self.inputs: DayInputs | None = None
        self.plan: Schedule | None = None
        self.reoptimisations = 0
        self._replanned_in: int | None = None
        self.poll_wall = cfg.controller.poll_seconds
        if speed > 1:
            self.poll_wall = max(0.25, cfg.controller.poll_seconds / speed)

    def _plan(self, day: date, now: pd.Timestamp) -> None:
        """Load the day's prices and optimise from the SoC the device reports."""
        self.day, self.inputs, self.plan = day, None, None
        start, end = day_bounds_utc(day)
        try:
            self.inputs = build_inputs(self.cfg, ensure_prices(self.cfg, start, end), day)
        except Exception as exc:  # noqa: BLE001 - no prices means no schedule, which is safe
            log.warning("controller.prices_unavailable", day=str(day), error=str(exc))
        if self.inputs is None:
            log.warning("controller.no_schedule", day=str(day))
            return
        t = self.controller.telemetry
        soc = self.cfg.battery.soc_start if t is None else t.soc
        # A controller started part-way through the day plans only the hours still ahead.
        self.plan = plan_rest_of_day(self.cfg, self.inputs, now, soc)
        self.controller.load_schedule(self.plan.frame, now)
        self.controller.reset_day()
        try:
            # Published with a delay, so a live day usually has none yet; a replay does.
            imbalance = ensure_imbalance_prices(start, end)
            self.controller.imbalance_prices = None if imbalance.empty else imbalance
        except Exception as exc:  # noqa: BLE001 - the view is optional (needs the token)
            self.controller.imbalance_prices = None
            log.info("controller.imbalance_prices_unavailable", reason=str(exc)[:120])
        self._replanned_in = None
        log.info("controller.schedule_loaded", day=str(day), intervals=len(self.plan.frame),
                 expected_eur=round(self.plan.cash_flow_eur, 2))

    async def loop(self) -> None:
        while True:
            try:
                await self._cycle()
            except Exception:  # noqa: BLE001 - the control loop must outlive any single error
                log.exception("controller.cycle_failed")
            await asyncio.sleep(self.poll_wall)

    async def _cycle(self) -> None:
        now = self.clock.now()
        if self.replay_day and now >= day_bounds_utc(self.replay_day)[1]:
            self.clock.restart(day_bounds_utc(self.replay_day)[0])  # replay the day again
            now, self.day = self.clock.now(), None
        today = now.tz_convert(BERLIN).date()
        if today != self.day:
            if self.controller.telemetry is None:
                # Plan from the device's real SoC, not the configured default.
                with contextlib.suppress(Exception):
                    self.controller.telemetry = await self.device.read()
            await asyncio.to_thread(self._plan, today, now)
        await self.controller.step(now)
        self._check_drift(now)

    def _check_drift(self, now: pd.Timestamp) -> None:
        c, t = self.controller, self.controller.telemetry
        if self.plan is None or self.inputs is None or t is None or c.mode != "AUTO":
            return
        if c.decision.tripped or not c.link_ok:
            return
        tol = self.cfg.controller.soc_drift_tolerance
        pos = interval_at(self.plan.frame, now)
        if pos == self._replanned_in:
            return  # the new plan only takes effect at the next boundary
        if needs_reoptimisation(self.plan.frame, now, t.soc, tol):
            self._replanned_in = pos
            self.plan = reoptimise(self.cfg, self.plan, self.inputs, now, t.soc)
            c.load_schedule(self.plan.frame, now)
            self.reoptimisations += 1
            log.info("controller.reoptimised", soc=round(t.soc, 4), at=now.isoformat())

    def state(self) -> dict[str, object]:
        now = self.clock.now()
        out = self.controller.state(now)
        out.update(
            speed=self.speed,
            replay_day=self.replay_day.isoformat() if self.replay_day else None,
            day=self.day.isoformat() if self.day else None,
            reoptimisations=self.reoptimisations,
            battery_id=self.cfg.battery.id,
            modbus=f"{self.cfg.controller.modbus_host}:{self.cfg.controller.modbus_port}",
        )
        return out

    def schedule_rows(self) -> list[dict[str, Any]]:
        if self.plan is None:
            return []
        cols = ["ts_utc", "dt_h", "price_eur_mwh", "power_kw", "soc", "cash_flow_eur"]
        frame = self.plan.frame[cols].copy()
        frame["ts_utc"] = frame["ts_utc"].map(lambda t: t.isoformat())
        return [{str(k): v for k, v in row.items()} for row in frame.to_dict("records")]


def create_app(runner: Runner) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(runner.loop())
        yield
        task.cancel()
        runner.device.close()

    app = FastAPI(title="bdo controller", lifespan=lifespan)

    @app.get("/state")
    def state() -> dict[str, object]:
        return runner.state()

    @app.get("/schedule")
    def schedule() -> list[dict[str, Any]]:
        return runner.schedule_rows()

    @app.get("/events")
    def events() -> list[dict[str, object]]:
        return [e.__dict__ for e in reversed(runner.controller.events)]

    @app.post("/mode")
    def mode(req: ModeRequest) -> dict[str, object]:
        try:
            runner.controller.set_mode(req.mode)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return runner.state()

    @app.post("/manual")
    def manual(req: ManualRequest) -> dict[str, object]:
        # Stored as requested; the safety layer clamps it on the next control cycle.
        runner.controller.manual_kw = req.power_kw
        return runner.state()

    @app.post("/sim/fault")
    async def fault(req: FaultRequest) -> dict[str, object]:
        """Demo hook: raise or clear a fault on the simulated device."""
        try:
            await runner.device.write_raw(reg.SIM_FAULT, 1 if req.active else 0)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(503, f"device unreachable: {exc}") from exc
        return {"active": req.active}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        try:
            while True:
                await socket.send_json(runner.state())
                await asyncio.sleep(max(0.25, min(1.0, runner.poll_wall)))
        except (WebSocketDisconnect, RuntimeError):
            pass

    return app


def run(cfg: Config, speed: float, day: date | None, host: str, port: int) -> None:
    if speed > 1 and day is None:
        raise SystemExit("--speed above 1 needs --day: accelerated mode replays a past day")
    uvicorn.run(create_app(Runner(cfg, speed, day)), host=host, port=port, log_level="warning")

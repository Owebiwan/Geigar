"""Dispatch controller (HW-4): the only component that talks to the device.

It writes the setpoint at every interval boundary and whenever the safe setpoint
changes, polls telemetry in between, and logs every dispatch decision (NF-4).
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import pandas as pd
import structlog

from bdo.config import Config
from bdo.device import registers as reg
from bdo.device.safety import SafetyDecision, SafetyInput, safe_setpoint
from bdo.optimizer.rolling import interval_at

log = structlog.get_logger()


@dataclass
class Telemetry:
    soc: float  # fraction
    power_kw: float
    voltage_v: float
    temperature_c: float
    fault_bits: int
    state: int
    heartbeat: int
    raw: dict[str, int] = field(default_factory=dict)


class Device(Protocol):
    async def read(self) -> Telemetry: ...
    async def write_setpoint(self, kw: float) -> None: ...


class ModbusDevice:
    """Modbus TCP client. Swapping the simulator for real hardware only changes host/port."""

    def __init__(self, host: str, port: int, unit: int) -> None:
        self.host, self.port, self.unit = host, port, unit
        self.client: Any = None

    async def _ensure(self) -> None:
        if self.client is None:
            # Created lazily: the pymodbus client needs a running event loop.
            from pymodbus.client import AsyncModbusTcpClient

            self.client = AsyncModbusTcpClient(self.host, port=self.port, timeout=2, retries=0)
        if not self.client.connected and not await self.client.connect():
            raise ConnectionError("modbus connect failed")

    async def read(self) -> Telemetry:
        await self._ensure()
        rr = await self.client.read_input_registers(0, count=len(reg.INPUT_REGISTERS),
                                                     slave=self.unit)
        if rr.isError():
            raise ConnectionError(f"modbus read failed: {rr}")
        raw = dict(zip(reg.INPUT_REGISTERS, rr.registers, strict=True))
        val = {r: r.decode(v) for r, v in raw.items()}
        return Telemetry(
            soc=val[reg.SOC] / 100.0, power_kw=val[reg.POWER], voltage_v=val[reg.VOLTAGE],
            temperature_c=val[reg.TEMPERATURE], fault_bits=int(val[reg.FAULT]),
            state=int(val[reg.STATE]), heartbeat=int(val[reg.HEARTBEAT]),
            raw={r.name: v for r, v in raw.items()},
        )

    async def write_setpoint(self, kw: float) -> None:
        await self._ensure()
        rr = await self.client.write_register(reg.SETPOINT.address, reg.SETPOINT.encode(kw),
                                              slave=self.unit)
        if rr.isError():
            raise ConnectionError(f"modbus write failed: {rr}")

    async def write_raw(self, register: reg.Register, value: int) -> None:
        await self._ensure()
        await self.client.write_register(register.address, value, slave=self.unit)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()


@dataclass
class Event:
    ts: str
    kind: str  # "dispatch" or "safety"
    reason: str
    setpoint_kw: float
    measured_kw: float | None
    soc: float | None


class Controller:
    def __init__(self, cfg: Config, device: Device) -> None:
        self.cfg = cfg
        self.device = device
        self.mode = "AUTO"
        self.manual_kw = 0.0
        self.schedule: pd.DataFrame | None = None
        self.schedule_created: pd.Timestamp | None = None
        self.telemetry: Telemetry | None = None
        self.link_ok = False
        self.last_ok_wall: float = time.monotonic()
        self.setpoint_kw = 0.0
        self.decision = SafetyDecision(0.0, "startup", True)
        self.revenue_eur = 0.0
        self.energy_charged_kwh = 0.0
        self.energy_discharged_kwh = 0.0
        self.events: list[Event] = []
        self._last_written: tuple[float, int | None] | None = None
        self._last_sim: pd.Timestamp | None = None

    def load_schedule(self, frame: pd.DataFrame, created: pd.Timestamp) -> None:
        self.schedule = frame.reset_index(drop=True)
        self.schedule_created = created
        self._last_written = None

    def set_mode(self, mode: str) -> None:
        if mode not in ("AUTO", "MANUAL", "STOP"):
            raise ValueError(f"unknown mode {mode!r}")
        self.mode = mode

    def reset_day(self) -> None:
        self.revenue_eur = self.energy_charged_kwh = self.energy_discharged_kwh = 0.0
        self._last_sim = None

    def _current(self, now: pd.Timestamp) -> tuple[int | None, pd.Series | None]:
        if self.schedule is None:
            return None, None
        pos = interval_at(self.schedule, now)
        return pos, (None if pos is None else self.schedule.iloc[pos])

    def _record(self, now: pd.Timestamp, kind: str, decision: SafetyDecision,
                interval: int | None) -> None:
        t = self.telemetry
        event = Event(now.isoformat(), kind, decision.reason, decision.setpoint_kw,
                      None if t is None else round(t.power_kw, 2),
                      None if t is None else round(t.soc, 4))
        self.events = [*self.events[-199:], event]
        log.info("dispatch.decision", interval=interval, mode=self.mode, **asdict(event))

    async def step(self, now: pd.Timestamp, wall: float | None = None) -> SafetyDecision:
        """One control cycle: poll, decide through the safety layer, write if needed."""
        wall = time.monotonic() if wall is None else wall
        try:
            self.telemetry = await self.device.read()
            self.link_ok = True
            self.last_ok_wall = wall
        except Exception as exc:  # noqa: BLE001 - any link failure is handled the same way
            if self.link_ok:
                log.warning("modbus.link_lost", error=str(exc))
            self.link_ok = False

        pos, row = self._current(now)
        t = self.telemetry
        self._account(now, row)
        if self.mode == "AUTO":
            requested = None if row is None else float(row["power_kw"])
        else:
            requested = self.manual_kw
        age = None
        if self.schedule_created is not None:
            age = (now - self.schedule_created).total_seconds()
        decision = safe_setpoint(
            SafetyInput(
                mode=self.mode, requested_kw=requested,
                fault_bits=0 if t is None else t.fault_bits,
                link_age_s=wall - self.last_ok_wall, schedule_age_s=age,
                soc=None if t is None else t.soc,
            ),
            self.cfg.battery, self.cfg.controller,
        )
        if decision.tripped and decision.reason != self.decision.reason:
            self._record(now, "safety", decision, pos)
        self.decision = decision
        self.setpoint_kw = decision.setpoint_kw

        key = (round(decision.setpoint_kw, 1), pos)
        if self.link_ok and key != self._last_written:
            try:
                await self.device.write_setpoint(decision.setpoint_kw)
                self._last_written = key
                if not decision.tripped:
                    self._record(now, "dispatch", decision, pos)
            except Exception as exc:  # noqa: BLE001
                log.warning("modbus.write_failed", error=str(exc))
                self.link_ok = False
        elif not self.link_ok:
            # Forces a fresh write, 0 kW if the timeout has passed, once the link returns.
            self._last_written = None
        return decision

    def _account(self, now: pd.Timestamp, row: pd.Series | None) -> None:
        """Integrate measured power into energy and revenue at the interval's price."""
        if self._last_sim is not None and self.telemetry is not None and self.link_ok:
            dt_h = max(0.0, (now - self._last_sim).total_seconds() / 3600.0)
            p = self.telemetry.power_kw
            self.energy_charged_kwh += max(p, 0.0) * dt_h
            self.energy_discharged_kwh += max(-p, 0.0) * dt_h
            if row is not None:
                self.revenue_eur += -p * dt_h * float(row["price_eur_mwh"]) / 1000.0
                self.revenue_eur -= self.cfg.battery.degradation_eur_per_kwh * abs(p) * dt_h
        self._last_sim = now

    def state(self, now: pd.Timestamp) -> dict[str, object]:
        pos, row = self._current(now)
        t = self.telemetry
        return {
            "ts": now.isoformat(),
            "mode": self.mode,
            "link_ok": self.link_ok,
            "link_age_s": round(time.monotonic() - self.last_ok_wall, 1),
            "setpoint_kw": self.setpoint_kw,
            "reason": self.decision.reason,
            "tripped": self.decision.tripped,
            "manual_kw": self.manual_kw,
            "interval": pos,
            "price_eur_mwh": None if row is None else float(row["price_eur_mwh"]),
            "planned_soc": None if row is None else float(row["soc"]),
            "revenue_eur": round(self.revenue_eur, 3),
            "charged_kwh": round(self.energy_charged_kwh, 2),
            "discharged_kwh": round(self.energy_discharged_kwh, 2),
            "soc": None if t is None else t.soc,
            "power_kw": None if t is None else t.power_kw,
            "voltage_v": None if t is None else t.voltage_v,
            "temperature_c": None if t is None else t.temperature_c,
            "fault_bits": 0 if t is None else t.fault_bits,
            "faults": [] if t is None else reg.fault_names(t.fault_bits),
            "heartbeat": None if t is None else t.heartbeat,
            "registers": {} if t is None else t.raw,
        }

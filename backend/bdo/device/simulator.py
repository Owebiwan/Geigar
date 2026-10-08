"""Simulated battery inverter / BMS as a Modbus TCP server (HW-1, HW-3)."""

from __future__ import annotations

import asyncio
import time

import numpy as np
import structlog
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusServerContext, ModbusSlaveContext
from pymodbus.server import StartAsyncTcpServer

from bdo.config import Config
from bdo.device import registers as reg

log = structlog.get_logger()

AMBIENT_C = 25.0
OVER_TEMPERATURE_C = 55.0
WATCHDOG_SECONDS = 30.0
TICK_SECONDS = 0.1


class _WatchedBlock(ModbusSequentialDataBlock):
    """Input-register block that remembers when the master last polled it."""

    def __init__(self, address: int, values: list[int]) -> None:
        super().__init__(address, values)
        self.last_access = time.monotonic()

    def getValues(self, address: int, count: int = 1) -> list[int]:  # noqa: N802
        self.last_access = time.monotonic()
        return super().getValues(address, count)  # type: ignore[no-any-return]


class BatterySimulator:
    """Battery physics with measurement noise. `step` is pure apart from the RNG."""

    def __init__(self, cfg: Config) -> None:
        self.b = cfg.battery
        self.noise_power = cfg.simulator.noise_power_fraction
        self.noise_soc = cfg.simulator.noise_soc_fraction
        self.rng = np.random.default_rng(cfg.simulator.seed)
        self.energy_kwh = cfg.battery.soc_start * cfg.battery.capacity_kwh
        self.power_kw = 0.0
        self.temperature_c = AMBIENT_C
        self.fault_bits = 0
        self.heartbeat = 0

    @property
    def soc(self) -> float:
        return self.energy_kwh / self.b.capacity_kwh

    def step(self, setpoint_kw: float, dt_seconds: float) -> None:
        b = self.b
        dt_h = dt_seconds / 3600.0
        e_min, e_max = b.soc_min * b.capacity_kwh, b.soc_max * b.capacity_kwh
        power = 0.0
        if not self.fault_bits and dt_h > 0:
            # The inverter tracks its setpoint with a small error (HW-3).
            power = setpoint_kw * (1.0 + self.rng.normal(0.0, self.noise_power))
            power = max(-b.max_discharge_kw, min(b.max_charge_kw, power))
            if power > 0:
                power = min(power, max(0.0, e_max - self.energy_kwh) / (b.eta_charge * dt_h))
            else:
                power = -min(-power, max(0.0, self.energy_kwh - e_min) * b.eta_discharge / dt_h)
        delta = b.eta_charge * power if power > 0 else power / b.eta_discharge
        self.energy_kwh = min(e_max, max(e_min, self.energy_kwh + delta * dt_h))
        self.power_kw = power
        # First-order thermal model: heating scales with |P|, cooling towards ambient.
        load = abs(power) / max(b.max_charge_kw, b.max_discharge_kw)
        target = AMBIENT_C + 18.0 * load
        alpha = min(1.0, dt_seconds / 1800.0)
        self.temperature_c += (target - self.temperature_c) * alpha
        if self.temperature_c > OVER_TEMPERATURE_C:
            self.fault_bits |= reg.FAULT_OVER_TEMPERATURE
        self.heartbeat = (self.heartbeat + 1) & 0xFFFF

    def telemetry(self) -> dict[reg.Register, float]:
        soc = self.soc + self.rng.normal(0.0, self.noise_soc)
        state = 3 if self.fault_bits else (1 if self.power_kw > 0.05 else
                                           2 if self.power_kw < -0.05 else 0)
        return {
            reg.SOC: float(np.clip(soc, 0.0, 1.0)) * 100.0,
            reg.POWER: self.power_kw,
            reg.VOLTAGE: 680.0 + 140.0 * self.soc + self.rng.normal(0.0, 0.3),
            reg.TEMPERATURE: self.temperature_c + self.rng.normal(0.0, 0.05),
            reg.FAULT: self.fault_bits,
            reg.STATE: state,
            reg.MAX_CHARGE: self.b.max_charge_kw,
            reg.MAX_DISCHARGE: self.b.max_discharge_kw,
            reg.HEARTBEAT: self.heartbeat,
        }


def build_context() -> tuple[ModbusServerContext, ModbusSlaveContext, _WatchedBlock]:
    telemetry = _WatchedBlock(0, [0] * 64)
    slave = ModbusSlaveContext(
        di=ModbusSequentialDataBlock(0, [0] * 16),
        co=ModbusSequentialDataBlock(0, [0] * 16),
        hr=ModbusSequentialDataBlock(0, [0] * 200),
        ir=telemetry,
    )
    return ModbusServerContext(slaves=slave, single=True), slave, telemetry


async def physics_loop(sim: BatterySimulator, slave: ModbusSlaveContext, telemetry: _WatchedBlock,
                       speed: float, stop: asyncio.Event | None = None) -> None:
    last = time.monotonic()
    while stop is None or not stop.is_set():
        await asyncio.sleep(TICK_SECONDS)
        now = time.monotonic()
        setpoint = reg.SETPOINT.decode(slave.getValues(3, reg.SETPOINT.address, 1)[0])
        injected = slave.getValues(3, reg.SIM_FAULT.address, 1)[0]
        sim.fault_bits = (sim.fault_bits | reg.FAULT_DEVICE) if injected else (
            sim.fault_bits & ~reg.FAULT_DEVICE)
        if now - telemetry.last_access > WATCHDOG_SECONDS:
            sim.fault_bits |= reg.FAULT_WATCHDOG
        else:
            sim.fault_bits &= ~reg.FAULT_WATCHDOG
        if not injected and sim.temperature_c < OVER_TEMPERATURE_C - 10:
            sim.fault_bits &= ~reg.FAULT_OVER_TEMPERATURE
        sim.step(setpoint, (now - last) * speed)
        last = now
        for register, value in sim.telemetry().items():
            slave.setValues(4, register.address, [register.encode(value)])


async def serve(cfg: Config, host: str = "0.0.0.0", port: int | None = None,
                speed: float = 1.0) -> None:
    context, slave, telemetry = build_context()
    sim = BatterySimulator(cfg)
    port = port or cfg.controller.modbus_port
    log.info("simulator.start", host=host, port=port, speed=speed, battery=cfg.battery.id)
    task = asyncio.create_task(physics_loop(sim, slave, telemetry, speed))
    try:
        await StartAsyncTcpServer(context=context, address=(host, port))
    finally:
        task.cancel()

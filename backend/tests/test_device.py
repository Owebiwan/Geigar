from __future__ import annotations

import asyncio
import socket

import pandas as pd
import pytest
from pymodbus.server import ServerAsyncStop, StartAsyncTcpServer

from bdo.device import registers as reg
from bdo.device.clock import Clock
from bdo.device.controller import Controller, ModbusDevice, Telemetry
from bdo.device.safety import SafetyInput, safe_setpoint
from bdo.device.simulator import BatterySimulator, build_context, physics_loop
from bdo.optimizer.model import solve_day

from .conftest import make_inputs


def _inp(**kw):
    base = dict(mode="AUTO", requested_kw=200.0, fault_bits=0, link_age_s=0.0,
                schedule_age_s=10.0, soc=0.5)
    return SafetyInput(**{**base, **kw})


class TestSafetyLayer:
    def test_passes_a_valid_schedule_setpoint(self, cfg):
        d = safe_setpoint(_inp(), cfg.battery, cfg.controller)
        assert (d.setpoint_kw, d.reason, d.tripped) == (200.0, "schedule", False)

    @pytest.mark.parametrize("requested,expected", [(9999.0, 500.0), (-9999.0, -500.0)])
    def test_clamps_to_power_limits(self, cfg, requested, expected):
        d = safe_setpoint(_inp(requested_kw=requested), cfg.battery, cfg.controller)
        assert (d.setpoint_kw, d.reason) == (expected, "clamped")

    def test_fault_register_forces_zero(self, cfg):
        d = safe_setpoint(_inp(fault_bits=reg.FAULT_DEVICE), cfg.battery, cfg.controller)
        assert (d.setpoint_kw, d.reason, d.tripped) == (0.0, "fault", True)

    def test_lost_link_forces_zero_after_30_seconds(self, cfg):
        assert safe_setpoint(_inp(link_age_s=29.0), cfg.battery, cfg.controller).setpoint_kw == 200
        d = safe_setpoint(_inp(link_age_s=30.1), cfg.battery, cfg.controller)
        assert (d.setpoint_kw, d.reason, d.tripped) == (0.0, "comm_loss", True)

    def test_stale_schedule_forces_zero(self, cfg):
        stale = cfg.controller.schedule_stale_seconds + 1
        d = safe_setpoint(_inp(schedule_age_s=stale), cfg.battery, cfg.controller)
        assert (d.setpoint_kw, d.reason, d.tripped) == (0.0, "stale_schedule", True)

    @pytest.mark.parametrize("kw", [dict(schedule_age_s=None), dict(requested_kw=None)])
    def test_missing_schedule_forces_zero(self, cfg, kw):
        d = safe_setpoint(_inp(**kw), cfg.battery, cfg.controller)
        assert (d.setpoint_kw, d.reason, d.tripped) == (0.0, "no_schedule", True)

    def test_stop_mode_overrides_everything(self, cfg):
        d = safe_setpoint(_inp(mode="STOP"), cfg.battery, cfg.controller)
        assert (d.setpoint_kw, d.reason, d.tripped) == (0.0, "stop", True)

    def test_manual_override_goes_through_the_same_rules(self, cfg):
        manual = dict(mode="MANUAL", schedule_age_s=None)
        ok = safe_setpoint(_inp(requested_kw=-9999.0, **manual), cfg.battery, cfg.controller)
        assert (ok.setpoint_kw, ok.reason) == (-500.0, "clamped")
        fine = safe_setpoint(_inp(requested_kw=120.0, **manual), cfg.battery, cfg.controller)
        assert (fine.setpoint_kw, fine.reason) == (120.0, "manual")
        fault = safe_setpoint(_inp(fault_bits=1, **manual), cfg.battery, cfg.controller)
        assert fault.setpoint_kw == 0.0
        lost = safe_setpoint(_inp(link_age_s=99, **manual), cfg.battery, cfg.controller)
        assert lost.setpoint_kw == 0.0

    def test_soc_window_blocks_further_charge_or_discharge(self, cfg):
        full = safe_setpoint(_inp(soc=0.9), cfg.battery, cfg.controller)
        empty = safe_setpoint(_inp(requested_kw=-100.0, soc=0.1), cfg.battery, cfg.controller)
        assert (full.setpoint_kw, full.reason) == (0.0, "soc_max")
        assert (empty.setpoint_kw, empty.reason) == (0.0, "soc_min")


class FakeDevice:
    def __init__(self) -> None:
        self.fail = False
        self.fault_bits = 0
        self.soc = 0.5
        self.power = 0.0
        self.writes: list[float] = []

    async def read(self) -> Telemetry:
        if self.fail:
            raise ConnectionError("down")
        return Telemetry(self.soc, self.power, 750.0, 25.0, self.fault_bits, 0, 1)

    async def write_setpoint(self, kw: float) -> None:
        if self.fail:
            raise ConnectionError("down")
        self.writes.append(kw)
        self.power = kw


@pytest.fixture
def rig(cfg):
    inputs = make_inputs(96, 15, 2)
    plan = solve_day(inputs, cfg.battery, cfg.site, 0.5)
    device = FakeDevice()
    controller = Controller(cfg, device)
    controller.load_schedule(plan.frame, inputs.index[0])
    active = plan.frame[plan.frame["power_kw"].abs() > 1].index[0]
    return controller, device, plan.frame, int(active)


class TestController:
    async def test_writes_the_scheduled_setpoint_at_each_boundary(self, rig):
        controller, device, frame, i = rig
        t = frame["ts_utc"].iloc[i]
        await controller.step(t + pd.Timedelta(seconds=1), wall=0.0)
        await controller.step(t + pd.Timedelta(seconds=5), wall=4.0)  # same interval: no rewrite
        assert device.writes == [pytest.approx(frame["power_kw"].iloc[i])]
        await controller.step(t + pd.Timedelta(minutes=15, seconds=1), wall=8.0)
        assert len(device.writes) == 2
        assert controller.events[-1].kind == "dispatch"

    async def test_fault_register_drives_power_to_zero(self, rig):
        controller, device, frame, i = rig
        now = frame["ts_utc"].iloc[i] + pd.Timedelta(seconds=1)
        await controller.step(now, wall=0.0)
        device.fault_bits = reg.FAULT_DEVICE
        d = await controller.step(now + pd.Timedelta(seconds=2), wall=2.0)
        assert d.reason == "fault" and device.writes[-1] == 0.0 and device.power == 0.0
        assert controller.events[-1].kind == "safety"
        assert controller.state(now)["faults"] == ["DEVICE FAULT"]

    async def test_stale_schedule_drives_power_to_zero(self, rig, cfg):
        controller, device, frame, i = rig
        now = frame["ts_utc"].iloc[i] + pd.Timedelta(seconds=1)
        await controller.step(now, wall=0.0)
        assert device.power != 0.0
        controller.schedule_created = now - pd.Timedelta(
            seconds=cfg.controller.schedule_stale_seconds + 5)
        d = await controller.step(now + pd.Timedelta(seconds=2), wall=2.0)
        assert d.reason == "stale_schedule" and device.writes[-1] == 0.0

    async def test_running_past_the_end_of_the_schedule_drives_power_to_zero(self, rig):
        controller, device, frame, i = rig
        await controller.step(frame["ts_utc"].iloc[i] + pd.Timedelta(seconds=1), wall=0.0)
        d = await controller.step(frame["ts_utc"].iloc[-1] + pd.Timedelta(hours=1), wall=2.0)
        assert d.reason == "no_schedule" and device.writes[-1] == 0.0

    async def test_lost_link_drives_power_to_zero_after_timeout(self, rig):
        controller, device, frame, i = rig
        now = frame["ts_utc"].iloc[i] + pd.Timedelta(seconds=1)
        await controller.step(now, wall=0.0)
        commanded = controller.setpoint_kw
        device.fail = True
        d = await controller.step(now + pd.Timedelta(seconds=10), wall=10.0)
        assert not controller.link_ok and d.setpoint_kw == commanded  # inside the grace period
        d = await controller.step(now + pd.Timedelta(seconds=31), wall=31.0)
        assert (d.setpoint_kw, d.reason, d.tripped) == (0.0, "comm_loss", True)
        # When the link returns the controller writes a fresh setpoint straight away.
        device.fail = False
        writes = len(device.writes)
        d = await controller.step(now + pd.Timedelta(seconds=33), wall=33.0)
        assert controller.link_ok and len(device.writes) == writes + 1 and d.reason == "schedule"

    async def test_manual_and_stop_modes(self, rig):
        controller, device, frame, i = rig
        now = frame["ts_utc"].iloc[i] + pd.Timedelta(seconds=1)
        controller.set_mode("MANUAL")
        controller.manual_kw = 9999.0
        d = await controller.step(now, wall=0.0)
        assert (d.setpoint_kw, d.reason) == (500.0, "clamped") and device.writes[-1] == 500.0
        controller.set_mode("STOP")
        d = await controller.step(now + pd.Timedelta(seconds=2), wall=2.0)
        assert d.reason == "stop" and device.writes[-1] == 0.0
        with pytest.raises(ValueError):
            controller.set_mode("TURBO")

    async def test_revenue_is_integrated_from_measured_power(self, rig, cfg):
        controller, device, frame, i = rig
        t = frame["ts_utc"].iloc[i]
        await controller.step(t, wall=0.0)
        await controller.step(t + pd.Timedelta(minutes=15) - pd.Timedelta(seconds=1), wall=2.0)
        row = frame.iloc[i]
        expected = -row["power_kw"] * 0.25 * row["price_eur_mwh"] / 1000 - (
            cfg.battery.degradation_eur_per_kwh * abs(row["power_kw"]) * 0.25)
        assert controller.revenue_eur == pytest.approx(expected, rel=0.01)
        controller.reset_day()
        assert controller.revenue_eur == 0.0


class TestSimulator:
    def test_physics_respects_limits_and_efficiency(self, cfg):
        cfg.simulator.noise_power_fraction = 0.0
        sim = BatterySimulator(cfg)
        sim.step(9999.0, 900)
        assert sim.power_kw == 500.0
        assert sim.energy_kwh == pytest.approx(500 + 500 * 0.25 * 0.95)
        for _ in range(20):
            sim.step(500.0, 900)
        assert sim.soc == pytest.approx(cfg.battery.soc_max) and sim.power_kw == 0.0
        for _ in range(20):
            sim.step(-500.0, 900)
        assert sim.soc == pytest.approx(cfg.battery.soc_min) and sim.power_kw == 0.0

    def test_noise_makes_measurements_drift_from_plan(self, cfg):
        sim = BatterySimulator(cfg)
        sim.step(300.0, 60)
        assert sim.power_kw != 300.0 and abs(sim.power_kw - 300.0) < 30
        t = sim.telemetry()
        assert t[reg.STATE] == 1 and 0 <= t[reg.SOC] <= 100

    def test_fault_stops_power_and_overheating_trips(self, cfg):
        sim = BatterySimulator(cfg)
        sim.fault_bits = reg.FAULT_DEVICE
        sim.step(400.0, 60)
        assert sim.power_kw == 0.0 and sim.telemetry()[reg.STATE] == 3
        sim.fault_bits = 0
        sim.temperature_c = 70.0
        sim.step(0.0, 1)
        assert sim.fault_bits & reg.FAULT_OVER_TEMPERATURE


def test_register_encoding_round_trips_and_saturates():
    assert reg.POWER.decode(reg.POWER.encode(-123.4)) == pytest.approx(-123.4)
    assert reg.POWER.decode(reg.POWER.encode(1e9)) == pytest.approx(3276.7)
    assert reg.SOC.encode(-5) == 0
    assert reg.fault_names(0x5) == ["OVER-TEMPERATURE", "COMM WATCHDOG"]
    doc = reg.markdown()
    assert "| 0 | WSET |" in doc and "ChaState" in doc


def test_clock_runs_at_the_configured_speed():
    start = pd.Timestamp("2025-05-11T00:00Z")
    clock = Clock(speed=3600.0, start=start)
    import time
    time.sleep(0.05)
    elapsed = (clock.now() - start).total_seconds()
    assert 150 < elapsed < 2000
    assert abs((Clock().now() - pd.Timestamp.now(tz="UTC")).total_seconds()) < 1


async def test_full_loop_over_modbus_tcp(cfg):
    """Controller -> Modbus TCP -> simulator: setpoint, telemetry, fault and link loss."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    cfg.simulator.noise_power_fraction = 0.0
    context, slave, telemetry = build_context()
    sim = BatterySimulator(cfg)
    stop = asyncio.Event()
    physics = asyncio.create_task(physics_loop(sim, slave, telemetry, 60.0, stop))
    server = asyncio.create_task(StartAsyncTcpServer(context=context, address=("127.0.0.1", port)))
    await asyncio.sleep(0.3)

    device = ModbusDevice("127.0.0.1", port, 1)
    controller = Controller(cfg, device)
    controller.set_mode("MANUAL")
    controller.manual_kw = 250.0
    now = pd.Timestamp("2025-05-11T10:00Z")
    try:
        await controller.step(now)
        await asyncio.sleep(0.4)
        await controller.step(now)
        assert controller.link_ok
        assert controller.telemetry.power_kw == pytest.approx(250.0, abs=0.1)
        assert controller.telemetry.soc > 0.5
        assert set(controller.telemetry.raw) == {r.name for r in reg.INPUT_REGISTERS}

        await device.write_raw(reg.SIM_FAULT, 1)
        await asyncio.sleep(0.3)
        decision = await controller.step(now)
        assert decision.reason == "fault"
        await asyncio.sleep(0.3)
        await controller.step(now)
        assert controller.telemetry.power_kw == 0.0
        await device.write_raw(reg.SIM_FAULT, 0)
        await asyncio.sleep(0.3)
        assert (await controller.step(now)).reason == "manual"  # fault cleared

        await ServerAsyncStop()
        device.close()
        device.port = 1  # nothing listens here
        decision = await controller.step(now, wall=controller.last_ok_wall + 60)
        assert not controller.link_ok and decision.reason == "comm_loss"
        assert decision.setpoint_kw == 0.0
    finally:
        stop.set()
        device.close()
        server.cancel()
        await asyncio.gather(physics, return_exceptions=True)

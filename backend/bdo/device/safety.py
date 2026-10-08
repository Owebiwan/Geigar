"""Safety layer (HW-5). A pure function, so every rule is directly testable.

Every setpoint the controller sends, scheduled or manual, goes through here. No
other code path writes to the device.
"""

from __future__ import annotations

from dataclasses import dataclass

from bdo.config import BatteryConfig, ControllerConfig


@dataclass(frozen=True)
class SafetyInput:
    mode: str  # AUTO, MANUAL or STOP
    requested_kw: float | None  # None = no schedule entry for the current time
    fault_bits: int
    link_age_s: float  # seconds since the last successful telemetry read
    schedule_age_s: float | None  # None = no schedule loaded
    soc: float | None = None


@dataclass(frozen=True)
class SafetyDecision:
    setpoint_kw: float
    reason: str
    tripped: bool  # True when a safety rule forced 0 kW


def safe_setpoint(
    inp: SafetyInput, battery: BatteryConfig, ctrl: ControllerConfig
) -> SafetyDecision:
    if inp.mode == "STOP":
        return SafetyDecision(0.0, "stop", True)
    if inp.fault_bits:
        return SafetyDecision(0.0, "fault", True)
    if inp.link_age_s > ctrl.comm_timeout_seconds:
        return SafetyDecision(0.0, "comm_loss", True)
    if inp.mode == "AUTO":
        if inp.schedule_age_s is None or inp.requested_kw is None:
            return SafetyDecision(0.0, "no_schedule", True)
        if inp.schedule_age_s > ctrl.schedule_stale_seconds:
            return SafetyDecision(0.0, "stale_schedule", True)
    requested = inp.requested_kw or 0.0
    clamped = max(-battery.max_discharge_kw, min(battery.max_charge_kw, requested))
    if inp.soc is not None:
        if clamped > 0 and inp.soc >= battery.soc_max:
            return SafetyDecision(0.0, "soc_max", False)
        if clamped < 0 and inp.soc <= battery.soc_min:
            return SafetyDecision(0.0, "soc_min", False)
    if clamped != requested:
        return SafetyDecision(clamped, "clamped", False)
    return SafetyDecision(clamped, "schedule" if inp.mode == "AUTO" else "manual", False)

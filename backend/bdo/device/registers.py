"""Modbus register map shared by the simulator and the controller (HW-1, HW-2).

Point names follow the SunSpec storage models (124 / 802) where one exists. The
layout itself is compact: telemetry lives in input registers and the setpoint in a
holding register, as a plain Modbus inverter would expose them. SunSpec proper puts
every point in holding registers behind a "SunS" marker; that part is not copied.
docs/register-map.md is generated from this file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Register:
    address: int
    name: str
    sunspec: str
    kind: str  # "input" (read-only, FC4) or "holding" (read/write, FC3/FC6)
    dtype: str  # "uint16" or "int16"
    scale: float  # engineering value = raw * scale
    unit: str
    description: str

    def encode(self, value: float) -> int:
        raw = int(round(value / self.scale))
        if self.dtype == "int16":
            raw = max(-32768, min(32767, raw))
            return raw & 0xFFFF
        return max(0, min(65535, raw))

    def decode(self, raw: int) -> float:
        if self.dtype == "int16" and raw >= 0x8000:
            raw -= 0x10000
        return raw * self.scale


SOC = Register(0, "SOC", "ChaState", "input", "uint16", 0.01, "%", "State of charge")
POWER = Register(1, "W", "W", "input", "int16", 0.1, "kW",
                 "Actual AC power, positive = charging")
VOLTAGE = Register(2, "V", "V", "input", "uint16", 0.1, "V", "DC bus voltage")
TEMPERATURE = Register(3, "TMP", "TmpCab", "input", "int16", 0.1, "degC", "Cell temperature")
FAULT = Register(4, "FAULT", "Evt1", "input", "uint16", 1, "bitfield",
                 "bit0 over-temperature, bit1 device fault, bit2 comm watchdog")
STATE = Register(5, "STATE", "ChaSt", "input", "uint16", 1, "enum",
                 "0 idle, 1 charging, 2 discharging, 3 fault")
MAX_CHARGE = Register(6, "WCHAMAX", "WChaMax", "input", "uint16", 0.1, "kW", "Max charge power")
MAX_DISCHARGE = Register(7, "WDISCHAMAX", "WDisChaMax", "input", "uint16", 0.1, "kW",
                         "Max discharge power")
HEARTBEAT = Register(8, "HB", "Hb", "input", "uint16", 1, "count",
                     "Increments every simulation step")

SETPOINT = Register(0, "WSET", "WSet", "holding", "int16", 0.1, "kW",
                    "Power setpoint, positive = charging")
SIM_FAULT = Register(100, "SIM_FAULT", "-", "holding", "uint16", 1, "flag",
                     "Simulator only: non-zero raises a device fault, 0 clears it")

INPUT_REGISTERS = [SOC, POWER, VOLTAGE, TEMPERATURE, FAULT, STATE, MAX_CHARGE, MAX_DISCHARGE,
                   HEARTBEAT]
HOLDING_REGISTERS = [SETPOINT, SIM_FAULT]

FAULT_OVER_TEMPERATURE = 0x1
FAULT_DEVICE = 0x2
FAULT_WATCHDOG = 0x4
FAULT_NAMES = {FAULT_OVER_TEMPERATURE: "OVER-TEMPERATURE", FAULT_DEVICE: "DEVICE FAULT",
               FAULT_WATCHDOG: "COMM WATCHDOG"}


def fault_names(bits: int) -> list[str]:
    return [name for bit, name in FAULT_NAMES.items() if bits & bit]


def markdown() -> str:
    lines = ["# Modbus register map", "",
             "Unit id and port come from `controller.modbus_*` in the config. "
             "All registers are 16 bit; `value = raw * scale`.", ""]
    for title, regs in (("Input registers (FC 04, read-only)", INPUT_REGISTERS),
                        ("Holding registers (FC 03 / 06)", HOLDING_REGISTERS)):
        lines += [f"## {title}", "",
                  "| Addr | Name | SunSpec point | Type | Scale | Unit | Description |",
                  "| --- | --- | --- | --- | --- | --- | --- |"]
        lines += [f"| {r.address} | {r.name} | {r.sunspec} | {r.dtype} | {r.scale} | {r.unit} | "
                  f"{r.description} |" for r in regs]
        lines.append("")
    return "\n".join(lines)

# Modbus register map

Unit id and port come from `controller.modbus_*` in the config. All registers are 16 bit; `value = raw * scale`.

## Input registers (FC 04, read-only)

| Addr | Name | SunSpec point | Type | Scale | Unit | Description |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | SOC | ChaState | uint16 | 0.01 | % | State of charge |
| 1 | W | W | int16 | 0.1 | kW | Actual AC power, positive = charging |
| 2 | V | V | uint16 | 0.1 | V | DC bus voltage |
| 3 | TMP | TmpCab | int16 | 0.1 | degC | Cell temperature |
| 4 | FAULT | Evt1 | uint16 | 1 | bitfield | bit0 over-temperature, bit1 device fault, bit2 comm watchdog |
| 5 | STATE | ChaSt | uint16 | 1 | enum | 0 idle, 1 charging, 2 discharging, 3 fault |
| 6 | WCHAMAX | WChaMax | uint16 | 0.1 | kW | Max charge power |
| 7 | WDISCHAMAX | WDisChaMax | uint16 | 0.1 | kW | Max discharge power |
| 8 | HB | Hb | uint16 | 1 | count | Increments every simulation step |

## Holding registers (FC 03 / 06)

| Addr | Name | SunSpec point | Type | Scale | Unit | Description |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | WSET | WSet | int16 | 0.1 | kW | Power setpoint, positive = charging |
| 100 | SIM_FAULT | - | uint16 | 1 | flag | Simulator only: non-zero raises a device fault, 0 clears it |


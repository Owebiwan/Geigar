"""Typed configuration loaded from one YAML file, plus a stable hash for reproducibility."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class BatteryConfig(BaseModel):
    id: str = "BAT-01"
    capacity_kwh: float = Field(gt=0)
    max_charge_kw: float = Field(gt=0)
    max_discharge_kw: float = Field(gt=0)
    eta_charge: float = Field(gt=0, le=1)
    eta_discharge: float = Field(gt=0, le=1)
    soc_min: float = Field(ge=0, le=1)
    soc_max: float = Field(ge=0, le=1)
    soc_start: float = Field(ge=0, le=1)
    soc_end: float = Field(ge=0, le=1)
    degradation_eur_per_kwh: float = Field(ge=0)
    max_cycles_per_day: float | None = None
    grid_import_kw: float = Field(gt=0)
    grid_export_kw: float = Field(gt=0)

    @model_validator(mode="after")
    def _check_soc_window(self) -> BatteryConfig:
        if not self.soc_min < self.soc_max:
            raise ValueError("soc_min must be below soc_max")
        for name in ("soc_start", "soc_end"):
            if not self.soc_min <= getattr(self, name) <= self.soc_max:
                raise ValueError(f"{name} must lie inside the SoC window")
        return self

    @property
    def usable_kwh(self) -> float:
        return (self.soc_max - self.soc_min) * self.capacity_kwh

    @property
    def round_trip_efficiency(self) -> float:
        return self.eta_charge * self.eta_discharge


class SiteConfig(BaseModel):
    load_annual_kwh: float = Field(default=0, ge=0)
    load_profile: str = "G0"
    demand_charge_eur_per_kw_year: float = Field(default=0, ge=0)
    grid_fee_eur_per_mwh: float = Field(default=0, ge=0)
    solar_kwp: float = Field(default=0, ge=0)
    latitude: float = 52.52
    longitude: float = 13.40

    @property
    def has_load(self) -> bool:
        return self.load_annual_kwh > 0

    @property
    def has_solar(self) -> bool:
        return self.solar_kwp > 0


class MarketConfig(BaseModel):
    zone: str = "DE-LU"
    source: str = "auto"
    timezone: str = "Europe/Berlin"


class ReservesConfig(BaseModel):
    """Balancing capacity markets (v2). Off by default: v1 is day-ahead only."""

    enabled: bool = False
    fcr: bool = True
    afrr: bool = True
    # Energy the battery must be able to deliver or absorb per kW of reserve sold.
    # FCR: the 15-minute criterion for storage. aFRR: the whole 4-hour product, the most
    # cautious reading; shorter values raise revenue sharply (see the README table).
    fcr_energy_hours: float = Field(default=0.25, ge=0)
    afrr_energy_hours: float = Field(default=4.0, ge=0)
    # Wear from FCR activation: kWh of throughput per kW reserved per hour.
    fcr_throughput_kwh_per_kw_h: float = Field(default=0.05, ge=0)


class ControllerConfig(BaseModel):
    modbus_host: str = "127.0.0.1"
    modbus_port: int = 5020
    modbus_unit: int = 1
    poll_seconds: float = 2
    comm_timeout_seconds: float = 30
    schedule_stale_seconds: float = 129600
    soc_drift_tolerance: float = 0.05


class SimulatorConfig(BaseModel):
    noise_power_fraction: float = 0.01
    noise_soc_fraction: float = 0.002
    seed: int = 7


class Config(BaseModel):
    battery: BatteryConfig
    site: SiteConfig = SiteConfig()
    market: MarketConfig = MarketConfig()
    reserves: ReservesConfig = ReservesConfig()
    controller: ControllerConfig = ControllerConfig()
    simulator: SimulatorConfig = SimulatorConfig()

    def hash(self) -> str:
        """Hash of everything that affects optimizer and backtest results (BT-6)."""
        payload = self.model_dump(include={"battery", "site", "market"})
        if self.reserves.enabled:
            # Only part of the hash when used, so day-ahead-only runs keep their hash.
            payload["reserves"] = self.reserves.model_dump()
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


def default_config_path() -> Path:
    return Path(os.environ.get("BDO_CONFIG", "config/battery.yaml"))


def data_dir() -> Path:
    path = Path(os.environ.get("BDO_DATA_DIR", "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config(path: str | Path | None = None) -> Config:
    path = Path(path) if path else default_config_path()
    with path.open() as fh:
        cfg = Config.model_validate(yaml.safe_load(fh))
    # Environment overrides let Docker Compose point the controller at the simulator
    # container without a second config file (HW-7).
    if host := os.environ.get("BDO_MODBUS_HOST"):
        cfg.controller.modbus_host = host
    if port := os.environ.get("BDO_MODBUS_PORT"):
        cfg.controller.modbus_port = int(port)
    return cfg

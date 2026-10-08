"""Site solar profile from Open-Meteo global horizontal irradiance."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from bdo.config import SiteConfig, data_dir

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
PERFORMANCE_RATIO = 0.80  # inverter, temperature and soiling losses lumped together


def fetch_irradiance(
    site: SiteConfig, first: date, last: date, client: httpx.Client | None = None
) -> pd.Series:
    """Hourly shortwave radiation in W/m2, indexed by UTC interval start."""
    own = client is None
    client = client or httpx.Client(timeout=60)
    try:
        resp = client.get(
            ARCHIVE,
            params={
                "latitude": site.latitude,
                "longitude": site.longitude,
                "start_date": first.isoformat(),
                "end_date": last.isoformat(),
                "hourly": "shortwave_radiation",
                "timezone": "UTC",
            },
        )
        resp.raise_for_status()
    finally:
        if own:
            client.close()
    hourly = resp.json()["hourly"]
    idx = pd.to_datetime(hourly["time"], utc=True)
    return pd.Series(hourly["shortwave_radiation"], index=idx, dtype="float64", name="ghi_wm2")


def irradiance_cached(
    site: SiteConfig, first: date, last: date, root: Path | None = None
) -> pd.Series:
    root = (root or data_dir()) / "weather"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"ghi_{site.latitude:.2f}_{site.longitude:.2f}_{first}_{last}.parquet"
    if path.exists():
        return pd.read_parquet(path)["ghi_wm2"]
    series = fetch_irradiance(site, first, last)
    series.to_frame().to_parquet(path)
    return series


def solar_kw(ghi_wm2: pd.Series, kwp: float, index: pd.DatetimeIndex) -> pd.Series:
    """PV output in kW on `index`. Hourly means are held constant within the hour."""
    hourly_kw = (ghi_wm2.fillna(0.0) / 1000.0 * kwp * PERFORMANCE_RATIO).clip(lower=0.0)
    return hourly_kw.reindex(index.floor("h")).set_axis(index).fillna(0.0)

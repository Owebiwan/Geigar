"""Day-ahead prices from SMARD (Bundesnetzagentur). Public, no token needed."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd

from bdo.data.store import PRICE_COLUMNS, empty_prices

BASE = "https://www.smard.de/app/chart_data"
PRICE_FILTER = {"DE-LU": 4169}
# First delivery day with 15-minute day-ahead products: 1 Oct 2025 (Berlin time).
# SMARD's quarter-hour series before that date only repeats the hourly price four times.
QUARTER_HOUR_START = pd.Timestamp("2025-09-30T22:00:00Z")


def _fetch_series(
    client: httpx.Client, zone: str, res: str, start: pd.Timestamp, end: pd.Timestamp
) -> pd.DataFrame:
    flt = PRICE_FILTER[zone]
    index = client.get(f"{BASE}/{flt}/{zone}/index_{res}.json").raise_for_status().json()
    chunks = sorted(index["timestamps"])
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    rows: list[tuple[int, float]] = []
    for i, chunk in enumerate(chunks):
        nxt = chunks[i + 1] if i + 1 < len(chunks) else chunk + 8 * 86_400_000
        if chunk >= end_ms or nxt <= start_ms:
            continue
        url = f"{BASE}/{flt}/{zone}/{flt}_{zone}_{res}_{chunk}.json"
        series = client.get(url).raise_for_status().json()["series"]
        # A null value means "not published"; it is dropped here and shows up as a gap.
        rows += [(ts, val) for ts, val in series if val is not None and start_ms <= ts < end_ms]
    if not rows:
        return empty_prices()
    df = pd.DataFrame(rows, columns=["ms", "price_eur_mwh"])
    df["ts_utc"] = pd.to_datetime(df["ms"], unit="ms", utc=True)
    df["resolution_min"] = 60 if res == "hour" else 15
    df["source"] = "smard"
    return df[PRICE_COLUMNS]


def fetch_hourly(
    flt: int, start: pd.Timestamp, end: pd.Timestamp, zone: str = "DE-LU",
    client: httpx.Client | None = None, workers: int = 6, res: str = "hour",
) -> pd.Series:
    """Any SMARD series for [start, end), indexed by UTC interval start. Nulls stay missing.

    `res` is "hour" or "quarterhour".
    """
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        index = client.get(f"{BASE}/{flt}/{zone}/index_{res}.json").raise_for_status().json()
        chunks = sorted(index["timestamps"])
        start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        wanted = [
            c for i, c in enumerate(chunks)
            if c < end_ms and (chunks[i + 1] if i + 1 < len(chunks) else c + 8 * 86_400_000)
            > start_ms
        ]

        def load(chunk: int) -> list[list[float]]:
            url = f"{BASE}/{flt}/{zone}/{flt}_{zone}_{res}_{chunk}.json"
            return client.get(url).raise_for_status().json()["series"]  # type: ignore[no-any-return]

        with ThreadPoolExecutor(max_workers=workers) as pool:
            parts = list(pool.map(load, wanted))
    finally:
        if own:
            client.close()
    rows = {ts: val for part in parts for ts, val in part
            if val is not None and start_ms <= ts < end_ms}
    series = pd.Series(rows, dtype="float64").sort_index()
    series.index = pd.to_datetime(series.index, unit="ms", utc=True)
    return series


def fetch_prices(
    start: pd.Timestamp, end: pd.Timestamp, zone: str = "DE-LU", client: httpx.Client | None = None
) -> pd.DataFrame:
    """Prices for [start, end) at the market's native resolution (DR-2)."""
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        parts = []
        if start < QUARTER_HOUR_START:
            parts.append(_fetch_series(client, zone, "hour", start, min(end, QUARTER_HOUR_START)))
        if end > QUARTER_HOUR_START:
            parts.append(
                _fetch_series(client, zone, "quarterhour", max(start, QUARTER_HOUR_START), end)
            )
    finally:
        if own:
            client.close()
    parts = [p for p in parts if not p.empty]
    return pd.concat(parts, ignore_index=True) if parts else empty_prices()

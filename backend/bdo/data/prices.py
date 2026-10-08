"""Fetch-through-cache entry point for price data."""

from __future__ import annotations

import os

import httpx
import pandas as pd
import structlog

from bdo.config import Config, data_dir
from bdo.data import entsoe, smard
from bdo.data.store import PriceStore
from bdo.timeutil import UTC, month_starts

log = structlog.get_logger()


def resolve_source(cfg: Config) -> str:
    if cfg.market.source != "auto":
        return cfg.market.source
    return "entsoe" if os.environ.get("ENTSOE_TOKEN") else "smard"


def _fetch(source: str, start: pd.Timestamp, end: pd.Timestamp, zone: str) -> pd.DataFrame:
    if source == "entsoe":
        token = os.environ.get("ENTSOE_TOKEN")
        if not token:
            raise RuntimeError("ENTSOE_TOKEN is not set; use source 'smard' or add the token")
        return entsoe.fetch_prices(start, end, token, zone)
    if source == "smard":
        return smard.fetch_prices(start, end, zone)
    raise ValueError(f"unknown price source {source!r}")


def ensure_prices(
    cfg: Config,
    start: pd.Timestamp,
    end: pd.Timestamp,
    source: str | None = None,
    store: PriceStore | None = None,
    now: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Return prices for [start, end), fetching only months that are not cached yet."""
    source = source or resolve_source(cfg)
    store = store or PriceStore(data_dir())
    now = now or pd.Timestamp.now(tz=UTC)
    for month in month_starts(start, end):
        month_end = month + pd.offsets.MonthBegin(1)
        if store.is_cached(source, month, min(end, month_end)):
            continue
        log.info("prices.fetch", source=source, month=f"{month:%Y-%m}")
        store.write(source, month, _fetch(source, month, month_end, cfg.market.zone), now)
    return store.load(source, start, end)


def cross_check(cfg: Config, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, object]:
    """Compare ENTSO-E against SMARD on the intervals both have."""
    try:
        a = ensure_prices(cfg, start, end, source="entsoe")
        b = ensure_prices(cfg, start, end, source="smard")
    except (RuntimeError, httpx.HTTPError) as exc:
        return {"available": False, "reason": str(exc)}
    merged = a.merge(b, on=["ts_utc", "resolution_min"], suffixes=("_entsoe", "_smard"))
    diff = (merged["price_eur_mwh_entsoe"] - merged["price_eur_mwh_smard"]).abs()
    return {
        "available": True,
        "compared": int(len(merged)),
        "mismatches": int((diff > 0.01).sum()),
        "max_abs_diff": float(diff.max()) if len(diff) else 0.0,
    }

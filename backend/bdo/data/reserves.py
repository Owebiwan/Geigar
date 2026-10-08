"""Balancing data from ENTSO-E: FCR and aFRR capacity prices, and imbalance prices.

Capacity prices come as one value per product block. They are stored per 15-minute
settlement period in EUR per MW per 15 minutes, which is the unit ENTSO-E uses: the
2023 FCR series sums to about EUR 127k per MW-year, in line with published revenues.
"""

from __future__ import annotations

import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import httpx
import pandas as pd
import structlog

from bdo.config import data_dir
from bdo.data.entsoe import API, raise_without_token
from bdo.timeutil import UTC, month_starts

log = structlog.get_logger()

# Germany procures reserves jointly, so one control area carries the national price.
CONTROL_AREA = "10YDE-EON------1"
GERMANY = "10Y1001A1001A83F"
FCR, AFRR = "A52", "A51"
UP, DOWN = "A01", "A02"
RESERVE_COLUMNS = ["fcr", "afrr_up", "afrr_dn"]
ISP = pd.Timedelta(minutes=15)


def _documents(content: bytes) -> list[str]:
    if content[:2] != b"PK":
        return [content.decode()]
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return [archive.read(name).decode() for name in archive.namelist()]


def _root(xml_text: str) -> ET.Element:
    root = ET.fromstring(xml_text)
    for el in root.iter():
        el.tag = re.sub(r"^\{.*\}", "", el.tag)
    return root


def _points(period: ET.Element, field: str) -> pd.Series:
    """Expand a period's points to one value per 15 minutes (a point holds until the next)."""
    start = pd.Timestamp(period.findtext("timeInterval/start", ""))
    end = pd.Timestamp(period.findtext("timeInterval/end", ""))
    index = pd.date_range(start, end, freq=ISP, inclusive="left")
    values = pd.Series(float("nan"), index=index)
    for point in period.iter("Point"):
        pos = int(point.findtext("position", "0")) - 1
        amount = point.findtext(field)
        if amount is not None and 0 <= pos < len(index):
            values.iloc[pos] = float(amount)
    return values.ffill()


def parse_capacity(xml_text: str) -> dict[str, pd.Series]:
    """Capacity prices by flow direction (A01 up, A02 down, A03 symmetric)."""
    out: dict[str, list[pd.Series]] = {}
    for series in _root(xml_text).iter("TimeSeries"):
        direction = series.findtext("flowDirection.direction", "")
        for period in series.iter("Period"):
            out.setdefault(direction, []).append(_points(period, "procurement_Price.amount"))
    return {k: pd.concat(v) for k, v in out.items()}


def parse_imbalance(xml_text: str) -> pd.Series:
    """Imbalance price per settlement period. Germany has a single price for both signs."""
    parts = []
    for series in _root(xml_text).iter("TimeSeries"):
        for period in series.iter("Period"):
            parts.append(_points(period, "imbalance_Price.amount"))
    return pd.concat(parts) if parts else pd.Series(dtype="float64")


def _get(client: httpx.Client, token: str, **params: str) -> list[str]:
    resp = client.get(API, params={"securityToken": token, **params})
    if resp.status_code == 400 and "No matching data" in resp.text:
        return []
    if resp.status_code == 400 and "exceeds the allowed maximum" in resp.text:
        # Too many documents for one request: split the window in two and retry.
        start = pd.Timestamp(params["periodStart"], tz=UTC)
        end = pd.Timestamp(params["periodEnd"], tz=UTC)
        middle = (start + (end - start) / 2).floor("D")
        if start < middle < end:
            first = {**params, "periodEnd": f"{middle:%Y%m%d%H%M}"}
            second = {**params, "periodStart": f"{middle:%Y%m%d%H%M}"}
            return _get(client, token, **first) + _get(client, token, **second)
    raise_without_token(resp, token)
    return _documents(resp.content)


def _tidy(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    series = series[~series.index.duplicated(keep="last")].sort_index()
    series.index = pd.DatetimeIndex(series.index).tz_convert(UTC)
    return series[(series.index >= start) & (series.index < end)]


def fetch_reserve_prices(
    start: pd.Timestamp, end: pd.Timestamp, token: str, client: httpx.Client | None = None
) -> pd.DataFrame:
    own = client is None
    client = client or httpx.Client(timeout=120)
    window = {"periodStart": f"{start:%Y%m%d%H%M}", "periodEnd": f"{end:%Y%m%d%H%M}"}
    base = {"documentType": "A81", "type_MarketAgreement.Type": "A01", "businessType": "B95",
            "controlArea_Domain": CONTROL_AREA, **window}
    try:
        columns: dict[str, list[pd.Series]] = {c: [] for c in RESERVE_COLUMNS}
        for doc in _get(client, token, processType=FCR, **base):
            for series in parse_capacity(doc).values():
                columns["fcr"].append(series)
        for doc in _get(client, token, processType=AFRR, **base):
            parsed = parse_capacity(doc)
            for direction, name in ((UP, "afrr_up"), (DOWN, "afrr_dn")):
                if direction in parsed:
                    columns[name].append(parsed[direction])
    finally:
        if own:
            client.close()
    frame = pd.DataFrame({
        name: _tidy(pd.concat(parts), start, end) if parts else pd.Series(dtype="float64")
        for name, parts in columns.items()
    })
    return frame.rename_axis("ts_utc")


def fetch_imbalance_prices(
    start: pd.Timestamp, end: pd.Timestamp, token: str, client: httpx.Client | None = None
) -> pd.Series:
    own = client is None
    client = client or httpx.Client(timeout=120)
    try:
        docs = _get(client, token, documentType="A85", controlArea_Domain=GERMANY,
                    periodStart=f"{start:%Y%m%d%H%M}", periodEnd=f"{end:%Y%m%d%H%M}")
    finally:
        if own:
            client.close()
    parts = [parse_imbalance(doc) for doc in docs]
    parts = [p for p in parts if not p.empty]
    if not parts:
        return pd.Series(dtype="float64", name="imbalance_eur_mwh")
    return _tidy(pd.concat(parts), start, end).rename("imbalance_eur_mwh")


def _cached_months(
    kind: str, start: pd.Timestamp, end: pd.Timestamp, root: Path | None,
    now: pd.Timestamp | None,
) -> pd.DataFrame:
    """Month-by-month cache. A finished month is fetched once; the running month always."""
    token = os.environ.get("ENTSOE_TOKEN")
    folder = (root or data_dir()) / kind
    folder.mkdir(parents=True, exist_ok=True)
    now = now or pd.Timestamp.now(tz=UTC)
    parts = []
    for month in month_starts(start, end):
        month_end = month + pd.offsets.MonthBegin(1)
        path = folder / f"{month:%Y-%m}.parquet"
        if not path.exists() or now < month_end + pd.Timedelta(days=3):
            if not token:
                raise RuntimeError(f"{kind} data needs ENTSOE_TOKEN; SMARD does not publish it")
            log.info(f"{kind}.fetch", month=f"{month:%Y-%m}")
            if kind == "reserves":
                frame = fetch_reserve_prices(month, month_end, token)
            else:
                frame = fetch_imbalance_prices(month, month_end, token).to_frame()
            frame.to_parquet(path)
        parts.append(pd.read_parquet(path))
    out = pd.concat(parts).sort_index() if parts else pd.DataFrame()
    return out[(out.index >= start) & (out.index < end)]


def ensure_reserve_prices(
    start: pd.Timestamp, end: pd.Timestamp, root: Path | None = None,
    now: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """FCR, aFRR up and aFRR down capacity prices, EUR per MW per 15 minutes."""
    return _cached_months("reserves", start, end, root, now)


def ensure_imbalance_prices(
    start: pd.Timestamp, end: pd.Timestamp, root: Path | None = None,
    now: pd.Timestamp | None = None,
) -> pd.Series:
    """Imbalance settlement price (reBAP), EUR per MWh, per 15 minutes."""
    frame = _cached_months("imbalance", start, end, root, now)
    if frame.empty:
        return pd.Series(dtype="float64", name="imbalance_eur_mwh")
    return frame["imbalance_eur_mwh"]

"""Day-ahead prices from the ENTSO-E Transparency Platform (document type A44)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx
import pandas as pd

from bdo.data.store import PRICE_COLUMNS, empty_prices

API = "https://web-api.tp.entsoe.eu/api"
EIC = {"DE-LU": "10Y1001A1001A82H"}
_RES = {"PT15M": 15, "PT30M": 30, "PT60M": 60}
SDAC_SEQUENCE = "1"


def _strip_ns(tag: str) -> str:
    return re.sub(r"^\{.*\}", "", tag)


def parse_a44(xml_text: str) -> pd.DataFrame:
    """Parse a Publication_MarketDocument into one row per interval.

    ENTSO-E may omit a point whose price equals the previous one (curve type A03).
    Those positions are filled with the previous price, which is the documented
    meaning of the format and not an interpolation.

    For DE-LU the document carries more than one auction per day. The single day-ahead
    coupling (SDAC), the market this project trades, has classification sequence 1;
    sequence 2 is a separate 15-minute auction with different prices and is dropped.
    """
    root = ET.fromstring(xml_text)
    for el in root.iter():
        el.tag = _strip_ns(el.tag)
    if root.tag == "Acknowledgement_MarketDocument":
        return empty_prices()  # "no matching data found"
    rows: list[tuple[pd.Timestamp, float, int]] = []
    for series in root.iter("TimeSeries"):
        sequence = series.findtext("classificationSequence_AttributeInstanceComponent.position")
        if sequence is not None and sequence.strip() != SDAC_SEQUENCE:
            continue
        rows += _period_rows(series)
    if not rows:
        return empty_prices()
    df = pd.DataFrame(rows, columns=["ts_utc", "price_eur_mwh", "resolution_min"])
    df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True)
    df["source"] = "entsoe"
    return df[PRICE_COLUMNS].sort_values("ts_utc", kind="stable").reset_index(drop=True)


def _period_rows(series: ET.Element) -> list[tuple[pd.Timestamp, float, int]]:
    rows: list[tuple[pd.Timestamp, float, int]] = []
    for period in series.iter("Period"):
        res = _RES[period.findtext("resolution", "")]
        start = pd.Timestamp(period.findtext("timeInterval/start", ""))
        end = pd.Timestamp(period.findtext("timeInterval/end", ""))
        n = int((end - start) / pd.Timedelta(minutes=res))
        points = {
            int(p.findtext("position", "0")): float(p.findtext("price.amount", "nan"))
            for p in period.iter("Point")
        }
        last: float | None = None
        for pos in range(1, n + 1):
            last = points.get(pos, last)
            if last is not None:
                rows.append((start + pd.Timedelta(minutes=res * (pos - 1)), last, res))
    return rows


def fetch_prices(
    start: pd.Timestamp,
    end: pd.Timestamp,
    token: str,
    zone: str = "DE-LU",
    client: httpx.Client | None = None,
) -> pd.DataFrame:
    own = client is None
    client = client or httpx.Client(timeout=60)
    try:
        resp = client.get(
            API,
            params={
                "securityToken": token,
                "documentType": "A44",
                "in_Domain": EIC[zone],
                "out_Domain": EIC[zone],
                "periodStart": f"{start:%Y%m%d%H%M}",
                "periodEnd": f"{end:%Y%m%d%H%M}",
            },
        )
        resp.raise_for_status()
    finally:
        if own:
            client.close()
    df = parse_a44(resp.text)
    return df[(df["ts_utc"] >= start) & (df["ts_utc"] < end)].reset_index(drop=True)

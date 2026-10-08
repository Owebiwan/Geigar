from __future__ import annotations

from datetime import date

import httpx
import pandas as pd

from bdo.data import entsoe, load_profile, smard, weather
from bdo.data.prices import ensure_prices
from bdo.data.quality import check_prices, day_prices
from bdo.data.store import PriceStore
from bdo.timeutil import day_bounds_utc, month_starts

from .conftest import make_prices


def test_dst_days_have_23_and_25_hours():
    for day, hours in [(date(2025, 3, 30), 23), (date(2025, 10, 26), 25), (date(2025, 6, 1), 24)]:
        start, end = day_bounds_utc(day)
        assert (end - start) == pd.Timedelta(hours=hours)


def test_dst_day_is_a_complete_day_at_quarter_hour_resolution():
    start, end = day_bounds_utc(date(2025, 10, 26))
    prices = make_prices(start.isoformat(), 100, res=15)
    part = day_prices(prices, date(2025, 10, 26))
    assert part is not None and len(part) == 100


def test_gaps_and_duplicates_are_reported_not_repaired():
    prices = make_prices("2025-06-01T00:00:00Z", 24)
    start, end = prices["ts_utc"].iloc[0], prices["ts_utc"].iloc[-1] + pd.Timedelta(hours=1)
    broken = pd.concat([prices.drop(index=[5, 6]), prices.iloc[[10]]], ignore_index=True)
    report = check_prices(broken, start, end)
    assert not report.ok
    assert report.duplicates == [prices["ts_utc"].iloc[10]]
    assert report.gaps == [(prices["ts_utc"].iloc[5], prices["ts_utc"].iloc[7])]
    assert len(broken) == 23  # nothing was filled in


def test_day_with_gap_is_rejected():
    start, _ = day_bounds_utc(date(2025, 6, 1))
    prices = make_prices(start.isoformat(), 24).drop(index=[3])
    assert day_prices(prices, date(2025, 6, 1)) is None


def test_trailing_gap_detected():
    prices = make_prices("2025-06-01T00:00:00Z", 20)
    start = prices["ts_utc"].iloc[0]
    report = check_prices(prices, start, start + pd.Timedelta(hours=24))
    assert report.gaps == [(start + pd.Timedelta(hours=20), start + pd.Timedelta(hours=24))]


def test_cache_never_fetches_a_complete_month_twice(cfg, tmp_path, monkeypatch):
    calls = []

    def fake_fetch(start, end, zone="DE-LU", client=None):
        calls.append((start, end))
        n = int((end - start) / pd.Timedelta(hours=1))
        return make_prices(start.isoformat(), n).assign(source="smard")

    monkeypatch.setattr(smard, "fetch_prices", fake_fetch)
    store = PriceStore(tmp_path)
    start, end = pd.Timestamp("2025-03-10T00:00Z"), pd.Timestamp("2025-04-05T00:00Z")
    now = pd.Timestamp("2026-01-01T00:00Z")
    first = ensure_prices(cfg, start, end, source="smard", store=store, now=now)
    second = ensure_prices(cfg, start, end, source="smard", store=store, now=now)
    assert len(calls) == 2  # March and April, once each
    assert first.equals(second)
    assert first["ts_utc"].min() == start and first["ts_utc"].max() < end
    assert str(first["ts_utc"].dt.tz) == "UTC"


def test_open_month_is_refetched_only_when_more_data_is_needed(cfg, tmp_path, monkeypatch):
    calls = []

    def fake_fetch(start, end, zone="DE-LU", client=None):
        calls.append(start)
        return make_prices(start.isoformat(), 24 * 10).assign(source="smard")  # 10 days published

    monkeypatch.setattr(smard, "fetch_prices", fake_fetch)
    store = PriceStore(tmp_path)
    now = pd.Timestamp("2025-03-10T12:00Z")
    start = pd.Timestamp("2025-03-01T00:00Z")
    ensure_prices(cfg, start, pd.Timestamp("2025-03-09T00:00Z"), "smard", store, now)
    ensure_prices(cfg, start, pd.Timestamp("2025-03-10T00:00Z"), "smard", store, now)
    assert len(calls) == 1
    ensure_prices(cfg, start, pd.Timestamp("2025-03-12T00:00Z"), "smard", store, now)
    assert len(calls) == 2


def test_month_starts():
    months = month_starts(pd.Timestamp("2025-01-15T00:00Z"), pd.Timestamp("2025-03-01T00:00Z"))
    assert [f"{m:%Y-%m}" for m in months] == ["2025-01", "2025-02"]


def _smard_client(index: dict, series: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if "index_" in request.url.path:
            return httpx.Response(200, json={"timestamps": index[request.url.path.split("_")[-1]]})
        return httpx.Response(200, json={"series": series[request.url.path.split("/")[-1]]})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_smard_uses_native_resolution_on_each_side_of_the_cutover():
    cut = int(smard.QUARTER_HOUR_START.timestamp() * 1000)
    chunk = cut - 3_600_000 * 2
    hourly = [[chunk + i * 3_600_000, 50.0 + i] for i in range(4)]
    quarter = [[chunk + i * 900_000, 10.0 + i] for i in range(16)]
    quarter[-1][1] = None  # unpublished value
    client = _smard_client(
        {"hour.json": [chunk], "quarterhour.json": [chunk]},
        {f"4169_DE-LU_hour_{chunk}.json": hourly, f"4169_DE-LU_quarterhour_{chunk}.json": quarter},
    )
    start = pd.Timestamp(chunk, unit="ms", tz="UTC")
    df = smard.fetch_prices(start, start + pd.Timedelta(hours=4), client=client)
    before, after = df[df["ts_utc"] < smard.QUARTER_HOUR_START], df[
        df["ts_utc"] >= smard.QUARTER_HOUR_START]
    assert list(before["resolution_min"]) == [60, 60]
    assert list(after["resolution_min"]) == [15] * 7  # the null is dropped, not filled
    assert not check_prices(df, start, start + pd.Timedelta(hours=4)).ok


ENTSOE_XML = """<?xml version="1.0"?>
<Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3">
<TimeSeries>
<classificationSequence_AttributeInstanceComponent.position>2
</classificationSequence_AttributeInstanceComponent.position>
<Period><timeInterval><start>2025-10-19T22:00Z</start><end>2025-10-19T23:00Z</end>
</timeInterval><resolution>PT15M</resolution>
<Point><position>1</position><price.amount>99</price.amount></Point>
</Period></TimeSeries>
<TimeSeries>
<classificationSequence_AttributeInstanceComponent.position>1
</classificationSequence_AttributeInstanceComponent.position>
<Period><timeInterval><start>2025-10-19T22:00Z</start><end>2025-10-19T23:00Z</end>
</timeInterval><resolution>PT15M</resolution>
<Point><position>1</position><price.amount>10.5</price.amount></Point>
<Point><position>3</position><price.amount>-4</price.amount></Point>
</Period></TimeSeries>
</Publication_MarketDocument>"""


def test_entsoe_parse_repeats_omitted_points_and_keeps_only_the_sdac_auction():
    df = entsoe.parse_a44(ENTSOE_XML)
    assert list(df["price_eur_mwh"]) == [10.5, 10.5, -4.0, -4.0]
    assert set(df["resolution_min"]) == {15}
    assert df["ts_utc"].iloc[0] == pd.Timestamp("2025-10-19T22:00Z")


def test_entsoe_no_data_document_is_empty():
    xml = '<Acknowledgement_MarketDocument xmlns="urn:x"><Reason/></Acknowledgement_MarketDocument>'
    assert entsoe.parse_a44(xml).empty


def test_entsoe_fetch_sends_token_and_window():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, text=ENTSOE_XML)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    df = entsoe.fetch_prices(pd.Timestamp("2025-10-19T22:00Z"), pd.Timestamp("2025-10-19T22:30Z"),
                             "secret", client=client)
    assert seen["securityToken"] == "secret" and seen["periodStart"] == "202510192200"
    assert len(df) == 2


def test_load_profile_sums_to_annual_consumption():
    idx = pd.date_range("2025-01-01", "2026-01-01", freq="15min", tz="Europe/Berlin",
                        inclusive="left").tz_convert("UTC")
    load = load_profile.load_kw(idx, 3_000_000)
    assert abs(load.sum() * 0.25 - 3_000_000) < 1
    local = load.tz_convert("Europe/Berlin")
    assert local[local.index.dayofweek < 5].mean() > local[local.index.dayofweek == 6].mean()


def test_solar_profile_holds_hourly_value_and_never_goes_negative():
    ghi = pd.Series([0.0, 500.0, -3.0], index=pd.date_range("2025-06-01T10:00Z", periods=3,
                                                           freq="h"))
    idx = pd.date_range("2025-06-01T10:00Z", periods=12, freq="15min")
    out = weather.solar_kw(ghi, 400, idx)
    assert list(out.iloc[4:8].round(6)) == [160.0] * 4
    assert (out >= 0).all()

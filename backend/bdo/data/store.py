"""Local Parquet cache for price data, queried through DuckDB (DR-1).

Prices are stored per source and UTC calendar month. A manifest records which
months are complete, so a finished month is never requested from the API again.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd

from bdo.timeutil import UTC

PRICE_COLUMNS = ["ts_utc", "price_eur_mwh", "resolution_min", "source"]


def empty_prices() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_utc": pd.Series([], dtype="datetime64[ns, UTC]"),
            "price_eur_mwh": pd.Series([], dtype="float64"),
            "resolution_min": pd.Series([], dtype="int64"),
            "source": pd.Series([], dtype="object"),
        }
    )


class PriceStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root) / "prices"

    def _dir(self, source: str) -> Path:
        path = self.root / source
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _file(self, source: str, month: pd.Timestamp) -> Path:
        return self._dir(source) / f"{month:%Y-%m}.parquet"

    def _manifest_path(self, source: str) -> Path:
        return self._dir(source) / "manifest.json"

    def manifest(self, source: str) -> dict[str, dict[str, object]]:
        path = self._manifest_path(source)
        if not path.exists():
            return {}
        return json.loads(path.read_text())  # type: ignore[no-any-return]

    def is_cached(self, source: str, month: pd.Timestamp, needed_until: pd.Timestamp) -> bool:
        """True when the month is complete, or already covers everything the caller needs."""
        entry = self.manifest(source).get(f"{month:%Y-%m}")
        if entry is None:
            return False
        if entry["complete"]:
            return True
        return pd.Timestamp(str(entry["covered_until"])) >= needed_until

    def write(self, source: str, month: pd.Timestamp, df: pd.DataFrame, now: pd.Timestamp) -> None:
        month_end = month + pd.offsets.MonthBegin(1)
        df = df[(df["ts_utc"] >= month) & (df["ts_utc"] < month_end)]
        df = df.sort_values("ts_utc", kind="stable").reset_index(drop=True)
        df[PRICE_COLUMNS].to_parquet(self._file(source, month), index=False)
        if df.empty:
            covered = month
        else:
            last = df.iloc[-1]
            covered = last["ts_utc"] + pd.Timedelta(minutes=int(last["resolution_min"]))
        manifest = self.manifest(source)
        manifest[f"{month:%Y-%m}"] = {
            "fetched_at": now.isoformat(),
            "covered_until": covered.isoformat(),
            "complete": bool(now >= month_end),
            "rows": int(len(df)),
        }
        self._manifest_path(source).write_text(json.dumps(manifest, indent=2, sort_keys=True))

    def load(self, source: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        files = sorted(str(p) for p in self._dir(source).glob("*.parquet"))
        if not files:
            return empty_prices()
        df = duckdb.execute(
            "SELECT ts_utc, price_eur_mwh, resolution_min, source FROM read_parquet(?) "
            "WHERE ts_utc >= ? AND ts_utc < ? ORDER BY ts_utc",
            [files, start.to_pydatetime(), end.to_pydatetime()],
        ).df()
        if df.empty:
            return empty_prices()
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True).dt.tz_convert(UTC)
        df["resolution_min"] = df["resolution_min"].astype("int64")
        return df

    def last_fetch(self, source: str) -> str | None:
        stamps = [str(v["fetched_at"]) for v in self.manifest(source).values()]
        return max(stamps) if stamps else None
